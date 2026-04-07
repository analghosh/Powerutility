
import logging
from typing import Dict, Any, Optional, List
from datetime import time
from fastapi import HTTPException, status
from services.processing.app.v2.services.rtm_transaction_service import rtm_transaction_service
from libs.core.jwt_validator import TokenPayload

logger = logging.getLogger(__name__)

class RTMTransactionController:
    async def submit_bid(
        self,
        current_user: TokenPayload,
        bid_data: Dict[str, Any],
        correlation_id: str = None,
        start_time: time = None
    ) -> Dict[str, Any]:
        """
        Handle RTM Bid Submission
        """
        logger.info(f"RTM User: {current_user.username} initiating RTM Bid Submission")
        logger.debug(f"Correlation ID: {correlation_id}")

        result = await rtm_transaction_service.submit_sequential_bid(
            current_user=current_user,
            bid_data=bid_data,
            correlation_id=correlation_id,
            start_time=start_time
        )
        
        logger.info(f"RTM Bid Submission processing completed. Result: {result.get('status', 'unknown')}")
        return result

    async def submit_sequential_bid(
        self,
        current_user: TokenPayload,
        bid_data: Dict[str, Any],
        correlation_id: str = None,
        start_time: time = None
    ) -> Dict[str, Any]:
        """
        Handle RTM Sequential Bid Submission (one API call per block)
        """
        logger.info(f"RTM User: {current_user.username} initiating RTM Sequential Bid Submission")
        logger.debug(f"Correlation ID: {correlation_id}")

        result = await rtm_transaction_service.submit_sequential_bid(
            current_user=current_user,
            bid_data=bid_data,
            correlation_id=correlation_id,
            start_time=start_time
        )
        
        logger.info(f"RTM Sequential Bid Submission completed. Result: {result.get('status', 'unknown')}")
        return result

    async def withdraw_bid(
        self,
        time_blocks: List[int],
        correlation_id: str = None,
        schedule_date: str = None
    ) -> Dict[str, Any]:
        """
        Handle RTM bid withdrawal for specific time blocks
        
        Args:
            time_blocks: List of time block integers (1-96)
            correlation_id: Request correlation ID
            
        Returns:
            Withdrawal result
        """
        try:
            logger.info(f"RTMTransactionController.withdraw_bid called for blocks {time_blocks}, correlation_id: {correlation_id}")
            
            result = await rtm_transaction_service.withdraw_bid(
                time_blocks=time_blocks,
                correlation_id=correlation_id,
                schedule_date=schedule_date
            )
            
            return result
            
        except ValueError as e:
            logger.warning(f"Validation error in withdraw_bid: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Unexpected error in withdraw_bid: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error during bid withdrawal"
            )

    async def get_trade_book(
        self,
        delivery_date: int = None,
        session: int = 0,
        from_period: str = "ALL",
        to_period: str = "ALL",
        buy_sell: str = "ALL",
        bid_area_id: str = "ALL",
        trade_id: int = 0,
        order_id: int = 0,
        sorting_type: int = 2,
        page_number: int = 0,
        page_size: int = 0,
        correlation_id: str = None
    ) -> Dict[str, Any]:
        """
        Handle RTM trade book fetch request
        
        Args:
            delivery_date: RTM Delivery Date timestamp
            session: Session filter (0 for all)
            from_period: From Period Id filter
            to_period: To Period Id filter
            buy_sell: Buy/Sell filter ('ALL', 'B', 'S')
            bid_area_id: Bid Area Id filter
            trade_id: Trade Id filter
            order_id: Order Id filter
            sorting_type: Sorting option (3-10)
            page_number: Page number for pagination
            page_size: Page size for pagination
            correlation_id: Request correlation ID
            
        Returns:
            Trade book data from exchange
        """
        try:
            logger.info(f"RTMTransactionController.get_trade_book called, correlation_id: {correlation_id}")
            
            result = await rtm_transaction_service.get_trade_book(
                delivery_date=delivery_date,
                session=session,
                from_period=from_period,
                to_period=to_period,
                buy_sell=buy_sell,
                bid_area_id=bid_area_id,
                trade_id=trade_id,
                order_id=order_id,
                sorting_type=sorting_type,
                page_number=page_number,
                page_size=page_size,
                correlation_id=correlation_id
            )
            
            return result
            
        except ValueError as e:
            logger.warning(f"Validation error in get_trade_book: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Unexpected error in get_trade_book: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error fetching RTM trade book"
            )

    async def get_price_quantity(
        self,
        delivery_date: int = None,
        correlation_id: str = None
    ) -> Dict[str, Any]:
        """
        Handle RTM price quantity (MCP) data fetch request
        
        Args:
            delivery_date: RTM Delivery Date timestamp
            correlation_id: Request correlation ID
            
        Returns:
            Price quantity data from exchange
        """
        try:
            logger.info(f"RTMTransactionController.get_price_quantity called, correlation_id: {correlation_id}")
            
            result = await rtm_transaction_service.get_price_quantity(
                delivery_date=delivery_date,
                correlation_id=correlation_id
            )
            
            return result
            
        except ValueError as e:
            logger.warning(f"Validation error in get_price_quantity: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Unexpected error in get_price_quantity: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error fetching RTM price quantity"
            )


    async def get_rtm_single_bid_book(
        self
    ) -> Dict[str, Any]:
        """
        Get RTM Single Bid Book (T and T+1 if available).
        """
        try:
            data = await rtm_transaction_service.get_rtm_book()
            return {"data": data}
        except Exception as e:
            logger.exception("Error occurred in RTMTransactionController.get_rtm_single_bid_book")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch RTM single bid book."
            )

rtm_transaction_controller = RTMTransactionController()
