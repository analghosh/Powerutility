import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Union
from fastapi import HTTPException
from zoneinfo import ZoneInfo

from services.processing.app.services.rtm_scheduling_services import rtm_scheduling_services

logger = logging.getLogger(__name__)

class RTMSchedulingController:
    def __init__(self, rtm_scheduling_services=rtm_scheduling_services):
        self.rtm_scheduling_services = rtm_scheduling_services
        

    # helpers
    

    def rtm_scheduling_table(self) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
        try:
            data = self.rtm_scheduling_services.getAllPlantDataForScheduling()
            if not data:
                # No data returned, give friendly message
                return {"detail": f"No data "}
            return data
        except HTTPException as exc:
            if exc.status_code == 404:
                return {"detail": f"No data "}
            raise
    

# Global singleton
rtm_scheduling_controller = RTMSchedulingController()