import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException, status, Request
from ..services.demandforecast_services import demandforecast_service

logger = logging.getLogger(__name__)


class DemandforecastController:
    """
    Controller for handling DemandforecastController-related business logic and request processing.
    """

    def __init__(self):
        self.demandforecast_services = demandforecast_service

    def _extract_request_info(self, request: Request) -> Dict[str, Optional[str]]:
        """
        Extract useful information from the request for auditing/logging.

        Args:
            request (Request): The FastAPI request object.

        Returns:
            Dict[str, Optional[str]]: Extracted request metadata.
        """
        return {
            "ip_address": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "method": request.method,
            "path": request.url.path,
            "timestamp": request.headers.get("x-request-timestamp")
        }

    async def demandforecast(self, schedule_date) -> Dict[str, Any]:
        try:
            data = await self.demandforecast_services.demand_forecast(schedule_date)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DsmoverviewController.dsm")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )

    async def monthly_average_mape(self, month: Optional[str], zone: Optional[str]) -> Dict[str, Any]:
        try:
            return await self.demandforecast_services.monthly_average_mape(month, zone)
        except Exception:
            logger.exception("Error in monthly_average_mape")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process monthly average MAPE request."
            )


# Global controller instance
demandforecast_controller = DemandforecastController()
