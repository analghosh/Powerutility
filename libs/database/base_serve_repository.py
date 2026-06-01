"""
Base repository for serve entities (demand and supply)
"""

import logging
from datetime import datetime
from typing import Dict, Any, List
from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import UpdateOne

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
    def __init__(self, message: str, validation_errors: list = None):
        super().__init__(message, "VALIDATION", {"validation_errors": validation_errors or []})
        self.validation_errors = validation_errors or []


class DatabaseConnectionError(DataInsertionError):
    def __init__(self, message: str, connection_details: dict = None):
        super().__init__(message, "CONNECTION", {"connection_details": connection_details or {}})
        self.connection_details = connection_details or {}


class BulkInsertPartialFailureError(DataInsertionError):
    def __init__(self, message: str, expected_count: int, actual_count: int, failed_indices: list = None):
        super().__init__(message, "PARTIAL_FAILURE", {
            "expected_count": expected_count,
            "actual_count": actual_count,
            "failed_indices": failed_indices or [],
        })
        self.expected_count = expected_count
        self.actual_count = actual_count
        self.failed_indices = failed_indices or []


class DataMappingError(DataInsertionError):
    def __init__(self, message: str, mapping_errors: list = None):
        super().__init__(message, "MAPPING", {"mapping_errors": mapping_errors or []})
        self.mapping_errors = mapping_errors or []


logger = logging.getLogger(__name__)


