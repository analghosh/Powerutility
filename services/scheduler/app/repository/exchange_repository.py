"""
Market Data Repository
Handles database operations for market data using entity-repository pattern
"""

import logging
from datetime import datetime, timedelta, date
from typing import Dict, Any, List, Optional
from bson import ObjectId

from libs.database.base_serve_repository import BaseServeRepository, DataInsertionError
from libs.enum.raw_data_status import RawDataStatus
from ..core.config import settings

logger = logging.getLogger(__name__)


class ExchangeRepository(BaseServeRepository):
    """Repository for market data operations using entity-repository pattern"""

    def __init__(self):
        """Initialize the repository with MongoDB connection"""
        super().__init__(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE,
            serve_collection=settings.MONGODB_EXCHANGE_SERVE_COLLECTION,
            historical_collection=settings.MONGODB_EXCHANGE_SERVE_HISTORICAL_COLLECTION
        )

        self._raw_collection = None

    async def create_indexes(self):
        """Create unique indexes for the Serve collection (Normal collection)"""
        # Note: Time-Series collections (Historical) do not support unique indexes
        await self.serve_collection.create_index(
            [
                ("scheduled_at", 1),
                ("time_block", 1),
                ("market_type", 1),
                ("px_name", 1)
            ],
            unique=True,
            name="idx_unique_exchange_key",
            background=True
        )

    async def _ensure_market_data_time_series_collections(self):
        """Ensure collections exist and handle Time-Series migrations"""
        try:
            # 1. Ensure Historical is a Time-Series Collection
            existing_names = await self.db.list_collection_names()
            if settings.MONGODB_EXCHANGE_SERVE_HISTORICAL_COLLECTION not in existing_names:
                await self.db.create_collection(
                    settings.MONGODB_EXCHANGE_SERVE_HISTORICAL_COLLECTION,
                    timeseries={
                        "timeField": "archived_at",
                        "metaField": "scheduled_at",
                        "granularity": "hours",
                    },
                )
            self.historical_collection = self.db[settings.MONGODB_EXCHANGE_SERVE_HISTORICAL_COLLECTION]

            # 2. Ensure Main Serve collection is a Normal collection (for Updates/Deletes)
            main_coll_name = settings.MONGODB_EXCHANGE_SERVE_COLLECTION
            existing_colls = await self.db.list_collections(filter={"name": main_coll_name}).to_list(length=10000)
            existing_coll_info = existing_colls[0] if existing_colls else None

            if existing_coll_info:
                options = existing_coll_info.get("options", {})
                if "timeseries" in options or existing_coll_info.get("type") == "view":
                    logger.warning(f"Migrating {main_coll_name} from TS/View to Normal...")
                    await self._migrate_to_normal_collection(main_coll_name)
            elif not existing_coll_info:
                await self.db.create_collection(main_coll_name)

            self.serve_collection = self.db[main_coll_name]

        except Exception as e:
            logger.error("Error ensuring exchange collections: %s", str(e))

    async def _migrate_to_normal_collection(self, coll_name: str):
        """Helper to snapshot and recreate a collection as a normal one"""
        try:
            start_date = datetime.now() - timedelta(days=5)
            cursor = self.db[coll_name].find({"scheduled_at": {"$gte": start_date}})
            all_docs = await cursor.to_list(length=10000)

            deduped_data = {}
            for doc in all_docs:
                if "_id" in doc: del doc["_id"]
                key = (doc.get("scheduled_at"), doc.get("time_block"), doc.get("market_type"), doc.get("px_name"))
                deduped_data[key] = doc

            snapshot = list(deduped_data.values())
            await self.db.drop_collection(coll_name)
            await self.db.create_collection(coll_name)
            if snapshot:
                await self.db[coll_name].insert_many(snapshot)
            logger.info(f"Migration successful for {coll_name}")
        except Exception as e:
            logger.error(f"Migration failed: {e}")

    @property
    def raw_collection(self):
        if self._raw_collection is None:
            self._raw_collection = self.db[settings.MONGODB_IEX_EXCHANGE_RAW_COLLECTION]
        return self._raw_collection

    def _map_fields_for_db(self, record: Dict[str, Any]) -> Dict[str, Any]:
        mapped_record = {}
        for field_name, value in record.items():
            if isinstance(value, date) and not isinstance(value, datetime):
                mapped_record[field_name] = datetime.combine(value, datetime.min.time())
            else:
                mapped_record[field_name] = value
        return mapped_record

    def _day_bounds_utc(self, schedule_date: datetime) -> tuple:
        if isinstance(schedule_date, str):
            schedule_date = datetime.strptime(schedule_date, "%Y-%m-%d")
        start_date = schedule_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)
        return start_date, end_date

    async def find_by_schedule_timeblock_market(self, scheduled_at: datetime, time_block: int, market_type: str) -> Optional[Dict[str, Any]]:
        start_date, end_date = self._day_bounds_utc(scheduled_at)
        try:
            query = {
                "scheduled_at": {"$gte": start_date, "$lt": end_date},
                "time_block": time_block,
                "market_type": market_type
            }
            return await self.serve_collection.find_one(query)
        except Exception as e:
            logger.error(f"Error finding exchange record: {str(e)}")
            return None

    async def archive_old_exchange_data(self, days_to_keep: int = 2) -> Dict[str, Any]:
        """
        Archive Strategy for Time-Series Historical.
        Processes in 7-day chunks to avoid loading unbounded historical data into memory.
        """
        try:
            today_min = datetime.combine(date.today(), datetime.min.time())
            delete_cutoff = today_min - timedelta(days=days_to_keep)

            logger.info(f"Archiving to Time-Series. Cutoff Archive: {today_min.date()}, Cleanup: {delete_cutoff.date()}")

            # Find earliest record to archive (cheap indexed lookup)
            earliest = await self.serve_collection.find_one(
                {"scheduled_at": {"$lt": today_min}},
                {"scheduled_at": 1},
                sort=[("scheduled_at", 1)]
            )
            if not earliest:
                logger.info("No candidates for archival.")
                del_res = await self.serve_collection.delete_many({"scheduled_at": {"$lt": delete_cutoff}})
                return {"archived_count": 0, "deleted_count": del_res.deleted_count, "status": "success"}

            total_archived = 0
            chunk_days = 7
            chunk_start = earliest["scheduled_at"].replace(hour=0, minute=0, second=0, microsecond=0)

            # Process in 7-day chunks — each chunk holds at most ~7K exchange records in RAM
            while chunk_start < today_min:
                chunk_end = min(chunk_start + timedelta(days=chunk_days), today_min)

                candidates = []
                async for doc in self.serve_collection.find({
                    "scheduled_at": {"$gte": chunk_start, "$lt": chunk_end}
                }):
                    candidates.append(doc)

                if candidates:
                    # Load existing historical keys for this chunk to avoid duplicates
                    seen_keys: set = set()
                    async for d in self.historical_collection.find(
                        {"scheduled_at": {"$gte": chunk_start, "$lt": chunk_end}},
                        {"scheduled_at": 1, "time_block": 1, "market_type": 1, "px_name": 1, "_id": 0}
                    ):
                        seen_keys.add((d["scheduled_at"], d["time_block"], d["market_type"], d.get("px_name")))

                    now = datetime.now()
                    to_insert = []
                    for doc in candidates:
                        key = (doc["scheduled_at"], doc["time_block"], doc["market_type"], doc.get("px_name"))
                        if key not in seen_keys:
                            hist_doc = dict(doc)
                            hist_doc.pop("_id", None)
                            hist_doc["archived_at"] = now
                            to_insert.append(hist_doc)
                            seen_keys.add(key)

                    if to_insert:
                        res = await self.historical_collection.insert_many(to_insert, ordered=False)
                        total_archived += len(res.inserted_ids)

                    logger.info(
                        f"Chunk {chunk_start.date()} → {chunk_end.date()}: "
                        f"{len(candidates)} candidates, {len(to_insert)} new inserts"
                    )

                chunk_start = chunk_end

            logger.info(f"Successfully archived {total_archived} exchange records.")

            # Cleanup Serve collection (Older than T-N)
            del_res = await self.serve_collection.delete_many({"scheduled_at": {"$lt": delete_cutoff}})
            logger.info(f"Cleaned {del_res.deleted_count} records from Serve.")

            return {
                "archived_count": total_archived,
                "deleted_count": del_res.deleted_count,
                "status": "success"
            }

        except Exception as e:
            logger.error(f"Archival failed: {str(e)}", exc_info=True)
            return {"archived_count": 0, "deleted_count": 0, "error": str(e)}

    async def get_market_data_by_date(self, scheduled_at: datetime, market_type: str, px_name: str = None) -> List[Dict[str, Any]]:
        """
        Fetch market data records for a specific date and market type, sorted by time block.

        Args:
            scheduled_at: The date to fetch records for
            market_type: The market type (e.g., "RTM", "DAM")
            px_name: Optional exchange name filter (e.g., "IEX")

        Returns:
            List of market data records
        """
        start_date, end_date = self._day_bounds_utc(scheduled_at)
        try:
            query = {
                "scheduled_at": {"$gte": start_date, "$lt": end_date},
                "market_type": market_type
            }
            if px_name:
                query["px_name"] = px_name
            records = await self.serve_collection.find(query).sort("time_block", 1).to_list(10000)
            logger.info(f"Found {len(records)} exchange records for {market_type} on {start_date.date()}")
            return records
        except Exception as e:
            logger.error(f"Error fetching market data by date: {str(e)}")
            return []

    async def bulk_upsert_exchange_records(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Bulk upsert for Serve collection (Normal collection)"""
        try:
            if not records: return {"inserted_count": 0}
            mapped_records = [self._map_fields_for_db(r) for r in records]
            unique_key = ["scheduled_at", "time_block", "market_type", "px_name"]
            return await self.bulk_upsert_optimized(mapped_records, unique_key)
        except Exception as e:
            logger.error("Error in bulk upsert exchange: %s", str(e))
            raise

    async def get_pending_raw_data(self, limit: int = 10) -> List[Dict[str, Any]]:
        try:
            query = {"status": {"$in": [RawDataStatus.PENDING, RawDataStatus.FAILED]}}
            cursor = self.raw_collection.find(query).sort("createdAt", 1).limit(limit)
            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error("Error getting pending raw data: %s", str(e))
            return []


# Global repository instance
exchange_repository = ExchangeRepository()
