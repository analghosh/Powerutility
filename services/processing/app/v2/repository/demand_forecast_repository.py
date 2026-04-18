"""
Demand Forecast Repository
Handles data access for demand forecast operations using MongoDB aggregation pipelines
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import date, datetime

from .base_repository import GenericRepository

logger = logging.getLogger(__name__)


class DemandForecastRepository:
    """
    Repository for Demand Forecast data operations.
    Contains all MongoDB aggregation pipelines for demand forecast queries.
    """

    def __init__(self):
        self.repository = GenericRepository()

    def get_demand_forecast_data_pipeline(
        self,
        collection_name: str,
        target_date: date,
        market_type: str
    ) -> List[Dict[str, Any]]:
        """
        Get demand forecast data for a specific date and market type using aggregation pipeline.
        
        Args:
            collection_name: Name of the MongoDB collection
            target_date: The target date to query
            market_type: Either "IDF" or "DAF"
            
        Returns:
            List of demand forecast records
        """
        try:
            # Convert date to naive datetime for MongoDB compatibility
            # The database stores naive datetime objects, so we need to match that format
            from datetime import datetime
            target_datetime = datetime.combine(target_date, datetime.min.time())
            
            pipeline = [
                {
                    "$match": {
                        "scheduled_at": target_datetime,  # Using scheduled_at (Date type) from entity
                        "type": market_type  # Using type field from entity
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "actual": 1,  # actual field from entity
                        "forecast": 1,  # forecast field from entity
                        "time_block": 1,  # time_block field from entity
                        "scheduled_at": 1,  # scheduled_at field from entity
                        "mape": 1,  # mape field from entity (lowercase)
                        "mpe": 1,  # mpe field from entity (lowercase)
                        "rostering": 1,  # rostering field from entity
                        "comp_udm": 1,  # comp_udm field from entity
                        "comp_act_demand": 1,  # comp_act_demand field from entity
                        # Convert mape to MAPE for response (to match v1 API format)
                        "MAPE": {
                            "$cond": [
                                {"$gt": ["$mape", 0]},
                                {"$concat": [{"$toString": {"$round": ["$mape", 2]}}, "%"]},
                                "0%"
                            ]
                        }
                    }
                },
                {
                    "$sort": {"time_block": 1}  # ascending order
                }
            ]
            
            result = self.repository.aggregate(collection_name, pipeline)
            return result
        except Exception as e:
            logger.exception(f"[DemandForecastRepository] Failed to execute demand forecast data pipeline: {str(e)}")
            raise

    def get_monthly_average_mape_pipeline(
        self,
        collection_name: str,
        match_stage: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Get monthly average MAPE data using aggregation pipeline.
        
        Args:
            collection_name: Name of the MongoDB collection
            match_stage: Match criteria for the aggregation
            
        Returns:
            List of monthly average MAPE records
        """
        try:
            pipeline = [
                {"$match": match_stage},
                # only average valid MAPEs - using proper entity field name: mape (lowercase)
                # Check for double type (MongoDB's numeric type) or use $exists to ensure field exists
                {"$match": {"mape": {"$exists": True, "$ne": None}}},
                {"$group": {
                    "_id": "$type",
                    "avg_mape": {"$avg": "$mape"},  # Using mape (lowercase) from entity
                    "count": {"$sum": 1}
                }}
            ]
            
            result = self.repository.aggregate(collection_name, pipeline)
            return result
        except Exception as e:
            logger.exception(f"[DemandForecastRepository] Failed to execute monthly average MAPE pipeline: {str(e)}")
            raise
    
    def get_data_pipeline(
    self,
    collection_name: str,
    from_block: int,
    to_block: int,
    scheduled_at
) -> List[Dict[str, Any]]:
        """
    Get filtered forecast data using aggregation pipeline.
    
    Args:
        collection_name: Name of the MongoDB collection.
        from_block: Starting time block (integer).
        to_block: Ending time block (integer).
        scheduled_at: The scheduled date (datetime object).
        
    Returns:
        List of forecast records.
    """
        try:
            pipeline = [
                {"$match": {
                    "scheduled_at": scheduled_at,
                    "type": "IDF",
                    "time_block": {"$gte": from_block, "$lte": to_block}
                }},
                {"$project": {"_id": 0}}
            ]
            
            result = self.repository.aggregate(collection_name, pipeline)
            return result
        except Exception as e:
            logger.exception(f"[DemandForecastRepository] Failed to execute monthly average MAPE pipeline: {str(e)}")
            raise


    async def get_max_updated_at(self, collection_name: str, date_str: str) -> Optional[datetime]:
        """
        Get the maximum updated_at timestamp for a given date.
        
        Args:
            collection_name: Name of the MongoDB collection
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            Maximum updated_at timestamp or None if not found
        """
        try:
            # Assuming scheduled_at is ISODate in Mongo, similar to other methods
            # But wait, get_demand_forecast_data_pipeline uses datetime.combine(target_date, datetime.min.time())
            # which produces a datetime object. GenericRepository usually handles basic types.
            

            result = await self.repository.get_all_async(
                collection_name=collection_name,
                filter={},
                sort=[("updated_at", -1)],
                limit=1,
                projection={"updated_at": 1}
            )
            
            if result and result[0].get("updated_at"):
                val = result[0]["updated_at"]
                if isinstance(val, str):
                    try:
                        return datetime.fromisoformat(val)
                    except ValueError:
                        return None
                return val
            
            return None
            
        except Exception as e:
            logger.error(f"[DemandForecastRepository] Error getting max updated_at for date {date_str}: {str(e)}")
            return None


# Create repository instance
demand_forecast_repository = DemandForecastRepository()
