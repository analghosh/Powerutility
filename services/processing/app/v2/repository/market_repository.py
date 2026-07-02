"""
Market Repository for V2 API
Handles market-related database operations for exchange tables and bid trade data.
"""

from __future__ import annotations
from typing import List, Dict, Optional, Any, Tuple
import json
import logging
from datetime import datetime, timedelta

from .base_repository import GenericRepository
from ...core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_EXCHANGE = "IEX"
BLOCKS_PER_DAY = 96  # 96 blocks of 15 minutes


def build_exchange_pipeline_string(start_date: str, end_date: str, current_block: int) -> str:
    """
    Build the exchange data aggregation pipeline as a truly concatenated string.
    
    Args:
        start_date: Start date in ISO format
        end_date: End date in ISO format  
        current_block: Current block for RTM filtering
        
    Returns:
        MongoDB aggregation pipeline as a concatenated string
    """
    # Start building the pipeline string by concatenation
    pipeline = "["
    
    # Stage 1: $match stage
    pipeline += '{"$match":{"scheduled_at":{"$gte":{"$dateFromString":{"dateString":"'
    pipeline += start_date
    pipeline += '"}},"$lt":{"$dateFromString":{"dateString":"'
    pipeline += end_date
    pipeline += '"}}},"$expr":{"$or":[{"$ne":["$market_type","RTM"]},{"$lt":["$time_block",'
    pipeline += str(current_block + 1)
    pipeline += ']}]}}},'
    
    # Stage 2: First $group stage
    # CORRECTED: Use px_name, buy_bid, and sell_bid in the concatenated string
    pipeline += '{"$group":{"_id":{"schedule_date":{"$dateToString":{"format":"%Y-%m-%d","date":"$scheduled_at"}},"exchange":"$px_name","market":"$market_type"},"blocks":{"$push":{"time_block":"$time_block","buy":"$buy_bid","sell":"$sell_bid","mcp":"$mcp","acp":"$acp","acv":"$acv","mcv":"$mcv"}}}},'
    
    # Stage 3: Second $group stage
    pipeline += '{"$group":{"_id":{"schedule_date":"$_id.schedule_date","exchange":"$_id.exchange"},"markets":{"$push":{"k":"$_id.market","v":"$blocks"}}}},'
    
    # Stage 4: Third $group stage
    pipeline += '{"$group":{"_id":"$_id.schedule_date","exchanges":{"$push":{"k":"$_id.exchange","v":{"$arrayToObject":"$markets"}}}}},'
    
    # Stage 5: $project stage
    pipeline += '{"$project":{"_id":0,"schedule_date":"$_id","exchanges":{"$arrayToObject":"$exchanges"}}}'
    
    # Close the pipeline array
    pipeline += "]"
    
    return pipeline


def build_bid_trade_pipeline_string(schedule_date: str) -> str:
    """
    Build the bid trade data aggregation pipeline as a truly concatenated string.
    
    Args:
        schedule_date: Schedule date in YYYY-MM-DD format
        
    Returns:
        MongoDB aggregation pipeline as a concatenated string
    """
    # Build pipeline by concatenating strings
    pipeline = "["
    
    # Stage 1: $match
    pipeline += '{"$match":{"schedule_date":"' + schedule_date + '"}},'
    
    # Stage 2: $addFields
    pipeline += '{"$addFields":{"exchange":{"$ifNull":["$exchange","' + DEFAULT_EXCHANGE + '"]}}},'
    
    # Stage 3: First $group
    pipeline += '{"$group":{"_id":{"market":"$market","time_block":"$time_block"},"total_bidded":{"$sum":"$bidded"},"total_traded":{"$sum":"$traded"},"total_value":{"$sum":"$value"}}},'
    
    # Stage 4: $sort
    pipeline += '{"$sort":{"_id.time_block":1}},'
    
    # Stage 5: Second $group
    pipeline += '{"$group":{"_id":"$_id.market","time_blocks":{"$push":{"time_block":"$_id.time_block","total_bidded":"$total_bidded","total_traded":"$total_traded","total_value":"$total_value"}}}},'
    
    # Stage 6: $project
    pipeline += '{"$project":{"_id":0,"market":"$_id","time_blocks":1}}'
    
    # Close array
    pipeline += "]"
    
    return pipeline


