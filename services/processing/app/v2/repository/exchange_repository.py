# repositories/exchange_repository.py

from typing import List, Dict, Optional, Any
from .base_repository import GenericRepository
import logging
from datetime import datetime, timedelta

from ...core.config import settings
from ...services.dashboard_new import ist_now, block_from_hhmm

logger = logging.getLogger(__name__)


class ExchangeRepository(GenericRepository):
    """
    Repository for exchange_serve collection using v2 pattern.
    Uses exchange_serve field names following the mapping:
    - scheduled_at.$date (instead of schedule_date)
    - market_type (instead of market)
    - px_name (instead of exchange)
    - buy_bid (instead of buy)
    - sell_bid (instead of sell)
    - All other exchange_serve specific fields
    """
    
    def __init__(self):
        super().__init__()
        self.collection_name = settings.MONGODB_EXCHANGE_SERVE_COLLECTION if hasattr(settings, 'MONGODB_EXCHANGE_SERVE_COLLECTION') else "exchange_serve"
        logger.info(f"ExchangeRepository initialized with collection: {self.collection_name}")

    async def _get_exchange_sync(self, schedule_date: str) -> List[Dict[str, Any]]:
        """
        Get exchange data by schedule date, using exchange_serve fields.
        Maps scheduled_at.$date to schedule_date for compatibility.

        Args:
            schedule_date: Schedule date in YYYY-MM-DD format

        Returns:
            List of exchange data grouped by exchange and market
        """
        now_ist = ist_now()
        is_today_ist = (now_ist.date().isoformat() == schedule_date)
        if is_today_ist:
            current_block = block_from_hhmm((now_ist + timedelta(seconds=1)).strftime("%H:%M"))
        else:
            current_block = 95
            
        pipeline = [
            {
                "$match": {
                    "$expr": {
                        "$eq": [
                            {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                            schedule_date
                        ]
                    },
                    "$or": [
                        {"$ne": ["$market_type", "RTM"]},  # non-RTM → keep all
                        {"$lt": ["$time_block", int(current_block + 1)]}  # RTM → only past blocks
                    ]
                }
            },
            {
                "$group": {
                    "_id": {
                        "schedule_date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                        "px_name": "$px_name",
                        "market_type": "$market_type"
                    },
                    "blocks": {
                        "$push": {
                            "time_block": "$time_block",
                            "buy_bid": "$buy_bid",
                            "sell_bid": "$sell_bid",
                            "mcp": "$mcp",
                            "acp": "$acp",
                            "acv": "$acv",
                            "mcv": "$mcv",
                            "final_scheduled_volume": "$final_scheduled_volume"
                        }
                    }
                }
            },
            {
                "$group": {
                    "_id": {
                        "schedule_date": "$_id.schedule_date",
                        "px_name": "$_id.px_name"
                    },
                    "markets": {
                        "$push": {
                            "k": "$_id.market_type",
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
                            "k": "$_id.px_name",
                            "v": {"$arrayToObject": "$markets"}
                        }
                    }
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "schedule_date": "$_id",
                    "exchanges": {"$arrayToObject": "$exchanges"}
                }
            }
        ]

        return await super().aggregate(self.collection_name, pipeline)

    async def get_exchange(self, schedule_date: str) -> List[Dict[str, Any]]:
        """
        Get exchange data by schedule date asynchronously.

        Args:
            schedule_date: Schedule date in YYYY-MM-DD format

        Returns:
            List of exchange data grouped by exchange and market
        """
        return await self._get_exchange_sync(schedule_date)

    async def get_exchange_power_optimization(
        self, 
        schedule_date: str, 
        market_filters: Optional[List[Dict[str, str]]] = None
    ) -> List[Dict[str, Any]]:
        """
        Dedicated method for Power Optimization API with historical fallback.
        Args:
            schedule_date: Schedule date string YYYY-MM-DD
            market_filters: Optional list of dicts [{"exchange": "IEX", "market": "DAM"}, ...]
        """
        try:
            now_ist = ist_now()
            # is_today_ist = (now_ist.date().isoformat() == schedule_date)
            # if is_today_ist:
            #     current_block = block_from_hhmm((now_ist + timedelta(seconds=1)).strftime("%H:%M"))
            # else:
            #     current_block = 95
                
            match_stage = {
                "$match": {
                    "$expr": {
                        "$eq": [
                            {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                            schedule_date
                        ]
                    }
                }
            }
            
            # Add strict market filters if provided
            if market_filters:
                # Build an $or query for each exchange+market combination
                or_conditions = []
                for mf in market_filters:
                    cond = {}
                    if "exchange" in mf:
                        cond["px_name"] = mf["exchange"]
                    if "market" in mf:
                        cond["market_type"] = mf["market"]
                    if cond:
                        or_conditions.append(cond)
                
                if or_conditions:
                    match_stage["$match"]["$or"] = or_conditions
                else:
                    # Filter provided but empty valid constraints? 
                    # If market_filters was ["invalid"], or_conditions is empty.
                    # Should unlikely match anything if intended filters were strict.
                    # For now if empty logic, maybe skip?
                    pass

            pipeline = [
                match_stage,
                {
                    "$group": {
                        "_id": {
                            "exchange": "$px_name",
                            "market": "$market_type"
                        },
                        "data": {
                            "$push": {
                                "block": "$time_block",
                                "buy_bid": "$buy_bid",
                                "sell_bid": "$sell_bid",
                                "mcp": "$mcp",
                                "mcv": "$mcv"
                            }
                        }
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "exchange": "$_id.exchange",
                        "market": "$_id.market",
                        "data": 1
                    }
                },
                 {
                    "$sort": {"exchange": 1, "market": 1} # Deterministic order
                }
            ]
            
            result = await self.db[self.collection_name].aggregate(pipeline).to_list(length=10000)

            if not result:
                historical_collection = getattr(settings, 'MONGODB_EXCHANGE_SERVE_HISTORICAL_COLLECTION', "exchange_serve_historical")
                logger.debug(f"PowerOpt: No exchange data in {self.collection_name}, trying {historical_collection}")
                result = await self.db[historical_collection].aggregate(pipeline).to_list(length=10000)
                
            return result
        except Exception as e:
            logger.error(f"PowerOpt: Failed to get exchange data: {str(e)}")
            return []

    def _get_exchange_by_date_time_block_sync(self, schedule_date: str, time_block: int) -> List[Dict[str, Any]]:
        """
        Get exchange data by schedule date and time block, using exchange_serve fields.
        
        Args:
            schedule_date: Schedule date in YYYY-MM-DD format
            time_block: Time block number
            
        Returns:
            List of exchange data with trade data joined
        """
        logger.info(f"get_exchange_by_date_time_block: schedule_date={schedule_date}, time_block={time_block}")
        
        pipeline = [
            {
                "$match": {
                    "$expr": {
                        "$and": [
                            {
                                "$eq": [
                                    {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                                    schedule_date
                                ]
                            },
                            {"$eq": ["$time_block", time_block]}
                        ]
                    }
                }
            },
            {
                "$lookup": {
                    "from": "serve_px_bid_trade",
                    "let": {
                        "s_date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                        "t_block": "$time_block",
                        "mkt": "$market_type",
                        "exch": "$px_name"
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
                    "schedule_date": {
                        "$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}
                    },
                    "buy_bid": {"$ifNull": ["$buy_bid", 0.00]},
                    "sell_bid": {"$ifNull": ["$sell_bid", 0.00]},
                    "mcp": {"$ifNull": ["$mcp", 0]},
                    "acp": {"$ifNull": ["$acp", 0]},
                    "acv": {"$ifNull": ["$acv", 0.00]},
                    "mcv": {"$ifNull": ["$mcv", 0.00]},
                    "market_type": {"$ifNull": ["$market_type", ""]},
                    "px_name": {"$ifNull": ["$px_name", ""]},
                    "final_scheduled_volume": {"$ifNull": ["$final_scheduled_volume", 0.00]},
                    "data_type": {"$ifNull": ["$data_type", ""]},
                    "raw_data_id": {"$ifNull": ["$raw_data_id", ""]},
                    "bidded": {"$ifNull": ["$trade_data.bidded", ""]},
                    "traded": {"$ifNull": ["$trade_data.traded", ""]},
                    "value": {"$ifNull": ["$trade_data.value", ""]}
                }
            }
        ]

        return self.aggregate(self.collection_name, pipeline)

    async def get_exchange_by_date_time_block(self, schedule_date: str, time_block: int) -> List[Dict[str, Any]]:
        """
        Get exchange data by schedule date and time block asynchronously.
        
        Args:
            schedule_date: Schedule date in YYYY-MM-DD format
            time_block: Time block number
            
        Returns:
            List of exchange data with trade data joined
        """
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(
                executor,
                self._get_exchange_by_date_time_block_sync,
                schedule_date,
                time_block
            )

    def get_exchange_by_market(self, schedule_date: str, market_type: str, px_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get exchange data filtered by market type and optionally by exchange name.
        Uses exchange_serve fields.
        
        Args:
            schedule_date: Schedule date in YYYY-MM-DD format
            market_type: Market type (e.g., "RTM", "DAM")
            px_name: Optional exchange name (e.g., "IEX")
            
        Returns:
            List of exchange data matching the criteria
        """
        try:
            match_filter = {
                "$expr": {
                    "$eq": [
                        {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                        schedule_date
                    ]
                },
                "market_type": market_type
            }
            
            if px_name:
                match_filter["px_name"] = px_name
            
            pipeline = [
                {"$match": match_filter},
                {
                    "$project": {
                        "_id": 0,
                        "schedule_date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                        "time_block": 1,
                        "market_type": 1,
                        "px_name": 1,
                        "buy_bid": 1,
                        "sell_bid": 1,
                        "mcp": 1,
                        "acp": 1,
                        "acv": 1,
                        "mcv": 1,
                        "final_scheduled_volume": 1,
                        "data_type": 1
                    }
                },
                {"$sort": {"time_block": 1}}
            ]
            
            return self.aggregate(self.collection_name, pipeline)
        except Exception as e:
            logger.error(f"Failed to get exchange by market: {str(e)}")
            return []
