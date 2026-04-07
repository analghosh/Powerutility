import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException, status, Request
from services.processing.app.services.dayaheadmarket_services import dayaheadmarket_service

logger = logging.getLogger(__name__)


class DayaheadmarketController:
    """
    Controller for handling DayaheadmarketController-related business logic and request processing.
    """

    def __init__(self):
        self.dayaheadmarket_service = dayaheadmarket_service

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

    async def dayahead_single_bid_graph(self, schedule_date, from_block, to_block) -> Dict[str, Any]:
        try:
            data = await self.dayaheadmarket_service.dayahead_single_bid_graph(schedule_date, from_block, to_block)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DayaheadmarketController.dayahead_single_bid_graph")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dayahead market data request."
            )
    
    async def dayahead_single_bid_table(self, schedule_date, from_block, to_block) -> Dict[str, Any]:
        try:
            data = await self.dayaheadmarket_service.dayahead_single_bid_table(schedule_date, from_block, to_block)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DayaheadmarketController.dayahead_single_bid_table")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dayahead market data request."
            )
        
    async def approval_request_list(self, schedule_date, from_block, to_block) -> Dict[str, Any]:
        try:
            data = await self.dayaheadmarket_service.approval_request_list(schedule_date, from_block, to_block)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DayaheadmarketController.approval_request_list")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dayahead market data request."
            )
    
    async def BidCreationpost(self, data) -> Dict[str, Any]:
        try:
            data = await self.dayaheadmarket_service.BidCreationpost(data)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DayaheadmarketController.BidCreationpost")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dayahead market data request."
            )
        
    async def submit_for_approval_post_data(self, request: Dict[str, Any]) -> Dict[str, Any]:
        try:
            logger.info(f"Checking {request}")
            data = await self.dayaheadmarket_service.submit_for_approval_post_data(request)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DayaheadmarketController.submit_for_approval_post_data")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dayahead market data request."
            )
            
    async def dayahead_scheduling_cal_table(self, schedule_date) -> Dict[str, Any]:
        try:
            data = await self.dayaheadmarket_service.calSchedulingTableData(schedule_date)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DayaheadmarketController.dayahead_scheduling_cal_table")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dayahead market data request."
            )

    async def submitForPreExchangeData(self, data) -> Dict[str, Any]:
        try:
            data = await self.dayaheadmarket_service.submitForPreExchangeData(data)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DayaheadmarketController.submitForPreExchangeData")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dayahead market data request."
            )

# Global controller instance
dayaheadmarket_controller = DayaheadmarketController()
