"""
Upload Tracking Repository Implementation
Implements IUploadTrackingRepository interface
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from bson import ObjectId

from libs.database.base_repository import BaseRepository
from libs.enum.raw_data_status import RawDataStatus
from ..interfaces.repositories import IUploadTrackingRepository

logger = logging.getLogger(__name__)


class UploadTrackingRepository(BaseRepository, IUploadTrackingRepository):
    """
    Repository for managing upload tracking and status
    """
    
    def __init__(self, mongodb_uri: str, database_name: str):
        """
        Initialize upload tracking repository
        
        Args:
            mongodb_uri: MongoDB connection URI
            database_name: Database name
        """
        self.collection_name = "upload_tracking"
        super().__init__(self.collection_name, mongodb_uri, database_name)
        self._create_collection_if_not_exists()
        self._create_indexes()
    
    def _create_indexes(self):
        """Create necessary indexes for upload tracking"""
        try:
            # Index on upload_id for fast lookups
            self.collection.create_index("upload_id", unique=True)
            
            # Index on status for filtering
            self.collection.create_index("status")
            
            # Index on created_at for time-based queries
            self.collection.create_index("created_at")
            
            # Compound index for status and created_at
            self.collection.create_index([("status", 1), ("created_at", -1)])
            
            logger.info("Upload tracking indexes created successfully")
            
        except Exception as e:
            logger.error("Error creating upload tracking indexes: %s", e, exc_info=True)
    
    async def create_upload_record(
        self,
        upload_id: str,
        file_name: str,
        file_size: int,
        data_type: str,
        provider_type: str,
        status: RawDataStatus = RawDataStatus.PENDING,
        file_path: Optional[str] = None
    ) -> str:
        """Create a new upload tracking record"""
        try:
            upload_data = {
                "upload_id": upload_id,
                "file_name": file_name,
                "file_path": file_path,
                "file_size": file_size,
                "data_type": data_type,
                "provider_type": provider_type,
                "status": status.value,
                "status_message": "Upload created",
                "retry_count": 0,  # Track number of processing attempts
                "created_at": datetime.now(),
                "updated_at": datetime.now()
            }
            
            logger.info("Creating upload record: upload_id=%s, file=%s", upload_id, file_name)
            
            result = self.collection.insert_one(upload_data)
            document_id = str(result.inserted_id)
            
            logger.info("Upload record created: upload_id=%s, document_id=%s", upload_id, document_id)
            return document_id
            
        except Exception as e:
            logger.error("Error creating upload record: %s", e, exc_info=True)
            raise
    
    async def get_upload_by_id(self, upload_id: str) -> Optional[Dict[str, Any]]:
        """
        Get upload record by upload ID
        
        Args:
            upload_id: Upload identifier
            
        Returns:
            Optional[Dict[str, Any]]: Upload record if found
        """
        try:
            record = self.collection.find_one({"upload_id": upload_id})
            if record:
                record["_id"] = str(record["_id"])
            return record
            
        except Exception as e:
            logger.error("Error getting upload record for %s: %s", upload_id, e, exc_info=True)
            return None
    
    async def update_upload_status(
        self,
        upload_id: str,
        status: RawDataStatus,
        status_message: str = "",
        completed_at: Optional[datetime] = None,
        failed_at: Optional[datetime] = None,
        increment_retry: bool = False
    ) -> bool:
        """
        Update upload status
        
        Args:
            upload_id: Upload identifier
            status: New status
            status_message: Status message
            completed_at: Completion timestamp
            failed_at: Failure timestamp
            increment_retry: Whether to increment retry count (for failed attempts)
        """
        try:
            update_data = {
                "status": status.value,
                "status_message": status_message,
                "updated_at": datetime.now()
            }
            
            if completed_at:
                update_data["completed_at"] = completed_at
            
            if failed_at:
                update_data["failed_at"] = failed_at
            
            # Build update operation
            update_operation = {"$set": update_data}
            
            # Increment retry count if this is a failed attempt
            if increment_retry:
                update_operation["$inc"] = {"retry_count": 1}
            
            result = self.collection.update_one(
                {"upload_id": upload_id},
                update_operation
            )
            
            success = result.modified_count > 0
            logger.info("Upload status updated: upload_id=%s, status=%s, increment_retry=%s, success=%s", 
                       upload_id, status.value, increment_retry, success)
            
            return success
            
        except Exception as e:
            logger.error("Error updating upload status for %s: %s", upload_id, e, exc_info=True)
            return False
    
    async def get_uploads_by_status(self, status: RawDataStatus, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get uploads by status
        
        Args:
            status: Status to filter by
            limit: Maximum number of records to return
            
        Returns:
            List[Dict[str, Any]]: List of upload records
        """
        try:
            cursor = self.collection.find(
                {"status": status.value}
            ).sort("created_at", -1).limit(limit)
            
            records = []
            for record in cursor:
                record["_id"] = str(record["_id"])
                records.append(record)
            
            return records
            
        except Exception as e:
            logger.error("Error getting uploads by status %s: %s", status.value, e, exc_info=True)
            return []
    
    def get_uploads_by_provider(self, provider_type: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get uploads by provider type
        
        Args:
            provider_type: Provider type to filter by
            limit: Maximum number of records to return
            
        Returns:
            List[Dict[str, Any]]: List of upload records
        """
        try:
            cursor = self.collection.find(
                {"provider_type": provider_type}
            ).sort("created_at", -1).limit(limit)
            
            records = []
            for record in cursor:
                record["_id"] = str(record["_id"])
                records.append(record)
            
            return records
            
        except Exception as e:
            logger.error("Error getting uploads by provider %s: %s", provider_type, e, exc_info=True)
            return []
    
    def get_upload_statistics(self) -> Dict[str, Any]:
        """
        Get upload statistics
        
        Returns:
            Dict[str, Any]: Upload statistics
        """
        try:
            pipeline = [
                {
                    "$group": {
                        "_id": "$status",
                        "count": {"$sum": 1}
                    }
                }
            ]
            
            status_counts = list(self.collection.aggregate(pipeline))
            
            # Get total count
            total_count = self.collection.count_documents({})
            
            # Get recent uploads (last 24 hours)
            from datetime import timedelta
            yesterday = datetime.now() - timedelta(days=1)
            recent_count = self.collection.count_documents({
                "created_at": {"$gte": yesterday}
            })
            
            return {
                "total_uploads": total_count,
                "recent_uploads_24h": recent_count,
                "status_breakdown": {item["_id"]: item["count"] for item in status_counts}
            }
            
        except Exception as e:
            logger.error("Error getting upload statistics: %s", e, exc_info=True)
            return {
                "total_uploads": 0,
                "recent_uploads_24h": 0,
                "status_breakdown": {},
                "error": str(e)
            }
    
    def delete_upload_record(self, upload_id: str) -> bool:
        """
        Delete upload record
        
        Args:
            upload_id: Upload identifier
            
        Returns:
            bool: True if deleted successfully
        """
        try:
            result = self.collection.delete_one({"upload_id": upload_id})
            if result.deleted_count > 0:
                logger.info("Deleted upload record for %s", upload_id)
                return True
            else:
                logger.warning("No upload record found for deletion: %s", upload_id)
                return False
                
        except Exception as e:
            logger.error("Error deleting upload record for %s: %s", upload_id, e, exc_info=True)
            return False
    
    async def get_pending_uploads(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get pending uploads for processing
        
        Args:
            limit: Maximum number of records to return
            
        Returns:
            List[Dict[str, Any]]: List of pending upload records
        """
        try:
            cursor = self.collection.find(
                {"status": RawDataStatus.PENDING.value}
            ).sort("created_at", 1).limit(limit)
            
            records = []
            for record in cursor:
                record["_id"] = str(record["_id"])
                records.append(record)
            
            logger.info("Retrieved %d pending uploads", len(records))
            return records
            
        except Exception as e:
            logger.error("Error getting pending uploads: %s", e, exc_info=True)
            return []
    
    def get_uploads_by_status_and_date(self, status: List[str], before_date: Optional[datetime] = None, after_date: Optional[datetime] = None, limit: int = 100, max_retries: int = 3) -> List[Dict[str, Any]]:
        """
        Get uploads by status and date range, excluding those that exceeded max retries
        
        Args:
            status: Status to filter by
            before_date: Get records before this date
            after_date: Get records after this date
            limit: Maximum number of records to return
            max_retries: Maximum retry attempts (default: 3)
            
        Returns:
            List[Dict[str, Any]]: List of upload records that haven't exceeded retry limit
        """
        try:
            query = {
                "status": {"$in": status},
                # Exclude records that have been retried too many times
                "$or": [
                    {"retry_count": {"$exists": False}},  # Old records without retry_count
                    {"retry_count": {"$lt": max_retries}}  # Records under retry limit
                ]
            }
            
            if before_date or after_date:
                date_query = {}
                if before_date:
                    date_query["$lt"] = before_date
                if after_date:
                    date_query["$gt"] = after_date
                query["created_at"] = date_query
            
            cursor = self.collection.find(query).sort("created_at", -1).limit(limit)
            
            records = []
            for record in cursor:
                record["_id"] = str(record["_id"])
                records.append(record)
            
            logger.info("Found %d uploads with status %s (max_retries: %d)", len(records), status, max_retries)
            return records
            
        except Exception as e:
            logger.error("Error getting uploads by status and date: %s", e, exc_info=True)
            return []
    
    def delete_old_upload_records(self, cutoff_date: datetime) -> int:
        """
        Delete upload records older than cutoff date
        
        Args:
            cutoff_date: Delete records before this date
            
        Returns:
            int: Number of records deleted
        """
        try:
            result = self.collection.delete_many({
                "created_at": {"$lt": cutoff_date},
                "status": {"$in": [str(RawDataStatus.COMPLETED), str(RawDataStatus.FAILED)]}
            })
            
            deleted_count = result.deleted_count
            logger.info("Deleted %d old upload records", deleted_count)
            return deleted_count
            
        except Exception as e:
            logger.error("Error deleting old upload records: %s", e, exc_info=True)
            return 0
    
    async def get_uploads_paginated(
        self,
        skip: int = 0,
        limit: int = 20,
        status_filter: Optional[str] = None,
        sort_by: str = "created_at",
        sort_order: int = -1
    ) -> Dict[str, Any]:
        """Get paginated uploads with optional filtering and sorting"""
        try:
            # Build query filter
            query_filter = {}
            if status_filter:
                query_filter["status"] = status_filter
            
            # Get total count for pagination metadata
            total_count = self.collection.count_documents(query_filter)
            
            # Build sort criteria
            sort_criteria = [(sort_by, sort_order)]
            
            # Execute paginated query
            cursor = self.collection.find(query_filter).sort(sort_criteria).skip(skip).limit(limit)
            
            uploads = []
            for upload in cursor:
                upload["_id"] = str(upload["_id"])
                uploads.append(upload)
            
            # Calculate pagination metadata
            total_pages = (total_count + limit - 1) // limit if limit > 0 else 1
            current_page = (skip // limit) + 1 if limit > 0 else 1
            has_next = skip + limit < total_count
            has_prev = skip > 0
            
            result = {
                "uploads": uploads,
                "pagination": {
                    "total_count": total_count,
                    "total_pages": total_pages,
                    "current_page": current_page,
                    "page_size": limit,
                    "has_next": has_next,
                    "has_prev": has_prev,
                    "skip": skip,
                    "limit": limit
                }
            }
            
            logger.info("Retrieved %d uploads (page %d/%d, total: %d)", 
                       len(uploads), current_page, total_pages, total_count)
            return result
            
        except Exception as e:
            logger.error("Error getting paginated uploads: %s", e, exc_info=True)
            return {
                "uploads": [],
                "pagination": {
                    "total_count": 0,
                    "total_pages": 0,
                    "current_page": 1,
                    "page_size": limit,
                    "has_next": False,
                    "has_prev": False,
                    "skip": skip,
                    "limit": limit
                }
            }