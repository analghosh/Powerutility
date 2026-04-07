"""
Graph service layer using repository pattern.
Handles business logic for graph-related data operations.
"""

import logging
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime, timedelta, timezone, date
import numpy as np

from ..repository.graph_repository import graph_repository
from ...core.config import settings

logger = logging.getLogger(__name__)

# Time utilities
IST = timezone(timedelta(hours=5, minutes=30))


class GraphService:
    """
    Service layer for Graph-related operations.
    Handles business logic and data processing for graph visualization.
    """

    def __init__(self):
        self.graph_repo = graph_repository
        # Collection names from settings
        self.demand_serve = settings.MONGODB_DEMAND_SERVE_COLLECTION
        self.demand_serve_historical = settings.MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION
        self.peak_demand_31_days = "peak_demand_31_days"
        self.peak_demand_serve = "peak_demand_serve"

    # ---------- Helper methods ----------
    @classmethod
    def _today_ist_str(cls) -> str:
        return datetime.now(IST).date().isoformat()
    
    @classmethod
    def _today_ist_date(cls) -> date:
        return datetime.now(IST).date()
    
    def _date_includes_today(self, start_date_str: str, end_date_str: str) -> bool:
        today = self._today_ist_date()
        d0 = self._parse_yyyy_mm_dd(start_date_str)
        d1 = self._parse_yyyy_mm_dd(end_date_str)
        return d0 <= today <= d1

    def _current_block_ist(self) -> int:
        now = datetime.now(IST).time()
        return (now.hour * 4) + (now.minute // 15) + 1

    @staticmethod
    def _ensure_markets(markets: Optional[List[str]]) -> List[str]:
        if not markets:
            return ["IDF"]
        m = sorted({(s or "").strip().upper() for s in markets if (s or "").strip()})
        return m or ["IDF"]

    @staticmethod
    def _ensure_zones(zones: Optional[List[str]]) -> List[str]:
        """
        Default to ["PUDUCHERRY"].
        """
        if not zones:
            return ["PUDUCHERRY"]
        z = sorted({(s or "").strip().upper() for s in zones if (s or "").strip()})
        return z or ["PUDUCHERRY"]

    @staticmethod
    def _parse_yyyy_mm_dd(val: str) -> date:
        return datetime.strptime(val, "%Y-%m-%d").date()

    @staticmethod
    def _clamp_block(n: Optional[int]) -> Optional[int]:
        if n is None:
            return None
        n = int(n)
        return 1 if n < 1 else 96 if n > 96 else n

    @staticmethod
    def _date_list_inclusive(start_yyyy_mm_dd: str, end_yyyy_mm_dd: str) -> List[str]:
        y1, m1, d1 = map(int, start_yyyy_mm_dd.split("-"))
        y2, m2, d2 = map(int, end_yyyy_mm_dd.split("-"))
        d0 = date(y1, m1, d1)
        d1_ = date(y2, m2, d2)
        out: List[str] = []
        cur = d0
        while cur <= d1_:
            out.append(cur.isoformat())
            cur = cur + timedelta(days=1)
        return out

    @staticmethod
    def _overall_blocks_stats(blocks_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Overall stats (min, max, avg, std, median, q25, q75) for:
        comp_act_demand, comp_udm, forecast, MAPE (like '0.81%' → 0.81).
        Returns a flat dict keyed by field.
        """
        FIELDS = ("comp_act_demand", "comp_udm", "forecast", "mape")

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

    # ---------- Core methods ----------
    async def demand_blocks(
        self,
        markets: Optional[List[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        from_block: Optional[int] = None,  # per-day
        to_block: Optional[int] = None,    # per-day
        group_by_date: bool = True,
        zones: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Get demand blocks data for graph visualization.
        """
        eff_markets = self._ensure_markets(markets)
        eff_zones   = self._ensure_zones(zones)
        start_date_str = date_from or self._today_ist_str()
        end_date_str   = date_to or start_date_str

        try:
            d0 = self._parse_yyyy_mm_dd(start_date_str)
            d1 = self._parse_yyyy_mm_dd(end_date_str)
        except ValueError:
            raise ValueError("date_from/date_to must be in YYYY-MM-DD format")

        if d1 < d0:
            d0, d1 = d1, d0
            start_date_str, end_date_str = d0.isoformat(), d1.isoformat()

        b_from = self._clamp_block(from_block) or 1
        b_to = self._clamp_block(to_block) or 96
        if b_from > b_to:
            b_from, b_to = b_to, b_from

        include_today = self._date_includes_today(start_date_str, end_date_str)
        _mask_future_markets = {"IDF", "DAF"}
        only_mask_market = (len(eff_markets) == 1 and eff_markets[0] in _mask_future_markets)

        today_str = self._today_ist_date().isoformat()
        cur_blk = self._current_block_ist()

        # Convert string dates to Date objects for comparison
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

        # Get demand blocks data using repository
        result = await self.graph_repo.get_demand_blocks_pipeline(
            collection_name=self.demand_serve_historical,
            markets=eff_markets,
            zones=eff_zones,
            start_date=start_date,
            end_date=end_date,
            b_from=b_from,
            b_to=b_to,
            include_today=include_today,
            only_mask_market=only_mask_market,
            today_str=today_str,
            cur_blk=cur_blk,
            group_by_date=group_by_date
        )

        return {"result": result}


    async def all_three_unified(
        self,
        markets: Optional[List[str]] = None,
        zones: Optional[List[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
        group_by_date: bool = True,
        denominator: Literal["matched", "expected"] = "matched",
    ) -> Dict[str, Any]:
        """
        Get unified data combining blocks, demand curve, and MAPE curve.
        """
        # Get blocks data
        blocks_payload = await self.demand_blocks(
            markets=markets,
            zones=zones,
            date_from=date_from,
            date_to=date_to,
            from_block=from_block,
            to_block=to_block,
            group_by_date=group_by_date,
        )

        # Add stats to blocks
        stats = self._overall_blocks_stats(blocks_payload)
        blocks_payload["forecast_demand_status"] = stats

        # Create meta and get variables for repository calls
        eff_markets = self._ensure_markets(markets)
        eff_zones = self._ensure_zones(zones)
        start_date_str = date_from or self._today_ist_str()
        end_date_str = date_to or start_date_str
        b_from = self._clamp_block(from_block) or 1
        b_to = self._clamp_block(to_block) or 96

        # Convert string dates to Date objects for repository calls
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()

        # Get curve data using repository
        demand_curve_result = await self.graph_repo.get_demand_curve_pipeline(
            collection_name=self.demand_serve_historical,
            markets=eff_markets,
            zones=eff_zones,
            start_date=start_date,
            end_date=end_date,
            b_from=b_from,
            b_to=b_to,
            denominator=denominator
        )
        demand_curve = {"result": demand_curve_result}

        mape_curve_result = await self.graph_repo.get_mape_curve_pipeline(
            collection_name=self.demand_serve_historical,
            markets=eff_markets,
            zones=eff_zones,
            start_date=start_date,
            end_date=end_date,
            b_from=b_from,
            b_to=b_to,
            denominator=denominator
        )
        mape_curve = {"result": mape_curve_result}

        # Get last 7 days data using repository
        last_7_days_result = await self.graph_repo.get_last_7_days_pipeline(
            collection_name=self.peak_demand_serve,
            markets=eff_markets,
            zones=eff_zones,
            b_from=b_from,
            b_to=b_to
        )
        last_7_days = {"result": last_7_days_result}

        meta = {
            "markets": eff_markets,
            "zones": eff_zones,
            "date_from": start_date_str,
            "date_to": end_date_str,
            "from_block": b_from,
            "to_block": b_to,
            "group_by_date": group_by_date,
            "denominator": denominator,
        }

        return {
            "blocks": blocks_payload,
            "demand_curve": demand_curve.get("result", []),
            "mape_curve": mape_curve.get("result", []),
            "last_7_days": last_7_days.get("result", []),
            "meta": meta,
        }


    async def selected_days_blocks_and_stats(
        self,
        *,
        dates: List[str],
        markets: Optional[List[str]] = None,
        zones: Optional[List[str]] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
        group_by_date: bool = True,
    ) -> Dict[str, Any]:
        """
        Get selected days blocks and statistics data.
        """
        # Get blocks data for selected dates
        blocks_payload = await self.demand_blocks(
            markets=markets,
            zones=zones,
            date_from=dates[0] if dates else None,
            date_to=dates[-1] if dates else None,
            from_block=from_block,
            to_block=to_block,
            group_by_date=group_by_date,
        )

        # Filter to only selected dates
        if group_by_date:
            filtered_blocks = []
            for day_data in blocks_payload.get("result", []):
                if day_data["schedule_date"] in dates:
                    filtered_blocks.append(day_data)
            blocks_payload["result"] = filtered_blocks
        else:
            filtered_blocks = []
            for row in blocks_payload.get("result", []):
                if row["schedule_date"] in dates:
                    filtered_blocks.append(row)
            blocks_payload["result"] = filtered_blocks

        # Get stats for selected dates
        stats_payload = await self._get_selected_days_stats(
            dates=dates,
            markets=markets,
            zones=zones,
            from_block=from_block,
            to_block=to_block,
        )

        return {
            "blocks": blocks_payload,
            "stats": stats_payload,
        }

    async def _get_selected_days_stats(
        self,
        dates: List[str],
        markets: Optional[List[str]] = None,
        zones: Optional[List[str]] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get statistics for selected days.
        """
        eff_markets = self._ensure_markets(markets)
        eff_zones = self._ensure_zones(zones)
        b_from = self._clamp_block(from_block) or 1
        b_to = self._clamp_block(to_block) or 96

        # Get selected days stats using repository
        result = await self.graph_repo.get_selected_days_stats_pipeline(
            collection_name=self.demand_serve_historical,
            dates=dates,
            markets=eff_markets,
            zones=eff_zones,
            b_from=b_from,
            b_to=b_to
        )
        return result


# Create service instance
graph_service = GraphService()
