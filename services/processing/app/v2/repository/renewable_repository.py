"""
Renewable V2 Repository
Data access layer for renewable energy data
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

from ...core.config import settings

logger = logging.getLogger(__name__)


class RenewableRepository:
    """
    Repository for renewable energy data operations.
    Handles data retrieval using the generic repository pattern.
    """

    def __init__(self):
        from .base_repository import GenericRepository
        self.repository = GenericRepository()
        
        self.mongodb_uri = settings.MONGODB_URI
        self.database_name = settings.MONGODB_DATABASE
        self.serve_collection = "renewable_serve"
        self.historical_collection = "renewable_serve_historical"

    async def get_max_updated_at_from_renewable(self, date_str: str) -> Optional[datetime]:
        """
        Get the maximum updated_at timestamp for a given date.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            Maximum updated_at timestamp or None if not found
        """
        try:
            # Parse date for query
            target_date = datetime.strptime(date_str, "%Y-%m-%d")
            
            # Using get_all_async with sorts to get max updated_at efficiently
            # Filter for the specific day

            result = await self.repository.get_all_async(
                collection_name=self.serve_collection,
                filter={},
                sort=[("updated_at", -1)],
                limit=1,
                projection={"updated_at": 1}
            )
            
            if result and result[0].get("updated_at"):
                logger.info(f"Retrieved max updated_at for renewable data on date: {date_str}")
                val = result[0]["updated_at"]
                if isinstance(val, str):
                    try:
                        return datetime.fromisoformat(val)
                    except ValueError:
                        return None
                return val
            
            logger.info(f"No updated_at field found for renewable data on date: {date_str}")
            return None
            
        except Exception as e:
            logger.error(f"Error getting max updated_at for renewable data on date {date_str}: {str(e)}")
            return None

    async def get_schedule_by_date(self, date_str: str) -> List[Dict[str, Any]]:
        """
        Get renewable schedule data for a specific date.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            List of renewable schedule records
        """
        try:
            # Use the repository's database connection
            db = self.repository.db
            collection = db[self.serve_collection]
            
            # Parse date for query
            target_date = datetime.strptime(date_str, "%Y-%m-%d")
            
            # Query for renewable schedule data
            query = {
                "scheduled_at": {
                    "$gte": target_date,
                    "$lt": target_date.replace(hour=23, minute=59, second=59)
                }
            }
            
            # Project only needed fields
            projection = {
                "scheduled_at": 1,
                "time_block": 1,
                "type": 1,
                "technology": 1,
                "schedule_volume": 1,
                "plant_id": 1,
                "category": 1,
                "c_s": 1
            }
            
            # Execute query with sorting
            cursor = collection.find(query, projection).sort([
                ("technology", 1),
                ("type", 1),
                ("time_block", 1)
            ])
            
            results = await cursor.to_list(None)
            
            logger.info(f"Retrieved {len(results)} renewable schedule records for date: {date_str}")
            return results
            
        except Exception as e:
            logger.error(f"Error querying renewable schedule for date {date_str}: {str(e)}")
            return []

    async def get_availability_by_date(self, date_str: str) -> List[Dict[str, Any]]:
        """
        Get renewable availability data for a specific date.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            List of renewable availability records
        """
        try:
            # Use the repository's database connection
            db = self.repository.db
            collection = db[self.serve_collection]
            
            # Parse date for query
            target_date = datetime.strptime(date_str, "%Y-%m-%d")
            
            # Query for renewable availability data
            query = {
                "scheduled_at": {
                    "$gte": target_date,
                    "$lt": target_date.replace(hour=23, minute=59, second=59)
                }
            }
            
            # Project only needed fields
            projection = {
                "scheduled_at": 1,
                "time_block": 1,
                "schedule_volume": 1,
                "technology": 1,
                "plant_id": 1,
                "category": 1,
                "c_s": 1
            }
            
            # Execute query with sorting by time_block
            cursor = collection.find(query, projection).sort("time_block", 1)
            
            results = await cursor.to_list(None)
            
            logger.info(f"Retrieved {len(results)} renewable availability records for date: {date_str}")
            return results
            
        except Exception as e:
            logger.error(f"Error querying renewable availability for date {date_str}: {str(e)}")
            return []

    async def get_renewable_summary(self, date_str: str) -> Dict[str, Any]:
        """
        Get renewable energy summary for a specific date.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            Dict containing renewable energy summary
        """
        try:
            # Use the repository's database connection
            db = self.repository.db
            collection = db[self.serve_collection]
            
            # Parse date for query
            target_date = datetime.strptime(date_str, "%Y-%m-%d")
            
            # Aggregation pipeline for summary
            pipeline = [
                {
                    "$match": {
                        "scheduled_at": {
                            "$gte": target_date,
                            "$lt": target_date.replace(hour=23, minute=59, second=59)
                        }
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "technology": "$technology",
                            "type": "$type"
                        },
                        "total_volume": {"$sum": "$schedule_volume"},
                        "avg_volume": {"$avg": "$schedule_volume"},
                        "max_volume": {"$max": "$schedule_volume"},
                        "min_volume": {"$min": "$schedule_volume"},
                        "count": {"$sum": 1}
                    }
                },
                {
                    "$sort": {
                        "_id.technology": 1,
                        "_id.type": 1
                    }
                }
            ]
            
            cursor = collection.aggregate(pipeline)
            try:
                results = await cursor.to_list(None)
            finally:
                cursor.close()
            
            # Format results
            summary = {
                "date": date_str,
                "summary_by_technology": [],
                "total_records": sum(r["count"] for r in results),
                "total_volume": sum(r["total_volume"] for r in results)
            }
            
            for result in results:
                summary["summary_by_technology"].append({
                    "technology": result["_id"]["technology"],
                    "type": result["_id"]["type"],
                    "total_volume": result["total_volume"],
                    "average_volume": result["avg_volume"],
                    "max_volume": result["max_volume"],
                    "min_volume": result["min_volume"],
                    "record_count": result["count"]
                })
            
            logger.info(f"Generated renewable summary for date: {date_str}")
            return summary
            
        except Exception as e:
            logger.error(f"Error generating renewable summary for date {date_str}: {str(e)}")
            return {
                "date": date_str,
                "summary_by_technology": [],
                "total_records": 0,
                "total_volume": 0,
                "error": str(e)
            }

    async def get_renewable_by_plant(self, date_str: str, plant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get renewable data by plant for a specific date.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            plant_id: Optional plant ID filter
            
        Returns:
            List of renewable records filtered by plant
        """
        try:
            # Use the repository's database connection
            db = self.repository.db
            collection = db[self.serve_collection]
            
            # Parse date for query
            target_date = datetime.strptime(date_str, "%Y-%m-%d")
            
            # Build query
            query = {
                "scheduled_at": {
                    "$gte": target_date,
                    "$lt": target_date.replace(hour=23, minute=59, second=59)
                }
            }
            
            if plant_id:
                query["plant_id"] = plant_id
            
            # Execute query with sorting
            cursor = collection.find(query).sort([
                ("plant_id", 1),
                ("time_block", 1)
            ])
            
            results = await cursor.to_list(None)
            
            logger.info(f"Retrieved {len(results)} renewable records by plant for date: {date_str}, plant_id: {plant_id}")
            return results
            
        except Exception as e:
            logger.error(f"Error querying renewable by plant for date {date_str}, plant_id {plant_id}: {str(e)}")
            return []

# Global singleton
renewable_repository = RenewableRepository()