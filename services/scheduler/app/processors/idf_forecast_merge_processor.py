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
from libs.helpers.idf_active_session import (
    active_session_blocks_from_mapping,
    active_session_should_flip_manual_to_auto,
    csv_forecast_mw,
    index_forecast_by_block,
    merge_time_blocks_active_through_session_48,
)
from libs.helpers.rtm_forecast_block_filter import (
    rtm_forecast_run_context,
    should_write_forecast_block,
    target_scheduled_dates_for_rtm,
)
from libs.helpers.idf_forecast_audit import switch_change_audit_fields
from libs.models.forecast_serve_entity import FORECAST_SOURCE_CSV, FORECAST_SOURCE_RTM_API
from .base_processor import BaseProcessor
from ..core.config import settings
from ..repository.demand_serve_repository import demand_serve_repository
from ..repository.forecast_serve_repository import forecast_serve_repository
from ..repository.parameter_repository import parameter_repository
from ..repository.rtm_product_hardcoded_data_repository import RTMProductHardCodedDataRepository

logger = logging.getLogger(__name__)

RTM_SOURCE_FORECAST = "rtm_forecast_api"


class IdfForecastMergeProcessor(BaseProcessor):
    """Publish active forecast source from forecast_serve into demand_serve (IDF)."""

    def __init__(self):
        super().__init__()
        self.demand_repository = demand_serve_repository
        self.forecast_repository = forecast_serve_repository
        self.parameter_repository = parameter_repository
        self.rtm_hardcoded_repository = RTMProductHardCodedDataRepository()

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

    async def execute_forecast_serve_archival_job(self) -> Dict[str, Any]:
        """Archive old forecast_serve rows to forecast_serve_historical (daily job)."""
        job_type = "forecast_serve_archival"
        job_name = "Forecast Serve Data Archival"
        schedule = "0 0 * * *"
        start_time = datetime.now()

        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        self.set_correlation_id(execution_id or "")

        logger.info("=== EXECUTING FORECAST SERVE ARCHIVAL JOB ===")

        try:
            days_to_keep = getattr(settings, "FORECAST_SERVE_DAYS_TO_KEEP", 2)
            archive_result = await self.forecast_repository.archive_old_forecast_data(
                days_to_keep=days_to_keep
            )

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            if archive_result.get("error"):
                result = {
                    "status": "error",
                    "message": f"Forecast serve archival error: {archive_result['error']}",
                    "execution_time": execution_time,
                    "data_type": "forecast_serve",
                    "correlation_id": execution_id,
                }
                await self._record_job_completion(
                    job_type=job_type,
                    job_name=job_name,
                    schedule=schedule,
                    execution_start=start_time,
                    execution_end=end_time,
                    execution_id=execution_id or "",
                    status="error",
                    success=False,
                    result=result,
                    error_message=archive_result["error"],
                )
                return result

            archived_count = archive_result.get("archived_count", 0)
            deleted_count = archive_result.get("deleted_count", 0)
            result = {
                "status": "success",
                "message": (
                    f"Archived {archived_count} forecast records, "
                    f"deleted {deleted_count} from forecast_serve"
                ),
                "execution_time": execution_time,
                "archived_count": archived_count,
                "deleted_count": deleted_count,
                "cutoff_date": archive_result.get("cutoff_date"),
                "data_type": "forecast_serve",
                "correlation_id": execution_id,
            }
            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id or "",
                status="success",
                success=True,
                result=result,
            )
            logger.info(
                "Forecast serve archival completed: archived=%d deleted=%d in %.2fs",
                archived_count,
                deleted_count,
                execution_time,
            )
            return result
        except Exception as e:
            logger.error("Error executing forecast serve archival job: %s", e, exc_info=True)
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

        before_doc = await self.parameter_repository.get_by_key(IDF_FORECAST_SWITCH_KEY)
        previous_use_rtm = parse_switch_use_rtm_api((before_doc or {}).get("para_value"))
        previous = {
            "use_rtm_api": previous_use_rtm,
            "active_source": (
                FORECAST_SOURCE_RTM_API if previous_use_rtm else FORECAST_SOURCE_CSV
            ),
            "para_value": switch_para_value(previous_use_rtm),
            "para_key": IDF_FORECAST_SWITCH_KEY,
        }

        await self.parameter_repository.upsert_by_key(IDF_FORECAST_SWITCH_KEY, "true")
        await self.parameter_repository.upsert_by_key(
            IDF_FORECAST_SWITCH_LAST_RESET_KEY,
            today_ist.isoformat(),
        )
        new_state = {
            "use_rtm_api": True,
            "active_source": FORECAST_SOURCE_RTM_API,
            "para_value": "true",
            "para_key": IDF_FORECAST_SWITCH_KEY,
        }
        logger.info("IDF forecast switch reset to API mode for new IST day %s", today_ist)
        self._audit_switch_change(
            action="idf_forecast_switch_system_reset",
            message=f"IDF forecast switch reset to API mode for IST day {today_ist}",
            previous=previous,
            new_state=new_state,
            extra={
                "changed_by": "system",
                "change_source": "scheduler",
                "trigger": "idf_forecast_merge_daily_reset",
                "ist_date": today_ist.isoformat(),
            },
        )
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

    @staticmethod
    def _parse_csv_forecast_mw(forecast: Any) -> Optional[float]:
        if IdfForecastMergeProcessor._csv_forecast_invalid(forecast):
            return None
        return float(forecast)

    def _audit_switch_change(
        self,
        action: str,
        message: str,
        previous: Dict[str, Any],
        new_state: Dict[str, Any],
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write audit trail for switch changes (system or future callers)."""
        try:
            from libs.middleware import get_correlation_id
            from ..core.dependencies import get_audit_logger

            correlation_id = get_correlation_id() or self.get_correlation_id() or ""
            audit_logger = get_audit_logger()
            audit_logger.log_system_operation(
                service="scheduler",
                operation=action,
                message=message,
                correlation_id=correlation_id,
                success=True,
                **switch_change_audit_fields(previous, new_state),
                **(extra or {}),
            )
        except Exception as audit_err:
            logger.warning("Failed to write IDF forecast switch audit log: %s", audit_err)

    async def _flip_switch_to_auto(self, trigger: str, message: str) -> None:
        before_doc = await self.parameter_repository.get_by_key(IDF_FORECAST_SWITCH_KEY)
        previous_use_rtm = parse_switch_use_rtm_api((before_doc or {}).get("para_value"))
        previous = {
            "use_rtm_api": previous_use_rtm,
            "active_source": (
                FORECAST_SOURCE_RTM_API if previous_use_rtm else FORECAST_SOURCE_CSV
            ),
            "para_value": switch_para_value(previous_use_rtm),
            "para_key": IDF_FORECAST_SWITCH_KEY,
        }
        await self.parameter_repository.upsert_by_key(IDF_FORECAST_SWITCH_KEY, "true")
        new_state = {
            "use_rtm_api": True,
            "active_source": FORECAST_SOURCE_RTM_API,
            "para_value": "true",
            "para_key": IDF_FORECAST_SWITCH_KEY,
        }
        logger.info("%s — idf_forecast_switch set to Auto (API)", message)
        self._audit_switch_change(
            action="idf_forecast_switch_auto_fallback",
            message=message,
            previous=previous,
            new_state=new_state,
            extra={
                "changed_by": "system",
                "change_source": "scheduler",
                "trigger": trigger,
            },
        )

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

        session_mapping = await self.rtm_hardcoded_repository.get_session_mapping()
        active_blocks = active_session_blocks_from_mapping(
            session_mapping, ctx["current_block"]
        )
        active_session_start = min(active_blocks) if active_blocks else ctx["current_block"]

        target_dates = target_scheduled_dates_for_rtm(
            today_ist,
            ctx["after_evening_cutover"],
            ctx.get("include_tomorrow_forecast", False),
        )

        merged = skipped = failed = 0
        switch_flipped = False

        for target_date in target_dates:
            scheduled_at = self._scheduled_at_midnight(target_date)
            scheduled_date = scheduled_at.date()

            csv_docs = await self.forecast_repository.find_by_scheduled_at_and_source(
                scheduled_at, FORECAST_SOURCE_CSV
            )
            api_docs = await self.forecast_repository.find_by_scheduled_at_and_source(
                scheduled_at, FORECAST_SOURCE_RTM_API
            )
            csv_by_block = index_forecast_by_block(csv_docs)
            api_by_block = index_forecast_by_block(api_docs)

            if not use_rtm_api:
                should_flip, trigger = active_session_should_flip_manual_to_auto(
                    active_blocks, csv_by_block
                )
                if should_flip:
                    await self._flip_switch_to_auto(
                        trigger=trigger,
                        message=(
                            f"Active session blocks {active_blocks}: "
                            f"switching Manual to Auto ({trigger})"
                        ),
                    )
                    use_rtm_api = True
                    switch_flipped = True

            source_forecast_label = (
                RTM_SOURCE_FORECAST if use_rtm_api else FORECAST_SOURCE_CSV
            )

            for time_block in merge_time_blocks_active_through_session_48(
                active_session_start
            ):
                try:
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

                    if use_rtm_api:
                        doc = api_by_block.get(time_block)
                        if not doc:
                            skipped += 1
                            continue
                        forecast_mw = float(doc.get("forecast") or 0.0)
                        forecasted_frequency = float(
                            doc.get("forecasted_frequency") or 0.0
                        )
                    else:
                        doc = csv_by_block.get(time_block)
                        if not doc:
                            skipped += 1
                            continue
                        forecast_mw = csv_forecast_mw(
                            doc.get("forecast"),
                            treat_blank_as_zero=True,
                        )
                        if forecast_mw is None:
                            skipped += 1
                            continue
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
                        time_block,
                        block_err,
                    )

        active_source = FORECAST_SOURCE_RTM_API if use_rtm_api else FORECAST_SOURCE_CSV
        logger.info(
            "IDF forecast merge complete source=%s merged=%d skipped=%d failed=%d "
            "switch_flipped=%s active_session=%s",
            active_source,
            merged,
            skipped,
            failed,
            switch_flipped,
            active_blocks,
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
