"""
Scheduler Demand Serve Repository
Handles database operations for demand serve and historical collections
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pymongo.errors import DuplicateKeyError

from libs.database.base_serve_repository import BaseServeRepository
from libs.models import DemandServeEntity, DemandServeHistoricalEntity
from ..core.config import settings

logger = logging.getLogger(__name__)


class DemandServeRepository(BaseServeRepository):
    """Repository for demand serve and historical collections"""
    
    def __init__(self):
        """Initialize the repository with MongoDB connection"""
        super().__init__(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE,
            serve_collection=settings.MONGODB_DEMAND_SERVE_COLLECTION,
            historical_collection=settings.MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION
        )
        # Ensure time-series collections are created
        self._ensure_time_series_collections()
        self.create_indexes()

    def create_indexes(self):
        """Create unique indexes for the collection"""
        self.serve_collection.create_index(
            [("type", 1), ("scheduled_at", 1), ("time_block", 1)],
            unique=True,
            name="idx_unique_demand_key",
            background=True
        )
        # Note: Time-series collections don't support unique indexes
        # We handle duplicates at the application level
    
    def _ensure_time_series_collections(self):
        """
        Ensure collections exist.
        Migrate 'demand_serve' from Time-Series to Normal Collection if needed.
        Keep 'demand_serve_historical' as Time-Series.
        """
        try:
            # 1. Historical Collection (Keep as Time-Series)
            if settings.MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION not in self.db.list_collection_names():
                self.db.create_collection(
                    settings.MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION,
                    timeseries={
                        "timeField": "archived_timestamp",
                        "metaField": "schedule_date",
                        "granularity": "hours",
                    },
                )
                logger.info("Created time-series collection: %s", settings.MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION)

            self.historical_collection = self.db[settings.MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION]
            
            # 2. Main Collection (Ensure Normal + Safe Migration)
            main_coll_name = settings.MONGODB_DEMAND_SERVE_COLLECTION
            
            # collections = self.db.list_collections(namesOnly=False) # namesOnly=False not supported on some versions
            # existing_coll_info = next((c for c in collections if c["name"] == main_coll_name), None)
            
            # Optimized probe
            existing_colls = list(self.db.list_collections(filter={"name": main_coll_name}))
            existing_coll_info = existing_colls[0] if existing_colls else None
            
            if existing_coll_info:
                # Robust View Detection
                is_view_type = existing_coll_info.get("type") == "view"
                options = existing_coll_info.get("options", {})
                is_view_options = "viewOn" in options or "pipeline" in options
                is_view = is_view_type or is_view_options
                
                is_timeseries = "timeseries" in options
                
                if is_view or is_timeseries:
                    logger.warning(f"⚠️ Collection '{main_coll_name}' is {'VIEW' if is_view else 'Time-Series'}. Starting SAFE MIGRATION...")
                    
                    # Migration Logic - Snapshot Data
                    start_migration_date = datetime.now() - timedelta(days=5)
                    logger.info("Snapshotting data before migration...")
                    
                    snapshot_data = []
                    try:
                        cursor = self.db[main_coll_name].find({"scheduled_at": {"$gte": start_migration_date}})
                        
                        # Deduplicate in memory
                        deduped_map = {}
                        for doc in cursor:
                             if "_id" in doc: del doc["_id"]
                             # Unique Key: type, scheduled_at, time_block
                             key = (doc.get("type"), doc.get("scheduled_at"), doc.get("time_block"))
                             deduped_map[key] = doc
                        
                        snapshot_data = list(deduped_map.values())
                        logger.info(f"Loaded {len(snapshot_data)} unique records for migration.")
                    except Exception as e:
                        logger.error(f"Error snapshotting data: {e}")
                        # If snapshot fails, we might be in trouble, but proceed to try fix structure.

                    # Attempt Rename (Safe default for TS) -> Fallback to Drop (for View)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_name = f"{main_coll_name}_ts_backup_{timestamp}"
                    
                    try:
                        # Try rename first
                        self.db[main_coll_name].rename(backup_name)
                        logger.info(f"Renamed Old Entity to: {backup_name}")
                    except Exception as e:
                        # Check for specific mongo error 166: CommandNotSupportedOnView
                        error_msg = str(e)
                        if "CommandNotSupportedOnView" in error_msg or "cannot rename view" in error_msg or (hasattr(e, 'code') and e.code == 166):
                            logger.warning(f"Rename failed because entity is a VIEW. Dropping view: {main_coll_name}")
                            self.db.drop_collection(main_coll_name)
                            logger.info(f"Dropped View: {main_coll_name}")
                        else:
                            # Re-raise other errors
                            raise e

                    # Create Normal Collection
                    self.db.create_collection(main_coll_name)
                    logger.info(f"Created Normal Collection: {main_coll_name}")

                    # Restore Data
                    if snapshot_data:
                        batch_size = 1000
                        for i in range(0, len(snapshot_data), batch_size):
                             batch = snapshot_data[i:i+batch_size]
                             self.db[main_coll_name].insert_many(batch)
                        logger.info(f"✅ Migration Complete. Restored {len(snapshot_data)} records.")
                    else:
                        logger.info("✅ Migration Complete. No records to restore.")
                        
            elif not existing_coll_info:
                self.db.create_collection(main_coll_name)
                # Create Index immediately for new collection
                self.db[main_coll_name].create_index(
                    [("type", 1), ("scheduled_at", 1), ("time_block", 1)],
                    unique=True,
                    name="idx_unique_demand_key"
                )
            
            self.serve_collection = self.db[main_coll_name]
            
        except Exception as e:
            logger.error("Error ensuring demand collections: %s", str(e))

    
    def find_by_unique_key(self, scheduled_at: datetime, time_block: Optional[int], type: str) -> Optional[Dict[str, Any]]:
        """
        Find demand serve record by unique key (scheduled_at, time_block, type)
        For time-series collections, we find the most recent record with this key
        
        Args:
            scheduled_at: Schedule date and time
            time_block: Time block identifier
            type: Market type (IDF/DAF)
            
        Returns:
            Dict[str, Any] or None if not found
        """
        try:
            filter_criteria = {
                "scheduled_at": scheduled_at,
                "type": type
            }
            
            # Only add time_block if it's not None
            if time_block is not None:
                filter_criteria["time_block"] = time_block
            
            # For time-series, find the most recent record (by inserted_at desc)
            record = self.serve_collection.find_one(
                filter_criteria,
                sort=[("inserted_at", -1)]  # Most recent first
            )
            
            if record:
                logger.debug("Found existing demand record for key: %s", filter_criteria)
            return record
        except Exception as e:
            logger.error("Error finding demand record by unique key: %s", str(e))
            return None
    
    def delete_duplicates_by_unique_key(self, scheduled_at: datetime, time_block: Optional[int], type: str) -> int:
        """
        Delete duplicate records for a unique key, keeping only the most recent one
        This is necessary for time-series collections since we can't use unique indexes
        
        Args:
            scheduled_at: Schedule date and time
            time_block: Time block identifier
            type: Market type (IDF/DAF)
            
        Returns:
            int: Number of duplicate records deleted
        """
        try:
            filter_criteria = {
                "scheduled_at": scheduled_at,
                "type": type
            }
            
            # Only add time_block if it's not None
            if time_block is not None:
                filter_criteria["time_block"] = time_block
            
            # Find all records with this unique key
            cursor = self.serve_collection.find(filter_criteria).sort("inserted_at", -1)
            try:
                all_records = list(cursor)
            finally:
                cursor.close()
            
            if len(all_records) <= 1:
                return 0  # No duplicates
            
            # Keep the most recent record (first in sorted list)
            keep_record_id = all_records[0]["_id"]
            
            # Delete all others
            delete_filter = {
                **filter_criteria,
                "_id": {"$ne": keep_record_id}
            }
            
            result = self.serve_collection.delete_many(delete_filter)
            deleted_count = result.deleted_count
            
            if deleted_count > 0:
                logger.info(f"Deleted {deleted_count} duplicate records for key: {filter_criteria}")
            
            return deleted_count
            
        except Exception as e:
            logger.error("Error deleting duplicate records for key %s: %s", filter_criteria, str(e))
            return 0
    
    def delete_all_by_unique_key(self, scheduled_at: datetime, time_block: Optional[int], type: str) -> int:
        """
        Delete ALL records for a unique key (used when replacing records)
        
        Args:
            scheduled_at: Schedule date and time
            time_block: Time block identifier
            type: Market type (IDF/DAF)
            
        Returns:
            int: Number of records deleted
        """
        try:
            filter_criteria = {
                "scheduled_at": scheduled_at,
                "type": type
            }
            
            # Only add time_block if it's not None
            if time_block is not None:
                filter_criteria["time_block"] = time_block
            
            # Delete all records with this unique key
            result = self.serve_collection.delete_many(filter_criteria)
            deleted_count = result.deleted_count
            
            if deleted_count > 0:
                logger.info(f"Deleted {deleted_count} records for key: {filter_criteria}")
            
            return deleted_count
            
        except Exception as e:
            logger.error("Error deleting records for key %s: %s", filter_criteria, str(e))
            return 0
    
    def bulk_insert_demand_serve_with_deduplication(self, entities: List[DemandServeEntity]) -> Dict[str, Any]:
        """
        Bulk insert demand serve records with deduplication for time-series collections.
        Optimized to use bulk_write (Delete batch + Insert batch).
        
        Args:
            entities: List of demand serve entities to insert
            
        Returns:
            Dict[str, Any]: Result containing operation details
        """
        try:
            if not entities:
                logger.info("No entities to insert")
                return {
                    "total_processed": 0,
                    "inserted_count": 0,
                    "duplicates_removed": 0,
                    "error_count": 0,
                    "errors": []
                }
            
            # Group entities by unique key for deduplication
            unique_keys = {}
            for entity in entities:
                key = (entity.scheduled_at, entity.time_block, entity.type)
                if key not in unique_keys:
                    unique_keys[key] = []
                unique_keys[key].append(entity)
            
            logger.info(f"Processing {len(entities)} entities with {len(unique_keys)} unique keys")
            
            deletes = []
            inserts = []
            
            # Process each unique key
            for (scheduled_at, time_block, type_val), entity_list in unique_keys.items():
                try:
                    # Sort entities by inserted_at (most recent first)
                    entity_list.sort(key=lambda x: x.inserted_at, reverse=True)
                    
                    # Keep only the most recent entity for this unique key
                    entity_to_insert = entity_list[0]
                    
                    # 1. Add Delete Filter
                    filter_criteria = {
                        "scheduled_at": scheduled_at,
                        "type": type_val
                    }
                    if time_block is not None:
                        filter_criteria["time_block"] = time_block
                        
                    deletes.append(filter_criteria)
                    
                    # 2. Add Insert Document
                    entity_dict = entity_to_insert.model_dump(by_alias=True)
                    inserts.append(entity_dict)
                    
                except Exception as key_error:
                    logger.error(f"Error preparing key {scheduled_at}, {time_block}: {key_error}")
            
            # Execute Bulk Optimized Operation
            result = self.bulk_delete_then_insert_optimized(deletes, inserts)
            
            return {
                "total_processed": result.get("total", 0),
                "inserted_count": result.get("inserted", 0),
                "duplicates_removed": result.get("deleted", 0),
                "error_count": 0,
                "errors": []
            }
            
        except Exception as e:
            logger.error("Error in bulk insert with deduplication: %s", str(e))
            raise

    def insert_demand_serve(self, demand_serve: DemandServeEntity) -> str:
        """
        Insert a new demand serve record
        
        Args:
            demand_serve: Demand serve entity to insert
            
        Returns:
            str: ID of the inserted record
        """
        return self.insert_serve(demand_serve)
    
    def insert_demand_historical(self, demand_historical: DemandServeHistoricalEntity) -> str:
        """
        Insert a new demand historical record
        
        Args:
            demand_historical: Demand historical entity to insert
            
        Returns:
            str: ID of the inserted record
        """
        return self.insert_historical(demand_historical)
    
    def delete_demand_serve_by_date(self, scheduled_at: datetime) -> Dict[str, Any]:
        """
        Delete demand serve records by scheduled_at date
        
        Args:
            scheduled_at: Schedule date to delete records for
            
        Returns:
            Dict[str, Any]: Dictionary containing the number of records deleted and the deleted records
        """
        return self.delete_serve_by_date(scheduled_at)
    
    def find_demand_serve_by_date(self, scheduled_at: datetime) -> List[Dict[str, Any]]:
        """
        Find demand serve records by scheduled_at date
        
        Args:
            scheduled_at: Schedule date to find records for
            
        Returns:
            List of records found
        """
        return self.find_serve_by_date(scheduled_at)
    
    def find_demand_serve_by_date_range(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """
        Find demand serve records by date range
        
        Args:
            start_date: Start date
            end_date: End date
            
        Returns:
            List of records found
        """
        return self.find_serve_by_date_range(start_date, end_date)
    
    def get_forcasted_demand(self, scheduled_at: datetime) -> List[Dict[str, Any]]:
        """
        Get forecasted demand serve record by unique key
        
        Args:
            scheduled_at: Schedule date and time
            time_block: Time block identifier
            
        Returns:
            Dict[str, Any] or None if not found
        """
        # Fix: Use scheduled_at + 1 day to fetch exactly 24 hours of data
        tomorrow_date = scheduled_at + timedelta(days=1)
        query = {
            "scheduled_at": {"$gte": scheduled_at, "$lt": tomorrow_date}, "type":"IDF"}
        
        cursor = self.serve_collection.find(query, sort=[("scheduled_at", 1), ("time_block", 1)])
        try:
            return list(cursor)
        finally:
            cursor.close()

    def get_actual_demand(self, scheduled_at: datetime) -> List[Dict[str, Any]]:
        """
        Get actual demand serve record by unique key
        
        Args:
            scheduled_at: Schedule date and time
            
        Returns:
            List[Dict[str, Any]] or None if not found
        """
        # Actual demand is stored in IDF records as comp_act_demand
        tomorrow_date = scheduled_at + timedelta(days=1)
        query = {
            "scheduled_at": {"$gte": scheduled_at, "$lt": tomorrow_date}, "type":"IDF"}
        
        cursor = self.serve_collection.find(query, sort=[("scheduled_at", 1), ("time_block", 1)])
        try:
            return list(cursor)
        finally:
            cursor.close()
    
    def archive_old_demand_data(self, days_to_keep: int = 2) -> Dict[str, Any]:
        """
        Archive old demand data to historical collection and cleanup serve collection.
        Retention Policy:
            - Serve Collection: Keeps data >= (Today - days_to_keep)
            - Historical Collection: Keeps data < Today
            - Overlap: (Today - days_to_keep) to (Today - 1) exists in BOTH.
        
        Args:
            days_to_keep: Number of past days to keep in serve collection (default 2)
            
        Returns:
            Dict containing archival results
        """
        try:
            today = datetime.now().date()
            # We archive everything strictly older than today (Yesterday and before)
            archive_cutoff = today
            # We delete everything strictly older than (Today - days_to_keep)
            delete_cutoff = today - timedelta(days=days_to_keep)
            
            logger.info(f"📦 Starting Demand archival. Archive < {archive_cutoff}, Delete < {delete_cutoff}")
            
            # 1. Find records to archive (Yesterday and older)
            start_time = datetime.now()
            
            cursor = self.serve_collection.find({
                "scheduled_at": {"$lt": datetime.combine(archive_cutoff, datetime.min.time())}
            })
            
            archival_candidates = list(cursor)
            
            if not archival_candidates:
                logger.info("No demand records found for archival")
                return {
                    "archived_count": 0,
                    "deleted_count": 0,
                    "message": "No records to archive"
                }

            logger.info(f"Found {len(archival_candidates)} demand records to potentially archive")

            # 2. Prepare for historical insertion with Deduplication
            historical_inserts = []
            
            if archival_candidates:
                min_date = min(d["scheduled_at"] for d in archival_candidates)
                max_date = max(d["scheduled_at"] for d in archival_candidates)
                
                # Fetch existing historical records in this range to dedup in memory
                existing_hist_cursor = self.historical_collection.find({
                    "scheduled_at": {"$gte": min_date, "$lte": max_date}
                }, {"scheduled_at": 1, "time_block": 1, "type": 1, "_id": 0})
                
                existing_keys = set()
                for doc in existing_hist_cursor:
                    key = (
                        doc.get("scheduled_at"),
                        doc.get("time_block"),
                        doc.get("type")
                    )
                    existing_keys.add(key)
                
                for doc in archival_candidates:
                    key = (
                        doc.get("scheduled_at"),
                        doc.get("time_block"),
                        doc.get("type")
                    )
                    
                    if key not in existing_keys:
                        hist_doc = dict(doc)
                        if "_id" in hist_doc: del hist_doc["_id"]
                        
                        hist_doc["archived_at"] = datetime.now()
                        hist_doc["archived_timestamp"] = datetime.now()
                        
                        historical_inserts.append(hist_doc)
                        existing_keys.add(key)
            
            archived_count = 0
            if historical_inserts:
                try:
                    res = self.historical_collection.insert_many(historical_inserts, ordered=False)
                    archived_count = len(res.inserted_ids)
                except Exception as e:
                    logger.error(f"Bulk insert error in demand archival: {e}")
                    if hasattr(e, 'details'):
                        archived_count = e.details.get('nInserted', 0)
            
            logger.info(f"✅ Archived {archived_count} new demand records to historical")
            
            # 3. Cleanup old records from Serve
            delete_query = {
                "scheduled_at": {"$lt": datetime.combine(delete_cutoff, datetime.min.time())}
            }
            
            del_res = self.serve_collection.delete_many(delete_query)
            deleted_count = del_res.deleted_count
            
            logger.info(f"🧹 Cleaned up {deleted_count} demand records older than {delete_cutoff}")
            
            return {
                "status": "success",
                "archived_count": archived_count,
                "deleted_count": deleted_count,
                "cutoff_date": delete_cutoff.isoformat(),
                "execution_time": (datetime.now() - start_time).total_seconds()
            }
            
        except Exception as e:
            logger.error("Error in demand archival: %s", str(e))
            return {"error": str(e), "status": "error"}

    def count_demand_serve(self) -> int:
        """
        Count total demand serve records
        
        Returns:
            int: Total number of records
        """
        return self.count_serve()
    
    def count_demand_historical(self) -> int:
        """
        Count total demand historical records
        
        Returns:
            int: Total number of records
        """
        return self.count_historical()

    def bulk_insert_demand_serve(self, entities: List[DemandServeEntity]) -> int:
        """
        Bulk insert demand serve records
        
        Args:
            entities: List of demand serve entities to insert
            
        Returns:
            int: Number of inserted records
        """
        return self.bulk_insert_serve(entities)

    def bulk_insert_demand_historical(self, entities: List[DemandServeHistoricalEntity]) -> List[str]:
        """
        Bulk insert demand historical records
        
        Args:
            entities: List of demand historical entities to insert
            
        Returns:
            List of inserted record IDs
        """
        return self.bulk_insert_historical(entities)

    def delete_by_id(self, record_id: str) -> bool:
        """
        Delete demand serve record by ID
        
        Args:
            record_id: MongoDB ObjectId as string
            
        Returns:
            bool: True if deletion was successful
        """
        try:
            from bson import ObjectId
            result = self.serve_collection.delete_one({"_id": ObjectId(record_id)})
            success = result.deleted_count > 0
            if success:
                logger.debug("Deleted demand record with ID: %s", record_id)
            else:
                logger.warning("No demand record found with ID: %s", record_id)
            return success
        except Exception as e:
            logger.error("Error deleting demand record by ID %s: %s", record_id, str(e))
            return False

    def delete_insert_demand_serve(self, filter_criteria: Dict[str, Any], entities: List[DemandServeEntity]) -> Dict[str, Any]:
        """
        Delete existing demand serve records by filter criteria and insert new ones
        This method implements a delete-insert pattern suitable for time-series data
        
        Args:
            filter_criteria: MongoDB query criteria for deletion (can include multiple filters)
                          Examples:
                          - {"scheduled_at": datetime(2025, 1, 15)}  # Single date
                          - {"scheduled_at": {"$gte": start_date, "$lt": end_date}}  # Date range
                          - {"time_block": 1, "type": "IDF"}  # Multiple criteria
                          - {"zone": "North", "scheduled_at": {"$gte": start_date}}  # Mixed criteria
            entities: List of new demand serve entities to insert
            
        Returns:
            Dict[str, Any]: Dictionary containing operation results
        """
        try:
            # First, find existing records matching criteria
            cursor = self.serve_collection.find(filter_criteria)
            try:
                existing_records = list(cursor)
            finally:
                cursor.close()
            
            # Delete existing records matching criteria
            delete_result = self.serve_collection.delete_many(filter_criteria)
            deleted_count = delete_result.deleted_count
            
            logger.info(f"Deleted {deleted_count} existing demand serve records matching criteria: {filter_criteria}")
            
            # Insert new entities using bulk upsert (which will handle unique constraints)
            processed_count = 0
            if entities:
                processed_count = self.bulk_insert_demand_serve(entities)
                logger.info(f"Processed {processed_count} demand serve records (upserted/updated)")
            
            return {
                "deleted_count": deleted_count,
                "processed_count": processed_count,
                "deleted_records": existing_records,
                "filter_criteria": filter_criteria
            }
            
        except Exception as e:
            logger.error(f"Error in delete-insert operation with criteria {filter_criteria}: {str(e)}")
            raise

    def bulk_delete_insert_custom(self, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Execute bulk delete and insert operations efficiently.
        
        Args:
            operations: List of dicts, each containing:
                - filter: keys to match for deletion
                - document: data to insert
                
        Returns:
            Dict with counts of deleted and inserted records
        """
        if not operations:
            return {"deleted_count": 0, "inserted_count": 0}

        try:
            from pymongo import DeleteOne, InsertOne, WriteConcern

            bulk_ops = []
            for op in operations:
                # Add DeleteOne operation
                if op.get("filter"):
                    bulk_ops.append(DeleteOne(op["filter"]))
                
                # Add InsertOne operation
                if op.get("document"):
                    # Ensure document is a dict, not an Entity object
                    doc = op["document"]
                    if hasattr(doc, "dict"):
                        doc = doc.dict(by_alias=True)
                    elif hasattr(doc, "model_dump"):
                        doc = doc.model_dump(by_alias=True)
                    
                    bulk_ops.append(InsertOne(doc))

            if not bulk_ops:
                return {"deleted_count": 0, "inserted_count": 0}

            # Execute bulk write
            # Use ordered=False to continue processing even if some fail (though duplicates shouldn't be an issue with DeleteOne first)
            result = self.serve_collection.bulk_write(bulk_ops, ordered=True)

            return {
                "deleted_count": result.deleted_count,
                "inserted_count": result.inserted_count,
                "modified_count": result.modified_count
            }

        except Exception as e:
            logger.error(f"Error in bulk_delete_insert_custom: {str(e)}")
            raise

    def bulk_insert_demand_serve_with_deduplication(self, entities: List[DemandServeEntity]) -> Dict[str, Any]:
        """
        Bulk insert demand serve entities using Optimized Bulk Upsert.
        """
        try:
            if not entities:
                return {"inserted_count": 0}

            records = [e.model_dump(by_alias=True) for e in entities]
            
            # Unique Key: type, scheduled_at, time_block
            unique_key = ["type", "scheduled_at", "time_block"]
            
            result = self.bulk_upsert_optimized(records, unique_key)
            
            return {
                "inserted_count": result.get("inserted_count", 0),
                "modified_count": result.get("modified_count", 0),
                "upserted_count": result.get("upserted_count", 0),
                "total_processed": result.get("total_processed", 0)
            }
            
        except Exception as e:
            logger.error("Error in bulk insert demand: %s", str(e))
            raise

    def bulk_insert_demand_historical_with_deduplication(self, entities: List[DemandServeHistoricalEntity]) -> Dict[str, Any]:
        """
        Bulk insert demand historical records with robust deduplication.
        Unique Key: type, scheduled_at, time_block
        """
        try:
            if not entities:
                return {"inserted_count": 0, "error_count": 0, "duplicates_removed": 0}

            logger.info(f"Processing {len(entities)} demand historical entities for deduplication...")
            
            # Convert to dicts
            inserts = [e.model_dump(by_alias=True) for e in entities]
            
            # Gather keys to check for existing records
            dates = [i.get("scheduled_at") for i in inserts if i.get("scheduled_at")]
            if not dates:
                return {"inserted_count": 0, "error_count": 0, "duplicates_removed": 0}
            
            min_date = min(dates)
            max_date = max(dates)
            
            # Fetch existing keys in this range
            existing_cursor = self.historical_collection.find(
                {
                    "scheduled_at": {"$gte": min_date, "$lte": max_date}
                },
                projection={"scheduled_at": 1, "time_block": 1, "type": 1, "_id": 0}
            )
            
            existing_keys = set()
            for doc in existing_cursor:
                k = (
                    doc.get("scheduled_at"),
                    doc.get("time_block"),
                    doc.get("type")
                )
                existing_keys.add(k)
            
            # Filter duplicates
            final_inserts = []
            duplicates_count = 0
            seen_in_batch = set()
            
            for i in inserts:
                check_key = (
                    i.get("scheduled_at"),
                    i.get("time_block"),
                    i.get("type")
                )
                
                if check_key in existing_keys or check_key in seen_in_batch:
                    duplicates_count += 1
                    continue
                
                final_inserts.append(i)
                seen_in_batch.add(check_key)
            
            if not final_inserts:
                return {
                    "total_processed": len(entities),
                    "inserted_count": 0, 
                    "error_count": 0, 
                    "duplicates_removed": duplicates_count
                }

            from pymongo import InsertOne
            from pymongo.errors import BulkWriteError
            
            operations = [InsertOne(i) for i in final_inserts]
            
            try:
                result = self.historical_collection.bulk_write(operations, ordered=False)
                inserted_count = result.inserted_count
                error_count = 0
            except BulkWriteError as bwe:
                inserted_count = bwe.details.get('nInserted', 0)
                error_count = len(bwe.details.get('writeErrors', []))
                logger.warning(f"Demand historical insert had {error_count} errors. {inserted_count} records inserted.")

            return {
                "total_processed": len(entities),
                "inserted_count": inserted_count,
                "error_count": error_count,
                "duplicates_removed": duplicates_count,
                "errors": []
            }
        except Exception as e:
            logger.error("Error in bulk insert demand historical with deduplication: %s", str(e))
            return {"inserted_count": 0, "error_count": 1, "errors": [str(e)], "duplicates_removed": 0}
    def get_demand(self, schedule_date_str: str) -> List[Dict[str, Any]]:
        """
        Get demand for a specific date string YYYY-MM-DD.
        Used by RTM Scheduling Processor.
        
        Args:
            schedule_date_str: Date string in YYYY-MM-DD format
            
        Returns:
            List of demand records for that date
        """
        try:
            dt = datetime.strptime(schedule_date_str, "%Y-%m-%d")
            # Create range for the entire day
            start_date = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            
            return self.find_demand_serve_by_date_range(start_date, end_date)
        except Exception as e:
            logger.error(f"Error in get_demand: {str(e)}")
            return []

    async def get_all_async(self, collection_name: str, filter: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Get all records asynchronously matching the filter criteria.
        
        Args:
            collection_name: Name of the collection (unused in this implementation as we use self.serve_collection)
            filter: MongoDB filter criteria
            
        Returns:
            List of matching records
        """
        try:
            # Note: PyMongo is synchronous. For true async, we would need Motor.
            # However, since the codebase seems to be using PyMongo, we'll wrap the sync call.
            # If the caller expects a coroutine, we can use asyncio.to_thread or similar if needed,
            # but here we'll just return the result directly if the caller awaits it (which might fail if not a coroutine).
            # Given the usage 'await self.demand_repository.get_all_async', this method MUST be async.
            
            import asyncio
            
            def _sync_find():
                cursor = self.serve_collection.find(filter)
                try:
                    return list(cursor)
                finally:
                    cursor.close()
            
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _sync_find)
            
        except Exception as e:
            logger.error(f"Error in get_all_async: {str(e)}")
            return []

# Create repository instance
demand_serve_repository = DemandServeRepository()
