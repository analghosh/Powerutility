import logging
from datetime import datetime, timedelta, timezone, date
from typing import List, Optional, Dict, Any, Literal
import numpy as np 

from ..repository.serve_demand_table_repository import DashboardRepository

logger = logging.getLogger(__name__)


class DemandForecastAnalysisServices:
    """
    Demand blocks (per-day window, grouped by date or flat).
    - Default markets: ["IDF"] (falls back to `type` if `market` missing)
    - Default zones:   ["PUDUCHERRY"] (case-insensitive match on `zone` field)
    - Date range: inclusive [date_from .. date_to], defaults to today (IST)
    - Block window: per day (1..96), defaults 1..96
    - MAPE: average of parsed % values per block; fallback computed from (forecast vs comp_act_demand)
    """

    IST = timezone(timedelta(hours=5, minutes=30))

    def __init__(self, collection_name: str = "serve_demand_table_historical"):
        self.repo = DashboardRepository(collection_name=collection_name)
        self.repo_live = DashboardRepository(collection_name="serve_demand_table")
        

    # ---------- helpers ----------
    @classmethod
    def _today_ist_str(cls) -> str:
        return datetime.now(cls.IST).date().isoformat()
    
    @classmethod
    def _today_ist_date(cls) -> date:
        return datetime.now(cls.IST).date()
    
    def _date_includes_today(self, start_date_str: str, end_date_str: str) -> bool:
        today = self._today_ist_date()
        d0 = self._parse_yyyy_mm_dd(start_date_str)
        d1 = self._parse_yyyy_mm_dd(end_date_str)
        return d0 <= today <= d1

    def _current_block_ist(self) -> int:
        now = datetime.now(self.IST).time()
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
        import numpy as np

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
                # "sum": float(np.sum(a)),  # keep commented if you don't want it
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


    # ---------- core ----------
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
        eff_markets = self._ensure_markets(markets)
        eff_zones   = self._ensure_zones(zones)  # default ["PUDUCHERRY"]
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
        # Mask future blocks when exactly one market is selected and it is IDF or DAF
        _mask_future_markets = {"IDF", "DAF"}
        only_mask_market = (len(eff_markets) == 1 and eff_markets[0] in _mask_future_markets)

        today_str = self._today_ist_date().isoformat()
        cur_blk = self._current_block_ist()

        # Normalize fields: schedule_date -> string date, market/type, and zone -> UPPER
        normalize_stage = {
            "$addFields": {
                "_sdate": {
                    "$let": {
                        "vars": {"t": {"$type": "$schedule_date"}},
                        "in": {
                            "$trim": {
                                "input": {
                                    "$cond": [
                                        {"$eq": ["$$t", "date"]},
                                        {
                                            "$dateToString": {
                                                "date": "$schedule_date",
                                                "format": "%Y-%m-%d",
                                                "timezone": "Asia/Kolkata",
                                            }
                                        },
                                        {"$toString": "$schedule_date"},
                                    ]
                                }
                            }
                        }
                    }
                },
                "_market_upper": {"$toUpper": {"$ifNull": ["$market", "$type"]}},
                "_zone_upper":   {"$toUpper": {"$ifNull": ["$zone",  ""]}},
            }
        }

        match_and = [
            {"$in":  ["$_market_upper", eff_markets]},
            {"$in":  ["$_zone_upper",   eff_zones]},   # zone filter with default
            {"$gte": ["$_sdate", start_date_str]},
            {"$lte": ["$_sdate", end_date_str]},
            {"$gte": ["$time_block", b_from]},
            {"$lte": ["$time_block", b_to]},
        ]

        today_str = self._today_ist_date().isoformat()
        match_and.append({"$lte": ["$_sdate", today_str]})


        core = [
            normalize_stage,
            {"$match": {"$expr": {"$and": match_and}}},
        ]

        # NEW: if range includes today, union today’s rows from the live table
        if self._date_includes_today(start_date_str, end_date_str):
            # reuse the same normalize + match filters for the live collection
            core.append({
                "$unionWith": {
                    "coll": "serve_demand_table",
                    "pipeline": [
                        normalize_stage,
                        {"$match": {"$expr": {"$and": match_and}}},
                    ],
                }
            })

        # continue with your existing stages unchanged
        core += [
            {
                "$addFields": {
                    "_comp_act_demand": {"$ifNull": ["$comp_act_demand", 0]},
                    "_comp_udm":        {"$ifNull": ["$comp_udm", 0]},
                    "_forecast":        {"$ifNull": ["$forecast", 0]},
                    "_mape_raw":        {"$ifNull": ["$MAPE", ""]},
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
                    "_id": {"schedule_date": "$_sdate", "time_block": "$time_block"},
                    "comp_act_demand": {"$sum": "$_comp_act_demand"},
                    "comp_udm":        {"$sum": "$_comp_udm"},
                    "forecast":        {"$sum": "$_forecast"},
                    "mape_num_sum":    {"$sum": {"$ifNull": ["$_mape_num", 0]}},
                    "mape_cnt":        {"$sum": "$_mape_has"},
                }
            },
            {
                "$addFields": {
                    "mape_avg": {
                        "$cond": [
                            {"$gt": ["$mape_cnt", 0]},
                            {"$divide": ["$mape_num_sum", "$mape_cnt"]},
                            {
                                "$cond": [
                                    {"$gt": ["$comp_act_demand", 0]},
                                    {
                                        "$multiply": [
                                            {
                                                "$divide": [
                                                    {"$abs": {"$subtract": ["$forecast", "$comp_act_demand"]}},
                                                    "$comp_act_demand",
                                                ]
                                            },
                                            100,
                                        ]
                                    },
                                    0,
                                ]
                            },
                        ]
                    }
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "schedule_date": "$_id.schedule_date",
                    "time_block": "$_id.time_block",
                    "data": {
                    "comp_act_demand": {
                        "$cond": [
                        {
                            "$and": [
                            {"$literal": include_today},
                            {"$literal": only_mask_market},
                            {"$eq": ["$_id.schedule_date", today_str]},
                            {"$gt": ["$_id.time_block", cur_blk]}
                            ]
                        },
                        {"$literal": None},
                        {"$round": ["$comp_act_demand", 2]}
                        ]
                    },
                    "comp_udm": {
                        "$cond": [
                        {
                            "$and": [
                            {"$literal": include_today},
                            {"$literal": only_mask_market},
                            {"$eq": ["$_id.schedule_date", today_str]},
                            {"$gt": ["$_id.time_block", cur_blk]}
                            ]
                        },
                        {"$literal": None},
                        {"$round": ["$comp_udm", 2]}
                        ]
                    },
                    
                    "forecast": {"$round": ["$forecast", 2]},
                    "MAPE": {
                        "$cond": [
                        {
                            "$and": [
                            {"$literal": include_today},
                            {"$literal": only_mask_market},
                            {"$eq": ["$_id.schedule_date", today_str]},
                            {"$gt": ["$_id.time_block", cur_blk]}
                            ]
                        },
                        "",  # keep string type; hidden in future
                        {
                            "$concat": [
                            {"$toString": {"$round": ["$mape_avg", 2]}},
                            "%"
                            ]
                        }
                        ]
                    }
                    }
                }
            },

            {"$sort": {"schedule_date": 1, "time_block": 1}},
        ]


        docs = await self.repo.aggregate(core)

        # reshape
        wanted_days = self._date_list_inclusive(start_date_str, end_date_str)
        by_date: Dict[str, List[Dict[str, Any]]] = {}
        for doc in docs:
            by_date.setdefault(doc["schedule_date"], []).append(
                {"time_block": doc["time_block"], "data": doc["data"]}
            )

        if group_by_date:
            result = [{"schedule_date": d, "blocks": by_date.get(d, [])} for d in wanted_days]
            return {"result": result}

        # flat
        flat: List[Dict[str, Any]] = []
        for d in wanted_days:
            for row in by_date.get(d, []):
                flat.append({"schedule_date": d, **row})
        return {"result": flat}


    # ---------- new: demand curve (rank by comp_act_demand) ----------
    async def demand_curve(
        self,
        markets: Optional[List[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        from_block: Optional[int] = None,   # per-day
        to_block: Optional[int] = None,     # per-day
        denominator: str = "matched",       # "matched" | "expected"
        zones: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Rank selected (date, block) rows by comp_act_demand ASC, returning:
          - count (1..N)
          - percentage:
              matched  -> count / totalMatchedRows * 100
              expected -> count / (days * slotsPerDay) * 100
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

        days_inclusive = (d1 - d0).days + 1
        slots_per_day = (b_to - b_from + 1)
        expected_total = days_inclusive * slots_per_day

        normalize_stage = {
            "$addFields": {
                "_sdate": {
                    "$let": {
                        "vars": {"t": {"$type": "$schedule_date"}},
                        "in": {
                            "$trim": {
                                "input": {
                                    "$cond": [
                                        {"$eq": ["$$t", "date"]},
                                        {
                                            "$dateToString": {
                                                "date": "$schedule_date",
                                                "format": "%Y-%m-%d",
                                                "timezone": "Asia/Kolkata",
                                            }
                                        },
                                        {"$toString": "$schedule_date"},
                                    ]
                                }
                            }
                        }
                    }
                },
                "_market_upper": {"$toUpper": {"$ifNull": ["$market", "$type"]}},
                "_zone_upper":   {"$toUpper": {"$ifNull": ["$zone",  ""]}},
                "_cad":          {"$toDouble": {"$ifNull": ["$comp_act_demand", 0]}},
            }
        }
        include_today = self._date_includes_today(start_date_str, end_date_str)
        only_idf = (eff_markets == ["IDF"])
        cur_blk = self._current_block_ist()
        today_str = self._today_ist_date().isoformat()

        match_and = [
            {"$in":  ["$_market_upper", eff_markets]},
            {"$in":  ["$_zone_upper",   eff_zones]},
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
                expected_total = ((d1 - d0).days) * slots_per_day + today_slots
            else:
                expected_total = ((d1 - d0).days + 1) * slots_per_day

        match_stage = {"$match": {"$expr": {"$and": match_and}}}

        group_stage = {
            "$group": {
                "_id": {"schedule_date": "$_sdate", "time_block": "$time_block"},
                "comp_act_demand": {"$sum": "$_cad"},
            }
        }

        sort_stage = {
            "$sort": {
                "comp_act_demand": 1,
                "_id.schedule_date": 1,
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
                # 👇 THIS is the required piece you were missing
                "sortBy": {
                    "comp_act_demand": 1,
                    "_id.schedule_date": 1,
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

        # pipeline = [
        #     normalize_stage,
        #     match_stage,
        #     group_stage,
        #     sort_stage,
        #     window_stage,
        #     project_stage,
        # ]

        pipeline = [
            normalize_stage,
            match_stage,
        ]

        if self._date_includes_today(start_date_str, end_date_str):
            pipeline.append({
                "$unionWith": {
                    "coll": "serve_demand_table",
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
        rows = await self.repo.aggregate(pipeline)

        return {
            "result": rows,
            "meta": {
                "markets": eff_markets,
                "zones": eff_zones,
                "date_from": start_date_str,
                "date_to": end_date_str,
                "from_block": b_from,
                "to_block": b_to,
                "denominator": denominator,
                "expected_total": expected_total if denominator == "expected" else None,
            },
        }


    async def mape_curve(
        self,
        markets: Optional[List[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        from_block: Optional[int] = None,   # per-day
        to_block: Optional[int] = None,     # per-day
        denominator: str = "matched",       # "matched" | "expected"
        zones: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Rank selected (date, block) rows by average MAPE ASC, returning:
          - count (1..N)           using $documentNumber
          - MAPE (string, e.g. "6.12%")
          - percentage:
              matched  -> count / totalMatchedRows * 100
              expected -> count / (days * slotsPerDay) * 100
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

        days_inclusive = (d1 - d0).days + 1
        slots_per_day = (b_to - b_from + 1)
        expected_total = days_inclusive * slots_per_day

        # Normalize schedule_date and fields; parse MAPE string safely
        normalize_stage = {
            "$addFields": {
                "_sdate": {
                    "$let": {
                        "vars": {"t": {"$type": "$schedule_date"}},
                        "in": {
                            "$trim": {
                                "input": {
                                    "$cond": [
                                        {"$eq": ["$$t", "date"]},
                                        {
                                            "$dateToString": {
                                                "date": "$schedule_date",
                                                "format": "%Y-%m-%d",
                                                "timezone": "Asia/Kolkata",
                                            }
                                        },
                                        {"$toString": "$schedule_date"},
                                    ]
                                }
                            }
                        }
                    }
                },
                "_market_upper": {"$toUpper": {"$ifNull": ["$market", "$type"]}},
                "_zone_upper":   {"$toUpper": {"$ifNull": ["$zone",  ""]}},
                "_mape_str":     {"$toString": {"$ifNull": ["$MAPE", ""]}},
            }
        }
        include_today = self._date_includes_today(start_date_str, end_date_str)
        only_idf = (eff_markets == ["IDF"])
        cur_blk = self._current_block_ist()
        today_str = self._today_ist_date().isoformat()

        match_and = [
            {"$in":  ["$_market_upper", eff_markets]},
            {"$in":  ["$_zone_upper",   eff_zones]},
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
                expected_total = ((d1 - d0).days) * slots_per_day + today_slots
            else:
                expected_total = ((d1 - d0).days + 1) * slots_per_day
        
        match_stage = {"$match": {"$expr": {"$and": match_and}}}

        # Parse MAPE "%"-string -> numeric; mark presence
        parse_mape_stage = {
            "$addFields": {
                "_mape_num": {
                    "$cond": [
                        {"$or": [
                            {"$eq": ["$_mape_str", ""]},
                            {"$eq": ["$_mape_str", "null"]},
                        ]},
                        0,
                        {"$toDouble": {
                            "$replaceAll": {
                                "input": "$_mape_str",
                                "find": "%",
                                "replacement": ""
                            }
                        }}
                    ]
                },
                "_mape_has": {
                    "$cond": [
                        {"$or": [
                            {"$eq": ["$_mape_str", ""]},
                            {"$eq": ["$_mape_str", "null"]},
                        ]},
                        0,
                        1
                    ]
                }
            }
        }

        # Collapse duplicates per (date, block) and compute avg MAPE
        group_stage = {
            "$group": {
                "_id": {"schedule_date": "$_sdate", "time_block": "$time_block"},
                "mape_num_sum": {"$sum": "$_mape_num"},
                "mape_cnt":     {"$sum": "$_mape_has"},
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
                "_id.schedule_date": 1,
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

        # pipeline = [
        #     normalize_stage,
        #     match_stage,
        #     parse_mape_stage,
        #     group_stage,
        #     finalize_avg_stage,
        #     sort_stage,
        #     window_stage,
        #     project_stage,
        # ]

        pipeline = [
            normalize_stage,
            match_stage,
        ]

        if self._date_includes_today(start_date_str, end_date_str):
            pipeline.append({
                "$unionWith": {
                    "coll": "serve_demand_table",
                    "pipeline": [
                        normalize_stage,
                        match_stage,
                    ],
                }
            })

        pipeline += [
            parse_mape_stage,
            group_stage,
            finalize_avg_stage,
            sort_stage,
            window_stage,
            project_stage,
        ]


        logger.debug("Executing mape_curve pipeline: %s", pipeline)
        rows = await self.repo.aggregate(pipeline)

        return {
            "result": rows,
            "meta": {
                "markets": eff_markets,
                "zones": eff_zones,
                "date_from": start_date_str,
                "date_to": end_date_str,
                "from_block": b_from,
                "to_block": b_to,
                "denominator": denominator,
                "expected_total": expected_total if denominator == "expected" else None,
            },
        }


    async def all_three_unified(
        self,
        *,
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
        Run ALL THREE analyses with the SAME filters + SAME denominator for both curves.
        Returns a single meta.
        """
        # Normalize effective filters exactly once (to expose in meta deterministically)
        eff_markets = self._ensure_markets(markets)
        eff_zones   = self._ensure_zones(zones)

        # Default to yesterday (IST) when not provided
        if date_from:
            start_date_str = date_from
        else:
            start_date_str = self._today_ist_date().isoformat()

        end_date_str = date_to or start_date_str
        
       
        # Validate/normalize dates and blocks via helpers (reuse logic from other methods)
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

        slots_per_day = (b_to - b_from + 1)
        expected_total = None
        if denominator == "expected":
            include_today = self._date_includes_today(start_date_str, end_date_str)
            if include_today:
                today_slots = max(0, min(b_to, self._current_block_ist()) - b_from + 1)
                expected_total = ((d1 - d0).days) * slots_per_day + today_slots  # full past days + partial today
            else:
                expected_total = ((d1 - d0).days + 1) * slots_per_day

        # Execute all three using the SAME filters and SAME denominator
        blocks_payload = await self.demand_blocks(
            markets=eff_markets,
            date_from=start_date_str,
            date_to=end_date_str,
            from_block=b_from,
            to_block=b_to,
            group_by_date=group_by_date,
            zones=eff_zones,
        )

        demand_curve_payload = await self.demand_curve(
            markets=eff_markets,
            date_from=start_date_str,
            date_to=end_date_str,
            from_block=b_from,
            to_block=b_to,
            denominator=denominator,
            zones=eff_zones,
        )

        mape_curve_payload = await self.mape_curve(
            markets=eff_markets,
            date_from=start_date_str,
            date_to=end_date_str,
            from_block=b_from,
            to_block=b_to,
            denominator=denominator,
            zones=eff_zones,
        )

        # fetch last 7 days from precomputed table
        last7_payload = await self.demand_forecast_last_7_days()

        # Forecast Demand Status Table Statistics
        overall_stats = self._overall_blocks_stats(blocks_payload)
        # per_date_stats = self._per_date_blocks_stats(blocks_payload)
       
        # Strip per-endpoint metas; provide ONE unified meta

        return {
        "blocks": {
            "result": blocks_payload["result"],
            "forecast_demand_status": overall_stats, 
            # "date_wise_status": per_date_stats,
            },   # wrap to match DemandDataGrouped/Flat ✅
        "demand_curve": demand_curve_payload["result"],
        "mape_curve": mape_curve_payload["result"],
        "last_7_days": last7_payload["result"],
        "meta": {
            "markets": eff_markets,
            "zones": eff_zones,
            "date_from": start_date_str,
            "date_to": end_date_str,
            "from_block": b_from,
            "to_block": b_to,
            "group_by_date": group_by_date,
            "denominator": denominator,
            "expected_total": expected_total,
        },
    }


    async def demand_forecast_last_7_days(self) -> Dict[str, Any]:
        """
        Read from the precomputed table `peak_demand_31_days`
        and return the last 7 IST dates (today back 6 days), inclusive.
        Each row contains: schedule_date, time_block, peak_demand, average_demand.
        """
        # work with the precomputed collection
        repo = DashboardRepository(collection_name="peak_demand_31_days")

        # compute date window in IST: today .. today-6
        today_ist = datetime.now(self.IST).date()
        start_ist = today_ist - timedelta(days=7)
        start_str = start_ist.isoformat()
        end_ist = today_ist - timedelta(days=1)
        end_str = end_ist.isoformat()

        pipeline: List[Dict[str, Any]] = [
            {"$match": {"schedule_date": {"$gte": start_str, "$lte": end_str}}},
            {"$project": {
                "_id": 0,
                "schedule_date": 1,
                # "time_block": 1,
                "peak_demand":    {"$round": ["$peak_demand", 2]},
                "average_demand": {"$round": ["$average_demand", 2]},
            }},
            {"$sort": {"schedule_date": 1}}  # chronological
        ]

        logger.debug("Executing demand_forecast_last_7_days pipeline: %s", pipeline)
        rows = await repo.aggregate(pipeline)
        return {"result": rows}



        # ---------- new: selected days (max 10) ----------
    def _validate_dates_list(self, dates: Optional[List[str]]) -> List[str]:
        """
        Ensure we have 1..10 unique YYYY-MM-DD dates, normalized and sorted.
        """
        if not dates:
            raise ValueError("dates must be a non-empty list of YYYY-MM-DD strings (max 10)")
        uniq = []
        seen = set()
        for s in dates:
            if not s:
                continue
            try:
                d = self._parse_yyyy_mm_dd(str(s).strip())
            except ValueError:
                raise ValueError(f"Invalid date format: {s!r}; expected YYYY-MM-DD")
            iso = d.isoformat()
            if iso not in seen:
                seen.add(iso)
                uniq.append(iso)
        if not uniq:
            raise ValueError("No valid dates provided")
        if len(uniq) > 10:
            raise ValueError("You can request at most 10 dates at a time")
        return sorted(uniq)

    async def demand_for_selected_days(
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
        Return rows for specific dates (max 10), per selected block window.
        Each block returns BOTH comp_act_demand and comp_udm.

        Shapes:
        - grouped: {"result":[{"schedule_date":"YYYY-MM-DD","blocks":[
                        {"time_block":n,"data":{"comp_act_demand":x,"comp_udm":y}}, ...
                    ]}, ...]}
        - flat:    {"result":[
                        {"schedule_date":"YYYY-MM-DD","time_block":n,"data":{"comp_act_demand":x,"comp_udm":y}}, ...
                    ]}
        """
        eff_dates   = self._validate_dates_list(dates)
        eff_markets = self._ensure_markets(markets)
        eff_zones   = self._ensure_zones(zones)

        b_from = self._clamp_block(from_block) or 1
        b_to   = self._clamp_block(to_block) or 96
        if b_from > b_to:
            b_from, b_to = b_to, b_from

        normalize_stage = {
            "$addFields": {
                "_sdate": {
                    "$let": {
                        "vars": {"t": {"$type": "$schedule_date"}},
                        "in": {
                            "$trim": {
                                "input": {
                                    "$cond": [
                                        {"$eq": ["$$t", "date"]},
                                        {
                                            "$dateToString": {
                                                "date": "$schedule_date",
                                                "format": "%Y-%m-%d",
                                                "timezone": "Asia/Kolkata",
                                            }
                                        },
                                        {"$toString": "$schedule_date"},
                                    ]
                                }
                            }
                        }
                    }
                },
                "_market_upper": {"$toUpper": {"$ifNull": ["$market", "$type"]}},
                "_zone_upper":   {"$toUpper": {"$ifNull": ["$zone",  ""]}},
                "_cad":          {"$toDouble": {"$ifNull": ["$comp_act_demand", 0]}},
                "_udm":          {"$toDouble": {"$ifNull": ["$comp_udm", 0]}},
            }
        }

        today_str = self._today_ist_date().isoformat()
        match_stage = {
            "$match": {
                "$expr": {
                    "$and": [
                        {"$in":  ["$_sdate", eff_dates]},
                        {"$in":  ["$_market_upper", eff_markets]},
                        {"$in":  ["$_zone_upper",   eff_zones]},
                        {"$gte": ["$time_block", b_from]},
                        {"$lte": ["$time_block", b_to]},
                        {"$lte": ["$_sdate", today_str]},
                    ]
                }
            }
        }

        # Collapse per (date, block) in case multiple raw docs exist
        group_stage = {
            "$group": {
                "_id": {"schedule_date": "$_sdate", "time_block": "$time_block"},
                "comp_act_demand": {"$sum": "$_cad"},
                "comp_udm":        {"$sum": "$_udm"},
            }
        }

        project_stage = {
            "$project": {
                "_id": 0,
                "schedule_date": "$_id.schedule_date",
                "time_block": "$_id.time_block",
                "data": {
                    "comp_act_demand": {"$round": ["$comp_act_demand", 2]},
                    "comp_udm":        {"$round": ["$comp_udm", 2]},
                },
            }
        }

        pipeline = [
            normalize_stage,
            match_stage,
            group_stage,
            {"$sort": {"schedule_date": 1, "time_block": 1}},
            project_stage,
        ]

        logger.debug("Executing demand_for_selected_days pipeline: %s", pipeline)
        rows = await self.repo.aggregate(pipeline)

        # reshape to match grouped/flat shapes
        by_date: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            by_date.setdefault(r["schedule_date"], []).append(
                {"time_block": r["time_block"], "data": r["data"]}
            )

        if group_by_date:
            result = [{"schedule_date": d, "blocks": by_date.get(d, [])} for d in eff_dates]
            return {"result": result}

        flat: List[Dict[str, Any]] = []
        for d in eff_dates:
            for row in by_date.get(d, []):
                flat.append({"schedule_date": d, **row})
        return {"result": flat}

    async def comp_act_demand_stats_by_date(
        self,
        *,
        dates: List[str],
        markets: Optional[List[str]] = None,
        zones: Optional[List[str]] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        For each selected date (max 10), compute stats for BOTH fields across the selected blocks:
        stats.comp_act_demand => {min,max,avg,sum,count}
        stats.comp_udm        => {min,max,avg,sum,count}

        Returns:
        {
            "result": [
            {
                "schedule_date": "YYYY-MM-DD",
                "stats": {
                "comp_act_demand": {"min":..,"max":..,"avg":..,"sum":..,"count":..},
                "comp_udm":       {"min":..,"max":..,"avg":..,"sum":..,"count":..}
                }
            }, ...
            ]
        }
        """
        eff_dates   = self._validate_dates_list(dates)
        eff_markets = self._ensure_markets(markets)
        eff_zones   = self._ensure_zones(zones)

        b_from = self._clamp_block(from_block) or 1
        b_to   = self._clamp_block(to_block) or 96
        if b_from > b_to:
            b_from, b_to = b_to, b_from

        normalize_stage = {
            "$addFields": {
                "_sdate": {
                    "$let": {
                        "vars": {"t": {"$type": "$schedule_date"}},
                        "in": {
                            "$trim": {
                                "input": {
                                    "$cond": [
                                        {"$eq": ["$$t", "date"]},
                                        {
                                            "$dateToString": {
                                                "date": "$schedule_date",
                                                "format": "%Y-%m-%d",
                                                "timezone": "Asia/Kolkata",
                                            }
                                        },
                                        {"$toString": "$schedule_date"},
                                    ]
                                }
                            }
                        }
                    }
                },
                "_market_upper": {"$toUpper": {"$ifNull": ["$market", "$type"]}},
                "_zone_upper":   {"$toUpper": {"$ifNull": ["$zone",  ""]}},
                "_cad":          {"$toDouble": {"$ifNull": ["$comp_act_demand", 0]}},
                "_udm":          {"$toDouble": {"$ifNull": ["$comp_udm", 0]}},
            }
        }

        today_str = self._today_ist_date().isoformat()
        match_stage = {
            "$match": {
                "$expr": {
                    "$and": [
                        {"$in":  ["$_sdate", eff_dates]},
                        {"$in":  ["$_market_upper", eff_markets]},
                        {"$in":  ["$_zone_upper",   eff_zones]},
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
                "_id": {"schedule_date": "$_sdate", "time_block": "$time_block"},
                "cad": {"$sum": "$_cad"},
                "udm": {"$sum": "$_udm"},
            }
        }

        # 2) Compute per-date stats for both fields
        group_date = {
            "$group": {
                "_id": "$_id.schedule_date",

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
            {"$sort": {"schedule_date": 1}},
            project_stage,
        ]

        logger.debug("Executing comp_act_demand_stats_by_date pipeline: %s", pipeline)
        rows = await self.repo.aggregate(pipeline)

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
        rows = sorted(rows, key=lambda r: r["schedule_date"])

        return {"result": rows}

        

        
        

# Global instance
demand_forecast_analysis_services = DemandForecastAnalysisServices()
