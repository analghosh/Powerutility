"""
Upload Tracking Repository Implementation
Clean implementation of IUploadTrackingRepository interface
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from bson import ObjectId

from libs.database.base_repository import BaseRepository
from libs.enum.raw_data_status import RawDataStatus
from ..interfaces.repositories import IUploadTrackingRepository

logger = logging.getLogger(__name__)


class UploadTrackingRepositoryImpl(BaseRepository, IUploadTrackingRepository):
    """
    Clean implementation of upload tracking repository
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
        self._create_indexes()
    
    def _create_indexes(self):
        """Create necessary indexes for upload tracking"""
        import asyncio
        async def _do_create():
            try:
                # Index on upload_id for fast lookups
                await self.collection.create_index("upload_id", unique=True)
                
                # Index on status for filtering
                await self.collection.create_index("status")
                
                # Index on created_at for time-based queries
                await self.collection.create_index("created_at")
                
                # Compound index for status and created_at
                await self.collection.create_index([("status", 1), ("created_at", -1)])
                
                logger.info("Upload tracking indexes created successfully")
            except Exception as e:
                logger.error("Error creating upload tracking indexes: %s", e, exc_info=True)
        try:
            asyncio.get_running_loop().create_task(_do_create())
        except RuntimeError:
            pass
    
    async def create_upload_record(
        self,
        upload_id: str,
        file_name: str,
        file_size: int,
        data_type: str,
        provider_type: str,
        status: RawDataStatus = RawDataStatus.PENDING
    ) -> str:
        """Create a new upload tracking record"""
        try:
            upload_data = {
                "upload_id": upload_id,
                "file_name": file_name,
                "file_size": file_size,
                "data_type": data_type,
                "provider_type": provider_type,
                "status": status.value,
                "status_message": "Upload created",
                "created_at": datetime.now(),
                "updated_at": datetime.now()
            }
            
            logger.info("Creating upload record: upload_id=%s, file=%s", upload_id, file_name)
            
            result = await self.collection.insert_one(upload_data)
            document_id = str(result.inserted_id)
            
            logger.info("Upload record created: upload_id=%s, document_id=%s", upload_id, document_id)
            return document_id
            
        except Exception as e:
            logger.error("Error creating upload record: %s", e, exc_info=True)
            raise
    
    async def get_upload_by_id(self, upload_id: str) -> Optional[Dict[str, Any]]:
        """Get upload record by ID"""
        try:
            upload_data = await self.collection.find_one({"upload_id": upload_id})
            
            if upload_data:
                # Convert ObjectId to string for JSON serialization
                upload_data["_id"] = str(upload_data["_id"])
                return upload_data
            
            return None
            
        except Exception as e:
            logger.error("Error getting upload by ID %s: %s", upload_id, e, exc_info=True)
            return None
    
    async def update_upload_status(
        self,
        upload_id: str,
        status: RawDataStatus,
        status_message: str = "",
        completed_at: Optional[datetime] = None,
        failed_at: Optional[datetime] = None
    ) -> bool:
        """Update upload status"""
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
            
            result = await self.collection.update_one(
                {"upload_id": upload_id},
                {"$set": update_data}
            )
            
            success = result.modified_count > 0
            logger.info("Upload status updated: upload_id=%s, status=%s, success=%s", 
                       upload_id, status.value, success)
            
            return success
            
        except Exception as e:
            logger.error("Error updating upload status for %s: %s", upload_id, e, exc_info=True)
            return False
    
    async def get_pending_uploads(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get pending uploads for processing"""
        try:
            cursor = self.collection.find(
                {"status": RawDataStatus.PENDING.value}
            ).sort("created_at", 1).limit(limit)

            uploads = await cursor.to_list(length=limit)
            for upload in uploads:
                upload["_id"] = str(upload["_id"])
            
            logger.info("Retrieved %d pending uploads", len(uploads))
            return uploads
            
        except Exception as e:
            logger.error("Error getting pending uploads: %s", e, exc_info=True)
            return []
    
    async def get_uploads_by_status(
        self,
        status: RawDataStatus,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get uploads by status"""
        try:
            cursor = self.collection.find(
                {"status": status.value}
            ).sort("created_at", -1).limit(limit)

            uploads = await cursor.to_list(length=limit)
            for upload in uploads:
                upload["_id"] = str(upload["_id"])
            
            logger.info("Retrieved %d uploads with status %s", len(uploads), status.value)
            return uploads
            
        except Exception as e:
            logger.error("Error getting uploads by status %s: %s", status.value, e, exc_info=True)
            return []
    
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
            total_count = await self.collection.count_documents(query_filter)
            
            # Build sort criteria
            sort_criteria = [(sort_by, sort_order)]
            
            # Execute paginated query
            cursor = self.collection.find(query_filter).sort(sort_criteria).skip(skip).limit(limit)

            uploads = await cursor.to_list(length=limit)
            for upload in uploads:
                upload["_id"] = str(upload["_id"])
            
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