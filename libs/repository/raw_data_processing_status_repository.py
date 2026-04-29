"""
Raw Data Processing Status Repository
Handles processing status tracking for raw data records
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from bson import ObjectId

from libs.database.base_repository import BaseRepository
from libs.enum.raw_data_status import RawDataStatus
from libs.models.raw_data_processing_status_entity import RawDataProcessingStatusEntity

logger = logging.getLogger(__name__)


class RawDataProcessingStatusRepository(BaseRepository):
    """
    Repository for managing raw data processing status
    Separate from raw data for better performance and organization
    """
    
    def __init__(self, mongodb_uri: str, database_name: str, data_type: Optional[str] = None):
        """
        Initialize raw data processing status repository
        
        Args:
            mongodb_uri: MongoDB connection URI
            database_name: Database name
            data_type: Optional data type for collection separation (e.g., 'demand', 'supply')
        """
        if data_type:
            # Normalize data_type (lowercase) and append suffix
            self.collection_name = f"{data_type.lower()}_raw_data_processing_status"
        else:
            self.collection_name = "raw_data_processing_status"
            
        super().__init__(self.collection_name, mongodb_uri, database_name)
        self._create_collection_if_not_exists()
        self._create_indexes()
    
    async def _create_indexes(self):
        """Create necessary indexes for efficient querying"""
        try:
            # Index for status-based queries (most common for cron jobs)
            self.collection.create_index([("status", 1), ("created_at", 1)])
            
            # Index for raw_data_id lookups
            self.collection.create_index("raw_data_id")
            
            # Index for upload_id tracking
            self.collection.create_index("upload_id", sparse=True)
            
            # Compound index for data_type and provider filtering
            self.collection.create_index([("data_type", 1), ("provider_type", 1), ("status", 1)])

            # Compound index on provider_id and status for created_at in ascending order for efficient retrieval of pending records by provider
            self.collection.create_index([("provider_id", 1), ("status", 1), ("created_at", 1)])
            
            # Index for retry logic
            self.collection.create_index([("status", 1), ("retry_count", 1)])
            
            logger.info("Raw data processing status indexes created successfully")
            
        except Exception as e:
            logger.error("Error creating processing status indexes: %s", e, exc_info=True)
    
    async def create_processing_status(self, entity: RawDataProcessingStatusEntity) -> str:
        """
        Create processing status record
        
        Args:
            entity: Processing status entity
            
        Returns:
            str: Document ID
        """
        try:
            # Convert entity to dict
            data = entity.model_dump(by_alias=True, exclude_unset=True)
            
            # Ensure required fields
            if not data.get("raw_data_id"):
                raise ValueError("raw_data_id is required")
            
            # Insert document
            result = self.collection.insert_one(data)
            
            logger.info("Created processing status record: %s (raw_data_id: %s, status: %s)", 
                       result.inserted_id, data.get("raw_data_id"), data.get("status"))
            
            return str(result.inserted_id)
            
        except Exception as e:
            logger.error("Error creating processing status record: %s", e, exc_info=True)
            raise
    
    async def update_status(self, status_id: str, status: RawDataStatus, **kwargs) -> bool:
        """
        Update processing status
        
        Args:
            status_id: Processing status document ID
            status: New status
            **kwargs: Additional fields to update
            
        Returns:
            bool: Success status
        """
        try:
            update_data = {
                "status": status.value,
                "updated_at": datetime.now()
            }
            
            # Add timestamp fields based on status
            if status == RawDataStatus.PROCESSING:
                update_data["processing_started_at"] = datetime.now()
            elif status == RawDataStatus.COMPLETED:
                update_data["processing_completed_at"] = datetime.now()
            elif status == RawDataStatus.FAILED:
                update_data["processing_completed_at"] = datetime.now()
                if "error_message" not in kwargs:
                    update_data["error_message"] = "Processing failed"
            
            # Add any additional fields
            update_data.update(kwargs)
            
            result = self.collection.update_one(
                {"_id": ObjectId(status_id)},
                {"$set": update_data}
            )
            
            success = result.modified_count > 0
            if success:
                logger.info("Updated processing status: %s -> %s", status_id, status.value)
            else:
                logger.warning("No processing status updated for ID: %s", status_id)
            
            return success
            
        except Exception as e:
            logger.error("Error updating processing status for %s: %s", status_id, e, exc_info=True)
            return False
    
    async def get_pending_processing(self, data_type: Optional[str] = None, provider_type: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get pending processing records for cron jobs
        
        Args:
            data_type: Filter by data type (optional)
            provider_type: Filter by provider type (optional)
            limit: Maximum number of records
            
        Returns:
            List[Dict[str, Any]]: Pending processing records
        """
        try:
            query = {"status": RawDataStatus.PENDING.value}
            
            if data_type:
                query["data_type"] = data_type
            
            if provider_type:
                query["provider_type"] = provider_type

            records = await self.collection.find(query).sort("created_at", 1).limit(limit).to_list(length=10000)

            for record in records:
                record["_id"] = str(record["_id"])
                record["raw_data_id"] = str(record["raw_data_id"])
                records.append(record)
            
            logger.info("Found %d pending processing records (type: %s, provider: %s)", 
                       len(records), data_type or "all", provider_type or "all")
            
            return records
            
        except Exception as e:
            logger.error("Error fetching pending processing records: %s", e, exc_info=True)
            return []
    
    async def get_by_raw_data_id(self, raw_data_id: str) -> Optional[Dict[str, Any]]:
        """
        Get processing status by raw data ID
        
        Args:
            raw_data_id: Raw data document ID
            
        Returns:
            Dict[str, Any]: Processing status record or None
        """
        try:
            # Note: raw_data_id is stored as a string in this collection
            record = self.collection.find_one({"raw_data_id": raw_data_id})
            if record:
                record["_id"] = str(record["_id"])
                record["raw_data_id"] = str(record["raw_data_id"])
            return record
            
        except Exception as e:
            logger.error("Error fetching processing status by raw_data_id %s: %s", raw_data_id, e, exc_info=True)
            return None
    
    async def get_by_upload_id(self, upload_id: str) -> Optional[Dict[str, Any]]:
        """
        Get processing status by upload ID
        
        Args:
            upload_id: Upload tracking ID
            
        Returns:
            Dict[str, Any]: Processing status record or None
        """
        try:
            record = self.collection.find_one({"upload_id": upload_id})
            if record:
                record["_id"] = str(record["_id"])
                record["raw_data_id"] = str(record["raw_data_id"])
            return record
            
        except Exception as e:
            logger.error("Error fetching processing status by upload_id %s: %s", upload_id, e, exc_info=True)
            return None
    
    async def get_failed_for_retry(self, max_retries: int = 3, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get failed processing records that can be retried
        
        Args:
            max_retries: Maximum retry attempts
            limit: Maximum number of records
            
        Returns:
            List[Dict[str, Any]]: Failed records eligible for retry
        """
        try:
            query = {
                "status": RawDataStatus.FAILED.value,
                "retry_count": {"$lt": max_retries}
            }

            records = await self.collection.find(query).sort("updated_at", 1).limit(limit).to_list(length=10000)

            for record in records:
                record["_id"] = str(record["_id"])
                record["raw_data_id"] = str(record["raw_data_id"])
                records.append(record)
            
            logger.info("Found %d failed records eligible for retry", len(records))
            return records
            
        except Exception as e:
            logger.error("Error fetching failed records for retry: %s", e, exc_info=True)
            return []
    
    async def get_statistics(self, data_type: Optional[str] = None, provider_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Get processing status statistics
        
        Args:
            data_type: Filter by data type (optional)
            provider_type: Filter by provider type (optional)
            
        Returns:
            Dict[str, Any]: Statistics
        """
        try:
            match_filter = {}
            if data_type:
                match_filter["data_type"] = data_type
            if provider_type:
                match_filter["provider_type"] = provider_type
            
            pipeline = [
                {"$match": match_filter},
                {"$group": {
                    "_id": "$status",
                    "count": {"$sum": 1}
                }}
            ]

            results = await self.collection.aggregate(pipeline).to_list(length=10000)

            stats = {
                "total": 0,
                "pending": 0,
                "processing": 0,
                "completed": 0,
                "failed": 0
            }
            
            for result in results:
                status = result["_id"]
                count = result["count"]
                stats["total"] += count
                if status in stats:
                    stats[status] = count
            
            return stats
            
        except Exception as e:
            logger.error("Error getting processing status statistics: %s", e, exc_info=True)
            return {"error": str(e)}
    
    def cleanup_old_completed(self, older_than_days: int = 30) -> int:
        """
        Clean up old completed processing status records
        
        Args:
            older_than_days: Delete completed records older than this many days
            
        Returns:
            int: Number of deleted records
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=older_than_days)
            
            query = {
                "status": RawDataStatus.COMPLETED.value,
                "processing_completed_at": {"$lt": cutoff_date}
            }
            
            result = self.collection.delete_many(query)
            
            logger.info("Cleaned up %d old completed processing status records (older than %d days)", 
                       result.deleted_count, older_than_days)
            
            return result.deleted_count
            
        except Exception as e:
            logger.error("Error cleaning up old processing status records: %s", e, exc_info=True)
            return 0
