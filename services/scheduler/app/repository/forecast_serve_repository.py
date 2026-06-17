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

# Safety batch size when reading one delivery day from live forecast_serve
_ARCHIVE_FETCH_BATCH_SIZE = 5000


def _parse_doc_updated_at(doc: Dict[str, Any]) -> datetime:
    value = doc.get("updated_at") or doc.get("created_at") or datetime.min
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min
    return datetime.min


def _count_types(docs: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for doc in docs:
        market_type = str(doc.get("type") or "unknown")
        counts[market_type] = counts.get(market_type, 0) + 1
    return counts


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

    async def _fetch_all_live_docs_for_day(
        self,
        range_start: datetime,
        range_end: datetime,
    ) -> List[Dict[str, Any]]:
        """Load every live forecast_serve document for one calendar day (no row cap)."""
        docs: List[Dict[str, Any]] = []
        cursor = self.collection.find(
            {"scheduled_at": {"$gte": range_start, "$lt": range_end}}
        ).batch_size(_ARCHIVE_FETCH_BATCH_SIZE)
        async for doc in cursor:
            docs.append(doc)
        return docs

    async def _list_live_delivery_days(self) -> List[str]:
        """Distinct YYYY-MM-DD delivery days present in live forecast_serve."""
        rows = await self.collection.aggregate(
            [
                {
                    "$project": {
                        "delivery_day": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$scheduled_at",
                            }
                        }
                    }
                },
                {"$group": {"_id": "$delivery_day"}},
                {"$sort": {"_id": 1}},
            ]
        ).to_list(length=1000)
        return [str(row["_id"]) for row in rows if row.get("_id")]

    async def archive_old_forecast_data(
        self, days_to_keep: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Mirror every document currently in forecast_serve into forecast_serve_historical,
        then delete live rows older than the rolling window (days_to_keep).

        All types and revisions are archived (IDF, IDF_final, DAF, etc.) per delivery day.
        Idempotent per revision key: skips only when historical already has the same
        revision with an equal or newer updated_at.
        """
        if days_to_keep is None:
            days_to_keep = getattr(settings, "FORECAST_SERVE_DAYS_TO_KEEP", 2)

        try:
            await self.ensure_historical_collection()

            today = datetime.now().date()
            delete_cutoff = today - timedelta(days=days_to_keep)

            logger.info(
                "Starting forecast_serve archival. Mirror all live rows to historical; "
                "delete live scheduled_at < %s",
                delete_cutoff,
            )

            start_time = datetime.now()

            delivery_days = await self._list_live_delivery_days()
            if not delivery_days:
                logger.info("No forecast_serve records found for archival")
                return {
                    "archived_count": 0,
                    "deleted_count": 0,
                    "replaced_count": 0,
                    "skipped_count": 0,
                    "failed_count": 0,
                    "message": "No records to archive",
                    "cutoff_date": delete_cutoff.isoformat(),
                }

            total_candidates = 0
            archived_count = 0
            replaced_count = 0
            skipped_count = 0
            failed_count = 0
            archived_by_type: Dict[str, int] = {}
            candidate_by_type: Dict[str, int] = {}

            for delivery_day in delivery_days:
                range_start = datetime.strptime(delivery_day, "%Y-%m-%d")
                range_end = range_start + timedelta(days=1)
                archival_candidates = await self._fetch_all_live_docs_for_day(
                    range_start, range_end
                )

                if not archival_candidates:
                    continue

                total_candidates += len(archival_candidates)
                for market_type, count in _count_types(archival_candidates).items():
                    candidate_by_type[market_type] = (
                        candidate_by_type.get(market_type, 0) + count
                    )

                existing_map: Dict[tuple, Dict[str, Any]] = {}
                existing_hist_cursor = self.historical_collection.find(
                    {"scheduled_at": {"$gte": range_start, "$lt": range_end}},
                    {
                        "scheduled_at": 1,
                        "time_block": 1,
                        "type": 1,
                        "source": 1,
                        "forecast_revision": 1,
                        "updated_at": 1,
                        "_id": 1,
                    },
                )
                async for doc in existing_hist_cursor:
                    existing_map[forecast_serve_revision_key(doc)] = doc

                historical_inserts: List[Dict[str, Any]] = []
                keys_to_replace: List[tuple] = []
                now = datetime.now()

                for doc in archival_candidates:
                    key = forecast_serve_revision_key(doc)
                    existing = existing_map.get(key)
                    source_updated_at = _parse_doc_updated_at(doc)

                    if existing:
                        hist_updated_at = _parse_doc_updated_at(existing)
                        if source_updated_at <= hist_updated_at:
                            skipped_count += 1
                            continue
                        keys_to_replace.append(key)

                    hist_doc = dict(doc)
                    hist_doc.pop("_id", None)
                    hist_doc["archived_at"] = now
                    hist_doc["archived_timestamp"] = now
                    if doc.get("scheduled_at"):
                        hist_doc["schedule_date"] = doc["scheduled_at"].strftime(
                            "%Y-%m-%d"
                        )
                    historical_inserts.append(hist_doc)

                if keys_to_replace:
                    replace_filter = {
                        "$or": [
                            {
                                "scheduled_at": key[0],
                                "time_block": key[1],
                                "type": key[2],
                                "source": key[3],
                                "forecast_revision": key[4],
                            }
                            for key in keys_to_replace
                        ]
                    }
                    try:
                        del_result = await self.historical_collection.delete_many(
                            replace_filter
                        )
                        replaced_count += del_result.deleted_count
                    except Exception as del_err:
                        logger.error(
                            "Error deleting stale historical forecast records for %s: %s",
                            delivery_day,
                            del_err,
                        )

                if historical_inserts:
                    day_archived = 0
                    try:
                        res = await self.historical_collection.insert_many(
                            historical_inserts, ordered=False
                        )
                        day_archived = len(res.inserted_ids)
                    except Exception as e:
                        logger.error(
                            "Bulk insert error in forecast archival for %s: %s",
                            delivery_day,
                            e,
                        )
                        day_archived = (
                            e.details.get("nInserted", 0)
                            if hasattr(e, "details") and e.details
                            else 0
                        )
                    day_failed = len(historical_inserts) - day_archived
                    failed_count += max(0, day_failed)
                    archived_count += day_archived
                    for market_type, count in _count_types(historical_inserts).items():
                        archived_by_type[market_type] = (
                            archived_by_type.get(market_type, 0) + count
                        )

            logger.info(
                "forecast_serve archival candidates by type: %s",
                candidate_by_type,
            )
            logger.info(
                "Archived %d of %d forecast_serve records to historical "
                "(skipped %d unchanged, failed %d, by type: %s, replaced %d)",
                archived_count,
                total_candidates,
                skipped_count,
                failed_count,
                archived_by_type,
                replaced_count,
            )
            if archived_count + skipped_count + failed_count != total_candidates:
                logger.warning(
                    "forecast_serve archival count mismatch: candidates=%d "
                    "archived=%d skipped=%d failed=%d",
                    total_candidates,
                    archived_count,
                    skipped_count,
                    failed_count,
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
                "replaced_count": replaced_count,
                "skipped_count": skipped_count,
                "failed_count": failed_count,
                "deleted_count": deleted_count,
                "archived_by_type": archived_by_type,
                "candidate_by_type": candidate_by_type,
                "delivery_days_processed": len(delivery_days),
                "cutoff_date": delete_cutoff.isoformat(),
                "execution_time": (datetime.now() - start_time).total_seconds(),
            }
        except Exception as e:
            logger.error("Error in forecast_serve archival: %s", e)
            return {"error": str(e), "status": "error"}


forecast_serve_repository = ForecastServeRepository()
