"""
Base repository for authentication service
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from bson import ObjectId
import logging

logger = logging.getLogger(__name__)


class BaseRepository:
    """Base repository with common CRUD operations"""
    
    async def __init__(self, collection_name: str, db_name: str = None):
        from ..core.config import settings
        from ..core.database import get_auth_database, get_audit_database
        
        self.db_name = db_name or settings.AUTH_DB_NAME
        
        # Use shared database connection instead of creating new ones
        if self.db_name == settings.AUDIT_DB_NAME:
            self.db: Database = get_audit_database()
        else:
            self.db: Database = get_auth_database()
            
        self.collection: Collection = self.db[collection_name]
        self.collection_name = collection_name
    
    async def create(self, data: Dict[str, Any]) -> str:
        """Create a new document"""
        try:
            data["created_at"] = datetime.now()
            data["updated_at"] = datetime.now()
            result = self.collection.insert_one(data)
            logger.info(f"Created document in {self.collection_name} with ID: {result.inserted_id}")
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Error creating document in {self.collection_name}: {e}")
            raise
    
    async def find_by_id(self, id: str) -> Optional[Dict[str, Any]]:
        """Find document by ID"""
        try:
            if not ObjectId.is_valid(id):
                return None
            doc = self.collection.find_one({"_id": ObjectId(id)})
            return doc
        except Exception as e:
            logger.error(f"Error finding document by ID in {self.collection_name}: {e}")
            return None
    
    async def find_one(self, filter: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find one document by filter"""
        try:
            doc = self.collection.find_one(filter)
            return doc
        except Exception as e:
            logger.error(f"Error finding document in {self.collection_name}: {e}")
            return None
    
    async def find_many(self, filter: Dict[str, Any], limit: int = 100, skip: int = 0, sort: List[tuple] = None) -> List[Dict[str, Any]]:
        """Find multiple documents by filter with pagination and sorting"""
        try:
            cursor = self.collection.find(filter).skip(skip).limit(limit)
            
            if sort:
                cursor = cursor.sort(sort)
            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error(f"Error finding documents in {self.collection_name}: {e}")
            return []
    
    def update(self, id: str, data: Dict[str, Any]) -> bool:
        """Update document by ID"""
        try:
            if not ObjectId.is_valid(id):
                return False
            data["updated_at"] = datetime.now()
            result = self.collection.update_one(
                {"_id": ObjectId(id)},
                {"$set": data}
            )
            success = result.modified_count > 0
            if success:
                logger.info(f"Updated document in {self.collection_name} with ID: {id}")
            return success
        except Exception as e:
            logger.error(f"Error updating document in {self.collection_name}: {e}")
            return False
    
    def delete(self, id: str) -> bool:
        """Delete document by ID"""
        try:
            if not ObjectId.is_valid(id):
                return False
            result = self.collection.delete_one({"_id": ObjectId(id)})
            success = result.deleted_count > 0
            if success:
                logger.info(f"Deleted document in {self.collection_name} with ID: {id}")
            return success
        except Exception as e:
            logger.error(f"Error deleting document in {self.collection_name}: {e}")
            return False
    
    def soft_delete(self, id: str) -> bool:
        """Soft delete document by setting is_deleted flag"""
        try:
            if not ObjectId.is_valid(id):
                return False
            result = self.collection.update_one(
                {"_id": ObjectId(id)},
                {
                    "$set": {
                        "is_deleted": True,
                        "deleted_at": datetime.now(),
                        "updated_at": datetime.now()
                    }
                }
            )
            success = result.modified_count > 0
            if success:
                logger.info(f"Soft deleted document in {self.collection_name} with ID: {id}")
            return success
        except Exception as e:
            logger.error(f"Error soft deleting document in {self.collection_name}: {e}")
            return False
    
    def exists(self, filter: Dict[str, Any]) -> bool:
        """Check if document exists"""
        try:
            count = self.collection.count_documents(filter, limit=1)
            return count > 0
        except Exception as e:
            logger.error(f"Error checking document existence in {self.collection_name}: {e}")
            return False
    
    def count(self, filter: Dict[str, Any]) -> int:
        """Count documents by filter"""
        try:
            return self.collection.count_documents(filter)
        except Exception as e:
            logger.error(f"Error counting documents in {self.collection_name}: {e}")
            return 0
    
    def close(self):
        """
        Releases the repository reference.
        NOTE: Managed by centralized factory. Explicitly calling 
        self.client.close() here would shut down the shared pool.
        """
        try:
            logger.info(f"Released repository reference for {self.collection_name}")
        except Exception as e:
            logger.error(f"Error during cleanup for {self.collection_name}: {e}")
