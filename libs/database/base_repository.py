"""
Async base repository with common database operations using Motor (AsyncIOMotorClient)
"""

from typing import Optional, List, Dict, Any, Callable
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection
from bson import ObjectId
import logging
from pymongo import ReturnDocument

logger = logging.getLogger(__name__)

# Import centralized MongoDB client factory
from .mongo_client_factory import get_mongo_client


class BaseRepository:
    """Async base repository with common CRUD operations using Motor"""

    def __init__(self, collection_name: str, mongodb_uri: str, database_name: str):
        self.client: AsyncIOMotorClient = get_mongo_client(
            mongodb_uri,
            "shared_repository_client",
            maxPoolSize=50,
            minPoolSize=10,
        )
        self.db_name = database_name
        self.db: AsyncIOMotorDatabase = self.client[database_name]
        self.collection: AsyncIOMotorCollection = self.db[collection_name]
        self.collection_name = collection_name

    async def _create_collection_if_not_exists(self):
        """Create collection if it does not exist"""
        names = await self.db.list_collection_names()
        if self.collection_name not in names:
            await self.db.create_collection(self.collection_name)
            logger.info(f"Created collection: {self.collection_name}")

    async def create(self, data: Dict[str, Any], add_timestamps: bool = True) -> str:
        """Create a new document"""
        try:
            if add_timestamps:
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
            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error(f"Error finding documents in {self.collection_name}: {e}")
            return []

    async def update(
        self, id: str, data: Dict[str, Any], add_timestamps: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Update document by ID and return the updated document"""
        try:
            if not ObjectId.is_valid(id):
                return None
            if add_timestamps:
                data["updated_at"] = datetime.now()
            result = await self.collection.find_one_and_update(
                {"_id": ObjectId(id)},
                {"$set": data},
                return_document=ReturnDocument.AFTER,
            )
            if result:
                logger.info(f"Updated document in {self.collection_name} with ID: {id}")
            return result
        except Exception as e:
            logger.error(f"Error updating document in {self.collection_name}: {e}")
            return None

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
            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error(
                f"Error finding documents with pagination in {self.collection_name}: {e}"
            )
            return []

    async def aggregate(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Execute aggregation pipeline"""
        try:
            cursor = self.collection.aggregate(pipeline)
            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error(f"Error executing aggregation in {self.collection_name}: {e}")
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

    async def bulk_upsert(
        self,
        documents: List[Dict[str, Any]],
        filter_criteria: Callable[[Dict[str, Any]], Dict[str, Any]],
        add_timestamps: bool = True,
    ) -> Dict[str, int]:
        """Bulk upsert documents based on custom filter criteria"""
        try:
            inserted_count = 0
            updated_count = 0

            for doc in documents:
                if add_timestamps:
                    current_time = datetime.now()
                    doc["updated_at"] = current_time

                filter_query = filter_criteria(doc)
                existing_doc = await self.collection.find_one(filter_query)

                if existing_doc:
                    if add_timestamps:
                        doc["updated_at"] = current_time
                    result = await self.collection.update_one(filter_query, {"$set": doc})
                    if result.modified_count > 0:
                        updated_count += 1
                else:
                    if add_timestamps:
                        doc["created_at"] = current_time
                        doc["updated_at"] = current_time
                    await self.collection.insert_one(doc)
                    inserted_count += 1

            logger.info(
                f"Bulk upsert completed in {self.collection_name}: "
                f"{inserted_count} inserted, {updated_count} updated"
            )
            return {
                "inserted": inserted_count,
                "updated": updated_count,
                "total": inserted_count + updated_count,
            }
        except Exception as e:
            logger.error(f"Error in bulk upsert for {self.collection_name}: {e}")
            raise

    async def bulk_upsert_optimized(
        self, documents: List[Dict[str, Any]], unique_key_fields: List[str]
    ) -> Dict[str, Any]:
        """Perform optimized bulk upsert using MongoDB bulk_write operations."""
        if not documents:
            return {"inserted": 0, "updated": 0, "upserted": 0, "total": 0}

        from pymongo import UpdateOne

        operations = []
        for doc in documents:
            try:
                filter_query = {field: doc[field] for field in unique_key_fields}
            except KeyError as e:
                logger.warning(f"Document missing unique key field {e}: {doc}")
                continue

            update_data = doc.copy()
            if "_id" in update_data:
                del update_data["_id"]
            operations.append(UpdateOne(filter_query, {"$set": update_data}, upsert=True))

        if not operations:
            return {"inserted": 0, "updated": 0, "upserted": 0, "total": 0}

        batch_size = 1000
        total_stats = {"inserted": 0, "updated": 0, "upserted": 0, "total": 0}

        try:
            for i in range(0, len(operations), batch_size):
                batch_ops = operations[i : i + batch_size]
                if not batch_ops:
                    continue
                result = await self.collection.bulk_write(batch_ops, ordered=False)
                total_stats["inserted"] += result.inserted_count
                total_stats["updated"] += result.modified_count
                total_stats["upserted"] += result.upserted_count
                total_stats["total"] += (
                    result.inserted_count + result.modified_count + result.upserted_count
                )
            logger.info(
                f"Bulk upsert optimized result for {self.collection_name}: {total_stats}"
            )
            return total_stats
        except Exception as e:
            logger.error(f"Error in bulk_upsert_optimized for {self.collection_name}: {e}")
            raise

    def close(self):
        """Close database connection (managed by singleton pool)"""
        logger.debug(f"Repository {self.collection_name} using singleton connection pool")

    async def bulk_delete_then_insert_optimized(
        self,
        deletes: List[Dict[str, Any]],
        inserts: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Perform optimized bulk delete-then-insert using MongoDB bulk_write operations."""
        if not deletes and not inserts:
            return {"deleted": 0, "inserted": 0, "total": 0}

        from pymongo import DeleteOne, InsertOne

        operations = []

        for del_filter in deletes:
            operations.append(DeleteOne(del_filter))

        for doc in inserts:
            if "_id" in doc:
                del doc["_id"]
            current_time = datetime.now()
            doc["created_at"] = current_time
            doc["updated_at"] = current_time
            if "inserted_at" not in doc:
                doc["inserted_at"] = current_time
            operations.append(InsertOne(doc))

        if not operations:
            return {"deleted": 0, "inserted": 0, "total": 0}

        try:
            batch_size = 100
            total_deleted = 0
            total_inserted = 0

            for i in range(0, len(operations), batch_size):
                batch = operations[i : i + batch_size]
                if not batch:
                    continue
                result = await self.collection.bulk_write(batch, ordered=True)
                total_deleted += result.deleted_count
                total_inserted += result.inserted_count
                logger.debug(
                    f"Batch {i // batch_size + 1}: "
                    f"Deleted {result.deleted_count}, Inserted {result.inserted_count}"
                )

            stats = {
                "deleted": total_deleted,
                "inserted": total_inserted,
                "total": total_deleted + total_inserted,
            }
            logger.info(
                f"Bulk delete-insert optimized result for {self.collection_name}: {stats}"
            )
            return stats
        except Exception as e:
            logger.error(
                f"Error in bulk_delete_then_insert_optimized for {self.collection_name}: {e}"
            )
            raise
