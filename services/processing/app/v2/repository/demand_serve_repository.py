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

    def _get_demand_sync(self, schedule_date: str) -> List[Dict[str, Any]]:
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
            
            result = self.aggregate(self.collection_name, pipeline)
            return list(result) if result else []
        except Exception as e:
            logger.error(f"Failed to get demand data: {str(e)}")
            return []

    def get_demand_power_optimization(
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
            
            result = self.aggregate(self.collection_name, pipeline)
            
            if not result:
                historical_collection = getattr(settings, 'MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION', "demand_serve_historical")
                logger.debug(f"PowerOpt: No demand data in {self.collection_name}, trying {historical_collection}")
                
                # Copy match_dict for count check
                count_query = match_dict.copy()
                
                try:
                    collection = self.db[historical_collection]
                    hist_count = collection.count_documents(count_query, limit=1, maxTimeMS=5000)
                except Exception as hist_err:
                    logger.warning(f"PowerOpt: Historical count check failed: {hist_err}")
                    hist_count = 0
                
                if hist_count == 0:
                    return []
                
                result = self.aggregate(historical_collection, pipeline)
                
            return list(result) if result else []
        except Exception as e:
            logger.error(f"PowerOpt: Failed to get demand data: {str(e)}")
            return []

    def get_demand(self, schedule_date: str) -> List[Dict[str, Any]]:
        """
        Get demand forecast data for a schedule date.
        Returns data with both old and new field names for compatibility.
        
        Args:
            schedule_date: Date in YYYY-MM-DD format
            
        Returns:
            List of demand records with mapped fields
        """
        return self._get_demand_sync(schedule_date)

    async def get_demand_async(self, schedule_date: str) -> List[Dict[str, Any]]:
        """Get demand data asynchronously"""
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(executor, self._get_demand_sync, schedule_date)

