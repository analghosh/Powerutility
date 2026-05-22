"""Controller for forecast_serve read API."""

import logging
from typing import Any, Dict

from ..services.forecast_serve_service import forecast_serve_service

logger = logging.getLogger(__name__)


class ForecastServeController:
    async def get_by_date_and_blocks(
        self,
        schedule_date: str,
        from_block: int,
        to_block: int,
    ) -> Dict[str, Any]:
        return await forecast_serve_service.get_forecast_serve_by_blocks(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
        )


forecast_serve_controller = ForecastServeController()
