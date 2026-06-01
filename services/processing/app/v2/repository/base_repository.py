"""
Generic Base Repository for v2 API
Provides common database operations that work with any collection
"""

from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.database import Database
from bson import ObjectId
from ...core.config import settings
import logging
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date

logger = logging.getLogger(__name__)


def _serialize_value(value):
    """Convert common BSON/Mongo types to JSON-serializable values."""
    try:
        # primitives
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


class CursorWrapper:
    """Wrap a PyMongo cursor and yield serialized documents while preserving chainable methods."""
    def __init__(self, cursor):
        self._cursor = cursor

    def sort(self, *args, **kwargs):
        return CursorWrapper(self._cursor.sort(*args, **kwargs))

    def limit(self, *args, **kwargs):
        return CursorWrapper(self._cursor.limit(*args, **kwargs))

    def skip(self, *args, **kwargs):
        return CursorWrapper(self._cursor.skip(*args, **kwargs))

    def batch_size(self, *args, **kwargs):
        return CursorWrapper(self._cursor.batch_size(*args, **kwargs))

    def __iter__(self):
        for doc in self._cursor:
            yield _serialize_value(doc)

    def __next__(self):
        doc = next(self._cursor)
        return _serialize_value(doc)

    def next(self):
        return self.__next__()

    def to_list(self):
        return [ _serialize_value(d) for d in self._cursor ]



