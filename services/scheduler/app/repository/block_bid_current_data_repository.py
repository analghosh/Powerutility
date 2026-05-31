"""
Block Bid Current Data Repository (Scheduler Service)
Handles MongoDB operations on block_bid_current_data for the lapse processor.
"""

import logging
from typing import Dict, Any, List, Optional

from libs.database.base_repository import BaseRepository
from services.scheduler.app.core.config import settings

logger = logging.getLogger(__name__)

COLLECTION_NAME = "block_bid_current_data"


class BlockBidCurrentDataRepository(BaseRepository):

    def __init__(self):
        super().__init__(
            collection_name=COLLECTION_NAME,
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE
        )

    async def find_by_filters(
        self,
        filters: Dict[str, Any],
        projection: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        try:
            cursor = self.collection.find(filters, projection or {})
            return await cursor.to_list(length=10000)
        except Exception as e:
            logger.error(f"[BlockBidRepo] find_by_filters failed: {str(e)}")
            return []

    async def update_many(
        self,
        filter_query: Dict[str, Any],
        update_query: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            result = await self.collection.update_many(filter_query, update_query)
            return {"modified_count": result.modified_count}
        except Exception as e:
            logger.error(f"[BlockBidRepo] update_many failed: {str(e)}")
            return {"modified_count": 0}


block_bid_current_data_repository = BlockBidCurrentDataRepository()
