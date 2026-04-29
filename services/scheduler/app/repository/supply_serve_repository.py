"""
Scheduler Supply Serve Repository
Handles database operations for supply serve and historical collections
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from libs.database.base_serve_repository import BaseServeRepository
from libs.models import SupplyServeEntity, SupplyServeHistoricalEntity
from ..core.config import settings

logger = logging.getLogger(__name__)


class SupplyServeRepository(BaseServeRepository):
    """Repository for supply serve and historical collections"""

    def __init__(self):
        """Initialize the repository with MongoDB connection"""
        super().__init__(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE,
            serve_collection=settings.MONGODB_SUPPLY_SERVE_COLLECTION,
            historical_collection=settings.MONGODB_SUPPLY_SERVE_HISTORICAL_COLLECTION
        )

    async def _ensure_time_series_collections(self):
        """
        Ensure collections exist.
        Migrates 'supply_serve' from Time-Series to Normal Collection if needed.
        Keeps 'supply_serve_historical' as Time-Series.
        """
        try:
            # 1. Historical Collection (Keep as Time-Series)
            existing_names = await self.db.list_collection_names()
            if settings.MONGODB_SUPPLY_SERVE_HISTORICAL_COLLECTION not in existing_names:
                await self.db.create_collection(
                    settings.MONGODB_SUPPLY_SERVE_HISTORICAL_COLLECTION,
                    timeseries={
                        "timeField": "archived_timestamp",
                        "metaField": "scheduled_at",
                        "granularity": "hours",
                    },
                )
                logger.info(
                    "Created time-series collection: %s",
                    settings.MONGODB_SUPPLY_SERVE_HISTORICAL_COLLECTION,
                )

            self.historical_collection = self.db[settings.MONGODB_SUPPLY_SERVE_HISTORICAL_COLLECTION]

            # 2. Main Collection (Ensure Normal Collection + Safe Migration)
            main_coll_name = settings.MONGODB_SUPPLY_SERVE_COLLECTION

            existing_colls = await self.db.list_collections(filter={"name": main_coll_name}).to_list(length=10000)
            existing_coll_info = existing_colls[0] if existing_colls else None

            if existing_coll_info:
                options = existing_coll_info.get("options", {})
                is_view = existing_coll_info.get("type") == "view"
                is_timeseries = "timeseries" in options

                if is_timeseries or is_view:
                    logger.warning(f"Collection '{main_coll_name}' is {'VIEW' if is_view else 'Time-Series'}. Starting SAFE MIGRATION to Normal Collection...")

                    # 1. Snapshot Data (Safety First)
                    start_migration_date = datetime.now() - timedelta(days=5)
                    logger.info("Snapshotting data before migration...")

                    snapshot_data = []
                    try:
                        cursor = self.db[main_coll_name].find({"scheduled_at": {"$gte": start_migration_date}})
                        all_docs = await cursor.to_list(length=10000)

                        deduped_map = {}
                        for doc in all_docs:
                            if "_id" in doc: del doc["_id"]
                            key = (
                                doc.get("plant_id"),
                                doc.get("scheduled_at"),
                                doc.get("time_block"),
                                doc.get("dc_sg")
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
                        await self.db[main_coll_name].rename(backup_name)
                        logger.info(f"Renamed to backup: {backup_name}")
                    except Exception as e:
                        logger.warning(f"Rename failed (View?). Dropping view: {main_coll_name}. Error: {e}")
                        await self.db.drop_collection(main_coll_name)

                    # 3. Create Normal
                    await self.db.create_collection(main_coll_name)
                    logger.info(f"Created Normal Collection: {main_coll_name}")

                    # 4. Restore
                    if snapshot_data:
                        batch_size = 1000
                        for i in range(0, len(snapshot_data), batch_size):
                             batch = snapshot_data[i:i+batch_size]
                             await self.db[main_coll_name].insert_many(batch)
                        logger.info(f"Migration Complete. Restored {len(snapshot_data)} records.")

                else:
                    logger.info(f"Collection '{main_coll_name}' exists and is likely Normal. No migration needed.")
            else:
                # Does not exist, create as Normal
                await self.db.create_collection(main_coll_name)
                logger.info(f"Created new Normal Collection: {main_coll_name}")

            self.serve_collection = self.db[main_coll_name]

        except (ConnectionError, TimeoutError) as e:
            logger.info("Could not create/migrate collections: %s", str(e))
        except Exception as e:
            logger.error("Error ensuring collections: %s", str(e))
            raise e

    async def create_indexes(self):
        """
        Create indexes for supply_serve and supply_serve_historical collections.
        """
        try:
            logger.info("Creating indexes for supply collections...")

            # SUPPLY_SERVE COLLECTION INDEXES (NORMAL COLLECTION)

            # 1. UNIQUE COMPOUND INDEX
            await self.serve_collection.create_index(
                [
                    ("plant_id", 1),
                    ("scheduled_at", 1),
                    ("time_block", 1),
                    ("dc_sg", 1)
                ],
                name="idx_unique_supply_key",
                unique=True,
                background=True
            )

            # 2. Common Query Indexes
            await self.serve_collection.create_index([("scheduled_at", 1)], background=True)
            await self.serve_collection.create_index([("time_block", 1)], background=True)
            await self.serve_collection.create_index([("plant_id", 1)], background=True)
            await self.serve_collection.create_index([("updated_flag", 1)], background=True)
            await self.serve_collection.create_index([("inserted_at", -1)], background=True)

            logger.info("Created indexes for supply_serve collection")

            # SUPPLY_SERVE_HISTORICAL COLLECTION INDEXES (TIME-SERIES)

            await self.historical_collection.create_index(
                [
                    ("plant_id", 1),
                    ("scheduled_at", 1)
                ],
                name="idx_historical_plant_schedule",
                background=True
            )

            await self.historical_collection.create_index([("scheduled_at", 1)], background=True)
            await self.historical_collection.create_index([("archived_at", -1)], background=True)

            logger.info("Created indexes for supply_serve_historical collection")

        except Exception as e:
            logger.warning(f"Failed to create some indexes: {e}")


    async def insert_supply_serve(self, supply_serve: SupplyServeEntity) -> str:
        """Insert a new supply serve record"""
        return await self.insert_serve(supply_serve)

    async def insert_supply_historical(self, supply_historical: SupplyServeHistoricalEntity) -> str:
        """Insert a new supply historical record"""
        return await self.insert_historical(supply_historical)

    async def delete_supply_serve_by_date(self, scheduled_at: datetime) -> Dict[str, Any]:
        """Delete supply serve records by scheduled_at date"""
        return await self.delete_serve_by_date(scheduled_at)

    async def find_supply_serve_by_date(self, scheduled_at: datetime) -> List[Dict[str, Any]]:
        """Find supply serve records by scheduled_at date"""
        return await self.find_serve_by_date(scheduled_at)

    async def find_supply_serve_by_date_range(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """Find supply serve records by date range"""
        return await self.find_serve_by_date_range(start_date, end_date)

    async def count_supply_serve(self) -> int:
        """Count total supply serve records"""
        return await self.count_serve()

    async def count_supply_historical(self) -> int:
        """Count total supply historical records"""
        return await self.count_historical()

    async def bulk_insert_supply_serve(self, entities: List[SupplyServeEntity]) -> List[str]:
        """Bulk insert supply serve records"""
        result = await self.bulk_insert_serve(entities)
        if isinstance(result, int):
            return [str(result)]
        elif isinstance(result, list):
            return [str(_id) for _id in result]
        else:
            return [str(result)]

    async def bulk_insert_supply_historical(self, entities: List[SupplyServeHistoricalEntity]) -> List[str]:
        """Bulk insert supply historical records"""
        return await self.bulk_insert_historical(entities)

    async def delete_insert_supply_serve(self, filter_criteria: Dict[str, Any], entities: List[SupplyServeEntity]) -> Dict[str, Any]:
        """
        Delete existing supply serve records by filter criteria and insert new ones.
        """
        try:
            cursor = self.serve_collection.find(filter_criteria)
            existing_records = await cursor.to_list(length=10000)

            delete_result = await self.serve_collection.delete_many(filter_criteria)
            deleted_count = delete_result.deleted_count

            logger.info(f"Deleted {deleted_count} existing supply serve records matching criteria: {filter_criteria}")

            inserted_ids = []
            if entities:
                inserted_ids = await self.bulk_insert_supply_serve(entities)

                if len(inserted_ids) == 1 and inserted_ids[0].isdigit():
                     logger.info(f"Inserted {inserted_ids[0]} new supply serve records")
                else:
                     logger.info(f"Inserted {len(inserted_ids)} new supply serve records")

            return {
                "deleted_count": deleted_count,
                "inserted_count": len(inserted_ids),
                "deleted_records": existing_records,
                "inserted_ids": inserted_ids,
                "filter_criteria": filter_criteria
            }

        except Exception as e:
            logger.error(f"Error in delete-insert operation with criteria {filter_criteria}: {str(e)}")
            raise

    async def find_by_unique_key(self, scheduled_at: datetime, time_block: Optional[int],
                          dc_sg: str, plant_id: str) -> Optional[Dict[str, Any]]:
        """Find supply record by unique key (scheduled_at, time_block, dc_sg, plant_id)."""
        try:
            filter_criteria = {
                "scheduled_at": scheduled_at,
                "dc_sg": dc_sg,
                "plant_id": plant_id
            }

            if time_block is not None:
                filter_criteria["time_block"] = time_block

            record = await self.serve_collection.find_one(filter_criteria, sort=[("inserted_at", -1)])

            if record:
                logger.debug(f"Found existing supply record for key: {filter_criteria}")
            else:
                logger.debug(f"No existing supply record found for key: {filter_criteria}")

            return record

        except Exception as e:
            logger.error("Error finding supply record by unique key: %s", str(e))
            return None

    async def bulk_insert_supply_serve_with_deduplication(self, entities: List[SupplyServeEntity]) -> Dict[str, Any]:
        """
        Bulk insert supply serve entities using Optimized Bulk Upsert.
        """
        try:
            if not entities:
                return {"inserted_count": 0}

            records = [e.model_dump(by_alias=True) for e in entities]

            for r in records:
                if "updated_flag" in r and hasattr(r["updated_flag"], "value"):
                    r["updated_flag"] = r["updated_flag"].value

            unique_key = ["plant_id", "scheduled_at", "time_block", "dc_sg"]

            result = await self.bulk_upsert_optimized(records, unique_key)

            return {
                "inserted_count": result.get("inserted_count", 0),
                "modified_count": result.get("modified_count", 0),
                "upserted_count": result.get("upserted_count", 0),
                "total_processed": result.get("total_processed", 0)
            }

        except Exception as e:
            logger.error("Error in bulk insert with deduplication: %s", str(e))
            return {
                "error": str(e),
                "inserted_count": 0
            }

    async def process_daily_archival_and_cleanup(self) -> Dict[str, Any]:
        """
        End-of-Day Job:
        1. Archive 'Yesterday's' data to Historical (Idempotent).
        2. Cleanup Main collection (keep floating window).
        """
        try:
            today = datetime.now().date()
            yesterday = today - timedelta(days=1)
            cleanup_cutoff = today - timedelta(days=3)

            logger.info(f"Starting archival for date: {yesterday}")

            start_yesterday = datetime.combine(yesterday, datetime.min.time())
            end_yesterday = datetime.combine(yesterday, datetime.max.time())

            cursor = self.serve_collection.find({
                "scheduled_at": {"$gte": start_yesterday, "$lte": end_yesterday}
            })
            all_docs = await cursor.to_list(length=10000)

            archival_batch = []
            archived_count = 0

            for doc in all_docs:
                hist_doc = dict(doc)
                if "_id" in hist_doc: del hist_doc["_id"]

                hist_doc["archived_at"] = datetime.now()
                hist_doc["archived_timestamp"] = datetime.now()

                archival_batch.append(hist_doc)

            if archival_batch:
                unique_key = ["plant_id", "scheduled_at", "time_block", "dc_sg"]

                from pymongo import UpdateOne
                hist_ops = []
                for hdoc in archival_batch:
                    filter_q = {k: hdoc.get(k) for k in unique_key}
                    hist_ops.append(
                        UpdateOne(filter_q, {"$setOnInsert": hdoc}, upsert=True)
                    )

                if hist_ops:
                    h_result = await self.historical_collection.bulk_write(hist_ops, ordered=False)
                    archived_count = h_result.upserted_count

            logger.info(f"Archived {archived_count} records for {yesterday}")

            logger.info(f"Cleaning up data older than {cleanup_cutoff}")

            del_result = await self.serve_collection.delete_many({
                "scheduled_at": {"$lt": datetime.combine(cleanup_cutoff, datetime.min.time())}
            })

            deleted_count = del_result.deleted_count
            logger.info(f"Deleted {deleted_count} old records")

            return {
                "archived_count": archived_count,
                "deleted_count": deleted_count,
                "archive_date": yesterday.isoformat(),
                "cleanup_cutoff": cleanup_cutoff.isoformat()
            }

        except Exception as e:
            logger.error("Error in daily archival process: %s", str(e))
            return {"error": str(e)}

    async def archive_old_supply_data(self, days_to_keep: int = 2) -> Dict[str, Any]:
        return await self.process_daily_archival_and_cleanup()

    async def delete_all_by_unique_key(self, scheduled_at: datetime, time_block: Optional[int],
                                dc_sg: str, plant_id: str) -> int:
        """Delete ALL records for a unique key."""
        try:
            filter_criteria = {
                "scheduled_at": scheduled_at,
                "dc_sg": dc_sg,
                "plant_id": plant_id
            }

            if time_block is not None:
                filter_criteria["time_block"] = time_block

            result = await self.serve_collection.delete_many(filter_criteria)
            deleted_count = result.deleted_count

            if deleted_count > 0:
                logger.debug(f"Deleted {deleted_count} supply records for key: {filter_criteria}")

            return deleted_count

        except Exception as e:
            logger.error("Error deleting supply records for key %s: %s", filter_criteria, str(e))
            return 0

    async def bulk_insert_supply_historical_with_deduplication(self, entities: List[SupplyServeHistoricalEntity]) -> Dict[str, Any]:
        """
        Bulk insert supply historical entities with robust error handling.
        Historical collection is Time-Series (append-only preference).
        """
        try:
            if not entities:
                return {"inserted_count": 0, "error_count": 0, "errors": [], "duplicates_removed": 0}

            logger.info(f"Processing {len(entities)} historical entities...")

            inserts = [e.model_dump(by_alias=True) for e in entities]

            if not inserts:
                return {"inserted_count": 0, "error_count": 0, "errors": [], "duplicates_removed": 0}

            from pymongo import InsertOne
            from pymongo.errors import BulkWriteError

            # 1. Internal Batch Deduplication
            unique_inserts = {}
            internal_dupes = 0
            for i in inserts:
                k = (
                    str(i.get("plant_id")),
                    i.get("scheduled_at"),
                    str(i.get("time_block")),
                    str(i.get("dc_sg"))
                )
                if k in unique_inserts:
                    internal_dupes += 1
                    continue
                unique_inserts[k] = i

            inserts = list(unique_inserts.values())

            dates = [i.get("scheduled_at") for i in inserts if i.get("scheduled_at")]
            if len(dates) == 0:
                 return {"inserted_count": 0, "error_count": 0, "errors": [], "duplicates_removed": internal_dupes}

            min_date = min(dates)
            max_date = max(dates)

            # Fetch existing keys in this range
            existing_cursor = self.historical_collection.find(
                {
                    "scheduled_at": {"$gte": min_date, "$lte": max_date}
                },
                projection={"plant_id": 1, "scheduled_at": 1, "time_block": 1, "dc_sg": 1, "_id": 0}
            )

            existing_keys = set()
            async for doc in existing_cursor:
                k = (
                    str(doc.get("plant_id")),
                    doc.get("scheduled_at"),
                    str(doc.get("time_block")),
                    str(doc.get("dc_sg"))
                )
                existing_keys.add(k)

            # 3. Filter Inserts against DB
            final_inserts = []
            db_duplicates_count = 0

            for i in inserts:
                check_key = (
                    str(i.get("plant_id")),
                    i.get("scheduled_at"),
                    str(i.get("time_block")),
                    str(i.get("dc_sg"))
                )

                if check_key in existing_keys:
                    db_duplicates_count += 1
                    continue

                final_inserts.append(i)

            total_duplicates = internal_dupes + db_duplicates_count

            if not final_inserts:
                return {
                    "inserted_count": 0,
                    "error_count": 0,
                    "errors": [],
                    "duplicates_removed": total_duplicates,
                    "modified_count": 0
                }

            operations = [InsertOne(i) for i in final_inserts]

            try:
                result = await self.historical_collection.bulk_write(operations, ordered=False)
                inserted_count = result.inserted_count
                error_count = 0
                errors = []
            except BulkWriteError as bwe:
                inserted_count = bwe.details.get('nInserted', 0)
                write_errors = bwe.details.get('writeErrors', [])
                error_count = len(write_errors)
                errors = [str(err.get('errmsg')) for err in write_errors]
                logger.warning(f"Historical insert had {error_count} errors. Inserted: {inserted_count}")

            return {
                "inserted_count": inserted_count,
                "modified_count": 0,
                "error_count": error_count,
                "errors": errors,
                "duplicates_removed": total_duplicates
            }

        except Exception as e:
            logger.error("Error in bulk insert historical: %s", str(e))
            return {"inserted_count": 0, "error_count": 1, "errors": [str(e)], "duplicates_removed": 0}

    async def aggregate_supply_schedule(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """
        Aggregate supply volume to calculate demand schedule.
        """
        try:
            pipeline = [
                {
                    "$match": {
                        "scheduled_at": {
                            "$gte": start_date,
                            "$lt": end_date
                        },
                        "dc_sg": "SG"
                    }
                },
                {
                    "$lookup": {
                        "from": "plant_master",
                        "localField": "plant_id",
                        "foreignField": "plant_id",
                        "as": "plant_info"
                    }
                },
                {
                    "$unwind": {
                        "path": "$plant_info",
                        "preserveNullAndEmptyArrays": True
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "time_block": "$time_block",
                            "scheduled_at": "$scheduled_at"
                        },
                        "central_schedule": {
                            "$sum": {
                                "$cond": [
                                    {"$eq": ["$plant_info.c_s", "central"]},
                                    "$volume",
                                    0
                                ]
                            }
                        },
                        "state_schedule": {
                            "$sum": {
                                "$cond": [
                                    {"$eq": ["$plant_info.c_s", "central"]},
                                    0,
                                    "$volume"
                                ]
                            }
                        },
                        "total_schedule": {"$sum": "$volume"}
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "time_block": "$_id.time_block",
                        "scheduled_at": "$_id.scheduled_at",
                        "central_schedule": 1,
                        "state_schedule": 1,
                        "total_schedule": 1
                    }
                },
                {
                    "$sort": {
                        "scheduled_at": -1,
                        "time_block": 1
                    }
                }
            ]

            cursor = self.serve_collection.aggregate(pipeline)
            return await cursor.to_list(length=10000)

        except Exception as e:
            logger.error(f"Error aggregating supply schedule: {str(e)}")
            return []


# Create repository instance
supply_serve_repository = SupplyServeRepository()
