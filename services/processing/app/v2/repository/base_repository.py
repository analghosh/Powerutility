"""
Async Generic Base Repository for v2 API using Motor (AsyncIOMotorClient)
Provides common database operations that work with any collection
"""

from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, date
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection
from pymongo import UpdateOne
from bson import ObjectId
from ...core.config import settings
import logging
import json

logger = logging.getLogger(__name__)


def _serialize_value(value):
    """Convert common BSON/Mongo types to JSON-serializable values."""
    try:
        if value is None or isinstance(value, (int, float, bool, str)):
            return value
        if isinstance(value, ObjectId):
            return str(value)
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: _serialize_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_serialize_value(v) for v in value]
        return str(value)
    except Exception:
        try:
            return str(value)
        except Exception:
            return None


class GenericRepository:
    """
    Async generic repository class using Motor (AsyncIOMotorClient).
    Handles common database functionality with async/await.
    """

    def __init__(self):
        self._client: Optional[AsyncIOMotorClient] = None
        self._db: Optional[AsyncIOMotorDatabase] = None
        self.db_name = None
        logger.info("GenericRepository initialized (lazy async Motor connection)")

    @property
    def client(self) -> AsyncIOMotorClient:
        """Lazy initialization of async Motor client using centralized factory."""
        if self._client is None:
            from libs.database.mongo_client_factory import get_processing_client
            logger.info(f"Connecting to MongoDB via async Motor factory: {settings.MONGODB_URI[:50]}...")
            self._client = get_processing_client(settings.MONGODB_URI)
            logger.debug("Async Motor client obtained from factory")
        return self._client

    @property
    def db(self) -> AsyncIOMotorDatabase:
        """Lazy initialization of async Motor database."""
        if self._db is None:
            self.db_name = settings.MONGODB_DATABASE
            self._db = self.client[self.db_name]
            logger.info(f"Connected to database: {self.db_name}")
        return self._db

    async def get_all(
        self,
        collection_name: str,
        filter: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, int]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        skip: int = 0,
        limit: Optional[int] = None,
        batch_size: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Get all documents from any collection with optional filtering, projection, and sorting."""
        try:
            collection = self.db[collection_name]
            query_filter = filter or {}

            cursor = collection.find(query_filter, projection).batch_size(batch_size)

            if sort:
                cursor = cursor.sort(sort)
            if skip > 0:
                cursor = cursor.skip(skip)
            if limit:
                cursor = cursor.limit(limit)

            results = []
            async for document in cursor:
                results.append(_serialize_value(document))
                if len(results) % batch_size == 0:
                    logger.info(f"Retrieved {len(results)} documents from '{collection_name}'...")

            logger.info(
                f"Retrieved {len(results)} documents from '{collection_name}' (limit: {limit or 'none'})"
            )
            return results
        except Exception as e:
            logger.error(f"Error retrieving documents from '{collection_name}': {e}")
            return []

    async def find_one(
        self,
        collection_name: str,
        filter: Dict[str, Any],
        projection: Optional[Dict[str, int]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Find a single document"""
        try:
            collection = self.db[collection_name]
            result = await collection.find_one(filter, projection)
            if result:
                return _serialize_value(result)
            return None
        except Exception as e:
            logger.error(f"Error retrieving document from '{collection_name}': {e}")
            return None

    # Alias for backward compat
    async def find_one_async(
        self,
        collection_name: str,
        query: Dict[str, Any],
        projection: Optional[Dict[str, int]] = None,
    ) -> Optional[Dict[str, Any]]:
        return await self.find_one(collection_name, query, projection)

    async def get_all_async(
        self,
        collection_name: str,
        filter: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, int]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        skip: int = 0,
        limit: Optional[int] = None,
        batch_size: int = 1000,
    ) -> List[Dict[str, Any]]:
        return await self.get_all(
            collection_name,
            filter=filter,
            projection=projection,
            sort=sort,
            skip=skip,
            limit=limit,
            batch_size=batch_size,
        )

    async def aggregate(
        self,
        collection_name: str,
        pipeline: List[Dict[str, Any]],
        allow_disk_use: bool = True,
        max_time_ms: Optional[int] = None,
        batch_size: int = 1000,
        max_docs: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Execute aggregation pipeline on any collection."""
        try:
            collection = self.db[collection_name]
            if max_time_ms is None:
                max_time_ms = 30000

            cursor_options = {
                "allowDiskUse": allow_disk_use,
                "batchSize": batch_size,
                "maxTimeMS": max_time_ms,
            }

            logger.info(
                f"Executing aggregation on '{collection_name}' ({len(pipeline)} stages)"
            )
            cursor = collection.aggregate(pipeline, **cursor_options)

            results = []
            doc_count = 0
            async for document in cursor:
                results.append(_serialize_value(document))
                doc_count += 1
                if max_docs and doc_count >= max_docs:
                    logger.warning(f"Aggregation on '{collection_name}' truncated at {max_docs} docs")
                    break
                if doc_count % batch_size == 0:
                    logger.info(f"Processed {doc_count} documents from '{collection_name}'...")

            logger.info(f"Aggregation on '{collection_name}' returned {len(results)} documents")
            return results
        except Exception as e:
            logger.error(f"Error executing aggregation on {collection_name}: {e}")
            return []

    async def aggregate_async(
        self,
        collection_name: str,
        pipeline: List[Dict[str, Any]],
        allow_disk_use: bool = True,
        max_time_ms: Optional[int] = None,
        batch_size: int = 1000,
        max_docs: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return await self.aggregate(
            collection_name,
            pipeline,
            allow_disk_use=allow_disk_use,
            max_time_ms=max_time_ms,
            batch_size=batch_size,
            max_docs=max_docs,
        )

    async def find_many(
        self,
        collection_name: str,
        filter: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return await self.get_all(collection_name, filter=filter, limit=limit)

    async def insert_one(self, collection_name: str, doc: Dict[str, Any]):
        collection = self.db[collection_name]
        return await collection.insert_one(doc)

    async def insert_one_async(self, collection_name: str, doc: Dict[str, Any]):
        return await self.insert_one(collection_name, doc)

    async def insert_many(self, collection_name: str, docs: List[Dict[str, Any]]):
        collection = self.db[collection_name]
        return await collection.insert_many(docs)

    async def insert_many_async(self, collection_name: str, documents: List[Dict[str, Any]]):
        return await self.insert_many(collection_name, documents)

    async def update_one(
        self,
        collection_name: str,
        filter: Dict[str, Any],
        update: Dict[str, Any],
        upsert: bool = False,
    ):
        collection = self.db[collection_name]
        return await collection.update_one(filter, update, upsert=upsert)

    async def update_one_async(
        self,
        collection_name: str,
        filter: Dict[str, Any],
        update: Dict[str, Any],
        upsert: bool = False,
    ):
        return await self.update_one(collection_name, filter, update, upsert=upsert)

    async def update_many(
        self, collection_name: str, filter_query: dict, update_query: dict
    ):
        collection = self.db[collection_name]
        return await collection.update_many(filter_query, update_query)

    async def update_many_async(
        self, collection_name: str, filter_query: dict, update_query: dict
    ):
        return await self.update_many(collection_name, filter_query, update_query)

    async def delete_one(self, collection_name: str, filter: Dict[str, Any]):
        collection = self.db[collection_name]
        return await collection.delete_one(filter)

    async def delete_many(self, collection_name: str, filter: Dict[str, Any]):
        collection = self.db[collection_name]
        return await collection.delete_many(filter)

    async def delete_many_async(self, collection_name: str, filter: Dict[str, Any]):
        return await self.delete_many(collection_name, filter)

    async def replace_one(
        self,
        collection_name: str,
        filter: Dict[str, Any],
        replacement: Dict[str, Any],
        upsert: bool = False,
    ):
        collection = self.db[collection_name]
        return await collection.replace_one(filter, replacement, upsert=upsert)

    async def get_count(
        self, collection_name: str, filter: Optional[Dict[str, Any]] = None
    ) -> int:
        """Get count of documents matching the filter in any collection."""
        try:
            collection = self.db[collection_name]
            query_filter = filter or {}
            count = await collection.count_documents(query_filter)
            logger.info(f"Document count for '{collection_name}': {count}")
            return count
        except Exception as e:
            logger.error(f"Error counting documents in '{collection_name}': {e}")
            return 0

    async def create_index(
        self,
        collection_name: str,
        keys: List[Tuple[str, int]],
        name: Optional[str] = None,
        unique: bool = False,
    ):
        """Create an index on the specified collection."""
        try:
            collection = self.db[collection_name]
            await collection.create_index(keys, name=name, unique=unique)
            logger.info(f"Created index '{name}' on '{collection_name}' with keys: {keys}")
        except Exception as e:
            logger.error(f"Error creating index on '{collection_name}': {e}")
            raise

    async def get_paginated_response(
        self,
        collection_name: str,
        filter: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, int]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        """Get paginated response with metadata from any collection."""
        try:
            collection = self.db[collection_name]
            query_filter = filter or {}

            page = max(1, page)
            page_size = max(1, min(page_size, 1000))
            skip = (page - 1) * page_size

            total = await collection.count_documents(query_filter)

            cursor = collection.find(query_filter, projection)
            if sort:
                cursor = cursor.sort(sort)
            cursor = cursor.skip(skip).limit(page_size)
            items = await cursor.to_list(length=10000)

            total_pages = (total + page_size - 1) // page_size
            has_next = page < total_pages
            has_prev = page > 1

            result = {
                "items": items,
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_next": has_next,
                "has_prev": has_prev,
            }
            logger.info(
                f"Paginated query on '{collection_name}': page {page}/{total_pages}, "
                f"{len(items)} items returned"
            )
            return result
        except Exception as e:
            logger.error(f"Error executing paginated query on '{collection_name}': {e}")
            return {
                "items": [],
                "page": page,
                "page_size": page_size,
                "total": 0,
                "total_pages": 0,
                "has_next": False,
                "has_prev": False,
            }

    async def find_by_id(
        self, collection_name: str, id: str
    ) -> Optional[Dict[str, Any]]:
        """Find document by ID in any collection."""
        try:
            collection = self.db[collection_name]
            if not ObjectId.is_valid(id):
                return None
            doc = await collection.find_one({"_id": ObjectId(id)})
            return _serialize_value(doc) if doc else None
        except Exception as e:
            logger.error(f"Error finding document by ID in {collection_name}: {e}")
            return None

    async def bulk_upsert_optimized(
        self,
        collection_name: str,
        documents: List[Dict[str, Any]],
        unique_key_fields: List[str],
    ) -> Dict[str, Any]:
        """Perform optimized bulk upsert using MongoDB bulk_write operations."""
        if not documents:
            return {"inserted": 0, "updated": 0, "upserted": 0, "total": 0}

        collection = self.db[collection_name]
        operations = []
        current_time = datetime.now()

        for doc in documents:
            try:
                filter_query = {field: doc[field] for field in unique_key_fields}
            except KeyError as e:
                logger.warning(f"Document missing unique key field {e}: {doc}")
                continue

            update_data = doc.copy()
            for key in ["_id", "created_at", "inserted_at"]:
                update_data.pop(key, None)
            update_data["updated_at"] = current_time

            set_on_insert = {
                "created_at": doc.get("created_at") or current_time,
                "inserted_at": doc.get("inserted_at") or current_time,
            }

            operations.append(
                UpdateOne(
                    filter_query,
                    {"$set": update_data, "$setOnInsert": set_on_insert},
                    upsert=True,
                )
            )

        if not operations:
            return {"inserted": 0, "updated": 0, "upserted": 0, "total": 0}

        batch_size = 1000
        total_stats = {"inserted": 0, "updated": 0, "upserted": 0, "total": 0, "matched": 0}

        try:
            for i in range(0, len(operations), batch_size):
                batch_ops = operations[i : i + batch_size]
                result = await collection.bulk_write(batch_ops, ordered=False)
                total_stats["inserted"] += result.upserted_count
                total_stats["updated"] += result.modified_count
                total_stats["upserted"] += result.upserted_count
                total_stats["matched"] += result.matched_count
                total_stats["total"] += len(batch_ops)

            logger.info(f"Bulk upsert optimized result for {collection_name}: {total_stats}")
            return total_stats
        except Exception as e:
            logger.error(f"Error in bulk_upsert_optimized for {collection_name}: {e}")
            raise

    def close(self):
        """Close database connection reference."""
        try:
            if self._client is not None:
                logger.info(f"Releasing connection reference for database: {self.db_name}")
                self._client = None
                self._db = None
        except Exception as e:
            logger.error(f"Error releasing connection: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
