"""
Forecast Serve service — query forecast_serve by date and time block range.
"""

import logging
from typing import Any, Dict, List

from libs.models.forecast_serve_entity import (
    FORECAST_SOURCE_CSV,
    FORECAST_SOURCE_DISPLAY_AUTO,
    FORECAST_SOURCE_DISPLAY_MANUAL,
    FORECAST_SOURCE_RTM_API,
)
from ..repository.forecast_serve_repository import forecast_serve_repository

logger = logging.getLogger(__name__)


class ForecastServeService:
    def __init__(self):
        self.repository = forecast_serve_repository

    @staticmethod
    def _format_block_record(doc: Dict[str, Any]) -> Dict[str, Any]:
        """Block-level fields only (source label lives on auto/manual parent)."""
        return {
            "id": doc.get("_id") or doc.get("id"),
            "scheduled_at": doc.get("scheduled_at"),
            "time_block": doc.get("time_block"),
            "type": doc.get("type"),
            "forecast": doc.get("forecast"),
            "forecasted_frequency": doc.get("forecasted_frequency"),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
        }

    async def get_forecast_serve_by_blocks(
        self,
        schedule_date: str,
        from_block: int,
        to_block: int,
    ) -> Dict[str, Any]:
        if from_block > to_block:
            raise ValueError("from_block must be less than or equal to to_block")

        raw = await self.repository.find_by_date_and_block_range(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
        )

        auto_records: List[Dict[str, Any]] = []
        manual_records: List[Dict[str, Any]] = []

        for doc in raw:
            block = self._format_block_record(doc)
            source = doc.get("source")
            if source == FORECAST_SOURCE_RTM_API:
                auto_records.append(block)
            elif source == FORECAST_SOURCE_CSV:
                manual_records.append(block)

        return {
            "schedule_date": schedule_date,
            "from_block": from_block,
            "to_block": to_block,
            "auto": {
                "source": FORECAST_SOURCE_RTM_API,
                "forecast_source": FORECAST_SOURCE_DISPLAY_AUTO,
                "count": len(auto_records),
                "records": auto_records,
            },
            "manual": {
                "source": FORECAST_SOURCE_CSV,
                "forecast_source": FORECAST_SOURCE_DISPLAY_MANUAL,
                "count": len(manual_records),
                "records": manual_records,
            },
        }


forecast_serve_service = ForecastServeService()
