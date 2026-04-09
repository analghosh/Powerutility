"""
Exchange Trade Repository
Repository for active and historical exchange trade data
"""

import logging
from typing import Dict, Any, List
from libs.database.base_serve_repository import BaseServeRepository
from libs.models.exchange_trade_serve_entity import (
    ensure_exchange_trade_serve_indexes, 
    ensure_exchange_trade_serve_historical_indexes,
    async_ensure_exchange_trade_serve_indexes,
    async_ensure_exchange_trade_serve_historical_indexes
)

logger = logging.getLogger(__name__)

class ExchangeTradeRepository(BaseServeRepository):
    """Repository for managing exchange trade serve active and historical data"""
    
    def __init__(self, mongodb_uri: str = None, database_name: str = None):
        """
        Initialize the exchange trade serve repository
        
        Args:
            mongodb_uri: Optional MongoDB connection URI
            database_name: Optional database name
        """
        import os
        from dotenv import load_dotenv
        
        load_dotenv()
        
        mongodb_uri = mongodb_uri or os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        database_name = database_name or os.getenv("MONGODB_DATABASE", "epms_dev")
        serve_collection = 'exchange_dsm_serve'
        historical_collection = 'exchange_dsm_serve_historical'
        
        super().__init__(
            mongodb_uri=mongodb_uri,
            database_name=database_name,
            serve_collection=serve_collection,
            historical_collection=historical_collection
        )
        # Indexes should be constructed via the async setup() method
        pass
        
    async def setup(self):
        """Ensure necessary indexes exist on both collections asynchronously"""
        await async_ensure_exchange_trade_serve_indexes(self.serve_collection)
        await async_ensure_exchange_trade_serve_historical_indexes(self.historical_collection)
        logger.info("ExchangeTradeRepository async indexes setup complete")

    def bulk_upsert_exchange_trade_records(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Upsert multiple exchange trade records using optimized bulk write.
        Matches on scheduled_at, time_block, px_name, market_type.
        """
        unique_key_fields = ["scheduled_at", "time_block", "px_name", "market_type"]
        return self.bulk_upsert_optimized(records, unique_key_fields)
