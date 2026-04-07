
import logging
from typing import Dict, Any, List, Optional

from fastapi import HTTPException
from services.processing.app.v2.services.forecastanalysis_service import ForecastAnalysisService

logger = logging.getLogger(__name__)

class ForecastAnalysisController:
    """
    Controller for handling forecast analysis API requests.
    """

    def __init__(self):
        self.service = ForecastAnalysisService()

    async def get_all_graph_data(self, **kwargs) -> Dict[str, Any]:
        """
        Handles the request for the all_graph endpoint.
        """
        try:
            return await self.service.get_all_graph_data(**kwargs)
        except Exception as e:
            logger.exception("Failed to get all graph data.")
            raise HTTPException(status_code=500, detail=f"Failed to get all graph data: {e}")

    async def get_demand_forecast_last_7_days(self) -> Dict[str, Any]:
        """
        Handles the request for last 7 days peak demand.
        """
        try:
            return await self.service.get_demand_forecast_last_7_days()
        except Exception as e:
            logger.exception("Failed to fetch last 7 days demand summary.")
            raise HTTPException(status_code=500, detail="Failed to fetch last 7 days demand summary.")

    async def get_selected_days_data(
        self,
        dates: List[str],
        markets: Optional[List[str]],
        zones: Optional[List[str]],
        from_block: int,
        to_block: int,
        group_by_date: bool
    ) -> Dict[str, Any]:
        """
        Handles the request for the selected_days endpoint.
        """
        try:
            return await self.service.get_selected_days_data(
                dates=dates,
                markets=markets,
                zones=zones,
                from_block=from_block,
                to_block=to_block,
                group_by_date=group_by_date
            )
        except Exception as e:
            logger.exception("Failed to fetch selected days data.")
            raise HTTPException(status_code=500, detail=f"Failed to fetch selected days data: {e}")
