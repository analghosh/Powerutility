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
            results = await cursor.to_list(length=10000)
            for doc in results:
                if "_id" in doc and isinstance(doc["_id"], ObjectId):
                    doc["_id"] = str(doc["_id"])
            logger.info(f"[BlockBidBook] {len(results)} records for {scheduled_at}")
            return results
        except Exception as e:
            logger.error(f"[BlockBidBook] get_bid_book failed: {e}")
            return []

    async def update_order_status_by_order_id(self, order_id: int, order_status: str, extra_fields: Dict[str, Any] = None) -> bool:
        """
        Immediately reflect a status change (e.g. Cancelled) in the book collection
        so the bid-book endpoint shows the updated status without waiting for the
        next scheduler run.
        """
        try:
            update_doc: Dict[str, Any] = {"OrderStatus": order_status}
            if extra_fields:
                update_doc.update(extra_fields)
            result = await self.collection.update_many(
                {"OrderId": order_id},
                {"$set": update_doc},
            )
            logger.info(f"[BlockBidBook] updated OrderStatus={order_status} for OrderId={order_id} ({result.modified_count} docs)")
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"[BlockBidBook] update_order_status_by_order_id failed for OrderId={order_id}: {e}")
            return False

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
            results = await cursor.to_list(length=10000)
            for doc in results:
                if "_id" in doc and isinstance(doc["_id"], ObjectId):
                    doc["_id"] = str(doc["_id"])
            logger.info(f"[BlockBidBook] {len(results)} trade records for {scheduled_at}")
            return results
        except Exception as e:
            logger.error(f"[BlockBidBook] get_trade_book failed: {e}")
            return []
