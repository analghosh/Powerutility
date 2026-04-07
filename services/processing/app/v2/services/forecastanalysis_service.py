
import logging
from datetime import datetime, timedelta, timezone, date
from typing import List, Optional, Dict, Any, Literal
import numpy as np

from services.processing.app.v2.repository.forecast_analysis_repository import forcast_analysis

logger = logging.getLogger(__name__)

class ForecastAnalysisService:
    IST = timezone(timedelta(hours=5, minutes=30))

    def __init__(self):
        self.repo = forcast_analysis

    # ---------- Helper Methods ----------
    @classmethod
    def _today_ist_str(cls) -> str: return datetime.now(cls.IST).date().isoformat()
    @classmethod
    def _today_ist_date(cls) -> date: return datetime.now(cls.IST).date()
    @staticmethod
    def _parse_yyyy_mm_dd(val: str) -> date: return datetime.strptime(val, "%Y-%m-%d").date()
    def _date_includes_today(self, s: str, e: str) -> bool: return self._parse_yyyy_mm_dd(s) <= self._today_ist_date() <= self._parse_yyyy_mm_dd(e)
    def _current_block_ist(self) -> int: now = datetime.now(self.IST).time(); return (now.hour * 4) + (now.minute // 15) + 1
    @staticmethod
    def _ensure_markets(m: Optional[List[str]]) -> List[str]: return sorted({(s or "").strip().upper() for s in (m or []) if s and s.strip()}) or ["IDF"]
    @staticmethod
    def _ensure_zones(z: Optional[List[str]]) -> List[str]: return sorted({(s or "").strip().upper() for s in (z or []) if s and s.strip()}) or ["PUDUCHERRY"]
    @staticmethod
    def _clamp_block(n: Optional[int]) -> int: n = int(n or 0); return 1 if n < 1 else 96 if n > 96 else n
    @staticmethod
    def _date_list_inclusive(s: str, e: str) -> List[str]:
        d0, d1 = ForecastAnalysisService._parse_yyyy_mm_dd(s), ForecastAnalysisService._parse_yyyy_mm_dd(e)
        return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    @staticmethod
    def _validate_dates_list(d: Optional[List[str]]) -> List[str]:
        if not d: raise ValueError("Dates list cannot be empty.")
        u = sorted({ForecastAnalysisService._parse_yyyy_mm_dd(i).isoformat() for i in d if i})
        if not u: raise ValueError("No valid dates provided.")
        if len(u) > 10: raise ValueError("Cannot request more than 10 dates.")
        return u

    @staticmethod
    def _overall_blocks_stats(blocks_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Overall stats (min, max, avg, std, median, q25, q75) for:
        comp_act_demand, comp_udm, forecast, MAPE (like '0.81%' → 0.81).
        Returns a flat dict keyed by field.
        """
        FIELDS = ("comp_act_demand", "comp_udm", "forecast", "MAPE")

        def to_float(x):
            if x is None:
                return np.nan
            if isinstance(x, (int, float)):
                return float(x)
            s = str(x).strip()
            if not s:
                return np.nan
            if s.endswith("%"):
                s = s[:-1]
            try:
                return float(s)
            except ValueError:
                return np.nan

        def stats(arr):
            a = np.asarray(arr, dtype=float)
            a = a[~np.isnan(a)]
            if a.size == 0:
                return {
                    "min": None, "max": None, "avg": None, "std": None,
                    "median": None, "q25": None, "q75": None, "sum": 0.0
                }
            res = {
                "min":   float(np.min(a)),
                "max":   float(np.max(a)),
                "avg":   float(np.mean(a)),
                "std":   float(np.std(a, ddof=0)),
                "median":float(np.median(a)),
                "q25":   float(np.percentile(a, 25)),
                "q75":   float(np.percentile(a, 75)),
            }
            # round to 2 decimals
            return {k: (None if v is None else round(v, 2)) for k, v in res.items()}

        # iterate over both grouped and flat shapes
        vals = {f: [] for f in FIELDS}
        result = blocks_payload.get("result", [])
        if result:
            grouped = isinstance(result[0], dict) and "blocks" in result[0]
            if grouped:
                for day in result:
                    for blk in day.get("blocks", []):
                        data = blk.get("data", {})
                        for f in FIELDS:
                            vals[f].append(to_float(data.get(f)))
            else:
                for row in result:
                    data = row.get("data", {})
                    for f in FIELDS:
                        vals[f].append(to_float(data.get(f)))

        return {f: stats(vs) for f, vs in vals.items()}

    # ---------- Service Implementations ----------

    async def get_all_graph_data(self, **kwargs) -> Dict[str, Any]:
        eff_markets, eff_zones = self._ensure_markets(kwargs.get("markets")), self._ensure_zones(kwargs.get("zones"))
        date_from, date_to = kwargs.get("date_from") or self._today_ist_str(), kwargs.get("date_to") or kwargs.get("date_from") or self._today_ist_str()
        from_block, to_block = self._clamp_block(kwargs.get("from_block")), self._clamp_block(kwargs.get("to_block"))
        group_by_date, denominator = kwargs.get("group_by_date", True), kwargs.get("denominator", "matched")
        
        dates = self._date_list_inclusive(date_from, date_to)
        
        # Check if any date is within the last 2 days window (Today, Tomorrow, Yesterday, Day Before Yesterday)
        # cutoff = self._today_ist_date() - timedelta(days=2)
        # union_live = any(self._parse_yyyy_mm_dd(d) >= cutoff for d in dates)
        
        today_str, cur_blk = self._today_ist_str(), self._current_block_ist()

        # Demand Blocks
        blocks_docs = await self.repo.fetch_demand_blocks(dates, eff_markets, eff_zones, from_block, to_block)
        if group_by_date:
            by_date = {d:[] for d in dates}
            for doc in blocks_docs: by_date[doc["schedule_date"]].append({"time_block":doc["time_block"],"data":doc["data"]})
            blocks_payload = {"result": [{"schedule_date":d, "blocks":b} for d,b in by_date.items()]}
        else: blocks_payload = {"result": blocks_docs}

        # Demand/MAPE Curves
        expected_total_demand, expected_total_mape = None, None # Calculate expected totals if needed
        demand_curve_payload = await self.repo.fetch_demand_curve(dates, eff_markets, eff_zones, from_block, to_block, today_str, cur_blk, denominator, expected_total_demand)
        mape_curve_payload = await self.repo.fetch_mape_curve(dates, eff_markets, eff_zones, from_block, to_block, today_str, cur_blk, denominator, expected_total_mape)
        
        # Last 7 Days
        last7_payload = await self.get_demand_forecast_last_7_days()

        # ADDED: Calculate overall statistics
        overall_stats = self._overall_blocks_stats(blocks_payload)

        # ADDED: Get Last Updated At
        # Get latest update time from historical demand collection
        max_updated_at = await self.repo.get_max_updated_at(self.repo.demand_historical)  
        # Fallback to current time if None, matching other services logic (or just return what we found)
        # Dashboard logic uses current UTC time as fallback.
        last_updated_at = max_updated_at if max_updated_at else datetime.now(timezone.utc)

        meta = {"markets": eff_markets, "zones": eff_zones, "date_from": date_from, "date_to": date_to, "from_block": from_block, "to_block": to_block, "group_by_date": group_by_date, "denominator": denominator, "expected_total": expected_total_demand}
        return {
            "blocks": blocks_payload,
            "forecast_demand_status": overall_stats, # ADDED
            "demand_curve": demand_curve_payload,
            "mape_curve": mape_curve_payload,
            "last_7_days": last7_payload["result"],
            "meta": meta,
            "last_updated_at": last_updated_at
        }

    async def get_demand_forecast_last_7_days(self) -> Dict[str, Any]:
        start, end = self._today_ist_date() - timedelta(days=7), self._today_ist_date() - timedelta(days=1)
        return {"result": await self.repo.fetch_peak_demand_last_7_days(datetime.combine(start,datetime.min.time()), datetime.combine(end,datetime.max.time()))}

    async def get_selected_days_data(self, dates: List[str], markets: Optional[List[str]], zones: Optional[List[str]], from_block: int, to_block: int, group_by_date: bool) -> Dict[str, Any]:
        eff_dates, eff_markets, eff_zones = self._validate_dates_list(dates), self._ensure_markets(markets), self._ensure_zones(zones)
        b_from, b_to = self._clamp_block(from_block), self._clamp_block(to_block)

        # Check if any date is within the last 2 days window (Today, Tomorrow, Yesterday, Day Before Yesterday)
        # cutoff = self._today_ist_date() - timedelta(days=2)
        # union_live = any(self._parse_yyyy_mm_dd(d) >= cutoff for d in eff_dates)

        # Blocks
        block_docs = await self.repo.fetch_selected_days_blocks(eff_dates, eff_markets, eff_zones, b_from, b_to)
        if group_by_date:
            by_date = {d:[] for d in eff_dates}
            for doc in block_docs: by_date[doc["schedule_date"]].append({"time_block":doc["time_block"],"data":doc["data"]})
            blocks_payload = {"result": [{"schedule_date":d, "blocks":b} for d,b in by_date.items()]}
        else: blocks_payload = {"result": block_docs}

        # Stats
        stats_rows = await self.repo.fetch_selected_days_stats(eff_dates, eff_markets, eff_zones, b_from, b_to)
        present = {r["schedule_date"] for r in stats_rows}
        for d in eff_dates:
            if d not in present: stats_rows.append({"schedule_date":d, "stats":{"comp_act_demand":{"min":None,"max":None,"avg":None,"sum":0.0,"count":0}, "comp_udm":{"min":None,"max":None,"avg":None,"sum":0.0,"count":0}}})
        stats_payload = sorted(stats_rows, key=lambda r: r["schedule_date"])

        return {"blocks": blocks_payload, "stats": stats_payload}
