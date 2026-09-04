import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException, status, Request
from services.processing.app.v2.services.new_hpdam_bidding_services import hpdam_bidding_services
from libs.audit_queue.simple_logger import AuditLogger
# from services.processing.app.v2.services.new_hpdam_bidding_services import hpdam_bidding_services
# from services.processing.app.services.block_bid_services import BlockBidServices
from ..services.block_bid_services import block_bid_service
from libs.core.jwt_validator import TokenPayload
logger = logging.getLogger(__name__)


class HPDAMBiddingController:
    """
    Controller for handling DayAheadmarketController-related business logic and request processing.
    """

    def __init__(self):
        self.hpdam_bidding_service = hpdam_bidding_services
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

    async   def dam_single_bid_graph(self, schedule_date, from_block, to_block) -> Dict[str, Any]:
        try:
            data =  await self.hpdam_bidding_service.dam_single_bid_graph(schedule_date, from_block, to_block)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in damBiddingController.dam_single_bid_graph")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dam single bid graph data request."
            )
    
    async def dam_single_bid_table(self, schedule_date, from_block, to_block) -> Dict[str, Any]:
        try:
            data = await self.hpdam_bidding_service.dam_single_bid_table(schedule_date, from_block, to_block)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in damBiddingController.dam_single_bid_table")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )
        
    async def approval_request_list(self, schedule_date, from_block, to_block) -> Dict[str, Any]:
        try:
            data = await self.hpdam_bidding_service.approval_request_list(schedule_date, from_block, to_block)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in damBiddingController.approval_request_list")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dsm data request."
            )
    
    async def BidCreationpost(self, data) -> Dict[str, Any]:
        try:
            data = await self.hpdam_bidding_service.BidCreationpost(data)
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
                manual_capture_bid = manual_capture_bid.strip().lower() in {"true", "1", "yes", "y"}
            data = await self.hpdam_bidding_service.submit_for_approval_post_data(
                request,
                current_user,
                audit_logger,
                manual_capture_bid=bool(manual_capture_bid),
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

    async def capture_daily_snapshot(self) -> Dict[str, Any]:
        try:
            data = await self.hpdam_bidding_service.capture_daily_snapshot()
            return {"data": data}
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error occurred in HPDAMBiddingController.capture_daily_snapshot")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process DAM daily capture snapshot request."
            )
 

    async def submitForPreExchangeData(self, request: Dict[str, Any], current_user: Dict[str, Any], audit_logger: AuditLogger = None) -> Dict[str, Any]:
        try:
            
            data = await self.hpdam_bidding_service.submitForPreExchangeData(request, current_user, audit_logger)
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
        Retrieves bid status data for DAM bidding.
        
        Args:
            request: Request dictionary containing optional 'bid_status_date' key.
                    Format: {'bid_status_date': 'YYYY-MM-DD'} or {}
        
        Returns:
            Dict containing bid status data
        """
        try:
            data = await self.hpdam_bidding_service.bid_status(request)
            return {"data": data}
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error occurred in HPDAMBiddingController.bid_status")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process bid status request."
            )
    
    async def trade_status(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Retrieves trade status data for DAM bidding.
        
        Args:
            request: Request dictionary containing optional 'trade_status_date' key.
                    Format: {'trade_status_date': 'YYYY-MM-DD'} or {}
        
        Returns:
            Dict containing trade status data
        """
        try:
            data = await self.hpdam_bidding_service.trade_status(request)
            return {"data": data}
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error occurred in HPDAMBiddingController.trade_status")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process trade status request."
            )

    async def bid_status(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Retrieves bid status data for DAM bidding.
        
        Args:
            request: Request dictionary containing optional 'bid_status_date' key.
                    Format: {'bid_status_date': 'YYYY-MM-DD'} or {}
        
        Returns:
            Dict containing bid status data
        """
        try:
            data = await self.hpdam_bidding_service.bid_status(request)
            return {"data": data}
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error occurred in HPDAMBiddingController.bid_status")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process bid status request."
            )
    
    async def trade_status(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Retrieves trade status data for DAM bidding.
        
        Args:
            request: Request dictionary containing optional 'trade_status_date' key.
                    Format: {'trade_status_date': 'YYYY-MM-DD'} or {}
        
        Returns:
            Dict containing trade status data
        """
        try:
            data = await self.hpdam_bidding_service.trade_status(request)
            # Service already returns the structure with "data" key, so return it directly
            return data
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error occurred in HPDAMBiddingController.trade_status")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process trade status request."
            )

    async def get_bid_submission_details(self, date_str: str) -> Dict[str, Any]:
        """
        Get DAM bid submission details for a specific date.
        """
        try:
            data = await self.hpdam_bidding_service.get_bid_submission_details(date_str)
            return {"data": data}
        except Exception:
            logger.exception("Error occurred in HPDAMBiddingController.get_bid_submission_details")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process bid submission details request."
            )

    async def get_dam_single_bid_book(self, date_str: str) -> Dict[str, Any]:
        """
        Get DAM Single Bid Book.
        """
        try:
            data = await self.hpdam_bidding_service.get_hpdam_book(date_str)
            return {"data": data}
        except Exception:
            logger.exception("Error occurred in HPDAMBiddingController.get_dam_single_bid_book")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch DAM single bid book."
            )
    
    async def get_dam_single_trade_book(self, date_str: str) -> Dict[str, Any]:
        """
        Get DAM Single Trade Book.
        """
        try:
            data = await self.hpdam_bidding_service.get_hpdam_trade_book(date_str)
            return {"data": data}
        except Exception:
            logger.exception("Error occurred in HPDAMBiddingController.get_dam_single_trade_book")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch DAM single trade book."
            )

# Global controller instance
hpdam_bidding_controller = HPDAMBiddingController()
