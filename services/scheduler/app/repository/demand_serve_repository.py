"""
Scheduler Demand Serve Repository
Handles database operations for demand serve and historical collections
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

from pymongo import ReplaceOne
from pymongo.errors import BulkWriteError

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

    async def create_indexes(self):
        """Create unique indexes for the collection"""
        await self.serve_collection.create_index(
            [("type", 1), ("scheduled_at", 1), ("time_block", 1)],
            unique=True,
            name="idx_unique_demand_key",
            background=True
        )
        # Note: Time-series collections don't support unique indexes
        # We handle duplicates at the application level

    async def _ensure_time_series_collections(self):
        """
        Ensure collections exist.
        Migrate 'demand_serve' from Time-Series to Normal Collection if needed.
        Keep 'demand_serve_historical' as Time-Series.
        """
        try:
            # 1. Historical Collection (Keep as Time-Series)
            existing_names = await self.db.list_collection_names()
            if settings.MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION not in existing_names:
                await self.db.create_collection(
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

            existing_colls = await self.db.list_collections(filter={"name": main_coll_name}).to_list(length=10000)
            existing_coll_info = existing_colls[0] if existing_colls else None

            if existing_coll_info:
                # Robust View Detection
                is_view_type = existing_coll_info.get("type") == "view"
                options = existing_coll_info.get("options", {})
                is_view_options = "viewOn" in options or "pipeline" in options
                is_view = is_view_type or is_view_options

                is_timeseries = "timeseries" in options

                if is_view or is_timeseries:
                    logger.warning(f"Collection '{main_coll_name}' is {'VIEW' if is_view else 'Time-Series'}. Starting SAFE MIGRATION...")

                    # Migration Logic - Snapshot Data
                    start_migration_date = datetime.now() - timedelta(days=5)
                    logger.info("Snapshotting data before migration...")

                    snapshot_data = []
                    try:
                        cursor = self.db[main_coll_name].find({"scheduled_at": {"$gte": start_migration_date}})
                        all_docs = await cursor.to_list(length=10000)

                        # Deduplicate in memory
                        deduped_map = {}
                        for doc in all_docs:
                             if "_id" in doc: del doc["_id"]
                             key = (doc.get("type"), doc.get("scheduled_at"), doc.get("time_block"))
                             deduped_map[key] = doc

                        snapshot_data = list(deduped_map.values())
                        logger.info(f"Loaded {len(snapshot_data)} unique records for migration.")
                    except Exception as e:
                        logger.error(f"Error snapshotting data: {e}")

                    # Attempt Rename (Safe default for TS) -> Fallback to Drop (for View)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_name = f"{main_coll_name}_ts_backup_{timestamp}"

                    try:
                        await self.db[main_coll_name].rename(backup_name)
                        logger.info(f"Renamed Old Entity to: {backup_name}")
                    except Exception as e:
                        error_msg = str(e)
                        if "CommandNotSupportedOnView" in error_msg or "cannot rename view" in error_msg or (hasattr(e, 'code') and e.code == 166):
                            logger.warning(f"Rename failed because entity is a VIEW. Dropping view: {main_coll_name}")
                            await self.db.drop_collection(main_coll_name)
                            logger.info(f"Dropped View: {main_coll_name}")
                        else:
                            raise e

                    # Create Normal Collection
                    await self.db.create_collection(main_coll_name)
                    logger.info(f"Created Normal Collection: {main_coll_name}")

                    # Restore Data
                    if snapshot_data:
                        batch_size = 1000
                        for i in range(0, len(snapshot_data), batch_size):
                             batch = snapshot_data[i:i+batch_size]
                             await self.db[main_coll_name].insert_many(batch)
                        logger.info(f"Migration Complete. Restored {len(snapshot_data)} records.")
                    else:
                        logger.info("Migration Complete. No records to restore.")

            elif not existing_coll_info:
                await self.db.create_collection(main_coll_name)
                # Create Index immediately for new collection
                await self.db[main_coll_name].create_index(
                    [("type", 1), ("scheduled_at", 1), ("time_block", 1)],
                    unique=True,
                    name="idx_unique_demand_key"
                )

            self.serve_collection = self.db[main_coll_name]

        except Exception as e:
            logger.error("Error ensuring demand collections: %s", str(e))


    async def find_by_unique_key(self, scheduled_at: datetime, time_block: Optional[int], type: str) -> Optional[Dict[str, Any]]:
        """
        Find demand serve record by unique key (scheduled_at, time_block, type)
        """
        try:
            filter_criteria = {
                "scheduled_at": scheduled_at,
                "type": type
            }

            if time_block is not None:
                filter_criteria["time_block"] = time_block

            record = await self.serve_collection.find_one(
                filter_criteria,
                sort=[("inserted_at", -1)]
            )

            if record:
                logger.debug("Found existing demand record for key: %s", filter_criteria)
            return record
        except Exception as e:
            logger.error("Error finding demand record by unique key: %s", str(e))
            return None

    async def delete_duplicates_by_unique_key(self, scheduled_at: datetime, time_block: Optional[int], type: str) -> int:
        """
        Delete duplicate records for a unique key, keeping only the most recent one.
        """
        try:
            filter_criteria = {
                "scheduled_at": scheduled_at,
                "type": type
            }

            if time_block is not None:
                filter_criteria["time_block"] = time_block

            cursor = self.serve_collection.find(filter_criteria).sort("inserted_at", -1)
            all_records = await cursor.to_list(length=10000)

            if len(all_records) <= 1:
                return 0  # No duplicates

            # Keep the most recent record (first in sorted list)
            keep_record_id = all_records[0]["_id"]

            # Delete all others
            delete_filter = {
                **filter_criteria,
                "_id": {"$ne": keep_record_id}
            }

            result = await self.serve_collection.delete_many(delete_filter)
            deleted_count = result.deleted_count

            if deleted_count > 0:
                logger.info(f"Deleted {deleted_count} duplicate records for key: {filter_criteria}")

            return deleted_count

        except Exception as e:
            logger.error("Error deleting duplicate records: %s", str(e))
            return 0

    async def delete_all_by_unique_key(self, scheduled_at: datetime, time_block: Optional[int], type: str) -> int:
        """
        Delete ALL records for a unique key (used when replacing records).
        """
        try:
            filter_criteria = {
                "scheduled_at": scheduled_at,
                "type": type
            }

            if time_block is not None:
                filter_criteria["time_block"] = time_block

            result = await self.serve_collection.delete_many(filter_criteria)
            deleted_count = result.deleted_count

            if deleted_count > 0:
                logger.info(f"Deleted {deleted_count} records for key: {filter_criteria}")

            return deleted_count

        except Exception as e:
            logger.error("Error deleting records for key %s: %s", filter_criteria, str(e))
            return 0

    async def bulk_insert_demand_serve_with_deduplication(self, entities: List[DemandServeEntity]) -> Dict[str, Any]:
        """
        Bulk insert demand serve records with deduplication for time-series collections.
        Optimized to use bulk_write (Delete batch + Insert batch).
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

            ops = []

            for (scheduled_at, time_block, type_val), entity_list in unique_keys.items():
                try:
                    entity_list.sort(key=lambda x: x.inserted_at, reverse=True)
                    entity_to_insert = entity_list[0]

                    filter_criteria = {
                        "scheduled_at": scheduled_at,
                        "type": type_val
                    }
                    if time_block is not None:
                        filter_criteria["time_block"] = time_block

                    entity_dict = entity_to_insert.model_dump(by_alias=True)
                    entity_dict.pop("_id", None)
                    now = datetime.now()
                    entity_dict["created_at"] = now
                    entity_dict["updated_at"] = now
                    entity_dict.setdefault("inserted_at", now)
                    ops.append(ReplaceOne(filter_criteria, entity_dict, upsert=True))

                except Exception as key_error:
                    logger.error(f"Error preparing key {scheduled_at}, {time_block}: {key_error}")

            # One replace per key instead of all deletes then all inserts: a row is
            # never absent, and a failure leaves the old row in place. The split
            # form lost every row it had deleted whenever one insert hit the
            # unique index (another job re-creating the key mid-batch) and
            # ordered=True stopped the rest.
            matched, upserted = await self._replace_by_key(ops)

            return {
                "total_processed": matched + matched + upserted,
                "inserted_count": matched + upserted,
                "duplicates_removed": matched,
                "error_count": 0,
                "errors": []
            }

        except Exception as e:
            logger.error("Error in bulk insert with deduplication: %s", str(e))
            raise

    async def _replace_by_key(self, ops: List[ReplaceOne], batch_size: int = 500) -> Tuple[int, int]:
        """
        Run upserting replaces unordered. Two upserts racing for the same missing
        key can both try to insert and one gets E11000; by then the row exists,
        so that op is retried once as a plain replace.
        """
        matched = upserted = 0
        for i in range(0, len(ops), batch_size):
            batch = ops[i:i + batch_size]
            try:
                result = await self.serve_collection.bulk_write(batch, ordered=False)
                matched += result.matched_count
                upserted += result.upserted_count
            except BulkWriteError as bwe:
                details = bwe.details or {}
                matched += details.get("nMatched", 0)
                upserted += details.get("nUpserted", 0)
                write_errors = details.get("writeErrors", [])
                dup_indexes = [e["index"] for e in write_errors if e.get("code") == 11000]
                others = [e for e in write_errors if e.get("code") != 11000]
                if dup_indexes:
                    logger.warning(
                        f"demand_serve: {len(dup_indexes)} upserts raced another writer, retrying"
                    )
                    retry = await self.serve_collection.bulk_write(
                        [batch[j] for j in dup_indexes], ordered=False
                    )
                    matched += retry.matched_count
                    upserted += retry.upserted_count
                if others:
                    raise
        logger.info(f"Bulk replace result for demand_serve: matched={matched} upserted={upserted}")
        return matched, upserted

    async def insert_demand_serve(self, demand_serve: DemandServeEntity) -> str:
        """Insert a new demand serve record"""
        return await self.insert_serve(demand_serve)

    async def insert_demand_historical(self, demand_historical: DemandServeHistoricalEntity) -> str:
        """Insert a new demand historical record"""
        return await self.insert_historical(demand_historical)

    async def delete_demand_serve_by_date(self, scheduled_at: datetime) -> Dict[str, Any]:
        """Delete demand serve records by scheduled_at date"""
        return await self.delete_serve_by_date(scheduled_at)

    async def find_demand_serve_by_date(self, scheduled_at: datetime) -> List[Dict[str, Any]]:
        """Find demand serve records by scheduled_at date"""
        return await self.find_serve_by_date(scheduled_at)

    async def find_demand_serve_by_date_range(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """Find demand serve records by date range"""
        return await self.find_serve_by_date_range(start_date, end_date)

    async def get_forcasted_demand(self, scheduled_at: datetime) -> List[Dict[str, Any]]:
        """Get forecasted demand serve record by unique key"""
        tomorrow_date = scheduled_at + timedelta(days=1)
        query = {
            "scheduled_at": {"$gte": scheduled_at, "$lt": tomorrow_date}, "type": "IDF"}

        cursor = self.serve_collection.find(query).sort([("scheduled_at", 1), ("time_block", 1)])
        return await cursor.to_list(length=10000)

    async def get_actual_demand(self, scheduled_at: datetime) -> List[Dict[str, Any]]:
        """Get actual demand serve record by unique key"""
        tomorrow_date = scheduled_at + timedelta(days=1)
        query = {
            "scheduled_at": {"$gte": scheduled_at, "$lt": tomorrow_date}, "type": "IDF"}

        cursor = self.serve_collection.find(query).sort([("scheduled_at", 1), ("time_block", 1)])
        return await cursor.to_list(length=10000)

    async def archive_old_demand_data(self, days_to_keep: int = 2) -> Dict[str, Any]:
        """
        Archive old demand data to historical collection and cleanup serve collection.

        The archival is idempotent: if a historical record already exists for the same
        (scheduled_at, time_block, type) key but the serve copy has been updated more
        recently, the historical copy is replaced so the historical table remains a true
        mirror of the serve table.
        """
        try:
            today = datetime.now().date()
            archive_cutoff = today
            delete_cutoff = today - timedelta(days=days_to_keep)

            logger.info(f"Starting Demand archival. Archive < {archive_cutoff}, Delete < {delete_cutoff}")

            start_time = datetime.now()

            cursor = self.serve_collection.find({
                "scheduled_at": {"$lt": datetime.combine(archive_cutoff, datetime.min.time())}
            })

            archival_candidates = await cursor.to_list(length=10000)

            if not archival_candidates:
                logger.info("No demand records found for archival")
                return {
                    "archived_count": 0,
                    "deleted_count": 0,
                    "replaced_count": 0,
                    "message": "No records to archive"
                }

            logger.info(f"Found {len(archival_candidates)} demand records to potentially archive")

            historical_inserts = []
            replaced_count = 0

            if archival_candidates:
                min_date = min(d["scheduled_at"] for d in archival_candidates)
                max_date = max(d["scheduled_at"] for d in archival_candidates)

                existing_hist_cursor = self.historical_collection.find({
                    "scheduled_at": {"$gte": min_date, "$lte": max_date}
                }, {"scheduled_at": 1, "time_block": 1, "type": 1, "updated_at": 1, "_id": 1})

                existing_map: Dict[tuple, Dict[str, Any]] = {}
                async for doc in existing_hist_cursor:
                    key = (
                        doc.get("scheduled_at"),
                        doc.get("time_block"),
                        doc.get("type")
                    )
                    existing_map[key] = doc

                keys_to_replace = []

                for doc in archival_candidates:
                    key = (
                        doc.get("scheduled_at"),
                        doc.get("time_block"),
                        doc.get("type")
                    )

                    existing = existing_map.get(key)
                    source_updated_at = doc.get("updated_at") or doc.get("created_at") or datetime.min
                    if isinstance(source_updated_at, str):
                        try:
                            source_updated_at = datetime.fromisoformat(source_updated_at.replace("Z", "+00:00"))
                        except ValueError:
                            source_updated_at = datetime.min

                    if existing:
                        hist_updated_at = existing.get("updated_at") or datetime.min
                        if isinstance(hist_updated_at, str):
                            try:
                                hist_updated_at = datetime.fromisoformat(hist_updated_at.replace("Z", "+00:00"))
                            except ValueError:
                                hist_updated_at = datetime.min

                        if source_updated_at <= hist_updated_at:
                            continue

                        keys_to_replace.append(key)

                    hist_doc = dict(doc)
                    if "_id" in hist_doc:
                        del hist_doc["_id"]

                    now = datetime.now()
                    hist_doc["archived_at"] = now
                    hist_doc["archived_timestamp"] = now

                    historical_inserts.append(hist_doc)

                if keys_to_replace:
                    replace_filter = {
                        "$or": [
                            {
                                "scheduled_at": key[0],
                                "time_block": key[1],
                                "type": key[2],
                            }
                            for key in keys_to_replace
                        ]
                    }
                    try:
                        del_result = await self.historical_collection.delete_many(replace_filter)
                        replaced_count = del_result.deleted_count
                        logger.info(f"Replaced {replaced_count} stale historical demand records")
                    except Exception as del_err:
                        logger.error(f"Error deleting stale historical records: {del_err}")

            archived_count = 0
            if historical_inserts:
                try:
                    res = await self.historical_collection.insert_many(historical_inserts, ordered=False)
                    archived_count = len(res.inserted_ids)
                except Exception as e:
                    logger.error(f"Bulk insert error in demand archival: {e}")
                    if hasattr(e, 'details'):
                        archived_count = e.details.get('nInserted', 0)

            logger.info(f"Archived {archived_count} demand records to historical (replaced {replaced_count})")

            delete_query = {
                "scheduled_at": {"$lt": datetime.combine(delete_cutoff, datetime.min.time())}
            }

            del_res = await self.serve_collection.delete_many(delete_query)
            deleted_count = del_res.deleted_count

            logger.info(f"Cleaned up {deleted_count} demand records older than {delete_cutoff}")

            return {
                "status": "success",
                "archived_count": archived_count,
                "replaced_count": replaced_count,
                "deleted_count": deleted_count,
                "cutoff_date": delete_cutoff.isoformat(),
                "execution_time": (datetime.now() - start_time).total_seconds()
            }

        except Exception as e:
            logger.error("Error in demand archival: %s", str(e))
            return {"error": str(e), "status": "error"}

    async def count_demand_serve(self) -> int:
        """Count total demand serve records"""
        return await self.count_serve()

    async def count_demand_historical(self) -> int:
        """Count total demand historical records"""
        return await self.count_historical()

    async def bulk_insert_demand_serve(self, entities: List[DemandServeEntity]) -> int:
        """Bulk insert demand serve records"""
        return await self.bulk_insert_serve(entities)

    async def bulk_insert_demand_historical(self, entities: List[DemandServeHistoricalEntity]) -> List[str]:
        """Bulk insert demand historical records"""
        return await self.bulk_insert_historical(entities)

    async def delete_by_id(self, record_id: str) -> bool:
        """Delete demand serve record by ID"""
        try:
            from bson import ObjectId
            result = await self.serve_collection.delete_one({"_id": ObjectId(record_id)})
            success = result.deleted_count > 0
            if success:
                logger.debug("Deleted demand record with ID: %s", record_id)
            else:
                logger.warning("No demand record found with ID: %s", record_id)
            return success
        except Exception as e:
            logger.error("Error deleting demand record by ID %s: %s", record_id, str(e))
            return False

    async def delete_insert_demand_serve(self, filter_criteria: Dict[str, Any], entities: List[DemandServeEntity]) -> Dict[str, Any]:
        """
        Delete existing demand serve records by filter criteria and insert new ones.
        """
        try:
            cursor = self.serve_collection.find(filter_criteria)
            existing_records = await cursor.to_list(length=10000)

            delete_result = await self.serve_collection.delete_many(filter_criteria)
            deleted_count = delete_result.deleted_count

            logger.info(f"Deleted {deleted_count} existing demand serve records matching criteria: {filter_criteria}")

            processed_count = 0
            if entities:
                processed_count = await self.bulk_insert_demand_serve(entities)
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

    async def find_idf_demand_for_blocks(
        self,
        scheduled_at: datetime,
        time_blocks: List[int],
    ) -> List[Dict[str, Any]]:
        """Fetch IDF demand_serve rows for a calendar day and given time blocks."""
        if not time_blocks:
            return []

        start_of_day = datetime.combine(scheduled_at.date(), datetime.min.time())
        end_of_day = start_of_day + timedelta(days=1)
        query = {
            "scheduled_at": {"$gte": start_of_day, "$lt": end_of_day},
            "type": "IDF",
            "time_block": {"$in": time_blocks},
        }
        cursor = self.serve_collection.find(query)
        return await cursor.to_list(length=max(len(time_blocks), 1))

    async def bulk_update_dsm_fields(self, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Set dsm_rate and net_dsm_amount on existing demand_serve documents (no delete/insert).
        Each operation: {"filter": {...}, "update": {"dsm_rate", "net_dsm_amount", ...}}
        """
        if not operations:
            return {"matched_count": 0, "modified_count": 0}

        try:
            from pymongo import UpdateOne

            bulk_ops = [
                UpdateOne(op["filter"], {"$set": op["update"]})
                for op in operations
                if op.get("filter") and op.get("update")
            ]
            if not bulk_ops:
                return {"matched_count": 0, "modified_count": 0}

            result = await self.serve_collection.bulk_write(bulk_ops, ordered=False)
            return {
                "matched_count": result.matched_count,
                "modified_count": result.modified_count,
            }
        except Exception as e:
            logger.error("Error in bulk_update_dsm_fields: %s", e)
            raise

    async def bulk_delete_insert_custom(self, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Execute bulk delete and insert operations efficiently.
        """
        if not operations:
            return {"deleted_count": 0, "inserted_count": 0}

        try:
            from pymongo import DeleteOne, InsertOne

            bulk_ops = []
            for op in operations:
                if op.get("filter"):
                    bulk_ops.append(DeleteOne(op["filter"]))

                if op.get("document"):
                    doc = op["document"]
                    if hasattr(doc, "dict"):
                        doc = doc.dict(by_alias=True)
                    elif hasattr(doc, "model_dump"):
                        doc = doc.model_dump(by_alias=True)

                    bulk_ops.append(InsertOne(doc))

            if not bulk_ops:
                return {"deleted_count": 0, "inserted_count": 0}

            result = await self.serve_collection.bulk_write(bulk_ops, ordered=True)

            return {
                "deleted_count": result.deleted_count,
                "inserted_count": result.inserted_count,
                "modified_count": result.modified_count
            }

        except Exception as e:
            logger.error(f"Error in bulk_delete_insert_custom: {str(e)}")
            raise

    async def bulk_insert_demand_historical_with_deduplication(self, entities: List[DemandServeHistoricalEntity]) -> Dict[str, Any]:
        """
        Bulk insert demand historical records with robust deduplication.
        Unique Key: type, scheduled_at, time_block
        """
        try:
            if not entities:
                return {"inserted_count": 0, "error_count": 0, "duplicates_removed": 0}

            logger.info(f"Processing {len(entities)} demand historical entities for deduplication...")

            inserts = [e.model_dump(by_alias=True) for e in entities]

            dates = [i.get("scheduled_at") for i in inserts if i.get("scheduled_at")]
            if not dates:
                return {"inserted_count": 0, "error_count": 0, "duplicates_removed": 0}

            min_date = min(dates)
            max_date = max(dates)

            existing_cursor = self.historical_collection.find(
                {
                    "scheduled_at": {"$gte": min_date, "$lte": max_date}
                },
                projection={"scheduled_at": 1, "time_block": 1, "type": 1, "_id": 0}
            )

            existing_keys = set()
            async for doc in existing_cursor:
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
                result = await self.historical_collection.bulk_write(operations, ordered=False)
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

    async def get_demand(self, schedule_date_str: str) -> List[Dict[str, Any]]:
        """
        Get demand for a specific date string YYYY-MM-DD.
        Used by RTM Scheduling Processor.
        """
        try:
            dt = datetime.strptime(schedule_date_str, "%Y-%m-%d")
            start_date = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = dt.replace(hour=23, minute=59, second=59, microsecond=999999)

            return await self.find_demand_serve_by_date_range(start_date, end_date)
        except Exception as e:
            logger.error(f"Error in get_demand: {str(e)}")
            return []

    async def get_all_async(self, collection_name: str, filter: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Get all records asynchronously matching the filter criteria.
        """
        try:
            cursor = self.serve_collection.find(filter)
            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error(f"Error in get_all_async: {str(e)}")
            return []


# Create repository instance
demand_serve_repository = DemandServeRepository()
