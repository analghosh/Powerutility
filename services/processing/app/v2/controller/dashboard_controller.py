"""
Dashboard V2 Controller
Enhanced dashboard controller with proper error handling and logging
"""

import logging
from typing import Dict, Any, Optional, List
from fastapi import HTTPException, status

from ..services.dashboard_services import dashboard_service

logger = logging.getLogger(__name__)


class DashboardController:
    """
    Controller for Dashboard V2 operations.
    Handles HTTP requests and delegates business logic to service layer.
    """

    def __init__(self):
        self.dashboard_service = dashboard_service

    async def get_dashboard_home(
        self,
        schedule_date: Optional[str] = None,
        unit_overrides: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Get dashboard home data.
        
        Args:
            schedule_date: Optional date filter in YYYY-MM-DD format
            
        Returns:
            Dict containing dashboard home data
        """
        try:
            logger.info(f"Fetching dashboard home data for schedule_date: {schedule_date}")
            result = await self.dashboard_service.get_dashboard_home_data(
                schedule_date=schedule_date,
                unit_overrides=unit_overrides,
            )
            return result
        except Exception as e:
            logger.error(f"Error in dashboard home controller: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch dashboard home data: {str(e)}"
            )

    async def dashboard_capacity_chart(self):
        """
        Controller method to fetch and return the sunburst chart data.
        """
        try:
            # Assuming service method is async, await it
            fig_json_dict = await self.dashboard_service.get_dashboard_capacity_chart()

            if not fig_json_dict or not isinstance(fig_json_dict, dict):
                raise HTTPException(status_code=500, detail="Empty or invalid chart JSON returned from service")
            return fig_json_dict
        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=500, detail=f"Controller error: {e}")

    async def get_demand_raw_v2(
        self,
        filters: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Get raw demand data from the V2 collection.

        Args:
            filters: Dictionary containing all query parameters for filtering, sorting, and pagination.

        Returns:
            List of dictionaries containing raw demand data.
        """
        try:
            logger.info(f"Fetching demand raw V2 data with filters: {filters}")
            result = await self.dashboard_service.get_demand_raw_v2_data(
                filters=filters,
            )
            return result
        except Exception as e:
            logger.error(f"Error in demand raw V2 controller: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch demand raw V2 data: {str(e)}"
            )
    
    async def get_temp_demand_raw_v2(
        self,
        filters: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Get raw temp demand data from the V2 collection.

        Args:
            filters: Dictionary containing all query parameters for filtering, sorting, and pagination.

        Returns:
            List of dictionaries containing raw demand data.
        """
        try:
            logger.info(f"Fetching  temp demand raw V2 data with filters: {filters}")
            result = await self.dashboard_service.get_temp_demand_raw_v2_data(
                filters=filters,
            )
            return result
        except Exception as e:
            logger.error(f"Error in demand raw V2 controller: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch demand raw V2 data: {str(e)}"
            )

# Global instance
dashboard_controller = DashboardController()
