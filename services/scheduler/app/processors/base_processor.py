"""
Base processor class for handling data processing logic triggered by cron jobs.
This class provides the main processing workflow for different data types.
"""

import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Any, Optional
from enum import Enum

from bson import ObjectId

from libs.repository.provider_repository import ProviderRepository
from libs.repository.raw_data_repository import RawDataRepository
from libs.middleware.correlation import set_correlation_id, get_correlation_id
from libs.core.job_entities import JobDetail
from libs.core.job_factory import create_job_execution_log_repository

logger = logging.getLogger(__name__)


class ProcessingStatus(Enum):
    """Enum for raw data processing status"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class BaseProcessor(ABC):
    """
    Base class for data processors.
    Handles the main processing workflow triggered by cron jobs.
    """
    
    def __init__(self):
        from ..core.config import settings
        from libs.core.job_factory import create_job_data_repository, create_job_detail_repository
        
        self.provider_repository = ProviderRepository(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )
        self.raw_data_repository = None  # Will be initialized dynamically based on data_type
        
        # Initialize job repositories for execution tracking
        self.job_data_repository = create_job_data_repository(
            settings.AUDIT_DB_URI,
            settings.AUDIT_DB_NAME,
            settings.JOB_DATA_COLLECTION
        )
        self.job_detail_repository = create_job_detail_repository(
            settings.AUDIT_DB_URI,
            settings.AUDIT_DB_NAME,
            settings.JOB_DETAIL_COLLECTION
        )
        self.job_execution_log_repository = create_job_execution_log_repository(
            settings.AUDIT_DB_URI,
            settings.AUDIT_DB_NAME
        )
        
        # Cache service is now available via middleware
        self.cache_service = None
        
        # Current execution_id serves as correlation ID for this processor instance
        self.current_execution_id = None
        
    async def process_metadata_trigger(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main entry point triggered by cron job.
        Processes metadata and orchestrates the data processing workflow.
        
        Args:
            metadata: Metadata containing data_type and other processing info
            
        Returns:
            Dict[str, Any]: Result dict with 'success' key and details
        """
        try:
            data_type = metadata.get("data_type")
            if not data_type:
                logger.error("No data_type found in metadata")
                return {"success": False, "error": "No data_type found in metadata"}
                
            logger.info(f"Starting processing for data_type: {data_type}")
            
            # Initialize raw data repository dynamically for this data type
            from ..core.config import settings
            self.raw_data_repository = RawDataRepository(
                mongodb_uri=settings.MONGODB_URI,
                database_name=settings.MONGODB_DATABASE,
                data_type=data_type
            )
            
            # Check if there's any provider configuration for this data_type
            provider_configs = await self._get_provider_configurations(data_type)
            logger.info(f"Found {len(provider_configs)} provider configurations for data_type: {data_type}")
            
            if not provider_configs:
                logger.warning(f"No provider configurations found for data_type: {data_type}")
                return {"success": False, "error": "No provider configs found"}
                
            success_count = 0
            total_providers = len(provider_configs)
            
            results_per_provider = {}
            # Process each provider configuration
            for provider_config in provider_configs:
                provider_id = provider_config.get("_id")
                provider_code = provider_config.get("code")
                logger.info(f"Processing provider: {provider_code} (ID: {provider_id})")
                
                try:
                    res = await self._process_provider_data(provider_config, metadata)
                    results_per_provider[provider_code] = res
                    if res.get("success"):
                        success_count += 1
                        logger.info(f"Successfully processed provider: {provider_code}")
                    else:
                        logger.warning(f"Failed to process provider: {provider_code}")
                except Exception as e:
                    logger.error(f"Error processing provider {provider_config.get('_id')}: {str(e)}")
                    results_per_provider[provider_code] = {"success": False, "error": str(e)}
                    continue
                    
            logger.info(f"Processed {success_count}/{total_providers} providers successfully for data_type: {data_type}")
            
            final_result = {
                "success": success_count > 0,
                "success_count": success_count,
                "total_providers": total_providers,
                "details": results_per_provider
            }
            return final_result
            
        except Exception as e:
            logger.error(f"Error in process_metadata_trigger: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _get_provider_configurations(self, data_type: str) -> List[Dict[str, Any]]:
        """
        Get provider configurations for the given data_type.
        For exchange data type, this will return all exchange providers regardless of sub_type.

        Args:
            data_type: The type of data to process

        Returns:
            List of provider configurations
        """
        try:
            # Query provider repository for configurations matching the data_type
            # Use 'enabled' field instead of 'is_active' to match provider schema
            query = {"data_type": data_type, "enabled": True}
            providers = await self.provider_repository.find_many(query)
            return providers
        except Exception as e:
            logger.error(f"Error getting provider configurations: {str(e)}")
            return []
    
    async def _process_provider_data(self, provider_config: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process raw data for a specific provider configuration using row-by-row approach.
        Processes each raw data document individually and returns from where it fails.

        Args:
            provider_config: Provider configuration document
            metadata: Processing metadata

        Returns:
            Dict[str, Any]: Result dict with 'success' key and details
        """
        try:
            provider_id = provider_config.get("_id")
            provider_code = provider_config.get("code", "UNKNOWN")
            logger.info(f"Processing data for provider: {provider_code} (ID: {provider_id})")
            
            # Ensure provider_id is a non-None string (e.g. ObjectId → str)
            if not provider_id:
                logger.error(f"Provider ID not found for provider: {provider_code}")
                return {"success": False, "error": "Provider ID not found"}
            provider_id = str(provider_id)

            logger.info(f"Provider ID after conversion: {provider_id} (type: {type(provider_id)})")

            # Get pending raw data for this provider in ascending order
            data_type = metadata.get("data_type")
            raw_data_items = await self._get_pending_raw_data(provider_id, data_type)
            logger.info(f"_get_pending_raw_data returned {len(raw_data_items)} items for provider: {provider_code}")
            
            if not raw_data_items:
                logger.info(f"No pending raw data found for provider: {provider_code} (ID: {provider_id})")
                return {"success": True, "message": "No pending data"}
                
            logger.info(f"Found {len(raw_data_items)} pending raw data items for provider: {provider_code}")
            
            processing_result = await self._process_raw_data_documents(
                raw_data_items=raw_data_items,
                metadata=metadata,
                provider_config=provider_config,
                provider_code=provider_code
            )
            
            return processing_result
                
        except Exception as e:
            logger.error(f"Error in _process_provider_data: {str(e)}")
            return {"success": False, "error": str(e)}
        finally:
            # Cleanup database connections after processing
            self._cleanup_connections()
    
    async def _process_raw_data_documents(self, raw_data_items: List[Dict[str, Any]], 
                                        metadata: Dict[str, Any], provider_config: Dict[str, Any],
                                        provider_code: str) -> Dict[str, Any]:
        """
        Process raw data documents one by one, returning from where it fails.
        
        Args:
            raw_data_items: List of raw data documents to process
            metadata: Processing metadata
            provider_config: Provider configuration
            provider_code: Provider code for logging
            
        Returns:
            Dict with processing results including success status and details
        """
        try:
            processed_docs = 0
            failed_doc_index = -1
            failed_doc_id = None
            total_records_processed = 0
            
            logger.info(f"Starting row-by-row processing of {len(raw_data_items)} documents for provider: {provider_code}")
            
            for i, item in enumerate(raw_data_items):
                doc_id = str(item.get("_id", f"unknown_{i}"))
                raw_data = item.get("raw_data", [])
                
                logger.info(f"Processing document {i+1}/{len(raw_data_items)} (ID: {doc_id}) with {len(raw_data)} records")
                
                try:
                    metadata["raw_data"] = {
                        "raw_data_id": doc_id,
                        "created_at": item.get("created_at")
                    }
                    data_type = metadata.get("data_type")
                    # Mark this document as processing with execution details
                    await self._update_raw_data_status_with_execution_details(
                        [doc_id], 
                        ProcessingStatus.PROCESSING, 
                        metadata.get("execution_id"),
                        metadata.get("correlation_id"),
                        provider_code,
                        data_type=data_type
                    )
                    
                    # Process this document's records with document ID
                    document_result = await self._process_single_document(
                        raw_data=raw_data,
                        metadata=metadata,
                        provider_config=provider_config,
                        document_id=doc_id,
                        document_index=i
                    )
                    
                    if document_result.get("success", False):
                        # Mark this document as completed with execution details
                        await self._update_raw_data_status_with_execution_details(
                            [doc_id], 
                            ProcessingStatus.COMPLETED, 
                            metadata.get("execution_id"),
                            metadata.get("correlation_id"),
                            provider_code,
                            document_result,
                            data_type=data_type
                        )
                        processed_docs += 1
                        total_records_processed += document_result.get("records_processed", 0)
                        logger.info(f"Successfully processed document {i+1} (ID: {doc_id}): {document_result.get('records_processed', 0)} records")
                    else:
                        # Document processing failed
                        failed_doc_index = i
                        failed_doc_id = doc_id
                        await self._update_raw_data_status_with_execution_details(
                            [doc_id], 
                            ProcessingStatus.FAILED, 
                            metadata.get("execution_id"),
                            metadata.get("correlation_id"),
                            provider_code,
                            document_result,
                            data_type=data_type
                        )
                        logger.error(f"Failed to process document {i+1} (ID: {doc_id}): {document_result.get('error', 'Unknown error')}")
                        break
                        
                except Exception as e:
                    # Exception during document processing
                    failed_doc_index = i
                    failed_doc_id = doc_id
                    await self._update_raw_data_status_with_execution_details(
                        [doc_id], 
                        ProcessingStatus.FAILED, 
                        metadata.get("execution_id"),
                        metadata.get("correlation_id"),
                        provider_code,
                        {"error": str(e), "exception": True},
                        data_type=data_type
                    )
                    logger.error(f"Exception processing document {i+1} (ID: {doc_id}): {str(e)}", exc_info=True)
                    break
            
            # Prepare result
            result = {
                "success": failed_doc_index == -1,
                "processed_documents": processed_docs,
                "total_documents": len(raw_data_items),
                "total_records_processed": total_records_processed,
                "failed_at_index": failed_doc_index,
                "failed_document_id": failed_doc_id,
                "remaining_documents": len(raw_data_items) - processed_docs
            }
            
            if result["success"]:
                logger.info(f"All {processed_docs} documents processed successfully for provider {provider_code}. Total records: {total_records_processed}")
            else:
                logger.warning(f"Processing stopped at document {failed_doc_index + 1} for provider {provider_code}. "
                             f"Processed: {processed_docs}/{len(raw_data_items)} documents, {total_records_processed} records")
            
            return result
            
        except Exception as e:
            logger.error(f"Error in _process_raw_data_documents for provider {provider_code}: {str(e)}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "processed_documents": 0,
                "total_records_processed": 0
            }
    
    async def _process_single_document(self, raw_data: List[Dict[str, Any]], metadata: Dict[str, Any], 
                                     provider_config: Dict[str, Any], document_id: str, 
                                     document_index: int) -> Dict[str, Any]:
        """
        Process a single document's raw data records.
        This method calls the specific processor's process_raw_data_to_serve method with document ID.
        
        Args:
            raw_data: List of raw data records from the document
            metadata: Processing metadata
            provider_config: Provider configuration
            document_id: Document ID for logging and passing to processor
            document_index: Document index for logging
            
        Returns:
            Dict with processing results
        """
        try:
            if not raw_data:
                logger.warning(f"Document {document_id} has no raw data records")
                return {"success": True, "records_processed": 0, "message": "Empty document"}
            
            logger.debug(f"Processing document {document_id} with {len(raw_data)} records")
            
            # Create enhanced metadata with document ID
            enhanced_metadata = dict(metadata)
            enhanced_metadata["raw_data_id"] = document_id
            
            # Call the specific processor class based on data_type with document ID
            if asyncio.iscoroutinefunction(self.process_raw_data_to_serve):
                processing_success = await self.process_raw_data_to_serve(
                    raw_data, 
                    enhanced_metadata, 
                    provider_config
                )
            else:
                processing_success = self.process_raw_data_to_serve(
                    raw_data, 
                    enhanced_metadata, 
                    provider_config
                )
                if inspect.isawaitable(processing_success):
                    processing_success = await processing_success
            
            if processing_success:
                records_processed = len(raw_data)
                logger.debug(f"Document {document_id}: Successfully processed {records_processed} records")
                return {
                    "success": True,
                    "records_processed": records_processed,
                    "message": f"Processed {records_processed} records successfully"
                }
            else:
                logger.error(f"Document {document_id}: Failed to process {len(raw_data)} records")
                return {
                    "success": False,
                    "records_processed": 0,
                    "error": "Processing failed",
                    "message": f"Failed to process {len(raw_data)} records"
                }
                
        except Exception as e:
            logger.error(f"Error processing document {document_id}: {str(e)}", exc_info=True)
            
            # Handle specific data insertion errors
            from libs.database.base_serve_repository import (
                DataInsertionError, DataValidationError, DatabaseConnectionError,
                BulkInsertPartialFailureError, DataMappingError
            )
            
            if isinstance(e, DataInsertionError):
                logger.error(f"Data insertion failed for document {document_id}: {str(e)}")
                
                # Determine specific error type for better handling
                error_type = "DataInsertionError"
                error_details = {}
                
                if isinstance(e, DataValidationError):
                    error_type = "DataValidationError"
                    error_details = {"validation_errors": e.validation_errors}
                elif isinstance(e, DatabaseConnectionError):
                    error_type = "DatabaseConnectionError"
                    error_details = {"connection_details": e.connection_details}
                elif isinstance(e, BulkInsertPartialFailureError):
                    error_type = "BulkInsertPartialFailureError"
                    error_details = {
                        "expected_count": e.expected_count,
                        "actual_count": e.actual_count,
                        "failed_indices": e.failed_indices
                    }
                elif isinstance(e, DataMappingError):
                    error_type = "DataMappingError"
                    error_details = {"mapping_errors": e.mapping_errors}
                
                return {
                    "success": False,
                    "records_processed": 0,
                    "error": f"Data insertion failed: {str(e)}",
                    "message": f"Data insertion exception: {str(e)}",
                    "exception_type": error_type,
                    "error_details": error_details
                }
            
            return {
                "success": False,
                "records_processed": 0,
                "error": str(e),
                "message": f"Exception during processing: {str(e)}"
            }
    

    async def _get_pending_raw_data(self, provider_id: str, data_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get pending raw data for a provider.
        First fetches pending/failed status records for the provider (oldest first),
        then the raw documents they point at, bounded by insert time so the
        time-series raw collection is never scanned end to end.
        
        Args:
            provider_id: Provider configuration ID
            data_type: Data type for dynamic collection selection
            
        Returns:
            List of raw data documents with pending status, in status order
        """
        try:
            from libs.repository.raw_data_processing_status_repository import RawDataProcessingStatusRepository
            from libs.enum.raw_data_status import RawDataStatus
            from ..core.config import settings
            
            # Initialize processing status repository
            processing_status_repo = RawDataProcessingStatusRepository(
                mongodb_uri=settings.MONGODB_URI,
                database_name=settings.MONGODB_DATABASE,
                data_type=data_type
            )
            
            # Oldest first by _id (status rows are stored without created_at), limit batch size
            status_records = await processing_status_repo.collection.find({
                "provider_id": provider_id,
                "status": {"$in": [RawDataStatus.PENDING.value, RawDataStatus.FAILED.value]}
            }).sort("_id", 1).limit(50).to_list(length=50)

            raw_ids = []
            for record in status_records:
                raw_data_id = record.get("raw_data_id")
                if ObjectId.is_valid(raw_data_id):
                    raw_ids.append(ObjectId(raw_data_id))
                else:
                    logger.warning(f"Invalid raw_data_id format: {raw_data_id}")

            # A status row whose raw document is gone is skipped
            found = {doc["_id"]: doc for doc in await self.raw_data_repository.find_by_ids(raw_ids)}
            pending_raw_data = [found[raw_id] for raw_id in raw_ids if raw_id in found]
            
            logger.info(
                f"Successfully fetched {len(pending_raw_data)} pending raw data records "
                f"for provider {provider_id}"
            )
            
            return pending_raw_data
            
        except Exception as e:
            logger.error(f"Error getting pending raw data for provider {provider_id}: {str(e)}")
            return []
    
    async def _update_raw_data_status(self, raw_data_ids: List[str], status: ProcessingStatus) -> bool:
        """
        Update the processing status of raw data items in the separate status collection.

        Args:
            raw_data_ids: List of raw data document IDs
            status: New processing status

        Returns:
            bool: True if update was successful
        """
        try:
            from ..core.database import get_database

            db = get_database()
            status_collection = db["raw_data_processing_status"]

            update_data = {
                "status": status.value,
                "updated_at": datetime.now()
            }

            if status == ProcessingStatus.PROCESSING:
                update_data["processing_started_at"] = datetime.now()
            elif status in [ProcessingStatus.COMPLETED, ProcessingStatus.FAILED]:
                update_data["processing_completed_at"] = datetime.now()

            query = {"raw_data_id": {"$in": raw_data_ids}}
            result = await status_collection.update_many(query, {"$set": update_data})

            logger.debug(f"Updated {result.modified_count} raw data status records to: {status.value}")
            return result.modified_count > 0

        except Exception as e:
            logger.error(f"Error updating raw data status: {str(e)}")
            return False
    
    async def _update_raw_data_status_with_execution_details(self, raw_data_ids: List[str], status: ProcessingStatus,
                                                             execution_id: Optional[str], correlation_id: Optional[str],
                                                             provider_code: str, processing_result: Optional[Dict[str, Any]] = None,
                                                             data_type: Optional[str] = None) -> bool:
        """
        Update the processing status of raw data items with execution details.

        Args:
            raw_data_ids: List of raw data document IDs
            status: New processing status
            execution_id: Job execution ID
            correlation_id: Correlation ID
            provider_code: Provider code
            processing_result: Processing result details
            data_type: Data type for dynamic collection

        Returns:
            bool: True if update was successful
        """
        try:
            from ..core.database import get_database

            db = get_database()

            collection_name = "raw_data_processing_status"
            if data_type:
                collection_name = f"{data_type.lower()}_raw_data_processing_status"

            status_collection = db[collection_name]

            update_data = {
                "status": status.value,
                "updated_at": datetime.now(),
                "provider_code": provider_code
            }

            if execution_id:
                update_data["execution_id"] = execution_id
            if correlation_id:
                update_data["correlation_id"] = correlation_id

            if status == ProcessingStatus.PROCESSING:
                update_data["processing_started_at"] = datetime.now()
            elif status in [ProcessingStatus.COMPLETED, ProcessingStatus.FAILED]:
                update_data["processing_completed_at"] = datetime.now()

            if processing_result:
                update_data["processing_result"] = {
                    "records_processed": processing_result.get("records_processed", 0),
                    "success": processing_result.get("success", False),
                    "message": processing_result.get("message", ""),
                    "error": processing_result.get("error"),
                    "exception": processing_result.get("exception", False)
                }

            query = {"raw_data_id": {"$in": raw_data_ids}}
            result = await status_collection.update_many(query, {"$set": update_data})

            logger.debug(f"Updated {result.modified_count} raw data status records to: {status.value} with execution details")
            return result.modified_count > 0

        except Exception as e:
            logger.error(f"Error updating raw data status with execution details: {str(e)}")
            return False
    
    def _cleanup_connections(self):
        """
        Cleanup database connections after processing completes.
        This helps prevent connection leaks and ensures proper resource management.
        """
        try:
            # Close any repository connections if they have close methods
            for attr_name in dir(self):
                attr = getattr(self, attr_name)
                # Only close repositories or other objects that might have their own close logic.
                # Motor clients are shared singletons managed by the factory — do not close them here.
                if hasattr(attr, 'close') and callable(getattr(attr, 'close')):
                    try:
                        attr.close()
                        logger.debug(f"Closed connection for {attr_name}")
                    except Exception as close_error:
                        logger.warning(f"Error closing {attr_name}: {str(close_error)}")
            
            # Force garbage collection to help with connection cleanup
            import gc
            gc.collect()
            
            logger.debug("Database connection cleanup completed")
            
        except Exception as e:
            logger.warning(f"Error during connection cleanup: {str(e)}")
    
    @abstractmethod
    def process_raw_data_to_serve(
        self, 
        raw_data: List[Dict[str, Any]], 
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> bool:
        """
        Abstract method to be implemented by child classes.
        Processes raw data and stores it in the respective serve collection.
        
        Args:
            raw_data: List of raw data items to process (from a single document)
            metadata: Processing metadata (now includes raw_data_id from document)
            provider_config: Provider configuration with schema and mapping
            
        Returns:
            bool: True if processing was successful
        """
        pass
    
    def _get_serve_collection_name(self, data_type: str) -> str:
        """
        Get the serve collection name based on data_type.
        
        Args:
            data_type: The type of data
            
        Returns:
            str: Serve collection name
        """
        collection_mapping = {
            "weather": "weather_serve",
            "bid": "bid_trade_serve", 
            "exchange": "exchange_serve"
        }
        return collection_mapping.get(data_type.lower(), f"{data_type.lower()}_serve")
    
    def _get_cache_service(self):
        """Get cache service from middleware"""
        if not self.cache_service:
            from libs.cache import get_cache_service
            self.cache_service = get_cache_service()
        return self.cache_service
    
    async def _get_timeblock_number(self, time_block_str: str) -> int:
        """
        Get timeblock number using cache service

        Args:
            time_block_str: Time block string in format "HH:MM-HH:MM"

        Returns:
            Timeblock number (1-96) or 0 if not found/invalid
        """
        if not time_block_str:
            return 0

        try:
            cache = self._get_cache_service()
            if not cache:
                logger.warning("Cache service not available for timeblock lookup")
                return 0

            time_parts = time_block_str.split("-")
            if len(time_parts) != 2:
                logger.warning(f"Invalid time block format: {time_block_str}. Expected 'HH:MM-HH:MM'")
                return 0

            time_str = time_parts[0].strip()
            end_time = time_parts[1].strip()
            cache_key = f"timeblock_mapping:{time_str}-{end_time}"

            timeblock_num = cache.get(cache_key)

            if timeblock_num is None:
                logger.debug(f"Timeblock mapping not in cache, fetching from database: {time_str}-{end_time}")
                timeblock_data = await self.get_timeblock_by_time_range(time_str, end_time)

                if timeblock_data:
                    timeblock_num = timeblock_data.get("timeblock", 0)
                    if timeblock_num:
                        cache.set(cache_key, timeblock_num, ttl=3600)
                        logger.debug(f"Cached timeblock number: {cache_key} = {timeblock_num}")
                else:
                    logger.warning(f"No timeblock found for time range {time_str}-{end_time}")
                    timeblock_num = 0
            else:
                logger.debug(f"Retrieved timeblock number from cache: {cache_key} = {timeblock_num}")

            if isinstance(timeblock_num, int) and 1 <= timeblock_num <= 96:
                return timeblock_num
            else:
                logger.warning(f"Invalid timeblock number {timeblock_num} for '{time_block_str}' - should be 1-96")
                return 0

        except (ValueError, TypeError, IndexError) as e:
            logger.error(f"Error parsing timeblock {time_block_str}: {str(e)}")
            return 0
    
    async def fetch_timeblock_data(self, timeblock_number: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Fetch time block data from the timeblock master collection
        
        Args:
            timeblock_number: Specific timeblock number to fetch (1-96). If None, fetches all timeblocks
            
        Returns:
            List of timeblock documents from the collection
        """
        try:
            logger.info("TESTING LOGS: Entering fetch_timeblock_data")
            from ..core.config import settings
            
            # Use cache key for this operation
            cache_key = f"timeblock_data:{timeblock_number if timeblock_number else 'all'}"
            
            # Try to get from cache first
            cache = self._get_cache_service()
            if cache:
                logger.info(f"TESTING LOGS: Checking cache for {cache_key}")
                cached_data = cache.get(cache_key)
                if cached_data:
                    logger.info(f"TESTING LOGS: Retrieved timeblock data from cache: {cache_key}")
                    return cached_data
            
            # Fetch from database
            logger.info("TESTING LOGS: Cache miss, fetching from database")
            from ..core.database import get_database
            logger.info("TESTING LOGS: Calling get_database()")
            db = get_database()
            logger.info(f"TESTING LOGS: Got database, collection: {settings.MONGODB_TIMEBLOCK_MASTER_COLLECTION}")
            timeblock_collection = db[settings.MONGODB_TIMEBLOCK_MASTER_COLLECTION]
            
            # Build query
            query = {}
            if timeblock_number is not None:
                if not (1 <= timeblock_number <= 96):
                    logger.warning(f"Invalid timeblock number: {timeblock_number}. Must be between 1-96")
                    return []
                query["timeblock"] = timeblock_number
            
            # Execute query with sorting
            logger.info(f"TESTING LOGS: Executing query: {query}")
            timeblock_records = await timeblock_collection.find(query).sort("timeblock", 1).to_list(length=10000)
            

            
            # Convert ObjectId to string for JSON serialization
            for record in timeblock_records:
                if "_id" in record:
                    record["_id"] = str(record["_id"])
            
            # Cache the result
            if cache and timeblock_records:
                # Cache for 1 hour since timeblock data is relatively static
                cache.set(cache_key, timeblock_records, ttl=3600)
                logger.debug(f"Cached timeblock data: {cache_key}")
            
            logger.info(f"Fetched {len(timeblock_records)} timeblock records from collection")
            return timeblock_records
            
        except Exception as e:
            logger.error(f"Error fetching timeblock data: {str(e)}")
            return []
    
    async def get_timeblock_by_time_range(self, start_time: str, end_time: str) -> Optional[Dict[str, Any]]:
        """
        Get specific timeblock data by time range

        Args:
            start_time: Start time in format "HH:MM" (e.g., "00:00")
            end_time: End time in format "HH:MM" (e.g., "00:15")

        Returns:
            Timeblock document or None if not found
        """
        try:
            cache_key = f"timeblock_range:{start_time}-{end_time}"

            cache = self._get_cache_service()
            if cache:
                cached_data = cache.get(cache_key)
                if cached_data:
                    logger.debug(f"Retrieved timeblock by range from cache: {cache_key}")
                    return cached_data

            from ..core.config import settings
            from ..core.database import get_database

            db = get_database()
            timeblock_collection = db[settings.MONGODB_TIMEBLOCK_MASTER_COLLECTION]
            
            # Query by start and end time
            query = {
                "starttime": start_time,
                "endtime": end_time
            }
            
            timeblock_record = await timeblock_collection.find_one(query)

            
            if timeblock_record:
                # Convert ObjectId to string
                if "_id" in timeblock_record:
                    timeblock_record["_id"] = str(timeblock_record["_id"])
                
                # Cache the full record and also cache just the timeblock number for faster lookup
                if cache:
                    # Cache full record
                    cache.set(cache_key, timeblock_record, ttl=3600)
                    logger.debug(f"Cached timeblock by range: {cache_key}")
                    
                    # Also cache just the timeblock number for faster _get_timeblock_number lookups
                    timeblock_num = timeblock_record.get("timeblock")
                    if timeblock_num:
                        timeblock_mapping_key = f"timeblock_mapping:{start_time}-{end_time}"
                        cache.set(timeblock_mapping_key, timeblock_num, ttl=3600)
                        logger.debug(f"Cached timeblock number: {timeblock_mapping_key} = {timeblock_num}")
                
                logger.debug(f"Found timeblock for {start_time}-{end_time}: {timeblock_record.get('timeblock')}")
                return timeblock_record
            else:
                logger.warning(f"No timeblock found for time range {start_time}-{end_time}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting timeblock by time range {start_time}-{end_time}: {str(e)}")
            return None
    
    def cache_timeblock_mapping(self, start_time: str, end_time: str, timeblock_number: int) -> bool:
        """
        Cache timeblock mapping for faster lookups
        
        Args:
            start_time: Start time in format "HH:MM"
            end_time: End time in format "HH:MM"
            timeblock_number: Timeblock number (1-96)
            
        Returns:
            True if cached successfully, False otherwise
        """
        try:
            cache = self._get_cache_service()
            if not cache:
                return False
            
            cache_key = f"timeblock_mapping:{start_time}-{end_time}"
            success = cache.set(cache_key, timeblock_number, ttl=3600)
            
            if success:
                logger.debug(f"Cached timeblock mapping: {cache_key} = {timeblock_number}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error caching timeblock mapping {start_time}-{end_time}: {str(e)}")
            return False
    
    async def preload_timeblock_mappings(self) -> bool:
        """
        Preload all timeblock mappings into cache for better performance
        
        Returns:
            True if successful, False otherwise
        """
        try:
            cache = self._get_cache_service()
            if not cache:
                logger.warning("Cache service not available for preloading timeblock mappings")
                return False
            
            # Check if already preloaded
            preload_key = "timeblock_mappings_preloaded"
            if cache.get(preload_key):
                logger.debug("Timeblock mappings already preloaded")
                return True
            
            # Fetch all timeblock data
            all_timeblocks = await self.fetch_timeblock_data()
            
            if not all_timeblocks:
                logger.warning("No timeblock data found for preloading")
                return False
            
            cached_count = 0
            for timeblock_record in all_timeblocks:
                start_time = timeblock_record.get("starttime")
                end_time = timeblock_record.get("endtime")
                timeblock_num = timeblock_record.get("timeblock")
                
                if start_time and end_time and timeblock_num:
                    if self.cache_timeblock_mapping(start_time, end_time, timeblock_num):
                        cached_count += 1
            
            # Mark as preloaded (cache for 1 hour)
            cache.set(preload_key, True, ttl=3600)
            
            logger.info(f"Preloaded {cached_count} timeblock mappings to cache")
            return cached_count > 0
            
        except Exception as e:
            logger.error(f"Error preloading timeblock mappings: {str(e)}")
            return False

    async def get_current_timeblock(self) -> Optional[Dict[str, Any]]:
        """
        Get the current timeblock based on current time
        
        Returns:
            Current timeblock document or None if not found
        """
        try:
            from datetime import datetime
            
            current_time = datetime.now()
            current_hour = current_time.hour
            current_minute = current_time.minute
            
            # Calculate which 15-minute block we're in (0, 15, 30, 45)
            timeblock_minute = (current_minute // 15) * 15
            
            # Format times
            start_time = f"{current_hour:02d}:{timeblock_minute:02d}"
            
            # Calculate end time
            end_minute = timeblock_minute + 15
            if end_minute >= 60:
                end_hour = (current_hour + 1) % 24
                end_minute = 0
            else:
                end_hour = current_hour
            
            end_time = f"{end_hour:02d}:{end_minute:02d}"
            
            logger.debug(f"Looking for current timeblock: {start_time}-{end_time}")
            return await self.get_timeblock_by_time_range(start_time, end_time)
            
        except Exception as e:
            logger.error(f"Error getting current timeblock: {str(e)}")
            return None

    def get_cache_stats(self) -> dict:
        """Get cache statistics for monitoring"""
        try:
            cache = self._get_cache_service()
            stats = {
                "cache_service_available": cache is not None,
                "processor_class": self.__class__.__name__
            }
            
            if cache:
                try:
                    # Test cache connection
                    cache.redis_client.ping()
                    stats["redis_available"] = True
                    stats["redis_host"] = cache.redis_client.connection_pool.connection_kwargs.get('host')
                    stats["redis_port"] = cache.redis_client.connection_pool.connection_kwargs.get('port')
                    stats["default_ttl"] = cache.default_ttl
                except Exception:
                    stats["redis_available"] = False
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting cache stats for {self.__class__.__name__}: {e}")
            return {"error": str(e), "processor_class": self.__class__.__name__}
    
    def set_correlation_id(self, correlation_id: str) -> None:
        """
        Set correlation ID for this processor instance and in context
        
        Args:
            correlation_id: Correlation ID to set (typically execution_id)
        """
        self.current_execution_id = correlation_id
        set_correlation_id(correlation_id)
        logger.debug("Set correlation ID: %s", correlation_id)
    
    def get_correlation_id(self) -> Optional[str]:
        """
        Get current correlation ID from context or instance
        
        Returns:
            Current correlation ID or None if not set
        """
        # Try context first, then instance variable
        correlation_id = get_correlation_id() or self.current_execution_id
        return correlation_id
    
    def clear_correlation_id(self) -> None:
        """
        Clear correlation ID from context and instance
        """
        self.current_execution_id = None
        set_correlation_id(None)
        logger.debug("Cleared correlation ID")
    
    async def _record_job_start(self, job_type: str, job_name: str, schedule: str,
                         execution_start: datetime, execution_id: Optional[str] = None) -> Optional[str]:
        """Record job start in audit database"""
        try:
            job_data = await self.job_data_repository.get_job(job_type)
            if not job_data:
                logger.error("Job data not found for job_type: %s", job_type)
                return None

            job_id = job_data.get("_id")

            if not execution_id:
                execution_id = self.get_correlation_id()
                if execution_id:
                    logger.info("Reusing existing execution ID from context: %s", execution_id)
                    return execution_id

                execution_id = f"{job_type}_{execution_start.strftime('%Y%m%d_%H%M%S')}"

            job_detail = JobDetail(
                job_id=str(job_id),
                execution_id=execution_id,
                job_name=job_name,
                execution_start=execution_start,
                execution_end=None,
                status="running",
                success=False,
                result_data=None,
                error_message=None,
                service="scheduler",
                duration_seconds=None,
                error_details=None,
                metadata=None
            )

            await self.job_detail_repository.log_execution(job_detail)
            logger.info("Recorded job start for %s (execution_id: %s) in audit database", job_type, execution_id)
            return execution_id

        except Exception as e:
            logger.error("Error recording job start for %s: %s", job_type, str(e))
            return None

    async def _record_job_completion(self, job_type: str, job_name: str, schedule: str,
                              execution_start: datetime, execution_end: datetime,
                              execution_id: str, status: str, success: bool,
                              result: Optional[Dict[str, Any]] = None,
                              error_message: Optional[str] = None):
        """Record job completion in audit database"""
        try:
            duration_seconds = None
            if execution_start and execution_end:
                duration_seconds = (execution_end - execution_start).total_seconds()

            update_data = {
                "execution_end": execution_end,
                "duration_seconds": duration_seconds,
                "status": status,
                "success": success,
                "error_message": error_message,
                "result_data": result,
            }

            updated = await self.job_detail_repository.update_execution(execution_id, update_data)

            if updated:
                logger.info("Updated job completion for %s (execution_id: %s, status: %s)", job_type, execution_id, status)
            else:
                logger.warning("Failed to update job execution %s, creating new record", execution_id)
                job_data = await self.job_data_repository.get_job(job_type)
                if not job_data:
                    logger.error("Job data not found for job_type: %s", job_type)
                    return

                job_id = job_data.get("_id")
                job_detail = JobDetail(
                    job_id=str(job_id),
                    execution_id=execution_id,
                    job_name=job_name,
                    execution_start=execution_start,
                    execution_end=execution_end,
                    duration_seconds=duration_seconds,
                    status=status,
                    success=success,
                    result_data=result,
                    error_message=error_message,
                    error_details=None,
                    service="scheduler",
                    metadata=None
                )

                await self.job_detail_repository.log_execution(job_detail)
                logger.info("Created new job completion record for %s", job_type)

        except Exception as e:
            logger.error("Error recording job completion for %s: %s", job_type, str(e))

    async def _record_job_execution(self, job_type: str, job_name: str, schedule: str,
                             execution_start: datetime, execution_end: Optional[datetime] = None,
                             execution_id: Optional[str] = None, status: Optional[str] = None,
                             success: Optional[bool] = None, result: Optional[Dict[str, Any]] = None,
                             error_message: Optional[str] = None, is_start: bool = True) -> Optional[str]:
        """Unified wrapper for start/completion calls - for backward compatibility/choice"""
        if is_start:
            return await self._record_job_start(job_type, job_name, schedule, execution_start, execution_id)
        else:
            await self._record_job_completion(
                job_type, job_name, schedule, execution_start,
                execution_end or execution_start,
                execution_id or "",
                status or "unknown",
                success if success is not None else False,
                result, error_message
            )
            return None

    def with_correlation_id(self, correlation_id: str):
        """
        Context manager for setting correlation ID for a block of code
        
        Args:
            correlation_id: Correlation ID to set
            
        Usage:
            with processor.with_correlation_id("job_123"):
                # All logging in this block will include correlation_id
                logger.info("This will include correlation ID")
        """
        class CorrelationContext:
            def __init__(self, processor, corr_id):
                self.processor = processor
                self.correlation_id = corr_id
                self.previous_id = None
            
            def __enter__(self):
                self.previous_id = self.processor.get_correlation_id()
                self.processor.set_correlation_id(self.correlation_id)
                return self
            
            def __exit__(self, exc_type, exc_val, exc_tb):
                if self.previous_id:
                    self.processor.set_correlation_id(self.previous_id)
                else:
                    self.processor.clear_correlation_id()
        
        return CorrelationContext(self, correlation_id)
