"""
V2 Demand Forecast Controller
Handles business logic for demand forecast operations with proper error handling
"""
import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException, status

from ..services.demand_forecast_services import DemandForecastService

logger = logging.getLogger(__name__)


class DemandForecastController:
    """
    Controller for handling demand forecast business logic and request processing.
    """

    def __init__(self):
        self.demand_forecast_service = DemandForecastService()

    async def demand_forecast(
        self, 
        schedule_date: Optional[str], 
        user_id: str, 
        username: str
    ) -> Dict[str, Any]:
        """
        Get demand forecast data with proper error handling and audit logging.
        
        Args:
            schedule_date: The schedule date to filter data (YYYY-MM-DD format)
            user_id: User ID for audit logging
            username: Username for audit logging
            
        Returns:
            Dict containing demand forecast data
            
        Raises:
            HTTPException: If data processing fails
        """
        try:
            logger.info(f"Processing demand forecast request for user {username} (ID: {user_id})")
            
            data = await self.demand_forecast_service.demand_forecast(schedule_date)
            
            logger.info(f"Successfully processed demand forecast request for user {username}")
            return data
            
        except HTTPException:
            logger.error(f"HTTP error in demand forecast request for user {username}")
            raise 
        except Exception as e:
            logger.exception(f"Unexpected error in demand forecast request for user {username}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process demand forecast data request."
            )

    async def monthly_average_mape(
        self, 
        month: Optional[str], 
        zone: Optional[str], 
        user_id: str, 
        username: str
    ) -> Dict[str, Any]:
        """
        Get monthly average MAPE data with proper error handling and audit logging.
        
        Args:
            month: Month in YYYY-MM format (optional)
            zone: Zone filter (optional)
            user_id: User ID for audit logging
            username: Username for audit logging
            
        Returns:
            Dict containing monthly average MAPE data
            
        Raises:
            HTTPException: If data processing fails
        """
        try:
            logger.info(f"Processing monthly average MAPE request for user {username} (ID: {user_id})")
            
            data = await self.demand_forecast_service.monthly_average_mape(month, zone)
            
            logger.info(f"Successfully processed monthly average MAPE request for user {username}")
            return data
            
        except Exception as e:
            logger.exception(f"Error in monthly average MAPE request for user {username}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process monthly average MAPE request."
            )
