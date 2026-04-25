import logging
import json
from typing import Dict, Any, Optional, Union
from fastapi import HTTPException, status, Request
from fastapi.responses import JSONResponse
from ..services.dashboard_pg_services import dashboard_service

logger = logging.getLogger(__name__)


class DashboardController:
    """
    Controller for handling dashboard-related business logic and request processing.
    """

    def __init__(self, dashboard_service=dashboard_service):
        """
        Initialize the DashboardController.

        Args:
            dashboard_service: The dashboard service instance to be used.
        """
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



    def dashboard(self, request: Request) -> Dict[str, Any]:
        try:
            return  self.dashboard_service.get_dashboard(request)
        except HTTPException as http_exc:
            logger.warning(f"No dashboard data found: {http_exc.detail}")
            raise http_exc
        except Exception as e:
            logger.exception("Unhandled error in DashboardController.dashboard")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dashboard request."
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
# Global singleton instance
dashboard_pg_controller = DashboardController()
