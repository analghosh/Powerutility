import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException, status, Request, UploadFile, File, Query

from ..services.limit_value_service import limit_value_service

logger = logging.getLogger(__name__)


class LimitValueController:
    """
    Controller for handling LimitValueController-related business logic and request processing.
    """

    def __init__(self):
        self.limit_value_service = limit_value_service

    def _extract_request_info(self, request: Request) -> Dict[str, Optional[str]]:
        """
        Extract useful information from the request for auditing/logging.
        """
        return {
            "ip_address": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "method": request.method,
            "path": request.url.path,
            "timestamp": request.headers.get("x-request-timestamp"),
        }

    async def import_limit_value(self, file: UploadFile = File(...)) -> Dict[str, Any]:
        """
        Import CSV file into limit_value collection.
        """
        try:
            data = await self.limit_value_service.upload_csv(file)
            return {"data": data}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Error occurred in LimitValueController.import_limit_value")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to process CSV upload: {str(e)}",
            )

    async def get_limit_values(self, schedule_date) -> Dict[str, Any]:
        try:
            result = await self.limit_value_service.fetch_all(schedule_date)
            return result
        except Exception as e:
            logger.exception("Error occurred in LimitValueController.get_limit_values")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch data: {str(e)}",
            )

# Global controller instance
limit_value_controller = LimitValueController()
