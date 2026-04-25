import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from fastapi import HTTPException
from zoneinfo import ZoneInfo

from ..services.renewable_supply_services import renewable_supply_services

logger = logging.getLogger(__name__)

class RenewableSupplyController:
    def __init__(self, renewable_service=renewable_supply_services, tz_name: str = "Asia/Kolkata"):
        self.renewable_service = renewable_service
        self.tz = ZoneInfo(tz_name)

    # helpers
    def _today_ist(self):
        return datetime.now(self.tz).date()

    def _window_bounds_str(self):
        today = self._today_ist()
        start = (today - timedelta(days=2)).isoformat()  # day-before-yesterday
        end   = (today + timedelta(days=1)).isoformat()  # tomorrow
        return start, end

    async def renewable_availability_by_day(self, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Default to today's date (Asia/Kolkata). If the service raises a 404 (no data),
        return a friendly message (HTTP 200) indicating the current 4-day window.
        """
        anchor = date or self._today_ist().isoformat()
        if len(anchor) != 10:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

        try:
            return await self.renewable_service.renewable_availability_by_day(anchor)
        except HTTPException as exc:
            if exc.status_code == 404:
                s, e = self._window_bounds_str()
                # Hardcoded/friendly message (200 OK)
                return {"detail": f"No data in current 4-day window {s}..{e}."}
            raise

    async def renewable_schedule_by_day(self, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Default to today's date (Asia/Kolkata). If the service raises a 404 (no data),
        return a friendly message (HTTP 200) indicating the current 4-day window.
        """
        anchor = date or self._today_ist().isoformat()
        if len(anchor) != 10:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

        try:
            return await self.renewable_service.renewable_schedule_by_day(anchor)
        except HTTPException as exc:
            if exc.status_code == 404:
                s, e = self._window_bounds_str()
                return {"detail": f"No schedule data in current 4-day window {s}..{e}."}
            raise

# Global singleton
renewable_supply_controller = RenewableSupplyController()
