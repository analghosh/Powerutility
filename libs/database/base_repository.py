"""
Base repository with common database operations
FIXED: Singleton connection pooling to prevent file descriptor leaks
"""

from typing import Optional, List, Dict, Any, Callable
from datetime import datetime
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from bson import ObjectId
import logging
from pymongo import ReturnDocument
import atexit

logger = logging.getLogger(__name__)

# Import centralized MongoDB client factory
from .mongo_client_factory import get_mongo_client


class BaseRepository:
    """Base repository with common CRUD operations and singleton connection pooling"""
    
    def __init__(self, collection_name: str, mongodb_uri: str, database_name: str):
        """
        Initialize the repository with singleton MongoDB connection
        
        IMPORTANT: All repositories now share a single MongoDB client to prevent
        connection pool exhaustion. Previously, each repository created a separate
        client (40 clients × 5 connections = 200 connections). Now all repositories
        share one client with a larger pool (1 client × 50 connections = 50 connections).
        
        Args:
            collection_name: Name of the collection
            mongodb_uri: MongoDB connection URI
            database_name: Database name
        """
        # ✅ FIX: Use SHARED client name for all repositories
        # This ensures all repositories reuse the same MongoClient instance
        # Previous: f"repository_{collection_name}" created 40+ separate clients
        # Current: "shared_repository_client" creates only 1 shared client
        self.client: MongoClient = get_mongo_client(
            mongodb_uri, 
            "shared_repository_client",
            maxPoolSize=50,  # Increased from 5 to handle concurrent operations
            minPoolSize=10   # Increased from 1 to maintain ready connections
        )
        self.db_name = database_name
        self.db: Database = self.client[database_name]
        self.collection: Collection = self.db[collection_name]
        self.collection_name = collection_name
    
    def _create_collection_if_not_exists(self):
        """Create collection if it does not exist"""
        if self.collection_name not in self.db.list_collection_names():
            self.db.create_collection(self.collection_name)
            logger.info(f"Created collection: {self.collection_name}")
        

    def create(self, data: Dict[str, Any], add_timestamps: bool = True) -> str:
        """Create a new document"""
        try:
            if add_timestamps:
                data["created_at"] = datetime.now()
                data["updated_at"] = datetime.now()
            result = self.collection.insert_one(data)
            logger.info(f"Created document in {self.collection_name} with ID: {result.inserted_id}")
            return str(result.inserted_id)
        except Exception as e:
            logger.error(f"Error creating document in {self.collection_name}: {e}")
            raise
    
    def find_by_id(self, id: str) -> Optional[Dict[str, Any]]:
        """Find document by ID"""
        try:
            if not ObjectId.is_valid(id):
                return None
            doc = self.collection.find_one({"_id": ObjectId(id)})
            return doc
        except Exception as e:
            logger.error(f"Error finding document by ID in {self.collection_name}: {e}")
            return None
    
    def find_one(self, filter: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find one document by filter"""
        try:
            doc = self.collection.find_one(filter)
            return doc
        except Exception as e:
            logger.error(f"Error finding document in {self.collection_name}: {e}")
            return None
    
    def find_many(self, filter: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]:
        """Find multiple documents by filter"""
        try:
            cursor = self.collection.find(filter).limit(limit)
            return list(cursor)
        except Exception as e:
            logger.error(f"Error finding documents in {self.collection_name}: {e}")
            return []
    
    def update(self, id: str, data: Dict[str, Any], add_timestamps: bool = True) -> Optional[Dict[str, Any]]:
        """Update document by ID and return the updated document"""
        try:
            if not ObjectId.is_valid(id):
                return None
            if add_timestamps:
                data["updated_at"] = datetime.now()
            
            # Use findOneAndUpdate to get the updated document
            result = self.collection.find_one_and_update(
                {"_id": ObjectId(id)},
                {"$set": data},
                return_document=ReturnDocument.AFTER
            )
            
            if result:
                logger.info(f"Updated document in {self.collection_name} with ID: {id}")
            return result
        except Exception as e:
            logger.error(f"Error updating document in {self.collection_name}: {e}")
            return None
    
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
    
    def find_with_pagination(self, filter: Dict[str, Any], skip: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        """Find documents with pagination"""
        try:
            cursor = self.collection.find(filter).skip(skip).limit(limit)
            return list(cursor)
        except Exception as e:
            logger.error(f"Error finding documents with pagination in {self.collection_name}: {e}")
            return []
    
    def aggregate(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Execute aggregation pipeline"""
        try:
            cursor = self.collection.aggregate(pipeline)
            return list(cursor)
        except Exception as e:
            logger.error(f"Error executing aggregation in {self.collection_name}: {e}")
            return []
    
    def create_index(self, index_spec: List[tuple], unique: bool = False) -> bool:
        """Create index on collection"""
        try:
            self.collection.create_index(index_spec, unique=unique)
            logger.info(f"Created index on {self.collection_name}: {index_spec}")
            return True
        except Exception as e:
            logger.error(f"Error creating index on {self.collection_name}: {e}")
            return False
    
    def bulk_upsert(self, documents: List[Dict[str, Any]], filter_criteria: Callable[[Dict[str, Any]], Dict[str, Any]], add_timestamps: bool = True) -> Dict[str, int]:
        """
        Bulk upsert documents based on custom filter criteria
        
        Args:
            documents: List of documents to upsert
            filter_criteria: Function that takes a document and returns the filter query for finding existing documents
            add_timestamps: Whether to add created_at and updated_at timestamps
            
        Returns:
            Dictionary with counts of inserted and updated documents
        """
        try:
            inserted_count = 0
            updated_count = 0
            
            for doc in documents:
                if add_timestamps:
                    current_time = datetime.now()
                    doc["updated_at"] = current_time
                
                # Use custom filter criteria to check if document exists
                filter_query = filter_criteria(doc)
                existing_doc = self.collection.find_one(filter_query)
                
                if existing_doc:
                    # Update existing document
                    if add_timestamps:
                        doc["updated_at"] = current_time
                    result = self.collection.update_one(
                        filter_query,
                        {"$set": doc}
                    )
                    if result.modified_count > 0:
                        updated_count += 1
                else:
                    # Insert new document
                    if add_timestamps:
                        doc["created_at"] = current_time
                        doc["updated_at"] = current_time
                    self.collection.insert_one(doc)
                    inserted_count += 1
            
            logger.info(f"Bulk upsert completed in {self.collection_name}: {inserted_count} inserted, {updated_count} updated")
            return {
                "inserted": inserted_count,
                "updated": updated_count,
                "total": inserted_count + updated_count
            }
        except Exception as e:
            logger.error(f"Error in bulk upsert for {self.collection_name}: {e}")
            raise
    
    def bulk_upsert_optimized(self, documents: List[Dict[str, Any]], unique_key_fields: List[str]) -> Dict[str, Any]:
        """
        Perform optimized bulk upsert using MongoDB bulk_write operations.
        
        Args:
            documents: List of documents to upsert
            unique_key_fields: List of field names that form the unique key for identifying records
            
        Returns:
            Dict containing operation stats
        """
        if not documents:
            return {"inserted": 0, "updated": 0, "upserted": 0, "total": 0}
            
        from pymongo import UpdateOne
        operations = []
        
        for doc in documents:
            # Create filter from unique key fields
            try:
                filter_query = {field: doc[field] for field in unique_key_fields}
            except KeyError as e:
                logger.warning(f"Document missing unique key field {e}: {doc}")
                continue
            
            # Prepare update operation (set all fields)
            # Remove _id if present to avoid immutable field error on update
            update_data = doc.copy()
            if "_id" in update_data:
                del update_data["_id"]
                
            operations.append(UpdateOne(filter_query, {"$set": update_data}, upsert=True))
            
        if not operations:
            return {"inserted": 0, "updated": 0, "upserted": 0, "total": 0}
            
        # Process in batches to avoid MongoDB timeouts
        batch_size = 1000
        total_stats = {"inserted": 0, "updated": 0, "upserted": 0, "total": 0}
        
        try:
            for i in range(0, len(operations), batch_size):
                batch_ops = operations[i:i + batch_size]
                if not batch_ops:
                    continue
                    
                # ordered=False allows parallel execution and continuation after errors
                result = self.collection.bulk_write(batch_ops, ordered=False)
                
                total_stats["inserted"] += result.inserted_count
                total_stats["updated"] += result.modified_count
                total_stats["upserted"] += result.upserted_count
                total_stats["total"] += (result.inserted_count + result.modified_count + result.upserted_count)
                
            logger.info(f"Bulk upsert optimized result for {self.collection_name}: {total_stats}")
            return total_stats
            
        except Exception as e:
            logger.error(f"Error in bulk_upsert_optimized for {self.collection_name}: {e}")
            # If we partially succeeded, we still raise to indicate failure, 
            # though some records might have been processed.
            raise e
    
    def close(self):
        """Close database connection (managed by singleton pool)"""
        # Connection is managed by singleton pool, no need to close individual instances
        logger.debug(f"Repository {self.collection_name} using singleton connection pool")

    def bulk_delete_then_insert_optimized(self, deletes: List[Dict[str, Any]], inserts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Perform optimized bulk delete-then-insert using MongoDB bulk_write operations.
        Useful for Time Series collections where atomic replacement is restricted.
        
        Args:
            deletes: List of filter dictionaries for DeleteOne operations
            inserts: List of document dictionaries for InsertOne operations
            
        Returns:
            Dict containing operation stats
        """
        if not deletes and not inserts:
            return {"deleted": 0, "inserted": 0, "total": 0}
            
        from pymongo import DeleteOne, InsertOne
        operations = []
        
        # 1. Add Deletes
        for del_filter in deletes:
            operations.append(DeleteOne(del_filter))
            
        # 2. Add Inserts
        for doc in inserts:
            # Remove _id if present to allow new ID generation or avoid conflicts
            if "_id" in doc:
                del doc["_id"]
            
            # Add timestamps
            current_time = datetime.now()
            doc["created_at"] = current_time
            doc["updated_at"] = current_time
            if "inserted_at" not in doc:
                doc["inserted_at"] = current_time
                
            operations.append(InsertOne(doc))
            
        if not operations:
            return {"deleted": 0, "inserted": 0, "total": 0}
            
        try:
            # Execute in batches to avoid timeout
            # Reduced to 100 to prevent socket timeouts on slow connections/large documents
            batch_size = 100
            total_deleted = 0
            total_inserted = 0
            
            for i in range(0, len(operations), batch_size):
                batch = operations[i:i + batch_size]
                if not batch: continue
                
                # ordered=True ensures deletes happen before inserts within the batch context if mixed,
                # but globally we added all deletes first so logic holds.
                result = self.collection.bulk_write(batch, ordered=True)
                total_deleted += result.deleted_count
                total_inserted += result.inserted_count
                logger.debug(f"Batch {i//batch_size + 1}: Deleted {result.deleted_count}, Inserted {result.inserted_count}")
            
            stats = {
                "deleted": total_deleted,
                "inserted": total_inserted,
                "total": total_deleted + total_inserted
            }
            logger.info(f"Bulk delete-insert optimized result for {self.collection_name}: {stats}")
            return stats
            
        except Exception as e:
            logger.error(f"Error in bulk_delete_then_insert_optimized for {self.collection_name}: {e}")
            raise e