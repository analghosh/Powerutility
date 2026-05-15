"""
Upload Raw Data Processor
Handles asynchronous file processing tasks with job detail recording
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path
import csv
import io

from libs.audit_queue.simple_logger import AuditLogger
from libs.messaging.job_publisher import JobPublisher
from libs.models.provider_models import ProviderEntity
from libs.enum.raw_data_status import RawDataStatus

from services.ingestion.app.core.config import settings
from services.ingestion.app.repository.upload_tracking_repository import UploadTrackingRepository
from services.ingestion.app.repositories.integration_apilog_repository import IntegrationApiLogRepository
from libs.models.integration_apilog_entity import IntegrationApiLogEntity
from services.ingestion.app.services.file_storage_service import file_storage_service
from services.ingestion.app.processors.base_raw_data_processor import BaseRawDataProcessor
from services.ingestion.app.processors.plants_processor import PlantsProcessor
from services.ingestion.app.processors.limit_values_processor import LimitValuesProcessor
import logging


logger = logging.getLogger("upload_raw_data_processor")


class UploadRawDataProcessor(BaseRawDataProcessor):
    """
    Service for managing upload raw data processing tasks with job detail recording
    """

    def __init__(self, audit_logger: AuditLogger):
        """Initialize upload raw data processor"""
        super().__init__()
        self.audit_logger = audit_logger
        self.job_publisher = JobPublisher()
        self.upload_tracking_repository = UploadTrackingRepository(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )
        self.integration_apilog_repository = IntegrationApiLogRepository(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )
        
        # Initialize processors for special data types
        self.plants_processor = PlantsProcessor(audit_logger)
        self.limit_values_processor = LimitValuesProcessor(audit_logger)

    async def process_uploaded_file(self, upload_id: str, data_type: str, provider_type: str) -> Dict[str, Any]:
        """
        Process uploaded file in background (wrapper for backward compatibility)
        """
        return await self.process_data(upload_id, data_type, provider_type)

    async def process_data(self, upload_id: str, data_type: str, provider_type: str) -> Dict[str, Any]:
        """
        Process uploaded file in background

        Args:
            upload_id: Upload identifier
            data_type: Type of data (demand or supply)
            provider_type: Provider type (e.g., test_data, scada_data)

        Returns:
            Dict[str, Any]: Processing result
        """
        try:
            logger.info("=== UPLOAD PROCESS START === upload_id: %s, type: %s, provider: %s",
                       upload_id, data_type, provider_type)

            # Get upload creation time from tracking record
            upload_record = await self.upload_tracking_repository.get_upload_by_id(upload_id)
            if not upload_record:
                raise ValueError(f"Upload record not found for upload_id: {upload_id}")

            upload_created_at = upload_record.get("created_at", datetime.now())
            logger.info("Using upload creation time: %s for upload_id: %s", upload_created_at, upload_id)

            # Update status to PROCESSING
            await self._update_upload_status(upload_id, RawDataStatus.PROCESSING, "Starting file processing")

            # Get file path - prefer path stored in record, fallback to service lookup
            file_path = upload_record.get("file_path")
            if not file_path:
                logger.info("file_path not found in record for upload_id: %s, falling back to storage lookup", upload_id)
                file_path = await file_storage_service.get_file_path(upload_id)
            
            if not file_path:
                raise FileNotFoundError(f"File not found for upload_id: {upload_id}")

            # Process the file
            processing_result = await self._process_file_content(
                file_path, upload_id, data_type, provider_type, upload_created_at
            )

            if processing_result["success"]:
                # Update status to COMPLETED
                await self._update_upload_status(
                    upload_id,
                    RawDataStatus.COMPLETED,
                    f"Processing completed successfully. Records: {processing_result['total_records']}"
                )

                # Archive a snapshot of the full uploads directory to the backup location.
                # The zip is never deleted — each upload produces a new timestamped archive.
                try:
                    archive_path = file_storage_service.zip_and_archive(settings.UPLOAD_BACKUP_DIR)
                    logger.info("Upload archive created at %s for upload_id: %s", archive_path, upload_id)
                except Exception as archive_err:
                    logger.error("Failed to create upload archive for upload_id %s: %s", upload_id, archive_err)

                logger.info("Upload processing completed successfully for upload_id: %s", upload_id)

                # Publish job trigger immediately after successful processing
                trigger_job_type = f"{data_type.lower()}_data_processing"
                # Handle both key variations for robustness
                record_count = processing_result.get('total_records', processing_result.get('processed_records', 0))
                
                logger.info(f"Triggering job: {trigger_job_type} for upload {upload_id}")
                self.job_publisher.publish_job_trigger(
                    job_type=trigger_job_type,
                    source_service="ingestion",
                    payload={
                        "upload_id": upload_id,
                        "data_type": data_type,
                        "record_count": record_count
                    }
                )

            else:
                # Update status to FAILED
                await self._update_upload_status(
                    upload_id,
                    RawDataStatus.FAILED,
                    f"Processing failed: {processing_result.get('error', 'Unknown error')}"
                )

                logger.error("Upload processing failed for upload_id: %s", upload_id)

            logger.info("=== UPLOAD PROCESS COMPLETE === upload_id: %s, success: %s",
                       upload_id, processing_result.get("success", False))
            return processing_result

        except (ValueError, FileNotFoundError, OSError, RuntimeError) as e:
            logger.error("=== UPLOAD PROCESS ERROR === upload_id: %s, error: %s", upload_id, e, exc_info=True)

            # Update status to FAILED
            await self._update_upload_status(
                upload_id,
                RawDataStatus.FAILED,
                f"Processing error: {str(e)}"
            )

            return {
                "success": False,
                "error": str(e),
                "upload_id": upload_id
            }

    async def _process_file_content(self, file_path: str, upload_id: str, data_type: str, provider_type: str, upload_created_at: datetime) -> Dict[str, Any]:
        """
        Process file content and store in appropriate collection

        Args:
            file_path: Path to stored file
            upload_id: Upload identifier
            data_type: Type of data
            provider_type: Provider type
            upload_created_at: Upload creation time to use for raw data insertion

        Returns:
            Dict[str, Any]: Processing result
        """
        try:
            # Read file content with encoding detection
            try:
                # Try UTF-8 first (most common)
                with open(file_path, 'r', encoding='utf-8') as file:
                    csv_content = file.read()
            except UnicodeDecodeError:
                # Fallback to UTF-8 with BOM if regular UTF-8 fails
                try:
                    with open(file_path, 'r', encoding='utf-8-sig') as file:
                        csv_content = file.read()
                except UnicodeDecodeError:
                    # Last resort: try with error handling
                    with open(file_path, 'r', encoding='utf-8', errors='replace') as file:
                        csv_content = file.read()
                        csv_content = file.read()
                        logger.warning("File read with encoding errors replaced - some characters may be corrupted")

            # Get complete provider details for validation and storage
            provider = await self._get_provider_details(provider_type, data_type)
            
            # Blind Log the raw file content regardless of provider check success
            # We use provider_type as fallback ID if provider is not found
            log_id = None
            try:
                provider_id_log = str(provider.id) if provider and provider.id else (provider.code if provider else provider_type)
                
                log_entity = IntegrationApiLogEntity(
                    provider_id=provider_id_log,
                    raw_data=csv_content,  # Limit log size
                    timestamp=datetime.now(),
                    metadata={
                        "file_name": Path(file_path).name,
                        "upload_id": upload_id,
                        "source": "upload_processor",
                        "provider_type": provider_type,
                        "provider_found": bool(provider)
                    }
                )
                log_id = self.integration_apilog_repository.save_log(data_type.lower(), log_entity)
                logger.info("Logged raw upload content to integration log (id: %s)", log_id)
            except Exception as log_error:
                logger.error("Failed to write integration log for upload %s: %s", upload_id, log_error, exc_info=True)

            if not provider:
                raise ValueError(f"Provider not found for {provider_type}/{data_type}")

            # Check if this is plants data type - process directly to plants collection
            if data_type.lower() == "plants":
                logger.info("Processing plants data type - routing to plants processor")
                return await self._process_plants_data(csv_content, provider, file_path, upload_id)
            
            # Check if this is limit_values data type - process through approval workflow
            if data_type.lower() == "limit_values":
                logger.info("Processing limit_values data type - routing to limit_values processor")
                return await self._process_limit_values_data(csv_content, provider, file_path, upload_id)
            
            # Parse CSV content with schema validation for other data types
            csv_data = self._parse_csv_content(csv_content, provider)
            if not csv_data:
                raise ValueError("Failed to parse CSV content or file is empty")

            # Store raw data using unified service with complete provider details
            additional_metadata = {
                "file_name": Path(file_path).name,
                "upload_id": upload_id,
                "upload_created_at": upload_created_at,
                "integration_apilog_id": log_id
            }
            result = await self.raw_data_service.store_raw_data(
                raw_data=csv_data,
                provider=provider,
                additional_metadata=additional_metadata
            )

            # Status decision based on validation results
            if result.get("success", False):
                # Validation passed - proceed with processing workflow
                processing_result = {
                    "success": True,
                    "upload_id": upload_id,
                    "raw_data_id": result.get("document_id"),
                    "processing_status_id": result.get("processing_status_id"),
                    "total_records": result.get("total_records", 0)
                }

                return processing_result
            else:
                # Validation failed - return failure without further processing
                validation_error = result.get("error", "Unknown validation error")
                logger.error("Data validation failed for upload_id %s: %s", upload_id, validation_error)

                return {
                    "success": False,
                    "error": f"Data validation failed: {validation_error}",
                    "upload_id": upload_id,
                    "raw_data_id": result.get("document_id"),
                    "processing_status_id": result.get("processing_status_id"),
                    "total_records": result.get("total_records", 0)
                }

        except (ValueError, OSError, RuntimeError, UnicodeDecodeError) as e:
            logger.error("Error processing file content for upload_id %s: %s", upload_id, e, exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "upload_id": upload_id
            }

    async def _update_upload_status(self, upload_id: str, status: RawDataStatus, message: str) -> None:
        """
        Update upload status in database

        Args:
            upload_id: Upload identifier
            status: New status
            message: Status message
        """
        try:
            update_data = {
                "status": status.value,
                "status_message": message,
                "updated_at": datetime.now()
            }

            if status == RawDataStatus.COMPLETED:
                update_data["completed_at"] = datetime.now()
            elif status == RawDataStatus.FAILED:
                update_data["failed_at"] = datetime.now()

            # Increment retry count when marking as failed
            increment_retry = (status == RawDataStatus.FAILED)

            await self.upload_tracking_repository.update_upload_status(
                upload_id, 
                status, 
                message, 
                completed_at=update_data.get("completed_at"), 
                failed_at=update_data.get("failed_at"),
                increment_retry=increment_retry
            )
            logger.debug("Updated upload status for %s: %s (increment_retry: %s)", upload_id, status.value, increment_retry)

        except (ValueError, TypeError, OSError) as e:
            logger.error("Error updating upload status for %s: %s", upload_id, e, exc_info=True)

    async def _mark_upload_permanently_failed(self, upload_id: str, retry_count: int) -> None:
        """
        Mark upload as permanently failed after exceeding max retries
        
        Args:
            upload_id: Upload identifier
            retry_count: Current retry count
        """
        try:
            message = f"Permanently failed after {retry_count} retry attempts. File may have been deleted or is inaccessible."
            
            await self.upload_tracking_repository.update_upload_status(
                upload_id,
                RawDataStatus.FAILED,
                message,
                failed_at=datetime.now(),
                increment_retry=False  # Don't increment further
            )
            
            logger.warning("Marked upload %s as permanently failed after %d retries", upload_id, retry_count)
            
        except Exception as e:
            logger.error("Error marking upload %s as permanently failed: %s", upload_id, e, exc_info=True)


    async def _record_job_completion(self, job_type: str, job_name: str, schedule: str,
                              execution_start: datetime, execution_end: datetime, execution_id: str,
                              status: str, success: bool, result: Optional[Dict[str, Any]] = None,
                              error_message: Optional[str] = None) -> None:
        """
        Record job completion in audit database

        Args:
            job_type: Type of job
            job_name: Name of job
            schedule: Cron schedule
            execution_start: Start time
            execution_end: End time
            execution_id: Execution ID
            status: Final status
            success: Whether job was successful
            result: Job result data
            error_message: Error message if failed
        """
        if not self.job_data_repository or not self.job_detail_repository:
            logger.debug("Job repositories not available for audit logging")
            return

        try:
            # Get the job_id (ObjectId) from job_data collection using job_type
            job_data = await self.job_data_repository.get_job(job_type)
            if not job_data:
                logger.error("Job data not found for job_type: %s", job_type)
                return

            # Use base class method for job completion recording
            await self.record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=execution_start,
                execution_end=execution_end,
                execution_id=execution_id,
                status=status,
                success=success,
                job_id=str(job_data.get('_id')),
                result=result,
                error_message=error_message
            )

        except (ValueError, TypeError, OSError, RuntimeError) as e:
            logger.error("Failed to record job completion for %s: %s", job_type, str(e))

    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute upload raw data processing as a cron job with job detail recording
        This method is called by the cron manager to process pending uploads

        Returns:
            Dict[str, Any]: Processing result with statistics
        """
        job_type = "upload_raw_data_processor"
        job_name = "Upload Raw Data Processor"
        schedule = "*/2 * * * *"  # Every 2 minutes
        start_time = datetime.now()

        # Record job start
        execution_id = await self.record_job_start(job_type, job_name, schedule, start_time)

        # Log job start
        if execution_id:
            self.audit_logger.log_system_operation(
                operation="cron_job_started",
                service="ingestion",
                message=f"Cron job started: {job_name}",
                job_detail_id=execution_id,
                job_name=job_name,
                job_type=job_type,
                schedule=schedule,
                execution_mode="scheduled",
            )

        logger.info("Starting upload raw data processing cron job at %s", start_time.strftime('%Y-%m-%d %H:%M:%S'))

        try:
            # Get pending uploads that need processing
            pending_uploads = await self.upload_tracking_repository.get_uploads_by_status_and_date(
                status=[RawDataStatus.PENDING, RawDataStatus.FAILED],
                limit=getattr(settings, 'BACKGROUND_PROCESSING_BATCH_SIZE', 10)
            )

            if not pending_uploads:
                logger.info("No pending uploads found for background processing")
                end_time = datetime.now()
                result = {
                    'status': 'success',
                    'processed_files': 0,
                    'execution_time': (end_time - start_time).total_seconds()
                }

                # Log job completion
                if execution_id:
                    self.audit_logger.log_system_operation(
                        operation="cron_job_completed",
                        service="ingestion",
                        message=f"Cron job completed: {job_name} - No pending uploads",
                        job_detail_id=execution_id,
                        job_name=job_name,
                        job_type=job_type,
                        execution_mode="scheduled",
                        duration_ms=int((end_time - start_time).total_seconds() * 1000),
                        processed_files=0,
                        success=True
                    )

                    # Record job completion in job detail system
                    await self._record_job_completion(
                        job_type, job_name, schedule, start_time, end_time, execution_id,
                        "success", True, result
                    )

                return result

            processed_files = 0
            failed_files = 0

            logger.info("Found %d pending uploads to process", len(pending_uploads))

            for upload in pending_uploads:
                try:
                    upload_id = upload.get('upload_id')
                    if not upload_id:
                        logger.error("Upload record missing upload_id: %s", upload)
                        failed_files += 1
                        continue

                    # Check retry count - skip if exceeded max retries
                    retry_count = upload.get('retry_count', 0)
                    max_retries = getattr(settings, 'UPLOAD_MAX_RETRIES', 3)
                    
                    if retry_count >= max_retries:
                        logger.warning("Upload %s exceeded max retries (%d/%d), marking as permanently failed", 
                                     upload_id, retry_count, max_retries)
                        await self._mark_upload_permanently_failed(upload_id, retry_count)
                        failed_files += 1
                        continue

                    file_path = upload.get('file_path')
                    data_type = upload.get('data_type', 'unknown')
                    provider_type = upload.get('provider_type', 'unknown')

                    logger.info("Processing upload: %s (file: %s, type: %s, retry: %d/%d)", 
                              upload_id, file_path, data_type, retry_count, max_retries)

                    # Log upload processing start
                    if execution_id:
                        self.audit_logger.log_system_operation(
                            operation="upload_processing_started",
                            service="ingestion",
                            message=f"Starting upload processing for file: {upload_id}",
                            job_detail_id=execution_id,
                            upload_id=upload_id,
                            file_path=file_path,
                            data_type=data_type,
                            provider_type=provider_type
                        )

                    # Process the file using existing method
                    result = await self.process_uploaded_file(
                        upload_id=upload_id,
                        data_type=data_type,
                        provider_type=provider_type
                    )

                    if result.get('success', False):
                        processed_files += 1
                        logger.info("Successfully processed upload: %s", upload_id)
                        
                        # Log successful upload processing
                        if execution_id:
                            self.audit_logger.log_system_operation(
                                operation="upload_processing_completed",
                                service="ingestion",
                                message=f"Successfully processed upload: {upload_id}",
                                job_detail_id=execution_id,
                                upload_id=upload_id,
                                data_type=data_type,
                                provider_type=provider_type,
                                processed_records=result.get('processed_records', 0),
                                success=True
                            )
                        
                        # Publish job trigger
                        # Job trigger is now handled within process_uploaded_file/process_data
                        # so we don't need to duplicate it here
                        logger.debug("Job trigger handled by processor for upload %s", upload_id)
                    else:
                        failed_files += 1
                        error_message = result.get('error', 'Unknown processing error')
                        logger.error("Failed to process upload %s: %s", upload_id, error_message)
                        
                        # Log failed upload processing
                        if execution_id:
                            self.audit_logger.log_system_operation(
                                operation="upload_processing_failed",
                                service="ingestion",
                                message=f"Upload processing failed: {error_message}",
                                job_detail_id=execution_id,
                                upload_id=upload_id,
                                data_type=data_type,
                                provider_type=provider_type,
                                success=False
                            )

                except (ValueError, OSError, RuntimeError) as file_error:
                    failed_files += 1
                    upload_id = upload.get('upload_id', 'unknown')
                    logger.error("Exception processing upload %s: %s", upload_id, str(file_error))

                    # Log upload processing exception
                    if execution_id:
                        self.audit_logger.log_system_operation(
                            operation="upload_processing_error",
                            service="ingestion",
                            message=f"Upload processing error: {str(file_error)}",
                            job_detail_id=execution_id,
                            upload_id=upload_id,
                            exception_type=type(file_error).__name__,
                            success=False
                        )

                    # Update status to failed
                    await self._update_upload_status(
                        upload_id,
                        RawDataStatus.FAILED,
                        f"Cron processing error: {str(file_error)}"
                    )

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            # Determine overall status
            overall_success = processed_files > 0 and failed_files == 0
            status = "success" if overall_success else ("partial_success" if processed_files > 0 else "error")

            result = {
                'status': status,
                'processed_files': processed_files,
                'failed_files': failed_files,
                'total_files': len(pending_uploads),
                'execution_time': execution_time,
                'execution_id': execution_id
            }

            # Log job completion with queue-based audit
            if execution_id:
                error_message = None if overall_success else f"Processed {processed_files}, failed {failed_files}"
                
                self.audit_logger.log_system_operation(
                    operation="cron_job_completed",
                    service="ingestion",
                    message=f"Cron job completed: {job_name} - {processed_files} processed, {failed_files} failed",
                    job_detail_id=execution_id,
                    job_name=job_name,
                    job_type=job_type,
                    execution_mode="scheduled",
                    duration_ms=int(execution_time * 1000),
                    processed_files=processed_files,
                    failed_files=failed_files,
                    total_files=len(pending_uploads),
                    success=overall_success
                )

                # Record job completion in job detail system
                await self._record_job_completion(
                    job_type, job_name, schedule, start_time, end_time, execution_id,
                    status, overall_success, result=result, error_message=error_message
                )

            logger.info("Upload raw data processing cron job completed in %.2fs", execution_time)
            logger.info("   - Processed: %d files", processed_files)
            logger.info("   - Failed: %d files", failed_files)
            logger.info("   - Total: %d files", len(pending_uploads))
            logger.info("   - Execution ID: %s", execution_id)

            return result

        except (ValueError, OSError, RuntimeError) as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            logger.error("Error in upload raw data processing cron job after %.2fs: %s", execution_time, str(e))

            result = {'status': 'error', 'error': str(e), 'execution_time': execution_time}

            # Log job error
            if execution_id:
                self.audit_logger.log_system_operation(
                    operation="cron_job_failed",
                    service="ingestion",
                    message=f"Cron job failed: {job_name} - {str(e)}",
                    job_detail_id=execution_id,
                    job_name=job_name,
                    job_type=job_type,
                    execution_mode="scheduled",
                    duration_ms=int(execution_time * 1000),
                    exception_type=type(e).__name__,
                    success=False
                )

                # Record job completion with error in job detail system
                await self._record_job_completion(
                    job_type, job_name, schedule, start_time, end_time, execution_id,
                    "error", False, result=result, error_message=str(e)
                )

            return result

    async def _get_provider_details(self, provider_type: str, data_type: str) -> Optional[ProviderEntity]:
        """
        Get complete provider details for validation and storage

        Args:
            provider_type: Provider type code
            data_type: Data type (demand, supply, etc.)

        Returns:
            Optional[ProviderEntity]: Complete provider object or None if not found
        """
        try:
            provider_service = self.provider_service

            # Get provider details directly
            provider = await provider_service.get_provider_by_code_and_data_type(
                provider_type, data_type.lower()
            )

            if not provider:
                logger.error("Provider not found: %s for data type %s", provider_type, data_type)
                return None

            if not provider.enabled:
                logger.error("Provider is disabled: %s for data type %s", provider_type, data_type)
                return None

            return provider

        except (ValueError, TypeError, OSError, RuntimeError) as e:
            logger.error("Error getting provider details for %s/%s: %s", provider_type, data_type, e)
            return None

    def _parse_csv_content(self, csv_content: str, provider: Optional[ProviderEntity] = None) -> List[Dict[str, Any]]:
        """
        Parse CSV content into list of dictionaries using appropriate parser

        Args:
            csv_content: Raw CSV content string
            provider: Provider entity containing schema definition (optional)

        Returns:
            List[Dict[str, Any]]: Parsed CSV data as list of dictionaries
        """
        try:
            # Import parser factory with error handling
            try:
                from libs.parsers import create_parser
            except ImportError as import_error:
                logger.error("Failed to import parser factory: %s", import_error)
                raise ValueError(f"Parser system not available: {str(import_error)}") from import_error
            
            # Create appropriate parser based on provider configuration
            try:
                parser = create_parser(provider)
                logger.info("Using parser: %s for provider: %s", 
                           parser.__class__.__name__, 
                           provider.code if provider else 'unknown')
            except Exception as parser_error:
                logger.error("Failed to create parser for provider %s: %s", 
                           provider.code if provider else 'unknown', parser_error)
                raise ValueError(f"Parser creation failed: {str(parser_error)}") from parser_error
            
            # Parse content using selected parser
            try:
                raw_data = parser.parse(csv_content, provider)
            except Exception as parse_error:
                logger.error("Failed to parse CSV content with %s: %s", 
                           parser.__class__.__name__, parse_error)
                raise ValueError(f"CSV parsing failed: {str(parse_error)}") from parse_error
            
            logger.info("Parsed CSV content: %d raw records using %s", 
                       len(raw_data), parser.__class__.__name__)

            # Apply schema validation and mapping if provider is provided
            if provider:
                # Check if parser already applied field mapping (e.g., TradingParser, SupplyCSVParser)
                parser_type = provider.metadata.get('parser_type', '').lower()
                if parser_type in ['trading', 'supply_csv', 'supply']:
                    # These parsers already handle their own mapping or provide structured data
                    logger.info("%s parser already applied field mapping, skipping additional processing", parser_type)
                    return raw_data
                else:
                    # Apply field mapping for other parsers (StandardCSVParser, etc.)
                    logger.info("Applying field mapping for parser type: %s", parser_type or 'standard')
                    mapped_data = self._apply_provider_field_mapping(raw_data, provider)
                    
                    # Skip schema mapping since we already applied field mapping
                    # map_data_to_schema would reverse our field mapping
                    schema_mapped_data = mapped_data
                
                # Validate data against schema using serve field names
                validation_result = self._validate_mapped_data_schema(schema_mapped_data, provider)
                
                if validation_result["success"]:
                    logger.info("CSV validation successful: %d records validated", validation_result["validated_records"])
                    return validation_result["validated_data"]
                else:
                    # Log validation errors and raise exception
                    error_msg = f"Validation failed: {', '.join(validation_result['errors'][:5])}"
                    if len(validation_result['errors']) > 5:
                        error_msg += f" (and {len(validation_result['errors']) - 5} more errors)"
                    raise ValueError(error_msg)
            else:
                # No schema validation, return raw data
                return raw_data

        except (ValueError, UnicodeDecodeError, OSError) as e:
            logger.error("Error parsing CSV content: %s", e, exc_info=True)
            raise ValueError(f"CSV parsing failed: {str(e)}") from e

    def _apply_provider_field_mapping(self, raw_data: List[Dict[str, Any]], provider: ProviderEntity) -> List[Dict[str, Any]]:
        """
        Apply field mapping from provider configuration to raw parsed data
        
        Args:
            raw_data: Raw parsed data records (with provider field names)
            provider: Provider entity with field mapping configuration
            
        Returns:
            List[Dict[str, Any]]: Data with fields mapped to serve field names
        """
        try:
            if not provider.mapping:
                logger.debug("No field mapping configured for provider %s", provider.code)
                return raw_data
            
            mapped_records = []
            
            for record in raw_data:
                mapped_record = {}
                
                # Apply field mappings
                for field_mapping in provider.mapping:
                    provider_field = field_mapping.provider_field
                    serve_field = field_mapping.serve_field
                    transformation = getattr(field_mapping, 'transformation', None)
                    
                    # Get value from parsed record
                    if provider_field in record:
                        value = record[provider_field]
                        
                        # Apply transformation if specified (basic transformations)
                        if transformation:
                            if transformation.lower() == 'uppercase':
                                value = str(value).upper()
                            elif transformation.lower() == 'lowercase':
                                value = str(value).lower()
                            elif transformation.lower() == 'strip':
                                value = str(value).strip()
                        
                        mapped_record[serve_field] = value
                    else:
                        # Check if provider field has a default value in schema
                        default_value = self._get_field_default_value(provider_field, provider)
                        if default_value is not None:
                            mapped_record[serve_field] = default_value
                
                # Add any unmapped fields that might be needed (like metadata fields)
                for key, value in record.items():
                    if key not in [mapping.provider_field for mapping in provider.mapping]:
                        # Keep unmapped fields as-is (like metadata fields)
                        mapped_record[key] = value
                
                mapped_records.append(mapped_record)
            
            logger.info("Applied field mapping for provider %s: %d records mapped", 
                        provider.code, len(mapped_records))
            return mapped_records
            
        except Exception as e:
            logger.error("Error applying field mapping for provider %s: %s", provider.code, str(e))
            # Return original data if mapping fails
            return raw_data
    
    def _get_field_default_value(self, field_name: str, provider: ProviderEntity) -> Any:
        """
        Get default value for a field from provider schema
        
        Args:
            field_name: Name of the field
            provider: Provider entity with schema definition
            
        Returns:
            Any: Default value or None if not found
        """
        try:
            for field in provider.fields:
                if field.field_name == field_name:
                    return getattr(field, 'default_value', None)
            return None
        except Exception as e:
            logger.error("Error getting default value for field %s: %s", field_name, str(e))
            return None

    def _validate_mapped_data_schema(self, data: List[Dict[str, Any]], provider: ProviderEntity) -> Dict[str, Any]:
        """
        Validate data that has already been field-mapped (uses serve field names)
        
        Args:
            data: List of data records with serve field names
            provider: Provider entity containing schema definition
            
        Returns:
            Dict[str, Any]: Validation result with success status and any errors
        """
        try:
            if not provider.fields or not provider.mapping:
                logger.warning("No schema or mapping defined for provider %s, skipping validation", provider.code)
                return {"success": True, "validated_records": len(data), "validated_data": data, "errors": []}

            # Create mapping from provider fields to serve fields
            serve_field_mapping = {}
            for mapping in provider.mapping:
                serve_field_mapping[mapping.provider_field] = mapping.serve_field
            
            # Get required serve fields (mapped from required provider fields)
            required_serve_fields = set()
            field_types_serve = {}
            
            for field in provider.fields:
                if field.is_required:
                    serve_field = serve_field_mapping.get(field.field_name, field.field_name)
                    required_serve_fields.add(serve_field.lower())
                
                # Map field types to serve field names
                serve_field = serve_field_mapping.get(field.field_name, field.field_name)
                field_types_serve[serve_field.lower()] = field.field_type

            validation_errors = []
            validated_data = []

            for record_num, record in enumerate(data, start=1):
                try:
                    # Create case-insensitive record mapping
                    case_insensitive_record = {key.lower(): value for key, value in record.items()}

                    # Check required fields (using serve field names)
                    missing_fields = required_serve_fields - set(case_insensitive_record.keys())
                    if missing_fields:
                        validation_errors.append(f"Record {record_num}: Missing required fields: {', '.join(missing_fields)}")
                        continue

                    # Convert and validate field types
                    converted_record = {}
                    for key, value in record.items():
                        key_lower = key.lower()
                        if key_lower in field_types_serve:
                            try:
                                converted_record[key] = self._convert_value_with_type(value, field_types_serve[key_lower])
                            except (ValueError, TypeError) as e:
                                validation_errors.append(f"Record {record_num}: Invalid type for field '{key}': {str(e)}")
                                break
                        else:
                            # Keep field as-is if no type specified
                            converted_record[key] = value
                    else:
                        # Only add record if no type conversion errors occurred
                        validated_data.append(converted_record)

                except Exception as e:
                    validation_errors.append(f"Record {record_num}: Validation error: {str(e)}")

            success = len(validation_errors) == 0
            result = {
                "success": success,
                "validated_records": len(validated_data),
                "validated_data": validated_data,
                "errors": validation_errors
            }

            if success:
                logger.info("Mapped data validation successful: %d records validated", len(validated_data))
            else:
                logger.warning("Mapped data validation failed: %d errors found", len(validation_errors))

            return result

        except Exception as e:
            logger.error("Error validating mapped data schema: %s", str(e))
            return {
                "success": False,
                "validated_records": 0,
                "validated_data": [],
                "errors": [f"Validation system error: {str(e)}"]
            }

    async def _process_plants_data(self, csv_content: str, provider: ProviderEntity, file_path: str, upload_id: str) -> Dict[str, Any]:
        """
        Process plants data directly into plants collection
        
        Args:
            csv_content: CSV file content as string
            provider: Provider entity containing schema and mapping configuration
            file_path: Path to the source file
            upload_id: Upload identifier
            
        Returns:
            Dict[str, Any]: Processing result
        """
        try:
            logger.info("Processing plants data for provider: %s", provider.code)
            
            # Parse CSV content using the same parser as other data types
            csv_data = self._parse_csv_content(csv_content, provider)
            if not csv_data:
                raise ValueError("Failed to parse CSV content or file is empty")
            
            logger.info("Parsed %d plant records from CSV", len(csv_data))
            
            # Process plants data using plants processor
            result = await self.plants_processor.process_plants_data(csv_data, provider)
            
            if result["success"]:
                logger.info("Successfully processed plants data: %d records processed", result["processed_records"])
                return {
                    "success": True,
                    "message": result["message"],
                    "total_records": result["processed_records"],
                    "inserted": result.get("inserted", 0),
                    "updated": result.get("updated", 0),
                    "provider_code": result.get("provider_code", provider.code),
                    "file_name": Path(file_path).name,
                    "upload_id": upload_id
                }
            else:
                logger.error("Failed to process plants data: %s", result.get("message", "Unknown error"))
                return {
                    "success": False,
                    "error": result.get("message", "Unknown error"),
                    "upload_id": upload_id,
                    "total_records": 0
                }
                
        except Exception as e:
            logger.error("Error processing plants data: %s", str(e), exc_info=True)
            return {
                "success": False,
                "error": f"Plants data processing failed: {str(e)}",
                "upload_id": upload_id,
                "total_records": 0
            }

    async def _process_limit_values_data(self, csv_content: str, provider: ProviderEntity, file_path: str, upload_id: str) -> Dict[str, Any]:
        """
        Process limit values data through approval workflow
        
        Args:
            csv_content: CSV file content as string
            provider: Provider entity containing schema and mapping configuration
            file_path: Path to the source file
            upload_id: Upload identifier
            
        Returns:
            Dict[str, Any]: Processing result with approval request ID
        """
        try:
            logger.info("Processing limit values data for provider: %s", provider.code)
            
            # Parse CSV content using the same parser as other data types
            csv_data = self._parse_csv_content(csv_content, provider)
            if not csv_data:
                raise ValueError("Failed to parse CSV content or file is empty")
            
            logger.info("Parsed %d limit values records from CSV", len(csv_data))
            
            # Get actor information from upload tracking
            upload_record = await self.upload_tracking_repository.get_upload_by_id(upload_id)
            actor = {
                "user_id": upload_record.get("created_by", "anonymous"),
                "username": upload_record.get("created_by_username", "anonymous"),
                "correlation_id": upload_record.get("correlation_id", "unknown")
            }
            
            # Process limit values data using limit values processor
            result = await self.limit_values_processor.process_limit_values_data(
                csv_data, provider, upload_id, Path(file_path).name, actor
            )
            
            if result["success"]:
                # Use approval_id if available, fallback to batch_id
                approval_id_to_log = result.get("approval_id") or result.get("batch_id", "unknown")
                logger.info("Successfully staged limit values data for approval: %s", approval_id_to_log)
                return {
                    "success": True,
                    "message": result.get("message"),
                    "total_records": result.get("total_records", 0),
                    "valid_records": result.get("valid_records", 0),
                    "invalid_records": result.get("invalid_records", result.get("failed_records", 0)),
                    "approval_id": approval_id_to_log,
                    "batch_id": result.get("batch_id"),
                    "status": result.get("status"),
                    "provider_code": provider.code,
                    "file_name": Path(file_path).name,
                    "upload_id": upload_id
                }
            else:
                logger.error("Failed to process limit values data: %s", result.get("message", "Unknown error"))
                return {
                    "success": False,
                    "error": result.get("message", "Unknown error"),
                    "upload_id": upload_id,
                    "total_records": 0
                }
                
        except Exception as e:
            logger.error("Error processing limit values data: %s", str(e), exc_info=True)
            return {
                "success": False,
                "error": f"Limit values data processing failed: {str(e)}",
                "upload_id": upload_id,
                "total_records": 0
            }
