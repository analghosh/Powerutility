"""
Weather Serve Repository
Handles database operations for weather serve data with timeseries support
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from bson import ObjectId
from pymongo import UpdateOne
from pymongo.errors import OperationFailure

from libs.database.base_serve_repository import BaseServeRepository
from libs.models.weather_serve_entity import WeatherServeEntity, WeatherServeHistoricalEntity
from ..core.config import settings

logger = logging.getLogger(__name__)


class WeatherServeRepository(BaseServeRepository):
    """Repository for weather serve data operations with timeseries support"""

    def __init__(self):
        """Initialize the weather serve repository"""
        super().__init__(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE,
            serve_collection=settings.MONGODB_WEATHER_SERVE_COLLECTION,
            historical_collection=settings.MONGODB_WEATHER_SERVE_HISTORICAL_COLLECTION
        )

    async def create_indexes(self):
        """Create unique indexes for the collection"""
        await self.serve_collection.create_index(
            [
                ("valid_time_utc", 1),
                ("type", 1),
                ("expiration_time_utc", 1),
                ("valid_time_local", 1)
            ],
            unique=True,
            name="idx_unique_weather_key",
            background=True
        )

    async def _ensure_weather_timeseries_collections(self):
        """
        Ensure collections exist.
        Migrate 'weather_serve' from Time-Series to Normal if needed.
        """
        try:
            # 1. Historical
            existing_names = await self.db.list_collection_names()
            if "weather_serve_historical" not in existing_names:
                await self.db.create_collection(
                    "weather_serve_historical",
                    timeseries={
                        "timeField": "archived_timestamp",
                        "metaField": "data_type",
                        "granularity": "hours",
                    },
                )

            # 2. Main (Safe Migration)
            main_coll_name = "weather_serve"
            existing_colls = await self.db.list_collections(filter={"name": main_coll_name}).to_list(length=10000)
            existing_coll_info = existing_colls[0] if existing_colls else None

            if existing_coll_info:
                is_view_type = existing_coll_info.get("type") == "view"
                options = existing_coll_info.get("options", {})
                is_view_options = "viewOn" in options or "pipeline" in options
                is_view = is_view_type or is_view_options

                is_timeseries = "timeseries" in options

                if is_view or is_timeseries:
                    logger.warning(f"Collection '{main_coll_name}' is {'VIEW' if is_view else 'Time-Series'}. Starting SAFE MIGRATION...")

                    start_migration_date = datetime.now() - timedelta(days=5)

                    # Snapshot and Deduplicate
                    snapshot_data = []
                    try:
                        cursor = self.db[main_coll_name].find({"timestamp": {"$gte": start_migration_date}})
                        all_docs = await cursor.to_list(length=100000)

                        deduped_map = {}
                        for doc in all_docs:
                            if "_id" in doc: del doc["_id"]
                            key = (
                                doc.get("valid_time_utc"),
                                doc.get("type"),
                                doc.get("expiration_time_utc"),
                                doc.get("valid_time_local")
                            )
                            deduped_map[key] = doc

                        snapshot_data = list(deduped_map.values())
                        logger.info(f"Loaded {len(snapshot_data)} unique records for migration.")
                    except Exception as e:
                        logger.error(f"Error snapshotting data: {e}")

                    # Rename or Drop
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_name = f"{main_coll_name}_ts_backup_{timestamp}"
                    try:
                        await self.db[main_coll_name].rename(backup_name)
                    except Exception as e:
                         logger.warning(f"Rename failed (View?). Dropping view: {main_coll_name}. Error: {e}")
                         await self.db.drop_collection(main_coll_name)

                    # Create Normal
                    await self.db.create_collection(main_coll_name)

                    # Restore
                    if snapshot_data:
                        batch_size = 1000
                        for i in range(0, len(snapshot_data), batch_size):
                             batch = snapshot_data[i:i+batch_size]
                             await self.db[main_coll_name].insert_many(batch)
                        logger.info(f"Weather Migration Complete. {len(snapshot_data)} records.")

            elif not existing_coll_info:
                await self.db.create_collection(main_coll_name)
                await self.db[main_coll_name].create_index(
                    [("valid_time_utc", 1), ("type", 1), ("expiration_time_utc", 1), ("valid_time_local", 1)],
                    unique=True,
                    name="idx_unique_weather_key"
                )

        except Exception as e:
            logger.error("Error creating weather collections: %s", str(e))


    async def bulk_upsert_weather_serve(self, weather_data: List[Any]) -> Dict[str, Any]:
        """
        Bulk upsert weather data using Optimized Bulk Upsert.
        """
        try:
            logger.info('wweather_datae', weather_data)
            import json
            logger.info("weather_data: ----------------------------------------- %s", weather_data)
            if not weather_data:
                return {"inserted_count": 0}

            records = []
            for e in weather_data:
                 if hasattr(e, "model_dump"): records.append(e.model_dump(by_alias=False,exclude_none=False))
                 elif hasattr(e, "dict"): records.append(e.dict(by_alias=False))
                 else: records.append(e)
            
            # Unique Key: valid_time_utc, type, expiration_time_utc, valid_time_local
            unique_key = ["valid_time_utc", "city"]
            logger.info("records: ----------------------------------------- %s", records)
            result = self.bulk_upsert_optimized(records, unique_key)
            
            return {
                "inserted_count": result.get("inserted_count", 0),
                "modified_count": result.get("modified_count", 0),
                "upserted_count": result.get("upserted_count", 0),
                "total_processed": result.get("total_processed", 0)
            }
        except Exception as e:
            logger.error("Error in bulk upsert weather: %s", str(e))
            raise

    async def bulk_insert(self, weather_data: List[Any]) -> int:
        res = await self.bulk_upsert_weather_serve(weather_data)
        return res.get("total_processed", 0)

    async def bulk_insert_weather_historical_with_deduplication(self, entities: List[Any]) -> Dict[str, Any]:
        """
        Bulk insert weather historical records with robust error handling (ordered=False).
        """
        try:
            if not entities:
                 return {"inserted_count": 0, "error_count": 0}

            from pymongo import InsertOne
            from pymongo.errors import BulkWriteError

            inserts = []
            for e in entities:
                if hasattr(e, "model_dump"): inserts.append(e.model_dump(by_alias=True))
                elif hasattr(e, "dict"): inserts.append(e.dict(by_alias=True))
                else: inserts.append(e)

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
            logger.error("Error in bulk insert historical weather: %s", str(e))
            return {"inserted_count": 0, "error_count": 1, "errors": [str(e)]}

    async def find_by_valid_time_utc(self, valid_time_utc: int, type: str) -> Optional[Dict[str, Any]]:
        """Find weather record by valid_time_utc."""
        try:
            return await self.serve_collection.find_one({"valid_time_utc": valid_time_utc, "type": type})
        except Exception as e:
            logger.error(f"Error finding weather record by valid_time_utc {valid_time_utc}: {str(e)}")
            return None

    async def delete_by_id(self, record_id: str) -> bool:
        """Delete weather record by ID."""
        try:
            result = await self.serve_collection.delete_one({"_id": ObjectId(record_id)})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting weather record {record_id}: {str(e)}")
            return False

    def delete_by_time_and_source(self, valid_time_local: str, source: str , city:str) -> int:
        """
        Delete weather records by valid_time_local and source.
        
        Args:
            valid_time_local: The local valid time string to match.
            source: The data source to match.
            
        Returns:
            The number of deleted documents.
        """
        try:
            query = {"valid_time_local": valid_time_local, "source": source, "city": city}
            result = self.serve_collection.delete_many(query)
            logger.debug(f"Deleted {result.deleted_count} weather records for time '{valid_time_local}' and source '{source}'.")
            return result.deleted_count
        except Exception as e:
            logger.error(f"Error deleting weather records for time '{valid_time_local}' and source '{source}': {str(e)}")
            return 0

    async def process_daily_archival_and_cleanup(self) -> Dict[str, Any]:
        """End-of-Day Job for Weather."""
        try:
            today = datetime.now().date()
            yesterday = today - timedelta(days=1)
            cleanup_cutoff = today - timedelta(days=3)

            logger.info(f"Starting Weather archival for {yesterday}")

            start_yesterday = datetime.combine(yesterday, datetime.min.time())
            end_yesterday = datetime.combine(yesterday, datetime.max.time())

            cursor = self.serve_collection.find({
                "timestamp": {"$gte": start_yesterday, "$lte": end_yesterday}
            })
            all_docs = await cursor.to_list(length=50000)

            archival_batch = []
            for doc in all_docs:
                hist_doc = dict(doc)
                if "_id" in hist_doc: del hist_doc["_id"]
                hist_doc["archived_at"] = datetime.now()
                hist_doc["archived_timestamp"] = datetime.now()
                archival_batch.append(hist_doc)

            archived_count = 0
            if archival_batch:
                unique_key = ["valid_time_utc", "type", "expiration_time_utc", "valid_time_local"]
                ops = [
                    UpdateOne({k: d.get(k) for k in unique_key}, {"$setOnInsert": d}, upsert=True)
                    for d in archival_batch
                ]
                res = await self.historical_collection.bulk_write(ops, ordered=False)
                archived_count = res.upserted_count

            logger.info(f"Archived {archived_count} weather records")

            logger.info(f"Cleaning up weather older than {cleanup_cutoff}")
            del_res = await self.serve_collection.delete_many({
                "timestamp": {"$lt": datetime.combine(cleanup_cutoff, datetime.min.time())}
            })

            return {
                "archived_count": archived_count,
                "deleted_count": del_res.deleted_count
            }

        except Exception as e:
            logger.error("Error in weather archival: %s", str(e))
            return {"error": str(e)}

    async def archive_old_weather_data(self, days_to_keep: int = 2) -> Dict[str, Any]:
        return await self.process_daily_archival_and_cleanup()
