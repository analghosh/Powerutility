"""
Demand Serve Historical service — query demand_serve_historical by date and time block range.
"""

import logging
from typing import Any, Dict, List

from ..repository.demand_serve_repository import DemandServeRepository

logger = logging.getLogger(__name__)


class DemandServeHistoricalService:
    def __init__(self):
        self.repository = DemandServeRepository()

    async def get_historical_by_blocks(
        self,
        schedule_date: str,
        from_block: int,
        to_block: int,
    ) -> Dict[str, Any]:
        if from_block > to_block:
            raise ValueError("from_block must be less than or equal to to_block")

        records: List[Dict[str, Any]] = (
            await self.repository.find_historical_by_date_and_blocks(
                schedule_date=schedule_date,
                from_block=from_block,
                to_block=to_block,
            )
        )

        return {
            "schedule_date": schedule_date,
            "type": "IDF",
            "from_block": from_block,
            "to_block": to_block,
            "count": len(records),
            "records": records,
        }


demand_serve_historical_service = DemandServeHistoricalService()
