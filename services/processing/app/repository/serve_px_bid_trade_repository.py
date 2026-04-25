# repositories/dashboard_repository.py

from typing import List, Dict, Optional, Any
from .base_repository import BaseRepository
import logging

logger = logging.getLogger(__name__)


class servePxBidTradeRepository(BaseRepository):
    def __init__(self, collection_name: str = "serve_px_bid_trade"):
        super().__init__(collection_name=collection_name)
        logger.info(f"DashboardRepository initialized with collection: {collection_name}")


    async def get_bid_trade(self, schedule_date: str) -> Optional[List[Dict[str, Any]]]:
        pipeline = [
    {
        "$match": {
            "schedule_date": schedule_date   # optional filter for date
        }
    },
    {
        "$group": {
            "_id": {
                "market": "$market",
                "time_block": "$time_block"
            },
            "total_bidded": {"$sum": "$bidded"},
            "total_traded": {"$sum": "$traded"},
            "total_value": {"$sum": "$value"}
        }
    },
    {
        "$sort": {
            "_id.time_block": 1
        }
    },
    {
        "$group": {
            "_id": "$_id.market",
            "time_blocks": {
                "$push": {
                    "time_block": "$_id.time_block",
                    "total_bidded": "$total_bidded",
                    "total_traded": "$total_traded",
                    "total_value": "$total_value"
                }
            }
        }
    },
    {
        "$project": {
            "_id": 0,
            "market": "$_id",
            "time_blocks": 1
        }
    }
]


        return await self.collection.aggregate(pipeline).to_list(length=10000)
    

    
    async def get_exchange_by_date_time_block(self, schedule_date: str, time_block) -> Optional[List[Dict[str, Any]]]:
        logger.info(f"get_exchange_by_date_time_block +++++++++++++++++++ ")
        pipeline = [
            {
                "$match": {
                    "schedule_date": schedule_date,
                    "time_block": time_block
                }
            },
            {
                "$group": {
                    "_id": {
                        "market": "$market",
                        "schedule_date": "$schedule_date",
                        "time_block": "$time_block"
                    },
                    "data": {
                        "$push": {
                            "exchange": "$exchange",
                            "bidded": "$bidded",
                            "traded": "$traded",
                            "value": "$value",
                            "group": "$group",
                            "forecasted_price": "$forecasted_price"
                        }
                    }
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "market": "$_id.market",
                    "schedule_date": "$_id.schedule_date",
                    "time_block": "$_id.time_block",
                    "data": 1
                }
            }
        ]




        return await self.collection.aggregate(pipeline).to_list(length=10000)
   
    
