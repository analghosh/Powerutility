"""
Base Raw Data Processor
Abstract base class for provider-specific raw data processors
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from datetime import datetime
from uuid import uuid4

from libs.repository.raw_data_processing_status_repository import RawDataProcessingStatusRepository
from libs.services.raw_data_service import RawDataService
from libs.enum.raw_data_status import RawDataStatus
from libs.core.job_entities import JobDetail
from libs.core.job_repositories import JobDetailRepository

logger = logging.getLogger(__name__)


class BaseRawDataProcessor(ABC):
    """
    Abstract base class for provider-specific raw data processors
    Each provider type will have its own implementation
    """
    
    def __init__(self, mongodb_uri: str, database_name: str, provider_type: str, data_type: str, audit_db_uri: Optional[str] = None, audit_db_name: str = "audit_db"):
        """
        Initialize the base raw data processor
        
        Args:
            mongodb_uri: MongoDB connection URI for EPMS database
            database_name: EPMS database name
            provider_type: Provider type (test_data, scada_data, etc.)
            data_type: Data type (demand, supply, etc.)
            audit_db_uri: MongoDB connection URI for audit database (optional)
            audit_db_name: Audit database name (default: audit_db)
        """
        self.provider_type = provider_type
        self.data_type = data_type
        self.processing_status_repository = RawDataProcessingStatusRepository(mongodb_uri, database_name)
        self.raw_data_service = RawDataService(mongodb_uri, database_name)
        
        # Initialize job detail repository for execution logging
        if audit_db_uri:
            self.job_detail_repository = JobDetailRepository(
                audit_db_uri=audit_db_uri,
                audit_db_name=audit_db_name,
                job_detail_collection="job_detail"
            )
        else:
            self.job_detail_repository = None
        
        logger.info("Initialized %s raw data processor for %s data type", provider_type, data_type)
    
    async def execute_cron_job(self, limit: int = 50, job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute raw data processing as a cron job
        This method is called by the cron manager
        
        Args:
            limit: Maximum number of records to process
            job_id: Optional job ID for execution logging
            
        Returns:
            Dict[str, Any]: Processing result with statistics
        """
        start_time = datetime.now()
        execution_id = str(uuid4())
        
        # Create job name for logging
        job_name = f"{self.provider_type}_{self.data_type}_raw_data_processing"
        if not job_id:
            job_id = f"{self.provider_type}_{self.data_type}_raw_data_processing"
        
        logger.info("Starting %s %s raw data processing cron job at %s (Execution ID: %s)", 
                   self.provider_type, self.data_type, start_time.strftime('%Y-%m-%d %H:%M:%S'), execution_id)
        
        # Log job start
        execution_log_id = None
        if self.job_detail_repository:
            try:
                job_detail = JobDetail(
                    job_id=job_id,
                    execution_id=execution_id,
                    job_name=job_name,
                    execution_start=start_time,
                    execution_end=None,
                    duration_seconds=None,
                    status="running",
                    success=False,
                    error_message=None,
                    error_details=None,
                    result_data={
                        "limit": limit,
                        "started_at": start_time.isoformat()
                    },
                    metadata={
                        "provider_type": self.provider_type,
                        "data_type": self.data_type
                    },
                    service="scheduler"
                )
                execution_log_id = await self.job_detail_repository.log_execution(job_detail)
                logger.info("Logged job execution start: %s", execution_id)
            except (ValueError, TypeError, OSError) as log_error:
                logger.warning("Failed to log job execution start: %s", str(log_error))
        
        try:
            # Get pending processing records for this provider and data type
            pending_records = self.processing_status_repository.get_pending_processing(
                provider_type=self.provider_type,
                data_type=self.data_type,
                limit=limit
            )
            
            if not pending_records:
                end_time = datetime.now()
                execution_time = (end_time - start_time).total_seconds()
                
                logger.info("No pending %s %s raw data records found for processing", self.provider_type, self.data_type)
                
                # Log job completion with no records
                if self.job_detail_repository and execution_log_id:
                    try:
                        await self.job_detail_repository.update_execution(execution_id, {
                            "execution_end": end_time,
                            "duration_seconds": execution_time,
                            "status": "success",
                            "success": True,
                            "result_data": {
                                "processed_records": 0,
                                "failed_records": 0,
                                "total_records": 0,
                                "execution_time": execution_time,
                                "message": "No pending records found",
                                "completed_at": end_time.isoformat()
                            }
                        })
                        logger.info("Updated job execution completion (no records): %s", execution_id)
                    except (ValueError, TypeError, OSError) as log_error:
                        logger.warning("Failed to update job execution completion: %s", str(log_error))
                
                return {
                    'status': 'success',
                    'provider_type': self.provider_type,
                    'processed_records': 0,
                    'failed_records': 0,
                    'total_records': 0,
                    'execution_time': execution_time,
                    'execution_id': execution_id,
                    'message': 'No pending records found'
                }
            
            processed_records = 0
            failed_records = 0
            
            logger.info("Found %d pending %s raw data records to process", 
                       len(pending_records), self.provider_type)
            
            for record in pending_records:
                try:
                    processing_status_id = record["_id"]
                    raw_data_id = record["raw_data_id"]
                    data_type = record["data_type"]
                    
                    logger.info("Processing %s raw data: status_id=%s, raw_data_id=%s, type=%s", 
                               self.provider_type, processing_status_id, raw_data_id, data_type)    
                    
                    # Update status to PROCESSING
                    self.processing_status_repository.update_status(
                        processing_status_id, 
                        RawDataStatus.PROCESSING,
                        processing_started_at=datetime.now()
                    )
                    
                    # Get raw data from time series collection
                    raw_data_record = self.raw_data_service.get_by_id(raw_data_id)
                    if not raw_data_record:
                        raise ValueError(f"Raw data record not found: {raw_data_id}")
                    
                    # Process the raw data using provider-specific logic
                    processing_result = await self._process_raw_data(
                        raw_data_record, record
                    )
                    
                    if processing_result.get("success", False):
                        # Update status to COMPLETED
                        self.processing_status_repository.update_status(
                            processing_status_id,
                            RawDataStatus.COMPLETED,
                            processing_completed_at=datetime.now(),
                            processing_metadata={
                                **record.get("processing_metadata", {}),
                                "processed_records": processing_result.get("processed_records", 0),
                                "processing_result": processing_result.get("result", {}),
                                "processor_type": self.provider_type
                            }
                        )
                        processed_records += 1
                        logger.info("Successfully processed %s raw data: status_id=%s", 
                                   self.provider_type, processing_status_id)
                    else:
                        # Update status to FAILED
                        error_message = processing_result.get("error", "Unknown processing error")
                        retry_count = record.get("retry_count", 0) + 1
                        
                        self.processing_status_repository.update_status(
                            processing_status_id,
                            RawDataStatus.FAILED,
                            error_message=error_message,
                            retry_count=retry_count,
                            processing_completed_at=datetime.now()
                        )
                        failed_records += 1
                        logger.error("Failed to process %s raw data status_id=%s: %s", 
                                   self.provider_type, processing_status_id, error_message)
                        
                except (ValueError, TypeError, OSError, RuntimeError) as record_error:
                    failed_records += 1
                    processing_status_id = record.get("_id", "unknown")
                    logger.error("Exception processing %s raw data record %s: %s", 
                               self.provider_type, processing_status_id, str(record_error))
                    
                    # Update status to failed
                    try:
                        retry_count = record.get("retry_count", 0) + 1
                        self.processing_status_repository.update_status(
                            processing_status_id,
                            RawDataStatus.FAILED,
                            error_message=f"Processing exception: {str(record_error)}",
                            retry_count=retry_count,
                            processing_completed_at=datetime.now()
                        )
                    except (ValueError, TypeError, OSError) as update_error:
                        logger.error("Failed to update status for failed %s record %s: %s", 
                                   self.provider_type, processing_status_id, update_error)
            
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            # Determine overall success status
            overall_success = failed_records == 0 and processed_records > 0
            status = "success" if overall_success else ("partial_success" if processed_records > 0 else "error")
            
            result = {
                'status': 'success',
                'provider_type': self.provider_type,
                'processed_records': processed_records,
                'failed_records': failed_records,
                'total_records': len(pending_records),
                'execution_time': execution_time,
                'execution_id': execution_id
            }
            
            # Log job completion
            if self.job_detail_repository and execution_log_id:
                try:
                    await self.job_detail_repository.update_execution(execution_id, {
                        "execution_end": end_time,
                        "duration_seconds": execution_time,
                        "status": status,
                        "success": overall_success,
                        "result_data": {
                            "processed_records": processed_records,
                            "failed_records": failed_records,
                            "total_records": len(pending_records),
                            "execution_time": execution_time,
                            "completed_at": end_time.isoformat()
                        }
                    })
                    logger.info("Updated job execution completion: %s", execution_id)
                except (ValueError, TypeError, OSError) as log_error:
                    logger.warning("Failed to update job execution completion: %s", str(log_error))
            
            logger.info("%s raw data processing cron job completed in %.2fs", 
                       self.provider_type, execution_time)
            logger.info("   - Provider: %s", self.provider_type)
            logger.info("   - Processed: %d records", processed_records)
            logger.info("   - Failed: %d records", failed_records)
            logger.info("   - Total: %d records", len(pending_records))
            logger.info("   - Execution ID: %s", execution_id)
            
            return result
            
        except (ValueError, TypeError, OSError, RuntimeError) as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            # Log job failure
            if self.job_detail_repository and execution_log_id:
                try:
                    await self.job_detail_repository.update_execution(execution_id, {
                        "execution_end": end_time,
                        "duration_seconds": execution_time,
                        "status": "error",
                        "success": False,
                        "error_message": str(e),
                        "error_details": {
                            "exception_type": type(e).__name__,
                            "failed_at": end_time.isoformat()
                        }
                    })
                    logger.info("Updated job execution failure: %s", execution_id)
                except (ValueError, TypeError, OSError) as log_error:
                    logger.warning("Failed to update job execution failure: %s", str(log_error))
            
            logger.error("Error in %s raw data processing cron job after %.2fs: %s", 
                       self.provider_type, execution_time, str(e))
            logger.error("   - Execution ID: %s", execution_id)
            
            return {
                'status': 'error', 
                'provider_type': self.provider_type,
                'error': str(e), 
                'execution_time': execution_time,
                'execution_id': execution_id
            }
    
    @abstractmethod
    async def _process_raw_data(self, raw_data_record: Dict[str, Any], status_record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process raw data using provider-specific logic
        This method must be implemented by each provider
        
        Args:
            raw_data_record: Raw data from time series collection
            status_record: Processing status record
            
        Returns:
            Dict[str, Any]: Processing result
        """
        raise NotImplementedError(f"{self.__class__.__name__} must implement _process_raw_data method")
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get processing statistics for this provider
        
        Returns:
            Dict[str, Any]: Statistics for this provider
        """
        try:
            return self.processing_status_repository.get_statistics(provider_type=self.provider_type)
        except (ValueError, TypeError, OSError, RuntimeError) as e:
            logger.error("Error getting statistics for %s processor: %s", self.provider_type, e)
            return {"error": str(e)}
