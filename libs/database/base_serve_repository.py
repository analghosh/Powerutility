"""
Base repository for serve entities (demand and supply)
"""

import logging
from datetime import datetime
from typing import Dict, Any, List
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from .base_repository import BaseRepository


class DataInsertionError(Exception):
    """Base custom exception for data insertion failures"""
    
    def __init__(self, message: str, error_type: str = "GENERAL", details: dict = None):
        super().__init__(message)
        self.error_type = error_type
        self.details = details or {}
    
    def __str__(self):
        base_msg = super().__str__()
        if self.error_type != "GENERAL":
            base_msg = f"[{self.error_type}] {base_msg}"
        if self.details:
            base_msg += f" Details: {self.details}"
        return base_msg


class DataValidationError(DataInsertionError):
    """Exception for data validation failures during insertion"""
    
    def __init__(self, message: str, validation_errors: list = None):
        super().__init__(message, "VALIDATION", {"validation_errors": validation_errors or []})
        self.validation_errors = validation_errors or []


class DatabaseConnectionError(DataInsertionError):
    """Exception for database connection failures during insertion"""
    
    def __init__(self, message: str, connection_details: dict = None):
        super().__init__(message, "CONNECTION", {"connection_details": connection_details or {}})
        self.connection_details = connection_details or {}


class BulkInsertPartialFailureError(DataInsertionError):
    """Exception for partial failures in bulk insert operations"""
    
    def __init__(self, message: str, expected_count: int, actual_count: int, failed_indices: list = None):
        super().__init__(message, "PARTIAL_FAILURE", {
            "expected_count": expected_count,
            "actual_count": actual_count,
            "failed_indices": failed_indices or []
        })
        self.expected_count = expected_count
        self.actual_count = actual_count
        self.failed_indices = failed_indices or []


class DataMappingError(DataInsertionError):
    """Exception for data mapping/transformation failures during insertion"""
    
    def __init__(self, message: str, mapping_errors: list = None):
        super().__init__(message, "MAPPING", {"mapping_errors": mapping_errors or []})
        self.mapping_errors = mapping_errors or []

logger = logging.getLogger(__name__)


