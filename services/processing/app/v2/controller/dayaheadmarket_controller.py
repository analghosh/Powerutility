import logging
from typing import Dict, Any, Optional, List
from fastapi import HTTPException, status, Request
from ..services.dayaheadmarket_services import dayaheadmarket_service
from libs.audit_queue.simple_logger import AuditLogger

logger = logging.getLogger(__name__)


class DayaheadmarketController:
    """
    Controller for handling DayaheadmarketController-related business logic and request processing.
    """

    def __init__(self):
        self.dayaheadmarket_service = dayaheadmarket_service

    async def dayahead_single_bid_graph(
        self,
        schedule_date: Optional[str],
        from_block: int,
        to_block: int,
        request: Request,
        current_user: Dict[str, Any],
        audit_logger: AuditLogger,
    ) -> Dict[str, Any]:
        try:
            return await self.dayaheadmarket_service.dayahead_single_bid_graph(
                schedule_date,
                from_block,
                to_block,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "Error occurred in DayaheadmarketController.dayahead_single_bid_graph"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dayahead market data request.",
            )

    async def dayahead_single_bid_table(
        self,
        schedule_date: Optional[str],
        from_block: int,
        to_block: int,
        request: Request,
        current_user: Dict[str, Any],
        audit_logger: AuditLogger,
    ) -> Dict[str, Any]:
        try:
            return await self.dayaheadmarket_service.dayahead_single_bid_table(
                schedule_date,
                from_block,
                to_block,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "Error occurred in DayaheadmarketController.dayahead_single_bid_table"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dayahead market data request.",
            )

    async def approval_request_list(
        self,
        schedule_date: Optional[str],
        from_block: int,
        to_block: int,
        request: Request,
        current_user: Dict[str, Any],
        audit_logger: AuditLogger,
    ) -> Dict[str, Any]:
        try:
            return await self.dayaheadmarket_service.approval_request_list(
                schedule_date,
                from_block,
                to_block,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "Error occurred in DayaheadmarketController.approval_request_list"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dayahead market approval request list.",
            )

    async def BidCreationpost(
        self,
        post_data: Dict[str, Any],
        request: Request,
        current_user: Dict[str, Any],
        audit_logger: AuditLogger,
    ) -> Dict[str, Any]:
        try:
            return await self.dayaheadmarket_service.BidCreationpost(post_data)
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "Error occurred in DayaheadmarketController.BidCreationpost"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dayahead market bid creation.",
            )

    async def submit_for_approval_post_data(
        self,
        request_data: Dict[str, Any],
        request: Request,
        current_user: Dict[str, Any],
        audit_logger: AuditLogger,
    ) -> Dict[str, Any]:
        try:
            return await self.dayaheadmarket_service.submit_for_approval_post_data(request_data, current_user)
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "Error occurred in DayaheadmarketController.submit_for_approval_post_data"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dayahead market approval submission.",
            )

    async def dayahead_scheduling_cal_table(
        self,
        schedule_date: Optional[str],
        request: Request,
        current_user: Dict[str, Any],
        audit_logger: AuditLogger,
    ) -> Dict[str, Any]:
        try:
            return await self.dayaheadmarket_service.calSchedulingTableData(schedule_date)
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "Error occurred in DayaheadmarketController.dayahead_scheduling_cal_table"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dayahead market scheduling calculation.",
            )

    async def submitForPreExchangeData(
        self,
        data: List[Dict[str, Any]],
        request: Request,
        current_user: Dict[str, Any],
        audit_logger: AuditLogger,
    ) -> Dict[str, Any]:
        try:
            return await self.dayaheadmarket_service.submitForPreExchangeData(data)
        except HTTPException:
            raise
        except Exception:
            logger.exception(
                "Error occurred in DayaheadmarketController.submitForPreExchangeData"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process dayahead market pre-exchange data.",
            )

# Global controller instance
dayaheadmarket_controller = DayaheadmarketController()
