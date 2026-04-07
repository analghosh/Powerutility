import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException, status, Request
from services.processing.app.services.realtimemarket_services import realtimemarket_service
# from services.processing.app.services.block_bid_services import BlockBidServices
from ..services.block_bid_services import block_bid_service
logger = logging.getLogger(__name__)


class RealtimemarketController:
    """
    Controller for handling RealtimemarketController-related business logic and request processing.
    """

    def __init__(self):
        self.realtimemarket_service = realtimemarket_service
        self.block_bid_service = block_bid_service

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

    async def rtm_single_bid_graph(self, schedule_date, from_block, to_block) -> Dict[str, Any]:
        try:
            data = await self.realtimemarket_service.rtm_single_bid_graph(schedule_date, from_block, to_block)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DsmoverviewController.dsm")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )
    
    async def rtm_single_bid_table(self, schedule_date, from_block, to_block) -> Dict[str, Any]:
        try:
            data = await self.realtimemarket_service.rtm_single_bid_table(schedule_date, from_block, to_block)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DsmoverviewController.dsm")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )
        
    async def approval_request_list(self, schedule_date, from_block, to_block) -> Dict[str, Any]:
        try:
            data = await self.realtimemarket_service.approval_request_list(schedule_date, from_block, to_block)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DsmoverviewController.dsm")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )
    
    async def block_bid_approval_request_list(self, schedule_date) -> Dict[str, Any]:
        try:
            data = await self.block_bid_service.get_block_bid_submitted_for_approval_list(schedule_date)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DsmoverviewController.dsm")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )
    
    async def BidCreationpost(self, data) -> Dict[str, Any]:
        try:
            data = await self.realtimemarket_service.BidCreationpost(data)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DsmoverviewController.dsm")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )
        
    async def submit_for_approval_post_data(self, request: Dict[str, Any]) -> Dict[str, Any]:
        try:
            logger.info(f"Checking {request}")
            data = await self.realtimemarket_service.submit_for_approval_post_data(request)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DsmoverviewController.dsm")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )
    
    async def submit_for_approval_block_bid(self, request: Dict[str, Any]) -> Dict[str, Any]:
        try:
            logger.info(f"Checking request: {request}")
            data = await self.block_bid_service.insert_block_bid(request)
            return {"data": data}
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error occurred in block bid")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process block bid request."
            )
        
    async def update_status_block_bid(self, request: Dict[str, Any]) -> Dict[str, Any]:
        try:
            logger.info(f"Checking request: {request}")
            data = await self.block_bid_service.update_block_bid_status(request)
            return {"data": data}
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error occurred in block bid")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process block bid request."
            )
            
    async def rtm_scheduling_cal_table(self, schedule_date) -> Dict[str, Any]:
        try:
            # data = await self.realtimemarket_service.rtm_scheduling_table(schedule_date)
            data = await self.realtimemarket_service.calSchedulingTableData(schedule_date)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DsmoverviewController.dsm")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )

    async def submitForPreExchangeData(self, request: Dict[str, Any]) -> Dict[str, Any]:
        try:
            
            data = await self.realtimemarket_service.submitForPreExchangeData(request)
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
realtimemarket_controller = RealtimemarketController()
