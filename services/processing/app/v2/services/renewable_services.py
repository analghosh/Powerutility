"""
Renewable V2 Services
Business logic for renewable energy data operations
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import HTTPException, status

from ..repository.renewable_repository import renewable_repository
from ...repository.serve_table_repository import SupplyTableRepository

from ...core.config import settings

logger = logging.getLogger(__name__)


class RenewableServices:
    """
    Service layer for renewable energy data operations.
    Handles business logic and data processing.
    """

    def __init__(self, tz_name: str = "Asia/Kolkata"):
        self.renewable_repository = renewable_repository
        # Use the same repositories as v1 for exact compatibility
        self.schedule_repo = SupplyTableRepository(collection_name=settings.MONGODB_RENEWABLE_SERVE)
        self.availability_repo = SupplyTableRepository(collection_name="serve_availability_table")
        self.tz = ZoneInfo(tz_name)

    def _today_ist(self):
        """Get today's date in IST timezone"""
        return datetime.now(self.tz).date()

    def _parse_date(self, date_str: str) -> datetime:
        """Parse date string to datetime object"""
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date format. Use YYYY-MM-DD."
            )

    def _is_date_in_window(self, date_str: str) -> bool:
        """Check if date is within the 4-day rolling window"""
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            today = self._today_ist()
            
            # 4-day window: day-before-yesterday to tomorrow
            window_start = today - timedelta(days=2)
            window_end = today + timedelta(days=1)
            
            return window_start <= target_date <= window_end
        except ValueError:
            return False

    async def get_renewable_schedule_by_day(self, date_str: str) -> Dict[str, Any]:
        """
        Get renewable schedule data for a specific date.
        Uses the same logic as v1 for exact compatibility.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            Dict containing renewable schedule data grouped by technology and type
            
        Raises:
            HTTPException: If date is invalid or no data found
        """
        try:
            logger.info(f"Fetching renewable schedule for date: {date_str}")
            
            # Validate date format (same as v1)
            if not isinstance(date_str, str) or len(date_str) != 10:
                raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
            
            scheduled_at = datetime.strptime(date_str, "%Y-%m-%d")
            
            # Use the exact same aggregation pipeline as v1
            tech_list = ["solar", "wind"]
            type_list = ["IDF", "DAF"]
            cs_list = ["central", "state"]

            base_match = {
                "scheduled_at": scheduled_at,
                "type": {"$in": type_list},
                "time_block": {"$gte": 1, "$lte": 96},
            }

            # Probe for existence (same as v1)
            probe = [{"$match": base_match}, {"$limit": 1}, {"$project": {"_id": 1}}]
            if not (await self.schedule_repo.aggregate(probe)):
                raise HTTPException(status_code=404, detail=f"No schedule data for {date_str}.")

            # Use the exact same aggregation pipeline as v1
            pipeline = [
                {"$match": base_match},
                # Lookup from plant_master
                {"$lookup": {
                    "from": "plant_master",
                    "let": {"p_id": "$plant_id", "p_no": "$approval_no"},
                    "pipeline": [
                        {"$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$plant_id", "$$p_id"]},
                                    {"$or": [
                                        {"$eq": ["$approval_no", "$$p_no"]},
                                        {"$eq": ["$$p_no", None]}
                                    ]}
                                ]
                            }
                        }},
                        {"$project": {"technology": 1, "c_s": 1, "dispatch_type": 1, "_id": 0}}
                    ],
                    "as": "plant_info"
                }},
                {"$addFields": {
                    "plant_data": {"$arrayElemAt": ["$plant_info", 0]}
                }},
                {"$addFields": {
                    "technology": {"$toLower": {"$ifNull": ["$plant_data.technology", "$technology"]}},
                    "c_s": {"$ifNull": ["$plant_data.c_s", "$c_s"]},
                    # "type": {"$ifNull": ["$plant_data.dispatch_type", "$type"]}  # Don't overwrite schedule type
                }},
                {"$match": {
                    "technology": {"$in": tech_list},
                    "c_s": {"$in": cs_list}
                }},
                {"$group": {
                    "_id": {
                        "c_s": "$c_s",
                        "technology": "$technology",
                        "type": "$type",
                        "tb": "$time_block"
                    },
                    "scheduled_volume": {"$sum": "$schedule_volume"},
                }},
                {"$group": {
                    "_id": {
                        "c_s": "$_id.c_s",
                        "technology": "$_id.technology",
                        "type": "$_id.type"
                    },
                    "data": {
                        "$push": {"time_block": "$_id.tb", "scheduled_volume": "$scheduled_volume"}
                    }
                }},
                {"$project": {
                    "_id": 0,
                    "scheduled_at": scheduled_at,
                    "c_s": "$_id.c_s",
                    "technology": "$_id.technology",
                    "type": "$_id.type",
                    "data": {
                        "$map": {
                            "input": {"$range": [1, 97]},
                            "as": "tb",
                            "in": {
                                "time_block": "$$tb",
                                "scheduled_volume": {
                                    "$round": [
                                        {"$let": {
                                            "vars": {"m": {"$first": {
                                                "$filter": {
                                                    "input": "$data",
                                                    "cond": {"$eq": ["$$this.time_block", "$$tb"]}
                                                }
                                            }}},
                                            "in": {"$ifNull": ["$$m.scheduled_volume", 0]}
                                        }}, 2]
                                }
                            }
                        }
                    }
                }},
                {"$addFields": {
                    "cs_order": {"$indexOfArray": [cs_list, "$c_s"]},
                    "tech_order": {"$indexOfArray": [tech_list, "$technology"]},
                    "type_order": {"$indexOfArray": [type_list, "$type"]},
                }},
                {"$sort": {"cs_order": 1, "tech_order": 1, "type_order": 1}},
                {"$project": {"cs_order": 0, "tech_order": 0, "type_order": 0}},
            ]

            docs = await self.schedule_repo.aggregate(pipeline)
            
            # Debug: Log the first few items to see the structure
            if docs and len(docs) > 0 and 'data' in docs[0] and len(docs[0]['data']) > 0:
                logger.info(f"DEBUG: First data item structure: {docs[0]['data'][0]}")
            
            # Get max updated_at timestamp with fallback
            max_updated_at = await self.renewable_repository.get_max_updated_at_from_renewable(date_str)
            last_updated_at = max_updated_at if max_updated_at else datetime.now(timezone.utc)
            
            logger.info(f"Successfully retrieved renewable schedule for date: {date_str}, docs: {len(docs)}")
            
            # Return in the same format as v1 with last_updated_at
            return {
                "data": docs,
                "last_updated_at": last_updated_at
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error fetching renewable schedule for date {date_str}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch renewable schedule data: {str(e)}"
            )

    async def get_renewable_availability_by_day(self, date_str: str) -> Dict[str, Any]:
        """
        Get renewable availability data for a specific date.
        Uses the same logic as v1 for exact compatibility.
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            Dict containing renewable availability data for 96 time blocks
            
        Raises:
            HTTPException: If date is invalid or no data found
        """
        try:
            logger.info(f"Fetching renewable availability for date: {date_str}")
            
            # Validate date format (same as v1)
            if not isinstance(date_str, str) or len(date_str) != 10:
                raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

            # Use the exact same aggregation pipeline as v1
            # Quick existence probe so callers can 404 cleanly
            probe = [
                {"$match": {
                    "schedule_date": date_str,
                    "time_block": {"$gte": 1, "$lte": 96},
                }},
                {"$limit": 1},
                {"$project": {"_id": 1}}
            ]
            if not (await self.availability_repo.aggregate(probe)):
                raise HTTPException(status_code=404, detail=f"No data for {date_str}.")

            # Use the exact same aggregation pipeline as v1
            pipeline = [
                {"$match": {
                    "schedule_date": date_str,
                    "time_block": {"$gte": 1, "$lte": 96},
                }},
                # Lookup from plant_master
                {"$lookup": {
                    "from": "plant_master",
                    "let": {"p_id": "$plant_id", "p_no": "$approval_no"},
                    "pipeline": [
                        {"$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$plant_id", "$$p_id"]},
                                    {"$or": [
                                        {"$eq": ["$approval_no", "$$p_no"]},
                                        {"$eq": ["$$p_no", None]}
                                    ]}
                                ]
                            }
                        }},
                        {"$project": {"technology": 1, "c_s": 1, "_id": 0}}
                    ],
                    "as": "plant_info"
                }},
                {"$addFields": {
                    "plant_data": {"$arrayElemAt": ["$plant_info", 0]}
                }},
                {"$addFields": {
                    # Map plant_master fields if available
                    "technology": {"$ifNull": ["$plant_data.technology", "$technology"]},
                    # Map Central -> DC, State -> SG
                    "derived_dc_sg": {
                        "$switch": {
                            "branches": [
                                {"case": {"$eq": [{"$toLower": "$plant_data.c_s"}, "central"]}, "then": "DC"},
                                {"case": {"$eq": [{"$toLower": "$plant_data.c_s"}, "state"]}, "then": "SG"}
                            ],
                            "default": "$dc_sg_type"
                        }
                    }
                }},
                {"$group": {
                    "_id": "$time_block",
                    "sg_state_solar": {"$max": {"$cond": [{"$and": [{"$eq": ["$derived_dc_sg","SG"]}, {"$eq": [{"$toLower": "$technology"}, "solar"]}]}, "$filterData.state_solar", None]}},
                    "sg_state_wind":  {"$max": {"$cond": [{"$and": [{"$eq": ["$derived_dc_sg","SG"]}, {"$eq": [{"$toLower": "$technology"}, "wind"]}]}, "$filterData.state_wind",  None]}},
                    "sg_solar":       {"$max": {"$cond": [{"$and": [{"$eq": ["$derived_dc_sg","SG"]}, {"$eq": [{"$toLower": "$technology"}, "solar"]}]}, "$filterData.Solar",       None]}},
                    "sg_wind":        {"$max": {"$cond": [{"$and": [{"$eq": ["$derived_dc_sg","SG"]}, {"$eq": [{"$toLower": "$technology"}, "wind"]}]}, "$filterData.Wind",        None]}},
                    "dc_state_solar": {"$max": {"$cond": [{"$and": [{"$eq": ["$derived_dc_sg","DC"]}, {"$eq": [{"$toLower": "$technology"}, "solar"]}]}, "$filterData.state_solar", None]}},
                    "dc_state_wind":  {"$max": {"$cond": [{"$and": [{"$eq": ["$derived_dc_sg","DC"]}, {"$eq": [{"$toLower": "$technology"}, "wind"]}]}, "$filterData.state_wind",  None]}},
                    "dc_solar":       {"$max": {"$cond": [{"$and": [{"$eq": ["$derived_dc_sg","DC"]}, {"$eq": [{"$toLower": "$technology"}, "solar"]}]}, "$filterData.Solar",       None]}},
                    "dc_wind":        {"$max": {"$cond": [{"$and": [{"$eq": ["$derived_dc_sg","DC"]}, {"$eq": [{"$toLower": "$technology"}, "wind"]}]}, "$filterData.Wind",        None]}},
                }},
                {"$project": {
                    "_id": 0,
                    "time_block": "$_id",
                    "state_solar": {"$ifNull": ["$sg_state_solar", "$dc_state_solar"]},
                    "state_wind":  {"$ifNull": ["$sg_state_wind",  "$dc_state_wind"]},
                    "Solar":       {"$ifNull": ["$sg_solar",       "$dc_solar"]},
                    "Wind":        {"$ifNull": ["$sg_wind",        "$dc_wind"]},
                    "gap_state_solar": {"$cond": [
                        {"$and":[{"$ne":["$sg_state_solar",None]},{"$ne":["$dc_state_solar",None]}]},
                        {"$subtract":["$sg_state_solar","$dc_state_solar"]}, None]},
                    "gap_state_wind": {"$cond": [
                        {"$and":[{"$ne":["$sg_state_wind",None]},{"$ne":["$dc_state_wind",None]}]},
                        {"$subtract":["$sg_state_wind","$dc_state_wind"]}, None]},
                    "gap_solar": {"$cond": [
                        {"$and":[{"$ne":["$sg_solar",None]},{"$ne":["$dc_solar",None]}]},
                        {"$subtract":["$sg_solar","$dc_solar"]}, None]},
                    "gap_wind": {"$cond": [
                        {"$and":[{"$ne":["$sg_wind",None]},{"$ne":["$dc_wind",None]}]},
                        {"$subtract":["$sg_wind","$dc_wind"]}, None]},
                    "source": {
                        "$cond": [
                            {"$or":[
                                {"$ne":["$sg_state_solar",None]},
                                {"$ne":["$sg_state_wind",None]},
                                {"$ne":["$sg_solar",None]},
                                {"$ne":["$sg_wind",None]},
                            ]},
                            "SG",
                            {"$cond":[
                                {"$or":[
                                    {"$ne":["$dc_state_solar",None]},
                                    {"$ne":["$dc_state_wind",None]},
                                    {"$ne":["$dc_solar",None]},
                                    {"$ne":["$dc_wind",None]},
                                ]},
                                "DC","NONE"
                            ]}
                        ]
                    }
                }},
                {"$sort": {"time_block": 1}}
            ]

            rows: List[Dict[str, Any]] = await self.availability_repo.aggregate(pipeline)

            # Normalize to 96 blocks (same as v1)
            by_tb = {r["time_block"]: r for r in rows}
            blocks: List[Dict[str, Any]] = []
            for tb in range(1, 97):
                blocks.append(by_tb.get(tb, {
                    "time_block": tb,
                    "state_solar": None, "state_wind": None,
                    "Solar": None, "Wind": None,
                    "gap_state_solar": None, "gap_state_wind": None, "gap_solar": None, "gap_wind": None,
                    "source": "NONE",
                }))
            
            # Get max updated_at timestamp with fallback
            max_updated_at = await self.renewable_repository.get_max_updated_at_from_renewable(date_str)
            last_updated_at = max_updated_at if max_updated_at else datetime.now(timezone.utc)
            
            logger.info(f"Successfully retrieved renewable availability for date: {date_str}, blocks: {len(blocks)}")
            
            # Return in the same format as v1 with last_updated_at
            return {
                "data": {"schedule_date": date_str, "blocks": blocks},
                "last_updated_at": last_updated_at
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error fetching renewable availability for date {date_str}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch renewable availability data: {str(e)}"
            )

    def _group_schedule_data(self, schedule_data: List[Dict[str, Any]], date_str: str) -> List[Dict[str, Any]]:
        """
        Group schedule data by technology and type.
        
        Args:
            schedule_data: Raw schedule data from repository
            date_str: Date string for grouping
            
        Returns:
            List of grouped data by technology and type
        """
        try:
            # Group by technology and type
            groups = {}
            
            for record in schedule_data:
                technology = record.get('technology', 'unknown')
                type_val = record.get('type', 'unknown')
                key = f"{technology}_{type_val}"
                
                if key not in groups:
                    groups[key] = {
                        "schedule_date": date_str,
                        "technology": technology,
                        "type": type_val,
                        "data": []
                    }
                
                # Add time block data
                groups[key]["data"].append({
                    "time_block": record.get('time_block'),
                    "scheduled_volume": record.get('schedule_volume', 0.0)
                })
            
            # Sort data within each group by time_block
            for group in groups.values():
                group["data"].sort(key=lambda x: x.get("time_block", 0))
            
            return list(groups.values())
            
        except Exception as e:
            logger.error(f"Error grouping schedule data: {str(e)}")
            return []

    def _process_availability_data(self, availability_data: List[Dict[str, Any]], date_str: str) -> List[Dict[str, Any]]:
        """
        Process availability data for 96 time blocks.
        
        Args:
            availability_data: Raw availability data from repository
            date_str: Date string for processing
            
        Returns:
            List of processed availability data for each time block
        """
        try:
            # Create a map of time_block to availability data
            availability_map = {}
            
            for record in availability_data:
                time_block = record.get('time_block')
                if time_block:
                    availability_map[time_block] = {
                        "time_block": time_block,
                        "available_capacity": record.get('schedule_volume', 0.0),
                        "technology": record.get('technology'),
                        "plant_id": record.get('plant_id'),
                        "category": record.get('category')
                    }
            
            # Ensure we have data for all 96 time blocks
            processed_data = []
            for block in range(1, 97):  # 1 to 96
                if block in availability_map:
                    processed_data.append(availability_map[block])
                else:
                    # Fill missing blocks with zero capacity
                    processed_data.append({
                        "time_block": block,
                        "available_capacity": 0.0,
                        "technology": None,
                        "plant_id": None,
                        "category": None
                    })
            
            return processed_data
            
        except Exception as e:
            logger.error(f"Error processing availability data: {str(e)}")
            return []


# Global singleton
renewable_service = RenewableServices()