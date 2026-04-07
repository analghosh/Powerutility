"""
Bid Market Service
Service layer for bid market business logic
"""

import logging
from typing import List, Dict, Any, Optional

from ..repository.bid_market_repository import bid_market_repository

logger = logging.getLogger(__name__)


class BidMarketService:
    """Service for bid market operations"""

    VALID_STATUSES = ["active", "inactive"]

    async def create_bid_market(self, bid_market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a single bid market
        
        Args:
            bid_market_data: Dictionary containing bid market data
            
        Returns:
            Created bid market
        """
        try:
            # Validate required fields
            if "type" not in bid_market_data:
                raise ValueError("type is required")
            
            if "name" not in bid_market_data:
                raise ValueError("name is required")
            
            if "status" not in bid_market_data:
                raise ValueError("status is required")
            
            # Validate status
            if bid_market_data["status"] not in self.VALID_STATUSES:
                raise ValueError(f"status must be one of: {', '.join(self.VALID_STATUSES)}")
            
            # Check if bid market with same name already exists
            existing = await bid_market_repository.get_bid_market_by_name(bid_market_data["name"])
            if existing:
                raise ValueError(f"Bid market with name '{bid_market_data['name']}' already exists")
            
            return await bid_market_repository.create_bid_market(bid_market_data)
            
        except Exception as e:
            logger.error(f"Error in bid market service create: {e}")
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
            # Validate all bid markets
            for bid_market in bid_markets:
                if "type" not in bid_market:
                    raise ValueError("All bid markets must have type")
                if "name" not in bid_market:
                    raise ValueError("All bid markets must have name")
                if "status" not in bid_market:
                    raise ValueError("All bid markets must have status")
                if bid_market["status"] not in self.VALID_STATUSES:
                    raise ValueError(f"status must be one of: {', '.join(self.VALID_STATUSES)}")
            
            # Check for duplicate names in the input
            names = [bid_market["name"] for bid_market in bid_markets]
            if len(names) != len(set(names)):
                raise ValueError("Duplicate name found in input bid markets")
            
            # Check if any names already exist in database
            for name in names:
                existing = await bid_market_repository.get_bid_market_by_name(name)
                if existing:
                    raise ValueError(f"Bid market with name '{name}' already exists")
            
            return await bid_market_repository.create_bid_markets_bulk(bid_markets)
            
        except Exception as e:
            logger.error(f"Error in bid market service bulk create: {e}")
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
            return await bid_market_repository.get_bid_market_by_id(bid_market_id)
            
        except Exception as e:
            logger.error(f"Error in bid market service get by ID: {e}")
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
            return await bid_market_repository.get_bid_market_by_name(name)
            
        except Exception as e:
            logger.error(f"Error in bid market service get by name: {e}")
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
            # Validate status filter if provided
            if status_filter and status_filter not in self.VALID_STATUSES:
                raise ValueError(f"status_filter must be one of: {', '.join(self.VALID_STATUSES)}")
            
            return await bid_market_repository.get_all_bid_markets(
                skip=skip,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order,
                status_filter=status_filter,
                type_filter=type_filter
            )
            
        except Exception as e:
            logger.error(f"Error in bid market service get all: {e}")
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
            # Validate status if being updated
            if "status" in update_data and update_data["status"] not in self.VALID_STATUSES:
                raise ValueError(f"status must be one of: {', '.join(self.VALID_STATUSES)}")
            
            # Check if new name already exists (if name is being updated)
            if "name" in update_data:
                existing = await bid_market_repository.get_bid_market_by_name(update_data["name"])
                if existing and str(existing.get("_id")) != bid_market_id:
                    raise ValueError(f"Bid market with name '{update_data['name']}' already exists")
            
            return await bid_market_repository.update_bid_market(bid_market_id, update_data)
            
        except Exception as e:
            logger.error(f"Error in bid market service update: {e}")
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
            return await bid_market_repository.delete_bid_market(bid_market_id)
            
        except Exception as e:
            logger.error(f"Error in bid market service delete: {e}")
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
            if status not in self.VALID_STATUSES:
                raise ValueError(f"status must be one of: {', '.join(self.VALID_STATUSES)}")
            
            return await bid_market_repository.get_bid_markets_by_status(status)
            
        except Exception as e:
            logger.error(f"Error in bid market service get by status: {e}")
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
            return await bid_market_repository.get_bid_markets_by_type(market_type)
            
        except Exception as e:
            logger.error(f"Error in bid market service get by type: {e}")
            raise


# Create singleton instance
bid_market_service = BidMarketService()