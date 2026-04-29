"""
Raw Data Repository
Handles single collection storage for all raw data types with metadata differentiation
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from bson import ObjectId
from pymongo.errors import OperationFailure

from libs.database.base_repository import BaseRepository
from libs.enum.raw_data_status import RawDataStatus
from libs.models.raw_data_entity import RawDataEntity

logger = logging.getLogger(__name__)


class RawDataRepository(BaseRepository):
    """
    Repository for managing all raw data in single collection
    Uses metadata to differentiate between data types and providers
    """
    
    def __init__(self, mongodb_uri: str, database_name: str, data_type: Optional[str] = None):
        """
        Initialize raw data repository
        
        Args:
            mongodb_uri: MongoDB connection URI
            database_name: Database name
            data_type: Optional data type for collection separation (e.g., 'demand', 'supply')
        """
        if data_type:
            # Normalize data_type (lowercase) and append suffix
            self.collection_name = f"{data_type.lower()}_raw_data"
        else:
            self.collection_name = "raw_data"
            
        super().__init__(self.collection_name, mongodb_uri, database_name)
        self._create_timeseries_collection_if_not_exists()
        self._create_indexes()
    
    async def _create_timeseries_collection_if_not_exists(self):
        """
        Create time series collection if it doesn't exist
        Configures MongoDB time series collection for optimal performance
        """
        try:
            # Check if collection already exists
            existing_collections = self.db.list_collection_names()
            if self.collection_name in existing_collections:
                logger.info("Time series collection '%s' already exists", self.collection_name)
                return
            
            # Create time series collection with proper configuration
            timeseries_options = {
                "timeField": "created_at",           # Time field for time series
                "metaField": "metadata",             # Metadata field for grouping
                "granularity": "minutes"             # Time series granularity
            }
            
            # Create the time series collection
            self.db.create_collection(
                self.collection_name,
                timeseries=timeseries_options
            )
            
            logger.info("Created time series collection '%s' with timeField='created_at', metaField='metadata'", 
                       self.collection_name)
            
        except Exception as e:
            logger.error("Error creating time series collection '%s': %s", self.collection_name, e, exc_info=True)
            # If time series creation fails, fall back to regular collection
            try:
                if self.collection_name not in self.db.list_collection_names():
                    self.db.create_collection(self.collection_name)
                    logger.warning("Created regular collection '%s' as fallback", self.collection_name)
            except Exception as fallback_error:
                logger.error("Error creating fallback collection: %s", fallback_error, exc_info=True)
                raise
    
    async def _create_indexes(self):
        """Create necessary indexes for time series collection"""
        indexes = [
            ([("data_type", 1), ("created_at", -1)], {}),
            ([("provider_id", 1), ("created_at", -1)], {}),
            ("metadata.upload_id", {}),
        ]
        for index_spec, kwargs in indexes:
            try:
                self.collection.create_index(index_spec, **kwargs)
            except OperationFailure as e:
                if e.code == 85:
                    # IndexOptionsConflict: equivalent index already exists (time series originalSpec ordering), safe to ignore
                    logger.debug("Index already exists (skipping): %s", e)
                else:
                    logger.error("Error creating raw data index: %s", e, exc_info=True)
            except Exception as e:
                logger.error("Error creating raw data index: %s", e, exc_info=True)

        logger.info("Time series collection indexes created successfully")
    
    async def create_raw_data(self, entity: RawDataEntity) -> str:
        """
        Create raw data record in single collection
        
        Args:
            entity: Raw data entity with metadata
            
        Returns:
            str: Document ID
        """
        try:
            # Convert entity to dict
            data = entity.model_dump(by_alias=True, exclude_unset=True)
            
            # Ensure required fields
            if not data.get("data_type"):
                raise ValueError("data_type is required")
            
            if not data.get("provider_id"):
                raise ValueError("provider_id is required")

            # Insert document
            result = self.collection.insert_one(data)
            
            logger.info("Created raw data record: %s (type: %s, provider: %s)", 
                       result.inserted_id, 
                       data.get("data_type"), 
                       data.get("metadata", {}).get("provider_type"))
            
            return str(result.inserted_id)
            
        except Exception as e:
            logger.error("Error creating raw data record: %s", e, exc_info=True)
            raise
    
    async def get_raw_data_by_ids(self, raw_data_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Get raw data records by their IDs
        
        Args:
            raw_data_ids: List of raw data document IDs
            
        Returns:
            List[Dict[str, Any]]: Raw data records
        """
        try:
            object_ids = [ObjectId(rid) for rid in raw_data_ids]
            records = await self.collection.find({"_id": {"$in": object_ids}}).to_list(length=10000)

            for record in records:
                record["_id"] = str(record["_id"])
                records.append(record)
            
            logger.info("Retrieved %d raw data records by IDs", len(records))
            return records
            
        except Exception as e:
            logger.error("Error fetching raw data by IDs: %s", e, exc_info=True)
            return []
    
    
    
    async def get_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        """
        Get raw data by document ID
        
        Args:
            record_id: Document ID
            
        Returns:
            Dict[str, Any]: Raw data record or None
        """
        try:
            record = self.collection.find_one({"_id": ObjectId(record_id)})
            if record:
                record["_id"] = str(record["_id"])
            return record
            
        except Exception as e:
            logger.error("Error fetching raw data by ID %s: %s", record_id, e, exc_info=True)
            return None

    async def get_by_upload_id(self, upload_id: str) -> Optional[Dict[str, Any]]:
        """
        Get raw data by upload identifier
        
        Args:
            upload_id: Upload tracking ID
            
        Returns:
            Dict[str, Any]: Raw data record or None
        """
        try:
            record = self.collection.find_one({"metadata.upload_id": upload_id})
            if record:
                record["_id"] = str(record["_id"])
            return record
            
        except Exception as e:
            logger.error("Error fetching raw data by upload_id %s: %s", upload_id, e, exc_info=True)
            return None
    
    async def get_statistics(self, data_type: Optional[str] = None, provider_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Get raw data statistics
        
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
                match_filter["metadata.provider_type"] = provider_type
            
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
                if status in ["pending", "processing", "completed", "failed"]:
                    stats[status] = count
            
            return stats
            
        except Exception as e:
            logger.error("Error getting raw data statistics: %s", e, exc_info=True)
            return {"error": str(e)}
    
    def cleanup_old_records(self, older_than_days: int = 30) -> int:
        """
        Clean up old raw data records
        
        Args:
            older_than_days: Delete records older than this many days
            
        Returns:
            int: Number of deleted records
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=older_than_days)
            
            query = {"created_at": {"$lt": cutoff_date}}
            
            result = self.collection.delete_many(query)
            
            logger.info("Cleaned up %d old raw data records (older than %d days)", 
                       result.deleted_count, older_than_days)
            
            return result.deleted_count
            
        except Exception as e:
            logger.error("Error cleaning up old raw data records: %s", e, exc_info=True)
            return 0
    
    def is_timeseries_collection(self) -> bool:
        """
        Check if the collection is properly configured as a time series collection
        
        Returns:
            bool: True if collection is time series, False otherwise
        """
        try:
            # Get collection info to check if it's a time series collection
            collection_info = self.db.command("listCollections", filter={"name": self.collection_name})
            collections = collection_info.get("cursor", {}).get("firstBatch", [])
            
            if collections:
                collection_data = collections[0]
                options = collection_data.get("options", {})
                timeseries_config = options.get("timeseries")
                
                if timeseries_config:
                    logger.info("Collection '%s' is configured as time series: %s", 
                               self.collection_name, timeseries_config)
                    return True
                else:
                    logger.info("Collection '%s' is NOT configured as time series", self.collection_name)
                    return False
            else:
                logger.warning("Collection '%s' not found", self.collection_name)
                return False
                
        except Exception as e:
            logger.error("Error checking time series configuration for '%s': %s", self.collection_name, e)
            return False
    
    def get_timeseries_stats(self) -> Dict[str, Any]:
        """
        Get time series collection statistics
        
        Returns:
            Dict[str, Any]: Time series statistics
        """
        try:
            if not self.is_timeseries_collection():
                return {"error": "Collection is not configured as time series"}
            
            # Get collection stats
            stats = self.db.command("collStats", self.collection_name)
            
            timeseries_stats = {
                "is_timeseries": True,
                "document_count": stats.get("count", 0),
                "storage_size_bytes": stats.get("storageSize", 0),
                "avg_obj_size_bytes": stats.get("avgObjSize", 0),
                "total_index_size_bytes": stats.get("totalIndexSize", 0),
                "timeseries_config": stats.get("timeseries", {})
            }
            
            return timeseries_stats
            
        except Exception as e:
            logger.error("Error getting time series stats: %s", e, exc_info=True)
            return {"error": str(e)}
