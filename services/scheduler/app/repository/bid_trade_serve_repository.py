"""
Bid Trade Serve Repository
Handles database operations for bid trade serve data with timeseries support
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from bson import ObjectId

from libs.database.base_serve_repository import BaseServeRepository
from libs.models.bid_trade_serve_entity import BidTradeServeEntity, BidTradeServeHistoricalEntity
from ..core.config import settings

logger = logging.getLogger(__name__)


class BidTradeServeRepository(BaseServeRepository):
    """Repository for bid trade serve data operations with timeseries support"""

    def __init__(self):
        """Initialize the bid trade serve repository"""
        super().__init__(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE,
            serve_collection=settings.MONGODB_BID_TRADE_SERVE_COLLECTION,
            historical_collection=settings.MONGODB_BID_TRADE_SERVE_HISTORICAL_COLLECTION
        )

    async def create_indexes(self):
        """Create unique indexes for the collection"""
        # Drop old index if it exists with a different key spec (e.g. missing bid_type)
        try:
            await self.serve_collection.drop_index("idx_unique_bid_key")
        except Exception:
            pass
        await self.serve_collection.create_index(
            [
                ("scheduled_at", 1),
                ("time_block", 1),
                ("type", 1),
                ("group", 1),
                ("bid_type", 1),
            ],
            unique=True,
            name="idx_unique_bid_key",
            background=True
        )

    async def _ensure_bid_timeseries_collections(self):
        """
        Ensure collections exist.
        Migrate 'bid_trade_serve' from Time-Series to Normal if needed.
        """
        try:
            # 1. Historical
            existing_names = await self.db.list_collection_names()
            if "bid_trade_serve_historical" not in existing_names:
                await self.db.create_collection(
                    "bid_trade_serve_historical",
                    timeseries={
                        "timeField": "archived_timestamp",
                        "metaField": "data_type",
                        "granularity": "hours",
                    },
                )
            self.historical_collection = self.db[settings.MONGODB_BID_TRADE_SERVE_HISTORICAL_COLLECTION]

            # 2. Main (Safe Migration)
            main_coll_name = settings.MONGODB_BID_TRADE_SERVE_COLLECTION
            # Optimized probe
            existing_colls = await self.db.list_collections(filter={"name": main_coll_name}).to_list(length=None)
            existing_coll_info = existing_colls[0] if existing_colls else None

            if existing_coll_info:
                # Robust View Detection
                options = existing_coll_info.get("options", {})
                is_view = existing_coll_info.get("type") == "view"
                # Robust timeseries detection
                is_timeseries = ("timeseries" in options) or (existing_coll_info.get("type") == "timeseries")

                if is_timeseries or is_view:
                    logger.warning(f"Bid Collection '{main_coll_name}' is {'VIEW' if is_view else 'Time-Series'}. SAFE MIGRATION...")

                    # 1. Snapshot
                    start_migration_date = datetime.now() - timedelta(days=5)
                    logger.info("Snapshotting data before migration...")

                    snapshot_data = []
                    try:
                        cursor = self.db[main_coll_name].find({"scheduled_at": {"$gte": start_migration_date}})
                        all_docs = await cursor.to_list(length=None)

                        deduped_map = {}
                        for doc in all_docs:
                            if "_id" in doc: del doc["_id"]
                            key = (
                                doc.get("scheduled_at"),
                                doc.get("time_block"),
                                doc.get("type"),
                                doc.get("group"),
                                doc.get("bid_type"),
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
                    except Exception as e:
                        logger.warning(f"Rename failed (View?). Dropping view: {main_coll_name}. Error: {e}")
                        await self.db.drop_collection(main_coll_name)

                    # 3. Create Normal
                    await self.db.create_collection(main_coll_name)

                    # 4. Restore
                    if snapshot_data:
                        batch_size = 1000
                        for i in range(0, len(snapshot_data), batch_size):
                             batch = snapshot_data[i:i+batch_size]
                             await self.db[main_coll_name].insert_many(batch)
                        logger.info(f"Bid Migration Complete. {len(snapshot_data)} records.")

            elif not existing_coll_info:
                await self.db.create_collection(main_coll_name)

            self.serve_collection = self.db[main_coll_name]

        except Exception as e:
            logger.error("Error ensuring bid collections: %s", str(e))


    async def bulk_insert_bid_trade_serve_with_deduplication(self, entities: List[BidTradeServeEntity]) -> Dict[str, Any]:
        """
        Bulk insert bid trade entities using Optimized Bulk Upsert.
        """
        try:
            if not entities:
                 return {"inserted_count": 0}

            # Convert to dicts if needed
            records = [e.model_dump(by_alias=True) for e in entities]
            
            # Unique Key: scheduled_at, time_block, type, group, bid_type
            unique_key = ["scheduled_at", "time_block", "type", "group", "bid_type"]
            
            result = self.bulk_upsert_optimized(records, unique_key)
            
            return {
                "inserted_count": result.get("inserted_count", 0),
                "modified_count": result.get("modified_count", 0),
                "upserted_count": result.get("upserted_count", 0),
                "total_processed": result.get("total_processed", 0)
            }

        except Exception as e:
            logger.error("Error bulk upserting bid: %s", str(e))
            raise

    async def bulk_upsert_by_order_id(self, entities: List[BidTradeServeEntity]) -> Dict[str, Any]:
        """
        Bulk upsert bid trade entities using OrderId as the unique key.
        """
        try:
            if not entities:
                 return {"inserted_count": 0}

            # Convert to dicts
            records = [e.model_dump(by_alias=True) for e in entities]

            # Unique Key: order_id
            # filtered records to ensure order_id exists
            valid_records = [r for r in records if r.get("order_id")]
            if not valid_records:
                logger.warning("No records with order_id found for upsert")
                return {"inserted_count": 0}

            unique_key = ["order_id"]

            result = await self.bulk_upsert_optimized(valid_records, unique_key)

            return {
                "inserted_count": result.get("inserted_count", 0),
                "modified_count": result.get("modified_count", 0),
                "upserted_count": result.get("upserted_count", 0),
                "total_processed": result.get("total_processed", 0)
            }

        except Exception as e:
            logger.error("Error bulk upserting bid by order_id: %s", str(e))
            raise

    async def bulk_insert(self, bid_data: List[Any]) -> int:
        res = await self.bulk_insert_bid_trade_serve_with_deduplication(bid_data)
        return res.get("total_processed", 0)

    async def process_daily_archival_and_cleanup(self) -> Dict[str, Any]:
        """
        End-of-Day Job for BidTrade.
        """
        try:
            today = datetime.now().date()
            yesterday = today - timedelta(days=1)
            cleanup_cutoff = today - timedelta(days=3)

            logger.info(f"Starting Bid archival for {yesterday}")

            start_yesterday = datetime.combine(yesterday, datetime.min.time())
            end_yesterday = datetime.combine(yesterday, datetime.max.time())

            cursor = self.serve_collection.find({
                "scheduled_at": {"$gte": start_yesterday, "$lte": end_yesterday}
            })

            all_docs = await cursor.to_list(length=None)
            archival_batch = []
            for doc in all_docs:
                hist_doc = dict(doc)
                if "_id" in hist_doc: del hist_doc["_id"]
                hist_doc["archived_at"] = datetime.now()
                hist_doc["archived_timestamp"] = datetime.now()
                archival_batch.append(hist_doc)

            archived_count = 0
            if archival_batch:
                try:
                    res = await self.historical_collection.insert_many(archival_batch, ordered=False)
                    archived_count = len(res.inserted_ids)
                except Exception as e:
                    if hasattr(e, 'details'):
                        archived_count = e.details.get('nInserted', 0)
                    logger.warning(f"Archival had some issues but processed {archived_count} records: {e}")

            logger.info(f"Archived {archived_count} bid records")

            logger.info(f"Cleaning up bid older than {cleanup_cutoff}")
            del_res = await self.serve_collection.delete_many({
                "scheduled_at": {"$lt": datetime.combine(cleanup_cutoff, datetime.min.time())}
            })

            return {
                "archived_count": archived_count,
                "deleted_count": del_res.deleted_count
            }

        except Exception as e:
            logger.error("Error in bid archival: %s", str(e))
            return {"error": str(e)}

    async def archive_old_bid_data(self, days_to_keep: int = 2) -> Dict[str, Any]:
        return await self.process_daily_archival_and_cleanup()

    async def bulk_insert_bid_trade_historical_with_deduplication(self, entities: List[BidTradeServeHistoricalEntity]) -> Dict[str, Any]:
        """
        Bulk insert bid historical records with robust error handling (ordered=False).
        """
        try:
            if not entities:
                 return {"inserted_count": 0, "error_count": 0}

            from motor.motor_asyncio import AsyncIOMotorCollection
            from pymongo import InsertOne
            from pymongo.errors import BulkWriteError

            inserts = [e.model_dump(by_alias=True) for e in entities]

            operations = [InsertOne(i) for i in inserts]

            try:
                result = await self.historical_collection.bulk_write(operations, ordered=False)
                inserted_count = result.inserted_count
                error_count = 0
            except BulkWriteError as bwe:
                inserted_count = bwe.details.get('nInserted', 0)
                error_count = len(bwe.details.get('writeErrors', []))
                logger.warning(f"Historical insert had {error_count} errors. {inserted_count} records inserted.")

            return {
                "inserted_count": inserted_count,
                 "error_count": error_count,
                 "errors": []
            }

        except Exception as e:
            logger.error("Error in bulk insert historical bid: %s", str(e))
            return {"inserted_count": 0, "error_count": 1, "errors": [str(e)]}
