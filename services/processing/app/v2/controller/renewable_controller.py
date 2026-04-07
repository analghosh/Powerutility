"""
Renewable V2 Controller
Enhanced renewable controller with proper error handling and logging
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import HTTPException, status

from ..services.renewable_services import renewable_service

logger = logging.getLogger(__name__)


class RenewableController:
    """
    Controller for Renewable V2 operations.
    Handles HTTP requests and delegates business logic to service layer.
    """

    def __init__(self, tz_name: str = "Asia/Kolkata"):
        self.renewable_service = renewable_service
        self.tz = ZoneInfo(tz_name)

    def _today_ist(self):
        """Get today's date in IST timezone"""
        return datetime.now(self.tz).date()

    def _window_bounds_str(self):
        """Get the current 4-day window bounds as strings"""
        today = self._today_ist()
        start = (today - timedelta(days=2)).isoformat()  # day-before-yesterday
        end = (today + timedelta(days=1)).isoformat()    # tomorrow
        return start, end

    async def get_renewable_schedule(self, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Get renewable schedule data for a specific date.
        
        Args:
            date: Optional date in YYYY-MM-DD format. Defaults to today in IST.
            
        Returns:
            Dict containing renewable schedule data
            
        Raises:
            HTTPException: If date format is invalid or data retrieval fails
        """
        try:
            # Use today's date if not provided
            anchor = date or self._today_ist().isoformat()
            
            # Validate date format
            if len(anchor) != 10:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Date must be in YYYY-MM-DD format"
                )
            
            logger.info(f"Fetching renewable schedule data for date: {anchor}")
            
            # Call service layer
            result = await self.renewable_service.get_renewable_schedule_by_day(anchor)
            
            logger.info(f"Successfully retrieved renewable schedule data for date: {anchor}")
            return result
            
        except HTTPException as he:
            # Re-raise HTTP exceptions from service layer
            if he.status_code == 404:
                # Convert 404 to friendly message with current window info
                start, end = self._window_bounds_str()
                logger.info(f"No renewable schedule data found for date: {anchor}, window: {start}..{end}")
                return {
                    "detail": f"No schedule data in current 4-day window {start}..{end}.",
                    "requested_date": anchor,
                    "window_start": start,
                    "window_end": end
                }
            raise he
            
        except Exception as e:
            logger.error(f"Error in renewable schedule controller for date {anchor}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch renewable schedule data: {str(e)}"
            )

    async def get_renewable_availability(self, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Get renewable availability data for a specific date.
        
        Args:
            date: Optional date in YYYY-MM-DD format. Defaults to today in IST.
            
        Returns:
            Dict containing renewable availability data
            
        Raises:
            HTTPException: If date format is invalid or data retrieval fails
        """
        try:
            # Use today's date if not provided
            anchor = date or self._today_ist().isoformat()
            
            # Validate date format
            if len(anchor) != 10:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Date must be in YYYY-MM-DD format"
                )
            
            logger.info(f"Fetching renewable availability data for date: {anchor}")
            
            # Call service layer
            result = await self.renewable_service.get_renewable_availability_by_day(anchor)
            
            logger.info(f"Successfully retrieved renewable availability data for date: {anchor}")
            return result
            
        except HTTPException as he:
            # Re-raise HTTP exceptions from service layer
            if he.status_code == 404:
                # Convert 404 to friendly message with current window info
                start, end = self._window_bounds_str()
                logger.info(f"No renewable availability data found for date: {anchor}, window: {start}..{end}")
                return {
                    "detail": f"No data in current 4-day window {start}..{end}.",
                    "requested_date": anchor,
                    "window_start": start,
                    "window_end": end
                }
            raise he
            
        except Exception as e:
            logger.error(f"Error in renewable availability controller for date {anchor}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch renewable availability data: {str(e)}"
            )


# Global singleton
renewable_controller = RenewableController()