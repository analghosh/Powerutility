"""
Limit Values Repository (main table operations)
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from .base_repository import GenericRepository
from ...core.config import settings

logger = logging.getLogger(__name__)


class LimitValuesRepository:
    def __init__(self):
        self.repository = GenericRepository()
        self.collection_name = getattr(settings, 'MONGODB_LIMIT_VALUES_COLLECTION', 'limit_values')

    async def ensure_collection_exists(self, collection_name: str):
        """
        Ensures the collection exists and has the required indexes.
        """
        try:
            # Check if collection exists (optional, could just ensure indexes)
            # Create indexes
            # Compound unique index for the business key
            await self.repository.create_index(
                collection_name,
                [
                    ("scheduled_at", 1),
                    ("block", 1),
                    ("id", 1),
                    ("market", 1),
                    ("category", 1),
                    ("area", 1)
                ],
                unique=True,
                name="limit_values_unique_idx"
            )
            
            # Index for efficient querying by date
            await self.repository.create_index(
                collection_name,
                [("scheduled_at", -1)],
                name="scheduled_at_desc_idx"
            )
            
            # Index for querying by id/block
            await self.repository.create_index(
                collection_name,
                [("id", 1), ("block", 1)],
                name="id_block_idx"
            )

        except Exception as e:
            logger.error(f"Failed to ensure collection {collection_name}: {e}")
            # Don't raise, just log - we might still be able to operate


    async def get_latest_inserted_at_data_pipeline_with_filters(
        self,
        collection_name: str,
        filters: Dict[str, Any] = None,
        date_filter: str = None
    ) -> List[Dict[str, Any]]:
        """
        Returns all records with latest inserted_at per key for a given date (no pagination).
        If date_filter is not provided, it uses the latest available date.
        For each unique key (block, id, market, category, area), returns the record with the latest inserted_at.
        """
        try:
            # Ensure indexes exist even on read-first scenarios
            await self.ensure_collection_exists(collection_name)
            
            pipeline = []
            
            # 1. Date matching stage
            if date_filter:
                filter_date_dt = datetime.strptime(date_filter, "%Y-%m-%d")
                
                pipeline.append({
                    "$match": 
                            {
                                "scheduled_at": {
                                    "$gte": filter_date_dt,
                                    "$lt": filter_date_dt + timedelta(days=1)
                                },
                                "category": "Limits"
                            }
                })
            else:
                today = datetime.now()
                start_time = today.replace(hour=0, minute=0, second=0, microsecond=0)
                end_time = start_time + timedelta(days=1)
                pipeline.append({
                    "$match": {
                        "scheduled_at": {
                            "$gte": start_time,
                            "$lt": end_time
                        }
                    }
                })

            # 2. Add other filters
            filters = filters or {}
            additional_match = {}
            if "block" in filters:
                additional_match["block"] = filters["block"]
            if "id" in filters:
                additional_match["id"] = filters["id"]
            if "market" in filters:
                additional_match["market"] = filters["market"]
            if "category" in filters:
                additional_match["category"] = filters["category"]
            if "area" in filters:
                additional_match["area"] = filters["area"]
            
            if additional_match:
                pipeline.append({"$match": additional_match})

            # 3. Group to find the latest version of each record for the matched day
            pipeline.extend([
                {"$sort": {"scheduled_at": -1}},
                {
                    "$group": {
                        "_id": {
                            "block": "$block",
                            "id": "$id",
                            "market": "$market",
                            "category": "$category",
                            "area": "$area"
                        },
                        "latest_doc": {"$first": "$$ROOT"}
                    }
                },
                {"$replaceRoot": {"newRoot": "$latest_doc"}},
                {"$sort": {"block": 1, "id": 1}}
            ])
            
            data = await self.repository.aggregate(collection_name, pipeline, max_time_ms=30000)
            return data
            
        except Exception as e:
            logger.exception(f"[LimitValuesRepository] Failed latest inserted_at with filters pipeline: {str(e)}")
            raise

    async def get_latest_inserted_at_data_pipeline_paginated(
        self,
        collection_name: str,
        from_block: int,
        to_block: int,
        filters: Dict[str, Any] = None,
        date_filter: str = None
    ) -> Dict[str, Any]:
        """
        Returns paginated records with latest inserted_at per key for a given date or max date,
        filtered by a block range.
        Uses optimized pipeline for speed.
        """
        try:
            # 0. Ensure indexes exist
            if date_filter and date_filter < datetime.now().strftime("%Y-%m-%d"):
                collection_name = f"{collection_name}_historical"
            await self.ensure_collection_exists(collection_name)

            base_pipeline = []

            # 1. Determine the target date
            target_date_str = date_filter
            if not target_date_str:
                # Find the latest date if not provided,scheduled_at 
                # Optimization: use sort + limit 1 instead of full group max if indexed
                max_date_pipeline = [
                    {"$sort": {"scheduled_at": -1}},
                    {"$limit": 1},
                    {
                        "$project": {
                            "date": {
                                "$ifNull": ["$scheduled_at", {"$toDate": "$schedule_date"}]
                            }
                        }
                    }
                ]
                max_date_result = await self.repository.aggregate(collection_name, max_date_pipeline)
                
                if max_date_result and max_date_result[0].get("date"):
                    max_date_val = max_date_result[0]["date"]
                    if isinstance(max_date_val, datetime):
                        target_date_str = max_date_val.strftime("%Y-%m-%d")
                    else:
                        # It's already a string, just take the date part
                        target_date_str = str(max_date_val)[:10]
                else:
                    return { "data": [], "page": 1, "page_size": to_block - from_block + 1, "total": 0, "total_pages": 0, "has_next": False, "has_prev": False }

            # 2. Date matching stage
            target_date_dt = datetime.strptime(target_date_str, "%Y-%m-%d")
            market_id = filters.get("market", "RTM")
            
            # The heart of optimization: match exactly what we need using indexes
            match_filter = {
                "scheduled_at": {
                    "$gte": target_date_dt,
                    "$lt": target_date_dt + timedelta(days=1)
                },
                "market": market_id,
                "category": "Limits"
            }

            # 3. Add other filters
            filters = filters or {}
            if "id" in filters:
                match_filter["id"] = filters["id"]
            if "category" in filters:
                match_filter["category"] = filters["category"]
            if "area" in filters:
                match_filter["area"] = filters["area"]
            
            base_pipeline.append({"$match": match_filter})

            # 4. Total count optimization
            # Since unique index exists on (scheduled_at, block, id, market, category, area),
            # for a specific market/date, each record is unique unless scheduled_at has different times.
            # Even then, the grouping stage is the bottleneck.
            
            count_pipeline = base_pipeline + [{"$count": "total"}]
            count_result = await self.repository.aggregate(collection_name, count_pipeline, max_time_ms=30000)
            total = count_result[0]["total"] if count_result else 0

            # 5. Main data pipeline
            # We filter by block range directly on the matched documents.
            # No grouping needed because of our unique index guarantees.
            data_pipeline = base_pipeline + [
                {"$match": {"block": {"$gte": from_block, "$lte": to_block}}},
                {"$sort": {"block": 1, "id": 1}}
            ]
            
            data = await self.repository.aggregate(collection_name, data_pipeline, max_time_ms=30000)
            
            # Pagination calculations
            page_size = to_block - from_block + 1
            page = (from_block - 1) // page_size + 1
            total_pages = (total + page_size - 1) // page_size
            has_next = page < total_pages
            has_prev = page > 1
            
            return {
                "data": data,
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_next": has_next,
                "has_prev": has_prev
            }
            
        except Exception as e:
            logger.exception(f"[LimitValuesRepository] Failed latest inserted_at paginated pipeline: {str(e)}")
            raise

    async def get_all_data_paginated(
        self, 
        collection_name: str, 
        page: int = 1, 
        page_size: int = 50, 
        filters: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Returns all data from collection with pagination, no date filtering.
        Returns all records regardless of date.
        """
        try:
            filters = filters or {}
            
            # Build match query from filters
            match_query = {}
            if "block" in filters:
                match_query["block"] = filters["block"]
            if "id" in filters:
                match_query["id"] = filters["id"]
            if "market" in filters:
                match_query["market"] = filters["market"]
            if "category" in filters:
                match_query["category"] = filters["category"]
            if "area" in filters:
                match_query["area"] = filters["area"]
            
            # Count total documents
            count_pipeline = []
            if match_query:
                count_pipeline.append({"$match": match_query})
            count_pipeline.append({"$count": "total"})
            
            count_result = await self.repository.aggregate(collection_name, count_pipeline, max_time_ms=30000)
            total = count_result[0]["total"] if count_result else 0
            
            # Calculate pagination
            page = max(1, page)
            page_size = max(1, min(page_size, 1000))  # Cap at 1000
            skip = (page - 1) * page_size
            total_pages = (total + page_size - 1) // page_size
            has_next = page < total_pages
            has_prev = page > 1
            
            # Main data pipeline with pagination
            data_pipeline = []
            if match_query:
                data_pipeline.append({"$match": match_query})
            data_pipeline.extend([
                {"$sort": {"scheduled_at": -1, "block": 1, "id": 1}},
                {"$skip": skip},
                {"$limit": page_size}
            ])
            
            data = await self.repository.aggregate(collection_name, data_pipeline, max_time_ms=30000)
            
            return {
                "data": data,
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_next": has_next,
                "has_prev": has_prev
            }
            
        except Exception as e:
            logger.exception(f"[LimitValuesRepository] Failed get_all_data_paginated: {str(e)}")
            raise
    
    async def get_all_data_with_filters(
        self, 
        collection_name: str, 
        filters: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        """
        Returns all data from collection without pagination, no date filtering.
        Returns all records regardless of date.
        """
        try:
            filters = filters or {}
            
            # Build match query from filters
            match_query = {}
            if "block" in filters:
                match_query["block"] = filters["block"]
            if "id" in filters:
                match_query["id"] = filters["id"]
            if "market" in filters:
                match_query["market"] = filters["market"]
            if "category" in filters:
                match_query["category"] = filters["category"]
            if "area" in filters:
                match_query["area"] = filters["area"]
            if "scheduled_at" in filters:
                match_query["scheduled_at"] = filters["scheduled_at"]
            
            # Build pipeline
            pipeline = []
            if match_query:
                pipeline.append({"$match": match_query})
            pipeline.append({"$sort": {"scheduled_at": -1, "block": 1, "id": 1}})
            
            data = await self.repository.aggregate(collection_name, pipeline, max_time_ms=30000)
            
            return data
            
        except Exception as e:
            logger.exception(f"[LimitValuesRepository] Failed get_all_data_with_filters: {str(e)}")
            raise
        
    async def get_data(
        self,
        start_date,
        end_date,
        market: str,
        id: str,
        area: str
    ) -> List[Dict[str, Any]]:
        """
        Get limit values data asynchronously.
        Accepts dates as either strings (YYYY-MM-DD) or date objects.
        """
        from datetime import date as _date
        if isinstance(start_date, str):
            start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        if isinstance(end_date, str):
            end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        if isinstance(start_date, _date) and not isinstance(start_date, datetime):
            start_date = datetime.combine(start_date, datetime.min.time())
        if isinstance(end_date, _date) and not isinstance(end_date, datetime):
            end_date = datetime.combine(end_date, datetime.min.time())

        filter_query = {"scheduled_at": {"$gte": start_date, "$lt": end_date}}
        if market and market != "":
            filter_query["market"] = market
        if id and id != "":
            filter_query["category"] = id
        if area and area != "" and area != "NA":
            filter_query["area"] = area

        return await self.repository.get_all(
            self.collection_name,
            filter=filter_query,
            sort=[("scheduled_at", 1)],
        )

    async def get_data_byID_with_latest_date(
        self,
        ID: str,
        market: str,
        category: str,
        area: str,
        scheduled_at: datetime = None
    ) -> List[Dict[str, Any]]:
        """
        Get limit values by ID with latest date asynchronously.
        If scheduled_at is not provided, defaults to today at midnight.
        """
        tomorrow_date = datetime.now() + timedelta(days=1)
        if scheduled_at is None:
            scheduled_at = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            match_filter = {
                "scheduled_at": {"$gte": scheduled_at, "$lt": tomorrow_date},
                "id": ID,
                "market": market
            }

            if category and category != "":
                match_filter["category"] = category
            if area and area != "" and area != "NA":
                match_filter["area"] = area

            result = await self.repository.get_all(
                self.collection_name,
                filter=match_filter,
                sort=[("scheduled_at", 1), ("block", 1)],
            )

            logger.info(f"Retrieved {len(result)} limit values for ID={ID}, latest_date={scheduled_at}")
            return result
        except Exception as e:
            logger.error(f"Failed to retrieve limit values by ID: {str(e)}")
            return []

    async def get_data_byID(
        self,
        schedule_date: str,
        ID: str,
        category: str,
        market: str,
        area: str
    ) -> List[Dict[str, Any]]:
        """
        Get limit values for a specific schedule date, ID, category, market and area.
        """
        try:
            if isinstance(schedule_date, str):
                target_date = datetime.strptime(schedule_date, "%Y-%m-%d")
            elif isinstance(schedule_date, datetime):
                target_date = schedule_date.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                target_date = schedule_date

            filter_query = {
                "scheduled_at": target_date,
                "id": ID,
                "market": market,
            }
            if category and category != "":
                filter_query["category"] = category
            if area and area != "" and area != "NA":
                filter_query["area"] = area

            result = await self.repository.get_all(
                self.collection_name,
                filter=filter_query,
                sort=[("block", 1)],
            )
            logger.info(f"Retrieved {len(result)} limit values for ID={ID}, date={schedule_date}")
            return result
        except Exception as e:
            logger.error(f"Failed to retrieve limit values by ID and date: {str(e)}")
            return []

    async def aggregate(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Run an aggregation pipeline and return the list of documents.
        """
        logger.debug("LimitValuesRepository.aggregate | pipeline=%s", pipeline)
        cursor = self.collection.aggregate(pipeline)
        try:
            return await cursor.to_list(length=10000)
        finally:
            cursor.close()

limit_values_repository = LimitValuesRepository()
