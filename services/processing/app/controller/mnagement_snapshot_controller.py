# app/controller/management_snapshot_controller.py
import logging
from typing import Optional, Dict, Any, List
from fastapi import HTTPException
from datetime import date, timedelta

from ..services.management_snapshot_services import management_snapshot_services

logger = logging.getLogger(__name__)


class ManagementSnapshotController:
    def __init__(self, svc=management_snapshot_services):
        self.svc = svc


    async def demand_supply_snapshot(
        self,
        schedule_date: Optional[str],
        from_block: Optional[int],
        to_block: Optional[int],
    ) -> Dict[str, Any]:
        try:
            return await self.svc.demand_supply_snapshot(
                schedule_date=schedule_date,
                from_block=from_block,
                to_block=to_block,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "supply_demand_trade snapshot failed | schedule_date=%s from_block=%s to_block=%s",
                schedule_date, from_block, to_block
            )
            raise HTTPException(status_code=500, detail="Failed to fetch supply-demand-trade snapshot.")


    async def iex_dam_rtm_snapshot(
        self,
        schedule_date: Optional[str],
        from_block: Optional[int],
        to_block: Optional[int],
    ) -> Dict[str, Any]:
        try:
            return await self.svc.iex_dam_rtm_snapshot(
                schedule_date=schedule_date,
                from_block=from_block,
                to_block=to_block,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "management_snapshot failed | schedule_date=%s from_block=%s to_block=%s",
                schedule_date, from_block, to_block
            )
            raise HTTPException(status_code=500, detail="Failed to fetch management snapshot.")

    async def variable_cost_snapshot(
        self,
        schedule_date: Optional[str],
        from_block: Optional[int],
        to_block: Optional[int],
        exchange: Optional[str],
    ) -> Dict[str, Any]:
        try:
            return await self.svc.variable_cost_snapshot(
                schedule_date=schedule_date,
                from_block=from_block,
                to_block=to_block,
                exchange=exchange,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "market_book_snapshot failed | schedule_date=%s from_block=%s to_block=%s exchange=%s",
                schedule_date, from_block, to_block, exchange
            )
            raise HTTPException(status_code=500, detail="Failed to fetch market book snapshot.")


    async def variable_cost_snapshot_range(
        self,
        date_from: str,
        date_to: str,
        from_block: Optional[int],
        to_block: Optional[int],
        exchange: Optional[str],
    ) -> List[Dict[str, Any]]:
        try:
            d_from = date.fromisoformat(date_from)
            d_to = date.fromisoformat(date_to)
        except Exception:
            raise HTTPException(status_code=422, detail="Invalid date_from/date_to (YYYY-MM-DD)")

        if d_from > d_to:
            d_from, d_to = d_to, d_from

        out: List[Dict[str, Any]] = []
        cur = d_from
        while cur <= d_to:
            out.append(await self.svc.variable_cost_snapshot(
                schedule_date=cur.isoformat(),
                from_block=from_block,
                to_block=to_block,
                exchange=exchange,
            ))
            cur += timedelta(days=1)
        return out
    
# Global instance
management_snapshot_controller = ManagementSnapshotController()
