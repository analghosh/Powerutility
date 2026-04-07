import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Union, Tuple, Optional, Callable

from fastapi import HTTPException, Request

from ..repository.dashboard_repository import DashboardRepository
# from ..repository.serve_data_historical_repository import HistoricalRepository

logger = logging.getLogger(__name__)

# =========================
# Time / Block utilities
# =========================

IST_OFFSET = timedelta(hours=5, minutes=30)
TOTAL_BLOCKS = 96
BLOCK_MINUTES = 15

def ist_now() -> datetime:
    return datetime.now(timezone.utc) + IST_OFFSET

def block_from_hhmm(hhmm: str) -> int:
    hour, minute = map(int, hhmm.split(":"))
    return min((hour * 60 + minute) // BLOCK_MINUTES + 1, TOTAL_BLOCKS)

def block_label(block_id: int) -> str:
    if block_id < 1 or block_id > TOTAL_BLOCKS:
        return "Invalid block"
    start = (datetime.strptime("00:00", "%H:%M") + timedelta(minutes=(block_id - 1) * BLOCK_MINUTES)).time()
    end_raw = (datetime.strptime("00:00", "%H:%M") + timedelta(minutes=block_id * BLOCK_MINUTES)).time()
    end = "00:00" if block_id == TOTAL_BLOCKS else end_raw.strftime("%H:%M")
    return f"{start.strftime('%H:%M')}-{end}"

def window_blocks(current_block: int, before: int = 12, after: int = 11) -> List[Dict[str, Union[int, str]]]:
    res: List[Dict[str, Union[int, str]]] = []
    for offset in range(-before, after + 1):
        bid = ((current_block - 1 + offset) % TOTAL_BLOCKS) + 1
        res.append({"block_id": bid, "full_time": block_label(bid)})
    return res

def last_24_blocks(current_block: int) -> List[Dict[str, Union[int, str]]]:
    res: List[Dict[str, Union[int, str]]] = []
    for i in range(23, -1, -1):
        bid = ((current_block - i - 1) % TOTAL_BLOCKS) + 1
        res.append({"block_id": bid, "full_time": block_label(bid)})
    return res


# =========================
# Dashboard Service (DRY)
# =========================

class DashboardPGService:
    # IST timezone for fallback timestamp
    IST = timezone(timedelta(hours=5, minutes=30))
    
    def __init__(self, collection_name: str = "serve_demand_table"):
        self.dashboard_repo = DashboardRepository(collection_name=collection_name)
        self.peak_demand_repo = DashboardRepository(collection_name="peak_demand_31_days")
        # self.serve_availability_repo = DashboardRepository(collection_name="serve_availability_table")
        self.exchange_repo = DashboardRepository(collection_name="exchange_table_api")
        self.plant_repo = DashboardRepository(collection_name="plant_master")
        # For getting max updated_at from serve_supply_table
        self.serve_supply_repo = DashboardRepository(collection_name="serve_supply_table")

    # ---------- Internal helpers ----------

    def get_max_updated_at_from_supply(self) -> Optional[str]:
        """
        Get the maximum updated_at timestamp from serve_supply_table.
        Returns ISO formatted datetime string or None if no data.
        """
        try:
            pipeline = [
                {"$group": {"_id": None, "max_updated": {"$max": "$updated_at"}}},
                {"$project": {"_id": 0, "max_updated": 1}}
            ]
            result = self.serve_supply_repo.aggregate(pipeline)
            if result and len(result) > 0 and result[0].get("max_updated"):
                max_updated = result[0]["max_updated"]
                # Convert to ISO string if it's a datetime object
                if isinstance(max_updated, datetime):
                    return max_updated.isoformat()
                return str(max_updated)
            return None
        except Exception as e:
            logger.exception("Failed to get max updated_at from serve_supply_table")
            return None

    def _safe_current_doc(
        self, schedule_date: str, current_block_id: int
    ) -> Dict[str, Any]:
        doc = self.dashboard_repo.get_current_block(schedule_date, current_block_id)
        if not doc:
            logger.warning("No current block doc: date=%s block=%s", schedule_date, current_block_id)
            return {}
        return doc

    def _fetch_blocks_map(
        self, schedule_date: str, block_ids: List[int]
    ) -> Dict[int, Dict[str, Any]]:
        docs = self.dashboard_repo.get_blocks_by_ids(schedule_date, block_ids)
        return {d.get("time_block"): d for d in docs}

    def _metric_series(
        self,
        *,
        schedule_date: str,
        current_block_id: int,
        block_ranges: List[Dict[str, Union[int, str]]],
        block_ids: List[int],
        # keys for values
        actual_key: str,
        forecast_key: Optional[str] = None,
        # how to round the plotted series & "current_*"
        series_round: Optional[int] = 2,
        current_round: Optional[int] = 2,
        # response field names
        response_key: str,
        # label of value on each item in chart array
        item_value_key: str,
    ) -> Dict[str, Any]:
        """
        Generic builder for demand/dsm_rate/net_dsm_amount-like series.
        Uses `actual_key` for blocks <= current; `forecast_key` (if supplied) for > current.
        """
        current_doc = self._safe_current_doc(schedule_date, current_block_id)
        blocks_map = self._fetch_blocks_map(schedule_date, block_ids)

        chart: List[Dict[str, Union[int, str, float]]] = []
        for b in block_ranges:
            bid = int(b["block_id"])
            src = blocks_map.get(bid, {})
            if bid <= current_block_id or not forecast_key:
                val = float(src.get(actual_key, 0) or 0)
            else:
                val = float(src.get(forecast_key, src.get(actual_key, 0) or 0))
            if series_round is not None:
                val = round(val, series_round)
            chart.append({"time_block": bid, "time": b["full_time"], item_value_key: val})

        current_val = float(current_doc.get(actual_key, 0) or 0)
        if current_round is not None:
            current_val = round(current_val, current_round)

        return {
            "data": {
                response_key: {
                    "current_block": block_label(current_block_id),
                    "current_time_block": current_block_id,
                    f"current_{item_value_key}": current_val,
                    "data": chart,
                }
            }
        }

    def _with_fallback(
        self,
        schedule_date: str,
        schedule_date_old: str,
        builder: Callable[[str], Dict[str, Any]],
        default_factory: Callable[[], Dict[str, Any]],
        log_name: str
    ) -> Dict[str, Any]:
        errors = []
        for date_try in (schedule_date, schedule_date_old):
            try:
                return builder(date_try)
            except HTTPException as e:
                errors.append((date_try, f"HTTP {e.status_code}: {e.detail}"))
            except Exception as e:
                errors.append((date_try, f"EXC: {e!r}"))
        logger.error("All fallbacks failed for %s: %s", log_name, errors)
        return default_factory()

    # ---------- Widgets ----------

    def get_dashboard_demand(
        self, schedule_date: str, current_block_id: int, block_ranges, block_ids
    ) -> Dict[str, Any]:
        # Use forecast for future blocks; actual for past/current
        return self._metric_series(
            schedule_date=schedule_date,
            current_block_id=current_block_id,
            block_ranges=block_ranges,
            block_ids=block_ids,
            actual_key="comp_act_demand",
            forecast_key="forecast_demand",  # <- adjust to your schema if different
            series_round=0,
            current_round=0,
            response_key="demand",
            item_value_key="demand",
        )

    def get_dashboard_frequency(
        self, schedule_date: str, current_block_id: int, block_ranges, block_ids
    ) -> Dict[str, Any]:
        # frequency is slightly different (switch at current)
        blocks_map = self._fetch_blocks_map(schedule_date, block_ids)
        current_doc = self.dashboard_repo.find_one({
            "schedule_date": schedule_date, "time_block": current_block_id
        })
        current_frequency = round(float((current_doc or {}).get("Raw_Frequency", 0) or 0), 2)

        chart = []
        past = True
        for b in block_ranges:
            bid = int(b["block_id"])
            src = blocks_map.get(bid, {})
            val = round(float(src.get("Raw_Frequency" if past else "forecasted_frequency", 0) or 0), 2)
            chart.append({"time_block": bid, "time": b["full_time"], "frequency": round(val, 2)})
            if bid == current_block_id and past:
                past = False

        return {
            "data": {
                "frequency": {
                    "current_block": block_label(current_block_id),
                    "current_time_block": current_block_id,
                    "current_frequency": current_frequency,
                    "data": chart,
                }
            }
        }

    def get_dashboard_ODUD(self, schedule_date: str, current_block_id: int) -> Dict[str, Any]:
        current = self.dashboard_repo.find_one({
            "schedule_date": schedule_date,
            "time_block": current_block_id
        }) or {}

        current_od_ud = round(
            float(current.get("Drawal", 0) or 0) - float(current.get("Schedule", 0) or 0), 0
        )

        # ✅ Last 11 + current = 12 blocks
        blocks = []
        for offset in range(-11, 1):  # -11 ... 0 inclusive
            bid = ((current_block_id - 1 + offset) % TOTAL_BLOCKS) + 1
            blocks.append({"block_id": bid, "full_time": block_label(bid)})

        ids = [b["block_id"] for b in blocks]
        m = self._fetch_blocks_map(schedule_date, ids)

        data = []
        for b in blocks:
            bid = int(b["block_id"])
            drawal = float(m.get(bid, {}).get("Drawal", 0) or 0)
            schedule = float(m.get(bid, {}).get("Schedule", 0) or 0)
            data.append({
                "time_block": bid,
                "time": b["full_time"],
                "od_ud": round(drawal - schedule, 0)
            })

        return {
            "current_block": block_label(current_block_id),
            "current_time_block": current_block_id,
            "current_od_ud": current_od_ud,
            "data": data,
        }

    def _exchange_pipeline(self, *, market: str, schedule_date: str, time_blocks: List[int]) -> List[Dict[str, Any]]:
        return [
            {"$match": {
                "market": market,
                "schedule_date": schedule_date,
                "time_block": {"$in": time_blocks},
                "exchange": "IEX",  # <-- keep only one exchange
            }},
            {"$sort": {"time_block": 1}},
            {"$project": {
                "_id": 0,
                "schedule_date": 1,
                "time_block": 1,
                "MCP": {"$convert": {"input": "$mcp", "to": "double", "onError": None, "onNull": None}},
                "PX_name": "$exchange",
            }},
        ]

    def get_dashboard_MCP(
        self, schedule_date: str, current_block_id: int, block_ranges, block_ids
    ) -> Dict[str, Any]:
        try:
            rtm_ids = list(range(max(1, current_block_id - 11), current_block_id + 1))

            # --- switched to serve_exchange_table ---
            rtm = self.dashboard_repo.aggregate_on(
                "serve_exchange_table",
                self._exchange_pipeline(market="RTM", schedule_date=schedule_date, time_blocks=rtm_ids),
            )
            dam = self.dashboard_repo.aggregate_on(
                "serve_exchange_table",
                self._exchange_pipeline(market="DAM", schedule_date=schedule_date, time_blocks=block_ids),
            )

            def process(rows):
                # mcp is a string in the new table; coerce safely
                data = []
                for r in rows:
                    raw = r.get("MCP", 0)  # aliased from "$mcp"
                    try:
                        val = round(float(raw or 0), 0)
                    except (TypeError, ValueError):
                        val = 0.0
                    data.append({"time_block": r.get("time_block"), "data": val})

                current_val = next((p["data"] for p in data if p["time_block"] == current_block_id), 0.0)
                return current_val, data

            rtm_now, rtm_data = process(rtm)
            dam_now, dam_data = process(dam)

            return {
                "mcp": {
                    "current_block": block_label(current_block_id),
                    "rtm": rtm_now,
                    "dam": dam_now,
                    "rtm_data": rtm_data,
                    "dam_data": dam_data,
                }
            }
        except Exception:
            logger.exception("Error fetching MCP")
            return {
                "mcp": {
                    "current_block": block_label(current_block_id),
                    "rtm": 0.0,
                    "dam": 0.0,
                    "rtm_data": [],
                    "dam_data": [],
                }
            }

    def get_dashboard_ScheduledDrawal(self, schedule_date: str, current_block_id: int) -> Dict[str, Any]:
        """
        Build Scheduled Drawal from serve_supply_table:
        - InterState = sum(volume) where c_s == "central"
        - IntraState = sum(volume) where c_s == "state"
        Keeps the response shape identical to the previous implementation.
        """
        # define window [current_block_id - 11, current_block_id + 12]
        start_tb = current_block_id - 11
        end_tb = current_block_id + 12

        pipeline = [
            {"$match": {
                "schedule_date": schedule_date,
                "time_block": {"$gte": start_tb, "$lte": end_tb}
            }},
            {"$group": {
                "_id": {"schedule_date": "$schedule_date", "time_block": "$time_block"},
                # InterState: only central
                "InterState": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$c_s", "central"]},
                            {"$convert": {"input": "$volume", "to": "double", "onError": 0, "onNull": 0}},
                            0
                        ]
                    }
                },
                # IntraState: only state
                "IntraState": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$c_s", "state"]},
                            {"$convert": {"input": "$volume", "to": "double", "onError": 0, "onNull": 0}},
                            0
                        ]
                    }
                },
            }},
            {"$project": {
                "_id": 0,
                "schedule_date": "$_id.schedule_date",
                "time_block": "$_id.time_block",
                "InterState": {"$round": ["$InterState", 2]},
                "IntraState": {"$round": ["$IntraState", 2]},
            }},
            {"$sort": {"time_block": 1}},
        ]

        # Use the generic dashboard_repo to target the desired collection.
        rows = list(self.dashboard_repo.aggregate_on("serve_supply_table", pipeline))
        # logger.warning(f"======================={rows}")
        # helper for current values
        current_inter = next((r["InterState"] for r in rows if r["time_block"] == current_block_id), None)
        current_intra = next((r["IntraState"] for r in rows if r["time_block"] == current_block_id), None)

        # to the same series shape as before
        def to_series(key: str):
            return [
                {"time_block": r["time_block"], "time": block_label(r["time_block"]), "data": r[key]}
                for r in rows
            ]

        return {
            "data": {
                "schedule_drawal": {
                    "current_block": block_label(current_block_id),
                    "inter_state": current_inter,
                    "intra_state": current_intra,
                    "inter_state_data": to_series("InterState"),
                    "intra_state_data": to_series("IntraState"),
                }
            }
        }

    def get_dashboard_peak_demand(self) -> Dict[str, Any]:
        try:
            today = ist_now().date()
            start = today - timedelta(days=30)
            start_str, end_str = start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

            pipeline = [
                {"$match": {"schedule_date": {"$gte": start_str, "$lte": end_str}}},
                {"$project": {
                    "_id": 0, "schedule_date": 1, "time_block": 1, "full_time": 1,
                    "peak_demand": {"$round": [{"$toDouble": "$peak_demand"}, 2]}
                }},
                {"$sort": {"schedule_date": 1}},
            ]
            recs = list(self.peak_demand_repo.aggregate(pipeline))  # type: ignore
            if not recs:
                return {"error": f"No peak demand records found between {start_str} and {end_str}"}

            daily = [{
                "date": r["schedule_date"],
                "time_block": r.get("time_block"),
                "full_time": r.get("full_time"),
                "peak_demand": round(float(r.get("peak_demand", 0.0) or 0.0), 0)
            } for r in recs]

            max_rec = max(recs, key=lambda r: (float(r.get("peak_demand", 0.0) or 0.0), r.get("schedule_date", "")))
            return {
                "overall_peak_demand_31_days": {
                    "max_date": max_rec["schedule_date"],
                    "peak_demand": float(max_rec.get("peak_demand", 0.0) or 0.0),
                    "time_block": max_rec.get("time_block"),
                    "full_time": max_rec.get("full_time"),
                },
                "daily_peak_data": daily
            }
        except Exception as e:
            logger.error("Error in get_dashboard_peak_demand: %s", e)
            return {"error": str(e)}


    def get_dashboard_ACP(
        self, schedule_date: str, current_block_id: int, block_ranges, block_ids
    ) -> Dict[str, Any]:
        """
        Build ACP widget from serve_exchange_table (IEX only), same shape as MCP.
        Returns:
        {
            "acp": {
                "current_block": "HH:MM-HH:MM",
                "current_time_block": <int>,
                "current_acp": <float>,
                "data": [{"time_block": int, "time": "HH:MM-HH:MM", "acp": float}, ...]
            }
        }
        """
        try:
            # pipeline to pull ACP values for the full 24-block window
            pipeline = [
                {"$match": {
                    "schedule_date": schedule_date,
                    "time_block": {"$in": block_ids},
                    "exchange": "IEX",
                    "market": "DAM",   # <-- filter only DAM
                }},
                {"$sort": {"time_block": 1}},
                {"$project": {
                    "_id": 0,
                    "schedule_date": 1,
                    "time_block": 1,
                    "ACP": {"$convert": {"input": "$acp", "to": "double", "onError": None, "onNull": None}},
                }},
            ]

            rows = self.dashboard_repo.aggregate_on("serve_exchange_table", pipeline)
            rows_map = {r.get("time_block"): r for r in rows}

            chart = []
            for b in block_ranges:
                bid = int(b["block_id"])
                raw = (rows_map.get(bid) or {}).get("ACP", 0)
                try:
                    val = round(float(raw or 0), 0)  # keep rounding consistent with MCP
                except (TypeError, ValueError):
                    val = 0.0
                chart.append({
                    "time_block": bid,
                    "time": b["full_time"],
                    "acp": val,
                })

            current_val = next((p["acp"] for p in chart if p["time_block"] == current_block_id), 0.0)

            return {
                "acp": {
                    "current_block": block_label(current_block_id),
                    "current_time_block": current_block_id,
                    "current_acp": current_val,
                    "data": chart,
                }
            }
        except Exception:
            logger.exception("Error fetching ACP")
            return {
                "acp": {
                    "current_block": block_label(current_block_id),
                    "current_time_block": current_block_id,
                    "current_acp": 0.0,
                    "data": [],
                }
            }
        
    def get_dashboard_dsm_rate(self, schedule_date: str, current_block_id: int, block_ranges, block_ids) -> Dict[str, Any]:
        return self._metric_series(
            schedule_date=schedule_date,
            current_block_id=current_block_id,
            block_ranges=block_ranges,
            block_ids=block_ids,
            actual_key="dsm_rate",
            forecast_key=None,   # if you have forecast, set it here
            series_round=2,
            current_round=2,
            response_key="dsm_rate",
            item_value_key="dsm_rate",
        )

    def get_dashboard_net_dsm_amount(self, schedule_date: str, current_block_id: int, block_ranges, block_ids) -> Dict[str, Any]:
        return self._metric_series(
            schedule_date=schedule_date,
            current_block_id=current_block_id,
            block_ranges=block_ranges,
            block_ids=block_ids,
            actual_key="net_dsm_amount",
            forecast_key=None,   # if you have forecast, set it here
            series_round=2,
            current_round=2,
            response_key="net_dsm_amount",
            item_value_key="net_dsm_amount",
        )


    def get_dashboard_capacity_chart(self) -> Dict[str, Any]:
        """
        Returns hierarchical sunburst data with colors:
        Total Capacity -> c_s -> category -> plant_name (with ppa)
        Reads from `plant_master` where:
        - c_s (central/state/other) is used directly
        - capacity comes from `ppa` (converted to double)
        """
        try:
            pipeline = [
                # Convert ppa safely
                {
                    "$addFields": {
                        "ppa_num": {
                            "$convert": {
                                "input": "$ppa",
                                "to": "double",
                                "onError": 0.0,
                                "onNull": 0.0
                            }
                        }
                    }
                },

                # 1) plant level
                {
                    "$group": {
                        "_id": {
                            "c_s": "$c_s",
                            "category": "$category",
                            "plant_name": "$plant_name"
                        },
                        "ppa": {"$sum": "$ppa_num"}
                    }
                },

                # 2) category level
                {
                    "$group": {
                        "_id": {
                            "c_s": "$_id.c_s",
                            "category": "$_id.category"
                        },
                        "plants": {
                            "$push": {
                                "plant_name": "$_id.plant_name",
                                "ppa": "$ppa"
                            }
                        },
                        "category_capacity": {"$sum": "$ppa"}
                    }
                },

                # 3) c_s level
                {
                    "$group": {
                        "_id": "$_id.c_s",
                        "categories": {
                            "$push": {
                                "category": "$_id.category",
                                "capacity": "$category_capacity",
                                "children": "$plants"
                            }
                        },
                        "c_s_capacity": {"$sum": "$category_capacity"}
                    }
                },

                # 4) root
                {
                    "$group": {
                        "_id": None,
                        "total_capacity": {"$sum": "$c_s_capacity"},
                        "c_s_groups": {
                            "$push": {
                                "c_s": "$_id",
                                "capacity": "$c_s_capacity",
                                "children": "$categories"
                            }
                        }
                    }
                },

                # 5) shape final
                {
                    "$project": {
                        "_id": 0,
                        "name": {"$literal": "Total Capacity"},
                        "capacity": "$total_capacity",
                        "children": "$c_s_groups"
                    }
                }
            ]

            result = list(self.plant_repo.aggregate(pipeline))
            if not result:
                raise HTTPException(status_code=404, detail="No capacity data found")

            root = result[0]

            # Color scheme by c_s
            cs_colors = {
                "central": "#34A853",
                "state": "#FF9900"
            }

            def lighten_color(base_hex: str, factor: float) -> str:
                base_hex = base_hex.lstrip('#')
                r, g, b = int(base_hex[:2], 16), int(base_hex[2:4], 16), int(base_hex[4:], 16)
                r = min(int(r + (255 - r) * factor), 255)
                g = min(int(g + (255 - g) * factor), 255)
                b = min(int(b + (255 - b) * factor), 255)
                return f"#{r:02X}{g:02X}{b:02X}"

            for cs_group in root['children']:
                cs_value = (cs_group.get('c_s') or "other").lower()
                cs_group['color'] = cs_colors.get(cs_value, "#CCCCCC")

                num_categories = len(cs_group.get('children', []))
                for c_idx, cat in enumerate(cs_group.get('children', [])):
                    factor = 0.2 + 0.6 * c_idx / max(1, num_categories - 1)
                    cat['color'] = lighten_color(cs_group['color'], factor)

                    num_plants = len(cat.get('children', []))
                    for p_idx, plant in enumerate(cat.get('children', [])):
                        factor_p = 0.3 + 0.4 * p_idx / max(1, num_plants - 1)
                        plant['color'] = lighten_color(cat['color'], factor_p)

            return root

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))



    # def get_dashboard_total_net_dsm_amount(self, schedule_date: str, current_block_id: int, block_ids: List[int]) -> Dict[str, Any]:
    #     """
    #     Returns the total net DSM amount up to the current block.
    #     {
    #         "total_net_dsm_amount": {
    #             "current_block": "HH:MM-HH:MM",
    #             "current_time_block": <int>,
    #             "total": <float>
    #         }
    #     }
    #     """
    #     try:
    #         # fetch all block docs up to current
    #         docs = self.dashboard_repo.get_blocks_by_ids(schedule_date, block_ids)
    #         total = 0.0
    #         for d in docs:
    #             tb = int(d.get("time_block", 0) or 0)
    #             if tb <= current_block_id:
    #                 val = float(d.get("net_dsm_amount", 0) or 0)
    #                 total += val

    #         return {
    #             "total_net_dsm_amount": {
    #                 "current_block": block_label(current_block_id),
    #                 "current_time_block": current_block_id,
    #                 "total": round(total, 2)
    #             }
    #         }
    #     except Exception as e:
    #         logger.exception("Error calculating total_net_dsm_amount")
    #         return {
    #             "total_net_dsm_amount": {
    #                 "current_block": block_label(current_block_id),
    #                 "current_time_block": current_block_id,
    #                 "total": 0.0
    #             }
    #         }
    def get_dashboard_total_net_dsm_amount(self, schedule_date: str, current_block_id: int,) -> Dict[str, Any]:
        """
        Day-to-date running sum of net_dsm_amount (from block 1 up to current),
        but only returns the last 12 points: past 11 + current.
        Response shape matches other widgets.
        """
        try:
            # 1) Build full day range 1..current_block_id
            full_ids = list(range(1, current_block_id + 1))
            docs_map = self._fetch_blocks_map(schedule_date, full_ids)

            # 2) Compute day-to-date cumulative across 1..current, in chronological order
            cumulative_series = []
            running = 0.0
            for tb in full_ids:
                val = float((docs_map.get(tb, {}) or {}).get("net_dsm_amount", 0) or 0)
                running += val
                cumulative_series.append({
                    "time_block": tb,
                    "time": block_label(tb),
                    "total_net_dsm_amount": round(running, 2),
                })

            # 3) Keep only the last 12 points (past 11 + current)
            last_12 = cumulative_series[-12:] if len(cumulative_series) >= 12 else cumulative_series

            # 4) Current cumulative is the last point
            current_val = last_12[-1]["total_net_dsm_amount"] if last_12 else 0.0

            return {
                "data": {
                    "total_net_dsm_amount": {
                        "current_block": block_label(current_block_id),
                        "current_time_block": current_block_id,
                        "current_total_net_dsm_amount": current_val,
                        "data": last_12
                    }
                }
            }
        except Exception:
            logger.exception("Error calculating total_net_dsm_amount (day-to-date)")
            return {
                "data": {
                    "total_net_dsm_amount": {
                        "current_block": block_label(current_block_id),
                        "current_time_block": current_block_id,
                        "current_total_net_dsm_amount": 0.0,
                        "data": []
                    }
                }
            }


    # ---------- Orchestrator ----------

    def get_dashboard(self, request: Request) -> Dict[str, Any]:
        """
        Assemble dashboard parts. Each part degrades gracefully and never throws,
        so the endpoint returns 200 with partial data when sources are missing.
        """
        now = ist_now()
        current_block = block_from_hhmm((now + timedelta(seconds=1)).strftime("%H:%M"))
        blocks = window_blocks(current_block, before=11, after=12)  # 11 before + current + 12 after
        block_ids = [int(b["block_id"]) for b in blocks]

        schedule_date = now.strftime("%Y-%m-%d")
        schedule_date_old = "2025-04-02"  # fallback

        # Short lambdas to keep call-sites tiny
        def dfactory_demand(): return {"data": {"demand": {
            "current_block": block_label(current_block),
            "current_time_block": current_block,
            "current_demand": 0.0,
            "data": []
        }}}
        def dfactory_freq(): return {"data": {"frequency": {
            "current_block": block_label(current_block),
            "current_time_block": current_block,
            "current_frequency": 0.0,
            "data": []
        }}}
        def dfactory_dsm(): return {"data": {"dsm_rate": {
            "current_block": block_label(current_block),
            "current_dsm_rate": 0.0,
            "data": []
        }}}
        def dfactory_net(): return {"data": {"net_dsm_amount": {
            "current_block": block_label(current_block),
            "current_net_dsm_amount": 0.0,
            "data": []
        }}}
        def dfactory_odud(): return {
            "current_block": block_label(current_block),
            "current_time_block": current_block,
            "current_od_ud": 0.0,
            "data": []
        }
        def dfactory_mcp(): return {"mcp": {
            "current_block": block_label(current_block),
            "rtm": 0.0, "dam": 0.0, "rtm_data": [], "dam_data": []
        }}

        def dfactory_total_net_sum(): return {"data": {"total_net_dsm_amount": {
            "current_block": block_label(current_block),
            "current_time_block": current_block,
            "current_total_net_dsm_amount": 0.0,
            "data": []
        }}}

        # Compose with real fallbacks
        demand = self._with_fallback(
            schedule_date, schedule_date_old,
            lambda d: self.get_dashboard_demand(d, current_block, blocks, block_ids),
            dfactory_demand, "demand"
        )
        frequency = self._with_fallback(
            schedule_date, schedule_date_old,
            lambda d: self.get_dashboard_frequency(d, current_block, blocks, block_ids),
            dfactory_freq, "frequency"
        )
        # schedule_drawal historically uses old date; keep behavior (or switch to fallback if desired)
        schedule_drawal = self.get_dashboard_ScheduledDrawal(schedule_date, current_block)
        od_ud = self._with_fallback(
            schedule_date, schedule_date_old,
            lambda d: self.get_dashboard_ODUD(d, current_block),
            dfactory_odud, "od_ud"
        )
        mcp = self._with_fallback(
            schedule_date, schedule_date_old,
            lambda d: self.get_dashboard_MCP(d, current_block, blocks, block_ids),
            dfactory_mcp, "mcp"
        )
        peak_demand = self.get_dashboard_peak_demand()
        acp = self._with_fallback(
            schedule_date, schedule_date_old,
            lambda d: self.get_dashboard_ACP(d, current_block, blocks, block_ids),
            dfactory_dsm, "acp"
        )
        dsm_rate = self._with_fallback(
            schedule_date, schedule_date_old,
            lambda d: self.get_dashboard_dsm_rate(d, current_block, blocks, block_ids),
            dfactory_dsm, "dsm_rate"
        )
        net_dsm_amount = self._with_fallback(
            schedule_date, schedule_date_old,
            lambda d: self.get_dashboard_net_dsm_amount(d, current_block, blocks, block_ids),
            dfactory_net, "net_dsm_amount"
        )

        total_net_dsm_amount = self._with_fallback(
            schedule_date, schedule_date_old,
            lambda d: self.get_dashboard_total_net_dsm_amount(d, current_block),
            dfactory_total_net_sum, "total_net_dsm_amount"
        )

        # Flatten into your existing response shape
        # Get last_updated_at with fallback to current time if not available
        last_updated = self.get_max_updated_at_from_supply()
        if not last_updated:
            # Fallback: use current IST time if field doesn't exist in DB
            last_updated = datetime.now(self.IST).isoformat()
            logger.warning("updated_at field not found in serve_supply_table, using current time as fallback")
        
        return {
            "demand": demand["data"]["demand"],
            "frequency": frequency["data"]["frequency"],
            "schedule_drawal": schedule_drawal["data"]["schedule_drawal"],
            "od_ud": od_ud,
            "mcp": mcp,
            "peak_demand": peak_demand,
            "acp": acp,
            "dsm_rate": dsm_rate["data"]["dsm_rate"],
            "net_dsm_amount": net_dsm_amount["data"]["net_dsm_amount"],
            "total_net_dsm_amount": total_net_dsm_amount["data"]["total_net_dsm_amount"],
            "last_updated_at": last_updated,
        }


# Global instance
dashboard_service = DashboardPGService()