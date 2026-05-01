"""
FTP Raw Data Processor
Handles the processing of FTP raw data downloads into raw data entities

Example Provider Configuration (Fixed Filename):
{
    "code": "example_ftp_provider",
    "name": "Example FTP Data Provider",
    "data_type": "EXCHANGE",
    "ingestion_type": "FTP",
    "enabled": true,
    "metadata": {
        "ftp_host": "ftp.example.com",
        "ftp_port": 21,
        "ftp_username": "username",
        "ftp_password": "password",
        "ftp_remote_path": "/data/exchange",
        "ftp_file_name": "data.csv",
        "ftp_passive_mode": true,
        "ftp_use_tls": false,
        "ftp_timeout": 30,
        "ftp_delete_after_download": false,
        "file_format": "csv",
        "csv_delimiter": ",",
        "csv_has_header": true,
        "file_encoding": "utf-8",
        "config": {
            "data_source": "ftp_server",
            "frequency": "daily"
        }
    },
    "fields": [
        {
            "field_name": "timestamp",
            "field_type": "datetime",
            "is_required": true
        },
        {
            "field_name": "price",
            "field_type": "float",
            "is_required": true
        }
    ]
}

Example Provider Configuration (Date-Based Filename):
{
    "code": "daily_ftp_provider",
    "name": "Daily FTP Data Provider",
    "data_type": "DEMAND",
    "ingestion_type": "FTP",
    "enabled": true,
    "metadata": {
        "ftp_host": "ftp.daily.com",
        "ftp_port": 21,
        "ftp_username": "daily_user",
        "ftp_password": "password",
        "ftp_remote_path": "/data/daily",
        "use_date_based_filename": true,
        "date_format": "dd-mm-yyyy",
        "ftp_passive_mode": true,
        "ftp_use_tls": false,
        "ftp_timeout": 30,
        "ftp_delete_after_download": false,
        "file_format": "csv",
        "csv_delimiter": ",",
        "csv_has_header": true,
        "file_encoding": "utf-8"
    },
    "fields": [
        {
            "field_name": "timestamp",
            "field_type": "datetime",
            "is_required": true
        },
        {
            "field_name": "demand_value",
            "field_type": "float",
            "is_required": true
        }
    ]
}

FTP Configuration Options (Required fields marked with *):
- ftp_host*: FTP server hostname or IP address (required)
- ftp_port: FTP server port (default: 21, auto-detects SFTP if != 21)
- ftp_username*: FTP username (required)
- ftp_password: FTP password (optional for anonymous FTP)
- ftp_remote_path: Remote directory path (default: "/")
- ftp_file_name: Complete filename to download (highest priority, e.g., "data.csv", "report_2024.csv")
- use_date_based_filename: Use date-based filenames (fallback when ftp_file_name not specified)
- date_format: Date format for filename (default: "dd-mm-yyyy", options: "dd-mm-yyyy", "yyyy-mm-dd", "ddmmyyyy", "yyyymmdd")
- ftp_passive_mode: Use passive mode for regular FTP (default: true)
- ftp_use_tls: Use FTP over TLS (FTPS) (default: false)
- ftp_use_sftp: Force SFTP usage (default: false, auto-detected based on port)
- ftp_timeout: Connection timeout in seconds (default: 30)
- ftp_delete_after_download: Delete files after successful download (default: false)
- file_format: File format - only "csv" is supported (default: "csv")
- csv_delimiter: CSV delimiter character (default: ",")
- csv_has_header: Whether CSV files have headers (default: true)
- file_encoding: File encoding (default: "utf-8")

Filename Priority Modes:
1. Fixed Filename Mode (Highest Priority):
   When ftp_file_name is provided in metadata, the processor will look for that exact file.
   For example, if ftp_file_name is "special_report.csv", it will look for "special_report.csv".
   If the file doesn't exist, it will log "file error" in the audit system and skip processing.

2. Date-Based Filename Mode (Fallback):
   When use_date_based_filename is true and ftp_file_name is not provided, the processor will automatically generate filenames based on today's date.
   For example, if today is 2024-01-25 and date_format is "dd-mm-yyyy", it will look for "25-01-2024.csv".
   If the file doesn't exist, it will log "date-based file not found" in the audit system and skip processing.

Validation Rules:
- FTP providers MUST have metadata configuration
- Required fields: ftp_host, ftp_username
- Filename specification (one of the following is required):
  - ftp_file_name: Fixed filename to download (highest priority)
  - use_date_based_filename=true: Enable date-based filename generation (fallback)
- For date-based filenames: use_date_based_filename=true, date_format must be valid
- Only CSV file format is supported
- Port must be between 1-65535 if specified
- Timeout must be positive integer if specified
- Boolean fields must be true/false
- CSV delimiter must be single character
- File encoding must be non-empty string
- Date format must be one of: dd-mm-yyyy, yyyy-mm-dd, ddmmyyyy, yyyymmdd
"""

import asyncio
import ftplib
import io
import logging
import os
import tempfile
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import csv
import paramiko

from libs.models.provider_models import DataType, IngestionType, ProviderEntity, ProviderSummary
from libs.core.job_entities import JobDetail
from services.ingestion.app.core.config import settings
from services.ingestion.app.processors.base_raw_data_processor import BaseRawDataProcessor
from libs.audit_queue.simple_logger import AuditLogger
from libs.messaging.job_publisher import JobPublisher
from libs.models.integration_apilog_entity import IntegrationApiLogEntity
from services.ingestion.app.repositories.integration_apilog_repository import IntegrationApiLogRepository

logger = logging.getLogger("ftp_raw_data_processor")

