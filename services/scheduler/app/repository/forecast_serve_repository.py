"""
Scheduler Forecast Serve Repository
forecast_serve (rolling window) + forecast_serve_historical (archived).
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from libs.helpers.forecast_serve_query import forecast_serve_revision_key
from libs.models.forecast_serve_entity import ensure_forecast_serve_historical_indexes
from libs.repository.forecast_serve_repository import (
    ForecastServeRepository as LibForecastServeRepository,
)

from ..core.config import settings

logger = logging.getLogger(__name__)


class ForecastServeRepository(LibForecastServeRepository):
    """forecast_serve access using scheduler service database settings."""

    def __init__(self):
        super().__init__(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE,
            collection_name=settings.MONGODB_FORECAST_SERVE_COLLECTION,
        )
        self.historical_collection_name = (
            settings.MONGODB_FORECAST_SERVE_HISTORICAL_COLLECTION
        )
        self.historical_collection = self.db[self.historical_collection_name]

    async def ensure_historical_collection(self) -> None:
        """Create forecast_serve_historical as time-series if missing (same pattern as demand)."""
        try:
            existing_names = await self.db.list_collection_names()
            if self.historical_collection_name not in existing_names:
                await self.db.create_collection(
                    self.historical_collection_name,
                    timeseries={
                        "timeField": "archived_timestamp",
                        "metaField": "schedule_date",
                        "granularity": "hours",
                    },
                )
                logger.info(
                    "Created time-series collection: %s",
                    self.historical_collection_name,
                )
            await ensure_forecast_serve_historical_indexes(self.historical_collection)
        except Exception as e:
            logger.warning(
                "Failed to ensure forecast_serve_historical collection: %s", e
            )

    async def archive_old_forecast_data(
        self, days_to_keep: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Archive forecast_serve rows older than today into historical, then delete
        rows older than the rolling window (same pattern as demand_serve archival).
        """
        if days_to_keep is None:
            days_to_keep = getattr(settings, "FORECAST_SERVE_DAYS_TO_KEEP", 2)

        try:
            await self.ensure_historical_collection()

            today = datetime.now().date()
            archive_cutoff = today
            delete_cutoff = today - timedelta(days=days_to_keep)

            logger.info(
                "Starting forecast_serve archival. Archive < %s, Delete < %s",
                archive_cutoff,
                delete_cutoff,
            )

            start_time = datetime.now()

            cursor = self.collection.find({
                "scheduled_at": {
                    "$lt": datetime.combine(archive_cutoff, datetime.min.time())
                }
            })
            archival_candidates = await cursor.to_list(length=10000)

            if not archival_candidates:
                logger.info("No forecast_serve records found for archival")
                return {
                    "archived_count": 0,
                    "deleted_count": 0,
                    "message": "No records to archive",
                    "cutoff_date": delete_cutoff.isoformat(),
                }

            logger.info(
                "Found %d forecast_serve records to potentially archive",
                len(archival_candidates),
            )

            min_date = min(d["scheduled_at"] for d in archival_candidates)
            max_date = max(d["scheduled_at"] for d in archival_candidates)

            existing_keys = set()
            existing_hist_cursor = self.historical_collection.find(
                {"scheduled_at": {"$gte": min_date, "$lte": max_date}},
                {
                    "scheduled_at": 1,
                    "time_block": 1,
                    "type": 1,
                    "source": 1,
                    "forecast_revision": 1,
                    "_id": 0,
                },
            )
            async for doc in existing_hist_cursor:
                existing_keys.add(forecast_serve_revision_key(doc))

            historical_inserts: List[Dict[str, Any]] = []
            for doc in archival_candidates:
                key = forecast_serve_revision_key(doc)
                if key in existing_keys:
                    continue
                hist_doc = dict(doc)
                hist_doc.pop("_id", None)
                hist_doc["archived_at"] = datetime.now()
                hist_doc["archived_timestamp"] = datetime.now()
                if doc.get("scheduled_at"):
                    hist_doc["schedule_date"] = doc["scheduled_at"].strftime("%Y-%m-%d")
                historical_inserts.append(hist_doc)
                existing_keys.add(key)

            archived_count = 0
            if historical_inserts:
                try:
                    res = await self.historical_collection.insert_many(
                        historical_inserts, ordered=False
                    )
                    archived_count = len(res.inserted_ids)
                except Exception as e:
                    logger.error("Bulk insert error in forecast archival: %s", e)
                    if hasattr(e, "details"):
                        archived_count = e.details.get("nInserted", 0)

            logger.info(
                "Archived %d of %d forecast_serve records to historical (all revisions)",
                archived_count,
                len(archival_candidates),
            )

            delete_query = {
                "scheduled_at": {
                    "$lt": datetime.combine(delete_cutoff, datetime.min.time())
                }
            }
            del_res = await self.collection.delete_many(delete_query)
            deleted_count = del_res.deleted_count

            logger.info(
                "Cleaned up %d forecast_serve records older than %s",
                deleted_count,
                delete_cutoff,
            )

            return {
                "status": "success",
                "archived_count": archived_count,
                "deleted_count": deleted_count,
                "cutoff_date": delete_cutoff.isoformat(),
                "execution_time": (datetime.now() - start_time).total_seconds(),
            }
        except Exception as e:
            logger.error("Error in forecast_serve archival: %s", e)
            return {"error": str(e), "status": "error"}


forecast_serve_repository = ForecastServeRepository()
