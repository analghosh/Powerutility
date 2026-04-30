"""
V2 Demand Forecast Service
Handles business logic for demand forecast operations with entity-based field mapping
"""
from datetime import date, timedelta, datetime
import calendar
import logging
from typing import List, Optional, Dict, Any

from ..repository.demand_forecast_repository import demand_forecast_repository
from libs.helpers.timeblock_helper import timeblock_helper

from ...core.config import settings

logger = logging.getLogger(__name__)


class DemandForecastService:
    """
    Service class responsible for demand forecast business logic with proper entity field mapping.
    """

    def __init__(self):
        # Use the correct collection names from entities
        self.demand_forecast_repo = demand_forecast_repository
        # Collection names from settings
        self.demand_serve_historical = settings.MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION
        
    async def _today_ist_date(self) -> date:
        """Get today's date in IST timezone"""
        from libs.helpers.timeblock_helper import ist_now
        return ist_now().date()

    async def _current_time_block(self) -> int:
        """Get current time block in IST"""
        return await timeblock_helper.get_current_timeblock()

    async def demand_forecast(self, schedule_date: Optional[str]) -> Dict[str, Any]:
        """
        Fetch DAF & IDF demand forecast data with proper entity field mapping.
        
        Behavior:
        - Always return 96 time blocks (1..96) in 'blocks'.
        - If schedule_date is today (IST):
            * For blocks <= current 15-min block: include full data (incl. MAPE_*).
            * For blocks > current block: include only comp_act_demand, comp_udm, forecast_DAF, forecast_IDF;
              set MAPE_* to None.
            * Error analysis (percent bins) is computed only on blocks <= current block.
        - If schedule_date is not today:
            * 'blocks' still lists 1..96; fields are filled when data exists, else None.
            * Error analysis uses only blocks that actually have MAPE values.
        """
        try:
            schedule_date = schedule_date or (await self._today_ist_date()).isoformat()
            
            # Convert string date to date object for comparison
            target_date = datetime.strptime(schedule_date, "%Y-%m-%d").date()
            
            # Get IDF and DAF data using proper entity field mapping
            idf_list = await self._get_demand_forecast_data(target_date, "IDF")
            daf_list = await self._get_demand_forecast_data(target_date, "DAF")

            # Normalize dicts keyed by integer time_block
            def tb_key(x):
                try:
                    return int(x)
                except Exception:
                    return x  # fall back if weird
                    
            idf_by_tb = {tb_key(r.get("time_block")): r for r in idf_list if r.get("time_block") is not None}
            daf_by_tb = {tb_key(r.get("time_block")): r for r in daf_list if r.get("time_block") is not None}

            # ---- IST "today" handling ----
            now_ist = await self._today_ist_date()
            is_today_ist = (now_ist == target_date)
            if is_today_ist:
                current_block = await self._current_time_block()
                considered_blocks_for_mape = list(range(1, current_block + 1))
            else:
                current_block = None
                # For error analysis on non-today, only consider blocks that actually exist in data
                considered_blocks_for_mape = sorted(set(k for k in (set(idf_by_tb) | set(daf_by_tb)) if isinstance(k, int)))

            # Always output 96 blocks
            all_blocks_output = list(range(1, 97))

            def safe_round(val):
                if isinstance(val, (int, float)):
                    return round(val, 2)
                try:
                    return round(float(val), 2)
                except Exception:
                    return val

            # ---------- Build blocks payload ----------
            blocks = []
            for tb in all_blocks_output:
                idf = idf_by_tb.get(tb)
                daf = daf_by_tb.get(tb)

                # base fields always provided (if available)
                data = {
                    "comp_act_demand": safe_round((idf or {}).get("comp_act_demand")) if idf else None,
                    "comp_udm": safe_round((idf or {}).get("comp_udm")) if idf else None,
                    "forecast_IDF": safe_round((idf or {}).get("forecast")) if idf else None,
                    "forecast_DAF": safe_round((daf or {}).get("forecast")) if daf else None,
                }

                # MAPE fields:
                if not is_today_ist or (is_today_ist and current_block is not None and tb <= current_block):
                    # full data allowed
                    data["MAPE_IDF"] = (idf or {}).get("MAPE") if idf else None
                    data["MAPE_DAF"] = (daf or {}).get("MAPE") if daf else None
                else:
                    # after current block: don't provide MAPE
                    data["MAPE_IDF"] = None
                    data["MAPE_DAF"] = None

                blocks.append({"time_block": tb, "data": data})

            # ---------- Forecast Error Analysis ----------
            def mape_values(src_by_tb, key: str):
                vals = []
                for tb in considered_blocks_for_mape:
                    v = (src_by_tb.get(tb) or {}).get(key)
                    try:
                        if v is None:
                            continue
                        vals.append(float(v))
                    except Exception:
                        continue
                return vals

            daf_mape = mape_values(daf_by_tb, "mape")
            idf_mape = mape_values(idf_by_tb, "mape")
            denom = max(len(considered_blocks_for_mape), 1)  # avoid /0

            daf_bins = [
                ("0–1%",   lambda x: 0.0 <= x <= 1.0),
                ("1–2%",   lambda x: 1.0 < x <= 2.0),
                ("2–3%",   lambda x: 2.0 < x <= 3.0),
                ("3–4%",   lambda x: 3.0 < x <= 4.0),
                ("4–5%",   lambda x: 4.0 < x <= 5.0),
                ("5–6%",   lambda x: 5.0 < x <= 6.0),
                (">6%",    lambda x: x > 6.0),
            ]
            idf_bins = [
                ("0–0.5%",  lambda x: 0.0 <= x <= 0.5),
                ("0.5-1%",  lambda x: 0.5 < x <= 1.0),
                ("1–1.5%",  lambda x: 1.0 < x <= 1.5),
                ("1.5–2%",  lambda x: 1.5 < x <= 2.0),
                ("2–2.5%",  lambda x: 2.0 < x <= 2.5),
                ("2.5–3%",  lambda x: 2.5 < x <= 3.0),
                (">3%",     lambda x: x > 3.0),
            ]

            def to_percent_distribution(values, bins_spec):
                res = []
                for label, pred in bins_spec:
                    count = sum(1 for v in values if pred(v))
                    pct = round((count / denom) * 100.0, 2)
                    res.append({"error_range": label, "percent": pct})
                return res

            error_analysis = {
                "DAF": to_percent_distribution(daf_mape, daf_bins),
                "IDF": to_percent_distribution(idf_mape, idf_bins),
                "denominator_blocks": denom,
            }

            # ---------- Get Last Updated At ----------
            # We check "demand_serve" collection for the max updated_at
            max_updated_at = await self.demand_forecast_repo.get_max_updated_at("demand_serve", schedule_date)
            from datetime import timezone
            # Fallback to current time if None? Or None?
            # Dashboard logic: last_updated_at = max_updated_at if max_updated_at else datetime.now(timezone.utc)
            # Use safe fallback
            last_updated_at = max_updated_at if max_updated_at else datetime.now(timezone.utc)

            # ---------- Assemble response ----------
            resp: Dict[str, Any] = {
                "schedule_date": schedule_date,
                "blocks": blocks,
                "forecast_error_analysis": error_analysis,
                "last_updated_at": last_updated_at
            }
            if is_today_ist:
                resp["current_time_block"] = current_block

            return resp

        except Exception as e:
            logger.exception(f"[DemandForecastService] Failed to fetch demand data: {e}")
            raise

    async def monthly_average_mape(self, month: Optional[str] = None, zone: Optional[str] = None) -> Dict[str, Any]:
        """
        Return only monthly average MAPE for DAF & IDF from historical data with proper entity field mapping.
        If the window includes today, only count rows up to the current time block.
        Optional: filter by zone.
        """
        now_ist = await self._today_ist_date()

        # Resolve month window (IST)
        if month:
            year, mon = map(int, month.split("-"))
            start_dt = date(year, mon, 1)
        else:
            start_dt = date(now_ist.year, now_ist.month, 1)

        last_day = calendar.monthrange(start_dt.year, start_dt.month)[1]
        end_dt = date(start_dt.year, start_dt.month, last_day)
        today_ist = now_ist
        if end_dt > today_ist:
            end_dt = today_ist

        start_str = start_dt.isoformat()
        end_str = end_dt.isoformat()

        # Base match for the whole month (DAF/IDF only, date range, optional zone)
        # Using proper entity field names: scheduled_at (Date type), type, zone
        # Convert date objects to datetime for MongoDB compatibility
        from datetime import datetime
        start_datetime = datetime.combine(start_dt, datetime.min.time())
        end_datetime = datetime.combine(end_dt, datetime.min.time())
        
        match_base: Dict[str, Any] = {
            "scheduled_at": {"$gte": start_datetime, "$lte": end_datetime},
            "type": {"$in": ["DAF", "IDF"]},
        }
        if zone:
            match_base["zone"] = zone

        # If the range contains today, we'll split the aggregation into:
        #   1) All days < today (no time_block cutoff)
        #   2) Today only (time_block <= current_block)
        # Otherwise do a single pipeline.
        contains_today = start_dt <= today_ist <= end_dt
        results: List[Dict[str, Any]] = []

        async def run_pipeline(match_stage: Dict[str, Any]) -> List[Dict[str, Any]]:
            return await self.demand_forecast_repo.get_monthly_average_mape_pipeline(
                self.demand_serve_historical, match_stage
            )

        if contains_today:
            # 1) everything before today
            match_past = match_base.copy()
            today_datetime = datetime.combine(today_ist, datetime.min.time())
            match_past["scheduled_at"] = {"$gte": start_datetime, "$lt": today_datetime}
            past_rows = await run_pipeline(match_past)

            # 2) today up to current block
            current_block = await self._current_time_block()
            match_today = match_base.copy()
            match_today["scheduled_at"] = today_datetime
            match_today["time_block"] = {"$lte": current_block}
            today_rows = await run_pipeline(match_today)

            # Merge the two sets: sum(count), weighted-average avg_mape
            by_type: Dict[str, Dict[str, float]] = {}
            for chunk in (past_rows + today_rows):
                typ = chunk["_id"]
                c = float(chunk.get("count", 0) or 0)
                a = float(chunk.get("avg_mape", 0) or 0)
                if typ not in by_type:
                    by_type[typ] = {"sum_mape": a * c, "count": c}
                else:
                    by_type[typ]["sum_mape"] += a * c
                    by_type[typ]["count"] += c

            def finalize(typ: str) -> Dict[str, float]:
                c = by_type.get(typ, {}).get("count", 0.0)
                s = by_type.get(typ, {}).get("sum_mape", 0.0)
                avg = round(s / c, 2) if c else 0.0
                return {"avg": avg, "count": int(c)}

            daf = finalize("DAF")
            idf = finalize("IDF")
        else:
            # Single pass for entire month
            rows = await run_pipeline(match_base)
            m = {r["_id"]: r for r in rows}
            daf = {
                "avg": round(float(m.get("DAF", {}).get("avg_mape", 0) or 0), 2),
                "count": int(m.get("DAF", {}).get("count", 0) or 0),
            }
            idf = {
                "avg": round(float(m.get("IDF", {}).get("avg_mape", 0) or 0), 2),
                "count": int(m.get("IDF", {}).get("count", 0) or 0),
            }

        return {
            "month": f"{start_dt:%Y-%m}",
            "date_range": {"start": start_str, "end": end_str},
            "average_mape": {"DAF": daf["avg"], "IDF": idf["avg"]},
            "counts": {"DAF": daf["count"], "IDF": idf["count"]},
            **({"zone": zone} if zone else {}),
        }

    async def _get_demand_forecast_data(self, target_date: date, market_type: str) -> List[Dict[str, Any]]:
        return await self.demand_forecast_repo.get_demand_forecast_data_pipeline(
            "demand_serve", target_date, market_type
        )
