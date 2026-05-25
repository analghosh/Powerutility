"""
Merges forecast_serve (rtm_forecast_api or csv) into demand_serve IDF records.
Runs every 15 minutes; respects idf_forecast_switch in parameters collection.
"""

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from pymongo import UpdateOne

from libs.constants.idf_forecast import (
    IDF_FORECAST_SWITCH_AUTO_FALLBACK_MESSAGE_KEY,
    IDF_FORECAST_SWITCH_KEY,
    IDF_FORECAST_SWITCH_LAST_RESET_KEY,
    MANUAL_AUTO_FLIP_ACTIVE_SESSION_CSV_MESSAGE,
    parse_switch_use_rtm_api,
    switch_para_value,
)
from libs.helpers.idf_forecast_switch_validation import active_session_csv_requires_auto_flip
from libs.helpers.idf_forecast_session import (
    index_forecast_docs,
    resolve_session_merge_ops_api_only,
    resolve_session_merge_ops_csv_manual,
    session_has_csv_forecast_present,
    session_ids_from_active_session_through_96,
    session_to_time_blocks,
    time_block_to_session_id,
    writable_blocks_for_date,
)
from libs.helpers.rtm_forecast_block_filter import (
    rtm_forecast_run_context,
    target_scheduled_dates_for_rtm,
)
from libs.helpers.idf_forecast_audit import switch_change_audit_fields
from libs.models.forecast_serve_entity import FORECAST_SOURCE_CSV, FORECAST_SOURCE_RTM_API
from .base_processor import BaseProcessor
from ..core.config import settings
from ..repository.demand_serve_repository import demand_serve_repository
from ..repository.forecast_serve_repository import forecast_serve_repository
from ..repository.parameter_repository import parameter_repository

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
        await self.parameter_repository.upsert_by_key(
            IDF_FORECAST_SWITCH_AUTO_FALLBACK_MESSAGE_KEY,
            "",
            extra_set={"trigger": "idf_forecast_merge_daily_reset", "changed_by": "system"},
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

    async def _flip_switch_to_auto(
        self,
        trigger: str,
        message: str,
        user_message: Optional[str] = None,
    ) -> None:
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
        display_message = user_message or message
        await self.parameter_repository.upsert_by_key(
            IDF_FORECAST_SWITCH_AUTO_FALLBACK_MESSAGE_KEY,
            display_message,
            extra_set={
                "trigger": trigger,
                "changed_by": "system",
            },
        )
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

    async def _load_demand_idf_by_block(
        self, scheduled_at: datetime
    ) -> Dict[int, Dict[str, Any]]:
        cursor = self.demand_repository.serve_collection.find(
            {"scheduled_at": scheduled_at, "type": "IDF"},
            {"time_block": 1, "forecast": 1, "rostering": 1, "forecasted_frequency": 1},
        )
        docs = await cursor.to_list(length=200)
        return {
            int(doc["time_block"]): doc
            for doc in docs
            if doc.get("time_block") is not None
        }

    @staticmethod
    def _build_merge_update_op(
        scheduled_at: datetime,
        time_block: int,
        forecast_mw: float,
        forecasted_frequency: float,
        source_forecast: str,
        demand_by_block: Dict[int, Dict[str, Any]],
        now: datetime,
    ) -> UpdateOne:
        rostering = float(
            (demand_by_block.get(time_block) or {}).get("rostering") or 0.0
        )
        comp_act_demand = forecast_mw + rostering
        filter_doc = {
            "scheduled_at": scheduled_at,
            "time_block": int(time_block),
            "type": "IDF",
        }
        return UpdateOne(
            filter_doc,
            {
                "$set": {
                    "scheduled_at": scheduled_at,
                    "time_block": int(time_block),
                    "type": "IDF",
                    "forecast": forecast_mw,
                    "forecasted_frequency": forecasted_frequency,
                    "comp_act_demand": comp_act_demand,
                    "comp_udm": forecast_mw,
                    "source_forecast": source_forecast,
                    "updated_at": now,
                    "timestamp": now,
                },
                "$setOnInsert": {
                    "inserted_at": now,
                    "created_at": now,
                    "system_revision_number": 1,
                },
            },
            upsert=True,
        )

    async def _execute_bulk_merge(self, bulk_ops: List[UpdateOne]) -> int:
        if not bulk_ops:
            return 0
        result = await self.demand_repository.serve_collection.bulk_write(
            bulk_ops, ordered=False
        )
        return int(result.modified_count) + len(result.upserted_ids)

    @staticmethod
    def _is_primary_delivery_date_for_auto_flip(
        scheduled_date: date,
        ctx: Dict[str, Any],
    ) -> bool:
        """
        Evaluate auto-flip only on the primary intraday delivery date (today before
        22:30 IST, tomorrow after evening cutover) so a future day with empty CSV
        does not flip the switch while today is still in Manual.
        """
        primary_dates = target_scheduled_dates_for_rtm(
            ctx["today_ist"],
            ctx["after_evening_cutover"],
            ctx.get("include_tomorrow_forecast", False),
        )
        return bool(primary_dates) and scheduled_date == primary_dates[0]

    async def _manual_pre_checks_flip_auto(
        self,
        ctx: Dict[str, Any],
        scheduled_date: date,
        csv_by_block: Dict[int, Dict[str, Any]],
        api_by_block: Dict[int, Dict[str, Any]],
        demand_by_block: Dict[int, Dict[str, Any]],
        writable_set: Set[int],
    ) -> bool:
        """Return True if switch was flipped to Auto (active session CSV zero/empty)."""
        _ = api_by_block, demand_by_block, writable_set
        if not self._is_primary_delivery_date_for_auto_flip(scheduled_date, ctx):
            return False
        active_sid = int(ctx["active_session_id"])
        should_flip, user_message = active_session_csv_requires_auto_flip(
            active_sid, csv_by_block
        )
        if should_flip:
            await self._flip_switch_to_auto(
                trigger="active_session_csv_zero_or_empty",
                message=(
                    f"Active session {active_sid} on {scheduled_date} has zero/empty "
                    f"CSV forecast; switching to Auto"
                ),
                user_message=user_message or MANUAL_AUTO_FLIP_ACTIVE_SESSION_CSV_MESSAGE,
            )
            return True
        return False

    async def _merge_delivery_day_manual(
        self,
        scheduled_at: datetime,
        scheduled_date: date,
        ctx: Dict[str, Any],
        csv_by_block: Dict[int, Dict[str, Any]],
        api_by_block: Dict[int, Dict[str, Any]],
        demand_by_block: Dict[int, Dict[str, Any]],
        writable_set: Set[int],
    ) -> Tuple[int, int, int]:
        merged = skipped = failed = 0
        now = datetime.now()
        bulk_ops: List[UpdateOne] = []

        for session_id in session_ids_from_active_session_through_96(ctx["now_local"]):
            if not session_has_csv_forecast_present(session_id, csv_by_block):
                b1, b2 = session_to_time_blocks(session_id)
                for block in (b1, b2):
                    if block in writable_set:
                        skipped += 1
                continue

            ops = resolve_session_merge_ops_csv_manual(
                session_id,
                csv_by_block,
                demand_by_block,
                FORECAST_SOURCE_CSV,
            )

            for time_block, forecast_mw, freq, source_label in ops:
                if time_block not in writable_set:
                    continue
                try:
                    bulk_ops.append(
                        self._build_merge_update_op(
                            scheduled_at,
                            time_block,
                            forecast_mw,
                            freq,
                            source_label,
                            demand_by_block,
                            now,
                        )
                    )
                except Exception as block_err:
                    failed += 1
                    logger.warning(
                        "Merge op build failed scheduled_at=%s block=%s: %s",
                        scheduled_at,
                        time_block,
                        block_err,
                    )

        try:
            merged = await self._execute_bulk_merge(bulk_ops)
        except Exception as bulk_err:
            logger.error("bulk_write failed for %s: %s", scheduled_at, bulk_err)
            failed += len(bulk_ops)
        return merged, skipped, failed

    async def _merge_delivery_day_auto(
        self,
        scheduled_at: datetime,
        scheduled_date: date,
        ctx: Dict[str, Any],
        api_by_block: Dict[int, Dict[str, Any]],
        demand_by_block: Dict[int, Dict[str, Any]],
        writable_set: Set[int],
    ) -> Tuple[int, int, int]:
        merged = skipped = failed = 0
        now = datetime.now()
        bulk_ops: List[UpdateOne] = []

        for session_id in session_ids_from_active_session_through_96(ctx["now_local"]):
            ops = resolve_session_merge_ops_api_only(
                session_id,
                api_by_block,
                RTM_SOURCE_FORECAST,
            )
            if not ops:
                b1, b2 = session_to_time_blocks(session_id)
                for b in (b1, b2):
                    if b in writable_set:
                        skipped += 1
                continue

            for time_block, forecast_mw, freq, source_label in ops:
                if time_block not in writable_set:
                    continue
                try:
                    bulk_ops.append(
                        self._build_merge_update_op(
                            scheduled_at,
                            time_block,
                            forecast_mw,
                            freq,
                            source_label,
                            demand_by_block,
                            now,
                        )
                    )
                except Exception as block_err:
                    failed += 1
                    logger.warning(
                        "API merge op failed block=%s: %s", time_block, block_err
                    )

        try:
            merged = await self._execute_bulk_merge(bulk_ops)
        except Exception as bulk_err:
            logger.error("bulk_write API failed for %s: %s", scheduled_at, bulk_err)
            failed += len(bulk_ops)
        return merged, skipped, failed

    async def run_merge(self) -> Tuple[int, int, int]:
        tz_name = getattr(settings, "CRON_TIMEZONE", None) or "Asia/Kolkata"
        min_ahead = max(
            0,
            int(getattr(settings, "RTM_FORECAST_MIN_AHEAD_BLOCKS", 5)),
        )
        ctx = rtm_forecast_run_context(tz_name, min_ahead)
        today_ist = ctx["today_ist"]

        await self._maybe_reset_switch_for_new_day(today_ist)
        switch_prefers_api = await self._get_use_rtm_api()
        api_merge_this_run = switch_prefers_api
        switch_flipped = False

        target_dates = target_scheduled_dates_for_rtm(
            today_ist,
            ctx["after_evening_cutover"],
            ctx.get("include_tomorrow_forecast", False),
        )

        merged = skipped = failed = 0

        for target_date in target_dates:
            scheduled_at = self._scheduled_at_midnight(target_date)
            scheduled_date = scheduled_at.date()
            writable_list = writable_blocks_for_date(
                scheduled_date,
                today_ist,
                ctx["current_block"],
                ctx["after_evening_cutover"],
                ctx["min_ahead_blocks"],
                int(ctx["active_session_id"]),
                ctx.get("now_local"),
            )
            writable_set: Set[int] = set(writable_list)

            csv_docs, api_docs, demand_by_block = await asyncio.gather(
                self.forecast_repository.find_by_scheduled_at_and_source(
                    scheduled_at, FORECAST_SOURCE_CSV
                ),
                self.forecast_repository.find_by_scheduled_at_and_source(
                    scheduled_at, FORECAST_SOURCE_RTM_API
                ),
                self._load_demand_idf_by_block(scheduled_at),
            )
            csv_by_block = index_forecast_docs(csv_docs)
            api_by_block = index_forecast_docs(api_docs)

            if not api_merge_this_run:
                if await self._manual_pre_checks_flip_auto(
                    ctx,
                    scheduled_date,
                    csv_by_block,
                    api_by_block,
                    demand_by_block,
                    writable_set,
                ):
                    switch_flipped = True
                    api_merge_this_run = True
                    logger.info(
                        "IDF forecast switch set to Auto; applying rtm_forecast_api "
                        "merge for remainder of this run"
                    )

            if api_merge_this_run:
                m, s, f = await self._merge_delivery_day_auto(
                    scheduled_at,
                    scheduled_date,
                    ctx,
                    api_by_block,
                    demand_by_block,
                    writable_set,
                )
            else:
                m, s, f = await self._merge_delivery_day_manual(
                    scheduled_at,
                    scheduled_date,
                    ctx,
                    csv_by_block,
                    api_by_block,
                    demand_by_block,
                    writable_set,
                )
            merged += m
            skipped += s
            failed += f

        merge_source = (
            FORECAST_SOURCE_RTM_API
            if (api_merge_this_run or switch_flipped)
            else FORECAST_SOURCE_CSV
        )
        logger.info(
            "IDF forecast merge complete source=%s merged=%d skipped=%d failed=%d "
            "switch_flipped=%s switch_prefers_api=%s",
            merge_source,
            merged,
            skipped,
            failed,
            switch_flipped,
            switch_prefers_api or switch_flipped,
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
