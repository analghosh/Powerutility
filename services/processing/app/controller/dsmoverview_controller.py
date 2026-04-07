import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException, status, Request
from ..services.dsmoverview_services import dsmoverview_service

logger = logging.getLogger(__name__)


class DsmoverviewController:
    """
    Controller for handling DsmoverviewController-related business logic and request processing.
    """

    def __init__(self):
        self.dsmoverview_service = dsmoverview_service

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

    async def dsm(self, schedule_date) -> Dict[str, Any]:
        try:
            data = await self.dsmoverview_service.dsm(schedule_date)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DsmoverviewController.dsm")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )
        
    async def dsmHistorical(self, from_date, to_date) -> Dict[str, Any]:
        try:
            data = await self.dsmoverview_service.dsmHistorical(from_date, to_date)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DsmoverviewController.dsm")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )
        
    async def dsmHistoricalTable(self, from_date, to_date, time_block) -> Dict[str, Any]:
        try:
            data = await self.dsmoverview_service.dsmHistoricalTotal(from_date, to_date, time_block)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DsmoverviewController.dsm")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )


# Global controller instance
dsmoverview_controller = DsmoverviewController()
