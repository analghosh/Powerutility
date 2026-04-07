"""
Exchange Bid Detail Repository
Repository for storing the response of the Bid Details API
"""

from typing import Dict, Any
from .base_repository import GenericRepository

class ExchangeBidDetailRepository(GenericRepository):
    """Repository for Exchange Bid Details"""
    
    def __init__(self):
        super().__init__()
        self.collection_name = "exchange_biddetail"

    async def create_bid_detail(self, data: Dict[str, Any]) -> Any:
        """
        Create a new bid detail record in market-specific collection
        
        Args:
            data: Bid detail document (must contain 'market_type')
        """
        market_type = data.get("market_type", "dam").lower()
        collection_name = f"{market_type}_exchange_biddetail"
        
        return await self.insert_one_async(collection_name, data)

# Singleton instance
exchange_bid_detail_repository = ExchangeBidDetailRepository()
