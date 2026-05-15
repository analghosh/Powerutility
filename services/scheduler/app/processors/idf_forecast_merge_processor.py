"""
Merges forecast_serve (rtm_forecast_api or csv) into demand_serve IDF records.
Runs every 15 minutes; respects idf_forecast_switch in parameters collection.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from libs.constants.idf_forecast import (
    IDF_FORECAST_SWITCH_KEY,
    IDF_FORECAST_SWITCH_LAST_RESET_KEY,
    parse_switch_use_rtm_api,
    switch_para_value,
)
from libs.helpers.rtm_forecast_block_filter import (
    rtm_forecast_run_context,
    should_write_forecast_block,
    target_scheduled_dates_for_rtm,
)
from libs.models.forecast_serve_entity import FORECAST_SOURCE_CSV, FORECAST_SOURCE_RTM_API
from libs.repository.forecast_serve_repository import forecast_serve_repository
from libs.repository.parameter_repository import parameter_repository

from .base_processor import BaseProcessor
from ..core.config import settings
from ..repository.demand_serve_repository import demand_serve_repository

logger = logging.getLogger(__name__)

RTM_SOURCE_FORECAST = "rtm_forecast_api"


class IdfForecastMergeProcessor(BaseProcessor):
    """Publish active forecast source from forecast_serve into demand_serve (IDF)."""

    def __init__(self):
        super().__init__()
        self.demand_repository = demand_serve_repository
        self.forecast_repository = forecast_serve_repository
        self.parameter_repository = parameter_repository

    async def execute_cron_job(self) -> Dict[str, Any]:
        job_type = "idf_forecast_merge"
        job_name = "IDF Forecast Merge"
        schedule = getattr(settings, "IDF_FORECAST_MERGE_CRON_SCHEDULE", "*/15 * * * *")
        start_time = datetime.now()

        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        self.set_correlation_id(execution_id or "")

        logger.info("=== EXECUTING IDF FORECAST MERGE JOB ===")

        try:
            merged, skipped, failed = await self.run_merge()

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            result = {
                "status": "success" if failed == 0 else "partial",
                "message": f"IDF forecast merge — merged={merged}, skipped={skipped}, failed={failed}",
                "merged": merged,
                "skipped": skipped,
                "failed": failed,
                "execution_time": execution_time,
                "data_type": "idf_forecast_merge",
            }

            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id or "",
                status=result["status"],
                success=failed == 0,
                result=result,
            )
            return result
        except Exception as e:
            logger.error("Error in IDF forecast merge job: %s", e, exc_info=True)
            return {"status": "error", "message": str(e)}
        finally:
            self.clear_correlation_id()

    async def _maybe_reset_switch_for_new_day(self, today_ist: date) -> bool:
        """Set idf_forecast_switch to true on IST calendar day rollover."""
        last_doc = await self.parameter_repository.get_by_key(IDF_FORECAST_SWITCH_LAST_RESET_KEY)
        last_date_str = (last_doc or {}).get("para_value")
        last_date: Optional[date] = None
        if last_date_str:
            try:
                last_date = datetime.strptime(str(last_date_str).strip()[:10], "%Y-%m-%d").date()
            except ValueError:
                last_date = None

        if last_date is not None and today_ist <= last_date:
            return False

        await self.parameter_repository.upsert_by_key(IDF_FORECAST_SWITCH_KEY, "true")
        await self.parameter_repository.upsert_by_key(
            IDF_FORECAST_SWITCH_LAST_RESET_KEY,
            today_ist.isoformat(),
        )
        logger.info("IDF forecast switch reset to API mode for new IST day %s", today_ist)
        return True

    async def _get_use_rtm_api(self) -> bool:
        await self.parameter_repository.ensure_idf_forecast_switch_default()
        doc = await self.parameter_repository.get_by_key(IDF_FORECAST_SWITCH_KEY)
        return parse_switch_use_rtm_api((doc or {}).get("para_value"))

    @staticmethod
    def _scheduled_at_midnight(d: date) -> datetime:
        return datetime(d.year, d.month, d.day)

    @staticmethod
    def _csv_forecast_invalid(forecast: Any) -> bool:
        if forecast is None:
            return True
        try:
            return float(forecast) == 0.0
        except (TypeError, ValueError):
            return True

    async def _get_rostering(
        self, scheduled_at: datetime, time_block: int
    ) -> float:
        existing = await self.demand_repository.serve_collection.find_one(
            {"scheduled_at": scheduled_at, "time_block": int(time_block), "type": "IDF"},
            {"rostering": 1},
        )
        if existing and existing.get("rostering") is not None:
            return float(existing.get("rostering") or 0.0)
        return 0.0

    async def _merge_block(
        self,
        scheduled_at: datetime,
        time_block: int,
        forecast_mw: float,
        forecasted_frequency: float,
        source_forecast: str,
        use_rtm_api: bool,
    ) -> bool:
        now = datetime.now()
        rostering = await self._get_rostering(scheduled_at, time_block)
        comp_act_demand = forecast_mw + rostering
        comp_udm = forecast_mw

        filter_doc = {
            "scheduled_at": scheduled_at,
            "time_block": int(time_block),
            "type": "IDF",
        }
        update_doc = {
            "$set": {
                "scheduled_at": scheduled_at,
                "time_block": int(time_block),
                "type": "IDF",
                "forecast": forecast_mw,
                "forecasted_frequency": forecasted_frequency,
                "comp_act_demand": comp_act_demand,
                "comp_udm": comp_udm,
                "source_forecast": source_forecast,
                "updated_at": now,
                "timestamp": now,
            },
            "$setOnInsert": {
                "inserted_at": now,
                "created_at": now,
                "source": source_forecast,
                "system_revision_number": 1,
            },
        }
        result = await self.demand_repository.serve_collection.update_one(
            filter_doc, update_doc, upsert=True
        )
        return bool(result.upserted_id or result.modified_count > 0)

    async def run_merge(self) -> Tuple[int, int, int]:
        tz_name = getattr(settings, "CRON_TIMEZONE", None) or "Asia/Kolkata"
        min_ahead = max(
            0,
            int(getattr(settings, "RTM_FORECAST_MIN_AHEAD_BLOCKS", 5)),
        )
        ctx = rtm_forecast_run_context(tz_name, min_ahead)
        today_ist = ctx["today_ist"]

        await self._maybe_reset_switch_for_new_day(today_ist)
        use_rtm_api = await self._get_use_rtm_api()
        active_source = FORECAST_SOURCE_RTM_API if use_rtm_api else FORECAST_SOURCE_CSV
        source_forecast_label = RTM_SOURCE_FORECAST if use_rtm_api else FORECAST_SOURCE_CSV

        target_dates = target_scheduled_dates_for_rtm(
            today_ist, ctx["after_evening_cutover"]
        )

        merged = skipped = failed = 0

        for target_date in target_dates:
            scheduled_at = self._scheduled_at_midnight(target_date)
            docs = await self.forecast_repository.find_by_scheduled_at_and_source(
                scheduled_at, active_source
            )

            for doc in docs:
                try:
                    tb = doc.get("time_block")
                    if tb is None:
                        skipped += 1
                        continue
                    time_block = int(tb)
                    scheduled_date = scheduled_at.date()

                    if use_rtm_api:
                        if not should_write_forecast_block(
                            scheduled_date,
                            time_block,
                            today_ist,
                            ctx["current_block"],
                            after_evening_cutover=ctx["after_evening_cutover"],
                            min_ahead_blocks=ctx["min_ahead_blocks"],
                        ):
                            skipped += 1
                            continue
                        forecast_mw = float(doc.get("forecast") or 0.0)
                        forecasted_frequency = float(doc.get("forecasted_frequency") or 0.0)
                    else:
                        if self._csv_forecast_invalid(doc.get("forecast")):
                            skipped += 1
                            continue
                        forecast_mw = float(doc.get("forecast"))
                        existing = await self.demand_repository.serve_collection.find_one(
                            {
                                "scheduled_at": scheduled_at,
                                "time_block": time_block,
                                "type": "IDF",
                            },
                            {"forecasted_frequency": 1},
                        )
                        forecasted_frequency = float(
                            (existing or {}).get("forecasted_frequency") or 0.0
                        )

                    ok = await self._merge_block(
                        scheduled_at,
                        time_block,
                        forecast_mw,
                        forecasted_frequency,
                        source_forecast_label,
                        use_rtm_api,
                    )
                    if ok:
                        merged += 1
                    else:
                        skipped += 1
                except Exception as block_err:
                    failed += 1
                    logger.warning(
                        "Merge failed scheduled_at=%s block=%s: %s",
                        scheduled_at,
                        doc.get("time_block"),
                        block_err,
                    )

        logger.info(
            "IDF forecast merge complete source=%s merged=%d skipped=%d failed=%d",
            active_source,
            merged,
            skipped,
            failed,
        )
        return merged, skipped, failed

    async def process_raw_data_to_serve(
        self,
        raw_data: List[Dict[str, Any]],
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any],
    ) -> bool:
        """Cron-only processor; raw-data pipeline not used."""
        return True
