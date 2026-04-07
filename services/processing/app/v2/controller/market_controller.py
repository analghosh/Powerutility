import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from fastapi import HTTPException, status

from ..services.market_services import market_service

logger = logging.getLogger(__name__)


class MarketController:
    """
    Controller for Market-related operations.
    Handles HTTP requests and delegates business logic to service layer.
    """

    def __init__(self):
        self.market_service = market_service

    async def get_exchange_graph(self, scheduled_at: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get market exchange data for graph visualization.
        
        Args:
            scheduled_at: Optional date filter in YYYY-MM-DD format
            
        Returns:
            Dict containing market exchange data for graphs
        """
        try:
            logger.info(f"Fetching market exchange graph data for scheduled_at: {scheduled_at}")
            result = await self.market_service.get_exchange_graph_data(scheduled_at)
            return result  # Wrap in v1 format
        except Exception as e:
            logger.error(f"Error in market exchange graph controller: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch market exchange graph data: {str(e)}"
            )

    async def get_overview_graph(self, scheduled_at: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get market overview data for graph visualization.
        
        Args:
            scheduled_at: Optional date filter in YYYY-MM-DD format
            
        Returns:
            Dict containing market overview data for graphs
        """
        try:
            logger.info(f"Fetching market overview graph data for scheduled_at: {scheduled_at}")
            result = await self.market_service.get_overview_graph_data(scheduled_at)
            return result # Wrap in v1 format
        except Exception as e:
            logger.error(f"Error in market overview graph controller: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch market overview graph data: {str(e)}"
            )

    async def get_exchange_table(self, scheduled_at: Optional[str] = None, time_block: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get market exchange data in table format.
        
        Args:
            scheduled_at: Optional date filter in YYYY-MM-DD format
            time_block: Optional time block filter (1-96)
            
        Returns:
            Dict containing market exchange data in table format
        """
        try:
            logger.info(f"Fetching market exchange table data for scheduled_at: {scheduled_at}, time_block: {time_block}")
            result = await self.market_service.get_exchange_table_data(scheduled_at, time_block)
            return result # Wrap in v1 format
        except Exception as e:
            logger.error(f"Error in market exchange table controller: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch market exchange table data: {str(e)}"
            )

    async def get_bidding_summary_table(self, scheduled_at: Optional[str] = None, time_block: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get market bidding summary data in table format.
        
        Args:
            scheduled_at: Optional date filter in YYYY-MM-DD format
            time_block: Optional time block filter (1-96)
            
        Returns:
            Dict containing market bidding summary data in table format
        """
        try:
            logger.info(f"Fetching market bidding summary data for scheduled_at: {scheduled_at}, time_block: {time_block}")
            result = await self.market_service.get_bidding_summary_data(scheduled_at, time_block)
            return result # Wrap in v1 format
        except Exception as e:
            logger.error(f"Error in market bidding summary controller: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch market bidding summary data: {str(e)}"
            )


# Create controller instance
market_controller = MarketController()