"""
Block Bid Book Repository (Scheduler Service)
Stores and upserts RTM block bid exchange data polled by BlockBidBookProcessor.
Keyed by OrderId — same pattern as SingleBidBookRepository.
"""
from typing import List, Dict, Any
import logging
from pymongo import ASCENDING

from libs.database.base_repository import BaseRepository
from services.scheduler.app.core.config import settings

logger = logging.getLogger(__name__)


class BlockBidBookRepository(BaseRepository):
    """
    Repository for rtm_block_bid_book_status collection.
    Upserts exchange RTMBlockBidDetails records keyed by OrderId.
    """

    def __init__(self):
        super().__init__(
            collection_name=settings.MONGODB_RTM_BLOCK_BID_BOOK_COLLECTION,
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE,
        )
        self._ensure_indexes()

    def _ensure_indexes(self):
        try:
            self.collection.create_index([("OrderId", ASCENDING)], unique=True, background=True)
            self.collection.create_index([("DeliveryDate", ASCENDING)], background=True)
            self.collection.create_index([("scheduled_at", ASCENDING)], background=True)
            logger.info(f"Indexes ensured for {self.collection_name}")
        except Exception as e:
            logger.error(f"Error creating indexes for {self.collection_name}: {e}")

    async def upsert_bids(self, bids: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Bulk upsert block bid records using OrderId as the unique key.
        """
        if not bids:
            return {"inserted": 0, "updated": 0, "upserted": 0, "total": 0}

        validated = []
        for bid in bids:
            if "OrderId" in bid:
                try:
                    bid["OrderId"] = int(bid["OrderId"])
                    validated.append(bid)
                except (ValueError, TypeError):
                    logger.warning(f"Skipping block bid with invalid OrderId: {bid.get('OrderId')}")

        return await self.bulk_upsert_optimized(validated, unique_key_fields=["OrderId"])
