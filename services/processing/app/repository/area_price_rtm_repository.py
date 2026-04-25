
from typing import List, Dict, Optional, Any
from .base_repository import BaseRepository
import logging

logger = logging.getLogger(__name__)


class AreaPriceRtmRepository(BaseRepository):
    def __init__(self, collection_name: str = "area_price_rtm"):
        super().__init__(collection_name=collection_name)
        logger.info(f"DashboardRepository initialized with collection: {collection_name}")

    async def get_data(self, schedule_date: str):
        pipeline = [
        {"$match": {"schedule_date": schedule_date}},
        {"$project": {
            "_id": 0,
        }},
        {"$sort": {"time_block": 1}}  # ascending order
    ]
        return await self.collection.aggregate(pipeline).to_list(length=10000)