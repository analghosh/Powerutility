"""
Price Quantity Response Repository
Handles database operations for price quantity response collection
"""
from typing import List, Dict, Any, Optional, Union
import logging

from .base_repository import GenericRepository

logger = logging.getLogger(__name__)


class PriceQuantityResponseRepository(GenericRepository):
    """
    Repository for 'price_quantity_response' collection.
    PHP Model: PriceQuantityResponse
    Collection: 'price_quantity_response'
    
    Note: Methods that return results must support ->toarray() conversion
    """
    
    def __init__(self, collection_name: Optional[str] = None):
        super().__init__()
        self.collection_name = collection_name or "price_quantity_response"
        logger.info(f"PriceQuantityResponseRepository initialized with collection: {self.collection_name}")
        self._create_collection_if_not_exists()
        self._create_indexes()
    
    def _create_collection_if_not_exists(self):
        """
        Create collection if it does not exist.
        Matches pattern used in ingestion services (libs/database/base_repository.py)
        """
        try:
            # Access db property to ensure database connection is initialized
            db = self.db
            db_name = self.db_name or "unknown"
            
            if self.collection_name not in db.list_collection_names():
                db.create_collection(self.collection_name)
                logger.info(f"Created collection '{self.collection_name}' in database '{db_name}'")
            else:
                logger.debug(f"Collection '{self.collection_name}' already exists in database '{db_name}'")
        except Exception as e:
            logger.warning(f"Error checking/creating collection '{self.collection_name}': {str(e)}")
            # Continue anyway - MongoDB will create collection on first insert
    
    def _create_indexes(self):
        """Create necessary indexes for efficient queries (matching ingestion service pattern)"""
        try:
            collection = self.collection
            
            # Index on inserted_date for date-based queries
            collection.create_index([("inserted_date", 1)])
            
            # Index on from_period_code for period-based queries
            collection.create_index([("from_period_code", 1)])
            
            # Index on cronJob_time for time-based queries
            collection.create_index([("cronJob_time", 1)])
            
            # Compound index for date and time queries
            collection.create_index([("inserted_date", 1), ("cronJob_time", 1)])
            
            # Compound index for date and period queries
            collection.create_index([("inserted_date", 1), ("from_period_code", 1)])
            
            logger.info(f"Created indexes for collection '{self.collection_name}'")
        except Exception as e:
            logger.warning(f"Error creating indexes for collection '{self.collection_name}': {str(e)}")
            # Continue anyway - indexes are optional for functionality
    
    @property
    def collection(self):
        """Get the MongoDB collection for this repository"""
        return self.db[self.collection_name]
    
    async def get_rtm_mcp_timeblock(self, datebid: str) -> Union[Dict[str, Dict[str, Any]], int]:
        """
        Get RTM MCP timeblock data for a specific date.
        
        PHP: $this->PriceQuantityResponse->getRtmMcpTimeblock($datebid)
        Query: orderBy('_id', 'desc')->where('inserted_date', $date)->first()
               Then: where('inserted_date', $date)->where('cronJob_time', $last_record['cronJob_time'])
                     ->get()->keyBy("from_period_code")
        
        Args:
            datebid: Inserted date in YYYY-MM-DD format
        
        Returns:
            Dictionary keyed by from_period_code if data exists, 0 otherwise
            Must support ->toarray() method for PHP compatibility
        """
        try:
            # Get last record for the date
            last_record = await self.collection.find_one(
                {"inserted_date": datebid},
                sort=[("_id", -1)]  # descending order
            )
            
            if last_record is None:
                logger.info(f"No RTM MCP timeblock data found for date {datebid}")
                return 0
            
            cron_job_time = last_record.get("cronJob_time")
            
            # Get all records for the date and cronJob_time, keyed by from_period_code
            query = {
                "inserted_date": datebid,
                "cronJob_time": cron_job_time
            }
            cursor = self.collection.find(query)
            results = await cursor.to_list(length=10000)
            
            # Convert to dictionary keyed by from_period_code
            result_dict = {}
            for result in results:
                from_period = result.get("from_period_code")
                if from_period is not None:
                    # Convert ObjectId to string for JSON serialization
                    if "_id" in result:
                        result["_id"] = str(result["_id"])
                    result_dict[from_period] = result
            
            logger.info(f"Retrieved {len(result_dict)} RTM MCP timeblock records for date {datebid}")
            return result_dict
            
        except Exception as e:
            logger.error(f"Error getting RTM MCP timeblock for date {datebid}: {str(e)}")
            return 0
    
    async def get_rtm_mcp_from_exchange(self, datebid: str, exchange: str = "IEX") -> Union[Dict[str, Dict[str, Any]], int]:
        """
        Get RTM MCP data from exchange_serve collection (IEX exchange data).
        This is an alternative to get_rtm_mcp_timeblock that uses exchange data directly.
        
        Args:
            datebid: Schedule date in YYYY-MM-DD format
            exchange: Exchange name (default: "IEX")
        
        Returns:
            Dictionary keyed by time_block (1-96) with MCP data, or 0 if no data found
            Format: {time_block: {"mcp": value, "time_block": time_block, ...}}
        """
        try:
            # Use exchange_serve collection
            exchange_collection = self.db.get_collection("exchange_serve")
            
            pipeline = [
                {
                    "$match": {
                        "$expr": {
                            "$eq": [
                                {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                                datebid
                            ]
                        },
                        "market_type": "RTM",
                        "px_name": exchange
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "time_block": 1,
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
                },
                {
                    "$sort": {"time_block": 1}
                }
            ]
            
            cursor = exchange_collection.aggregate(pipeline)
            results = await cursor.to_list(length=10000)
            
            if not results:
                logger.info(f"No RTM MCP data found in exchange_serve for date {datebid}, exchange {exchange}")
                return 0
            
            # Convert to dictionary keyed by time_block
            # Note: time_block in exchange_serve is 1-96, matches from_period_code format
            result_dict = {}
            for result in results:
                time_block = result.get("time_block")
                if time_block is not None:
                    # Store with time_block as key (matches from_period_code in price_quantity_response)
                    result_dict[time_block] = result
            
            logger.info(f"Retrieved {len(result_dict)} RTM MCP records from {exchange} exchange_serve for date {datebid}")
            return result_dict
            
        except Exception as e:
            logger.error(f"Error getting RTM MCP from exchange_serve for date {datebid}: {str(e)}")
            return 0
    
    async def get_dam_mcp_timeblock(self, datebid: str) -> Union[Dict[str, Dict[str, Any]], int]:
        """
        Get DAM MCP timeblock data for a specific date.
        
        PHP: $this->PriceQuantityResponse->getDamMcpTimeblock($datebid)
        Query: orderBy('_id', 'desc')->where('inserted_date', $date)->first()
               Then: where('inserted_date', $date)->where('cronJob_time', $last_record['cronJob_time'])
                     ->get()->keyBy("from_period_code")
        
        Args:
            datebid: Inserted date in YYYY-MM-DD format
        
        Returns:
            Dictionary keyed by from_period_code if data exists, 0 otherwise
            Must support ->toarray() method for PHP compatibility
        """
        try:
            # Get last record for the date
            last_record = await self.collection.find_one(
                {"inserted_date": datebid},
                sort=[("_id", -1)]  # descending order
            )
            
            if last_record is None:
                logger.info(f"No DAM MCP timeblock data found for date {datebid}")
                return 0
            
            cron_job_time = last_record.get("cronJob_time")
            
            # Get all records for the date and cronJob_time, keyed by from_period_code
            query = {
                "inserted_date": datebid,
                "cronJob_time": cron_job_time
            }
            cursor = self.collection.find(query)
            results = await cursor.to_list(length=10000)
            
            # Convert to dictionary keyed by from_period_code
            result_dict = {}
            for result in results:
                from_period = result.get("from_period_code")
                if from_period is not None:
                    # Convert ObjectId to string for JSON serialization
                    if "_id" in result:
                        result["_id"] = str(result["_id"])
                    result_dict[from_period] = result
            
            logger.info(f"Retrieved {len(result_dict)} DAM MCP timeblock records for date {datebid}")
            return result_dict
            
        except Exception as e:
            logger.error(f"Error getting DAM MCP timeblock for date {datebid}: {str(e)}")
            return 0
    
    async def get_dam_mcp_from_exchange(self, datebid: str, exchange: str = "IEX") -> Union[Dict[str, Dict[str, Any]], int]:
        """
        Get DAM MCP data from exchange_serve collection (IEX exchange data).
        This is an alternative to get_dam_mcp_timeblock that uses exchange data directly.
        
        Args:
            datebid: Schedule date in YYYY-MM-DD format
            exchange: Exchange name (default: "IEX")
        
        Returns:
            Dictionary keyed by time_block (1-96) with MCP data, or 0 if no data found
            Format: {time_block: {"mcp": value, "time_block": time_block, ...}}
        """
        try:
            # Use exchange_serve collection
            exchange_collection = self.db.get_collection("exchange_serve")
            
            pipeline = [
                {
                    "$match": {
                        "$expr": {
                            "$eq": [
                                {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                                datebid
                            ]
                        },
                        "market_type": "DAM",
                        "px_name": exchange
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "time_block": 1,
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
                },
                {
                    "$sort": {"time_block": 1}
                }
            ]
            
            cursor = exchange_collection.aggregate(pipeline)
            results = await cursor.to_list(length=10000)
            
            if not results:
                logger.info(f"No DAM MCP data found in exchange_serve for date {datebid}, exchange {exchange}")
                return 0
            
            # Convert to dictionary keyed by time_block
            # Note: time_block in exchange_serve is 1-96, matches from_period_code format
            result_dict = {}
            for result in results:
                time_block = result.get("time_block")
                if time_block is not None:
                    # Store with time_block as key (matches from_period_code in price_quantity_response)
                    result_dict[time_block] = result
            
            logger.info(f"Retrieved {len(result_dict)} DAM MCP records from {exchange} exchange_serve for date {datebid}")
            return result_dict
            
        except Exception as e:
            logger.error(f"Error getting DAM MCP from exchange_serve for date {datebid}: {str(e)}")
            return 0

