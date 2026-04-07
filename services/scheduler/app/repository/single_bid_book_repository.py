from typing import List, Dict, Any, Optional
import pymongo
from pymongo import IndexModel, ASCENDING
import logging
from services.scheduler.app.core.config import settings
from libs.database.base_repository import BaseRepository

logger = logging.getLogger(__name__)

class SingleBidBookRepository(BaseRepository):
    """
    Repository for Single Bid Book operations.
    Handles DAM and RTM bid books with unique index on OrderId.
    """
    
    def __init__(self, market_type: str):
        """
        Initialize repository for specific market type (dam/rtm).
        """
        self.market_type = market_type.lower()
        if self.market_type == "dam":
            collection_name = settings.MONGODB_DAM_BOOK_COLLECTION
        elif self.market_type == "rtm":
            collection_name = settings.MONGODB_RTM_BOOK_COLLECTION
        else:
            raise ValueError(f"Invalid market type: {market_type}")

        # Initialize BaseRepository
        # Note: BaseRepository uses singleton client factory internally
        super().__init__(
            collection_name=collection_name, 
            mongodb_uri=settings.MONGODB_URI, 
            database_name=settings.MONGODB_DATABASE
        )
        
        # Ensure Indexes on startup
        self._ensure_indexes()

    def _ensure_indexes(self):
        """
        Create required indexes.
        1. Unique Index on OrderId
        2. Index on DeliveryDate for querying
        """
        try:
            # unique index on OrderId
            self.collection.create_index([("OrderId", ASCENDING)], unique=True, background=True)
            
            # Index on DeliveryDate for efficient querying
            self.collection.create_index([("DeliveryDate", ASCENDING)], background=True)
            
            logger.info(f"Indexes ensure for {self.collection_name}")
        except Exception as e:
            logger.error(f"Error creating indexes for {self.collection_name}: {e}")

    def upsert_bids(self, bids: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Bulk upsert bids using OrderId as unique key.
        Uses optimized bulk_write from BaseRepository.
        
        Args:
            bids: List of bid dictionaries. Must contain 'OrderId'.
        """
        if not bids:
            return {"inserted": 0, "updated": 0, "upserted": 0, "total": 0}
            
        # Ensure OrderId is int for all bids before passing to base repo
        # This is critical for matching the unique index type
        validated_bids = []
        for bid in bids:
            if "OrderId" in bid:
                try:
                    bid["OrderId"] = int(bid["OrderId"])
                    validated_bids.append(bid)
                except (ValueError, TypeError):
                    logger.warning(f"Skipping bid with invalid OrderId: {bid.get('OrderId')}")
                    continue
        
        # Use BaseRepository's optimized bulk upsert
        return self.bulk_upsert_optimized(validated_bids, unique_key_fields=["OrderId"])