def parse_pipeline_string(pipeline_string: str) -> List[Dict[str, Any]]:
    """
    Parse a pipeline string back to a list of dictionaries for MongoDB execution.
    
    Args:
        pipeline_string: Pipeline as a JSON string
        
    Returns:
        List of pipeline stage dictionaries
    """
    try:
        return json.loads(pipeline_string)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse pipeline string: {e}")
        raise ValueError(f"Invalid pipeline string format: {e}")


def day_bounds_utc(schedule_date: str) -> Tuple[datetime, datetime]:
    """Given 'YYYY-MM-DD', return (start, end) datetime objects (UTC) spanning that date."""
    start_dt = datetime.strptime(schedule_date, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=1)
    return start_dt, end_dt

def log_pipeline(title: str, collection: str, extra: Dict[str, Any], pipeline: List[Dict[str, Any]]):
    logger.info("[%s] Collection: %s", title, collection)
    for k, v in extra.items():
        logger.info("  %s: %s", k, v)
    try:
        logger.info("Pipeline:\n%s", json.dumps(pipeline, default=str, indent=2))
    except Exception:
        logger.error("Pipeline (repr): %r", pipeline)

class MarketRepository:
    """Repository for market-related operations."""

    def __init__(self) -> None:
        self.repository = GenericRepository()
        self.serve_exchange_table = settings.MONGODB_SERVE_EXCHANGE_COLLECTION
        self.serve_px_bid_trade = settings.MONGODB_SERVE_PX_BID_COLLECTION
        logger.info(
            "MarketRepository initialized with collections: %s, %s",
            self.serve_exchange_table,
            self.serve_px_bid_trade,
        )

    async def _aggregate(self, collection: str, pipeline: List[Dict[str, Any]], batch_size=500, max_docs=50000) -> List[Dict[str, Any]]:
        try:
            result = await self.repository.aggregate(collection, pipeline, batch_size=batch_size, max_docs=max_docs)
            logger.info("[MarketRepository] aggregate on %s returned %d records", collection, len(result))
            return result
        except Exception as e:
            logger.exception("Error during aggregation on %s: %s", collection, str(e))
            return []

    async def get_exchange_data(self, schedule_date: str, current_block: Optional[int], use_concatenated: bool = False) -> List[Dict[str, Any]]:
        """Get exchange data grouped by schedule date and exchange, filtering RTM blocks."""
        current_block = max(1, min(int(current_block or BLOCKS_PER_DAY), BLOCKS_PER_DAY))

        if use_concatenated:
            # Use concatenated string approach
            from libs.utils.date_filters import get_day_bounds_utc
            start_date_str, end_date_str = get_day_bounds_utc(schedule_date)
            
            # Build pipeline as truly concatenated string
            pipeline_string = build_exchange_pipeline_string(start_date_str, end_date_str, current_block)
            
            # Parse string back to list for MongoDB execution
            pipeline = parse_pipeline_string(pipeline_string)
            
            # Log the concatenated pipeline string for debugging
            logger.info(f"[MarketRepository] Using concatenated pipeline (length: {len(pipeline_string)} chars)")
            logger.info(f"[MarketRepository] Pipeline: {pipeline_string[:200]}...")
        else:
            # Use traditional approach
            start_date, end_date = day_bounds_utc(schedule_date) 

            pipeline = [
                {
                    "$match": {
                        "scheduled_at": {
                            "$gte": start_date,
                            "$lt": end_date
                        },
                        "$expr": {
                            "$or": [
                                {"$ne": ["$market_type", "RTM"]},
                                {"$lt": ["$time_block", current_block + 1]}
                            ]
                        }
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "schedule_date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                            "exchange": "$px_name", 
                            "market": "$market_type",
                        },
                        "blocks": {
                            "$push": {
                                "time_block": "$time_block",
                                "buy": "$buy_bid",  
                                "sell": "$sell_bid", 
                                "mcp": "$mcp",
                                "acp": "$acp",
                                "acv": "$acv",
                                "mcv": "$mcv",
                            }
                        },
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "schedule_date": "$_id.schedule_date",
                            "exchange": "$_id.exchange"
                        },
                        "markets": {
                            "$push": {"k": "$_id.market", "v": "$blocks"}
                        }
                    }
                },
                {
                    "$group": {
                        "_id": "$_id.schedule_date",
                        "exchanges": {
                            "$push": {"k": "$_id.exchange", "v": {"$arrayToObject": "$markets"}}
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
        log_pipeline(
            "MarketRepository.get_exchange_data",
            self.serve_exchange_table,
            {"Schedule Date": schedule_date, "Current Block": current_block},
            pipeline,
        )
        return await self._aggregate(
            self.serve_exchange_table, pipeline, batch_size=500, max_docs=50000
        )

    async def get_exchange_table_data(self, schedule_date: str, time_block: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get row-wise exchange table for a given date (optionally filtered by time block), with traded data."""

        # REFINED: Create a proper date range for matching against a datetime field.
        start_date, end_date = day_bounds_utc(schedule_date)

        # REFINED: The match criteria now correctly targets the 'scheduled_at' field with a date range.
        match_criteria = {
            "scheduled_at": {
                "$gte": start_date,
                "$lt": end_date
            }
        }
        if time_block is not None:
            match_criteria["time_block"] = int(time_block)

        pipeline = [
            {"$match": match_criteria},
            {
                "$lookup": {
                    "from": self.serve_px_bid_trade,
                    "let": {
                        # REFINED: Use the correct source field 'scheduled_at' (datetime object).
                        "s_date": "$scheduled_at",
                        "t_block": "$time_block",
                        "mkt": "$market_type",
                        "exch": "$px_name",
                    },
                    "pipeline": [
                        {"$addFields": {"exchange": {"$ifNull": ["$exchange", DEFAULT_EXCHANGE]}}},
                        {"$match": {"$expr": {
                            "$and": [
                                # REFINED: Match against 'scheduled_at' in the bid_trade collection.
                                {"$eq": ["$scheduled_at", "$$s_date"]},
                                {"$eq": ["$time_block", "$$t_block"]},
                                # REFINED: Match against 'type' in the bid_trade collection.
                                {"$eq": ["$type", "$$mkt"]},
                                {"$eq": ["$exchange", "$$exch"]}
                            ]
                        }}},
                        {"$project": {"_id": 0}},
                    ],
                    "as": "trade_data",
                }
            },
            {"$unwind": {"path": "$trade_data", "preserveNullAndEmptyArrays": True}},
            {
                "$project": {
                    "_id": 0,
                    "time_block": {"$ifNull": ["$time_block", ""]},
                    # REFINED: Use 'scheduled_at' and format it as a string for consistent output.
                    "schedule_date": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$scheduled_at"
                        }
                    },
                    "buy": {"$ifNull": ["$buy_bid", "0.00"]},
                    "sell": {"$ifNull": ["$sell_bid", "0.00"]},
                    "mcp": {"$ifNull": ["$mcp", "0"]},
                    "acp": {"$ifNull": ["$acp", "0"]},
                    "acv": {"$ifNull": ["$acv", "0.00"]},
                    "mcv": {"$ifNull": ["$mcv", "0.00"]},
                    "market": {"$ifNull": ["$market_type", ""]},
                    "exchange": {"$ifNull": ["$px_name", DEFAULT_EXCHANGE]},
                    "bidded": {"$ifNull": ["$trade_data.bidded", ""]},
                    "traded": {"$ifNull": ["$trade_data.traded", ""]},
                    "value": {"$abs": {"$divide": [{"$multiply": [{"$ifNull": ["$acp", 0]}, {"$ifNull": ["$trade_data.traded", 0]}]}, 4]}},
                    "group": {"$ifNull": ["$trade_data.group", ""]},
                    "forecasted_price": {"$ifNull": ["$trade_data.forecasted_price", ""]},
                    "buy_sell_flag": {"$ifNull": ["$trade_data.buy_sell_flag", ""]},
                }
            },
            {"$sort": {"schedule_date": 1, "market": 1, "exchange": 1, "time_block": 1}},
        ]

        log_pipeline(
            "MarketRepository.get_exchange_table_data",
            self.serve_exchange_table,
            {"Schedule Date": schedule_date, "Time Block": time_block},
            pipeline,
        )
        return await self._aggregate(
            self.serve_exchange_table, pipeline, batch_size=500, max_docs=25000
        )

    async def get_bid_trade_data(self, schedule_date: str, use_concatenated: bool = False) -> List[Dict[str, Any]]:
        """Get bid/trade sums by market and time_block for the date."""

        # This logic is now consistent for both traditional and "concatenated" approaches
        # since string concatenation for queries is not recommended.

        # REFINED: Create start and end datetime objects for a proper range query.
        start_date, end_date = day_bounds_utc(schedule_date)

        pipeline = [
            {
                # REFINED: Match against the 'scheduled_at' field using a date range.
                "$match": {
                    "scheduled_at": {
                        "$gte": start_date,
                        "$lt": end_date
                    }
                }
            },
            {"$addFields": {"exchange": {"$ifNull": ["$exchange", DEFAULT_EXCHANGE]}}},
            {
                "$group": {
                    # REFINED: Group by the 'type' field instead of the non-existent 'market' field.
                    "_id": {"market": "$type", "time_block": "$time_block", "buy_sell_flag": "$buy_sell_flag"},
                    "total_bidded": {"$sum": "$bidded"},
                    "total_traded": {"$sum": "$traded"},
                    "total_value": {"$sum": "$value"},
                }
            },
            {"$sort": {"_id.time_block": 1}},
            {
                "$group": {
                    "_id": "$_id.market",
                    "time_blocks": {"$push": {
                        "time_block": "$_id.time_block",
                        "total_bidded": "$total_bidded",
                        "total_traded": "$total_traded",
                        "total_value": "$total_value",
                        "buy_sell_flag": "$_id.buy_sell_flag",
                    }},
                }
            },
            {"$project": {"_id": 0, "market": "$_id", "time_blocks": 1, "buy_sell_flag":1}},
        ]

        log_pipeline(
            "MarketRepository.get_bid_trade_data",
            self.serve_px_bid_trade,
            {"Schedule Date": schedule_date},
            pipeline,
        )
        return await self._aggregate(
            self.serve_px_bid_trade, pipeline, batch_size=1000, max_docs=100000
        )

    async def get_bid_trade_by_time_block(self, schedule_date: str, time_block: int) -> List[Dict[str, Any]]:
        """Get bid/trade rows for a date + time_block, grouped by market, preserving exchange breakdowns."""
        time_block = int(time_block)
    
        # REFINED: Create a proper date range for matching against the datetime field.
        start_date, end_date = day_bounds_utc(schedule_date)
    
        pipeline = [
            {
                # REFINED: Match against the correct 'scheduled_at' field and use a date range.
                "$match": {
                    "scheduled_at": {
                        "$gte": start_date,
                        "$lt": end_date
                    },
                    "time_block": time_block
                }
            },
            {"$addFields": {"exchange": {"$ifNull": ["$exchange", DEFAULT_EXCHANGE]}}},
            {
                "$group": {
                    # REFINED: Group by the correct fields: 'type' and 'scheduled_at'.
                    "_id": {
                        "market": "$type",
                        "schedule_date": "$scheduled_at",
                        "time_block": "$time_block"
                    },
                    "data": {"$push": {
                        "exchange": "$exchange",
                        "bidded": "$bidded",
                        "traded": "$traded",
                        "value": "$value",
                        "group": "$group",
                        "forecasted_price": "$forecasted_price",
                        "buy_sell_flag": "$buy_sell_flag",
                    }},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "market": "$_id.market",
                    # REFINED: Format the schedule_date for clean, consistent string output.
                    "schedule_date": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$_id.schedule_date"
                        }
                    },
                    "time_block": "$_id.time_block",
                    "data": 1,
                    "buy_sell_flag": 1
                }
            },
        ]
    
        log_pipeline(
            "MarketRepository.get_bid_trade_by_time_block",
            self.serve_px_bid_trade,
            {"Schedule Date": schedule_date, "Time Block": time_block},
            pipeline,
        )
        return await self._aggregate(
            self.serve_px_bid_trade, pipeline, batch_size=500, max_docs=10000
        )

market_repository = MarketRepository()