"""
DSM Repository
Handles data access for DSM-related operations using MongoDB aggregation pipelines
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from .base_repository import GenericRepository

logger = logging.getLogger(__name__)


class DsmRepository:
    """
    Repository for DSM data operations.
    Contains all MongoDB aggregation pipelines for DSM-related queries.
    """

    def __init__(self):
        self.repository = GenericRepository()

    async def get_dsm_data_pipeline(
        self, 
        collection_name: str, 
        scheduled_at: datetime
    ) -> List[Dict[str, Any]]:
        """
        Get DSM data for a specific schedule date using aggregation pipeline.
        
        Args:
            collection_name: Name of the MongoDB collection
            scheduled_at: Schedule date to filter by
            
        Returns:
            List of DSM records
        """
        try:
            pipeline = [
                {"$match": {"scheduled_at": scheduled_at, "type": "IDF"}},
                {
                    "$project": {
                        "_id": 0,
                        "frequency": 1,
                        "dsm_rate": 1,
                        "time_block": 1,
                        "schedule_date": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$scheduled_at"
                            }
                        },  # Format scheduled_at as date only
                        "net_dsm_amount": {
                            "$divide": ["$net_dsm_amount", 100000]
                        },
                        "odud": {  # this is overdrawal underdrawal
                            "$round": [
                                {"$subtract": ["$drawal", "$schedule"]},
                                2,  # number of decimal places
                            ]
                        },
                    }
                },
                {"$sort": {"time_block": 1}},
            ]

            logger.info(f"[DsmRepository] Querying collection: {collection_name}")
            logger.info(f"[DsmRepository] Scheduled at: {scheduled_at}")
            logger.info(f"[DsmRepository] Pipeline: {pipeline}")

            result = self.repository.aggregate(
                collection_name, pipeline, max_time_ms=30000
            )  # 30 second timeout
            
            logger.info(f"[DsmRepository] Aggregation result type: {type(result)}")

            if not result:
                historical_collection = collection_name + "_historical"
                result = self.repository.aggregate(
                    historical_collection, pipeline, max_time_ms=30000
                )

            return result
        except Exception as e:
            logger.exception(f"[DsmRepository] Failed to execute DSM data pipeline: {str(e)}")
            raise

    async def get_dsm_historical_data_pipeline(
        self,
        collection_name: str,
        from_date: datetime,
        to_date: datetime
    ) -> List[Dict[str, Any]]:
        """
        Get historical DSM data for a date range using aggregation pipeline.
        
        Args:
            collection_name: Name of the MongoDB collection
            from_date: Start date
            to_date: End date
            
        Returns:
            List containing historical DSM data
        """
        try:
            logger.info(f"[DsmRepository] Fetching historical DSM data from {from_date} to {to_date}")
            pipeline = [
                {
                    "$match": {
                        "scheduled_at": {
                            "$gte": from_date,
                            "$lte": to_date,
                        },
                        "type": "IDF",
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$scheduled_at"
                            }
                        },
                        "positive_sum": {
                            "$sum": {
                                "$cond": [
                                    {"$gt": [{"$subtract": ["$drawal", "$schedule"]}, 0]},
                                    {"$subtract": ["$drawal", "$schedule"]},
                                    0,
                                ]
                            }
                        },
                        "negative_sum": {
                            "$sum": {
                                "$cond": [
                                    {"$lt": [{"$subtract": ["$drawal", "$schedule"]}, 0]},
                                    {"$subtract": ["$drawal", "$schedule"]},
                                    0,
                                ]
                            }
                        },
                        "total_net_dsm_amount": {"$sum": "$net_dsm_amount"},
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "schedule_date": "$_id",  # _id is already the date string from grouping
                        "odud": {
                            "positive_sum": {"$round": ["$positive_sum", 2]},
                            "negative_sum": {"$round": ["$negative_sum", 2]},
                            # "total": {
                            #     "$round": [
                            #         {"$add": ["$positive_sum", "$negative_sum"]},
                            #         2,
                            #     ]
                            # },
                        },
                        "total_net_dsm_amount": {"$round": ["$total_net_dsm_amount", 2]},
                    }
                },
                {"$sort": {"schedule_date": 1}},
                # 👇 add a final stage to calculate overall totals
                {
                    "$group": {
                        "_id": None,
                        "dates": {"$push": "$$ROOT"},  # keep per-date results
                        "grand_total": {"$sum": "$total_net_dsm_amount"},
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "dates": 1,  # keep all schedule_date results
                        "grand_totals": {
                            "total": {"$round": ["$grand_total", 2]},
                        },
                    }
                },
            ]

            result = self.repository.aggregate(collection_name, pipeline)
            logger.info(f"[DsmRepository] Retrieved {len(result) if result else 0} result(s) from {collection_name}")
            if result and len(result) > 0:
                dates_count = len(result[0].get('dates', [])) if isinstance(result[0], dict) else 0
                logger.info(f"[DsmRepository] Found {dates_count} unique dates in result")
            return result
        except Exception as e:
            logger.exception(f"[DsmRepository] Failed to execute historical DSM data pipeline: {str(e)}")
            raise

    async def get_dsm_historical_table_data_pipeline(
        self,
        collection_name: str,
        from_date: Optional[datetime],
        to_date: Optional[datetime],
        time_block: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get historical DSM table data for a date range and time block using aggregation pipeline.
        
        Args:
            collection_name: Name of the MongoDB collection
            from_date: Start date
            to_date: End date
            time_block: Optional time block filter
            
        Returns:
            List containing historical DSM table data
        """
        try:
            # Validate required parameters
            if from_date is None or to_date is None:
                raise ValueError("from_date and to_date are required")
                
            # Build match criteria
            match_criteria = {
                "scheduled_at": {
                    "$gte": from_date,
                    "$lte": to_date,
                },
                "type": "IDF",
            }
            
            # Add time_block filter if provided
            if time_block:
                match_criteria["time_block"] = int(time_block)

            pipeline = [
                {"$match": match_criteria},
                # 👇 calculate odud before grouping
                {
                    "$addFields": {
                        "odud": {
                            "$round": [
                                {"$subtract": ["$drawal", "$schedule"]},
                                2,
                            ]
                        }
                    }
                },
                {
                    "$group": {
                        "_id": "$scheduled_at",
                        "positive_sum": {
                            "$sum": {
                                "$cond": [
                                    {"$gt": ["$odud", 0]},
                                    "$odud",
                                    0,
                                ]
                            }
                        },
                        "negative_sum": {
                            "$sum": {
                                "$cond": [
                                    {"$lt": ["$odud", 0]},
                                    "$odud",
                                    0,
                                ]
                            }
                        },
                        "total_dsm_rate": {"$sum": "$dsm_rate"},
                        "total_Raw_Frequency": {"$sum": "$frequency"},
                        "total_odud": {"$sum": "$odud"},
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "schedule_date": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$_id"
                            }
                        },
                        "odud": {
                            "positive_sum": {"$round": ["$positive_sum", 2]},
                            "negative_sum": {"$round": ["$negative_sum", 2]},
                            "total": {
                                "$round": [
                                    {"$add": ["$positive_sum", "$negative_sum"]},
                                    2,
                                ]
                            },
                        },
                        "total_dsm_rate": {"$round": ["$total_dsm_rate", 2]},
                        "total_Raw_Frequency": {"$round": ["$total_Raw_Frequency", 2]},
                        "total_odud": {"$round": ["$total_odud", 2]},
                    }
                },
                {"$sort": {"schedule_date": 1}},
            ]

            result = self.repository.aggregate(collection_name, pipeline)
            return result
        except Exception as e:
            logger.exception(f"[DsmRepository] Failed to execute historical DSM table data pipeline: {str(e)}")
            raise   

    async def test_collection_access(self, collection_name: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Test collection access by fetching a limited number of documents.
        
        Args:
            collection_name: Name of the MongoDB collection
            limit: Maximum number of documents to fetch
            
        Returns:
            List of sample documents
        """
        try:
            logger.info(f"[DsmRepository] Testing collection access: {collection_name}")
            result = self.repository.get_all(collection_name, limit=limit)
            logger.info(f"[DsmRepository] Collection test - found {len(result)} documents")
            if result:
                logger.info(f"[DsmRepository] Sample document: {result[0]}")
            return result
        except Exception as e:
            logger.exception(f"[DsmRepository] Failed to test collection access: {str(e)}")
            raise


# Create repository instance
dsm_repository = DsmRepository()
