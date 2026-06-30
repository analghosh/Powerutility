"""
Shared IDF forecast merge: forecast_serve (rtm_forecast_api or csv) → demand_serve IDF.

Used by scheduler cron and processing switch API (processing Docker cannot import scheduler).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from pymongo import UpdateOne

from libs.constants.idf_forecast import (
    IDF_FORECAST_SWITCH_AUTO_FALLBACK_MESSAGE_KEY,
    IDF_FORECAST_SWITCH_KEY,
    IDF_FORECAST_SWITCH_LAST_RESET_KEY,
    MANUAL_AUTO_FLIP_ACTIVE_SESSION_CSV_MESSAGE,
    parse_switch_use_rtm_api,
    switch_para_value,
)
from libs.helpers.idf_forecast_audit import switch_change_audit_fields
from libs.helpers.idf_forecast_session import (
    index_forecast_docs,
    merge_session_ids_for_delivery_date,
    resolve_session_merge_ops_api_only,
    resolve_session_merge_ops_csv_manual,
    session_has_csv_forecast_present,
    session_to_time_blocks,
    time_block_to_session_id,
    writable_blocks_for_date,
)
from libs.helpers.idf_forecast_switch_validation import active_session_csv_requires_auto_flip
from libs.helpers.idf_final_forecast_freeze import build_idf_final_bulk_ops
from libs.helpers.rtm_forecast_block_filter import (
    DEFAULT_CRON_TIMEZONE,
    DEFAULT_MIN_AHEAD_BLOCKS,
    effective_active_session_for_delivery_date,
    rtm_forecast_run_context,
    target_scheduled_dates_for_rtm,
)
from libs.models.forecast_serve_entity import FORECAST_SOURCE_CSV, FORECAST_SOURCE_RTM_API

logger = logging.getLogger(__name__)

RTM_SOURCE_FORECAST = "rtm_forecast_api"

AuditCallback = Callable[..., None]


class IdfForecastMergeService:
    """Publish active forecast source from forecast_serve into demand_serve (IDF)."""

    def __init__(
        self,
        parameter_repository: Any,
        forecast_repository: Any,
        demand_serve_collection: Any,
        audit_callback: Optional[AuditCallback] = None,
    ):
        self.parameter_repository = parameter_repository
        self.forecast_repository = forecast_repository
        self.demand_serve_collection = demand_serve_collection
        self.audit_callback = audit_callback

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

    def _audit_switch_change(
        self,
        action: str,
        message: str,
        previous: Dict[str, Any],
        new_state: Dict[str, Any],
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.audit_callback:
            return
        try:
            self.audit_callback(
                action=action,
                message=message,
                previous=previous,
                new_state=new_state,
                extra=extra or {},
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
        cursor = self.demand_serve_collection.find(
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
    def _forecast_revision_for_block(
        time_block: int,
        source_forecast: str,
        csv_by_block: Dict[int, Dict[str, Any]],
        api_by_block: Dict[int, Dict[str, Any]],
    ) -> Optional[int]:
        block_map = csv_by_block if source_forecast == FORECAST_SOURCE_CSV else api_by_block
        doc = block_map.get(int(time_block)) or {}
        rev = doc.get("forecast_revision")
        if rev is not None:
            return int(rev)
        b1, b2 = session_to_time_blocks(time_block_to_session_id(int(time_block)))
        for partner in (b1, b2):
            if partner == int(time_block):
                continue
            partner_rev = (block_map.get(partner) or {}).get("forecast_revision")
            if partner_rev is not None:
                return int(partner_rev)
        return None

    @staticmethod
    def _build_merge_update_op(
        scheduled_at: datetime,
        time_block: int,
        forecast_mw: float,
        forecasted_frequency: float,
        source_forecast: str,
        demand_by_block: Dict[int, Dict[str, Any]],
        now: datetime,
        forecast_revision: Optional[int] = None,
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
        set_fields: Dict[str, Any] = {
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
        }
        if forecast_revision is not None:
            set_fields["forecast_revision"] = forecast_revision
        return UpdateOne(
            filter_doc,
            {
                "$set": set_fields,
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
        result = await self.demand_serve_collection.bulk_write(
            bulk_ops, ordered=False
        )
        return int(result.modified_count) + len(result.upserted_ids)

    async def _sync_idf_final_for_delivery_day(
        self,
        scheduled_at: datetime,
        scheduled_date: date,
        ctx: Dict[str, Any],
        api_by_block: Dict[int, Dict[str, Any]],
    ) -> int:
        if not api_by_block:
            return 0

        existing_docs = await self.forecast_repository.find_idf_final_by_scheduled_at(
            scheduled_at
        )
        existing_by_block = index_forecast_docs(existing_docs)
        now = datetime.now()
        active_sid = effective_active_session_for_delivery_date(
            scheduled_date,
            ctx["today_ist"],
            ctx["now_local"],
            ctx["after_evening_cutover"],
        )
        bulk_ops = build_idf_final_bulk_ops(
            scheduled_at=scheduled_at,
            scheduled_date=scheduled_date,
            today_ist=ctx["today_ist"],
            after_evening_cutover=ctx["after_evening_cutover"],
            active_session_id=active_sid,
            api_by_block=api_by_block,
            existing_by_block=existing_by_block,
            now=now,
        )
        if not bulk_ops:
            return 0
        result = await self.forecast_repository.bulk_upsert_idf_final(bulk_ops)
        return int(result.get("upserted", 0) or 0) + int(result.get("modified", 0) or 0)

    @staticmethod
    def _is_primary_delivery_date_for_auto_flip(
        scheduled_date: date,
        ctx: Dict[str, Any],
    ) -> bool:
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
        _ = api_by_block, demand_by_block, writable_set
        if not self._is_primary_delivery_date_for_auto_flip(scheduled_date, ctx):
            return False
        active_sid = effective_active_session_for_delivery_date(
            scheduled_date,
            ctx["today_ist"],
            ctx["now_local"],
            ctx["after_evening_cutover"],
        )
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

        for session_id in merge_session_ids_for_delivery_date(
            scheduled_date,
            ctx["today_ist"],
            ctx["after_evening_cutover"],
            ctx["now_local"],
        ):
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
                            forecast_revision=self._forecast_revision_for_block(
                                time_block,
                                source_label,
                                csv_by_block,
                                api_by_block,
                            ),
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

        for session_id in merge_session_ids_for_delivery_date(
            scheduled_date,
            ctx["today_ist"],
            ctx["after_evening_cutover"],
            ctx["now_local"],
        ):
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
                            forecast_revision=self._forecast_revision_for_block(
                                time_block,
                                source_label,
                                {},
                                api_by_block,
                            ),
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

    async def run_merge(
        self,
        skip_daily_reset: bool = False,
        skip_auto_flip: bool = False,
        tz_name: Optional[str] = None,
        min_ahead_blocks: Optional[int] = None,
    ) -> Tuple[int, int, int]:
        tz = tz_name or DEFAULT_CRON_TIMEZONE
        min_ahead = max(
            0,
            int(min_ahead_blocks if min_ahead_blocks is not None else DEFAULT_MIN_AHEAD_BLOCKS),
        )
        ctx = rtm_forecast_run_context(tz, min_ahead)
        today_ist = ctx["today_ist"]

        if not skip_daily_reset:
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
        idf_final_synced = 0

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

            if not api_merge_this_run and not skip_auto_flip:
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

            try:
                idf_final_synced += await self._sync_idf_final_for_delivery_day(
                    scheduled_at,
                    scheduled_date,
                    ctx,
                    api_by_block,
                )
            except Exception as freeze_err:
                logger.warning(
                    "IDF_final sync failed for %s: %s", scheduled_at, freeze_err
                )

        merge_source = (
            FORECAST_SOURCE_RTM_API
            if (api_merge_this_run or switch_flipped)
            else FORECAST_SOURCE_CSV
        )
        logger.info(
            "IDF forecast merge complete source=%s merged=%d skipped=%d failed=%d "
            "idf_final_synced=%d switch_flipped=%s switch_prefers_api=%s "
            "skip_daily_reset=%s skip_auto_flip=%s",
            merge_source,
            merged,
            skipped,
            failed,
            idf_final_synced,
            switch_flipped,
            switch_prefers_api or switch_flipped,
            skip_daily_reset,
            skip_auto_flip,
        )
        return merged, skipped, failed


def merge_result_status(merged: int, skipped: int, failed: int) -> str:
    """Derive merge outcome status for API responses."""
    if failed > 0:
        return "partial" if merged > 0 else "error"
    return "success"
