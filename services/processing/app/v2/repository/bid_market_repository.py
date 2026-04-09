"""
Bid Market Repository
Repository for bid market CRUD operations
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from bson import ObjectId

from .base_repository import GenericRepository
from ...core.config import settings

logger = logging.getLogger(__name__)


class BidMarketRepository:
    """Repository for bid_market collection operations"""

    def __init__(self):
        self.repo = GenericRepository()
        self.collection_name = "bidMarket"

    async def create_bid_market(self, bid_market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a single bid market
        
        Args:
            bid_market_data: Dictionary containing bid market data
            
        Returns:
            Created bid market with ID
        """
        try:
            # Add timestamps
            now = datetime.now()
            bid_market_data["created_at"] = now
            bid_market_data["updated_at"] = now
            
            result = await self.repo.insert_one_async(self.collection_name, bid_market_data)
            
            logger.info(f"Created bid market with ID: {result.inserted_id}")
            
            # Return the created bid market
            return await self.repo.find_one_async(
                self.collection_name,
                {"_id": result.inserted_id}
            )
            
        except Exception as e:
            logger.error(f"Error creating bid market: {e}")
            raise

    async def create_bid_markets_bulk(self, bid_markets: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Create multiple bid markets in bulk
        
        Args:
            bid_markets: List of bid market dictionaries
            
        Returns:
            Dictionary with inserted IDs and count
        """
        try:
            # Add timestamps to all bid markets
            now = datetime.now()
            for bid_market in bid_markets:
                bid_market["created_at"] = now
                bid_market["updated_at"] = now
            
            result = await self.repo.insert_many_async(self.collection_name, bid_markets)
            
            logger.info(f"Created {len(result.inserted_ids)} bid markets in bulk")
            
            return {
                "inserted_ids": [str(id) for id in result.inserted_ids],
                "count": len(result.inserted_ids)
            }
            
        except Exception as e:
            logger.error(f"Error creating bid markets in bulk: {e}")
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
            if not ObjectId.is_valid(bid_market_id):
                return None
            
            return await self.repo.find_one_async(
                self.collection_name,
                {"_id": ObjectId(bid_market_id)}
            )
            
        except Exception as e:
            logger.error(f"Error getting bid market by ID: {e}")
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
            return await self.repo.find_one_async(
                self.collection_name,
                {"name": name}
            )
            
        except Exception as e:
            logger.error(f"Error getting bid market by name: {e}")
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
            # Build filter query
            filter_query = {}
            if status_filter:
                filter_query["status"] = status_filter
            if type_filter:
                filter_query["type"] = type_filter
            
            # Get total count
            total = await self.repo.get_count(self.collection_name, filter_query)
            
            # Get bid markets with pagination
            bid_markets = await self.repo.get_all_async(
                self.collection_name,
                filter=filter_query,
                skip=skip,
                limit=limit,
                sort=[(sort_by, sort_order)]
            )
            
            return {
                "bid_markets": bid_markets,
                "total": total,
                "skip": skip,
                "limit": limit,
                "filters": {
                    "status": status_filter,
                    "type": type_filter
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting all bid markets: {e}")
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
            Updated bid market document or None
        """
        try:
            if not ObjectId.is_valid(bid_market_id):
                return None
            
            # Add updated_at timestamp
            update_data["updated_at"] = datetime.now()
            
            result = await self.repo.update_one_async(
                self.collection_name,
                {"_id": ObjectId(bid_market_id)},
                {"$set": update_data}
            )
            
            if result.modified_count > 0:
                logger.info(f"Updated bid market with ID: {bid_market_id}")
                return await self.get_bid_market_by_id(bid_market_id)
            
            return None
            
        except Exception as e:
            logger.error(f"Error updating bid market: {e}")
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
            if not ObjectId.is_valid(bid_market_id):
                return False
            
            result = await self.repo.delete_one(
                self.collection_name,
                {"_id": ObjectId(bid_market_id)}
            )

            if result.deleted_count > 0:
                logger.info(f"Deleted bid market with ID: {bid_market_id}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error deleting bid market: {e}")
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
            return await self.repo.get_all_async(
                self.collection_name,
                filter={"status": status},
                sort=[("name", 1)]
            )
            
        except Exception as e:
            logger.error(f"Error getting bid markets by status: {e}")
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
            return await self.repo.get_all_async(
                self.collection_name,
                filter={"type": market_type},
                sort=[("name", 1)]
            )
            
        except Exception as e:
            logger.error(f"Error getting bid markets by type: {e}")
            raise


# Create singleton instance
bid_market_repository = BidMarketRepository()