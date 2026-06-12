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

        The archival is idempotent: if a historical record already exists for the same
        unique key but the serve copy has been updated more recently, the historical
        copy is replaced so the historical table remains a true mirror of the serve table.
        """
        try:
            today_min = datetime.combine(date.today(), datetime.min.time())
            delete_cutoff = today_min - timedelta(days=days_to_keep)

            logger.info(f"Archiving to Time-Series. Cutoff Archive: {today_min.date()}, Cleanup: {delete_cutoff.date()}")

            # 1. Fetch candidates from Serve (Anything older than today)
            cursor = self.serve_collection.find({"scheduled_at": {"$lt": today_min}})
            candidates = await cursor.to_list(length=10000)

            if not candidates:
                logger.info("No candidates for archival.")
                return {
                    "archived_count": 0,
                    "replaced_count": 0,
                    "deleted_count": 0,
                    "status": "success"
                }

            unique_key = ["scheduled_at", "time_block", "market_type", "px_name"]

            # 2. Deduplication / replacement against Historical Time-Series
            min_d = min(c["scheduled_at"] for c in candidates)
            max_d = max(c["scheduled_at"] for c in candidates)

            existing_hist_cursor = self.historical_collection.find(
                {"scheduled_at": {"$gte": min_d, "$lte": max_d}},
                {**{k: 1 for k in unique_key}, "updated_at": 1, "_id": 1}
            )

            existing_map: Dict[tuple, Dict[str, Any]] = {}
            async for d in existing_hist_cursor:
                key = tuple(d.get(k) for k in unique_key)
                existing_map[key] = d

            to_insert = []
            keys_to_replace = []

            for doc in candidates:
                key = tuple(doc.get(k) for k in unique_key)
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
                hist_doc["archived_at"] = datetime.now()
                to_insert.append(hist_doc)

            replaced_count = 0
            if keys_to_replace:
                replace_filter = {
                    "$or": [
                        {k: key[i] for i, k in enumerate(unique_key)}
                        for key in keys_to_replace
                    ]
                }
                try:
                    del_result = await self.historical_collection.delete_many(replace_filter)
                    replaced_count = del_result.deleted_count
                    logger.info(f"Replaced {replaced_count} stale historical exchange records")
                except Exception as del_err:
                    logger.error(f"Error deleting stale historical exchange records: {del_err}")

            # 3. Insert into Time-Series Historical
            archived_count = 0
            if to_insert:
                res = await self.historical_collection.insert_many(to_insert, ordered=False)
                archived_count = len(res.inserted_ids)
                logger.info(f"Successfully archived {archived_count} records (replaced {replaced_count}).")

            # 4. Cleanup Serve collection (Older than T-2)
            del_res = await self.serve_collection.delete_many({"scheduled_at": {"$lt": delete_cutoff}})
            logger.info(f"Cleaned {del_res.deleted_count} records from Serve.")

            return {
                "archived_count": archived_count,
                "replaced_count": replaced_count,
                "deleted_count": del_res.deleted_count,
                "status": "success"
            }

        except Exception as e:
            logger.error(f"Archival failed: {str(e)}", exc_info=True)
            return {"archived_count": 0, "replaced_count": 0, "deleted_count": 0, "error": str(e)}

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
            records = await self.serve_collection.find(query).sort("time_block", 1).to_list(length=10000)
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
