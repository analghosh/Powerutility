"""
Block Bid Book Repository (Processing Service)
Reads from rtm_block_bid_book_status — the collection kept live by the
BlockBidBookProcessor scheduler cron.
"""
from typing import List, Dict, Any
import logging
from bson import ObjectId

from .base_repository import GenericRepository
from ...core.config import settings

logger = logging.getLogger(__name__)


class BlockBidBookRepository(GenericRepository):
    """
    Read-side repository for rtm_block_bid_book_status.
    The scheduler writes; processing reads.
    """

    def __init__(self):
        super().__init__()
        self.collection_name = settings.MONGODB_RTM_BLOCK_BID_BOOK_COLLECTION

    @property
    def collection(self):
        return self.db[self.collection_name]

    async def get_bid_book(self, scheduled_at: str) -> List[Dict[str, Any]]:
        """
        Return all block bid records for a delivery date (YYYY-MM-DD).
        Sorted by FromPeriodId ascending.
        """
        try:
            cursor = self.collection.find(
                {"scheduled_at": scheduled_at},
                sort=[("FromPeriodId", 1)],
            )
            results = list(cursor)
            for doc in results:
                if "_id" in doc and isinstance(doc["_id"], ObjectId):
                    doc["_id"] = str(doc["_id"])
            logger.info(f"[BlockBidBook] {len(results)} records for {scheduled_at}")
            return results
        except Exception as e:
            logger.error(f"[BlockBidBook] get_bid_book failed: {e}")
            return []

    async def get_trade_book(self, scheduled_at: str) -> List[Dict[str, Any]]:
        """
        Return executed/provisional block bids for a delivery date.
        These are the bids that were matched on the exchange.
        """
        try:
            cursor = self.collection.find(
                {
                    "scheduled_at": scheduled_at,
                    "OrderStatus": {"$in": ["Executed", "Provisional"]},
                },
                sort=[("FromPeriodId", 1)],
            )
            results = list(cursor)
            for doc in results:
                if "_id" in doc and isinstance(doc["_id"], ObjectId):
                    doc["_id"] = str(doc["_id"])
            logger.info(f"[BlockBidBook] {len(results)} trade records for {scheduled_at}")
            return results
        except Exception as e:
            logger.error(f"[BlockBidBook] get_trade_book failed: {e}")
            return []
