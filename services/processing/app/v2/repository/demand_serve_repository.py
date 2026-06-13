"""
Demand Serve Repository for v2 RTM Scheduling
Uses demand_serve collection with proper field mappings
"""

from typing import List, Dict, Optional, Any
from .base_repository import GenericRepository
from ...core.config import settings
import logging
from datetime import datetime, timedelta
import asyncio
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

IDF_TYPE = "IDF"

class DemandServeRepository(GenericRepository):
    """
    Repository for demand_serve collection using v2 pattern.
    Field mappings:
    - schedule_date (old) → scheduled_at.$date (new)
    - Actual (old) → actual (new)
    - Drawal (old) → drawal (new)
    - MAPE (old) → mape (new)
    - MPE (old) → mpe (new)
    - Raw_Frequency (old) → frequency (new)
    - Schedule (old) → schedule (new)
    - forecast (old) → forecast (new)
    - Demand_actual_vs_forecast (PHP field name) → forecast (new) - CRITICAL for TotalAvailability compatibility
    - forecasted_frequency (old) → only in old table, use forecast if needed
    """
    
    def __init__(self):
        super().__init__()
        self.collection_name = settings.MONGODB_DEMAND_SERVE_COLLECTION if hasattr(settings, 'MONGODB_DEMAND_SERVE_COLLECTION') else "demand_serve"
        self.temp_demand_collection_name = settings.MONGODB_TEMP_DEMAND_SERVE_COLLECTION if hasattr(settings, 'MONGODB_TEMP_DEMAND_SERVE_COLLECTION') else "temp_demand_serve"
        
        logger.info(f"DemandServeRepository initialized with collection: {self.collection_name}")

    @property
    def collection(self):
        return self.db[self.collection_name]

    def ensure_graph_lookup_indexes(self) -> None:
        """Supports DAM graph $lookup on type DAF + scheduled_at + time_block."""
        try:
            self.collection.create_index(
                [("type", 1), ("scheduled_at", 1), ("time_block", 1)],
                name="type_sched_at_time_block_1",
                background=True,
            )
        except Exception as e:
            logger.warning("demand_serve index ensure skipped: %s", e)

    async def _get_demand_sync(self, schedule_date: str) -> List[Dict[str, Any]]:
        """Get demand data synchronously"""
        try:
            # Convert date string to datetime for scheduled_at matching
            target_date = datetime.strptime(schedule_date, "%Y-%m-%d")
            end_date = target_date + timedelta(days=1)
            
            pipeline = [
                {
                    "$match": {
                        "scheduled_at": {"$gte": target_date, "$lt": end_date},
                        "type": "IDF"  # RTM uses IDF (Intraday Forecast)
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        # Map scheduled_at.$date to schedule_date for compatibility
                        "schedule_date": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$scheduled_at"
                            }
                        },
                        "time_block": 1,
                        "type": 1,
                        "zone": 1,
                        # Map new fields to old field names for compatibility
                        "Actual": "$actual",  # Map actual → Actual
                        "actual": "$actual",  # Also keep standard field
                        "Drawal": "$drawal",  # Map drawal → Drawal
                        "drawal": "$drawal",  # Also keep standard field
                        "MAPE": "$mape",  # Map mape → MAPE
                        "mape": "$mape",  # Also keep standard field
                        "MPE": "$mpe",  # Map mpe → MPE
                        "mpe": "$mpe",  # Also keep standard field
                        "Raw_Frequency": "$frequency",  # Map frequency → Raw_Frequency
                        "frequency": "$frequency",  # Also keep standard field
                        "Schedule": "$schedule",  # Map schedule → Schedule
                        "schedule": "$schedule",  # Also keep standard field
                        "forecast": 1,
                        "Demand_actual_vs_forecast": "$forecast",  # CRITICAL: Map forecast → Demand_actual_vs_forecast for PHP compatibility (used in TotalAvailability)
                        "forecasted_frequency": {"$literal": 0},  # Not in demand_serve, set to 0
                        "comp_act_demand": 1,
                        "comp_udm": 1,
                        "net_dsm_amount": 1,
                        "rostering": 1
                    }
                },
                {
                    "$sort": {"time_block": 1}
                }
            ]
            
            result = await self.aggregate(self.collection_name, pipeline)
            return list(result) if result else []
        except Exception as e:
            logger.error(f"Failed to get demand data: {str(e)}")
            return []

    async def get_demand_power_optimization(
        self,
        schedule_date: str,
        mode: str = "forecast",
        demand_type: str = "IDF"
    ) -> List[Dict[str, Any]]:
        """
        Dedicated method for Power Optimization API with historical fallback.
        Args:
            schedule_date: YYYY-MM-DD
            mode: "forecast" or "optimization"
            demand_type: "IDF" or "DAF" (used mainly in optimization mode)
        """
        try:
            # Convert date string to datetime for scheduled_at matching
            target_date = datetime.strptime(schedule_date, "%Y-%m-%d")
            end_date = target_date + timedelta(days=1)

            # Compute current 15-min block (1..96). Use integer arithmetic to be safe.
            now = datetime.now()
            current_time_block = int(now.hour * 4 + (now.minute // 15) + 1)

            # Build match dictionary
            match_dict = {
                "scheduled_at": {"$gte": target_date, "$lt": end_date},
            }

            if mode == "forecast":
                # specific to forecast requirements
                match_dict["type"] = "IDF" 
                
                # Logic: 
                # Future: No data
                # Today: Up to previous block (< current)
                # Past: All blocks (96)
                
                if target_date.date() > now.date():
                    return []
                elif target_date.date() == now.date():
                    match_dict["time_block"] = {"$lt": current_time_block}
                
                projection = {
                    "_id": 0,
                    "schedule_date": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$scheduled_at"
                        }
                    },
                    "time_block": 1,
                    "comp_udm": 1,
                    "frequency": 1
                }
            else:
                # Optimization mode
                match_dict["type"] = demand_type # Use user provided type (IDF/DAF)
                # Optimization typically needs data for the whole day (all 96 blocks) for planning?
                # Removing the time_block <= current restriction for optimization mode allows fu ture planning.
                
                projection = {
                    "_id": 0,
                    "schedule_date": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$scheduled_at"
                        }
                    },
                    "time_block": 1,
                    "comp_act_demand": 1, # Use computed actual demand
                    "forecast": 1,  # Fallback for DAF when comp_act_demand is missing/0
                    # "frequency": 1 # Frequency not needed for optimization
                }

            pipeline = [
                {
                    "$match": match_dict
                },
                {
                    "$project": projection
                },
                {"$sort": {"time_block": 1}}
            ]
            
            result = await self.aggregate(self.collection_name, pipeline)

            if not result:
                historical_collection = getattr(settings, 'MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION', "demand_serve_historical")
                logger.debug(f"PowerOpt: No demand data in {self.collection_name}, trying {historical_collection}")

                count_query = match_dict.copy()

                try:
                    collection = self.db[historical_collection]
                    hist_count = await collection.count_documents(count_query, limit=1, maxTimeMS=5000)
                except Exception as hist_err:
                    logger.warning(f"PowerOpt: Historical count check failed: {hist_err}")
                    hist_count = 0

                if hist_count == 0:
                    return []

                result = await self.aggregate(historical_collection, pipeline)

            return list(result) if result else []
        except Exception as e:
            logger.error(f"PowerOpt: Failed to get demand data: {str(e)}")
            return []

    async def get_demand(self, schedule_date: str) -> List[Dict[str, Any]]:
        """
        Get demand forecast data for a schedule date.
        Returns data with both old and new field names for compatibility.

        Args:
            schedule_date: Date in YYYY-MM-DD format

        Returns:
            List of demand records with mapped fields
        """
        return await self._get_demand_sync(schedule_date)

    async def get_demand_async(self, schedule_date: str) -> List[Dict[str, Any]]:
        """Get demand data asynchronously (alias for get_demand)"""
        return await self._get_demand_sync(schedule_date)

    async def find_historical_by_date_and_blocks(
        self,
        schedule_date: str,
        from_block: int = 1,
        to_block: int = 96,
    ) -> List[Dict[str, Any]]:
        """Single-day convenience wrapper around find_historical_by_date_range."""
        return await self.find_historical_by_date_range(
            from_date=schedule_date,
            to_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
        )

    async def find_historical_by_date_range(
        self,
        from_date: str,
        to_date: str,
        from_block: int = 1,
        to_block: int = 96,
    ) -> List[Dict[str, Any]]:
        """
        Return IDF demand rows for an inclusive date range and time block range.

        Queries demand_serve and demand_serve_historical, preferring demand_serve
        when the same (date, time_block) exists in both collections.
        """
        try:
            range_start = datetime.strptime(from_date, "%Y-%m-%d")
            range_end_exclusive = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
            historical_collection = getattr(
                settings,
                "MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION",
                "demand_serve_historical",
            )

            pipeline = self._build_demand_block_read_pipeline(
                range_start=range_start,
                range_end_exclusive=range_end_exclusive,
                from_block=from_block,
                to_block=to_block,
            )

            serve_results = await self.aggregate(self.collection_name, pipeline)
            hist_results = await self.aggregate(historical_collection, pipeline)

            serve_map = {
                (row.get("date"), row.get("time_block")): row
                for row in (serve_results or [])
            }
            merged: List[Dict[str, Any]] = list(serve_map.values())

            for row in hist_results or []:
                key = (row.get("date"), row.get("time_block"))
                if key not in serve_map:
                    merged.append(row)

            merged.sort(key=lambda row: (row.get("date") or "", row.get("time_block") or 0))

            logger.debug(
                "Demand historical API: merged %s serve + %s historical → %s records",
                len(serve_results or []),
                len(hist_results or []),
                len(merged),
            )
            return merged
        except Exception as e:
            logger.error(
                "Failed to get demand data for %s to %s blocks %s-%s: %s",
                from_date,
                to_date,
                from_block,
                to_block,
                e,
            )
            return []

    @staticmethod
    def _build_demand_block_read_pipeline(
        range_start: datetime,
        range_end_exclusive: datetime,
        from_block: int,
        to_block: int,
    ) -> List[Dict[str, Any]]:
        """Shared aggregation for demand_serve and demand_serve_historical reads."""
        return [
            {
                "$match": {
                    "scheduled_at": {
                        "$gte": range_start,
                        "$lt": range_end_exclusive,
                    },
                    "type": IDF_TYPE,
                    "time_block": {"$gte": from_block, "$lte": to_block},
                }
            },
            {
                "$addFields": {
                    "_drawal": {"$ifNull": ["$drawal", "$Drawal"]},
                    "_frequency": {"$ifNull": ["$frequency", "$Raw_Frequency"]},
                    "_schedule": {"$ifNull": ["$schedule", "$Schedule"]},
                    "_od_ud": {"$ifNull": ["$od_ud", "$odud"]},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "date": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$scheduled_at",
                        }
                    },
                    "time_block": 1,
                    "drawal": "$_drawal",
                    "actual_demand": {
                        "$ifNull": ["$comp_act_demand", "$actual"]
                    },
                    "schedule": "$_schedule",
                    "od_ud": {
                        "$round": [
                            {
                                "$ifNull": [
                                    "$_od_ud",
                                    {"$subtract": ["$_drawal", "$_schedule"]},
                                ]
                            },
                            2,
                        ]
                    },
                    "dsm_rate": 1,
                    "net_dsm_amount": 1,
                    "frequency": "$_frequency",
                }
            },
            {"$sort": {"scheduled_at": 1, "time_block": 1}},
        ]

