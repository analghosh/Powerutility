"""
Forecast Serve Repository — read forecast_serve collection (IDF forecasts per source).
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

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

    async def find_by_date_and_block_range(
        self,
        schedule_date: str,
        from_block: int,
        to_block: int,
        market_type: str = IDF_TYPE,
    ) -> List[Dict[str, Any]]:
        """
        Return forecast_serve documents for a schedule date and inclusive time block range.
        """
        target_date = datetime.strptime(schedule_date, "%Y-%m-%d")
        end_date = target_date + timedelta(days=1)
        query: Dict[str, Any] = {
            "scheduled_at": {"$gte": target_date, "$lt": end_date},
            "type": market_type,
            "time_block": {"$gte": int(from_block), "$lte": int(to_block)},
        }
        return await self.get_all(
            self.collection_name,
            filter=query,
            sort=[("time_block", 1), ("source", 1)],
            limit=256,
        )

    async def find_by_scheduled_at_and_source(
        self,
        scheduled_at: datetime,
        source: str,
        market_type: str = IDF_TYPE,
    ) -> List[Dict[str, Any]]:
        """All forecast_serve rows for one delivery date and source."""
        end_date = scheduled_at + timedelta(days=1)
        query: Dict[str, Any] = {
            "scheduled_at": {"$gte": scheduled_at, "$lt": end_date},
            "type": market_type,
            "source": source,
        }
        return await self.get_all(
            self.collection_name,
            filter=query,
            sort=[("time_block", 1)],
            limit=500,
        )


forecast_serve_repository = ForecastServeRepository()