class GenericRepository:
    """
    Generic repository class for database operations.
    Handles common database functionality with MongoDB.
    Uses centralized MongoClientFactory for connection management.
    """

    def __init__(self):
        self._client: Optional[MongoClient] = None
        self._db: Optional[Database] = None
        self.db_name = None
        
        logger.info("GenericRepository initialized (lazy connection)")
    
    @property
    def client(self) -> MongoClient:
        """Lazy initialization of MongoDB client using centralized factory.
        Detects closed clients and reconnects automatically."""
        # The factory (MongoClientFactory) already handles health checks and ping tests.
        # We don't need to check _closed here as MongoClient doesn't have it, 
        # and we should just trust the factory to provide a healthy client.
        
        if self._client is None:
            from libs.database.mongo_client_factory import get_processing_client
            
            logger.info(f"Connecting to MongoDB via factory: {settings.MONGODB_URI[:50]}...")
            # Use centralized factory with proper connection pooling
            self._client = get_processing_client(settings.MONGODB_URI)
            
            # Test connection immediately
            try:
                self._client.admin.command('ping')
                logger.debug(f"MongoDB client connected successfully via factory")
            except Exception as e:
                logger.error(f"MongoDB connection test failed: {e}")
                raise
        return self._client
    
    @property 
    def db(self) -> Database:
        """Lazy initialization of MongoDB database.
        Resets if the underlying client has been refreshed."""
        if self._db is None:
            self.db_name = settings.MONGODB_DATABASE
            self._db = self.client[self.db_name]
            logger.info(f"Connected to database: {self.db_name}")
            logger.debug(f"Logger effective level: {logger.getEffectiveLevel()}")
        return self._db
    
    def get_all(
        self, 
        collection_name: str,
        filter: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, int]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        skip: int = 0, # Added skip parameter
        limit: Optional[int] = None,
        batch_size: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Get all documents from any collection with optional filtering, projection, and sorting
        
        Args:
            collection_name: Name of the collection to query
            filter: MongoDB filter query (default: {})
            projection: Fields to include/exclude (default: None - all fields)
            sort: List of (field, direction) tuples for sorting (default: None)
            skip: Number of documents to skip (default: 0)
            limit: Maximum number of documents to return (default: None - no limit)
            batch_size: Cursor batch size for memory efficiency (default: 1000)
            
        Returns:
            List of documents matching the criteria
        """
        try:
            collection = self.db[collection_name]
            query_filter = filter or {}
            
            # Create cursor with batch size optimization
            cursor = collection.find(query_filter, projection).batch_size(batch_size)
            
            if sort:
                cursor = cursor.sort(sort)
            
            if skip > 0:
                cursor = cursor.skip(skip)

            if limit:
                cursor = cursor.limit(limit)
            
            # Efficient cursor processing with serialization
            results = []
            doc_count = 0
            
            for document in cursor:
                # Serialize the document to handle ObjectIds and other BSON types
                serialized_doc = _serialize_value(document)
                results.append(serialized_doc)
                doc_count += 1
                
                # Log progress for large queries
                if doc_count % batch_size == 0:
                    logger.info(f"Retrieved {doc_count} documents from '{collection_name}'...")
            
            logger.info(f"Retrieved {len(results)} documents from '{collection_name}' (limit: {limit or 'none'})")
            return results
            
        except Exception as e:
            logger.error(f"Error retrieving documents from '{collection_name}': {e}")
            return []

    def find_one(self, collection_name: str, filter: Dict[str, Any], projection: Optional[Dict[str, int]] = None) -> Optional[Dict[str, Any]]:
        """
        Find a single document
        """
        try:
            collection = self.db[collection_name]
            result = collection.find_one(filter, projection)
            if result:
                return _serialize_value(result)
            return None
        except Exception as e:
            logger.error(f"Error retrieving document from '{collection_name}': {e}")
            return None

    async def find_one_async(self, collection_name: str, query: Dict[str, Any], projection: Optional[Dict[str, int]] = None) -> Optional[Dict[str, Any]]:
        """Async wrapper for find_one"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.find_one(collection_name, query, projection))

    async def get_all_async(self, collection_name: str, filter: Optional[Dict[str, Any]] = None, projection: Optional[Dict[str, int]] = None, sort: Optional[List[Tuple[str, int]]] = None, skip: int = 0, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Async wrapper for get_all"""
        loop = asyncio.get_event_loop()
        # Note: sort param in get_all expects list of tuples, verify usage matches
        return await loop.run_in_executor(None, lambda: self.get_all(collection_name, filter=filter, projection=projection, sort=sort, skip=skip, limit=limit))

    async def insert_one_async(self, collection_name: str, doc: Dict[str, Any]):
        """Async wrapper for insert_one"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.insert_one(collection_name, doc))

    async def update_one_async(self, collection_name: str, filter: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        """Async wrapper for update_one"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.update_one(collection_name, filter, update, upsert=upsert))
    
    async def update_many_async(self, collection_name: str, filter_query: dict, update_query: dict):
        collection = self.db[collection_name]
        return collection.update_many(filter_query, update_query)

    def aggregate(self, collection_name: str, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Run aggregation pipeline
        """
        try:
            collection = self.db[collection_name]
            return list(collection.aggregate(pipeline))
        except Exception as e:
            logger.error(f"Error aggregating documents from '{collection_name}': {e}")
            return []

    async def aggregate_async(self, collection_name: str, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Async wrapper for aggregate"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.aggregate(collection_name, pipeline))

    # --- Compatibility shims ---
    def find(self, collection_name: str, filter: Optional[Dict[str, Any]] = None, projection: Optional[Dict[str, int]] = None):
        collection = self.db[collection_name]
        cursor = collection.find(filter or {}, projection)
        return CursorWrapper(cursor)

    def find_many(self, collection_name: str, filter: Optional[Dict[str, Any]] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Compatibility shim for find_many"""
        return self.get_all(collection_name, filter=filter, limit=limit)

    def insert_one(self, collection_name: str, doc: Dict[str, Any]):
        collection = self.db[collection_name]
        return collection.insert_one(doc)
    
    def insert_many(self, collection_name: str, docs: List[Dict[str, Any]]):
        collection = self.db[collection_name]
        return collection.insert_many(docs)

    def update_one(self, collection_name: str, filter: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        collection = self.db[collection_name]
        return collection.update_one(filter, update, upsert=upsert)

    def delete_one(self, collection_name: str, filter: Dict[str, Any]):
        collection = self.db[collection_name]
        return collection.delete_one(filter)

    def delete_many(self, collection_name: str, filter: Dict[str, Any]):
        collection = self.db[collection_name]
        return collection.delete_many(filter)

    def replace_one(self, collection_name: str, filter: Dict[str, Any], replacement: Dict[str, Any], upsert: bool = False):
        collection = self.db[collection_name]
        return collection.replace_one(filter, replacement, upsert=upsert)
    
    def get_count(self, collection_name: str, filter: Optional[Dict[str, Any]] = None) -> int:
        """
        Get count of documents matching the filter in any collection
        
        Args:
            collection_name: Name of the collection to query
            filter: MongoDB filter query (default: {})
            
        Returns:
            Number of documents matching the filter
        """
        try:
            collection = self.db[collection_name]
            query_filter = filter or {}
            count = collection.count_documents(query_filter)
            logger.info(f"Document count for '{collection_name}': {count}")
            return count
            
        except Exception as e:
            logger.error(f"Error counting documents in '{collection_name}': {e}")
            return 0

    def create_index(self, collection_name: str, keys: List[Tuple[str, int]], name: Optional[str] = None, unique: bool = False):
        """
        Create an index on the specified collection.
        
        Args:
            collection_name: Name of the collection
            keys: List of (field, direction) tuples
            name: Optional name for the index
            unique: Whether the index should be unique
        """
        try:
            collection = self.db[collection_name]
            collection.create_index(keys, name=name, unique=unique)
            logger.info(f"Created index '{name}' on '{collection_name}' with keys: {keys}")
        except Exception as e:
            logger.error(f"Error creating index on '{collection_name}': {e}")
            raise
    
    def get_paginated_response(
        self,
        collection_name: str,
        filter: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, int]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        page: int = 1,
        page_size: int = 50
    ) -> Dict[str, Any]:
        """
        Get paginated response with metadata from any collection
        
        Args:
            collection_name: Name of the collection to query
            filter: MongoDB filter query (default: {})
            projection: Fields to include/exclude (default: None - all fields)
            sort: List of (field, direction) tuples for sorting (default: None)
            page: Page number (1-based, default: 1)
            page_size: Number of items per page (default: 50)
            
        Returns:
            Dictionary containing paginated results and metadata
        """
        try:
            collection = self.db[collection_name]
            query_filter = filter or {}
            
            # Calculate pagination parameters
            page = max(1, page)
            page_size = max(1, min(page_size, 1000))  # Cap at 1000 items per page
            skip = (page - 1) * page_size
            
            # Get total count
            total = collection.count_documents(query_filter)
            
            # Get documents for current page
            cursor = collection.find(query_filter, projection)
            
            if sort:
                cursor = cursor.sort(sort)
            
            cursor = cursor.skip(skip).limit(page_size)
            items = list(cursor)
            
            # Calculate pagination metadata
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
                "has_prev": has_prev
            }
            
            logger.info(f"Paginated query on '{collection_name}': page {page}/{total_pages}, {len(items)} items returned")
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
                "has_prev": False
            }
    
    def aggregate(
        self, 
        collection_name: str,
        pipeline: List[Dict[str, Any]], 
        allow_disk_use: bool = True,
        max_time_ms: Optional[int] = None,
        batch_size: int = 1000,
        max_docs: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Execute aggregation pipeline on any collection with optimizations for large datasets
        
        Args:
            collection_name: Name of the collection to aggregate on
            pipeline: MongoDB aggregation pipeline
            allow_disk_use: Allow disk use for large operations (default: True)
            max_time_ms: Maximum execution time in milliseconds (default: None)
            batch_size: Cursor batch size for memory efficiency (default: 1000)
            max_docs: Maximum documents to return (default: None = unlimited)
            
        Returns:
            List of aggregation results
        """
        try:
            collection = self.db[collection_name]
            
            # Log aggregation execution
            logger.info(f"Executing aggregation on collection '{collection_name}' in database '{self.db_name}'")
            logger.info(f"Pipeline stages: {len(pipeline)}, allowDiskUse: {allow_disk_use}, maxTimeMS: {max_time_ms}")
            
            # Log pipeline details only in debug mode
            if logger.isEnabledFor(logging.DEBUG):
                try:
                    import json
                    logger.debug(f"Aggregation pipeline:\n{json.dumps(pipeline, default=str, indent=2)}")
                except Exception:
                    logger.debug(f"Aggregation pipeline (raw): {pipeline}")
            
            # Set default timeout if not provided (30 seconds)
            if max_time_ms is None:
                max_time_ms = 30000  # 30 seconds default timeout
            
            # Execute aggregation with optimized parameters
            cursor_options = {
                'allowDiskUse': allow_disk_use,
                'batchSize': batch_size,
                'maxTimeMS': max_time_ms  # Always set timeout to prevent hanging
            }
            
            cursor = collection.aggregate(pipeline, **cursor_options)
            
            # Efficient cursor processing with memory management
            try:
                results = []
                doc_count = 0
                
                # Process cursor in batches for memory efficiency
                for document in cursor:
                    # Serialize the document to handle ObjectIds and other BSON types
                    serialized_doc = _serialize_value(document)
                    results.append(serialized_doc)
                    doc_count += 1
                    
                    # Check document limit to prevent memory issues
                    if max_docs and doc_count >= max_docs:
                        logger.warning(f"Aggregation on '{collection_name}' truncated at {max_docs} documents")
                        break
                    
                    # Yield control periodically for large datasets
                    if doc_count % batch_size == 0:
                        logger.info(f"Processed {doc_count} documents from '{collection_name}'...")
                
                # Log results summary
                logger.info(f"Aggregation on '{collection_name}' returned {len(results)} documents")
                
                # Log sample result only in debug mode
                if logger.isEnabledFor(logging.DEBUG) and results:
                    logger.debug(f"Sample result: {results[0]}")
                elif results:
                    logger.info(f"Sample result keys: {list(results[0].keys()) if isinstance(results[0], dict) else 'Non-dict result'}")
                
                return results
                
            except Exception as cursor_error:
                logger.error(f"Error processing aggregation cursor for '{collection_name}': {cursor_error}")
                return []
            
        except Exception as e:
            logger.error(f"Error executing aggregation on {collection_name}: {e}")
            logger.error(f"Pipeline that failed: {pipeline}")
            return []
    
    def aggregate_stream(
        self, 
        collection_name: str,
        pipeline: List[Dict[str, Any]], 
        allow_disk_use: bool = True,
        max_time_ms: Optional[int] = None,
        batch_size: int = 1000
    ):
        """
        Stream aggregation results for very large datasets (generator)
        
        Args:
            collection_name: Name of the collection to aggregate on
            pipeline: MongoDB aggregation pipeline
            allow_disk_use: Allow disk use for large operations (default: True)
            max_time_ms: Maximum execution time in milliseconds (default: None)
            batch_size: Cursor batch size for memory efficiency (default: 1000)
            
        Yields:
            Individual documents from aggregation results
        """
        try:
            collection = self.db[collection_name]
            
            logger.debug(f"Starting streaming aggregation on '{collection_name}'")
            
            # Execute aggregation with optimized parameters
            cursor_options = {
                'allowDiskUse': allow_disk_use,
                'batchSize': batch_size
            }
            
            if max_time_ms is not None:
                cursor_options['maxTimeMS'] = max_time_ms
            
            cursor = collection.aggregate(pipeline, **cursor_options)
            
            # Stream results one by one
            doc_count = 0
            try:
                for document in cursor:
                    yield document
                    doc_count += 1
                    
                    # Log progress periodically
                    if doc_count % batch_size == 0:
                        logger.debug(f"Streamed {doc_count} documents from '{collection_name}'...")
                
                logger.info(f"Streaming aggregation on '{collection_name}' completed: {doc_count} documents")
                
            except Exception as cursor_error:
                logger.error(f"Error streaming aggregation cursor for '{collection_name}': {cursor_error}")
                
        except Exception as e:
            logger.error(f"Error starting streaming aggregation on {collection_name}: {e}")
    
    def aggregate_batched(
        self, 
        collection_name: str,
        pipeline: List[Dict[str, Any]], 
        batch_size: int = 1000,
        allow_disk_use: bool = True,
        max_time_ms: Optional[int] = None
    ):
        """
        Process aggregation results in batches (generator)
        
        Args:
            collection_name: Name of the collection to aggregate on
            pipeline: MongoDB aggregation pipeline
            batch_size: Number of documents per batch (default: 1000)
            allow_disk_use: Allow disk use for large operations (default: True)
            max_time_ms: Maximum execution time in milliseconds (default: None)
            
        Yields:
            Batches of documents as lists
        """
        try:
            collection = self.db[collection_name]
            
            logger.debug(f"Starting batched aggregation on '{collection_name}' (batch_size: {batch_size})")
            
            # Execute aggregation with optimized parameters
            cursor_options = {
                'allowDiskUse': allow_disk_use,
                'batchSize': batch_size
            }
            
            if max_time_ms is not None:
                cursor_options['maxTimeMS'] = max_time_ms
            
            cursor = collection.aggregate(pipeline, **cursor_options)
            
            # Process in batches
            batch = []
            total_count = 0
            
            try:
                for document in cursor:
                    batch.append(document)
                    total_count += 1
                    
                    # Yield batch when full
                    if len(batch) >= batch_size:
                        yield batch
                        logger.debug(f"Yielded batch of {len(batch)} documents from '{collection_name}' (total: {total_count})")
                        batch = []
                
                # Yield remaining documents
                if batch:
                    yield batch
                    logger.debug(f"Yielded final batch of {len(batch)} documents from '{collection_name}'")
                
                logger.info(f"Batched aggregation on '{collection_name}' completed: {total_count} documents")
                
            except Exception as cursor_error:
                logger.error(f"Error processing batched aggregation cursor for '{collection_name}': {cursor_error}")
                
        except Exception as e:
            logger.error(f"Error starting batched aggregation on {collection_name}: {e}")
    
    def find_by_id(self, collection_name: str, id: str) -> Optional[Dict[str, Any]]:
        """
        Find document by ID in any collection
        
        Args:
            collection_name: Name of the collection to query
            id: Document ID (string)
            
        Returns:
            Document if found, None otherwise
        """
        try:
            collection = self.db[collection_name]
            if not ObjectId.is_valid(id):
                return None
            
            doc = collection.find_one({"_id": ObjectId(id)})
            return _serialize_value(doc) if doc else None
            
        except Exception as e:
            logger.error(f"Error finding document by ID in {collection_name}: {e}")
            return None
    
    def find_one(
        self, 
        collection_name: str,
        filter: Dict[str, Any], 
        projection: Optional[Dict[str, int]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Find one document by filter in any collection
        
        Args:
            collection_name: Name of the collection to query
            filter: MongoDB filter query
            projection: Fields to include/exclude (default: None - all fields)
            
        Returns:
            Document if found, None otherwise
        """
        try:
            collection = self.db[collection_name]
            doc = collection.find_one(filter, projection)
            return _serialize_value(doc) if doc else None
            
        except Exception as e:
            logger.error(f"Error finding document in {collection_name}: {e}")
            return None

    def close(self):
        """
        Close database connection reference.
        NOTE: We DO NOT call client.close() here because the client is 
        managed by the singleton MongoClientFactory and shared across repositories.
        """
        try:
            if self._client is not None:
                logger.info(f"Releasing connection reference for database: {self.db_name}")
                self._client = None
                self._db = None
        except Exception as e:
            logger.error(f"Error releasing connection: {e}")
    
    # --- Async Methods for Better Performance ---
    
    async def get_all_async(
        self, 
        collection_name: str,
        filter: Optional[Dict[str, Any]] = None,
        projection: Optional[Dict[str, int]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        skip: int = 0, # Added skip parameter
        limit: Optional[int] = None,
        batch_size: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Async version of get_all for better performance in parallel operations
        
        Args:
            collection_name: Name of the collection to query
            filter: MongoDB filter query (default: {})
            projection: Fields to include/exclude (default: None - all fields)
            sort: List of (field, direction) tuples for sorting (default: None)
            skip: Number of documents to skip (default: 0)
            limit: Maximum number of documents to return (default: None - no limit)
            batch_size: Cursor batch size for memory efficiency (default: 1000)
            
        Returns:
            List of documents matching the criteria
        """
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as executor:
                return await loop.run_in_executor(
                    executor,
                    lambda: self.get_all(
                        collection_name=collection_name,
                        filter=filter,
                        projection=projection,
                        sort=sort,
                        skip=skip, # Passed skip to get_all
                        limit=limit,
                        batch_size=batch_size
                    )
                )
        except Exception as e:
            logger.error(f"Error in async get_all for collection '{collection_name}': {e}")
            return []
    
    async def aggregate_async(
        self, 
        collection_name: str,
        pipeline: List[Dict[str, Any]], 
        allow_disk_use: bool = True,
        max_time_ms: Optional[int] = None,
        batch_size: int = 1000,
        max_docs: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Async version of aggregate for better performance in parallel operations
        
        Args:
            collection_name: Name of the collection to aggregate on
            pipeline: MongoDB aggregation pipeline
            allow_disk_use: Allow disk use for large operations (default: True)
            max_time_ms: Maximum execution time in milliseconds (default: None)
            batch_size: Cursor batch size for memory efficiency (default: 1000)
            max_docs: Maximum documents to return (default: None = unlimited)
            
        Returns:
            List of aggregation results
        """
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as executor:
                return await loop.run_in_executor(
                    executor,
                    lambda: self.aggregate(
                        collection_name=collection_name,
                        pipeline=pipeline,
                        allow_disk_use=allow_disk_use,
                        max_time_ms=max_time_ms,
                        batch_size=batch_size,
                        max_docs=max_docs
                    )
                )
        except Exception as e:
            logger.error(f"Error in async aggregate for collection '{collection_name}': {e}")
            return []

    async def insert_many_async(
        self, 
        collection_name: str,
        documents: List[Dict[str, Any]]
    ):
        """
        Async version of insert_many for better performance in parallel operations
        
        Args:
            collection_name: Name of the collection to insert into
            documents: List of documents to insert
            
        Returns:
            Insert result from MongoDB
        """
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as executor:
                return await loop.run_in_executor(
                    executor,
                    lambda: self.insert_many(collection_name, documents)
                )
        except Exception as e:
            logger.error(f"Error in async insert_many for collection '{collection_name}': {e}")
            return None

    async def insert_one_async(
        self, 
        collection_name: str,
        document: Dict[str, Any]
    ):
        """
        Async version of insert_one for better performance in parallel operations
        
        Args:
            collection_name: Name of the collection to insert into
            document: Document to insert
            
        Returns:
            Insert result from MongoDB
        """
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as executor:
                return await loop.run_in_executor(
                    executor,
                    lambda: self.insert_one(collection_name, document)
                )
        except Exception as e:
            logger.error(f"Error in async insert_one for collection '{collection_name}': {e}")
            return None

    async def update_one_async(
        self, 
        collection_name: str,
        filter: Dict[str, Any],
        update: Dict[str, Any],
        upsert: bool = False
    ):
        """
        Async version of update_one for better performance in parallel operations
        
        Args:
            collection_name: Name of the collection to update
            filter: MongoDB filter query
            update: Update operations
            upsert: Whether to insert if document doesn't exist
            
        Returns:
            Update result from MongoDB
        """
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as executor:
                return await loop.run_in_executor(
                    executor,
                    lambda: self.update_one(collection_name, filter, update, upsert)
                )
        except Exception as e:
            logger.error(f"Error in async update_one for collection '{collection_name}': {e}")
            return None

    async def delete_many_async(
        self, 
        collection_name: str,
        filter: Dict[str, Any]
    ):
        """
        Async version of delete_many for better performance in parallel operations
        
        Args:
            collection_name: Name of the collection to delete from
            filter: MongoDB filter query
            
        Returns:
            Delete result from MongoDB
        """
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as executor:
                return await loop.run_in_executor(
                    executor,
                    lambda: self.delete_many(collection_name, filter)
                )
        except Exception as e:
            logger.error(f"Error in async delete_many for collection '{collection_name}': {e}")
            return None

    async def find_one_async(
        self, 
        collection_name: str,
        filter: Dict[str, Any],
        projection: Optional[Dict[str, int]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Async version of find_one for better performance in parallel operations
        
        Args:
            collection_name: Name of the collection to query
            filter: MongoDB filter query
            projection: Fields to include/exclude
            
        Returns:
            Document if found, None otherwise
        """
        try:
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as executor:
                return await loop.run_in_executor(
                    executor,
                    lambda: self.find_one(collection_name, filter, projection)
                )
        except Exception as e:
            logger.error(f"Error in async find_one for collection '{collection_name}': {e}")
            return None

    def bulk_upsert_optimized(self, collection_name: str, documents: List[Dict[str, Any]], unique_key_fields: List[str]) -> Dict[str, Any]:
        """
        Perform optimized bulk upsert using MongoDB bulk_write operations.
        
        Args:
            collection_name: Name of the collection
            documents: List of documents to upsert
            unique_key_fields: List of field names that form the unique key
            
        Returns:
            Dict containing operation stats
        """
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
            
            # Prepare update data
            update_data = doc.copy()
            # Remove keys that shouldn't be updated or cause conflicts
            for key in ["_id", "created_at", "inserted_at"]:
                update_data.pop(key, None)
            
            update_data["updated_at"] = current_time
            
            # Use $setOnInsert for creation timestamps
            set_on_insert = {
                "created_at": doc.get("created_at") or current_time,
                "inserted_at": doc.get("inserted_at") or current_time
            }
            
            operations.append(UpdateOne(
                filter_query, 
                {"$set": update_data, "$setOnInsert": set_on_insert}, 
                upsert=True
            ))
            
        if not operations:
            return {"inserted": 0, "updated": 0, "upserted": 0, "total": 0}
            
        # Process in batches
        batch_size = 1000
        total_stats = {"inserted": 0, "updated": 0, "upserted": 0, "total": 0, "matched": 0}
        
        try:
            for i in range(0, len(operations), batch_size):
                batch_ops = operations[i:i + batch_size]
                result = collection.bulk_write(batch_ops, ordered=False)
                
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
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()