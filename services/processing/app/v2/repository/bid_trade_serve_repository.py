"""
Bid Trade Serve Repository
Handles database operations for bid_trade_serve collection
"""
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import logging

from .base_repository import GenericRepository

logger = logging.getLogger(__name__)


class BidTradeServeRepository(GenericRepository):
    """
    Repository for 'bid_trade_serve' collection.
    Collection: 'bid_trade_serve'
    """
    
    def __init__(self, collection_name: Optional[str] = None):
        super().__init__()
        self.collection_name = collection_name or "bid_trade_serve"
        logger.info(f"BidTradeServeRepository initialized with collection: {self.collection_name}")

    async def setup(self):
        """Create collection and indexes. Call once from async startup."""
        await self._create_collection_if_not_exists()
        await self._create_indexes()

    async def _create_collection_if_not_exists(self):
        """Create collection if it does not exist."""
        try:
            db = self.db
            db_name = self.db_name or "unknown"
            existing = await db.list_collection_names()
            if self.collection_name not in existing:
                await db.create_collection(self.collection_name)
                logger.info(f"Created collection '{self.collection_name}' in database '{db_name}'")
            else:
                logger.debug(f"Collection '{self.collection_name}' already exists in database '{db_name}'")
        except Exception as e:
            logger.warning(f"Error checking/creating collection '{self.collection_name}': {str(e)}")

    async def _create_indexes(self):
        """Create necessary indexes for efficient queries."""
        try:
            collection = self.collection

            # Index on scheduled_at for date-based queries
            await collection.create_index([("scheduled_at", 1)])

            # Index on type for filtering by market type
            await collection.create_index([("type", 1)])

            # Index on time_block for time block queries
            await collection.create_index([("time_block", 1)])

            # Index on bid_type (single / block)
            await collection.create_index([("bid_type", 1)])

            # Compound index for date, type, time_block, and bid_type queries
            await collection.create_index([("scheduled_at", 1), ("type", 1), ("time_block", 1), ("bid_type", 1)])

            logger.info(f"Created indexes for collection '{self.collection_name}'")
        except Exception as e:
            logger.warning(f"Error creating indexes for collection '{self.collection_name}': {str(e)}")
    
    @property
    def collection(self):
        """Get the MongoDB collection for this repository"""
        return self.db[self.collection_name]
    
    async def get_trade_quantity_by_date_and_type(
        self, 
        datebid: str, 
        bid_type: str = "RTM"
    ) -> Dict[int, float]:
        """
        Get traded quantities grouped by time_block for a specific date and type.
        
        Args:
            datebid: Schedule date in YYYY-MM-DD format
            bid_type: Bid type (RTM, DAM, GDAM, TAM). Defaults to "RTM"
        
        Returns:
            Dictionary mapping time_block (int) to total traded quantity (float)
            Example: {1: 150.5, 2: 200.0, ...}
        """
        try:
            # Parse date string to datetime for query
            scheduled_date = datetime.strptime(datebid, "%Y-%m-%d")
            start_of_day = datetime.combine(scheduled_date.date(), datetime.min.time())
            end_of_day = datetime.combine(scheduled_date.date(), datetime.max.time())
            
            # Query with date range and type filter
            query = {
                "scheduled_at": {"$gte": start_of_day, "$lte": end_of_day},
                "type": bid_type
            }
            
            # Aggregate to sum traded values by time_block
            pipeline = [
                {"$match": query},
                {
                    "$group": {
                        "_id": "$time_block",
                        "total_traded": {"$sum": "$traded"}
                    }
                },
                {"$sort": {"_id": 1}}
            ]
            
            cursor = self.collection.aggregate(pipeline)
            results = await cursor.to_list(length=10000)
            
            # Convert to dictionary: time_block -> total_traded
            trade_quantity_map = {}
            for result in results:
                time_block = result.get("_id")
                total_traded = result.get("total_traded", 0.0)
                if time_block is not None:
                    trade_quantity_map[int(time_block)] = float(total_traded)
            
            logger.info(f"Retrieved trade quantities for {len(trade_quantity_map)} time blocks for date {datebid}, type {bid_type}")
            return trade_quantity_map
            
        except Exception as e:
            logger.error(f"Error getting trade quantity by date and type for {datebid}, {bid_type}: {str(e)}")
            return {}
    
    async def get_trade_data_by_date_and_type(
        self,
        datebid: str,
        bid_type: str = "RTM"
    ) -> List[Dict[str, Any]]:
        """
        Get all trade data records for a specific date and type.
        
        Args:
            datebid: Schedule date in YYYY-MM-DD format
            bid_type: Bid type (RTM, DAM, GDAM, TAM). Defaults to "RTM"
        
        Returns:
            List of trade data dictionaries
        """
        try:
            # Parse date string to datetime for query
            scheduled_date = datetime.strptime(datebid, "%Y-%m-%d")
            start_of_day = datetime.combine(scheduled_date.date(), datetime.min.time())
            end_of_day = datetime.combine(scheduled_date.date(), datetime.max.time())
            
            # Query with date range and type filter
            query = {
                "scheduled_at": {"$gte": start_of_day, "$lte": end_of_day},
                "type": bid_type
            }
            
            cursor = self.collection.find(query).sort("time_block", 1)
            results = await cursor.to_list(length=10000)
            
            # Convert ObjectId to string for JSON serialization
            for result in results:
                if "_id" in result:
                    result["_id"] = str(result["_id"])
                # Convert datetime fields to ISO format strings
                for field in ["scheduled_at", "inserted_at", "updated_at", "created_at", "timestamp"]:
                    if field in result and isinstance(result[field], datetime):
                        result[field] = result[field].isoformat()
            
            logger.info(f"Retrieved {len(results)} trade data records for date {datebid}, type {bid_type}")
            return results
            
        except Exception as e:
            logger.error(f"Error getting trade data by date and type for {datebid}, {bid_type}: {str(e)}")
            return []
    
    async def get_trade_status_data_with_mcp(
        self,
        datebid: str,
        bid_type: str = "RTM",
        exchange: str = "IEX"
    ) -> List[Dict[str, Any]]:
        """
        Get trade status data from bid_trade_serve with MCP from exchange_serve.
        
        Args:
            datebid: Schedule date in YYYY-MM-DD format
            bid_type: Bid type (RTM, DAM, GDAM, TAM). Defaults to "RTM"
            exchange: Exchange name (IEX, PXIL, etc.). Defaults to "IEX"
        
        Returns:
            List of trade status dictionaries with fields:
            - date: from scheduled_at
            - time_block: from time_block
            - single_bid_mw: aggregated bidded (single + block)
            - cleared_volume_mw: aggregated traded (single + block)
            - mcp_rs_mwh: from exchange_serve matching time_block
        """
        try:
            # Parse date string to datetime for query
            scheduled_date = datetime.strptime(datebid, "%Y-%m-%d")
            start_of_day = datetime.combine(scheduled_date.date(), datetime.min.time())
            end_of_day = datetime.combine(scheduled_date.date(), datetime.max.time())
            
            # Get exchange_serve collection for MCP lookup
            exchange_collection = self.db.get_collection("exchange_serve")
            
            # Aggregate pipeline: sum bidded + traded across all bid_types (single + block)
            # per time_block, then join MCP from exchange_serve.
            pipeline = [
                {
                    "$match": {
                        "scheduled_at": {"$gte": start_of_day, "$lte": end_of_day},
                        "type": bid_type
                    }
                },
                {
                    "$group": {
                        "_id": "$time_block",
                        "total_bidded": {"$sum": {"$ifNull": ["$bidded", 0]}},
                        "total_traded": {"$sum": {"$ifNull": ["$traded", 0]}},
                        "scheduled_at": {"$first": "$scheduled_at"}
                    }
                },
                {"$sort": {"_id": 1}},
                {
                    "$lookup": {
                        "from": "exchange_serve",
                        "let": {
                            "tb": "$_id",
                            "sched_date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}}
                        },
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": ["$time_block", "$$tb"]},
                                            {"$eq": ["$px_name", exchange]},
                                            {"$eq": ["$market_type", bid_type]},
                                            {
                                                "$eq": [
                                                    {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                                                    "$$sched_date"
                                                ]
                                            }
                                        ]
                                    }
                                }
                            },
                            {"$sort": {"_id": -1}},
                            {"$limit": 1},
                            {
                                "$project": {
                                    "_id": 0,
                                    "mcp": {
                                        "$cond": {
                                            "if": {"$isNumber": "$mcp"},
                                            "then": "$mcp",
                                            "else": {
                                                "$convert": {
                                                    "input": "$mcp",
                                                    "to": "double",
                                                    "onError": 0,
                                                    "onNull": 0
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        ],
                        "as": "exchange_mcp"
                    }
                },
                {
                    "$unwind": {
                        "path": "$exchange_mcp",
                        "preserveNullAndEmptyArrays": True
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "date": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$scheduled_at"
                            }
                        },
                        "time_block": "$_id",
                        "single_bid_mw": "$total_bidded",
                        "cleared_volume_mw": "$total_traded",
                        "mcp_rs_mwh": {"$ifNull": ["$exchange_mcp.mcp", 0]}
                    }
                }
            ]
            
            cursor = self.collection.aggregate(pipeline)
            results = await cursor.to_list(length=10000)
            
            logger.info(f"Retrieved {len(results)} trade status records with MCP for date {datebid}, type {bid_type}")
            return results
            
        except Exception as e:
            logger.error(f"Error getting trade status data with MCP for {datebid}, {bid_type}: {str(e)}")
            return []

    async def adjustment_noc_buy_sell(self, scheduled_at: str) -> Dict[int, dict]:
        """
        Fetch DAM cleared bid quantity block-wise
        for NOC buy/sell adjustment.
        """

        try:

            start_date = datetime.strptime(scheduled_at, "%Y-%m-%d")
            end_date = start_date + timedelta(days=1)
            pipeline = [
                {
                    "$match": {
                        "scheduled_at": {
                            "$gte": start_date,
                            "$lt": end_date
                        },
                        "data_type": "bid",
                        "type": "DAM",
                        "time_block": {"$gte": 1, "$lte": 96}
                    }
                },
                {
                    "$group": {
                        "_id": "$time_block",
                        "bid_cleared": {"$sum": {"$ifNull": ["$traded", 0]}},
                        "buy_sell_flag": {"$first": "$buy_sell_flag"}
                    }
                }
            ]

            cursor = self.collection.aggregate(pipeline)
            result = await cursor.to_list(length=10000)

            block_map: Dict[int, dict] = {}

            for doc in result:
                block_map[doc["_id"]] = {
                    "bid_cleared": float(doc.get("bid_cleared", 0)),
                    "buy_sell_flag": doc.get("buy_sell_flag")
                }

            logger.info(
                f"DAM NOC adjustment fetched successfully for date={scheduled_at}, "
                f"blocks_found={len(block_map)}"
            )
            

            return block_map

        except Exception as e:
            logger.exception(
                f"Error while fetching DAM NOC adjustment data for date={scheduled_at}: {str(e)}"
            )
            return {}

    @staticmethod
    def from_period_to_time_block(from_period: str) -> int:
        """
        Convert FromPeriodId (e.g., "02:00") to a time_block integer (1-96).
        
        Mapping: 00:00 -> 1, 00:15 -> 2, ..., 23:45 -> 96
        """
        try:
            h, m = map(int, from_period.strip().split(":"))
            return (h * 60 + m) // 15 + 1
        except Exception:
            logger.warning(f"Could not convert period '{from_period}' to time_block")
            return 0

    async def update_bidded_by_block(
        self,
        delivery_date_ts: int,
        time_block: int,
        bid_type: str,
        bidded_value: float
    ):
        """
        Update the bidded value for a single (scheduled_at, time_block, type) record.
        
        Args:
            delivery_date_ts: Unix timestamp of delivery date (converted to UTC date)
            time_block: Time block number (1-96)
            bid_type: Market type ("RTM" or "DAM")
            bidded_value: New bidded value to set
        """
        try:
            from datetime import timezone
            delivery_dt = datetime.fromtimestamp(delivery_date_ts, tz=timezone.utc)
            start_of_day = delivery_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day + timedelta(days=1)

            filter_query = {
                "scheduled_at": {"$gte": start_of_day, "$lt": end_of_day},
                "time_block": time_block,
                "type": bid_type,
                "bid_type": "single",
                "data_type": "bid"
            }
            update_query = {"$set": {"bidded": bidded_value}}

            result = await self.collection.update_one(filter_query, update_query)
            logger.info(
                f"update_bidded_by_block: type={bid_type}, block={time_block}, "
                f"date={start_of_day.strftime('%Y-%m-%d')}, bidded={bidded_value}, "
                f"matched={result.matched_count}, modified={result.modified_count}"
            )
            return result
        except Exception as e:
            logger.error(f"Error in update_bidded_by_block: {str(e)}")
            return None

    async def reset_bidded_for_date(
        self,
        delivery_date_ts: int,
        bid_type: str
    ):
        """
        Reset bidded to 0 for ALL time blocks on a given delivery date + type.
        Used by DAM cancel_bid which cancels all blocks at once.
        
        Args:
            delivery_date_ts: Unix timestamp of delivery date
            bid_type: Market type ("RTM" or "DAM")
        """
        try:
            from datetime import timezone
            delivery_dt = datetime.fromtimestamp(delivery_date_ts, tz=timezone.utc)
            start_of_day = delivery_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day + timedelta(days=1)

            filter_query = {
                "scheduled_at": {"$gte": start_of_day, "$lt": end_of_day},
                "type": bid_type,
                "bid_type": "single",
                "data_type": "bid"
            }
            update_query = {"$set": {"bidded": 0}}

            result = await self.collection.update_many(filter_query, update_query)
            logger.info(
                f"reset_bidded_for_date: type={bid_type}, "
                f"date={start_of_day.strftime('%Y-%m-%d')}, "
                f"matched={result.matched_count}, modified={result.modified_count}"
            )
            return result
        except Exception as e:
            logger.error(f"Error in reset_bidded_for_date: {str(e)}")
            return None