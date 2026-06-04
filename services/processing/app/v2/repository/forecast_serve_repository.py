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
    build_latest_revision_or_filter,
    fetch_latest_forecast_revision,
)
from libs.models.forecast_serve_entity import FORECAST_SOURCE_CSV, FORECAST_SOURCE_RTM_API

from ...core.config import settings
from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

IDF_TYPE = "IDF"


class ForecastServeRepository(GenericRepository):
    """Async repository for forecast_serve collection."""

    def __init__(self):
        super().__init__()
        self.collection_name = settings.MONGODB_FORECAST_SERVE_COLLECTION
        logger.info(
            "ForecastServeRepository initialized with collection: %s",
            self.collection_name,
        )

    @property
    def _collection(self):
        return self.db[self.collection_name]

    async def get_latest_forecast_revision(
        self,
        scheduled_at: datetime,
        source: str,
        market_type: str = IDF_TYPE,
        scheduled_at_end: Optional[datetime] = None,
    ):
        return await fetch_latest_forecast_revision(
            self._collection,
            scheduled_at,
            source,
            market_type,
            scheduled_at_end,
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
        """
        target_date = datetime.strptime(schedule_date, "%Y-%m-%d")
        end_date = target_date + timedelta(days=1)
        try:
            sources_latest = {
                FORECAST_SOURCE_RTM_API: await self.get_latest_forecast_revision(
                    target_date,
                    FORECAST_SOURCE_RTM_API,
                    market_type,
                    end_date,
                ),
                FORECAST_SOURCE_CSV: await self.get_latest_forecast_revision(
                    target_date,
                    FORECAST_SOURCE_CSV,
                    market_type,
                    end_date,
                ),
            }
            filt = build_latest_revision_or_filter(
                target_date,
                end_date,
                sources_latest,
                market_type,
                time_block_range=(from_block, to_block),
            )
            cursor = (
                self._collection.find(filt, projection=FORECAST_SERVE_READ_PROJECTION)
                .sort([("time_block", 1), ("source", 1)])
            )
            return await cursor.to_list(length=256)
        except Exception as e:
            logger.error("Error finding forecast_serve by block range: %s", e)
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
