"""
DAM Transaction Controller
Controller for DAM Single Bid Transaction API
"""

import logging
from typing import Dict, Any
from datetime import time

from fastapi import HTTPException, status
from libs.core.jwt_validator import TokenPayload
from ..services.gdam_transaction_service import gdam_transaction_service

logger = logging.getLogger(__name__)


class GDAMTransactionController:
    """Controller for DAM Transaction operations"""
    
    async def submit_bid(
        self,
        ocf_toggle: bool,
        bid_area_id: str,
        current_user: TokenPayload,
        bid_data: Dict[str, Any],
        correlation_id: str = None,
        start_time: time = None
    ) -> Dict[str, Any]:
        """
        Handle bid submission request
        
        Args:
            ocf_toggle: If true, fetch OCF values from limit_values
            bid_area_id: Optional bid area ID override
            current_user: Authenticated user
            bid_data: Bid data payload
            correlation_id: Request correlation ID
            
        Returns:
            API response
        """
        try:
            logger.info(f"GDAMTransactionController.submit_bid called for user: {current_user.username}, correlation_id: {correlation_id}")
            
            result = await gdam_transaction_service.submit_single_bid(
                current_user=current_user,
                bid_data=bid_data,
                ocf_toggle=ocf_toggle,
                bid_area_id=bid_area_id,
                correlation_id=correlation_id,
                start_time=start_time
            )
            
            return result
            
        except ValueError as e:
            logger.warning(f"Validation error in submit_bid: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Unexpected error in submit_bid: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error during bid submission"
            )

    async def cancel_bid(
        self,
        correlation_id: str = None
    ) -> Dict[str, Any]:
        """
        Handle bid cancellation request
        
        Args:
            correlation_id: Request correlation ID
            
        Returns:
            Cancellation result
        """
        try:
            logger.info(f"GDAMTransactionController.cancel_bid called, correlation_id: {correlation_id}")
            
            result = await gdam_transaction_service.cancel_bid(
                correlation_id=correlation_id
            )
            
            return result
            
        except ValueError as e:
            logger.warning(f"Validation error in cancel_bid: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Unexpected error in cancel_bid: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Internal server error during bid cancellation"
            )

    async def get_trade_book(
        self,
        delivery_date: int = None,
        from_period: str = "ALL",
        to_period: str = "ALL",
        buy_sell: str = "0",
        bid_area_id: str = "ALL",
        trade_id: int = 0,
        order_id: int = 0,
        page_number: int = 0,
        page_size: int = 0,
        correlation_id: str = None
    ) -> Dict[str, Any]:
        """
        Handle trade book fetch request
        
        Args:
            delivery_date: DAM Delivery Date timestamp
            from_period: From Period Id filter
            to_period: To Period Id filter
            buy_sell: Buy/Sell filter ('0'=Both, '1'=Buy, '2'=Sell)
            bid_area_id: Bid Area Id filter
            trade_id: Trade Id filter
            order_id: Order Id filter
            page_number: Page number for pagination
            page_size: Page size for pagination
            correlation_id: Request correlation ID
            
        Returns:
            Trade book data from exchange
        """
        try:
            logger.info(f"GDAMTransactionController.get_trade_book called, correlation_id: {correlation_id}")
            
            result = await gdam_transaction_service.get_trade_book(
                delivery_date=delivery_date,
                from_period=from_period,
                to_period=to_period,
                buy_sell=buy_sell,
                bid_area_id=bid_area_id,
                trade_id=trade_id,
                order_id=order_id,
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
                detail="Internal server error fetching trade book"
            )

    async def get_price_quantity(
        self,
        delivery_date: int = None,
        correlation_id: str = None
    ) -> Dict[str, Any]:
        """
        Handle DAM price quantity (MCP) data fetch request
        
        Args:
            delivery_date: DAM Delivery Date timestamp
            correlation_id: Request correlation ID
            
        Returns:
            Price quantity data from exchange
        """
        try:
            logger.info(f"GDAMTransactionController.get_price_quantity called, correlation_id: {correlation_id}")
            
            result = await gdam_transaction_service.get_price_quantity(
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
                detail="Internal server error fetching DAM price quantity"
            )


# Singleton instance
gdam_transaction_controller = GDAMTransactionController()
