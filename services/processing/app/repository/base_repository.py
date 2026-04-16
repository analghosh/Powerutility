"""
Async base repository with common database operations using Motor
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorCollection
from bson import ObjectId
import logging

logger = logging.getLogger(__name__)


class BaseRepository:
    """Async base repository with common CRUD operations using Motor"""

    def __init__(self, collection_name: str, db_name: str = None):  # type: ignore
        from ..core.config import settings
        from ..core.database import get_database

        self.db_name = db_name or settings.MONGODB_DATABASE
        self.db: AsyncIOMotorDatabase = get_database()
        self.collection: AsyncIOMotorCollection = self.db[collection_name]
        self.collection_name = collection_name

    async def create(self, data: Dict[str, Any]) -> str:
        """Create a new document"""
        try:
            data["created_at"] = datetime.now()
            data["updated_at"] = datetime.now()
            result = await self.collection.insert_one(data)
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
            doc = await self.collection.find_one({"_id": ObjectId(id)})
            return doc
        except Exception as e:
            logger.error(f"Error finding document by ID in {self.collection_name}: {e}")
            return None

    async def find_one(self, filter: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find one document by filter"""
        try:
            doc = await self.collection.find_one(filter)
            return doc
        except Exception as e:
            logger.error(f"Error finding document in {self.collection_name}: {e}")
            return None

    async def find_many(self, filter: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]:
        """Find multiple documents by filter"""
        try:
            cursor = self.collection.find(filter).limit(limit)
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error finding documents in {self.collection_name}: {e}")
            return []

    async def update(self, id: str, data: Dict[str, Any]) -> bool:
        """Update document by ID"""
        try:
            if not ObjectId.is_valid(id):
                return False
            data["updated_at"] = datetime.now()
            result = await self.collection.update_one(
                {"_id": ObjectId(id)}, {"$set": data}
            )
            success = result.modified_count > 0
            if success:
                logger.info(f"Updated document in {self.collection_name} with ID: {id}")
            return success
        except Exception as e:
            logger.error(f"Error updating document in {self.collection_name}: {e}")
            return False

    async def delete(self, id: str) -> bool:
        """Delete document by ID"""
        try:
            if not ObjectId.is_valid(id):
                return False
            result = await self.collection.delete_one({"_id": ObjectId(id)})
            success = result.deleted_count > 0
            if success:
                logger.info(f"Deleted document in {self.collection_name} with ID: {id}")
            return success
        except Exception as e:
            logger.error(f"Error deleting document in {self.collection_name}: {e}")
            return False

    async def soft_delete(self, id: str) -> bool:
        """Soft delete document by setting is_deleted flag"""
        try:
            if not ObjectId.is_valid(id):
                return False
            result = await self.collection.update_one(
                {"_id": ObjectId(id)},
                {
                    "$set": {
                        "is_deleted": True,
                        "deleted_at": datetime.now(),
                        "updated_at": datetime.now(),
                    }
                },
            )
            success = result.modified_count > 0
            if success:
                logger.info(f"Soft deleted document in {self.collection_name} with ID: {id}")
            return success
        except Exception as e:
            logger.error(f"Error soft deleting document in {self.collection_name}: {e}")
            return False

    async def exists(self, filter: Dict[str, Any]) -> bool:
        """Check if document exists"""
        try:
            count = await self.collection.count_documents(filter, limit=1)
            return count > 0
        except Exception as e:
            logger.error(f"Error checking document existence in {self.collection_name}: {e}")
            return False

    async def count(self, filter: Dict[str, Any]) -> int:
        """Count documents by filter"""
        try:
            return await self.collection.count_documents(filter)
        except Exception as e:
            logger.error(f"Error counting documents in {self.collection_name}: {e}")
            return 0

    async def find_with_pagination(
        self, filter: Dict[str, Any], skip: int = 0, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Find documents with pagination"""
        try:
            cursor = self.collection.find(filter).skip(skip).limit(limit)
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(
                f"Error finding documents with pagination in {self.collection_name}: {e}"
            )
            return []

    async def aggregate(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Execute aggregation pipeline"""
        try:
            cursor = self.collection.aggregate(pipeline)
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error executing aggregation in {self.collection_name}: {e}")
            return []

    async def aggregate_on(
        self, collection_name: str, pipeline: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Execute aggregation pipeline on a specified collection"""
        try:
            other_collection = self.db[collection_name]
            cursor = other_collection.aggregate(pipeline)
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error executing aggregation on {collection_name}: {e}")
            return []

    async def create_index(self, index_spec: List[tuple], unique: bool = False) -> bool:
        """Create index on collection"""
        try:
            await self.collection.create_index(index_spec, unique=unique)
            logger.info(f"Created index on {self.collection_name}: {index_spec}")
            return True
        except Exception as e:
            logger.error(f"Error creating index on {self.collection_name}: {e}")
            return False

    def close(self):
        """Releases the repository reference (connection managed by factory)."""
        try:
            logger.info(f"Released repository reference for {self.collection_name}")
        except Exception as e:
            logger.error(f"Error during cleanup for {self.collection_name}: {e}")
