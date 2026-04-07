import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException, status, Request
from ..services.dashboard_services import dashboard_service

logger = logging.getLogger(__name__)


class DashboardController:
    """
    Controller for handling dashboard-related business logic and request processing.
    """

    def __init__(self):
        self.dashboard_service = dashboard_service

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

    async def demand(self) -> Dict[str, Any]:
        """
        Get the demand-related data from the dashboard service.

        Returns:
            Dict[str, Any]: Dictionary containing dashboard demand data.

        Raises:
            HTTPException: In case of service or internal errors.
        """
        try:
            data = await self.dashboard_service.demand_data()
            return {
                "data": len(data) if isinstance(data, list) else 1,
                "records": data if isinstance(data, list) else [data]
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Error occurred in DashboardController.demand")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process demand data request."
            )


# Global controller instance
dashboard_controller = DashboardController()