class BaseServeRepository(BaseRepository):
    """Base repository for serve entities with Motor async operations."""

    def __init__(self, mongodb_uri: str, database_name: str,
                 serve_collection: str, historical_collection: str):
        super().__init__(collection_name=serve_collection, mongodb_uri=mongodb_uri, database_name=database_name)
        self.serve_collection: AsyncIOMotorCollection = self.db[serve_collection]
        self.historical_collection: AsyncIOMotorCollection = self.db[historical_collection]

    async def insert_serve(self, entity: Any) -> str:
        try:
            entity_dict = entity.model_dump(by_alias=True)
            result = await self.serve_collection.insert_one(entity_dict)
            if not result.inserted_id:
                raise DataInsertionError("Failed to insert serve record - no ID returned")
            logger.info("Inserted serve record with ID: %s", result.inserted_id)
            return str(result.inserted_id)
        except Exception as e:
            error_msg = f"Error inserting serve record: {str(e)}"
            logger.error(error_msg, exc_info=True)
            if isinstance(e, DataInsertionError):
                raise
            raise DataInsertionError(error_msg) from e

    async def insert_historical(self, entity: Any) -> str:
        try:
            entity_dict = entity.model_dump(by_alias=True)
            result = await self.historical_collection.insert_one(entity_dict)
            logger.info("Inserted historical record with ID: %s", result.inserted_id)
            return str(result.inserted_id)
        except Exception as e:
            logger.error("Error inserting historical record: %s", str(e))
            raise

    async def bulk_insert_serve(self, entities: List[Any]) -> int:
        try:
            if not entities:
                return 0
            entity_dicts = []
            invalid_entities = []
            for i, entity in enumerate(entities):
                try:
                    if hasattr(entity, "model_dump"):
                        entity_dicts.append(entity.model_dump(by_alias=True))
                    elif isinstance(entity, dict):
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
                raise DataValidationError(
                    "No valid entities to insert",
                    [f"Entity at index {i} is invalid" for i in invalid_entities],
                )
            if invalid_entities:
                logger.warning(f"Skipped {len(invalid_entities)} invalid entities")
            result = await self.serve_collection.insert_many(entity_dicts)
            inserted_count = len(result.inserted_ids)
            if inserted_count != len(entity_dicts):
                raise BulkInsertPartialFailureError(
                    f"Bulk insert partial failure: expected {len(entity_dicts)}, got {inserted_count}",
                    expected_count=len(entity_dicts),
                    actual_count=inserted_count,
                    failed_indices=invalid_entities,
                )
            logger.info("Bulk inserted %d serve records", inserted_count)
            return inserted_count
        except Exception as e:
            error_msg = f"Error bulk inserting serve records: {str(e)}"
            logger.error(error_msg, exc_info=True)
            if isinstance(e, DataInsertionError):
                raise
            raise DataInsertionError(error_msg) from e

    async def bulk_insert_historical(self, entities: List[Any]) -> List[str]:
        try:
            if not entities:
                return []
            entity_dicts = [entity.model_dump(by_alias=True) for entity in entities]
            result = await self.historical_collection.insert_many(entity_dicts)
            inserted_ids = [str(id) for id in result.inserted_ids]
            logger.info("Bulk inserted %d historical records", len(inserted_ids))
            return inserted_ids
        except Exception as e:
            logger.error("Error bulk inserting historical records: %s", str(e))
            raise

    async def delete_serve_by_date(self, scheduled_at: datetime) -> Dict[str, Any]:
        try:
            deleted_records = await self.find_serve_by_date(scheduled_at)
            result = await self.serve_collection.delete_many({"scheduled_at": scheduled_at})
            deleted_count = result.deleted_count
            logger.info("Deleted %d serve records for date: %s", deleted_count, scheduled_at)
            return {"deleted_count": deleted_count, "deleted_records": deleted_records}
        except Exception as e:
            logger.error("Error deleting serve records for date %s: %s", scheduled_at, str(e))
            raise

    async def find_serve_by_date(self, scheduled_at: datetime) -> List[Dict[str, Any]]:
        try:
            cursor = self.serve_collection.find({"scheduled_at": scheduled_at})
            records = await cursor.to_list(length=1000)
            logger.info("Found %d serve records for date: %s", len(records), scheduled_at)
            return records
        except Exception as e:
            logger.error("Error finding serve records for date %s: %s", scheduled_at, str(e))
            raise

    async def find_serve_by_date_range(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        try:
            query = {
                "$or": [
                    {"inserted_at": {"$gte": start_date, "$lt": end_date}},
                    {"created_at": {"$gte": start_date, "$lt": end_date}},
                    {"timestamp": {"$gte": start_date, "$lt": end_date}},
                    {"scheduled_at": {"$gte": start_date, "$lt": end_date}},
                ]
            }
            cursor = self.serve_collection.find(query)
            records = await cursor.to_list(length=1000)
            logger.info("Found %d serve records for date range: %s to %s", len(records), start_date, end_date)
            return records
        except Exception as e:
            logger.error("Error finding serve records for date range %s to %s: %s", start_date, end_date, str(e))
            raise

    async def count_serve(self) -> int:
        try:
            count = await self.serve_collection.count_documents({})
            logger.info("Serve collection count: %d", count)
            return count
        except Exception as e:
            logger.error("Error counting serve records: %s", str(e))
            raise

    async def count_historical(self) -> int:
        try:
            count = await self.historical_collection.count_documents({})
            logger.info("Historical collection count: %d", count)
            return count
        except Exception as e:
            logger.error("Error counting historical records: %s", str(e))
            raise

    async def upsert_serve_record(self, filter_query: Dict[str, Any], record_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            existing_record = await self.serve_collection.find_one(filter_query)
            operation_type = "insert"
            record_id = None
            created_time = None

            if existing_record:
                record_id = existing_record["_id"]
                created_time = existing_record.get("created_at")
                delete_result = await self.serve_collection.delete_one({"_id": record_id})
                if delete_result.deleted_count == 0:
                    logger.error("Failed to delete existing record for upsert")
                    return {"success": False, "error": "Failed to delete existing record", "operation_type": "failed_delete", "record_id": None}
                operation_type = "replace"
                record_data["_id"] = record_id
                if created_time:
                    record_data["created_at"] = created_time
            else:
                record_data = {k: v for k, v in record_data.items() if k != "_id"}

            insert_result = await self.serve_collection.insert_one(record_data)
            final_record_id = record_id if record_id else insert_result.inserted_id

            if not final_record_id:
                return {"success": False, "error": "Failed to insert new record", "operation_type": "failed_insert", "record_id": None}

            logger.info("%s record with ID: %s for query: %s",
                        "Replaced" if operation_type == "replace" else "Inserted",
                        final_record_id, filter_query)
            return {"success": True, "operation_type": operation_type, "record_id": str(final_record_id)}
        except Exception as e:
            logger.error("Error upserting serve record: %s", str(e))
            return {"success": False, "error": str(e), "operation_type": "error", "record_id": None}

    async def bulk_upsert_serve_records(self, records_with_filters: List[Dict[str, Any]]) -> Dict[str, Any]:
        try:
            if not records_with_filters:
                return {"success": True, "inserted_count": 0, "replaced_count": 0, "total_count": 0, "failed_count": 0}
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
                result = await self.upsert_serve_record(filter_query, record_data)
                if result.get("success"):
                    if result.get("operation_type") == "insert":
                        inserted_count += 1
                    elif result.get("operation_type") == "replace":
                        replaced_count += 1
                else:
                    failed_count += 1
                    logger.warning("Failed to upsert record: %s", result.get("error"))
            total_count = inserted_count + replaced_count
            logger.info("Bulk upsert completed: %d inserted, %d replaced, %d failed",
                        inserted_count, replaced_count, failed_count)
            return {
                "success": failed_count == 0,
                "inserted_count": inserted_count,
                "replaced_count": replaced_count,
                "total_count": total_count,
                "failed_count": failed_count,
            }
        except Exception as e:
            logger.error("Error in bulk upsert serve records: %s", str(e))
            return {
                "success": False,
                "error": str(e),
                "inserted_count": 0,
                "replaced_count": 0,
                "total_count": 0,
                "failed_count": len(records_with_filters) if records_with_filters else 0,
            }

    async def bulk_upsert_optimized(self, records: List[Dict[str, Any]], unique_key_fields: List[str]) -> Dict[str, Any]:
        try:
            if not records:
                return {"success": True, "inserted_count": 0, "modified_count": 0, "upserted_count": 0, "total_processed": 0}
            operations = []
            for record in records:
                filter_query = {field: record.get(field) for field in unique_key_fields}
                excluded_fields = {"_id", "created_at", "inserted_at"}
                update_data = {k: v for k, v in record.items() if k not in excluded_fields}
                current_time = datetime.now()
                update_data["updated_at"] = current_time
                created_at_val = record.get("created_at") or current_time
                inserted_at_val = record.get("inserted_at") or current_time
                op = UpdateOne(
                    filter_query,
                    {"$set": update_data, "$setOnInsert": {"created_at": created_at_val, "inserted_at": inserted_at_val}},
                    upsert=True,
                )
                operations.append(op)
            if not operations:
                return {"success": True, "total_processed": 0}
            result = await self.serve_collection.bulk_write(operations, ordered=False)
            return {
                "success": True,
                "inserted_count": result.upserted_count,
                "modified_count": result.modified_count,
                "upserted_count": result.upserted_count,
                "matched_count": result.matched_count,
                "total_processed": len(records),
            }
        except Exception as e:
            logger.error(f"Error in bulk_upsert_optimized: {str(e)}")
            return {"success": False, "error": str(e), "total_processed": 0}
