import logging
import asyncio
import hashlib
import json
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from bson import ObjectId # Import ObjectId

# Update these import paths to match your project structure
from .base_repository import GenericRepository
from libs.helpers.timeblock_helper import block_label
from libs.cache.redis_cache import RedisCache
from ...core.config import settings

logger = logging.getLogger(__name__)


def block_to_time_str(block: Any) -> str:
    """Converts a time block number to a 'HH:MM-HH:MM' string."""
    try:
        return block_label(int(block))
    except (ValueError, TypeError):
        return "00:00-00:00"


class DashboardRepository:
    """
    Fetches dashboard data using separate, dedicated methods for each data component for easier debugging.
    """

    def __init__(self):
        self.repository = GenericRepository()
        self.demand_serve = settings.MONGODB_DEMAND_SERVE_COLLECTION
        self.exchange_serve = settings.MONGODB_SERVE_EXCHANGE_COLLECTION
        self.peak_demand_serve = settings.MONGODB_PEAK_DEMAND_SERVE_COLLECTION
        self.plant_master = settings.MONGODB_PLANT_MASTER_COLLECTION
        self.supply_serve = settings.MONGODB_SUPPLY_SERVE_COLLECTION
        self.temp_demand_serve = settings.MONGODB_TEMP_DEMAND_SERVE_COLLECTION

        try:
            self.cache = RedisCache()
            self.cache_enabled = True
        except Exception as e:
            logger.warning(f"Redis cache not available: {e}")
            self.cache = None
            self.cache_enabled = False

    def _get_match_query_for_window(self, schedule_date: datetime, current_block: int, window_block_ids: List[int]) -> Dict:
        """Builds a MongoDB match query that handles midnight crossovers for a given window of blocks."""
        if not window_block_ids:
            return {"time_block": {"$in": []}}

        crosses_midnight = window_block_ids[0] > window_block_ids[-1]
        
        queries = []
        if crosses_midnight:
            split_index = window_block_ids.index(1)
            if current_block < 12:  # PAST CROSSOVER
                yesterday_date = schedule_date - timedelta(days=1)
                yesterday_blocks = window_block_ids[:split_index]
                today_blocks = window_block_ids[split_index:]
                if yesterday_blocks:
                    queries.append({"scheduled_at": yesterday_date, "time_block": {"$in": yesterday_blocks}})
                if today_blocks:
                    queries.append({"scheduled_at": schedule_date, "time_block": {"$in": today_blocks}})
            else:  # FUTURE CROSSOVER
                tomorrow_date = schedule_date + timedelta(days=1)
                today_blocks = window_block_ids[:split_index]
                tomorrow_blocks = window_block_ids[split_index:]
                if today_blocks:
                    queries.append({"scheduled_at": schedule_date, "time_block": {"$in": today_blocks}})
                if tomorrow_blocks:
                    queries.append({"scheduled_at": tomorrow_date, "time_block": {"$in": tomorrow_blocks}})
        else:
            queries.append({"scheduled_at": schedule_date, "time_block": {"$in": window_block_ids}})

        if not queries:
            return {"time_block": {"$in": []}}
            
        return {"$or": queries} if len(queries) > 1 else queries[0]

    async def get_dashboard_data(
            self, schedule_date_str: str, current_block: int
    ) -> Dict[str, Any]:
        try:
            schedule_date = datetime.fromisoformat(schedule_date_str.replace("Z", "+00:00"))
        except ValueError:
            schedule_date = datetime.strptime(schedule_date_str, "%Y-%m-%d")

        try:
            # 1. DEFINE THE 24-BLOCK WINDOW (11 before, current, 12 after)
            window_block_ids = [((current_block - 1 + offset) % 96) + 1 for offset in range(-11, 13)]

            # 2. DETECT MIDNIGHT CROSSOVER AND FETCH DATA
            match_query = self._get_match_query_for_window(schedule_date, current_block, window_block_ids)
            # Ensure only IDF type data is fetched
            all_daily_blocks_raw = await self.repository.get_all_async(
                collection_name=self.demand_serve,
                filter={**match_query, "type": "IDF"}
            )

            # Create a map for easy lookup and to ensure correct order
            # Handle both datetime objects and string dates from database
            def get_date_key(scheduled_at):
                if isinstance(scheduled_at, str):
                    try:
                        return datetime.fromisoformat(scheduled_at.replace("Z", "+00:00")).date()
                    except ValueError:
                        return datetime.strptime(scheduled_at, "%Y-%m-%d").date()
                elif hasattr(scheduled_at, 'date'):
                    return scheduled_at.date()
                else:
                    return scheduled_at
            
            block_map = {(get_date_key(b.get('scheduled_at')), b.get('time_block')): b for b in all_daily_blocks_raw}
            
            all_daily_blocks = []
            yesterday_date = schedule_date - timedelta(days=1)
            tomorrow_date = schedule_date + timedelta(days=1)
            
            crosses_midnight = window_block_ids[0] > window_block_ids[-1]
            if crosses_midnight:
                split_index = window_block_ids.index(1)
                if current_block < 12: # PAST
                    for bid in window_block_ids[:split_index]:
                        all_daily_blocks.append(block_map.get((yesterday_date.date(), bid), {"time_block": bid}))
                    for bid in window_block_ids[split_index:]:
                        all_daily_blocks.append(block_map.get((schedule_date.date(), bid), {"time_block": bid}))
                else: # FUTURE
                    for bid in window_block_ids[:split_index]:
                        all_daily_blocks.append(block_map.get((schedule_date.date(), bid), {"time_block": bid}))
                    for bid in window_block_ids[split_index:]:
                        all_daily_blocks.append(block_map.get((tomorrow_date.date(), bid), {"time_block": bid}))
            else:
                for bid in window_block_ids:
                    all_daily_blocks.append(block_map.get((schedule_date.date(), bid), {"time_block": bid}))


            # 3. FETCH OTHER DATA AND FORMAT RESPONSE
            # Ensure current_block is in valid range (1-96), never 0
            # If current_block is 1, wrap to 96 for display (as per requirement: block 1 should show as 96)
            # Clamp to 1-96 range first to prevent 0 or invalid values
            current_block = max(1, min(96, current_block ))
            display_block = current_block
            
            exchange_data, peak_demand_data, scheduled_drawal = await asyncio.gather(
                self._get_exchange_data(schedule_date, display_block, window_block_ids),
                self._get_peak_demand_data(schedule_date),
                self.get_dashboard_scheduled_drawal(schedule_date, display_block, window_block_ids)
            )
            return {
                "demand": self._format_demand_data(all_daily_blocks, display_block),
                "frequency": self._format_frequency_data(all_daily_blocks, display_block),
                "schedule_drawal": scheduled_drawal,
                "od_ud": self._format_od_ud_data(all_daily_blocks, display_block, scheduled_drawal),
                "dsm_rate": self._format_dsm_rate_data(all_daily_blocks, display_block),
                "net_dsm_amount": self._format_net_dsm_amount_data(all_daily_blocks, display_block),
                "total_net_dsm_amount": self._format_total_net_dsm_data(all_daily_blocks, display_block),
                **exchange_data,
                "peak_demand": peak_demand_data,
            }
            # return {
            #     "demand": self._format_demand_data(all_daily_blocks, current_block),
            #     "frequency": self._format_frequency_data(all_daily_blocks, current_block),
            #     "schedule_drawal": scheduled_drawal,
            #     "od_ud": self._format_od_ud_data(all_daily_blocks, current_block),
            #     "dsm_rate": self._format_dsm_rate_data(all_daily_blocks, current_block),
            #     "net_dsm_amount": self._format_net_dsm_amount_data(all_daily_blocks, current_block),
            #     "total_net_dsm_amount": self._format_total_net_dsm_data(all_daily_blocks, current_block),
            #     **exchange_data,
            #     "peak_demand": peak_demand_data,
            # }

        except Exception as e:
            logger.error(f"Failed to generate dashboard data for {schedule_date_str}: {e}", exc_info=True)
            return self._get_default_empty_structure()

    async def _get_daily_blocks_by_ids(self, schedule_date: datetime, block_ids: List[int]) -> List[Dict[str, Any]]:
        """Efficiently fetches specific blocks for a given day."""
        if not block_ids:
            return []
        try:
            return await self.repository.get_all_async(
                collection_name=self.demand_serve,
                filter={"scheduled_at": schedule_date, "time_block": {"$in": block_ids}, "type": "IDF"},
            )
        except Exception as e:
            logger.error(f"Could not fetch blocks {block_ids} for {schedule_date.date()}: {e}")
            return []

    async def get_dashboard_scheduled_drawal(self, schedule_date: datetime, current_block_id: int,
                                             window_block_ids: list) -> Dict[str, Any]:
        """Builds Scheduled Drawal for the specified window."""
        match_query = self._get_match_query_for_window(schedule_date, current_block_id, window_block_ids)
        
        pipeline = [
            {"$match": {**match_query, "dc_sg": "SG"}},
            {"$lookup": {"from": self.plant_master, "localField": "plant_id", "foreignField": "plant_id",
                         "as": "plant_details"}},
            {"$unwind": "$plant_details"},
            {"$group": {
                "_id": {"time_block": "$time_block", "scheduled_at": "$scheduled_at"},
                "InterState": {"$sum": {"$cond": [{"$eq": ["$plant_details.c_s", "central"]}, {
                    "$convert": {"input": "$volume", "to": "double", "onError": 0, "onNull": 0}}, 0]}},
                "IntraState": {"$sum": {"$cond": [{"$eq": ["$plant_details.c_s", "state"]}, {
                    "$convert": {"input": "$volume", "to": "double", "onError": 0, "onNull": 0}}, 0]}},
            }},
            {"$project": {"_id": 0, "time_block": "$_id.time_block", "scheduled_at": "$_id.scheduled_at",
                          "InterState": {"$round": ["$InterState", 2]}, "IntraState": {"$round": ["$IntraState", 2]}}},
        ]
        rows = list(await self.repository.aggregate_async(self.supply_serve, pipeline))
        rows_map = {r['time_block']: r for r in rows}

        window_rows = [rows_map.get(bid, {}) for bid in window_block_ids]
        current_data = rows_map.get(current_block_id, {})

        return {
            "current_block": block_label(current_block_id),
            "inter_state": current_data.get("InterState"),
            "intra_state": current_data.get("IntraState"),
            "inter_state_data": [{"time_block": bid, "time": block_label(bid), "data": r.get("InterState")} for bid, r
                                 in zip(window_block_ids, window_rows)],
            "intra_state_data": [{"time_block": bid, "time": block_label(bid), "data": r.get("IntraState")} for bid, r
                                 in zip(window_block_ids, window_rows)],
        }

    async def _get_exchange_data(self, schedule_date: datetime, current_block: int, window_block_ids: List[int]) -> \
    Dict[str, Any]:
        """Runs separate aggregation pipelines for DAM and RTM data on `exchange_serve`."""

        rtm_block_ids = [((current_block + 3 + offset) % 96) + 1 for offset in range(-11, 1)]
        dam_block_ids = window_block_ids

        rtm_match_query = self._get_match_query_for_window(schedule_date, current_block, rtm_block_ids)
        dam_match_query = self._get_match_query_for_window(schedule_date, current_block, dam_block_ids)

        def _get_pipeline(market_type: str, match_query: Dict):
            if not match_query.get("$or") and not match_query.get("time_block"): return None
            
            final_match = {"market_type": market_type, "px_name": "IEX", **match_query}

            return [
                {"$match": final_match},
                {"$sort": {"time_block": 1}},
                {"$project": {
                    "_id": 0, "time_block": 1, "mcp": 1, "acp": 1, "market_type": 1
                }}
            ]

        dam_pipeline = _get_pipeline("DAM", dam_match_query)
        rtm_pipeline = _get_pipeline("RTM", rtm_match_query)

        dam_task = self.repository.aggregate_async(self.exchange_serve, dam_pipeline) if dam_pipeline else asyncio.sleep(0, result=[])
        rtm_task = self.repository.aggregate_async(self.exchange_serve, rtm_pipeline) if rtm_pipeline else asyncio.sleep(0, result=[])

        dam_results, rtm_results = await asyncio.gather(dam_task, rtm_task)
        
        dam_results = dam_results or []
        rtm_results = rtm_results or []

        dam_map: Dict[int, float] = {}
        for b in dam_results:
            tb = b.get("time_block")
            if isinstance(tb, int):
                # The values from MongoDB are correct, just use them directly without rounding.
                # If they need to be rounded, round them, but do not multiply or shift.
                val = b.get("mcp")
                dam_map[tb] = round(float(val), 2) if val is not None else 0.0

        rtm_map: Dict[int, float] = {}
        acp_map: Dict[int, float] = {}
        for b in rtm_results:
            tb = b.get("time_block")
            if isinstance(tb, int):
                mcp_val = b.get("mcp")
                acp_val = b.get("acp")
                rtm_map[tb] = round(float(mcp_val), 2) if mcp_val is not None else 0.0
                acp_map[tb] = round(float(acp_val), 2) if acp_val is not None else 0.0

        # Show exactly 24 blocks: past 11, current 1, and future 12
        mcp_window = window_block_ids

        dam_data = [
            {"time_block": bid, "data": dam_map.get(bid, 0.0)}
            for bid in mcp_window
        ]

        rtm_max_block = current_block
        rtm_data = [
            {"time_block": bid, "data": rtm_map.get(bid, 0.0)}
            for bid in mcp_window
            if bid <= rtm_max_block
        ]

        acp_data = [
            {
                "time_block": bid,
                "time": block_to_time_str(bid),
                "acp": acp_map.get(bid, 0.0),
            }
            for bid in mcp_window
            if bid <= current_block
        ]

        # Find current values
        current_dam = next((d["data"] for d in dam_data if d.get("time_block") == current_block ), 0.0)
        current_rtm = next((r["data"] for r in rtm_data if r.get("time_block") == current_block ), 0.0)
        current_acp = next((a["acp"] for a in acp_data if a.get("time_block") == current_block), 0.0)

        return {
            "mcp": {"mcp": {"current_block": block_to_time_str(current_block), "rtm": current_rtm, "dam": current_dam,
                            "rtm_data": rtm_data, "dam_data": dam_data}},
            "acp": {"acp": {"current_block": block_to_time_str(current_block), "current_time_block": current_block,
                            "current_acp": current_acp, "data": acp_data}},
        }

    async def _get_peak_demand_data(self, schedule_date: datetime) -> Dict[str, Any]:
        """Gets peak demand for the last 31 days from pre-aggregated data."""
        end_date = schedule_date
        start_date = end_date - timedelta(days=31)
        pipeline = [
            {"$match": {"scheduled_at": {"$gte": start_date, "$lte": end_date}}},
            {"$sort": {"scheduled_at": 1}},
            {"$group": {
                "_id": None,
                "daily_peak_data": {
                    "$push": {"date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                              "time_block": "$time_block", "peak_demand": "$peak_demand"}},
                "all_data_for_max": {
                    "$push": {"peak_demand": "$peak_demand", "date": "$scheduled_at", "time_block": "$time_block"}},
            }},
            {"$addFields": {"overall_peak_details": {"$first": {"$filter": {"input": "$all_data_for_max", "cond": {
                "$eq": ["$$this.peak_demand", {"$max": "$all_data_for_max.peak_demand"}]}}}}}},
            {"$project": {
                "_id": 0,
                "overall_peak_demand_31_days": {
                    "max_date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$overall_peak_details.date"}},
                    "peak_demand": "$overall_peak_details.peak_demand",
                    "time_block": "$overall_peak_details.time_block"},
                "daily_peak_data": "$daily_peak_data",
            }},
        ]
        result = await self.repository.aggregate_async(self.peak_demand_serve, pipeline)
        if not result: return self._get_default_empty_structure(is_peak=True)
        return result[0]

    async def get_demand_raw_v2(
        self,
        collection_name: str,
        query: Dict[str, Any],
        projection: Optional[Dict[str, Any]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        skip: int = 0,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetches raw demand data from a specified collection using a given query.
        Converts ObjectId to string for Pydantic compatibility.
        """
        try:
            data = await self.repository.get_all_async(
                collection_name=collection_name,
                filter=query,
                projection=projection,
                sort=sort,
                skip=skip,
                limit=limit
            )
            # Convert ObjectId to string for Pydantic compatibility
            for item in data:
                if '_id' in item and isinstance(item['_id'], ObjectId):
                    item['_id'] = str(item['_id'])
            return data
        except Exception as e:
            logger.error(f"Error fetching demand raw V2 data from collection {collection_name} with query {query}: {str(e)}")
            raise

    async def get_max_updated_at(self, collection_name: str) -> Optional[str]:
        """
        Returns the maximum updated_at value from the specified collection.
        """
        try:
            # Use get_all_async with sort and limit 1 to emulate find_one with sort
            last_data = await self.repository.get_all_async(
                collection_name=collection_name,
                filter={},
                sort=[("updated_at", -1)],
                limit=1,
                projection={"updated_at": 1}
            )
            if last_data:
                updated_at = last_data[0].get("updated_at")
                if isinstance(updated_at, datetime):
                    return updated_at.isoformat()
                return str(updated_at) if updated_at else None
            return None
        except Exception as e:
            logger.exception(f"Failed to get max updated_at from {collection_name}")
            return None
    # --- Data Formatting Methods ---

    def _format_demand_data(self, window_blocks: List[Dict[str, Any]], current_block: int) -> Dict:
        current_block_data = next((b for b in window_blocks if b.get("time_block") == current_block), {})
        data = []
        for b in window_blocks:
            demand_value = b.get("actual", 0) if b.get("time_block", 0) <= current_block else b.get("forecast",
                                                                                                             0)
            data.append({"time_block": b.get("time_block"), "time": block_to_time_str(b.get("time_block")),
                         "demand": round(demand_value or 0, 2)})
        return {
            "current_block": block_to_time_str(current_block), "current_time_block": current_block,
            "current_demand": round(current_block_data.get("actual", 0) or 0, 2), "data": data,
        }

    def _format_frequency_data(self, window_blocks: List[Dict[str, Any]], current_block: int) -> Dict:
        current_block_data = next((b for b in window_blocks if b.get("time_block") == current_block), {})
        return {
            "current_block": block_to_time_str(current_block), "current_time_block": current_block,
            "current_frequency": round(current_block_data.get("frequency", 0) or 0, 2),
            "data": [{"time_block": b.get("time_block"), "time": block_to_time_str(b.get("time_block")),
                      "frequency": round(b.get("frequency", 0) or 0, 2)} for b in window_blocks],
        }

    def _format_od_ud_data(self, window_blocks: List[Dict[str, Any]], current_block: int, scheduled_drawal: Optional[Dict[str, Any]] = None) -> Dict:
        current_block_data = next((b for b in window_blocks if b.get("time_block") == current_block), {})
        
        # Create a map of intra_state_schedule_drawal and inter_state_schedule by time_block for quick lookup
        intra_state_map = {}
        inter_state_map = {}
        if scheduled_drawal:
            if scheduled_drawal.get("intra_state_data"):
                for item in scheduled_drawal["intra_state_data"]:
                    tb = item.get("time_block")
                    if tb:
                        intra_state_map[tb] = item.get("data", 0) or 0
            
            if scheduled_drawal.get("inter_state_data"):
                for item in scheduled_drawal["inter_state_data"]:
                    tb = item.get("time_block")
                    if tb:
                        inter_state_map[tb] = item.get("data", 0) or 0
        
        # Helper function to calculate OD_UD for a block
        def calculate_od_ud(block_data: Dict[str, Any]) -> float:
            time_block = block_data.get("time_block")
            drawal = block_data.get("drawal", 0) or 0
            
            # Get schedules
            intra_state_schedule = intra_state_map.get(time_block, 0) if time_block else 0
            inter_state_schedule = inter_state_map.get(time_block, 0) if time_block else 0
            
            # Use block_data schedule if available, else fallback to inter_state_schedule
            schedule = (inter_state_schedule + intra_state_schedule)
            
            # If actual is present and not zero, use drawal - (schedule - intra_state_schedule_drawal)
            if drawal is not None and drawal != 0:
                result = drawal - (schedule - intra_state_schedule)
                return result
            else:
                # If actual is not present, is None, or is 0, use drawal - schedule (current logic)
                # result = drawal - (inter_state_schedule + intra_state_schedule)
                result = drawal - schedule 

                return result
        
        current_od_ud = round(calculate_od_ud(current_block_data), 2)
        
        data = []
        for b in window_blocks:
            od_ud_value = round(calculate_od_ud(b), 2)
            data.append({
                "time_block": b.get("time_block"),
                "time": block_to_time_str(b.get("time_block")),
                "od_ud": od_ud_value
            })
        
        return {
            "current_block": block_to_time_str(current_block),
            "current_time_block": current_block,
            "current_od_ud": current_od_ud,
            "data": data,
        }

    def _format_dsm_rate_data(self, window_blocks: List[Dict[str, Any]], current_block: int) -> Dict:
        current_block_data = next((b for b in window_blocks if b.get("time_block") == current_block), {})
        return {
            "current_block": block_to_time_str(current_block), "current_time_block": current_block,
            "current_dsm_rate": round(current_block_data.get("dsm_rate", 0) or 0, 2),
            "data": [{"time_block": b.get("time_block"), "time": block_to_time_str(b.get("time_block")),
                      "dsm_rate": round(b.get("dsm_rate", 0) or 0, 2)} for b in window_blocks],
        }

    def _format_net_dsm_amount_data(self, window_blocks: List[Dict[str, Any]], current_block: int) -> Dict:
        current_block_data = next((b for b in window_blocks if b.get("time_block") == current_block), {})
        net_dsm_window = window_blocks[:12]  # Only first 12 blocks (same as od_ud)
        return {
            "current_block": block_to_time_str(current_block), "current_time_block": current_block,
            "current_net_dsm_amount": round(current_block_data.get("net_dsm_amount", 0) or 0, 2),
            "data": [{"time_block": b.get("time_block"), "time": block_to_time_str(b.get("time_block")),
                      "net_dsm_amount": round(b.get("net_dsm_amount", 0) or 0, 2)} for b in net_dsm_window],
        }

    def _format_total_net_dsm_data(self, window_blocks: List[Dict[str, Any]], current_block: int) -> Dict:
        total_dsm_window = window_blocks[:12]  # Only first 12 blocks (same as od_ud)
        cumulative_sum = 0
        cumulative_data = []
        for b in total_dsm_window:
            if b.get("time_block", 0) > current_block: break
            cumulative_sum += round(b.get("net_dsm_amount", 0) or 0, 2)
            cumulative_data.append({"time_block": b.get("time_block"), "time": block_to_time_str(b.get("time_block")),
                                    "total_net_dsm_amount": round(cumulative_sum, 2)})
        return {
            "current_block": block_to_time_str(current_block), "current_time_block": current_block,
            "current_total_net_dsm_amount": round(cumulative_sum, 2), "data": cumulative_data,
        }

    def _get_default_empty_structure(self, is_peak: bool = False) -> Dict:
        """
        Returns a default empty structure for graceful failure.
        """
        if is_peak: return {"overall_peak_demand_31_days": {}, "daily_peak_data": []}
        return {"demand": {}, "frequency": {}, "schedule_drawal": {}, "od_ud": {}, "mcp": {}, "acp": {}, "dsm_rate": {},
                "net_dsm_amount": {}, "total_net_dsm_amount": {}, "peak_demand": {}, }



# Global instance
dashboard_repository = DashboardRepository()
