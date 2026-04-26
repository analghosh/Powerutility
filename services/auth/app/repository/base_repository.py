"""
Base repository for authentication service
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorCollection
from bson import ObjectId
import logging

logger = logging.getLogger(__name__)


class BaseRepository:
    """Base repository with common CRUD operations using Motor async client."""

    def __init__(self, collection_name: str, db_name: str = None):
        from ..core.config import settings
        from ..core.database import get_auth_database, get_audit_database

        self.db_name = db_name or settings.AUTH_DB_NAME

        if self.db_name == settings.AUDIT_DB_NAME:
            self.db: AsyncIOMotorDatabase = get_audit_database()
        else:
            self.db: AsyncIOMotorDatabase = get_auth_database()

        self.collection: AsyncIOMotorCollection = self.db[collection_name]
        self.collection_name = collection_name

    async def create(self, data: Dict[str, Any]) -> str:
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
        try:
            if not ObjectId.is_valid(id):
                return None
            return await self.collection.find_one({"_id": ObjectId(id)})
        except Exception as e:
            logger.error(f"Error finding document by ID in {self.collection_name}: {e}")
            return None

    async def find_one(self, filter: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            return await self.collection.find_one(filter)
        except Exception as e:
            logger.error(f"Error finding document in {self.collection_name}: {e}")
            return None

    async def find_many(self, filter: Dict[str, Any], limit: int = 100, skip: int = 0, sort: List[tuple] = None) -> List[Dict[str, Any]]:
        try:
            cursor = self.collection.find(filter).skip(skip).limit(limit)
            if sort:
                cursor = cursor.sort(sort)
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error(f"Error finding documents in {self.collection_name}: {e}")
            return []

    async def update(self, id: str, data: Dict[str, Any]) -> bool:
        try:
            if not ObjectId.is_valid(id):
                return False
            data["updated_at"] = datetime.now()
            result = await self.collection.update_one(
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

    async def delete(self, id: str) -> bool:
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
        try:
            if not ObjectId.is_valid(id):
                return False
            result = await self.collection.update_one(
                {"_id": ObjectId(id)},
                {"$set": {"is_deleted": True, "deleted_at": datetime.now(), "updated_at": datetime.now()}}
            )
            success = result.modified_count > 0
            if success:
                logger.info(f"Soft deleted document in {self.collection_name} with ID: {id}")
            return success
        except Exception as e:
            logger.error(f"Error soft deleting document in {self.collection_name}: {e}")
            return False

    async def exists(self, filter: Dict[str, Any]) -> bool:
        try:
            count = await self.collection.count_documents(filter, limit=1)
            return count > 0
        except Exception as e:
            logger.error(f"Error checking document existence in {self.collection_name}: {e}")
            return False

    async def count(self, filter: Dict[str, Any]) -> int:
        try:
            return await self.collection.count_documents(filter)
        except Exception as e:
            logger.error(f"Error counting documents in {self.collection_name}: {e}")
            return 0

    def close(self):
        logger.debug(f"Repository {self.collection_name} using Motor connection pool")
