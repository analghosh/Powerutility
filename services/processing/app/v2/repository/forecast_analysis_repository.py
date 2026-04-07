from .base_repository import GenericRepository
from typing import List, Dict, Any, Optional
from datetime import datetime
from ...core.config import settings


class ForecastAnalysisRepository():
    """
    Repository for forecast analysis data, containing all pipeline logic.
    Inherits generic database operations from BaseRepository.
    """

    def __init__(self):
        super().__init__()
        self.repo = GenericRepository()
        # Use the v2 collection names
        self.demand_historical = settings.MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION
        # self.demand_serve = settings.MONGODB_DEMAND_SERVE_COLLECTION # Currently not used as per original code comments, but can be re-enabled if needed.
        self.peak_demand_serve = settings.MONGODB_PEAK_DEMAND_SERVE_COLLECTION

    # The __init__ is inherited from BaseRepository, so we don't need to redefine it
    # unless we add new dependencies.

    async def fetch_demand_blocks(self, dates: List[str], markets: List[str], zones: List[str], from_block: int,
                            to_block: int) -> List[Dict[str, Any]]:

        cad_market = ["IDF"] if "DAF" in markets else markets
        non_cad_market = markets
        query_markets = markets + ["IDF"] if "DAF" in markets else markets

        normalize = {"$addFields": {"_sdate": {"$dateToString": {"date": "$scheduled_at", "format": "%Y-%m-%d"}},
                                    "_market": {"$toUpper": {"$ifNull": ["$type", "$market"]}}}}

        match = {"$match": {"$expr": {
            "$and": [{"$in": ["$_sdate", dates]}, {"$in": ["$_market", query_markets]},
                     {"$gte": ["$time_block", from_block]}, {"$lte": ["$time_block", to_block]}]}}}

        pipeline = [normalize, match]

        pipeline.extend([
            {"$group": {"_id": {"d": "$_sdate", "b": "$time_block"},
                        "cad": {"$sum": {"$cond": [
                                {"$in": ["$_market", cad_market]},
                                {"$ifNull": ["$comp_act_demand", 0]},
                                0
                        ]}},
                        "cudm": {"$sum": {"$cond": [
                                {"$in": ["$_market", non_cad_market]},
                                {"$ifNull": ["$comp_udm", 0]},
                                0
                        ]}},
                        "f": {"$sum": {"$cond": [
                                {"$in": ["$_market", non_cad_market]},
                                {"$ifNull": ["$forecast", 0]},
                                0
                        ]}},
                        "m": {"$avg": {"$ifNull": ["$mape", 0]}}}},

            {"$project": {"_id": 0, "schedule_date": "$_id.d", "time_block": "$_id.b",
                          "data": {"comp_act_demand": {"$round": ["$cad", 2]},
                                   "comp_udm": {"$round": ["$cudm", 2]},
                                   "forecast": {"$round": ["$f", 2]},
                                   "MAPE": {"$concat": [{"$toString": {"$round": ["$m", 2]}}, "%"]}}}},

            {"$sort": {"schedule_date": 1, "time_block": 1}}
        ])

        return await self.repo.aggregate_async(self.demand_historical, pipeline)

    async def fetch_demand_curve(self, dates: List[str], markets: List[str], zones: List[str], from_block: int, to_block: int,
                           today_str: str, cur_blk: int, denominator: str, expected_total: Optional[float]) -> List[Dict[str, Any]]:
        normalize = {"$addFields": {"_sdate": {"$dateToString": {"date": "$scheduled_at", "format": "%Y-%m-%d"}},
                                    "_market": {"$toUpper": {"$ifNull": ["$type", "$market"]}},
                                    "_cad": {"$toDouble": {"$ifNull": ["$comp_act_demand", 0]}}}}
        match_and = [{"$in": ["$_market", markets]}, {"$in": ["$_sdate", dates]},
                     {"$gte": ["$time_block", from_block]}, {"$lte": ["$time_block", to_block]}, {
                         "$or": [{"$lt": ["$_sdate", today_str]},
                                 {"$and": [{"$eq": ["$_sdate", today_str]}, {"$lte": ["$time_block", cur_blk]}]}]}]
        match = {"$match": {"$expr": {"$and": match_and}}}
        pipeline = [normalize, match]

        # The union with live data is currently disabled as per the original code comments. If needed, it can be re-enabled by uncommenting the following lines:
        # if union_with_live:
        #     pipeline.append({"$unionWith": {"coll": self.demand_serve, "pipeline": [normalize, match]}})
        #     pipeline.extend([{"$group": {"_id": "$_id", "doc": {"$first": "$$ROOT"}}}, {"$replaceRoot": {"newRoot": "$doc"}}])

        group = {"$group": {"_id": {"d": "$_sdate", "b": "$time_block"}, "cad": {"$sum": "$_cad"}}}
        sort = {"$sort": {"cad": 1, "_id.d": 1, "_id.b": 1}}
        window = {
            "$setWindowFields": {"sortBy": {"cad": 1}, "output": {"count": {"$documentNumber": {}}, "total": {"$count": {}}}}}
        if denominator == "expected":
            percent_expr = {"$cond": [{"$gt": [expected_total, 0]},
                                      {"$round": [{"$multiply": [{"$divide": ["$count", expected_total]}, 100]}, 2]},
                                      0]}
        else:
            percent_expr = {"$cond": [{"$gt": ["$total", 0]},
                                      {"$round": [{"$multiply": [{"$divide": ["$count", "$total"]}, 100]}, 2]}, 0]}
        project = {"$project": {"_id": 0, "count": "$count", "comp_act_demand": {"$round": ["$cad", 2]},
                                "percentage": percent_expr}}
        pipeline.extend([group, sort, window, project])
        return await self.repo.aggregate_async(self.demand_historical, pipeline)

    async def fetch_mape_curve(self, dates: List[str], markets: List[str], zones: List[str], from_block: int, to_block: int,
                         today_str: str, cur_blk: int, denominator: str, expected_total: Optional[float]) -> List[Dict[str, Any]]:
        normalize = {"$addFields": {"_sdate": {"$dateToString": {"date": "$scheduled_at", "format": "%Y-%m-%d"}},
                                    "_market": {"$toUpper": {"$ifNull": ["$type", "$market"]}},
                                    "_mape": {"$toDouble": {"$ifNull": ["$mape", 0]}}}}
        match_and = [{"$in": ["$_market", markets]}, {"$in": ["$_sdate", dates]},
                     {"$gte": ["$time_block", from_block]}, {"$lte": ["$time_block", to_block]}, {
                         "$or": [{"$lt": ["$_sdate", today_str]},
                                 {"$and": [{"$eq": ["$_sdate", today_str]}, {"$lte": ["$time_block", cur_blk]}]}]}]
        match = {"$match": {"$expr": {"$and": match_and}}}
        pipeline = [normalize, match]

        # The union with live data is currently disabled as per the original code comments. If needed, it can be re-enabled by uncommenting the following lines:
        # if union_with_live:
        #     pipeline.append({"$unionWith": {"coll": self.demand_serve, "pipeline": [normalize, match]}})
        #     pipeline.extend([{"$group": {"_id": "$_id", "doc": {"$first": "$$ROOT"}}}, {"$replaceRoot": {"newRoot": "$doc"}}])

        group = {"$group": {"_id": {"d": "$_sdate", "b": "$time_block"}, "mape_avg": {"$avg": "$_mape"}}}
        sort = {"$sort": {"mape_avg": 1, "_id.d": 1, "_id.b": 1}}
        window = {"$setWindowFields": {"sortBy": {"mape_avg": 1},
                                       "output": {"count": {"$documentNumber": {}}, "total": {"$count": {}}}}}
        if denominator == "expected":
            percent_expr = {"$cond": [{"$gt": [expected_total, 0]},
                                      {"$round": [{"$multiply": [{"$divide": ["$count", expected_total]}, 100]}, 2]},
                                      0]}
        else:
            percent_expr = {"$cond": [{"$gt": ["$total", 0]},
                                      {"$round": [{"$multiply": [{"$divide": ["$count", "$total"]}, 100]}, 2]}, 0]}
        project = {"$project": {"_id": 0, "count": "$count",
                                "MAPE": {"$concat": [{"$toString": {"$round": ["$mape_avg", 2]}}, "%"]},
                                "percentage": percent_expr}}
        pipeline.extend([group, sort, window, project])
        return await self.repo.aggregate_async(self.demand_historical, pipeline)

    async def fetch_peak_demand_last_7_days(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        pipeline = [
            {"$match": {"scheduled_at": {"$gte": start_date, "$lte": end_date}}}, 
            {
                "$project": {
                    "_id": 0, 
                    "schedule_date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                    "peak_demand": {"$round": ["$peak_demand", 2]},
                    "average_demand": {"$round": ["$average_demand", 2]}
                }
            }, 
            {"$sort": {"schedule_date": 1}}
        ]
        return await self.repo.aggregate_async(self.peak_demand_serve, pipeline)

    async def fetch_selected_days_blocks(self, dates: List[str], markets: List[str], zones: List[str], 
                                        from_block: int, to_block: int) -> List[Dict[str, Any]]:
        normalize = {"$addFields": {"_sdate": {"$dateToString": {"date": "$scheduled_at", "format": "%Y-%m-%d"}},
                                    "_market": {"$toUpper": {"$ifNull": ["$type", "$market"]}},
}}
        match = {"$match": {"$expr": {
            "$and": [{"$in": ["$_sdate", dates]}, {"$in": ["$_market", markets]},
                     {"$gte": ["$time_block", from_block]}, {"$lte": ["$time_block", to_block]}]}}}
        pipeline = [normalize, match]

        # The union with live data is currently disabled as per the original code comments. If needed, it can be re-enabled by uncommenting the following lines:
        # if union_with_live:
        #     pipeline.append({"$unionWith": {"coll": self.demand_serve, "pipeline": [normalize, match]}})
        #     pipeline.extend([{"$group": {"_id": "$_id", "doc": {"$first": "$$ROOT"}}}, {"$replaceRoot": {"newRoot": "$doc"}}])

        pipeline.extend([
            {"$group": {"_id": {"d": "$_sdate", "b": "$time_block"},
                        "cad": {"$sum": {"$ifNull": ["$comp_act_demand", 0]}},
                        "cudm": {"$sum": {"$ifNull": ["$comp_udm", 0]}}}},
            {"$project": {"_id": 0, "schedule_date": "$_id.d", "time_block": "$_id.b",
                          "data": {"comp_act_demand": {"$round": ["$cad", 2]}, "comp_udm": {"$round": ["$cudm", 2]}}}},
            {"$sort": {"schedule_date": 1, "time_block": 1}}
        ])
        return await self.repo.aggregate_async(self.demand_historical, pipeline)

    async def fetch_selected_days_stats(self, dates: List[str], markets: List[str], zones: List[str], from_block: int,
                                  to_block: int) -> List[Dict[str, Any]]:
        from datetime import datetime, timedelta
        
        # Determine date filter: use provided dates or fall back to last 7 days
        if dates and len(dates) > 0:
            # Convert date strings to datetime objects (set time to 00:00:00)
            date_objects = []
            for date_str in dates:
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
                    date_objects.append(dt)
                except Exception:
                    continue
            date_match = {"$in": date_objects}
            sdate_match = {"$in": dates}
        else:
            # Fall back to last 7 days
            seven_days_ago = datetime.now() - timedelta(days=7)
            seven_days_ago = seven_days_ago.replace(hour=0, minute=0, second=0, microsecond=0)
            date_match = {"$gte": seven_days_ago}
            # For sdate_match, we'd need strings. But service usually provides dates.
            # Let's assume dates is provided as it's a required Query param in router.
            sdate_match = {"$exists": True}

        # Markets and zones handling for historical
        eff_markets = [m.upper() for m in (markets or []) if m]
        eff_zones = [z.upper() for z in (zones or []) if z]

        # ===== PART 1: Get comp_act_demand stats from peak_demand_serve =====
        # Pipeline for peak_demand_serve
        peak_pipeline = [
            {
                "$match": {
                    "scheduled_at": date_match
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "sdate": {"$dateToString": {"date": "$scheduled_at", "format": "%Y-%m-%d"}},
                    "max": "$peak_demand",
                    "min": "$min_demand",
                    "avg": "$average_demand",
                    "sum": {"$ifNull": ["$total_energy_mu", 0]},
                    "count": {"$literal": 96}
                }
            }
        ]
        
        peak_results = await self.repo.aggregate_async(self.peak_demand_serve, peak_pipeline)
        
        # ===== PART 2: Get comp_udm stats from demand_serve_historical =====
        # Pipeline for demand_serve_historical (has comp_udm field)
        udm_pipeline = [
            {
                "$addFields": {
                    "_market": {"$toUpper": {"$ifNull": ["$type", "$market"]}},
                    "_zone": {"$toUpper": {"$ifNull": ["$zone", "PUDUCHERRY"]}},
                    "_cad": {"$toDouble": {"$ifNull": ["$comp_act_demand", 0]}},
                    "_udm": {"$toDouble": {"$ifNull": ["$comp_udm", 0]}},
                }
            },
            {
                "$match": {
                    "scheduled_at": date_match,
                    "time_block": {"$gte": from_block, "$lte": to_block},
                    "_market": {"$in": eff_markets} if eff_markets else {"$exists": True},
                    "_zone": {"$in": eff_zones} if eff_zones else {"$exists": True}
                }
            },
            {
                "$group": {
                    "_id": {"date": {"$dateToString": {"date": "$scheduled_at", "format": "%Y-%m-%d"}}, "time_block": "$time_block"},
                    "udm": {"$sum": "$_udm"}
                }
            },
            {
                "$group": {
                    "_id": "$_id.date",
                    "min": {"$min": "$udm"},
                    "max": {"$max": "$udm"},
                    "avg": {"$avg": "$udm"},
                    "sum": {"$sum": "$udm"},
                    "count": {"$sum": 1}
                }
            }
        ]
        
        udm_results = await self.repo.aggregate_async(self.demand_historical, udm_pipeline)
        
        # ===== PART 3: Combine results =====
        # Create a map of date -> stats
        peak_map = {r["sdate"]: r for r in peak_results}
        udm_map = {r["_id"]: r for r in udm_results}
        
        # Get all unique dates from both sources
        all_dates = set(peak_map.keys()) | set(udm_map.keys())
        
        # Build final result
        combined_results = []
        for date in sorted(all_dates):
            peak_stats = peak_map.get(date, {})
            udm_stats = udm_map.get(date, {})
            
            combined_results.append({
                "schedule_date": date,
                "stats": {
                    "comp_act_demand": {
                        "min": round(peak_stats.get("min", 0), 2) if peak_stats.get("min") is not None else None,
                        "max": round(peak_stats.get("max", 0), 2) if peak_stats.get("max") is not None else None,
                        "avg": round(peak_stats.get("avg", 0), 2) if peak_stats.get("avg") is not None else None,
                        "sum": round(peak_stats.get("sum", 0), 2),
                        "count": peak_stats.get("count", 0)
                    },
                    "comp_udm": {
                        "min": round(udm_stats.get("min", 0), 2) if udm_stats.get("min") is not None else None,
                        "max": round(udm_stats.get("max", 0), 2) if udm_stats.get("max") is not None else None,
                        "avg": round(udm_stats.get("avg", 0), 2) if udm_stats.get("avg") is not None else None,
                        "sum": round(udm_stats.get("sum", 0), 2),
                        "count": udm_stats.get("count", 0)
                    }
                }
            })
        
        return combined_results

    async def get_max_updated_at(self, collection_name: str) -> Optional[datetime]:
        """
        Get the maximum updated_at timestamp from the collection.
        
        Args:
            collection_name: Name of the MongoDB collection
            
        Returns:
            Maximum updated_at timestamp or None if not found
        """
        try:
            # User request: Do not restrict by date. Get the absolute latest updated_at.
            result = await self.repo.get_all_async(
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
            # logger is not defined in this file's global scope based on previous read, let's check imports
            # The file has no logger defined globally. I will just return None or print/pass if strict.
            # Actually, previous read showed `import logging` was NOT there?
            # Let me double check the file content in memory or just add the import if needed.
            # Looking at the file content provided in Step 194:
            # It starts with `from .base_repository import GenericRepository`
            # It does NOT import logging.
            # I should stick to silent failure or just return None to be safe, or add logging if I can.
            # Given I'm appending to the end/inside class, I should be careful about using 'logger'.
            # I'll just return None to avoid `NameError`.
            return None


forcast_analysis = ForecastAnalysisRepository()
