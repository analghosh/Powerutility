# app/services/management_snapshot_services.py
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

from ..repository.serve_demand_table_repository import DashboardRepository

logger = logging.getLogger(__name__)


class ManagementSnapshotServices:
    """
    Build a per-day, per-time_block snapshot for DAM/RTM from `serve_exchange_table`.

    Output:
    {
      "schedule_date": "YYYY-MM-DD",
      "data": [
        { "time_block": 1, "dam_mcv": <float|null>, "rtm_mcv": <float|null>,
          "dam_mcp": <float|null>, "rtm_mcp": <float|null> },
        ...
      ]
    }

    Defaults:
      - schedule_date: today (IST)
      - blocks: 1..96
    Behavior:
      - Accepts schedule_date stored as string ("YYYY-MM-DD") or Date.
      - Safely parses numeric-like strings for mcv/mcp (handles "", "-", "NA", "null", commas, %).
      - Pivots markets ∈ {"DAM","RTM"} into dam_*/rtm_* per time_block.
      - Gap-fills missing blocks in the requested range with nulls.
    """

    IST = timezone(timedelta(hours=5, minutes=30))

    def __init__(
            self, 
            collection_name: str = "serve_exchange_table",  
            supply_collection: str = "serve_supply_table",
            demand_collection: str = "serve_demand_table",
            trade_collection: str = "serve_px_bid_trade",):
        
        self.repo = DashboardRepository(collection_name=collection_name)
        self.repo_supply = DashboardRepository(collection_name=supply_collection)
        self.repo_demand = DashboardRepository(collection_name=demand_collection)
        self.repo_trade = DashboardRepository(collection_name=trade_collection)

    # ---------- helpers ----------

    IST = timezone(timedelta(hours=5, minutes=30))

    @classmethod
    def _today_ist_str(cls) -> str:
        return datetime.now(cls.IST).date().isoformat()
    @classmethod
    def _today_ist_date(cls):
        return datetime.now(cls.IST).date()


    @classmethod
    def _current_block_ist(cls) -> int:
        now = datetime.now(cls.IST).time()
        n = (now.hour * 4) + (now.minute // 15) + 1
        # clamp to 1..96 just in case
        return 1 if n < 1 else 96 if n > 96 else n
    
    @staticmethod
    def _clamp_block(n: Optional[int]) -> Optional[int]:
        if n is None:
            return None
        n = int(n)
        return 1 if n < 1 else 96 if n > 96 else n


    # ---------- core ----------
    def demand_supply_snapshot(
        self,
        schedule_date: Optional[str] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Build the combined snapshot for a single date and time_block window.
        """
        date_str = schedule_date or self._today_ist_str()

        b_from = self._clamp_block(from_block) or 1
        b_to = self._clamp_block(to_block) or 96
        if b_from > b_to:
            b_from, b_to = b_to, b_from

        # ---- SUPPLY (serve_supply_table) ----
        pipe_supply: List[Dict[str, Any]] = [
            {
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
                    }
                }
            },
            {
                "$match": {
                    "$expr": {
                        "$and": [
                            {"$eq": ["$_sdate", date_str]},
                            {"$gte": ["$time_block", b_from]},
                            {"$lte": ["$time_block", b_to]},
                        ]
                    }
                }
            },
            {
                "$group": {
                    "_id": "$time_block",
                    "total_supply": {"$sum": "$volume"},
                }
            },
            {"$project": {"_id": 0, "time_block": "$_id", "total_supply": 1}},
        ]

        # ---- DEMAND (serve_demand_table) ----
        pipe_demand: List[Dict[str, Any]] = [
            {
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
                     # NEW: normalize `type` and compare upper-case
                    "_type_upper": {"$toUpper": {"$ifNull": ["$type", ""]}},
                }
            },
            {
                "$match": {
                    "$expr": {
                        "$and": [
                            {"$eq": ["$_sdate", date_str]},
                            {"$gte": ["$time_block", b_from]},
                            {"$lte": ["$time_block", b_to]},
                             {"$eq": ["$_type_upper", "IDF"]},
                        ]
                    }
                }
            },
            {
                "$group": {
                    "_id": "$time_block",
                    "comp_act_demand": {"$sum": "$comp_act_demand"},
                    "comp_udm": {"$sum": "$comp_udm"},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "time_block": "$_id",
                    "comp_act_demand": 1,
                    "comp_udm": 1,
                }
            },
        ]

        # ---- TRADES (serve_px_bid_trade) ----
        # Sum 'traded' split by market ∈ {DAM, RTM}
        pipe_trade: List[Dict[str, Any]] = [
            {
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
                    "_market": {"$toUpper": {"$ifNull": ["$market", ""]}},
                }
            },
            {
                "$match": {
                    "$expr": {
                        "$and": [
                            {"$eq": ["$_sdate", date_str]},
                            {"$gte": ["$time_block", b_from]},
                            {"$lte": ["$time_block", b_to]},
                            {"$in": ["$_market", ["DAM", "RTM"]]},
                        ]
                    }
                }
            },
            {
                "$group": {
                    "_id": {"tb": "$time_block"},
                    "dam_trade": {
                        "$sum": {"$cond": [{"$eq": ["$_market", "DAM"]}, "$traded", 0]}
                    },
                    "rtm_trade": {
                        "$sum": {"$cond": [{"$eq": ["$_market", "RTM"]}, "$traded", 0]}
                    },
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "time_block": "$_id.tb",
                    "dam_trade": 1,
                    "rtm_trade": 1,
                }
            },
        ]

        try:
            sup_rows = list(self.repo_supply.aggregate(pipe_supply))
        except Exception:
            logger.exception("Aggregation failed in serve_supply_table")
            sup_rows = []

        try:
            dem_rows = list(self.repo_demand.aggregate(pipe_demand))
        except Exception:
            logger.exception("Aggregation failed in serve_demand_table")
            dem_rows = []

        try:
            trd_rows = list(self.repo_trade.aggregate(pipe_trade))
        except Exception:
            logger.exception("Aggregation failed in serve_px_bid_trade")
            trd_rows = []

        sup_by = {r["time_block"]: float(r.get("total_supply", 0) or 0) for r in sup_rows}
        dem_by = {
            r["time_block"]: {
                "comp_act_demand": float(r.get("comp_act_demand", 0) or 0),
                "comp_udm": float(r.get("comp_udm", 0) or 0),
            }
            for r in dem_rows
        }
        trd_by = {
            r["time_block"]: {
                "dam_trade": float(r.get("dam_trade", 0) or 0),
                "rtm_trade": float(r.get("rtm_trade", 0) or 0),
            }
            for r in trd_rows
        }

        # Gap-fill 1..96 (or requested window)
        data: List[Dict[str, Any]] = []
        for tb in range(b_from, b_to + 1):
            total_supply = sup_by.get(tb, 0.0)
            comp_act = dem_by.get(tb, {}).get("comp_act_demand", 0.0)
            comp_udm = dem_by.get(tb, {}).get("comp_udm", 0.0)
            dam_trade = trd_by.get(tb, {}).get("dam_trade", 0.0)
            rtm_trade = trd_by.get(tb, {}).get("rtm_trade", 0.0)

            data.append(
                {
                    "time_block": tb,
                    "comp_act_demand": round(comp_act, 2),
                    "comp_udm": round(comp_udm, 2),
                    "total_supply": round(total_supply, 2),
                    "GAP": round(total_supply - comp_act, 2),
                    "rtm_trade": round(rtm_trade, 2),
                    "dam_trade": round(dam_trade, 2),
                }
            )

        return {"schedule_date": date_str, "data": data}



    def iex_dam_rtm_snapshot(
        self,
        schedule_date: Optional[str] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Build the snapshot for a single date and time_block window.
        """
        date_str = schedule_date or self._today_ist_str()

        b_from = self._clamp_block(from_block) or 1
        b_to = self._clamp_block(to_block) or 96
        if b_from > b_to:
            b_from, b_to = b_to, b_from
        
        today_str = self._today_ist_str()
        cur_blk = self._current_block_ist()
        is_today = (date_str == today_str)
        
        # Aggregation pipeline:
        #  - normalize schedule_date to string
        #  - upper-case market
        #  - robust parse mcv/mcp to double with onError/onNull = None
        #  - filter date + blocks + market ∈ {DAM, RTM}
        #  - pivot to dam_*/rtm_* per time_block
        pipeline: List[Dict[str, Any]] = [
            {
                "$addFields": {
                    # normalize schedule_date -> "YYYY-MM-DD"
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
                    "_market_upper": {"$toUpper": {"$ifNull": ["$market", ""]}},

                    # stringify raw fields, trim spaces
                    "_mcv_str": {
                        "$trim": {"input": {"$toString": {"$ifNull": ["$mcv", ""]}}}
                    },
                    "_mcp_str": {
                        "$trim": {"input": {"$toString": {"$ifNull": ["$mcp", ""]}}}
                    },
                }
            },
            # remove commas and percent
            {
                "$addFields": {
                    "_mcv_clean": {
                        "$replaceAll": {
                            "input": {
                                "$replaceAll": {
                                    "input": "$_mcv_str",
                                    "find": ",",
                                    "replacement": ""
                                }
                            },
                            "find": "%",
                            "replacement": ""
                        }
                    },
                    "_mcp_clean": {
                        "$replaceAll": {
                            "input": {
                                "$replaceAll": {
                                    "input": "$_mcp_str",
                                    "find": ",",
                                    "replacement": ""
                                }
                            },
                            "find": "%",
                            "replacement": ""
                        }
                    },
                }
            },
            # convert safely; treat "", "-", "na", "null" (any case) as None
            {
                "$addFields": {
                    "_mcv_num": {
                        "$convert": {
                            "input": {
                                "$cond": [
                                    {
                                        "$in": [
                                            {"$toLower": "$_mcv_clean"},
                                            ["", "-", "na", "null"],
                                        ]
                                    },
                                    None,
                                    "$_mcv_clean",
                                ]
                            },
                            "to": "double",
                            "onError": None,
                            "onNull": None,
                        }
                    },
                    "_mcp_num": {
                        "$convert": {
                            "input": {
                                "$cond": [
                                    {
                                        "$in": [
                                            {"$toLower": "$_mcp_clean"},
                                            ["", "-", "na", "null"],
                                        ]
                                    },
                                    None,
                                    "$_mcp_clean",
                                ]
                            },
                            "to": "double",
                            "onError": None,
                            "onNull": None,
                        }
                    },
                }
            },
            # filter by date, markets, and block window
            {
                "$match": {
                    "$expr": {
                        "$and": [
                            {"$eq": ["$_sdate", date_str]},
                            {"$in": ["$_market_upper", ["DAM", "RTM"]]},
                            {"$gte": ["$time_block", b_from]},
                            {"$lte": ["$time_block", b_to]},
                        ]
                    }
                }
            },
            # collapse duplicates per (time_block, market)
            {
                "$group": {
                    "_id": {"time_block": "$time_block", "market": "$_market_upper"},
                    "mcv": {"$first": "$_mcv_num"},
                    "mcp": {"$first": "$_mcp_num"},
                }
            },
            # pivot per time_block to dam_* / rtm_*
            {
                "$group": {
                    "_id": "$_id.time_block",
                    "dam_mcv": {
                        "$max": {
                            "$cond": [{"$eq": ["$_id.market", "DAM"]}, "$mcv", None]
                        }
                    },
                    "rtm_mcv": {
                        "$max": {
                            "$cond": [{"$eq": ["$_id.market", "RTM"]}, "$mcv", None]
                        }
                    },
                    "dam_mcp": {
                        "$max": {
                            "$cond": [{"$eq": ["$_id.market", "DAM"]}, "$mcp", None]
                        }
                    },
                    "rtm_mcp": {
                        "$max": {
                            "$cond": [{"$eq": ["$_id.market", "RTM"]}, "$mcp", None]
                        }
                    },
                }
            },
            {"$sort": {"_id": 1}},
            {
                "$project": {
                    "_id": 0,
                    "time_block": "$_id",
                    "dam_mcv": 1,
                    "dam_mcp": 1,
                    "rtm_mcv": {
                        "$cond": [
                            { "$and": [ {"$literal": is_today}, {"$gt": ["$_id", cur_blk]} ] },
                            None,
                            "$rtm_mcv"
                        ]
                    },
                    "rtm_mcp": {
                        "$cond": [
                            { "$and": [ {"$literal": is_today}, {"$gt": ["$_id", cur_blk]} ] },
                            None,
                            "$rtm_mcp"
                        ]
                    },
                }
            },
        ]

        try:
            rows = list(self.repo.aggregate(pipeline))
        except Exception:
            logger.exception("Error executing aggregation in serve_exchange_table")
            # In case of unexpected failures, still return a gap-filled shell
            rows = []

        # Gap-fill the requested range so the client always gets a contiguous series
        by_block = {r["time_block"]: r for r in rows}
        filled: List[Dict[str, Any]] = []
        for tb in range(b_from, b_to + 1):
            rec = by_block.get(tb) or {
                "time_block": tb,
                "dam_mcv": None,
                "rtm_mcv": None,
                "dam_mcp": None,
                "rtm_mcp": None,
            }
            filled.append(
            {
                "time_block": tb,
                "dam_mcv": round(rec["dam_mcv"], 2) if rec["dam_mcv"] is not None else None,
                "rtm_mcv": round(rec["rtm_mcv"], 2) if rec["rtm_mcv"] is not None else None,
                "dam_mcp": round(rec["dam_mcp"], 2) if rec["dam_mcp"] is not None else None,
                "rtm_mcp": round(rec["rtm_mcp"], 2) if rec["rtm_mcp"] is not None else None,
            }
        )

        return {
            "schedule_date": date_str,
            "data": filled,
        }
    
    def variable_cost_snapshot(
        self,
        schedule_date: Optional[str] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
        exchange: Optional[str] = None,   # e.g. "IEX" (optional filter)
    ) -> Dict[str, Any]:
        """
        Per time_block snapshot shaped like:
        {
          "schedule_date": "YYYY-MM-DD",
          "data": [
            {
              "time_block": 1,
              "DAM": {
                "Total": {"buy": <float>, "sell": <float>, "mcp": <float>, "mcv": <float>},
                "Ours":  {"buy": <float>, "sell": <float>, "mcp": <float>, "mcv": <float>}
              },
              "RTM": { ... }
            },
            ...
          ]
        }

        Total -> serve_exchange_table
        Ours  -> serve_px_bid_trade (sum(traded) per block/market; +ve to buy, -ve to sell(abs);
                 mcp copied from Total.mcp; mcv = traded (signed))
        """

        date_str = schedule_date or self._today_ist_str()
        b_from = self._clamp_block(from_block) or 1
        b_to = self._clamp_block(to_block) or 96
        if b_from > b_to:
            b_from, b_to = b_to, b_from

        # Build the aggregation in the DB (join exchange ↔ trades)
        pipeline: List[Dict[str, Any]] = [
            # Normalize schedule_date and market
            {
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
                    "_market": {"$toUpper": {"$ifNull": ["$market", ""]}},
                }
            },
            {
                "$match": {
                    "$expr": {
                        "$and": [
                            {"$eq": ["$_sdate", date_str]},
                            {"$in": ["$_market", ["DAM", "RTM"]]},
                            {"$gte": ["$time_block", b_from]},
                            {"$lte": ["$time_block", b_to]},
                            # apply exchange filter only if provided
                            *(
                                [{"$eq": ["$exchange", exchange]}]
                                if exchange is not None else []
                            ),
                        ]
                    }
                }
            },
            {
                "$lookup": {
                    "from": self.repo_trade.collection_name,  # "serve_px_bid_trade"
                    "let": {
                        "sd": "$_sdate",
                        "tb": "$time_block",
                        "mk": "$_market",
                        "ex": "$exchange",
                    },
                    "pipeline": [
                        {
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
                                "_market": {"$toUpper": {"$ifNull": ["$market", ""]}},
                            }
                        },
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$_sdate", "$$sd"]},
                                        {"$eq": ["$time_block", "$$tb"]},
                                        {"$eq": ["$_market", "$$mk"]},
                                        # only match on exchange if it exists on trade docs
                                        {
                                            "$or": [
                                                {"$not": [{"$ifNull": ["$exchange", False]}]},
                                                {"$eq": ["$exchange", "$$ex"]},
                                            ]
                                        },
                                    ]
                                }
                            }
                        },
                        {"$group": {"_id": None, "traded": {"$sum": "$traded"}}},
                    ],
                    "as": "trade",
                }
            },
            # Convert exchange numeric-like strings to numbers
            {
                "$addFields": {
                    "Total": {
                        "buy": {"$toDouble": {"$ifNull": ["$buy", 0]}},
                        "sell": {"$toDouble": {"$ifNull": ["$sell", 0]}},
                        "mcp": {"$toDouble": {"$ifNull": ["$mcp", None]}},
                        "mcv": {"$toDouble": {"$ifNull": ["$mcv", None]}},
                    },
                    "traded": {"$ifNull": [{"$first": "$trade.traded"}, 0]},
                }
            },
            # Build Ours from 'traded' and copy mcp from Total
            {
                "$addFields": {
                    "Ours": {
                        # buy will be -ve (only when traded is negative)
                        "buy": {
                            "$cond": [{"$lt": ["$traded", 0]}, {"$toDouble": "$traded"}, 0]
                        },
                        # sell will be +ve (only when traded is positive)
                        "sell": {
                            "$cond": [{"$gt": ["$traded", 0]}, {"$abs": {"$toDouble": "$traded"}}, 0]
                        },
                        "mcp": "$Total.mcp",
                        "mcv": {"$toDouble": "$traded"},
                    }
                }
            },
            # Keep only what we need; shape as {k: market, v: {Total,Ours}}
            {
                "$project": {
                    "_id": 0,
                    "time_block": 1,
                    "market": "$_market",
                    "kv": {"k": "$_market", "v": {"Total": "$Total", "Ours": "$Ours"}},
                }
            },
            # Group by time_block, pivot markets into object
            {"$group": {"_id": "$time_block", "markets": {"$push": "$kv"}}},
            {
                "$project": {
                    "_id": 0,
                    "time_block": "$_id",
                    "byMarket": {"$arrayToObject": "$markets"},
                }
            },
            # Final shape with fixed keys (DAM/RTM)
            {
                "$project": {
                    "time_block": 1,
                    "DAM": "$byMarket.DAM",
                    "RTM": "$byMarket.RTM",
                }
            },
            {"$sort": {"time_block": 1}},
        ]

        try:
            rows = list(self.repo.aggregate(pipeline))
        except Exception:
            logger.exception("Aggregation failed in market_book_snapshot")
            rows = []

        # Gap-fill requested blocks; ensure keys exist
        data: List[Dict[str, Any]] = []
        empty_pair = {"Total": None, "Ours": None}
        by_block = {r["time_block"]: r for r in rows}
        for tb in range(b_from, b_to + 1):
            r = by_block.get(tb)
            DAM = (r.get("DAM") if r else None) or empty_pair
            RTM = (r.get("RTM") if r else None) or empty_pair

            def _round_pair(pair):
                if not pair or not isinstance(pair, dict):
                    return pair
                return {
                    k: (
                        round(v, 2) if isinstance(v, (int, float)) and v is not None else v
                    )
                    for k, v in pair.items()
                }

            data.append(
                {
                    "time_block": tb,
                    "DAM": {"Total": _round_pair(DAM.get("Total")), "Ours": _round_pair(DAM.get("Ours"))},
                    "RTM": {"Total": _round_pair(RTM.get("Total")), "Ours": _round_pair(RTM.get("Ours"))},
                }
            )

        return {"schedule_date": date_str, "data": data}


# Global instance
management_snapshot_services = ManagementSnapshotServices()
