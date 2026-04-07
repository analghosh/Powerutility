"""
Bid Market Controller
Business logic for bid market CRUD operations
"""

import logging
from typing import Dict, Any, List, Optional

from ..services.bid_market_service import bid_market_service

logger = logging.getLogger(__name__)


class BidMarketController:
    """Controller for bid market operations"""

    async def create_bid_market(self, bid_market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a single bid market
        
        Args:
            bid_market_data: Dictionary containing bid market data
            
        Returns:
            Created bid market
        """
        try:
            logger.info(f"Creating bid market with name: {bid_market_data.get('name')}")
            result = await bid_market_service.create_bid_market(bid_market_data)
            logger.info(f"Successfully created bid market")
            return result
            
        except Exception as e:
            logger.error(f"Error in bid market controller create: {e}")
            raise

    async def create_bid_markets_bulk(self, bid_markets: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Create multiple bid markets in bulk
        
        Args:
            bid_markets: List of bid market dictionaries
            
        Returns:
            Dictionary with created count and IDs
        """
        try:
            logger.info(f"Creating {len(bid_markets)} bid markets in bulk")
            result = await bid_market_service.create_bid_markets_bulk(bid_markets)
            logger.info(f"Successfully created {result['count']} bid markets")
            return result
            
        except Exception as e:
            logger.error(f"Error in bid market controller bulk create: {e}")
            raise

    async def get_bid_market_by_id(self, bid_market_id: str) -> Optional[Dict[str, Any]]:
        """
        Get bid market by ID
        
        Args:
            bid_market_id: Bid market ID
            
        Returns:
            Bid market document or None
        """
        try:
            logger.info(f"Fetching bid market with ID: {bid_market_id}")
            return await bid_market_service.get_bid_market_by_id(bid_market_id)
            
        except Exception as e:
            logger.error(f"Error in bid market controller get by ID: {e}")
            raise

    async def get_bid_market_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get bid market by name
        
        Args:
            name: Bid market name
            
        Returns:
            Bid market document or None
        """
        try:
            logger.info(f"Fetching bid market with name: {name}")
            return await bid_market_service.get_bid_market_by_name(name)
            
        except Exception as e:
            logger.error(f"Error in bid market controller get by name: {e}")
            raise

    async def get_all_bid_markets(
        self,
        skip: int = 0,
        limit: int = 100,
        sort_by: str = "created_at",
        sort_order: int = -1,
        status_filter: Optional[str] = None,
        type_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get all bid markets with pagination and filtering
        
        Args:
            skip: Number of records to skip
            limit: Maximum number of records to return
            sort_by: Field to sort by
            sort_order: Sort order (1 for ascending, -1 for descending)
            status_filter: Filter by status (active/inactive)
            type_filter: Filter by type
            
        Returns:
            Dictionary with bid markets and metadata
        """
        try:
            logger.info(f"Fetching all bid markets (skip: {skip}, limit: {limit})")
            result = await bid_market_service.get_all_bid_markets(
                skip=skip,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order,
                status_filter=status_filter,
                type_filter=type_filter
            )
            logger.info(f"Successfully fetched {len(result['bid_markets'])} bid markets")
            return result
            
        except Exception as e:
            logger.error(f"Error in bid market controller get all: {e}")
            raise

    async def update_bid_market(
        self,
        bid_market_id: str,
        update_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Update bid market by ID
        
        Args:
            bid_market_id: Bid market ID
            update_data: Dictionary containing fields to update
            
        Returns:
            Updated bid market or None
        """
        try:
            logger.info(f"Updating bid market with ID: {bid_market_id}")
            result = await bid_market_service.update_bid_market(bid_market_id, update_data)
            if result:
                logger.info(f"Successfully updated bid market")
            return result
            
        except Exception as e:
            logger.error(f"Error in bid market controller update: {e}")
            raise

    async def delete_bid_market(self, bid_market_id: str) -> bool:
        """
        Delete bid market by ID
        
        Args:
            bid_market_id: Bid market ID
            
        Returns:
            True if deleted, False otherwise
        """
        try:
            logger.info(f"Deleting bid market with ID: {bid_market_id}")
            result = await bid_market_service.delete_bid_market(bid_market_id)
            if result:
                logger.info(f"Successfully deleted bid market")
            return result
            
        except Exception as e:
            logger.error(f"Error in bid market controller delete: {e}")
            raise

    async def get_bid_markets_by_status(self, status: str) -> List[Dict[str, Any]]:
        """
        Get all bid markets by status
        
        Args:
            status: Status to filter by (active/inactive)
            
        Returns:
            List of bid markets with the specified status
        """
        try:
            logger.info(f"Fetching bid markets with status: {status}")
            result = await bid_market_service.get_bid_markets_by_status(status)
            logger.info(f"Successfully fetched {len(result)} bid markets with status {status}")
            return result
            
        except Exception as e:
            logger.error(f"Error in bid market controller get by status: {e}")
            raise

    async def get_bid_markets_by_type(self, market_type: str) -> List[Dict[str, Any]]:
        """
        Get all bid markets by type
        
        Args:
            market_type: Type to filter by
            
        Returns:
            List of bid markets with the specified type
        """
        try:
            logger.info(f"Fetching bid markets with type: {market_type}")
            result = await bid_market_service.get_bid_markets_by_type(market_type)
            logger.info(f"Successfully fetched {len(result)} bid markets with type {market_type}")
            return result
            
        except Exception as e:
            logger.error(f"Error in bid market controller get by type: {e}")
            raise


# Create singleton instance
bid_market_controller = BidMarketController()