class BaseServeRepository(BaseRepository):
    """Base repository for serve entities with common operations"""
    
    def __init__(self, mongodb_uri: str, database_name: str, 
                 serve_collection: str, historical_collection: str):
        """
        Initialize the repository with MongoDB connection
        
        IMPORTANT: Uses shared MongoDB client from BaseRepository to prevent
        connection pool exhaustion. All serve repositories share the same client.
        
        Args:
            mongodb_uri: MongoDB connection URI
            database_name: Database name
            serve_collection: Serve collection name (active data)
            historical_collection: Historical collection name (archived data)
        """
        # Initialize parent class with serve_collection as the main collection
        # This will use the shared client from BaseRepository
        super().__init__(collection_name=serve_collection, mongodb_uri=mongodb_uri, database_name=database_name)
        
        # Access collections using the shared client
        self.serve_collection: Collection = self.db[serve_collection]
        self.historical_collection: Collection = self.db[historical_collection]
        
    def insert_serve(self, entity: Any) -> str:
        """
        Insert a new serve record
        
        Args:
            entity: Entity to insert
            
        Returns:
            str: ID of the inserted record
            
        Raises:
            DataInsertionError: If data insertion fails
        """
        try:
            entity_dict = entity.model_dump(by_alias=True)
            result = self.serve_collection.insert_one(entity_dict)
            
            if not result.inserted_id:
                error_msg = "Failed to insert serve record - no ID returned from database"
                logger.error(error_msg)
                raise DataInsertionError(error_msg)
                
            logger.info("Inserted serve record with ID: %s", result.inserted_id)
            return str(result.inserted_id)
            
        except Exception as e:
            error_msg = f"Error inserting serve record: {str(e)}"
            logger.error(error_msg, exc_info=True)
            if isinstance(e, DataInsertionError):
                raise
            raise DataInsertionError(error_msg) from e
            
    def insert_historical(self, entity: Any) -> str:
        """
        Insert a new historical record
        
        Args:
            entity: Historical entity to insert
            
        Returns:
            str: ID of the inserted record
        """
        try:
            entity_dict = entity.model_dump(by_alias=True)
            result = self.historical_collection.insert_one(entity_dict)
            logger.info("Inserted historical record with ID: %s", result.inserted_id)
            return str(result.inserted_id)
        except Exception as e:
            logger.error("Error inserting historical record: %s", str(e))
            raise

    def bulk_insert_serve(self, entities: List[Any]) -> int:
        """
        Bulk insert serve records
        
        Args:
            entities: List of entities (can be entity objects or dictionaries)
            
        Returns:
            Number of inserted records
            
        Raises:
            DataInsertionError: If data insertion fails
        """
        try:
            if not entities:
                logger.info("No entities to insert")
                return 0
            
            # Handle both entity objects and dictionaries
            entity_dicts = []
            invalid_entities = []
            
            for i, entity in enumerate(entities):
                try:
                    if hasattr(entity, 'model_dump'):
                        # Entity object with model_dump method
                        entity_dicts.append(entity.model_dump(by_alias=True))
                    elif isinstance(entity, dict):
                        # Already a dictionary
                        entity_dicts.append(entity)
                    else:
                        logger.warning(f"Unsupported entity type at index {i}: {type(entity)}")
                        invalid_entities.append(i)
                        continue
                except Exception as entity_error:
                    logger.error(f"Error processing entity at index {i}: {str(entity_error)}")
                    invalid_entities.append(i)
                    continue
            
            if not entity_dicts:
                error_msg = "No valid entities to insert after processing"
                logger.error(error_msg)
                raise DataValidationError(error_msg, [f"Entity at index {i} is invalid" for i in invalid_entities])
            
            if invalid_entities:
                logger.warning(f"Skipped {len(invalid_entities)} invalid entities at indices: {invalid_entities}")
            
            result = self.serve_collection.insert_many(entity_dicts)
            inserted_count = len(result.inserted_ids)
            
            if inserted_count != len(entity_dicts):
                error_msg = f"Bulk insert partial failure: expected {len(entity_dicts)} records, got {inserted_count}"
                logger.error(error_msg)
                raise BulkInsertPartialFailureError(
                    error_msg, 
                    expected_count=len(entity_dicts), 
                    actual_count=inserted_count,
                    failed_indices=invalid_entities
                )
            
            logger.info("Bulk inserted %d serve records successfully", inserted_count)
            return inserted_count
            
        except Exception as e:
            error_msg = f"Error bulk inserting serve records: {str(e)}"
            logger.error(error_msg, exc_info=True)
            if isinstance(e, DataInsertionError):
                raise
            raise DataInsertionError(error_msg) from e

    def bulk_insert_historical(self, entities: List[Any]) -> List[str]:
        """
        Bulk insert historical records
        
        Args:
            entities: List of historical entities to insert
            
        Returns:
            List of inserted record IDs
        """
        try:
            if not entities:
                logger.info("No historical entities to insert")
                return []
            
            entity_dicts = [entity.model_dump(by_alias=True) for entity in entities]
            result = self.historical_collection.insert_many(entity_dicts)
            inserted_ids = [str(id) for id in result.inserted_ids]
            
            logger.info("Bulk inserted %d historical records", len(inserted_ids))
            return inserted_ids
        except Exception as e:
            logger.error("Error bulk inserting historical records: %s", str(e))
            raise
            
    def delete_serve_by_date(self, scheduled_at: datetime) -> Dict[str, Any]:
        """
        Delete serve records by scheduled_at date
        
        Args:
            scheduled_at: Schedule date to delete records for
            
        Returns:
            Dict[str, Any]: Dictionary containing the number of records deleted and the deleted records
        """
        try:
            deleted_records = self.find_serve_by_date(scheduled_at)
            # Delete the records
            result = self.serve_collection.delete_many({"scheduled_at": scheduled_at})
            deleted_count = result.deleted_count
            
            logger.info("Deleted %d serve records for date: %s", deleted_count, scheduled_at)
            
            return {
                "deleted_count": deleted_count,
                "deleted_records": deleted_records
            }
        except Exception as e:
            logger.error("Error deleting serve records for date %s: %s", scheduled_at, str(e))
            raise
            
    def find_serve_by_date(self, scheduled_at: datetime) -> List[Dict[str, Any]]:
        """
        Find serve records by scheduled_at date
        
        Args:
            scheduled_at: Schedule date to find records for
            
        Returns:
            List of records found
        """
        try:
            records = list(self.serve_collection.find({"scheduled_at": scheduled_at}))
            logger.info("Found %d serve records for date: %s", len(records), scheduled_at)
            return records
        except Exception as e:
            logger.error("Error finding serve records for date %s: %s", scheduled_at, str(e))
            raise
            
    def find_serve_by_date_range(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """
        Find serve records by date range
        
        Args:
            start_date: Start date
            end_date: End date
            
        Returns:
            List of records found
        """
        try:
            query = {
                "$or": [
                    {"inserted_at": {"$gte": start_date, "$lt": end_date}},
                    {"created_at": {"$gte": start_date, "$lt": end_date}},
                    {"timestamp": {"$gte": start_date, "$lt": end_date}},
                    {"scheduled_at": {"$gte": start_date, "$lt": end_date}},
                ]
            }
            records = list(self.serve_collection.find(query))
            logger.info("Found %d serve records for date range: %s to %s", len(records), start_date, end_date)
            return records
        except Exception as e:
            logger.error("Error finding serve records for date range %s to %s: %s", start_date, end_date, str(e))
            raise
            
    def count_serve(self) -> int:
        """
        Count total serve records
        
        Returns:
            int: Total number of records
        """
        try:
            count = self.serve_collection.count_documents({})
            logger.info("Serve collection count: %d", count)
            return count
        except Exception as e:
            logger.error("Error counting serve records: %s", str(e))
            raise
            
    def count_historical(self) -> int:
        """
        Count total historical records
        
        Returns:
            int: Total number of records
        """
        try:
            count = self.historical_collection.count_documents({})
            logger.info("Historical collection count: %d", count)
            return count
        except Exception as e:
            logger.error("Error counting historical records: %s", str(e))
            raise
            
    def upsert_serve_record(self, filter_query: Dict[str, Any], record_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Upsert serve record for time series collections by deleting existing and inserting with same ID.
        This maintains the time series collection definition while preventing data discrepancy.
        
        Args:
            filter_query: Query to find existing record
            record_data: Complete formatted record data to upsert
            
        Returns:
            Dictionary with operation result including 'success', 'operation_type', and 'record_id'
        """
        try:
            # Check if record exists
            existing_record = self.serve_collection.find_one(filter_query)
            
            operation_type = "insert"
            record_id = None
            createdTime = None
            
            if existing_record:
                # Preserve the existing ID
                record_id = existing_record["_id"]
                createdTime = existing_record["created_at"]
                
                # Delete existing record first
                delete_result = self.serve_collection.delete_one({"_id": record_id})
                if delete_result.deleted_count == 0:
                    logger.error("Failed to delete existing record for upsert")
                    return {
                        "success": False,
                        "error": "Failed to delete existing record",
                        "operation_type": "failed_delete",
                        "record_id": None
                    }
                operation_type = "replace"
                logger.debug("Deleted existing record with ID: %s for upsert", record_id)
                
                # Set the same ID in the new record data
                record_data["_id"] = record_id
                record_data["created_at"] = createdTime
            else:
                # For new records, remove any existing _id to let MongoDB generate one
                record_data = {k: v for k, v in record_data.items() if k != "_id"}
            
            # Insert record (with preserved ID for replacements, new ID for inserts)
            insert_result = self.serve_collection.insert_one(record_data)
            
            # Get the final record ID
            final_record_id = record_id if record_id else insert_result.inserted_id
            
            if not final_record_id:
                logger.error("Failed to insert new record for upsert")
                return {
                    "success": False,
                    "error": "Failed to insert new record",
                    "operation_type": "failed_insert",
                    "record_id": None
                }
            
            logger.info("TESTING LOGS: %s record with ID: %s for query: %s", 
                        "Replaced" if operation_type == "replace" else "Inserted", 
                        final_record_id, filter_query)
            
            return {
                "success": True,
                "operation_type": operation_type,
                "record_id": str(final_record_id)
            }
            
        except Exception as e:
            logger.error("Error upserting serve record: %s", str(e))
            return {
                "success": False,
                "error": str(e),
                "operation_type": "error",
                "record_id": None
            }

    def bulk_upsert_serve_records(self, records_with_filters: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Bulk upsert serve records for time series collections.
        Each record should contain 'filter_query' and 'record_data'.
        
        Args:
            records_with_filters: List of dictionaries containing 'filter_query' and 'record_data'
            
        Returns:
            Dictionary with bulk operation results
        """
        try:
            if not records_with_filters:
                return {
                    "success": True,
                    "inserted_count": 0,
                    "replaced_count": 0,
                    "total_count": 0,
                    "failed_count": 0
                }
            
            inserted_count = 0
            replaced_count = 0
            failed_count = 0
            
            for record_info in records_with_filters:
                filter_query = record_info.get("filter_query")
                record_data = record_info.get("record_data")
                
                if not filter_query or not record_data:
                    logger.warning("Skipping record missing filter_query or record_data")
                    failed_count += 1
                    continue
                
                # Perform individual upsert
                result = self.upsert_serve_record(filter_query, record_data)
                
                if result.get("success"):
                    logger.info(f"TESTING LOGS: Successfully upserted block {filter_query.get('time_block')} ({filter_query.get('type')})")
                    if result.get("operation_type") == "insert":
                        inserted_count += 1
                    elif result.get("operation_type") == "replace":
                        replaced_count += 1
                else:
                    failed_count += 1
                    logger.warning("TESTING LOGS: Failed to upsert record: %s", result.get("error"))
            
            total_count = inserted_count + replaced_count
            
            logger.info("Bulk upsert completed: %d inserted, %d replaced, %d failed out of %d records",
                       inserted_count, replaced_count, failed_count, len(records_with_filters))
            
            return {
                "success": failed_count == 0,  # Success if no failures
                "inserted_count": inserted_count,
                "replaced_count": replaced_count,
                "total_count": total_count,
                "failed_count": failed_count
            }
            
        except Exception as e:
            logger.error("Error in bulk upsert serve records: %s", str(e))
            return {
                "success": False,
                "error": str(e),
                "inserted_count": 0,
                "replaced_count": 0,
                "total_count": 0,
                "failed_count": len(records_with_filters) if records_with_filters else 0
            }

    def bulk_upsert_optimized(self, records: List[Dict[str, Any]], unique_key_fields: List[str]) -> Dict[str, Any]:
        """
        Optimized bulk upsert using MongoDB bulk_write operations.
        
        Args:
            records: List of dictionaries containing the data to upsert.
            unique_key_fields: List of field names that form the unique key for matching.
            
        Returns:
            Dict with 'inserted_count', 'modified_count', 'upserted_count', 'success'.
        """
        try:
            if not records:
                return {
                    "success": True,
                    "inserted_count": 0,
                    "modified_count": 0,
                    "upserted_count": 0,
                    "total_processed": 0
                }

            from pymongo import UpdateOne
            
            operations = []
            
            for record in records:
                # Construct filter from unique key fields
                filter_query = {field: record.get(field) for field in unique_key_fields}
                
                # MongoDB requires specific update operators. 
                # We use $set for all fields in the record.
                # Exclude _id, created_at, inserted_at from update data to prevent conflicts with setOnInsert
                # and ensure immutability of creation timestamps.
                excluded_fields = {"_id", "created_at", "inserted_at"}
                update_data = {k: v for k, v in record.items() if k not in excluded_fields}
                
                # Add/Update timestamps
                current_time = datetime.now()
                update_data["updated_at"] = current_time
                
                # For insert, we also want created_at. 
                # $setOnInsert allows setting fields ONLY when a new document is inserted.
                # Use value from record if available, else current_time
                created_at_val = record.get("created_at") or current_time
                inserted_at_val = record.get("inserted_at") or current_time
                
                set_on_insert = {"created_at": created_at_val, "inserted_at": inserted_at_val}
                
                # Construct the UpdateOne operation with upsert=True
                op = UpdateOne(
                    filter_query,
                    {
                        "$set": update_data,
                        "$setOnInsert": set_on_insert
                    },
                    upsert=True
                )
                operations.append(op)
            
            if not operations:
                return {"success": True, "total_processed": 0}
            
            # Execute bulk write
            # ordered=False allows continuing even if one fails (good for batch processing resilience)
            result = self.serve_collection.bulk_write(operations, ordered=False)
            
            # Count upserts (new insertions) and modifications
            # bulk_write result provides:
            # - inserted_count (for InsertOne only, so 0 here)
            # - upserted_count (number of upserts that resulted in an insert)
            # - modified_count (number of existing docs modified)
            # - matched_count (number of docs matched)
            
            return {
                "success": True,
                "inserted_count": result.upserted_count, 
                "modified_count": result.modified_count,
                "upserted_count": result.upserted_count,
                "matched_count": result.matched_count,
                "total_processed": len(records)
            }
            
        except Exception as e:
            logger.error(f"Error in bulk_upsert_optimized: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "total_processed": 0
            }