class FTPRawDataProcessor(BaseRawDataProcessor):
    """
    Service for managing FTP raw data processing tasks with job detail recording
    """

    def __init__(self, audit_logger: AuditLogger):
        """Initialize FTP raw data processor"""
        super().__init__()
        self.audit_logger = audit_logger
        self.job_publisher = JobPublisher()
        self.ftp_timeout = getattr(settings, 'FTP_TIMEOUT', 30)
        self.retry_attempts = getattr(settings, 'FTP_RETRY_ATTEMPTS', 3)
        self.retry_delay = getattr(settings, 'FTP_RETRY_DELAY', 5)
        self.temp_dir = getattr(settings, 'TEMP_DIR', 'temp')
        
        # Ensure temp directory exists
        os.makedirs(self.temp_dir, exist_ok=True)
        
        self.integration_apilog_repository = IntegrationApiLogRepository(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )

    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute FTP raw data processing as a cron job (implementation of abstract method)
        """
        # Process FTP providers for all data types
        return await self.process_all_ftp_data()

    async def _get_all_ftp_providers(self) -> Optional[List[ProviderSummary]]:
        """
        Get all enabled FTP providers regardless of data type
        
        Returns:
            Optional[List[ProviderSummary]]: List of FTP providers or None if error
        """
        try:
            # Get all enabled FTP providers
            provider_response = await self.provider_service.get_all_providers(
                ingestion_type=IngestionType.FTP, enabled=True
            )
            
            if not provider_response or not hasattr(provider_response, 'providers') or not provider_response.providers:
                logger.warning("No FTP providers found")
                return []
            
            return provider_response.providers
            
        except Exception as e:
            logger.error("Error getting FTP providers: %s", str(e))
            return None

    async def process_all_ftp_data(self) -> Dict[str, Any]:
        """
        Execute FTP raw data processing for all data types
        """
        job_type = "ftp_raw_data_processor"
        job_name = "FTP Raw Data Processor"
        schedule = "*/30 * * * *"  # Every 30 minutes
        start_time = datetime.now()

        # Record job start
        execution_id = await self.record_job_start(job_type, job_name, schedule, start_time)

        logger.info("Starting FTP raw data processing cron job at %s", start_time.strftime('%Y-%m-%d %H:%M:%S'))

        try:
            # Get all enabled FTP providers regardless of data type
            providers = await self._get_all_ftp_providers()

            if not providers:
                logger.info("No FTP providers found for processing")
                end_time = datetime.now()
                result = {
                    'status': 'success',
                    'processed_providers': 0,
                    'execution_time': (end_time - start_time).total_seconds()
                }

                # Record job completion
                if execution_id:
                    await self.record_job_completion(
                        job_type, job_name, schedule, start_time, end_time, execution_id,
                        "success", True, execution_id, result
                    )

                return result

            logger.info("Found %d FTP providers for processing", len(providers))

            processed_providers = 0
            failed_providers = 0

            # Process data for each provider
            for provider in providers:
                try:
                    provider_code = provider.code
                    data_type = provider.data_type 

                    logger.info("Processing FTP data for provider: %s, data type: %s", provider_code, data_type)

                    # Process FTP data for this provider
                    result = await self._process_provider_data(provider)

                    if result and result.get('success', False):
                        logger.info("Successfully processed FTP data for provider: %s, data_type: %s",
                                       provider_code, data_type)
                        
                        # Publish job trigger
                        dt_value = str(data_type.value if hasattr(data_type, 'value') else data_type).lower()
                        trigger_job_type = f"{dt_value}_data_processing"
                        logger.info(f"Triggering job: {trigger_job_type} for provider {provider_code}")
                        self.job_publisher.publish_job_trigger(
                            job_type=trigger_job_type,
                            source_service="ingestion",
                            payload={
                                "provider_code": provider_code,
                                "data_type": dt_value,
                                "record_count": result.get('stored_records', 0)
                            }
                        )
                    else:
                        error_msg = result.get('error', 'Unknown error') if result else 'No result returned'
                        logger.error("Failed to process FTP data for provider: %s, data_type: %s, error: %s",
                                       provider_code, data_type, error_msg)                        

                    processed_providers += 1

                except (ValueError, OSError, RuntimeError) as provider_error:
                    failed_providers += 1
                    provider_code = getattr(provider, 'code', 'unknown')
                    logger.error("Exception processing provider %s: %s", provider_code, str(provider_error))

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            # Determine overall status
            overall_success = processed_providers > 0 and failed_providers == 0
            status = "success" if overall_success else ("partial_success" if processed_providers > 0 else "error")

            result = {
                'status': status,
                'processed_providers': processed_providers,
                'failed_providers': failed_providers,
                'total_providers': len(providers),
                'execution_time': execution_time,
                'execution_id': execution_id
            }

            # Record job completion
            if execution_id:
                error_message = None if overall_success else f"Processed {processed_providers}, failed {failed_providers}"
                await self.record_job_completion(
                    job_type, job_name, schedule, start_time, end_time, execution_id,
                    status, overall_success, execution_id, result, error_message
                )

            logger.info("FTP raw data processing cron job completed in %.2fs", execution_time)
            logger.info("   - Processed: %d providers", processed_providers)
            logger.info("   - Failed: %d providers", failed_providers)
            logger.info("   - Total: %d providers", len(providers))
            logger.info("   - Execution ID: %s", execution_id)

            return result

        except (ValueError, OSError, RuntimeError) as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            logger.error("Error in FTP raw data processing cron job after %.2fs: %s", execution_time, str(e))

            result = {'status': 'error', 'error': str(e), 'execution_time': execution_time}

            # Record job completion with error
            if execution_id:
                await self.record_job_completion(
                    job_type, job_name, schedule, start_time, end_time, execution_id,
                    "error", False, execution_id, result, str(e)
                )

            return result

    async def _process_provider_data(self, provider: ProviderSummary) -> Dict[str, Any]:
        """
        Process FTP data for a single provider
        
        Args:
            provider: Provider entity to process
            
        Returns:
            Dict[str, Any]: Processing result
        """
        try:
            provider_code = provider.code
            data_type = provider.data_type
            
            logger.info("Processing provider: %s, data type: %s", provider_code, data_type)
            
            # Get full provider entity for processing
            full_provider = await self._get_full_provider_entity(provider_code)
            if not full_provider:
                logger.error("Could not retrieve full provider entity for: %s", provider_code)
                return {'success': False, 'error': 'Provider entity not found'}
            
            # Extract FTP configuration from provider metadata
            ftp_config = self._extract_ftp_config_from_provider(full_provider)
            if not ftp_config:
                logger.warning("No FTP configuration found for provider: %s", provider_code)
                return {'success': False, 'error': 'No FTP configuration found in provider metadata'}
            
            all_downloaded_data = []
            successful_files = 0
            failed_files = 0
            
            # Download data from FTP server
            log_ids = []
            try:
                download_result = await self._download_ftp_data(ftp_config, full_provider)
                
                downloaded_data = download_result.get("data", [])
                log_id = download_result.get("log_id")
                
                if downloaded_data:
                    all_downloaded_data.extend(downloaded_data)
                    successful_files += 1
                    if log_id:
                        log_ids.append(log_id)
                    logger.info("Successfully downloaded %d records from FTP server for provider: %s", 
                               len(downloaded_data), provider_code)
                else:
                    failed_files += 1
                    logger.warning("No data downloaded from FTP server for provider: %s", provider_code)
                    if log_id: # Still capture log id if we got file content but parsing failed (empty)
                        log_ids.append(log_id)
                        
            except Exception as ftp_error:
                failed_files += 1
                logger.error("Error downloading from FTP server for provider %s: %s", provider_code, str(ftp_error))
            
            if not all_downloaded_data:
                logger.warning("No data downloaded for provider: %s", provider_code)
                return {'success': False, 'error': 'No data downloaded from FTP server'}
            
            # Map data to schema first (apply field mappings)
            mapped_data = self.map_data_to_schema(all_downloaded_data, full_provider)
            
            # Then validate the mapped data
            validation_result = self.validate_data_schema(mapped_data, full_provider)
            if not validation_result["success"]:
                logger.error("Data validation failed for provider %s: %s", 
                           provider_code, "; ".join(validation_result["errors"][:3]))
                return {'success': False, 'error': 'Data validation failed', 'validation_errors': validation_result["errors"]}
            
            # Use validated data
            validated_data = validation_result["validated_data"]
            
            # Store data using raw data service
            storage_result = await self._store_ftp_data(validated_data, full_provider, log_ids)
            
            if storage_result["success"]:
                logger.info("Successfully processed provider %s: %d records stored", 
                           provider_code, storage_result.get("stored_records", 0))
                return {
                    'success': True,
                    'provider_code': provider_code,
                    'data_type': data_type,
                    'downloaded_records': len(all_downloaded_data),
                    'validated_records': validation_result["validated_records"],
                    'stored_records': storage_result.get("stored_records", 0),
                    'successful_files': successful_files,
                    'failed_files': failed_files
                }
            else:
                logger.error("Failed to store data for provider %s: %s", provider_code, storage_result.get("error"))
                return {'success': False, 'error': storage_result.get("error")}
                
        except Exception as e:
            logger.error("Unexpected error processing provider %s: %s", provider.code, str(e))
            return {'success': False, 'error': str(e)}

    async def _get_full_provider_entity(self, provider_code: str) -> Optional[ProviderEntity]:
        """
        Get full provider entity by code
        
        Args:
            provider_code: Provider code to retrieve
            
        Returns:
            Optional[ProviderEntity]: Full provider entity or None if not found
        """
        try:
            # Use provider service to get full provider entity
            provider_entity = await self.provider_service.get_provider_by_code(provider_code)
            if provider_entity:
                return provider_entity
            else:
                logger.error("Provider not found for code: %s", provider_code)
                return None
        except Exception as e:
            logger.error("Error retrieving provider entity for code %s: %s", provider_code, str(e))
            return None

    async def _store_ftp_data(self, data: List[Dict[str, Any]], provider: ProviderEntity, log_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Store FTP data using the raw data service
        
        Args:
            data: Processed data to store
            provider: Provider entity
            log_ids: List of integration log IDs
            
        Returns:
            Dict[str, Any]: Storage result
        """
        try:
            if not data:
                return {'success': False, 'error': 'No data to store'}
            
            additional_metadata = provider.metadata.get("config", {}).copy() if provider.metadata else {}
            if log_ids:
                if len(log_ids) == 1:
                    additional_metadata["integration_apilog_id"] = log_ids[0]
                else:
                    additional_metadata["integration_apilog_id"] = log_ids

            # Use raw data service to store the data
            result = await self.raw_data_service.store_raw_data(
                raw_data=data,
                provider=provider,
                additional_metadata=additional_metadata
            )
            
            if result.get("success"):
                return {
                    'success': True,
                    'stored_records': len(data),
                    'batch_id': result.get("batch_id")
                }
            else:
                return {'success': False, 'error': result.get("error", "Unknown storage error")}
                
        except Exception as e:
            logger.error("Error storing FTP data: %s", str(e))
            return {'success': False, 'error': str(e)}

    def _extract_ftp_config_from_provider(self, provider: ProviderEntity) -> Optional[Dict[str, Any]]:
        """
        Extract FTP configuration from provider metadata

        Args:
            provider: Provider entity containing metadata

        Returns:
            Optional[Dict[str, Any]]: FTP configuration or None if invalid
        """
        try:
            metadata = provider.metadata or {}

            if not isinstance(metadata, dict):
                logger.warning("Provider %s metadata is not a dictionary", provider.code)
                return None
            
            # Extract FTP configuration
            ftp_config = {
                'host': metadata.get('ftp_host'),
                'port': metadata.get('ftp_port', 21),
                'username': metadata.get('ftp_username'),
                'password': metadata.get('ftp_password'),
                'remote_path': metadata.get('ftp_remote_path', '/'),
                'file_name': metadata.get('ftp_file_name'),
                'use_date_based_filename': metadata.get('use_date_based_filename', False),  # New option
                'date_format': metadata.get('date_format', 'dd-mm-yyyy'),  # Date format for filename
                'passive_mode': metadata.get('ftp_passive_mode', True),
                'use_tls': metadata.get('ftp_use_tls', False),
                'use_sftp': metadata.get('ftp_use_sftp', False),  # Auto-detect SFTP based on port
                'timeout': metadata.get('ftp_timeout', self.ftp_timeout),
                'delete_after_download': metadata.get('ftp_delete_after_download', False),
                'file_format': metadata.get('file_format', 'csv'),  # Only CSV format supported
                'csv_delimiter': metadata.get('csv_delimiter', ','),
                'csv_has_header': metadata.get('csv_has_header', True),
                'encoding': metadata.get('file_encoding', 'utf-8')
            }

            # Auto-detect SFTP based on port if not explicitly set
            if not ftp_config.get('use_sftp') and ftp_config['port'] != 21:
                ftp_config['use_sftp'] = True
                logger.info("Auto-detected SFTP for provider %s based on port %d", provider.code, ftp_config['port'])

            # Validate required fields
            if not ftp_config['host']:
                logger.error("FTP host is required for provider %s", provider.code)
                return None
            
            if not ftp_config['username']:
                logger.error("FTP username is required for provider %s", provider.code)
                return None
            
            # Handle filename logic with priority: ftp_file_name > use_date_based_filename
            if ftp_config.get('file_name'):
                # Priority 1: Use fixed filename from ftp_file_name metadata (highest priority)
                ftp_config['is_date_based'] = False
                logger.info("Using fixed filename for provider %s: %s", provider.code, ftp_config['file_name'])
            elif ftp_config.get('use_date_based_filename', False):
                # Priority 2: Generate filename based on today's date
                today = datetime.now()
                date_format = ftp_config.get('date_format', 'dd-mm-yyyy')
                
                if date_format == 'dd-mm-yyyy':
                    date_str = today.strftime('%d-%m-%Y')
                elif date_format == 'yyyy-mm-dd':
                    date_str = today.strftime('%Y-%m-%d')
                elif date_format == 'ddmmyyyy':
                    date_str = today.strftime('%d%m%Y')
                elif date_format == 'yyyymmdd':
                    date_str = today.strftime('%Y%m%d')
                else:
                    logger.error("Unsupported date format '%s' for provider %s. Supported formats: dd-mm-yyyy, yyyy-mm-dd, ddmmyyyy, yyyymmdd", 
                               date_format, provider.code)
                    return None
                
                # Generate filename with date and CSV extension
                ftp_config['file_name'] = f"{date_str}.csv"
                ftp_config['is_date_based'] = True
                ftp_config['date_used'] = date_str
                
                logger.info("Using date-based filename for provider %s: %s (format: %s)", 
                           provider.code, ftp_config['file_name'], date_format)
            else:
                logger.error("FTP filename is required for provider %s. Please specify 'ftp_file_name' in metadata or enable 'use_date_based_filename'", provider.code)
                return None

            logger.info("Extracted FTP configuration for provider %s: host=%s, port=%d, path=%s, filename=%s", 
                       provider.code, ftp_config['host'], ftp_config['port'], ftp_config['remote_path'], 
                       ftp_config.get('file_name', 'NOT_SPECIFIED'))
            return ftp_config

        except (ValueError, TypeError, AttributeError) as e:
            logger.error("Error extracting FTP configuration from provider %s metadata: %s", provider.code, str(e))
            return None

    async def _download_ftp_data(self, ftp_config: Dict[str, Any], provider: ProviderEntity) -> Dict[str, Any]:
        """
        Download data from FTP server
        
        Returns:
             Dict[str, Any]: Result with 'data' and 'log_id'
        """
        try:
            # Use asyncio.to_thread to run FTP operations in a separate thread
            return await asyncio.to_thread(self._download_ftp_data_sync, ftp_config, provider)
        except Exception as e:
            logger.error("Error in async FTP download for provider %s: %s", provider.code, str(e))
            return {"data": [], "log_id": None}

    def _download_ftp_data_sync(self, ftp_config: Dict[str, Any], provider: ProviderEntity) -> Dict[str, Any]:
        """Synchronous FTP/SFTP download operation"""
        # Check if SFTP should be used
        if ftp_config.get('use_sftp', False):
            return self._download_sftp_data_sync(ftp_config, provider)
        else:
            return self._download_regular_ftp_data_sync(ftp_config, provider)

    def _download_regular_ftp_data_sync(self, ftp_config: Dict[str, Any], provider: ProviderEntity) -> Dict[str, Any]:
        """
        Synchronous regular FTP download operation
        
        Returns:
            Dict[str, Any]: download result with data and log_id
        """
        ftp = None
        result = {"data": [], "log_id": None}
        
        try:
            # Establish FTP connection
            if ftp_config.get('use_tls', False):
                ftp = ftplib.FTP_TLS()
            else:
                ftp = ftplib.FTP()
            
            # Set timeout
            ftp.timeout = ftp_config.get('timeout', self.ftp_timeout)
            
            # Connect to server
            logger.info("Connecting to FTP server %s:%d for provider %s", 
                       ftp_config['host'], ftp_config['port'], provider.code)
            ftp.connect(ftp_config['host'], ftp_config['port'])
            
            # Login
            ftp.login(ftp_config['username'], ftp_config.get('password', ''))
            
            # Enable TLS if required
            if ftp_config.get('use_tls', False):
                ftp.prot_p()  # Enable data channel encryption
            
            # Set passive mode
            ftp.set_pasv(ftp_config.get('passive_mode', True))
            
            # Change to remote directory
            remote_path = ftp_config.get('remote_path', '/')
            if remote_path and remote_path != '/':
                try:
                    ftp.cwd(remote_path)
                    logger.info("Changed to directory: %s", remote_path)
                except Exception as e:
                    logger.warning("Could not change to directory %s: %s", remote_path, str(e))
                    # Log directory not found in audit system
                    self.audit_logger.log_system_operation(
                        service="ingestion",
                        operation="ftp_directory_not_found",
                        message=f"FTP directory not found: {remote_path}",
                        provider_code=provider.code,
                        data_type=provider.data_type.value,
                        expected_directory=remote_path,
                        error_message=str(e)
                    )
            
            # Get specific filename to download
            filename = ftp_config.get('file_name')
            if not filename:
                logger.error("No filename specified in FTP configuration for provider %s", provider.code)
                return {"data": [], "log_id": None}
            
            logger.info("Downloading specific file: %s for provider %s", filename, provider.code)
            
            # Download and process the specific file
            try:
                # _download_and_parse_file_ftp returns Dict with data and log_id
                download_result = self._download_and_parse_file_ftp(ftp, filename, ftp_config, provider)
                
                file_data = download_result.get("data", [])
                log_id = download_result.get("log_id")
                
                result["log_id"] = log_id
                
                if file_data:
                    result["data"] = file_data
                    logger.info("Successfully parsed %d records from file %s", len(file_data), filename)
                    
                    # Delete file after download if configured
                    if ftp_config.get('delete_after_download', False):
                        try:
                            ftp.delete(filename)
                            logger.info("Deleted file %s from FTP server", filename)
                        except Exception as delete_error:
                            logger.warning("Could not delete file %s: %s", filename, str(delete_error))
                else:
                    logger.warning("No data parsed from file %s", filename)
                    
            except Exception as file_error:
                # Log file not found in audit log
                if ftp_config.get('is_date_based', False):
                    logger.warning("Date-based file %s not found for provider %s", filename, provider.code)
                    # Log to audit system
                    self.audit_logger.log_system_operation(
                        service="ingestion",
                        operation="ftp_file_not_found",
                        message=f"Date-based file not found: {filename}",
                        provider_code=provider.code,
                        data_type=provider.data_type.value,
                        expected_filename=filename,
                        date_used=ftp_config.get('date_used'),
                        file_path=ftp_config.get('remote_path', '/')
                    )
                else:
                    logger.error("Error processing file %s: %s", filename, str(file_error))
                    # Log to audit system
                    self.audit_logger.log_system_operation(
                        service="ingestion",
                        operation="ftp_file_error",
                        message=f"Error processing file: {filename}",
                        provider_code=provider.code,
                        data_type=provider.data_type.value,
                        error_message=str(file_error),
                        file_path=ftp_config.get('remote_path', '/')
                    )
                return {"data": [], "log_id": None}
            
            return result
            
        except ftplib.all_errors as ftp_error:
            logger.error("FTP error for provider %s: %s", provider.code, str(ftp_error))
            return {"data": [], "log_id": None}
        except Exception as e:
            logger.error("Unexpected error downloading FTP data for provider %s: %s", provider.code, str(e))
            return {"data": [], "log_id": None}
        finally:
            if ftp:
                try:
                    ftp.quit()
                except:
                    try:
                        ftp.close()
                    except:
                        pass

    def _download_sftp_data_sync(self, ftp_config: Dict[str, Any], provider: ProviderEntity) -> Dict[str, Any]:
        """
        Synchronous SFTP download operation
        
        Returns:
            Dict[str, Any]: download result
        """
        ssh_client = None
        sftp_client = None
        result = {"data": [], "log_id": None}
        
        try:
            # Create SSH client
            ssh_client = paramiko.SSHClient()
            ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Connect to server
            logger.info("Connecting to SFTP server %s:%d for provider %s", 
                       ftp_config['host'], ftp_config['port'], provider.code)
            
            ssh_client.connect(
                hostname=ftp_config['host'],
                port=ftp_config['port'],
                username=ftp_config['username'],
                password=ftp_config.get('password', ''),
                timeout=ftp_config.get('timeout', self.ftp_timeout)
            )
            
            # Create SFTP client
            sftp_client = ssh_client.open_sftp()
            
            # Change to remote directory
            remote_path = ftp_config.get('remote_path', '/')
            if remote_path and remote_path != '/':
                try:
                    sftp_client.chdir(remote_path)
                    logger.info("Changed to directory: %s", remote_path)
                except Exception as e:
                    logger.warning("Could not change to directory %s: %s", remote_path, str(e))
                    # Log directory not found in audit system
                    self.audit_logger.log_system_operation(
                        service="ingestion",
                        operation="ftp_directory_not_found",
                        message=f"FTP directory not found: {remote_path}",
                        provider_code=provider.code,
                        data_type=provider.data_type.value,
                        expected_directory=remote_path,
                        error_message=str(e)
                    )
                    # Try to use the path as is
            
            # Get specific filename to download
            filename = ftp_config.get('file_name')
            if not filename:
                logger.error("No filename specified in SFTP configuration for provider %s", provider.code)
                return {"data": [], "log_id": None}
            
            logger.info("Downloading specific file: %s for provider %s", filename, provider.code)
            
            # Download and process the specific file
            try:
                download_result = self._download_and_parse_file_sftp(sftp_client, filename, ftp_config, provider)
                
                file_data = download_result.get("data", [])
                log_id = download_result.get("log_id")
                
                result["log_id"] = log_id
                
                if file_data:
                    result["data"] = file_data
                    logger.info("Successfully parsed %d records from file %s", len(file_data), filename)
                    
                    # Delete file after download if configured
                    if ftp_config.get('delete_after_download', False):
                        try:
                            sftp_client.remove(filename)
                            logger.info("Deleted file %s from SFTP server", filename)
                        except Exception as delete_error:
                            logger.warning("Could not delete file %s: %s", filename, str(delete_error))
                else:
                    logger.warning("No data parsed from file %s", filename)
                    
            except Exception as file_error:
                # Log file not found in audit log
                # ... same as FTP ...
                if ftp_config.get('is_date_based', False):
                    # ...
                    logger.warning("Date-based file %s not found", filename)
                else:
                    logger.error("Error processing file %s: %s", filename, str(file_error))
                
                return {"data": [], "log_id": None}
            
            return result
            
        except paramiko.AuthenticationException as auth_error:
            logger.error("SFTP authentication failed for provider %s: %s", provider.code, str(auth_error))
            return {"data": [], "log_id": None}
        except paramiko.SSHException as ssh_error:
            logger.error("SFTP SSH error for provider %s: %s", provider.code, str(ssh_error))
            return {"data": [], "log_id": None}
        except Exception as e:
            logger.error("Unexpected error downloading SFTP data for provider %s: %s", provider.code, str(e))
            return {"data": [], "log_id": None}
        finally:
            if sftp_client:
                try:
                    sftp_client.close()
                except:
                    pass
            if ssh_client:
                try:
                    ssh_client.close()
                except:
                    pass



    def _download_and_parse_file_ftp(self, ftp: ftplib.FTP, filename: str, ftp_config: Dict[str, Any], 
                                provider: ProviderEntity) -> Dict[str, Any]:
        """
        Download a single file from FTP and parse its contents
        
        Returns:
            Dict[str, Any]: Result with 'data' and 'log_id'
        """
        try:
            # Download file to memory
            file_data = io.BytesIO()
            ftp.retrbinary(f'RETR {filename}', file_data.write)
            file_data.seek(0)
            
            # Blind Log the raw file content
            # Decode for logging (might need to be careful with binary, but FTP Raw Processor assumes CSV/Text usually)
            # Actually we can log the bytes if we base64 encode, or decode to string
            # IntegrationApiLogEntity expects 'raw_data: Any'.
            # Let's decode to string using config encoding or utf-8
            encoding = ftp_config.get('encoding', 'utf-8')
            try:
                content_str = file_data.getvalue().decode(encoding)
            except:
                content_str = file_data.getvalue().decode('utf-8', errors='replace')

            log_id = None
            try:
                log_entity = IntegrationApiLogEntity(
                    provider_id=str(provider.id) if provider.id else provider.code,
                    raw_data=content_str, 
                    timestamp=datetime.now(),
                    metadata={
                        "filename": filename,
                        "size": len(content_str),
                        "source": "ftp"
                    }
                )
                log_id = self.integration_apilog_repository.save_log(provider.data_type.lower(), log_entity)
            except Exception as e:
                logger.error("Failed to integration log FTP file: %s", e)
            
            # Check file format - only CSV is supported
            file_format = ftp_config.get('file_format', 'csv').lower()
            file_extension = filename.lower().split('.')[-1] if '.' in filename else ''
            
            # Reject non-CSV formats
            if file_extension in ['xlsx', 'xls', 'json', 'txt'] and file_format == 'csv':
                raise ValueError(f"File {filename} has extension '{file_extension}' but only CSV files are supported")
            
            if file_format != 'csv':
                raise ValueError(f"File format '{file_format}' is not supported. Only CSV format is allowed")
            
            # Parse CSV content (we already decoded)
            parsed_data = self._parse_file_content(content_str, ftp_config, filename, provider)
            return {"data": parsed_data, "log_id": log_id}
                
        except Exception as e:
            logger.error("Error downloading and parsing FTP file %s: %s", filename, str(e))
            return {"data": [], "log_id": None}

    def _download_and_parse_file_sftp(self, sftp_client, filename: str, ftp_config: Dict[str, Any], 
                                     provider: ProviderEntity) -> Dict[str, Any]:
        """
        Download a single file from SFTP and parse its contents
        
        Returns:
            Dict[str, Any]: Result with 'data' and 'log_id'
        """
        try:
            # Download file to memory
            file_data = io.BytesIO()
            sftp_client.getfo(filename, file_data)
            file_data.seek(0)
            
            # Blind Log
            encoding = ftp_config.get('encoding', 'utf-8')
            try:
                content_str = file_data.getvalue().decode(encoding)
            except:
                content_str = file_data.getvalue().decode('utf-8', errors='replace')
                
            log_id = None
            try:
                log_entity = IntegrationApiLogEntity(
                    provider_id=str(provider.id) if provider.id else provider.code,
                    raw_data=content_str, 
                    timestamp=datetime.now(),
                    metadata={
                        "filename": filename,
                        "size": len(content_str),
                        "source": "sftp"
                    }
                )
                log_id = self.integration_apilog_repository.save_log(provider.data_type.lower(), log_entity)
            except Exception as e:
                logger.error("Failed to integration log SFTP file: %s", e)
                
            
            # Check file format - only CSV is supported
            file_format = ftp_config.get('file_format', 'csv').lower()
            file_extension = filename.lower().split('.')[-1] if '.' in filename else ''
            
            # Reject non-CSV formats
            if file_extension in ['xlsx', 'xls', 'json', 'txt'] and file_format == 'csv':
                raise ValueError(f"File {filename} has extension '{file_extension}' but only CSV files are supported")
            
            if file_format != 'csv':
                raise ValueError(f"File format '{file_format}' is not supported. Only CSV format is allowed")
            
            # Parse CSV content (we already decoded)
            return {"data": self._parse_file_content(content_str, ftp_config, filename, provider), "log_id": log_id}
                
        except Exception as e:
            logger.error("Error downloading and parsing SFTP file %s: %s", filename, str(e))
            return {"data": [], "log_id": None}

    def _parse_file_content(self, content: str, ftp_config: Dict[str, Any], filename: str, provider: Optional[ProviderEntity] = None) -> List[Dict[str, Any]]:
        """
        Parse file content - only CSV format is supported
        
        Args:
            content: File content as string
            ftp_config: FTP configuration
            filename: Name of the source file
            provider: Provider entity for parser selection (optional)
            
        Returns:
            List[Dict[str, Any]]: Parsed data records
            
        Raises:
            ValueError: If file format is not CSV
        """
        file_format = ftp_config.get('file_format', 'csv').lower()
        
        if file_format == 'csv':
            return self._parse_csv_content(content, ftp_config, filename, provider)
        else:
            raise ValueError(f"Unsupported file format '{file_format}' for file {filename}. Only CSV format is supported.")



    def _download_and_parse_file(self, ftp: ftplib.FTP, filename: str, ftp_config: Dict[str, Any], 
                                provider: ProviderEntity) -> List[Dict[str, Any]]:
        """
        Download a single file from FTP and parse its contents (legacy method for compatibility)
        """
        result = self._download_and_parse_file_ftp(ftp, filename, ftp_config, provider)
        return result.get("data", [])

    def _parse_csv_content(self, content: str, ftp_config: Dict[str, Any], filename: str, provider: Optional[ProviderEntity] = None) -> List[Dict[str, Any]]:
        """
        Parse CSV content into data records using appropriate parser
        
        Args:
            content: CSV file content as string
            ftp_config: FTP configuration containing CSV settings
            filename: Name of the source file
            provider: Provider entity for parser selection (optional)
            
        Returns:
            List[Dict[str, Any]]: Parsed data records
        """
        try:
            # Import parser factory
            from libs.parsers import create_parser
            
            # Create appropriate parser based on provider configuration
            parser = create_parser(provider)
            
            logger.info("Using parser: %s for file: %s (provider: %s)", 
                       parser.__class__.__name__, 
                       filename,
                       provider.code if provider else 'unknown')
            
            # Prepare parsing parameters from FTP config
            delimiter = ftp_config.get('csv_delimiter', ',')
            has_header = ftp_config.get('csv_has_header', True)
            
            # Handle tab delimiter properly
            if delimiter == '\\t':
                delimiter = '\t'
            
            # Parse content using selected parser
            if parser.get_parser_type() == 'standard':
                # For standard parser, pass FTP-specific parameters
                raw_data = parser.parse(content, provider, 
                                      delimiter=delimiter, 
                                      has_header=has_header)
            else:
                # For other parsers (like trading), use default parsing
                raw_data = parser.parse(content, provider)
            
            # Validate time format fields before processing
            time_validation_errors = self._validate_time_format_in_records(raw_data, filename)
            if time_validation_errors:
                error_msg = f"Time format validation failed for {filename}: {', '.join(time_validation_errors[:5])}"
                if len(time_validation_errors) > 5:
                    error_msg += f" (and {len(time_validation_errors) - 5} more errors)"
                logger.error(error_msg)
                raise ValueError(error_msg)
            
            # Add FTP-specific metadata to each record
            current_time = datetime.now()
            for record in raw_data:
                record['source_file'] = filename
                record['downloaded_at'] = current_time
            
            logger.info("Parsed %d records from file %s using %s", 
                       len(raw_data), filename, parser.__class__.__name__)
            return raw_data
            
        except Exception as e:
            logger.error("Error parsing CSV content from file %s: %s", filename, str(e))
            # Fallback to original parsing logic for compatibility
            return self._parse_csv_content_fallback(content, ftp_config, filename)
    
    def _parse_csv_content_fallback(self, content: str, ftp_config: Dict[str, Any], filename: str) -> List[Dict[str, Any]]:
        """
        Fallback CSV parsing logic (original implementation)
        
        Args:
            content: CSV file content as string
            ftp_config: FTP configuration containing CSV settings
            filename: Name of the source file
            
        Returns:
            List[Dict[str, Any]]: Parsed data records
        """
        try:
            # Create CSV reader
            delimiter = ftp_config.get('csv_delimiter', ',')
            has_header = ftp_config.get('csv_has_header', True)
            
            # Handle tab delimiter properly
            if delimiter == '\\t':
                delimiter = '\t'
            
            lines = content.strip().split('\n')
            if not lines:
                logger.warning("Empty CSV content in file %s", filename)
                return []
            
            csv_reader = csv.reader(lines, delimiter=delimiter)
            rows = list(csv_reader)
            
            if not rows:
                logger.warning("No rows found in CSV file %s", filename)
                return []
            
            data_records = []
            headers = None
            
            if has_header and len(rows) > 1:
                headers = [header.strip().lstrip('\ufeff') for header in rows[0]]
                data_rows = rows[1:]
            else:
                # Generate generic headers
                headers = [f'column_{i+1}' for i in range(len(rows[0]))]
                data_rows = rows
            
            current_time = datetime.now()
            
            # Convert rows to dictionaries
            for row_num, row in enumerate(data_rows, start=1):
                if len(row) == 0:  # Skip empty rows
                    continue
                    
                # Pad row with empty strings if it has fewer columns than headers
                while len(row) < len(headers):
                    row.append('')
                
                # Create record dictionary
                record = {}
                for i, header in enumerate(headers):
                    # Clean header name (remove BOM and whitespace)
                    clean_header = header.strip().lstrip('\ufeff')
                    if i < len(row):
                        record[clean_header] = row[i].strip() if row[i] else ''
                    else:
                        record[clean_header] = ''
                
                # Add metadata
                record['source_file'] = filename
                record['downloaded_at'] = current_time
                record['row_number'] = row_num
                
                data_records.append(record)
            
            logger.info("Parsed %d records from CSV file %s (fallback)", len(data_records), filename)
            return data_records
            
        except Exception as e:
            logger.error("Error in fallback CSV parsing from file %s: %s", filename, str(e))
            return []
    
    def _validate_time_format_in_records(self, records: List[Dict[str, Any]], filename: str) -> List[str]:
        """
        Validate time format fields in parsed records.
        Checks if fields like time_start, time_end, etc. are in HH:MM format.
        
        Args:
            records: List of parsed data records
            filename: Source filename for error messages
            
        Returns:
            List[str]: List of validation error messages (empty if valid)
        """
        import re
        
        errors = []
        # Strict HH:MM format (00:00 to 23:59) or 24:00 (special case for end of day)
        time_pattern = re.compile(r'^(([0-1][0-9]|2[0-3]):[0-5][0-9]|24:00)$')
        time_field_patterns = ['time_start', 'time_end', 'start_time', 'end_time']
        # Exclude patterns that should NOT be validated as time formats
        exclude_patterns = ['time_block', 'timeblock', 'block']
        
        if not records:
            return errors
        
        # Find time-related columns from first record
        time_columns = []
        if records:
            for key in records[0].keys():
                key_lower = key.lower().strip()
                # Check if it matches time patterns but exclude time_block and similar
                if (any(pattern in key_lower for pattern in time_field_patterns) and 
                    not any(exclude_pattern in key_lower for exclude_pattern in exclude_patterns)):
                    time_columns.append(key)
        
        # If no time columns found, no validation needed
        if not time_columns:
            return errors
        
        # Validate each record
        for idx, record in enumerate(records, start=1):
            for col in time_columns:
                value = str(record.get(col, '')).strip()
                
                # Skip empty values
                if not value or value.lower() in ['none', 'null', '']:
                    continue
                
                # Validate format
                if not time_pattern.match(value):
                    errors.append(
                        f"File '{filename}', Row {idx}, Column '{col}': Invalid time format '{value}'. "
                        f"Expected HH:MM format (e.g., 09:30, 14:45)"
                    )
                    
                    # Limit error messages to avoid overwhelming response
                    if len(errors) >= 10:
                        errors.append(f"... and more errors in '{filename}'. Please fix the above issues first.")
                        return errors
        
        return errors


    async def _get_provider_details(self, ingestion_type: IngestionType, data_type: DataType) -> Optional[List[ProviderSummary] | None]:
        """
        Get complete provider details for validation and storage

        Args:
            ingestion_type: Ingestion type code
            data_type: Data type (demand, supply, etc.) - Optional, if None gets all data types

        Returns:
            Optional[List[ProviderSummary]] | None: Complete provider objects or None if not found
        """
        try:
            # Get provider details - if data_type is None, get all enabled FTP providers
            if data_type:
                providerResponse = await self.provider_service.get_all_providers(
                    ingestion_type=ingestion_type, data_type=data_type, enabled=True
                )
            else:
                # Get all enabled FTP providers regardless of data type
                providerResponse = await self.provider_service.get_all_providers(
                    ingestion_type=ingestion_type, enabled=True
                )

            if not providerResponse:
                logger.error("No providers found for ingestion_type: %s", ingestion_type)
                return None

            if not providerResponse or (hasattr(providerResponse, 'providers') and not providerResponse.providers):
                logger.error("No provider exist for ingestion_type: %s", ingestion_type)
                return None

            return providerResponse.providers

        except (ValueError, TypeError, OSError, RuntimeError) as e:
            logger.error("Error getting provider details for %s: %s", ingestion_type, e)
            return None

    def map_data_to_schema(self, data: List[Dict[str, Any]], provider: ProviderEntity) -> List[Dict[str, Any]]:
        """
        Map raw data to provider schema format using proper mapping field
        
        Args:
            data: Raw data records
            provider: Provider entity containing schema and mapping configuration
            
        Returns:
            List[Dict[str, Any]]: Mapped data records
        """
        try:
            if not provider.fields:
                logger.warning("No schema defined for provider %s, returning data as-is", provider.code)
                return data

            # Create mapping dictionary from provider.mapping field
            field_mappings = {}
            if provider.mapping:
                for mapping_obj in provider.mapping:
                    field_mappings[mapping_obj.serve_field] = mapping_obj.provider_field
            
            mapped_data = []
            for record in data:
                mapped_record = {}
                
                for field in provider.fields:
                    field_name = field.field_name
                    
                    # Check if there's a mapping for this field
                    if field_name in field_mappings:
                        source_field = field_mappings[field_name]
                        if source_field in record:
                            mapped_record[field_name] = record[source_field]
                        else:
                            # Try case-insensitive match
                            source_field_lower = source_field.lower()
                            for key, value in record.items():
                                if key.lower() == source_field_lower:
                                    mapped_record[field_name] = value
                                    break
                            else:
                                if field.is_required:
                                    logger.warning("Required field '%s' not found in data (mapped from '%s')", field_name, source_field)
                    else:
                        # Direct field name match
                        if field_name in record:
                            mapped_record[field_name] = record[field_name]
                        else:
                            # Try case-insensitive match
                            field_name_lower = field_name.lower()
                            for key, value in record.items():
                                if key.lower() == field_name_lower:
                                    mapped_record[field_name] = value
                                    break
                            else:
                                if field.is_required:
                                    logger.warning("Required field '%s' not found in data", field_name)
                
                mapped_data.append(mapped_record)

            logger.info("Mapped %d records to schema format", len(mapped_data))
            return mapped_data

        except Exception as e:
            logger.error("Error mapping data to schema: %s", str(e))
            return data  # Return original data if mapping fails
