# repositories/area_price_dam_repository.py
from typing import List, Dict, Optional, Any
from .base_repository import BaseRepository
import logging

logger = logging.getLogger(__name__)


class AreaPriceDamRepository(BaseRepository):
    """
    Repository for the 'area_price_dam' collection.
    Handles dayahead market area price data operations.
    """

    def __init__(self, collection_name: str = "area_price_dam"):
        super().__init__(collection_name=collection_name)
        logger.info(f"AreaPriceDamRepository initialized with collection: {collection_name}")

    async def get_data(self, schedule_date: str) -> List[Dict[str, Any]]:
        """
        Get area price data for a specific schedule date.
        """
        try:
            pipeline = [
                {"$match": {"schedule_date": schedule_date}},
                {"$project": {
                    "_id": 0,
                }},
                {"$sort": {"time_block": 1}}  # ascending order
            ]
            return await self.collection.aggregate(pipeline).to_list(length=10000)
        except Exception as e:
            logger.error(f"Failed to get area price data in {self.collection_name}: {str(e)}")
            raise

    async def get_data_by_time_range(self, schedule_date: str, from_block: int, to_block: int) -> List[Dict[str, Any]]:
        """
        Get area price data for a specific schedule date and time block range.
        """
        try:
            pipeline = [
                {
                    "$match": {
                        "schedule_date": schedule_date,
                        "time_block": {"$gte": from_block, "$lte": to_block}
                    }
                },
                {"$project": {
                    "_id": 0,
                }},
                {"$sort": {"time_block": 1}}
            ]
            return await self.collection.aggregate(pipeline).to_list(length=10000)
        except Exception as e:
            logger.error(f"Failed to get area price data by time range in {self.collection_name}: {str(e)}")
            raise

    async def get_latest_price(self, schedule_date: str) -> Optional[Dict[str, Any]]:
        """
        Get the latest price data for a specific schedule date.
        """
        try:
            pipeline = [
                {"$match": {"schedule_date": schedule_date}},
                {"$sort": {"time_block": -1}},
                {"$limit": 1},
                {"$project": {"_id": 0}}
            ]
            result = await self.collection.aggregate(pipeline).to_list(length=10000)
            return result[0] if result else None
        except Exception as e:
            logger.error(f"Failed to get latest price in {self.collection_name}: {str(e)}")
            raise

    async def get_price_summary(self, schedule_date: str) -> Dict[str, Any]:
        """
        Get price summary statistics for a specific schedule date.
        """
        try:
            pipeline = [
                {"$match": {"schedule_date": schedule_date}},
                {"$group": {
                    "_id": None,
                    "min_price": {"$min": "$price"},
                    "max_price": {"$max": "$price"},
                    "avg_price": {"$avg": "$price"},
                    "total_volume": {"$sum": "$volume"}
                }}
            ]
            result = await self.collection.aggregate(pipeline).to_list(length=10000)
            return result[0] if result else {}
        except Exception as e:
            logger.error(f"Failed to get price summary in {self.collection_name}: {str(e)}")
            raise
