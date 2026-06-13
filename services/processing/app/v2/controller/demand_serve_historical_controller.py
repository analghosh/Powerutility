"""Controller for demand_serve_historical read API."""

import logging
from typing import Any, Dict

from ..services.demand_serve_historical_service import demand_serve_historical_service

logger = logging.getLogger(__name__)


class DemandServeHistoricalController:
    async def get_by_date_and_blocks(
        self,
        schedule_date: str,
        from_block: int,
        to_block: int,
    ) -> Dict[str, Any]:
        return await demand_serve_historical_service.get_historical_by_blocks(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
        )


demand_serve_historical_controller = DemandServeHistoricalController()
