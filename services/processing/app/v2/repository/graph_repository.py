"""
Graph Repository
Handles data access for graph-related operations using MongoDB aggregation pipelines

Note: This repository is optimized for collections that store date fields as MongoDB Date objects.
The date normalization uses direct $dateToString conversion instead of type checking for better performance.
Uses 'scheduled_at' field for date operations and all fields follow snake_case naming convention.
Response format maintains 'schedule_date' for backward compatibility with v1 API.
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, date

from .base_repository import GenericRepository

logger = logging.getLogger(__name__)


class GraphRepository:
    """
    Repository for Graph data operations.
    Contains all MongoDB aggregation pipelines for graph-related queries.
    """

    def __init__(self):
        self.repository = GenericRepository()

    async def get_demand_blocks_pipeline(
        self,
        collection_name: str,
        markets: List[str],
        zones: List[str],
        start_date: date,
        end_date: date,
        b_from: int,
        b_to: int,
        include_today: bool,
        only_mask_market: bool,
        today_str: str,
        cur_blk: int,
        group_by_date: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get demand blocks data for graph visualization using aggregation pipeline.
        
        Args:
            collection_name: Name of the MongoDB collection
            markets: List of market types to filter by
            zones: List of zones to filter by
            start_date: Start date for filtering
            end_date: End date for filtering
            b_from: Starting time block
            b_to: Ending time block
            include_today: Whether the date range includes today
            only_mask_market: Whether to mask future blocks for specific markets
            today_str: Today's date as string
            cur_blk: Current time block
            group_by_date: Whether to group results by date
            
        Returns:
            List of demand block records
        """
        try:
            # Normalize fields: market/type and zone -> UPPER
            normalize_stage = {
                "$addFields": {
                    "_market_upper": {"$toUpper": {"$ifNull": ["$market", "$type"]}},
                    "_zone_upper":   {"$toUpper": {"$ifNull": ["$zone",  ""]}},
                }
            }

            match_and = [
                {"$in":  ["$_market_upper", markets]},
                {"$in":  ["$_zone_upper",   zones]},
                {"$gte": ["$scheduled_at", {"$dateFromString": {"dateString": start_date.isoformat()}}]},
                {"$lte": ["$scheduled_at", {"$dateFromString": {"dateString": end_date.isoformat()}}]},
                {"$gte": ["$time_block", b_from]},
                {"$lte": ["$time_block", b_to]},
                {"$lte": ["$scheduled_at", {"$dateFromString": {"dateString": start_date.isoformat()}}]},  # today_date
            ]

            core = [
                normalize_stage,
                {"$match": {"$expr": {"$and": match_and}}},
            ]

            # If range includes today, union today's rows from the live table
            if include_today:
                core.append({
                    "$unionWith": {
                        "coll": collection_name.replace("_historical", ""),  # Use live collection
                        "pipeline": [
                            normalize_stage,
                            {"$match": {"$expr": {"$and": match_and}}},
                        ],
                    }
                })

            # Continue with existing stages
            core += [
                {
                    "$addFields": {
                        "_comp_act_demand": {"$ifNull": ["$comp_act_demand", 0]},
                        "_comp_udm":        {"$ifNull": ["$comp_udm", 0]},
                        "_forecast":        {"$ifNull": ["$forecast", 0]},
                        "_mape_raw":        {"$ifNull": ["$mape", ""]},
                    }
                },
                {
                    "$addFields": {
                        "_mape_str":   {"$trim": {"input": {"$toString": "$_mape_raw"}}},
                        "_mape_match": {"$regexFind": {"input": "$_mape_str", "regex": "[-+]?[0-9]*\\.?[0-9]+"}},
                        "_mape_num0": {
                            "$convert": {
                                "input": {"$ifNull": ["$_mape_match.match", None]},
                                "to": "double",
                                "onError": None,
                                "onNull":  None
                            }
                        },
                    }
                },
                {
                    "$addFields": {
                        "_mape_num": {"$cond": [{"$ne": ["$_mape_num0", None]}, {"$abs": "$_mape_num0"}, None]},
                        "_mape_has": {"$cond": [{"$ne": ["$_mape_num0", None]}, 1, 0]},
                    }
                },
                {
                    "$group": {
                        "_id": {"scheduled_at": "$scheduled_at", "time_block": "$time_block"},
                        "comp_act_demand": {"$sum": "$_comp_act_demand"},
                        "comp_udm":        {"$sum": "$_comp_udm"},
                        "forecast":        {"$sum": "$_forecast"},
                        "mape_num_sum":    {"$sum": {"$ifNull": ["$_mape_num", 0]}},
                        "mape_cnt":        {"$sum": "$_mape_has"},
                    }
                },
                {
                    "$addFields": {
                        "MAPE": {
                            "$cond": [
                                {"$gt": ["$mape_cnt", 0]},
                                {"$concat": [{"$toString": {"$round": ["$mape_num_sum", 2]}}, "%"]},
                                {"$concat": [{"$toString": {"$round": [{"$abs": {"$subtract": ["$forecast", "$comp_act_demand"]}}, 2]}}, "%"]}
                            ]
                        },
                        "schedule_date": {
                            "$dateToString": {
                                "date": "$_id.scheduled_at",
                                "format": "%Y-%m-%d",
                                "timezone": "Asia/Kolkata"
                            }
                        }
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "schedule_date": 1,
                        "time_block": "$_id.time_block",
                        "comp_act_demand": 1,
                        "comp_udm": 1,
                        "forecast": 1,
                        "MAPE": 1,
                    }
                },
                {"$sort": {"schedule_date": 1, "time_block": 1}},
            ]

            # Apply future block masking if needed
            if include_today and only_mask_market:
                core.append({
                    "$addFields": {
                        "comp_act_demand": {
                            "$cond": [
                                {"$and": [
                                    {"$eq": ["$schedule_date", today_str]},
                                    {"$gt": ["$time_block", cur_blk]}
                                ]},
                                0,
                                "$comp_act_demand"
                            ]
                        },
                        "comp_udm": {
                            "$cond": [
                                {"$and": [
                                    {"$eq": ["$schedule_date", today_str]},
                                    {"$gt": ["$time_block", cur_blk]}
                                ]},
                                0,
                                "$comp_udm"
                            ]
                        },
                    }
                })

            # Execute aggregation
            rows = await self.repository.aggregate(collection_name, core)

            # Group by date or return flat
            if group_by_date:
                grouped = {}
                for row in rows:
                    date_key = row["schedule_date"]
                    if date_key not in grouped:
                        grouped[date_key] = {
                            "schedule_date": date_key,
                            "blocks": []
                        }
                    grouped[date_key]["blocks"].append({
                        "time_block": row["time_block"],
                        "data": {
                            "comp_act_demand": row["comp_act_demand"],
                            "comp_udm": row["comp_udm"],
                            "forecast": row["forecast"],
                            "MAPE": row["MAPE"]
                        }
                    })
                result = list(grouped.values())
            else:
                result = []
                for row in rows:
                    result.append({
                        "schedule_date": row["schedule_date"],
                        "time_block": row["time_block"],
                        "data": {
                            "comp_act_demand": row["comp_act_demand"],
                            "comp_udm": row["comp_udm"],
                            "forecast": row["forecast"],
                            "MAPE": row["MAPE"]
                        }
                    })

            return result
        except Exception as e:
            logger.exception(f"[GraphRepository] Failed to execute demand blocks pipeline: {str(e)}")
            raise

    async def get_demand_curve_pipeline(
        self,
        collection_name: str,
        markets: List[str],
        zones: List[str],
        start_date: date,
        end_date: date,
        b_from: int,
        b_to: int,
        denominator: str = "matched"
    ) -> List[Dict[str, Any]]:
        """
        Get demand curve data using aggregation pipeline.
        
        Args:
            collection_name: Name of the MongoDB collection
            markets: List of market types to filter by
            zones: List of zones to filter by
            start_date: Start date for filtering
            end_date: End date for filtering
            b_from: Starting time block
            b_to: Ending time block
            denominator: How to compute percentage ("matched" or "expected")
            
        Returns:
            List of demand curve records
        """
        try:
            start_date_str = start_date.isoformat()
            end_date_str = end_date.isoformat()
            
            # Calculate expected total for percentage calculation
            days_inclusive = (end_date - start_date).days + 1
            slots_per_day = (b_to - b_from + 1)
            expected_total = days_inclusive * slots_per_day
            
            # Normalize scheduled_at and fields (optimized for Date objects)
            normalize_stage = {
                "$addFields": {
                    "_sdate": {
                        "$dateToString": {
                            "date": "$scheduled_at",
                            "format": "%Y-%m-%d",
                            "timezone": "Asia/Kolkata",
                        }
                    },
                    "_market_upper": {"$toUpper": {"$ifNull": ["$market", "$type"]}},
                    "_zone_upper":   {"$toUpper": {"$ifNull": ["$zone",  ""]}},
                    "_cad":          {"$toDouble": {"$ifNull": ["$comp_act_demand", 0]}},
                }
            }
            
            # Check if date range includes today
            from datetime import datetime, timezone, timedelta
            IST = timezone(timedelta(hours=5, minutes=30))
            today = datetime.now(IST).date()
            include_today = start_date <= today <= end_date
            cur_blk = (datetime.now(IST).time().hour * 4) + (datetime.now(IST).time().minute // 15) + 1
            today_str = today.isoformat()
            
            match_and = [
                {"$in":  ["$_market_upper", markets]},
                {"$in":  ["$_zone_upper",   zones]},
                {"$gte": ["$_sdate", start_date_str]},
                {"$lte": ["$_sdate", end_date_str]},
                {"$gte": ["$time_block", b_from]},
                {"$lte": ["$time_block", b_to]},
                {"$lte": ["$_sdate", today_str]},  # hard-stop at today (IST)
            ]
            
            # Exclude future blocks on "today": allow all past days; for today allow <= cur_blk
            match_and.append({
                "$or": [
                    {"$lt": ["$_sdate", today_str]},  # any block for strictly past days
                    {"$and": [
                        {"$eq": ["$_sdate", today_str]},
                        {"$lte": ["$time_block", cur_blk]},  # only up to the current time block
                    ]}
                ]
            })
            
            if denominator == "expected":
                slots_per_day = (b_to - b_from + 1)
                if include_today:
                    # blocks we actually allow today within [b_from..b_to]
                    today_slots = max(0, min(b_to, cur_blk) - b_from + 1)
                    # all full past days in the window + partial today
                    expected_total = ((end_date - start_date).days) * slots_per_day + today_slots
                else:
                    expected_total = ((end_date - start_date).days + 1) * slots_per_day
            
            match_stage = {"$match": {"$expr": {"$and": match_and}}}
            
            group_stage = {
                "$group": {
                    "_id": {"scheduled_at": "$_sdate", "time_block": "$time_block"},
                    "comp_act_demand": {"$sum": "$_cad"},
                }
            }
            
            sort_stage = {
                "$sort": {
                    "comp_act_demand": 1,
                    "_id.scheduled_at": 1,
                    "_id.time_block": 1,
                }
            }
            
            window_output = {
                # running count 1..N based on the window order
                "count": {"$count": {}, "window": {"documents": ["unbounded", "current"]}},
            }
            
            if denominator == "matched":
                # constant total across all rows in the partition
                window_output["total"] = {"$count": {}, "window": {"documents": ["unbounded", "unbounded"]}}
            
            window_stage = {
                "$setWindowFields": {
                    "sortBy": {
                        "comp_act_demand": 1,
                        "_id.scheduled_at": 1,
                        "_id.time_block": 1,
                    },
                    "output": window_output,
                }
            }
            
            if denominator == "expected":
                percent_expr = {
                    "$cond": [
                        {"$gt": [expected_total, 0]},
                        {"$round": [{"$multiply": [{"$divide": ["$count", expected_total]}, 100]}, 2]},
                        0
                    ]
                }
            else:
                percent_expr = {
                    "$cond": [
                        {"$gt": ["$total", 0]},
                        {"$round": [{"$multiply": [{"$divide": ["$count", "$total"]}, 100]}, 2]},
                        0
                    ]
                }
            
            project_stage = {
                "$project": {
                    "_id": 0,
                    "count": 1,
                    "comp_act_demand": {"$round": ["$comp_act_demand", 2]},
                    "percentage": percent_expr,
                }
            }
            
            pipeline = [
                normalize_stage,
                match_stage,
            ]
            
            if include_today:
                pipeline.append({
                    "$unionWith": {
                        "coll": collection_name.replace("_historical", ""),  # Use live collection
                        "pipeline": [
                            normalize_stage,
                            match_stage,
                        ],
                    }
                })
            
            pipeline += [
                group_stage,
                sort_stage,
                window_stage,
                project_stage,
            ]
            
            logger.debug("Executing demand_curve pipeline: %s", pipeline)
            rows = await self.repository.aggregate(collection_name, pipeline)
            return rows
            
        except Exception as e:
            logger.exception(f"[GraphRepository] Failed to execute demand curve pipeline: {str(e)}")
            raise

    async def get_mape_curve_pipeline(
        self,
        collection_name: str,
        markets: List[str],
        zones: List[str],
        start_date: date,
        end_date: date,
        b_from: int,
        b_to: int,
        denominator: str = "matched"
    ) -> List[Dict[str, Any]]:
        """
        Get MAPE curve data using aggregation pipeline.
        
        Args:
            collection_name: Name of the MongoDB collection
            markets: List of market types to filter by
            zones: List of zones to filter by
            start_date: Start date for filtering
            end_date: End date for filtering
            b_from: Starting time block
            b_to: Ending time block
            denominator: How to compute percentage ("matched" or "expected")
            
        Returns:
            List of MAPE curve records
        """
        try:
            start_date_str = start_date.isoformat()
            end_date_str = end_date.isoformat()
            
            # Calculate expected total for percentage calculation
            days_inclusive = (end_date - start_date).days + 1
            slots_per_day = (b_to - b_from + 1)
            expected_total = days_inclusive * slots_per_day
            
            # Normalize fields for serve_demand_table collection (uses schedule_date as string and MAPE as number)
            normalize_stage = {
                "$addFields": {
                    "_sdate": "$schedule_date",  # schedule_date is already a string in YYYY-MM-DD format
                    "_market_upper": {"$toUpper": {"$ifNull": ["$market", "$type"]}},
                    "_zone_upper":   {"$toUpper": {"$ifNull": ["$zone",  ""]}},
                    "_mape_num":     {"$toDouble": {"$ifNull": ["$MAPE", 0]}},  # MAPE is already numeric
                }
            }
            
            # Check if date range includes today
            from datetime import datetime, timezone, timedelta
            IST = timezone(timedelta(hours=5, minutes=30))
            today = datetime.now(IST).date()
            include_today = start_date <= today <= end_date
            cur_blk = (datetime.now(IST).time().hour * 4) + (datetime.now(IST).time().minute // 15) + 1
            today_str = today.isoformat()
            
            match_and = [
                {"$in":  ["$_market_upper", markets]},
                {"$in":  ["$_zone_upper",   zones]},
                {"$gte": ["$_sdate", start_date_str]},
                {"$lte": ["$_sdate", end_date_str]},
                {"$gte": ["$time_block", b_from]},
                {"$lte": ["$time_block", b_to]},
                {"$lte": ["$_sdate", today_str]},  # cap at today
            ]
            
            # Exclude future blocks on "today": allow all past days; for today allow <= cur_blk
            match_and.append({
                "$or": [
                    {"$lt": ["$_sdate", today_str]},  # any block for strictly past days
                    {"$and": [
                        {"$eq": ["$_sdate", today_str]},
                        {"$lte": ["$time_block", cur_blk]},  # only up to the current time block
                    ]}
                ]
            })
            
            if denominator == "expected":
                slots_per_day = (b_to - b_from + 1)
                if include_today:
                    # blocks we actually allow today within [b_from..b_to]
                    today_slots = max(0, min(b_to, cur_blk) - b_from + 1)
                    # all full past days in the window + partial today
                    expected_total = ((end_date - start_date).days) * slots_per_day + today_slots
                else:
                    expected_total = ((end_date - start_date).days + 1) * slots_per_day
            
            match_stage = {"$match": {"$expr": {"$and": match_and}}}
            
            # Collapse duplicates per (date, block) and compute avg MAPE (MAPE is already numeric)
            group_stage = {
                "$group": {
                    "_id": {"scheduled_at": "$_sdate", "time_block": "$time_block"},
                    "mape_num_sum": {"$sum": "$_mape_num"},
                    "mape_cnt":     {"$sum": 1},  # Count all records
                }
            }
            
            finalize_avg_stage = {
                "$addFields": {
                    "mape_avg": {
                        "$cond": [
                            {"$gt": ["$mape_cnt", 0]},
                            {"$divide": ["$mape_num_sum", "$mape_cnt"]},
                            0
                        ]
                    }
                }
            }
            
            sort_stage = {
                "$sort": {
                    "mape_avg": 1,
                    "_id.scheduled_at": 1,
                    "_id.time_block": 1,
                }
            }
            
            # Window: use $documentNumber (requires EXACTLY ONE sortBy field)
            window_stage = {
                "$setWindowFields": {
                    "sortBy": {"mape_avg": 1},
                    "output": {
                        "count": {"$documentNumber": {}},
                        # total rows in the (single) result set
                        "total": {"$count": {}},
                    }
                }
            }
            
            # percentage expression based on denominator
            if denominator == "expected":
                percent_expr = {
                    "$cond": [
                        {"$gt": [expected_total, 0]},
                        {"$round": [
                            {"$multiply": [
                                {"$divide": ["$count", expected_total]},
                                100
                            ]},
                            2
                        ]},
                        0
                    ]
                }
            else:  # matched
                percent_expr = {
                    "$cond": [
                        {"$gt": ["$total", 0]},
                        {"$round": [
                            {"$multiply": [
                                {"$divide": ["$count", "$total"]},
                                100
                            ]},
                            2
                        ]},
                        0
                    ]
                }
            
            project_stage = {
                "$project": {
                    "_id": 0,
                    "count": 1,
                    "MAPE": {
                        "$concat": [
                            {"$toString": {"$round": ["$mape_avg", 2]}},
                            "%"
                        ]
                    },
                    "percentage": percent_expr,
                }
            }
            
            pipeline = [
                normalize_stage,
                match_stage,
            ]
            
            if include_today:
                pipeline.append({
                    "$unionWith": {
                        "coll": "serve_demand_table",  # Use correct live collection
                        "pipeline": [
                            normalize_stage,
                            match_stage,
                        ],
                    }
                })
            
            pipeline += [
                group_stage,
                finalize_avg_stage,
                sort_stage,
                window_stage,
                project_stage,
            ]
            
            logger.debug("Executing mape_curve pipeline: %s", pipeline)
            rows = await self.repository.aggregate(collection_name, pipeline)
            return rows
            
        except Exception as e:
            logger.exception(f"[GraphRepository] Failed to execute MAPE curve pipeline: {str(e)}")
            raise

    async def get_last_7_days_pipeline(
        self,
        collection_name: str,
        markets: List[str],
        zones: List[str],
        b_from: int,
        b_to: int
    ) -> List[Dict[str, Any]]:
        """
        Get last 7 days data from pre-calculated peak_demand_serve collection.
        
        Args:
            collection_name: Name of the MongoDB collection (peak_demand_serve)
            markets: List of market types (ignored)
            zones: List of zones (ignored)
            b_from: Starting time block (ignored)
            b_to: Ending time block (ignored)
            
        Returns:
            List of last 7 days records with schedule_date, peak_demand, and average_demand
        """
        try:
            from datetime import datetime, timezone, timedelta
            
            # Compute date window in IST: today-7 .. today-1 (last 7 days)
            IST = timezone(timedelta(hours=5, minutes=30))
            today_ist = datetime.now(IST).date()
            start_ist = today_ist - timedelta(days=7)
            end_ist = today_ist - timedelta(days=1)
            
            pipeline = [
                # Match records within the last 7 days
                {
                    "$match": {
                        "scheduled_at": {
                            "$gte": datetime.combine(start_ist, datetime.min.time()),
                            "$lte": datetime.combine(end_ist, datetime.max.time())
                        }
                    }
                },
                # Project required fields and format schedule_date
                {
                    "$project": {
                        "_id": 0,
                        "schedule_date": {
                            "$dateToString": {
                                "date": "$scheduled_at",
                                "format": "%Y-%m-%d",
                                "timezone": "Asia/Kolkata"
                            }
                        },
                        "peak_demand": {"$round": ["$peak_demand", 2]},
                        "average_demand": {"$round": ["$average_demand", 2]}
                    }
                },
                # Sort by date
                {"$sort": {"schedule_date": 1}}
            ]
            
            logger.debug("Executing simplified last_7_days pipeline: %s", pipeline)
            rows = await self.repository.aggregate(collection_name, pipeline)
            return rows
            
        except Exception as e:
            logger.exception(f"[GraphRepository] Failed to execute last 7 days pipeline: {str(e)}")
            raise

    async def get_selected_days_stats_pipeline(
        self,
        collection_name: str,
        dates: List[str],
        markets: List[str],
        zones: List[str],
        b_from: int,
        b_to: int
    ) -> List[Dict[str, Any]]:
        """
        Get statistics for selected days using aggregation pipeline.
        For each selected date, compute stats for BOTH comp_act_demand and comp_udm fields.
        
        Args:
            collection_name: Name of the MongoDB collection
            dates: List of dates to get stats for (max 10)
            markets: List of market types to filter by
            zones: List of zones to filter by
            b_from: Starting time block
            b_to: Ending time block
            
        Returns:
            List of statistics records with per-date stats for comp_act_demand and comp_udm
        """
        try:
            from datetime import datetime, timezone, timedelta
            
            # Validate dates (max 10)
            if len(dates) > 10:
                raise ValueError("You can request at most 10 dates at a time")
            
            # Normalize and sort dates
            eff_dates = sorted(set(d.strip() for d in dates if d and d.strip()))
            if not eff_dates:
                return []
            
            # Normalize scheduled_at and fields (optimized for Date objects)
            normalize_stage = {
                "$addFields": {
                    "_sdate": {
                        "$dateToString": {
                            "date": "$scheduled_at",
                            "format": "%Y-%m-%d",
                            "timezone": "Asia/Kolkata",
                        }
                    },
                    "_market_upper": {"$toUpper": {"$ifNull": ["$market", "$type"]}},
                    "_zone_upper":   {"$toUpper": {"$ifNull": ["$zone",  ""]}},
                    "_cad":          {"$toDouble": {"$ifNull": ["$comp_act_demand", 0]}},
                    "_udm":          {"$toDouble": {"$ifNull": ["$comp_udm", 0]}},
                }
            }
            
            # Get today's date for filtering
            IST = timezone(timedelta(hours=5, minutes=30))
            today_str = datetime.now(IST).date().isoformat()
            
            match_stage = {
                "$match": {
                    "$expr": {
                        "$and": [
                            {"$in":  ["$_sdate", eff_dates]},
                            {"$in":  ["$_market_upper", markets]},
                            {"$in":  ["$_zone_upper",   zones]},
                            {"$gte": ["$time_block", b_from]},
                            {"$lte": ["$time_block", b_to]},
                            {"$lte": ["$_sdate", today_str]},
                        ]
                    }
                }
            }
            
            # 1) Collapse per (date, block)
            group_slot = {
                "$group": {
                    "_id": {"scheduled_at": "$_sdate", "time_block": "$time_block"},
                    "cad": {"$sum": "$_cad"},
                    "udm": {"$sum": "$_udm"},
                }
            }
            
            # 2) Compute per-date stats for both fields
            group_date = {
                "$group": {
                    "_id": "$_id.scheduled_at",
                    "cad_min":   {"$min": "$cad"},
                    "cad_max":   {"$max": "$cad"},
                    "cad_avg":   {"$avg": "$cad"},
                    "cad_sum":   {"$sum": "$cad"},
                    "cad_count": {"$sum": 1},
                    "udm_min":   {"$min": "$udm"},
                    "udm_max":   {"$max": "$udm"},
                    "udm_avg":   {"$avg": "$udm"},
                    "udm_sum":   {"$sum": "$udm"},
                    "udm_count": {"$sum": 1},
                }
            }
            
            project_stage = {
                "$project": {
                    "_id": 0,
                    "schedule_date": "$_id",
                    "stats": {
                        "comp_act_demand": {
                            "min":   {"$round": ["$cad_min", 2]},
                            "max":   {"$round": ["$cad_max", 2]},
                            "avg":   {"$round": ["$cad_avg", 2]},
                            "sum":   {"$round": ["$cad_sum", 2]},
                            "count": "$cad_count",
                        },
                        "comp_udm": {
                            "min":   {"$round": ["$udm_min", 2]},
                            "max":   {"$round": ["$udm_max", 2]},
                            "avg":   {"$round": ["$udm_avg", 2]},
                            "sum":   {"$round": ["$udm_sum", 2]},
                            "count": "$udm_count",
                        },
                    },
                }
            }
            
            pipeline = [
                normalize_stage,
                match_stage,
                group_slot,
                group_date,
                {"$sort": {"_id": 1}},
                project_stage,
            ]
            
            logger.debug("Executing comp_act_demand_stats_by_date pipeline: %s", pipeline)
            rows = await self.repository.aggregate(collection_name, pipeline)
            
            # Ensure all requested dates appear (even if no data)
            present = {r["schedule_date"] for r in rows}
            for d in eff_dates:
                if d not in present:
                    rows.append({
                        "schedule_date": d,
                        "stats": {
                            "comp_act_demand": {"min": None, "max": None, "avg": None, "sum": 0.0, "count": 0},
                            "comp_udm":       {"min": None, "max": None, "avg": None, "sum": 0.0, "count": 0},
                        }
                    })
            
            # Sort by schedule_date for consistent output
            rows.sort(key=lambda x: x["schedule_date"])
            return rows
            
        except Exception as e:
            logger.exception(f"[GraphRepository] Failed to execute selected days stats pipeline: {str(e)}")
            raise


# Create repository instance
graph_repository = GraphRepository()
