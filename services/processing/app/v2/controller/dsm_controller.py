import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from fastapi import HTTPException, status, Request
from ..services.dsm_services import dsm_service

logger = logging.getLogger(__name__)


class DsmController:
    """
    Controller for DSM-related operations.
    Handles HTTP requests and delegates business logic to service layer.
    """

    def __init__(self):
        self.dsm_service = dsm_service

    async def dsm(self, schedule_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Get DSM data for a specific schedule date.
        
        Args:
            schedule_date: Optional date filter in YYYY-MM-DD format
            
        Returns:
            Dict containing DSM data and metadata
        """
        try:
            logger.info(f"Fetching DSM data for schedule_date: {schedule_date}")
            if schedule_date:
                scheduled_at = datetime.strptime(schedule_date, "%Y-%m-%d")
            else:
                scheduled_at = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            result = await self.dsm_service.get_dsm_data(scheduled_at)
            return result
        except Exception as e:
            logger.error(f"Error in DSM controller: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch DSM data: {str(e)}"
            )

    async def dsmHistorical(self, from_date: Optional[str] = None, to_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get historical DSM data for a date range.
        
        Args:
            from_date: Start date in YYYY-MM-DD format
            to_date: End date in YYYY-MM-DD format
            
        Returns:
            List containing historical DSM data and metadata
        """
        try:
            logger.info(f"Fetching historical DSM data from {from_date} to {to_date}")
            if from_date:
                from_date_dt = datetime.strptime(from_date, "%Y-%m-%d")
            else:
                from_date_dt = None
            if to_date:
                to_date_dt = datetime.strptime(to_date, "%Y-%m-%d")
            else:
                to_date_dt = None
            result = await self.dsm_service.get_dsm_historical_data(from_date_dt, to_date_dt)
            return result
        except Exception as e:
            logger.error(f"Error in DSM historical controller: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch historical DSM data: {str(e)}"
            )

    async def dsmHistoricalTable(self, from_date: Optional[str] = None, to_date: Optional[str] = None, time_block: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get historical DSM table data for a date range and time block.
        
        Args:
            from_date: Start date in YYYY-MM-DD format
            to_date: End date in YYYY-MM-DD format
            time_block: Optional time block filter
            
        Returns:
            List containing historical DSM table data and metadata
        """
        try:
            logger.info(f"Fetching historical DSM table data from {from_date} to {to_date}, time_block: {time_block}")
            if from_date:
                from_date_dt = datetime.strptime(from_date, "%Y-%m-%d")
            else:
                from_date_dt = None
            if to_date:
                to_date_dt = datetime.strptime(to_date, "%Y-%m-%d")
            else:
                to_date_dt = None
            result = await self.dsm_service.get_dsm_historical_table_data(from_date_dt, to_date_dt, time_block)
            return result
        except Exception as e:
            logger.error(f"Error in DSM historical table controller: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch historical DSM table data: {str(e)}"
            )


# Create controller instance
dsm_controller = DsmController()
