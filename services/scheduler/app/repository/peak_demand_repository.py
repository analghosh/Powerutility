"""
Repository for peak_demand_serve collection (normal collection, not timeseries).
Handles safe migration from timeseries/view to normal collection.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List
from pymongo import MongoClient

from libs.database.base_serve_repository import BaseServeRepository
from libs.models.peak_demand_serve_entity import PeakDemandServeEntity
from services.scheduler.app.core.config import settings

logger = logging.getLogger(__name__)


class PeakDemandRepository(BaseServeRepository):
    """Repository managing peak_demand_serve as a normal collection with archival."""

    def __init__(self):
        super().__init__(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE,
            serve_collection="peak_demand_serve",
            historical_collection="peak_demand_serve_historical",
        )
        self._ensure_collections()

    def _ensure_collections(self):
        """
        Ensure collections exist.
        Migrate peak_demand_serve from timeseries/view to normal collection if needed.
        Keep peak_demand_serve_historical as timeseries.
        """
        try:
            # --- Historical collection (keep as timeseries) ---
            if "peak_demand_serve_historical" not in self.db.list_collection_names():
                self.db.create_collection(
                    "peak_demand_serve_historical",
                    timeseries={
                        "timeField": "timestamp",
                        "metaField": None,
                        "granularity": "minutes",
                    },
                    expireAfterSeconds=None,
                )
                logger.info("Created time series collection: peak_demand_serve_historical")

            # --- Main collection: ensure it is a normal collection ---
            main_coll_name = "peak_demand_serve"
            existing_colls = list(self.db.list_collections(filter={"name": main_coll_name}))
            existing_coll_info = existing_colls[0] if existing_colls else None

            if existing_coll_info:
                is_view_type = existing_coll_info.get("type") == "view"
                options = existing_coll_info.get("options", {})
                is_view_options = "viewOn" in options or "pipeline" in options
                is_view = is_view_type or is_view_options
                is_timeseries = "timeseries" in options

                if is_view or is_timeseries:
                    logger.warning(
                        "⚠️ Collection '%s' is %s. Starting SAFE MIGRATION...",
                        main_coll_name, "VIEW" if is_view else "Time-Series"
                    )

                    # Snapshot existing data
                    snapshot_data = []
                    try:
                        cursor = self.db[main_coll_name].find({})
                        deduped_map = {}
                        for doc in cursor:
                            if "_id" in doc:
                                del doc["_id"]
                            # Remove type and timestamp fields during migration
                            doc.pop("type", None)
                            doc.pop("timestamp", None)
                            key = doc.get("scheduled_at")
                            if key:
                                deduped_map[key] = doc
                        snapshot_data = list(deduped_map.values())
                        logger.info("Loaded %d unique records for migration.", len(snapshot_data))
                    except Exception as e:
                        logger.error("Error snapshotting data: %s", e)

                    # Rename or drop old collection
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_name = f"{main_coll_name}_ts_backup_{timestamp}"
                    try:
                        self.db[main_coll_name].rename(backup_name)
                        logger.info("Renamed old entity to: %s", backup_name)
                    except Exception as e:
                        error_msg = str(e)
                        if ("CommandNotSupportedOnView" in error_msg
                                or "cannot rename view" in error_msg
                                or (hasattr(e, 'code') and e.code == 166)):
                            logger.warning("Rename failed (VIEW). Dropping view: %s", main_coll_name)
                            self.db.drop_collection(main_coll_name)
                        else:
                            raise

                    # Create normal collection
                    self.db.create_collection(main_coll_name)
                    logger.info("Created normal collection: %s", main_coll_name)

                    # Restore data
                    if snapshot_data:
                        batch_size = 1000
                        for i in range(0, len(snapshot_data), batch_size):
                            batch = snapshot_data[i:i + batch_size]
                            self.db[main_coll_name].insert_many(batch)
                        logger.info("✅ Migration complete. Restored %d records.", len(snapshot_data))
                    else:
                        logger.info("✅ Migration complete. No records to restore.")

            elif not existing_coll_info:
                self.db.create_collection(main_coll_name)
                logger.info("Created normal collection: %s", main_coll_name)

            self.serve_collection = self.db[main_coll_name]

            # Create indexes
            self.serve_collection.create_index("scheduled_at", unique=True)
            self.serve_collection.create_index("created_at")

        except Exception as e:
            logger.error("Error ensuring peak_demand_serve collections: %s", e)

    def insert_one(self, entity: PeakDemandServeEntity) -> Dict[str, Any]:
        """Insert a single peak demand entity."""
        data = entity.model_dump(by_alias=True)
        result = self.serve_collection.insert_one(data)
        return {"inserted_id": str(result.inserted_id)}

    def upsert_by_date(self, target_date, entity: PeakDemandServeEntity) -> Dict[str, Any]:
        """
        Upsert a peak demand record for a specific date.
        If a record exists for the date, update it. Otherwise, insert a new one.
        """
        start_dt = datetime.combine(target_date, datetime.min.time())
        end_dt = datetime.combine(target_date, datetime.max.time())
        data = entity.model_dump(by_alias=True)
        data.pop("_id", None)  # Remove _id so MongoDB can handle it

        result = self.serve_collection.update_one(
            {"scheduled_at": {"$gte": start_dt, "$lte": end_dt}},
            {"$set": data},
            upsert=True,
        )
        return {
            "matched_count": result.matched_count,
            "modified_count": result.modified_count,
            "upserted_id": str(result.upserted_id) if result.upserted_id else None,
        }

    def delete_by_date(self, target_date) -> int:
        """Delete peak demand record for a specific date."""
        start_dt = datetime.combine(target_date, datetime.min.time())
        end_dt = datetime.combine(target_date, datetime.max.time())
        result = self.serve_collection.delete_many({
            "scheduled_at": {"$gte": start_dt, "$lte": end_dt}
        })
        return result.deleted_count

    def archive_old_records(self, retention_days: int = 31) -> Dict[str, Any]:
        """
        Move records older than retention_days from serve to historical collection.
        
        Args:
            retention_days: Number of days to retain in serve collection.
            
        Returns:
            Dict with archived_count and deleted_count.
        """
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        query = {"scheduled_at": {"$lt": cutoff_date}}

        try:
            # Find old records
            cursor = self.serve_collection.find(query)
            try:
                old_records = list(cursor)
            finally:
                cursor.close()

            if not old_records:
                logger.info("No peak demand records older than %d days to archive.", retention_days)
                return {"archived_count": 0, "deleted_count": 0}

            # Prepare records for historical — add timestamp for timeseries
            historical_docs = []
            for doc in old_records:
                hist_doc = dict(doc)
                hist_doc.pop("_id", None)
                hist_doc["timestamp"] = hist_doc.get("scheduled_at", datetime.now())
                hist_doc["archived_at"] = datetime.now()
                historical_docs.append(hist_doc)

            # Insert into historical
            self.historical_collection.insert_many(historical_docs)
            archived_count = len(historical_docs)
            logger.info("Archived %d peak demand records to historical.", archived_count)

            # Delete from serve
            result = self.serve_collection.delete_many(query)
            deleted_count = result.deleted_count
            logger.info("Deleted %d old peak demand records from serve.", deleted_count)

            return {"archived_count": archived_count, "deleted_count": deleted_count}

        except Exception as e:
            logger.error("Error archiving old peak demand records: %s", e)
            return {"archived_count": 0, "deleted_count": 0, "error": str(e)}
