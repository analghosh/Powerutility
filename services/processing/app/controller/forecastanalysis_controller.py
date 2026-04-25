import logging
from typing import Optional, List, Dict, Any, Literal

from fastapi import HTTPException
from ..services.forecastanalysis_services import demand_forecast_analysis_services

logger = logging.getLogger(__name__)


class DemandController:
    def __init__(self, demand_services = demand_forecast_analysis_services):
        self.demand_services = demand_services

    async def demand_blocks(
        self,
        markets: Optional[List[str]],
        date_from: Optional[str],
        date_to: Optional[str],
        from_block: Optional[int],
        to_block: Optional[int],
        group_by_date: bool,
        zones: Optional[List[str]] = None,   # NEW
    ) -> Dict[str, Any]:
        try:
            return await self.demand_services.demand_blocks(
                markets=markets,
                date_from=date_from,
                date_to=date_to,
                from_block=from_block,
                to_block=to_block,
                group_by_date=group_by_date,
                zones=zones,                  # pass through
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "demand_blocks failed | markets=%s date_from=%s date_to=%s from_block=%s to_block=%s group_by_date=%s zones=%s",
                markets, date_from, date_to, from_block, to_block, group_by_date, zones
            )
            raise HTTPException(status_code=500, detail="Failed to fetch demand blocks.")
        

    async def demand_curve(
        self,
        markets: Optional[List[str]],
        date_from: Optional[str],
        date_to: Optional[str],
        from_block: Optional[int],
        to_block: Optional[int],
        denominator: str,
        zones: Optional[List[str]],
    ) -> Dict[str, Any]:
        try:
            return await self.demand_services.demand_curve(
                markets=markets,
                date_from=date_from,
                date_to=date_to,
                from_block=from_block,
                to_block=to_block,
                denominator=denominator,
                zones=zones,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "demand_curve failed | markets=%s date_from=%s date_to=%s from_block=%s to_block=%s denominator=%s zones=%s",
                markets, date_from, date_to, from_block, to_block, denominator, zones
            )
            raise HTTPException(status_code=500, detail="Failed to compute demand curve.")
    
    async def mape_curve(
        self,
        markets: Optional[List[str]],
        date_from: Optional[str],
        date_to: Optional[str],
        from_block: Optional[int],
        to_block: Optional[int],
        denominator: str,
        zones: Optional[List[str]],
    ) -> Dict[str, Any]:
        try:
            return await self.demand_services.mape_curve(
                markets=markets,
                date_from=date_from,
                date_to=date_to,
                from_block=from_block,
                to_block=to_block,
                denominator=denominator,
                zones=zones,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "mape_curve failed | markets=%s date_from=%s date_to=%s from_block=%s to_block=%s denominator=%s zones=%s",
                markets, date_from, date_to, from_block, to_block, denominator, zones
            )
            raise HTTPException(status_code=500, detail="Failed to compute MAPE curve.")

    async def all_three_unified(
        self,
        markets: Optional[List[str]],
        zones: Optional[List[str]],
        date_from: Optional[str],
        date_to: Optional[str],
        from_block: Optional[int],
        to_block: Optional[int],
        group_by_date: bool,
        denominator: Literal["matched", "expected"],
    ) -> Dict[str, Any]:
        try:
            return await self.demand_services.all_three_unified(
                markets=markets,
                zones=zones,
                date_from=date_from,
                date_to=date_to,
                from_block=from_block,
                to_block=to_block,
                group_by_date=group_by_date,
                denominator=denominator,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "all_three_unified failed | markets=%s zones=%s date_from=%s date_to=%s "
                "from_block=%s to_block=%s group_by_date=%s denominator=%s",
                markets, zones, date_from, date_to, from_block, to_block, group_by_date, denominator
            )
            raise HTTPException(status_code=500, detail="Failed to compute combined analysis.")


    async def demand_forecast_last_7_days(self) -> Dict[str, Any]:
        try:
            return await self.demand_services.demand_forecast_last_7_days()
        except HTTPException:
            raise
        except Exception:
            logger.exception("demand_forecast_last_7_days failed")
            raise HTTPException(status_code=500, detail="Failed to fetch last 7 days demand summary.")


    async def selected_days_blocks_and_stats(
        self,
        *,
        dates: List[str],
        markets: Optional[List[str]],
        zones: Optional[List[str]],
        from_block: Optional[int],
        to_block: Optional[int],
        group_by_date: bool,
    ) -> Dict[str, Any]:
        try:
            blocks_payload = await self.demand_services.demand_for_selected_days(
                dates=dates,
                markets=markets,
                zones=zones,
                from_block=from_block,
                to_block=to_block,
                group_by_date=group_by_date,
            )
            stats_payload = await self.demand_services.comp_act_demand_stats_by_date(
                dates=dates,
                markets=markets,
                zones=zones,
                from_block=from_block,
                to_block=to_block,
            )
            return {
                "blocks": blocks_payload,          # already shaped { result: [...] }
                "stats": stats_payload["result"],  # list of per-date stats rows
            }
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "selected_days_blocks_and_stats failed | dates=%s markets=%s zones=%s from_block=%s to_block=%s group_by_date=%s",
                dates, markets, zones, from_block, to_block, group_by_date
            )
            raise HTTPException(status_code=500, detail="Failed to fetch selected days data.")



# Global instance
demand_controller = DemandController()
