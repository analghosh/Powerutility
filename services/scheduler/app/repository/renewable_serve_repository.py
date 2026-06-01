"""
Scheduler Renewable Serve Repository
Handles database operations for renewable serve and historical collections
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from libs.database.base_serve_repository import BaseServeRepository
from libs.models.renewable_serve_entity import RenewableServeEntity, RenewableServeHistoricalEntity
from ..core.config import settings

logger = logging.getLogger(__name__)


class RenewableServeRepository(BaseServeRepository):
    """Repository for renewable serve and historical collections"""
    
    def __init__(self):
        """Initialize the repository with MongoDB connection"""
        super().__init__(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE,
            serve_collection=getattr(settings, 'MONGODB_RENEWABLE_SERVE_COLLECTION', 'renewable_serve'),
            historical_collection=getattr(settings, 'MONGODB_RENEWABLE_SERVE_HISTORICAL_COLLECTION', 'renewable_serve_historical')
        )
        self._ensure_time_series_collections()
        self.create_indexes()

    def create_indexes(self):
        """Create unique indexes for the collection.
        Unique key: scheduled_at, plant_id, time_block, type
        """
        try:
            # Drop legacy index that incorrectly included approval_no
            self.serve_collection.drop_index("idx_unique_renewable_key")
        except Exception:
            pass  # Index may not exist yet

        self.serve_collection.create_index(
            [
                ("scheduled_at", 1),
                ("plant_id", 1),
                ("time_block", 1),
                ("type", 1),
            ],
            unique=True,
            name="idx_unique_renewable_key",
            background=True
        )
    

    def _ensure_time_series_collections(self):
        """
        Ensure collections exist.
        Migrate 'renewable_serve' from Time-Series to Normal if needed.
        """
        try:
            serve_collection = getattr(settings, 'MONGODB_RENEWABLE_SERVE_COLLECTION', 'renewable_serve')
            historical_collection = getattr(settings, 'MONGODB_RENEWABLE_SERVE_HISTORICAL_COLLECTION', 'renewable_serve_historical')
            
            # 1. Historical
            if historical_collection not in self.db.list_collection_names():
                self.db.create_collection(
                    historical_collection,
                    timeseries={
                        "timeField": "archived_timestamp",
                        "metaField": "scheduled_at",
                        "granularity": "hours",
                    },
                )
            self.historical_collection = self.db[historical_collection]
            
            # 2. Main (Safe Migration)
            main_coll_name = serve_collection
            # Optimized probe
            existing_colls = list(self.db.list_collections(filter={"name": main_coll_name}))
            existing_coll_info = existing_colls[0] if existing_colls else None
            
            if existing_coll_info:
                # Robust View Detection
                options = existing_coll_info.get("options", {})
                is_view = existing_coll_info.get("type") == "view"
                is_timeseries = "timeseries" in options
                
                if is_timeseries or is_view:
                    logger.warning(f"⚠️ Renewable Collection '{main_coll_name}' is {'VIEW' if is_view else 'Time-Series'}. SAFE MIGRATION...")
                    
                    # 1. Snapshot
                    start_migration_date = datetime.now() - timedelta(days=5)
                    logger.info("Snapshotting data before migration...")
                    
                    snapshot_data = []
                    try:
                        cursor = self.db[main_coll_name].find({"scheduled_at": {"$gte": start_migration_date}})
                        
                        deduped_map = {}
                        for doc in cursor:
                            if "_id" in doc: del doc["_id"]
                            # plant_id, time_block, scheduled_at, type, approval_no
                            key = (
                                doc.get("plant_id"),
                                doc.get("time_block"),
                                doc.get("scheduled_at"),
                                doc.get("type"),
                                doc.get("approval_no")
                            )
                            deduped_map[key] = doc
                        
                        snapshot_data = list(deduped_map.values())
                        logger.info(f"Loaded {len(snapshot_data)} unique records for migration.")
                    except Exception as e:
                        logger.error(f"Error snapshotting data: {e}")
                    
                    # 2. Rename or Drop
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_name = f"{main_coll_name}_ts_backup_{timestamp}"
                    try:
                        self.db[main_coll_name].rename(backup_name)
                    except Exception as e:
                        logger.warning(f"Rename failed (View?). Dropping view: {main_coll_name}. Error: {e}")
                        self.db.drop_collection(main_coll_name)
                    
                    # 3. Create Normal
                    self.db.create_collection(main_coll_name)
                    
                    # 4. Restore
                    if snapshot_data: 
                        batch_size = 1000
                        for i in range(0, len(snapshot_data), batch_size):
                             batch = snapshot_data[i:i+batch_size]
                             self.db[main_coll_name].insert_many(batch)
                        logger.info(f"✅ Renewable Migration Complete. {len(snapshot_data)} records.")
                
            elif not existing_coll_info:
                self.db.create_collection(main_coll_name)
                pass

            self.serve_collection = self.db[main_coll_name]
            
        except Exception as e:
            logger.error("Error creating renewable collections: %s", str(e))

    def create_or_update(self, renewable_data: Dict[str, Any]) -> Optional[str]:
        """
        Create or update renewable serve record
        
        Args:
            renewable_data: Renewable data dictionary
            
        Returns:
            Optional[str]: Document ID if successful, None otherwise
        """
        try:
            # Check if record exists based on scheduled_at, time_block, and plant_id
            query = {
                "scheduled_at": renewable_data.get("scheduled_at"),
                "time_block": renewable_data.get("time_block"),
                "plant_id": renewable_data.get("plant_id")
            }
            
            existing_record = self.find_one(query)
            
            if existing_record:
                # Update existing record
                renewable_data["updated_at"] = datetime.now()
                return self.update_by_id(str(existing_record["_id"]), renewable_data)
            else:
                # Create new record
                renewable_data["created_at"] = datetime.now()
                renewable_data["timestamp"] = datetime.now()
                return self.create(renewable_data)
                
        except Exception as e:
            logger.error("Error creating/updating renewable serve record: %s", str(e))
            return None

    def upsert_by_unique_key(self, renewable_data: Dict[str, Any]) -> Optional[str]:
        """
        Upsert renewable serve record based on unique key combination.
        Uses optimized bulk upsert logic for consistency.
        """
        try:
            # Fix enum
            if "updated_flag" in renewable_data and hasattr(renewable_data["updated_flag"], "value"):
                renewable_data["updated_flag"] = renewable_data["updated_flag"].value
            
            # Unique Key: scheduled_at, plant_id, time_block, type
            unique_key = ["scheduled_at", "plant_id", "time_block", "type"]
            
            # Use bulk helper for single item
            result = self.bulk_upsert_optimized([renewable_data], unique_key)
            
            if result.get("success"):
                return "upserted"
            else:
                return None
                
        except Exception as e:
            logger.error("Error upserting renewable serve record: %s", str(e))
            return None

    def bulk_upsert_renewable_serve(self, entities: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Bulk upsert renewable serve entities using Optimized Bulk Upsert.
        """
        try:
            if not entities:
                return {"inserted_count": 0}

            # Map fields if needed (assuming entities are dicts or have been dumped)
            # If they are Entity objects, caller should convert? 
            # Pattern in other repos converts within method.
            # Assuming input here is List[Dict] based on usage context or adding safety.
            records = []
            for e in entities:
                if hasattr(e, "model_dump"): records.append(e.model_dump(by_alias=True))
                elif hasattr(e, "dict"): records.append(e.dict(by_alias=True))
                else: records.append(e)

            # Fix enums
            for r in records:
                if "updated_flag" in r and hasattr(r["updated_flag"], "value"):
                    r["updated_flag"] = r["updated_flag"].value
            
            unique_key = ["scheduled_at", "plant_id", "time_block", "type"]

            result = self.bulk_upsert_optimized(records, unique_key)

            return {
                "inserted_count": result.get("inserted_count", 0),
                "modified_count": result.get("modified_count", 0),
                "upserted_count": result.get("upserted_count", 0),
                "total_processed": result.get("total_processed", 0)
            }
        except Exception as e:
             logger.error("Error in bulk upsert renewable: %s", str(e))
             raise

    def process_daily_archival_and_cleanup(self) -> Dict[str, Any]:
        """
        End-of-Day Job for Renewable.
        """
        try:
            from datetime import timedelta
            
            today = datetime.now().date()
            yesterday = today - timedelta(days=1)
            cleanup_cutoff = today - timedelta(days=3)
            
            logger.info(f"📦 Starting Renewable archival for {yesterday}")
            
            start_yesterday = datetime.combine(yesterday, datetime.min.time())
            end_yesterday = datetime.combine(yesterday, datetime.max.time())
            
            cursor = self.serve_collection.find({
                "scheduled_at": {"$gte": start_yesterday, "$lte": end_yesterday}
            })
            
            archival_batch = []
            for doc in cursor:
                hist_doc = dict(doc)
                if "_id" in hist_doc: del hist_doc["_id"]
                hist_doc["archived_at"] = datetime.now()
                hist_doc["archived_timestamp"] = datetime.now()
                archival_batch.append(hist_doc)
                
            archived_count = 0
            if archival_batch:
                res = self.bulk_insert_renewable_historical_with_deduplication(archival_batch)
                archived_count = res.get("inserted_count", 0)
            
            logger.info(f"✅ Archived {archived_count} renewable records")
            
            logger.info(f"🧹 Cleaning up renewable older than {cleanup_cutoff}")
            del_res = self.serve_collection.delete_many({
                "scheduled_at": {"$lt": datetime.combine(cleanup_cutoff, datetime.min.time())}
            })
            
            return {
                "archived_count": archived_count,
                "deleted_count": del_res.deleted_count
            }
            
        except Exception as e:
            logger.error("Error in renewable archival: %s", str(e))
            return {"error": str(e)}
    
    def upsert_historical_by_unique_key(self, renewable_data: Dict[str, Any]) -> Optional[str]:
        """
        Upsert historical (robust insert).
        """
        try:
             res = self.bulk_insert_renewable_historical_with_deduplication([renewable_data])
             return "inserted" if res["inserted_count"] > 0 else None
        except Exception as e:
            logger.error("Error upserting renewable historical: %s", str(e))
            return None

    def bulk_insert_renewable_historical_with_deduplication(self, entities: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Bulk insert renewable historical records with robust deduplication.
        Unique Key: plant_id, time_block, scheduled_at, type, approval_no
        """
        try:
            if not entities:
                 return {"inserted_count": 0, "error_count": 0, "duplicates_removed": 0, "errors": []}
            
            logger.info(f"Processing {len(entities)} renewable historical entities for deduplication...")
            
            # Map fields if needed
            inserts = []
            for e in entities:
                if hasattr(e, "model_dump"): inserts.append(e.model_dump(by_alias=True))
                elif hasattr(e, "dict"): inserts.append(e.dict(by_alias=True))
                else: inserts.append(e)
            
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
                projection={"plant_id": 1, "time_block": 1, "scheduled_at": 1, "type": 1, "_id": 0}
            )

            existing_keys = set()
            for doc in existing_cursor:
                k = (
                    str(doc.get("plant_id")),
                    doc.get("time_block"),
                    doc.get("scheduled_at"),
                    doc.get("type"),
                )
                existing_keys.add(k)

            # Filter duplicates
            final_inserts = []
            duplicates_count = 0
            seen_in_batch = set()

            for i in inserts:
                check_key = (
                    str(i.get("plant_id")),
                    i.get("time_block"),
                    i.get("scheduled_at"),
                    i.get("type"),
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
                errors = []
            except BulkWriteError as bwe:
                inserted_count = bwe.details.get('nInserted', 0)
                write_errors = bwe.details.get('writeErrors', [])
                error_count = len(write_errors)
                errors = [str(err.get('errmsg')) for err in write_errors]
                logger.warning(f"Renewable historical insert had {error_count} errors. {inserted_count} records inserted.")
            
            return {
                "total_processed": len(entities),
                "inserted_count": inserted_count,
                "error_count": error_count,
                 "duplicates_removed": duplicates_count,
                 "errors": errors
            }
            
        except Exception as e:
            logger.error("Error in bulk insert historical renewable: %s", str(e))
            return {"inserted_count": 0, "error_count": 1, "errors": [str(e)], "duplicates_removed": 0}


    def create_historical(self, historical_data: Dict[str, Any]) -> Optional[str]:
        """
        Create historical renewable record
        
        Args:
            historical_data: Historical renewable data dictionary
            
        Returns:
            Optional[str]: Document ID if successful, None otherwise
        """
        try:
            # Add archival timestamps
            if "archived_at" not in historical_data:
                historical_data["archived_at"] = datetime.now()
            if "archived_timestamp" not in historical_data:
                historical_data["archived_timestamp"] = datetime.now()
            
            # Use historical collection
            historical_collection = getattr(settings, 'MONGODB_RENEWABLE_SERVE_HISTORICAL_COLLECTION', 'renewable_serve_historical')
            collection = self.db[historical_collection]
            
            # Insert with original _id preserved for duplicate tracking
            result = collection.insert_one(historical_data)
            return str(result.inserted_id)
            
        except Exception as e:
            logger.error("Error creating historical renewable record: %s", str(e))
            return None

    def find_historical_many(self, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Find multiple records in historical collection
        
        Args:
            query: MongoDB query dictionary
            
        Returns:
            List of matching historical records
        """
        try:
            historical_collection = getattr(settings, 'MONGODB_RENEWABLE_SERVE_HISTORICAL_COLLECTION', 'renewable_serve_historical')
            collection = self.db[historical_collection]
            
            cursor = collection.find(query)
            try:
                return list(cursor)
            finally:
                cursor.close()
            
        except Exception as e:
            logger.error("Error finding historical renewable records: %s", str(e))
            return []

    def find_historical_one(self, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Find single record in historical collection by unique key combination
        
        Args:
            query: MongoDB query dictionary (should contain unique key fields)
            
        Returns:
            Matching historical record or None if not found
        """
        try:
            historical_collection = getattr(settings, 'MONGODB_RENEWABLE_SERVE_HISTORICAL_COLLECTION', 'renewable_serve_historical')
            collection = self.db[historical_collection]
            
            # Find the most recent record with this unique key combination
            record = collection.find_one(query, sort=[("archived_at", -1)])
            
            if record:
                logger.debug("Found existing historical record for unique key: %s", query)
            
            return record
            
        except Exception as e:
            logger.error("Error finding historical renewable record by unique key: %s", str(e))
            return None

    
    def find_by_unique_keys(self, keys: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Find multiple records by unique keys using Optimized Range Query.
        Instead of massive $or query, we:
        1. Find min/max scheduled_at
        2. Find set of plant_ids
        3. Query range + $in plants
        4. Filter exactly in memory (for time_block, type etc)
        """
        if not keys:
            return []
            
        try:
            # 1. Inspect Keys
            # Gather Bounds
            min_date = None
            max_date = None
            plant_ids = set()
            types = set()
            
            for k in keys:
                sa = k.get("scheduled_at")
                if sa:
                    if min_date is None or sa < min_date: min_date = sa
                    if max_date is None or sa > max_date: max_date = sa
                
                pid = k.get("plant_id")
                if pid: plant_ids.add(pid)
                
                typ = k.get("type")
                if typ: types.add(typ)
            
            if not min_date or not max_date:
                logger.warning("No scheduled_at found in keys for lookup")
                return []
            
            # 2. Construct Optimized Query
            # We want records roughly in this time range for these plants
            query = {
                "scheduled_at": {"$gte": min_date, "$lte": max_date}
            }
            if plant_ids:
                query["plant_id"] = {"$in": list(plant_ids)}
            if types:
                query["type"] = {"$in": list(types)}
                
            # 3. Fetch Candidates
            # This might fetch slightly more data than exact keys (e.g. other time blocks), 
            # but is much faster/safer than 1000 $or clauses.
            cursor = self.serve_collection.find(query)
            candidates = []
            try:
                candidates = list(cursor)
            finally:
                cursor.close()
                
            # 4. Filter Exact Matches In-Memory
            # Build a set of key signatures from input 'keys' for fast checking
            target_signatures = set()
            for k in keys:
                # signature: (scheduled_at_iso, time_block, type, plant_id)
                # handle datetime comparison carefully
                sa = k.get("scheduled_at")
                if isinstance(sa, datetime):
                     # ensure consistent comparison. MongoDB returns datetime usually.
                     pass 
                
                sig = (
                    sa,
                    k.get("time_block"),
                    k.get("type"),
                    k.get("plant_id")
                )
                target_signatures.add(sig)
                
            final_results = []
            for candidate in candidates:
                cand_sig = (
                    candidate.get("scheduled_at"),
                    candidate.get("time_block"),
                    candidate.get("type"),
                    candidate.get("plant_id")
                )
                if cand_sig in target_signatures:
                    final_results.append(candidate)
                    
            logger.info(f"Optimized find_by_unique_keys: fetched {len(candidates)} candidates, matched {len(final_results)} exact records.")
            return final_results

        except Exception as e:
            logger.error(f"Error in optimized find_by_unique_keys: {e}")
            return []

    def bulk_upsert_custom(self, ops_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Bulk upsert specially for Time Series collection (Delete + Insert strategy).
        
        Args:
            ops_data: List of dicts, each containing:
                      - 'delete_id': Optional[str/ObjectId] - if present, delete this doc
                      - 'insert_doc': Dict - document to insert
        """
        if not ops_data:
            return {"success": True}
            
        from pymongo import InsertOne, DeleteOne
        bulk_ops = []
        
        inserted_count = 0
        deleted_count = 0
        
        for item in ops_data:
            if item.get("delete_id"):
                bulk_ops.append(DeleteOne({"_id": item["delete_id"]}))
                deleted_count += 1
            
            if item.get("insert_doc"):
                bulk_ops.append(InsertOne(item["insert_doc"]))
                inserted_count += 1
        
        if not bulk_ops:
            return {"success": True}
            
        try:
             # ordered=True ensures deletes happen before inserts? 
             # Actually ordered=False is fine as long as delete_id refers to old doc.
             # But if we are re-inserting same key, we generally want delete to finish first?
             # No, _id is unique. Key collision in Time Series is allowed? 
             # Time Series "metaField" + "timeField" is NOT a unique constraint by default.
             # So we can have duplicates.
             # Thus, we theoretically risk duplicates if delete fails and insert succeeds.
             # But typically we want ordered=True to be safe? 
             # No, ordered=True stops on error. ordered=False tries all.
             # Let's use ordered=False for performance, duplicates are handled by app logic usually.
             result = self.serve_collection.bulk_write(bulk_ops, ordered=False)
             
             return {
                 "success": True,
                 "inserted_count": inserted_count, # Approx (actual result.inserted_count)
                 "deleted_count": deleted_count,
                 "bulk_api_inserted": result.inserted_count,
                 "bulk_api_deleted": result.deleted_count
             }
        except Exception as e:
            logger.error(f"Error in bulk_upsert_custom: {e}")
            return {"success": False, "error": str(e)}

    def close(self):
        """
        Close database connections and cleanup resources.
        Delegates to parent class close method.
        """
        try:
            super().close()
            logger.debug("Renewable serve repository connections closed")
        except Exception as e:
            logger.warning(f"Error closing renewable serve repository connections: {str(e)}")


# Create singleton instance
renewable_serve_repository = RenewableServeRepository()