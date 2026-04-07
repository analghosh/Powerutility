import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException, status, Request
from services.processing.app.v2.services.new_rtm_bidding_services import rtm_bidding_services
from libs.audit_queue.simple_logger import AuditLogger
# from services.processing.app.v2.services.new_rtm_bidding_services import rtm_bidding_services
# from services.processing.app.services.block_bid_services import BlockBidServices
from ..services.block_bid_services import block_bid_service
from libs.core.jwt_validator import TokenPayload
logger = logging.getLogger(__name__)


class RTMBiddingController:
    """
    Controller for handling RealtimemarketController-related business logic and request processing.
    """

    def __init__(self):
        self.rtm_bidding_service = rtm_bidding_services
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

    async   def rtm_single_bid_graph(self, schedule_date, from_block, to_block) -> Dict[str, Any]:
        try:
            data =  await self.rtm_bidding_service.rtm_single_bid_graph(schedule_date, from_block, to_block)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in RTMBiddingController.rtm_single_bid_graph")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process rtm single bid graph data request."
            )
    
    async def rtm_single_bid_table(self, schedule_date, from_block, to_block) -> Dict[str, Any]:
        try:
            data = await self.rtm_bidding_service.rtm_single_bid_table(schedule_date, from_block, to_block)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in RTMBiddingController.rtm_single_bid_table")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )
        
    async def approval_request_list(self, schedule_date, from_block, to_block) -> Dict[str, Any]:
        try:
            data = await self.rtm_bidding_service.approval_request_list(schedule_date, from_block, to_block)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in RTMBiddingController.approval_request_list")
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
            logger.exception("Error occurred in RTMBiddingController.block_bid_approval_request_list")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )
    
    async def BidCreationpost(self, data, username) -> Dict[str, Any]:
        try:
            data = await self.rtm_bidding_service.BidCreationpost(data, username)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DsmoverviewController.dsm")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )
        
    async def submit_for_approval_post_data(self, request: Dict[str, Any], current_user: TokenPayload, audit_logger: AuditLogger = None) -> Dict[str, Any]:
        try:
            logger.info(f"Checking {request}")
            manual_capture_bid = request.get("manual_capture_bid", False)
            if isinstance(manual_capture_bid, str):
                manual_capture_bid = manual_capture_bid.strip().lower() in {"1", "true", "yes", "y"}
            data = await self.rtm_bidding_service.submit_for_approval_post_data(
                request,
                current_user,
                audit_logger,
                capture_bid=True,
                manual_capture_bid=manual_capture_bid,
            )
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
            
    # async def rtm_scheduling_cal_table(self, schedule_date) -> Dict[str, Any]:
    #     try:
    #         # data = await self.rtm_bidding_services.rtm_scheduling_table(schedule_date)
    #         data = await self.rtm_bidding_services.calSchedulingTableData(schedule_date)
    #         return {"data": data}
    #     except HTTPException:
    #         raise 
    #     except Exception:
    #         logger.exception("Error occurred in DsmoverviewController.dsm")
    #         raise HTTPException(
    #             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    #             detail="Failed to process dsm data request."
    #         )

    async def submitForPreExchangeData(self, request: Dict[str, Any], current_user: Dict[str, Any], audit_logger: AuditLogger = None) -> Dict[str, Any]:
        try:
            
            data = await self.rtm_bidding_service.submitForPreExchangeData(request, current_user, audit_logger)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in DsmoverviewController.dsm")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )
    
    async def bid_status(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get bid status data for RTM bidding.
        
        Args:
            request: Request dictionary containing optional 'bid_status_date' key.
                    Format: {'bid_status_date': 'YYYY-MM-DD'} or {}
        
        Returns:
            Dict containing bid status data
        """
        try:
            data = await self.rtm_bidding_service.bid_status(request)
            return {"data": data}
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error occurred in RTMBiddingController.bid_status")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process bid status request."
            )
    
    async def trade_status(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get trade status data for RTM bidding.
        
        Args:
            request: Request dictionary containing optional 'trade_status_date' key.
                    Format: {'trade_status_date': 'YYYY-MM-DD'} or {}
        
        Returns:
            Dict containing trade status data
        """
        try:
            data = await self.rtm_bidding_service.trade_status(request)
            return {"data": data}
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error occurred in RTMBiddingController.trade_status")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process trade status request."
            )

    async def bid_status(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get bid status data for RTM bidding.
        
        Args:
            request: Request dictionary containing optional 'bid_status_date' key.
                    Format: {'bid_status_date': 'YYYY-MM-DD'} or {}
        
        Returns:
            Dict containing bid status data
        """
        try:
            data = await self.rtm_bidding_service.bid_status(request)
            return {"data": data}
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error occurred in RTMBiddingController.bid_status")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process bid status request."
            )
    
    async def trade_status(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get trade status data for RTM bidding.
        
        Args:
            request: Request dictionary containing optional 'trade_status_date' key.
                    Format: {'trade_status_date': 'YYYY-MM-DD'} or {}
        
        Returns:
            Dict containing trade status data
        """
        try:
            data = await self.rtm_bidding_service.trade_status(request)
            # Service already returns the structure with "data" key, so return it directly
            return data
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error occurred in RTMBiddingController.trade_status")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process trade status request."
            )

    async def get_bid_submission_details(self, date_str: str) -> Dict[str, Any]:
        """
        Get RTM bid submission details for a specific date.
        """
        try:
            data = await self.rtm_bidding_service.get_bid_submission_details(date_str)
            return {"data": data}
        except Exception:
            logger.exception("Error occurred in RTMBiddingController.get_bid_submission_details")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process bid submission details request."
            )

    async def get_rtm_single_bid_book(self, scheduled_date: str) -> Dict[str, Any]:
        """
        Get RTM Single Bid Book.
        """
        try:
            from datetime import datetime
            scheduled_at = datetime.strptime(scheduled_date, "%Y-%m-%d")
            data = await self.rtm_bidding_service.get_rtm_single_book(scheduled_at)
            return {"data": data}
        except Exception:
            logger.exception("Error occurred in RTMBiddingController.get_rtm_single_bid_book")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch RTM single bid book."
            )

    async def get_rtm_single_trade_book(self, scheduled_date: str) -> Dict[str, Any]:
        """
        Get RTM Single Trade Book.
        """
        try:
            data = await self.rtm_bidding_service.get_rtm_trade_book(scheduled_date)
            return {"data": data}
        except Exception:
            logger.exception("Error occurred in RTMBiddingController.get_rtm_single_trade_book")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch RTM single trade book."
            )

# Global controller instance
rtm_bidding_controller = RTMBiddingController()
