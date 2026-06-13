"""
Demand Serve Historical service — query demand data by date range and time block range.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..repository.demand_serve_repository import DemandServeRepository

logger = logging.getLogger(__name__)


class DemandServeHistoricalService:
    def __init__(self):
        self.repository = DemandServeRepository()

    @staticmethod
    def _resolve_date_range(
        schedule_date: Optional[str],
        from_date: Optional[str],
        to_date: Optional[str],
    ) -> Tuple[str, str]:
        if schedule_date and (from_date or to_date):
            raise ValueError(
                "Provide either schedule_date or both from_date and to_date, not both"
            )
        if schedule_date:
            return schedule_date, schedule_date
        if from_date and to_date:
            if from_date > to_date:
                raise ValueError("from_date must be less than or equal to to_date")
            return from_date, to_date
        raise ValueError(
            "Provide schedule_date or both from_date and to_date"
        )

    async def get_historical_by_blocks(
        self,
        from_block: int,
        to_block: int,
        schedule_date: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        if from_block > to_block:
            raise ValueError("from_block must be less than or equal to to_block")

        resolved_from, resolved_to = self._resolve_date_range(
            schedule_date, from_date, to_date
        )

        records: List[Dict[str, Any]] = (
            await self.repository.find_historical_by_date_range(
                from_date=resolved_from,
                to_date=resolved_to,
                from_block=from_block,
                to_block=to_block,
            )
        )

        return {
            "from_date": resolved_from,
            "to_date": resolved_to,
            "type": "IDF",
            "from_block": from_block,
            "to_block": to_block,
            "count": len(records),
            "records": records,
        }


demand_serve_historical_service = DemandServeHistoricalService()
