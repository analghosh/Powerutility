"""
Forecast Serve Repository
Append-only inserts into forecast_serve — one document per source per block per revision.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from pymongo.errors import BulkWriteError

from libs.database.base_repository import BaseRepository
from libs.helpers.forecast_serve_query import (
    FORECAST_SERVE_READ_PROJECTION,
    apply_latest_revision_to_filter,
    build_day_source_match,
    build_latest_revision_or_filter,
    fetch_latest_forecast_revision,
)
from libs.models.forecast_serve_entity import (
    FORECAST_SOURCE_CSV,
    FORECAST_SOURCE_RTM_API,
    ensure_forecast_serve_indexes,
)

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION = "forecast_serve"
IDF_TYPE = "IDF"


class ForecastServeRepository(BaseRepository):
    """Repository for forecast_serve collection."""

    def __init__(
        self,
        mongodb_uri: str,
        database_name: str,
        collection_name: str = DEFAULT_COLLECTION,
    ):
        super().__init__(collection_name, mongodb_uri, database_name)

    async def create_indexes(self) -> None:
        await self._create_collection_if_not_exists()
        ensure_forecast_serve_indexes(self.collection)

    @staticmethod
    def _build_insert_document(
        scheduled_at: datetime,
        time_block: int,
        forecast: Optional[float],
        source: str,
        now: datetime,
        market_type: str = IDF_TYPE,
        forecasted_frequency: Optional[float] = None,
        forecast_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        doc: Dict[str, Any] = {
            "scheduled_at": scheduled_at,
            "time_block": int(time_block),
            "type": market_type,
            "source": source,
            "forecast": forecast,
            "created_at": now,
            "updated_at": now,
        }
        if forecasted_frequency is not None:
            doc["forecasted_frequency"] = forecasted_frequency
        if forecast_revision is not None:
            doc["forecast_revision"] = int(forecast_revision)
        return doc

    async def get_latest_forecast_revision(
        self,
        scheduled_at: datetime,
        source: str,
        market_type: str = IDF_TYPE,
        scheduled_at_end: Optional[datetime] = None,
    ) -> Optional[int]:
        """Max forecast_revision for a delivery day and source (indexed aggregation)."""
        try:
            return await fetch_latest_forecast_revision(
                self.collection,
                scheduled_at,
                source,
                market_type,
                scheduled_at_end,
            )
        except Exception as e:
            logger.error(
                "Error fetching latest forecast_revision scheduled_at=%s source=%s: %s",
                scheduled_at,
                source,
                e,
            )
            raise

    async def insert_forecast(
        self,
        scheduled_at: datetime,
        time_block: int,
        forecast: Optional[float],
        source: str,
        market_type: str = IDF_TYPE,
        forecast_revision: Optional[int] = None,
    ) -> bool:
        """Insert a single forecast document (append-only)."""
        now = datetime.now()
        doc = self._build_insert_document(
            scheduled_at,
            int(time_block),
            forecast,
            source,
            now,
            market_type,
            forecast_revision=forecast_revision,
        )
        try:
            result = await self.collection.insert_one(doc)
            return bool(result.inserted_id)
        except Exception as e:
            logger.error(
                "Error inserting forecast_serve scheduled_at=%s block=%s source=%s: %s",
                scheduled_at,
                time_block,
                source,
                e,
            )
            raise

    async def next_forecast_revision(
        self,
        scheduled_at: datetime,
        source: str,
        market_type: str = IDF_TYPE,
    ) -> int:
        """Next per-day ingest revision for (scheduled_at, source); starts at 1."""
        latest = await self.get_latest_forecast_revision(
            scheduled_at, source, market_type
        )
        return (latest or 0) + 1

    async def bulk_insert_forecasts(
        self,
        records: List[Dict[str, Any]],
        source: str,
        market_type: str = IDF_TYPE,
    ) -> Dict[str, int]:
        """
        Bulk insert forecasts (append-only). Each record must include scheduled_at,
        time_block, and forecast_revision for the ingest batch.
        """
        if not records:
            return {"inserted": 0, "upserted": 0, "modified": 0, "matched": 0}

        now = datetime.now()
        documents: List[Dict[str, Any]] = []
        for rec in records:
            scheduled_at = rec.get("scheduled_at")
            time_block = rec.get("time_block")
            if scheduled_at is None or time_block is None:
                continue
            revision = rec.get("forecast_revision")
            if revision is None:
                logger.warning(
                    "Skipping forecast_serve insert without forecast_revision: block=%s",
                    time_block,
                )
                continue
            freq = rec.get("forecasted_frequency")
            documents.append(
                self._build_insert_document(
                    scheduled_at,
                    int(time_block),
                    rec.get("forecast"),
                    source,
                    now,
                    market_type,
                    forecasted_frequency=freq if "forecasted_frequency" in rec else None,
                    forecast_revision=int(revision),
                )
            )

        if not documents:
            return {"inserted": 0, "upserted": 0, "modified": 0, "matched": 0}

        inserted = 0
        try:
            result = await self.collection.insert_many(documents, ordered=False)
            inserted = len(result.inserted_ids)
        except BulkWriteError as bwe:
            inserted = bwe.details.get("nInserted", 0) if bwe.details else 0
            logger.warning(
                "Partial forecast_serve insert source=%s inserted=%s error=%s",
                source,
                inserted,
                bwe.details,
            )
        except Exception as e:
            logger.error("Error bulk inserting forecast_serve source=%s: %s", source, e)
            raise

        return {
            "inserted": inserted,
            "upserted": inserted,
            "modified": 0,
            "matched": 0,
        }

    async def find_by_scheduled_at_and_source(
        self,
        scheduled_at: datetime,
        source: str,
        market_type: str = IDF_TYPE,
    ) -> List[Dict[str, Any]]:
        """forecast_serve docs for a date and source at the latest forecast_revision only."""
        try:
            latest = await self.get_latest_forecast_revision(
                scheduled_at, source, market_type
            )
            filt = apply_latest_revision_to_filter(
                build_day_source_match(scheduled_at, source, market_type),
                latest,
            )
            cursor = self.collection.find(filt, projection=FORECAST_SERVE_READ_PROJECTION)
            return await cursor.to_list(length=200)
        except Exception as e:
            logger.error("Error finding forecast_serve by date/source: %s", e)
            return []

    async def find_by_block(
        self,
        scheduled_at: datetime,
        time_block: int,
        market_type: str = IDF_TYPE,
    ) -> List[Dict[str, Any]]:
        """Return latest-revision forecast per source (0–2 docs) for one block."""
        try:
            docs: List[Dict[str, Any]] = []
            for src in (FORECAST_SOURCE_RTM_API, FORECAST_SOURCE_CSV):
                latest = await self.get_latest_forecast_revision(
                    scheduled_at, src, market_type
                )
                filt = apply_latest_revision_to_filter(
                    {
                        "scheduled_at": scheduled_at,
                        "time_block": int(time_block),
                        "type": market_type,
                        "source": src,
                    },
                    latest,
                )
                doc = await self.collection.find_one(
                    filt, projection=FORECAST_SERVE_READ_PROJECTION
                )
                if doc:
                    docs.append(doc)
            return docs
        except Exception as e:
            logger.error("Error finding forecast_serve by block: %s", e)
            return []

    async def find_by_date_block_range_latest_per_source(
        self,
        scheduled_at: datetime,
        scheduled_at_end: datetime,
        from_block: int,
        to_block: int,
        market_type: str = IDF_TYPE,
    ) -> List[Dict[str, Any]]:
        """Block-range read with DB filter on latest forecast_revision per source."""
        try:
            sources_latest = {
                FORECAST_SOURCE_RTM_API: await self.get_latest_forecast_revision(
                    scheduled_at,
                    FORECAST_SOURCE_RTM_API,
                    market_type,
                    scheduled_at_end,
                ),
                FORECAST_SOURCE_CSV: await self.get_latest_forecast_revision(
                    scheduled_at,
                    FORECAST_SOURCE_CSV,
                    market_type,
                    scheduled_at_end,
                ),
            }
            filt = build_latest_revision_or_filter(
                scheduled_at,
                scheduled_at_end,
                sources_latest,
                market_type,
                time_block_range=(from_block, to_block),
            )
            cursor = (
                self.collection.find(filt, projection=FORECAST_SERVE_READ_PROJECTION)
                .sort([("time_block", 1), ("source", 1)])
            )
            return await cursor.to_list(length=256)
        except Exception as e:
            logger.error("Error finding forecast_serve by block range: %s", e)
            return []
