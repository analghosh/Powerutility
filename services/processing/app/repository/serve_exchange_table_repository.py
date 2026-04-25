# repositories/dashboard_repository.py

from typing import List, Dict, Optional, Any
from .base_repository import BaseRepository
import logging
from datetime import datetime, timedelta
from ..services.dashboard_new import ist_now, block_from_hhmm
logger = logging.getLogger(__name__)


class serveExchangeTableRepository(BaseRepository):
    def __init__(self, collection_name: str = "serve_exchange_table"):
        super().__init__(collection_name=collection_name)
        logger.info(f"DashboardRepository initialized with collection: {collection_name}")


    async def get_exchange(self, schedule_date: str) -> Optional[List[Dict[str, Any]]]:
        # Suppose your time_block is hour-based, you can map current hour to block
        

        now_ist = ist_now()
        is_today_ist = (now_ist.date().isoformat() == schedule_date)
        if is_today_ist:
            current_block = block_from_hhmm((now_ist + timedelta(seconds=1)).strftime("%H:%M"))
        else:
            current_block = 95
        pipeline = [
    {
        "$match": {
            "schedule_date": schedule_date,
            "$expr": {
                "$or": [
                    { "$ne": ["$market", "RTM"] },   # non-RTM → keep all
                    { "$lt": ["$time_block", int(current_block+1)] }  # RTM → only past blocks
                ]
            }
        }
    },
    {
        "$group": {
            "_id": {
                "schedule_date": "$schedule_date",
                "exchange": "$exchange",
                "market": "$market"
            },
            "blocks": {
                "$push": {
                    "time_block": "$time_block",
                    "buy": "$buy",
                    "sell": "$sell",
                    "mcp": "$mcp",
                    "acp": "$acp",
                    "acv": "$acv",
                    "mcv": "$mcv"
                }
            }
        }
    },
    {
        "$group": {
            "_id": {
                "schedule_date": "$_id.schedule_date",
                "exchange": "$_id.exchange"
            },
            "markets": {
                "$push": {
                    "k": "$_id.market",
                    "v": "$blocks"
                }
            }
        }
    },
    {
        "$group": {
            "_id": "$_id.schedule_date",
            "exchanges": {
                "$push": {
                    "k": "$_id.exchange",
                    "v": { "$arrayToObject": "$markets" }
                }
            }
        }
    },
    {
        "$project": {
            "_id": 0,
            "schedule_date": "$_id",
            "exchanges": { "$arrayToObject": "$exchanges" }
        }
    }
]

        return await self.collection.aggregate(pipeline).to_list(length=10000)
    

    
    async def get_exchange_by_date_time_block(self, schedule_date: str, time_block) -> Optional[List[Dict[str, Any]]]:
        logger.info(f"get_exchange_by_date_time_block +++++++++++++++++++======================= ")
        pipeline = [
    {
        "$match": {
            "schedule_date": schedule_date,
            "time_block": time_block
        }
    },
    {
        "$lookup": {
            "from": "serve_px_bid_trade",
            "let": {
                "s_date": "$schedule_date",
                "t_block": "$time_block",
                "mkt": "$market",
                "exch": "$exchange"
            },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                {"$eq": ["$schedule_date", "$$s_date"]},
                                {"$eq": ["$time_block", "$$t_block"]},
                                {"$eq": ["$market", "$$mkt"]},
                                {"$eq": ["$exchange", "$$exch"]}
                            ]
                        }
                    }
                },
                {
                    "$project": {
                        "_id": 0
                    }
                }
            ],
            "as": "trade_data"
        }
    },
    {
        "$unwind": {
            "path": "$trade_data",
            "preserveNullAndEmptyArrays": True
        }
    },
    {
        "$project": {
            "_id": 0,
            "time_block": {"$ifNull": ["$time_block", ""]},
            "schedule_date": {"$ifNull": ["$schedule_date", ""]},
            "buy": {"$ifNull": ["$buy", "0.00"]},
            "sell": {"$ifNull": ["$sell", "0.00"]},
            "mcp": {"$ifNull": ["$mcp", "0"]},
            "acp": {"$ifNull": ["$acp", "0"]},
            "acv": {"$ifNull": ["$acv", "0.00"]},
            "mcv": {"$ifNull": ["$mcv", "0.00"]},
            "market": {"$ifNull": ["$market", ""]},
            "exchange": {"$ifNull": ["$exchange", ""]},
            "bidded": {"$ifNull": ["$trade_data.bidded", ""]},
            "traded": {"$ifNull": ["$trade_data.traded", ""]},
            "value": {"$ifNull": ["$trade_data.value", ""]}
        }
    }
]




        return await self.collection.aggregate(pipeline).to_list(length=10000)


    
