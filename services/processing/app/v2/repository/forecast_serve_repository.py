"""
Forecast Serve Repository — read forecast_serve collection (IDF forecasts per source).
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from libs.helpers.forecast_serve_query import (
    FORECAST_SERVE_READ_PROJECTION,
    apply_latest_revision_to_filter,
    build_day_source_match,
    build_forecast_serve_latest_revision_range_pipeline,
    build_latest_revision_or_filter,
    fetch_latest_forecast_revision,
)
from libs.helpers.historical_date_split import split_date_range_by_today, today_date_str
from libs.models.forecast_serve_entity import FORECAST_SOURCE_CSV, FORECAST_SOURCE_RTM_API

from ...core.config import settings
from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

IDF_TYPE = "IDF"

# 90 days × 96 blocks × 2 sources
_FORECAST_SERVE_RANGE_MAX_DOCS = 90 * 96 * 2


class ForecastServeRepository(GenericRepository):
    """Async repository for forecast_serve collection."""

    def __init__(self):
        super().__init__()
        self.collection_name = settings.MONGODB_FORECAST_SERVE_COLLECTION
        self.historical_collection_name = getattr(
            settings,
            "MONGODB_FORECAST_SERVE_HISTORICAL_COLLECTION",
            "forecast_serve_historical",
        )
        logger.info(
            "ForecastServeRepository initialized with collection: %s",
            self.collection_name,
        )

    @property
    def _collection(self):
        return self.db[self.collection_name]

    @property
    def _historical_collection(self):
        return self.db[self.historical_collection_name]

    async def _get_latest_forecast_revision_on_collection(
        self,
        collection,
        scheduled_at: datetime,
        source: str,
        market_type: str = IDF_TYPE,
        scheduled_at_end: Optional[datetime] = None,
    ):
        return await fetch_latest_forecast_revision(
            collection,
            scheduled_at,
            source,
            market_type,
            scheduled_at_end,
        )

    async def get_latest_forecast_revision(
        self,
        scheduled_at: datetime,
        source: str,
        market_type: str = IDF_TYPE,
        scheduled_at_end: Optional[datetime] = None,
    ):
        return await self._get_latest_forecast_revision_on_collection(
            self._collection,
            scheduled_at,
            source,
            market_type,
            scheduled_at_end,
        )

    async def _find_range_on_collection(
        self,
        collection,
        from_date: str,
        to_date: str,
        from_block: int,
        to_block: int,
        market_type: str = IDF_TYPE,
    ) -> List[Dict[str, Any]]:
        range_start = datetime.strptime(from_date, "%Y-%m-%d")
        range_end_exclusive = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
        pipeline = build_forecast_serve_latest_revision_range_pipeline(
            range_start=range_start,
            range_end_exclusive=range_end_exclusive,
            from_block=from_block,
            to_block=to_block,
            market_type=market_type,
        )
        cursor = collection.aggregate(pipeline)
        return await cursor.to_list(length=_FORECAST_SERVE_RANGE_MAX_DOCS)

    async def _find_by_date_and_block_range_on_collection(
        self,
        collection,
        schedule_date: str,
        from_block: int,
        to_block: int,
        market_type: str = IDF_TYPE,
    ) -> List[Dict[str, Any]]:
        return await self._find_range_on_collection(
            collection,
            schedule_date,
            schedule_date,
            from_block,
            to_block,
            market_type,
        )

    async def find_by_date_and_block_range(
        self,
        schedule_date: str,
        from_block: int,
        to_block: int,
        market_type: str = IDF_TYPE,
    ) -> List[Dict[str, Any]]:
        """
        Return forecast_serve documents for a schedule date and inclusive time block range
        at the latest forecast_revision per source (filtered in MongoDB).

        Past dates read from forecast_serve_historical only.
        Current/future dates read from forecast_serve, with historical fallback if empty.
        """
        from libs.helpers.historical_date_split import is_past_date

        try:
            if is_past_date(schedule_date):
                return await self._find_by_date_and_block_range_on_collection(
                    self._historical_collection,
                    schedule_date,
                    from_block,
                    to_block,
                    market_type,
                )

            result = await self._find_by_date_and_block_range_on_collection(
                self._collection,
                schedule_date,
                from_block,
                to_block,
                market_type,
            )
            if result:
                return result

            return await self._find_by_date_and_block_range_on_collection(
                self._historical_collection,
                schedule_date,
                from_block,
                to_block,
                market_type,
            )
        except Exception as e:
            logger.error("Error finding forecast_serve by block range: %s", e)
            return []

    async def find_by_date_range_and_block_range(
        self,
        from_date: str,
        to_date: str,
        from_block: int,
        to_block: int,
        market_type: str = IDF_TYPE,
    ) -> List[Dict[str, Any]]:
        """
        Return forecast_serve documents for an inclusive date range and block range.

        Uses one aggregation per collection segment (live/historical) with latest
        forecast_revision resolved per (scheduled_at, time_block, source).
        """
        try:
            if from_date > to_date:
                return []

            today = today_date_str()
            past_from, past_to, current_from, current_to = split_date_range_by_today(
                from_date, to_date, today
            )

            merged: List[Dict[str, Any]] = []

            if past_from and past_to:
                merged.extend(
                    await self._find_range_on_collection(
                        self._historical_collection,
                        past_from,
                        past_to,
                        from_block,
                        to_block,
                        market_type,
                    )
                )

            if current_from and current_to:
                current_results = await self._find_range_on_collection(
                    self._collection,
                    current_from,
                    current_to,
                    from_block,
                    to_block,
                    market_type,
                )
                if current_results:
                    merged.extend(current_results)
                else:
                    merged.extend(
                        await self._find_range_on_collection(
                            self._historical_collection,
                            current_from,
                            current_to,
                            from_block,
                            to_block,
                            market_type,
                        )
                    )

            return merged
        except Exception as e:
            logger.error(
                "Error finding forecast_serve by date range %s to %s: %s",
                from_date,
                to_date,
                e,
            )
            return []

    async def find_by_scheduled_at_and_source(
        self,
        scheduled_at: datetime,
        source: str,
        market_type: str = IDF_TYPE,
    ) -> List[Dict[str, Any]]:
        """forecast_serve rows for one delivery date and source at latest forecast_revision."""
        end_date = scheduled_at + timedelta(days=1)
        try:
            latest = await self.get_latest_forecast_revision(
                scheduled_at, source, market_type, end_date
            )
            filt = apply_latest_revision_to_filter(
                build_day_source_match(
                    scheduled_at, source, market_type, end_date
                ),
                latest,
            )
            cursor = (
                self._collection.find(filt, projection=FORECAST_SERVE_READ_PROJECTION)
                .sort([("time_block", 1)])
            )
            return await cursor.to_list(length=200)
        except Exception as e:
            logger.error("Error finding forecast_serve by date/source: %s", e)
            return []


forecast_serve_repository = ForecastServeRepository()
