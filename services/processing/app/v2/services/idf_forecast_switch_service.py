"""
IDF forecast switch service — read/update idf_forecast_switch parameter.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from libs.constants.idf_forecast import (
    IDF_FORECAST_SWITCH_AUTO_FALLBACK_MESSAGE_KEY,
    IDF_FORECAST_SWITCH_KEY,
    parse_switch_use_rtm_api,
    switch_para_value,
)
from libs.helpers.idf_forecast_switch_validation import (
    MANUAL_SWITCH_BLOCKED_MESSAGE,
    build_manual_switch_validation_context,
    scheduled_at_midnight,
    validate_csv_coverage_for_manual_switch,
)
from libs.helpers.rtm_forecast_block_filter import target_scheduled_dates_for_rtm
from libs.models.forecast_serve_entity import FORECAST_SOURCE_CSV, FORECAST_SOURCE_RTM_API
from ...core.config import settings
from ...repository.idf_forecast_parameter_repository import (
    idf_forecast_parameter_repository,
)
from ..repository.forecast_serve_repository import forecast_serve_repository

logger = logging.getLogger(__name__)


class ManualSwitchNotAllowedError(Exception):
    """Raised when switching to Manual mode is blocked due to missing CSV data."""

    def __init__(self, message: str = MANUAL_SWITCH_BLOCKED_MESSAGE):
        self.message = message
        super().__init__(message)


class IdfForecastSwitchService:
    def __init__(self):
        self.parameter_repository = idf_forecast_parameter_repository
        self.forecast_repository = forecast_serve_repository

    async def _get_auto_fallback_user_message(self) -> Optional[str]:
        doc = await self.parameter_repository.get_by_key(
            IDF_FORECAST_SWITCH_AUTO_FALLBACK_MESSAGE_KEY
        )
        if not doc:
            return None
        msg = doc.get("para_value")
        return str(msg).strip() if msg else None

    async def _clear_auto_fallback_user_message(self) -> None:
        await self.parameter_repository.upsert_by_key(
            IDF_FORECAST_SWITCH_AUTO_FALLBACK_MESSAGE_KEY,
            "",
            extra_set={"trigger": None, "changed_by": None},
        )

    async def get_switch(self) -> Dict[str, Any]:
        await self.parameter_repository.ensure_idf_forecast_switch_default()
        doc = await self.parameter_repository.get_by_key(IDF_FORECAST_SWITCH_KEY)
        use_rtm_api = parse_switch_use_rtm_api((doc or {}).get("para_value"))
        para_value = switch_para_value(use_rtm_api)
        active_source = FORECAST_SOURCE_RTM_API if use_rtm_api else FORECAST_SOURCE_CSV
        updated_at = (doc or {}).get("updated_at")
        auto_fallback_message = await self._get_auto_fallback_user_message()
        result = {
            "use_rtm_api": use_rtm_api,
            "active_source": active_source,
            "para_key": IDF_FORECAST_SWITCH_KEY,
            "para_value": para_value,
            "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else updated_at,
        }
        if auto_fallback_message:
            result["auto_fallback_message"] = auto_fallback_message
        return result

    async def _validate_manual_switch(self) -> None:
        tz_name = getattr(settings, "CRON_TIMEZONE", None) or "Asia/Kolkata"
        min_ahead = max(
            0,
            int(getattr(settings, "RTM_FORECAST_MIN_AHEAD_BLOCKS", 5)),
        )
        ctx = build_manual_switch_validation_context(tz_name, min_ahead)
        target_dates = target_scheduled_dates_for_rtm(
            ctx["today_ist"],
            ctx["after_evening_cutover"],
            ctx.get("include_tomorrow_forecast", False),
        )

        csv_docs_by_date = {}
        for target_date in target_dates:
            scheduled_at = scheduled_at_midnight(target_date)
            docs = await self.forecast_repository.find_by_scheduled_at_and_source(
                scheduled_at, FORECAST_SOURCE_CSV
            )
            csv_docs_by_date[target_date] = docs

        ok, message = validate_csv_coverage_for_manual_switch(
            csv_docs_by_date,
            ctx["today_ist"],
            ctx["now_local"],
            ctx["after_evening_cutover"],
            ctx.get("include_tomorrow_forecast", False),
        )
        if not ok:
            raise ManualSwitchNotAllowedError(message)

    async def _trigger_idf_forecast_merge(self) -> Dict[str, Any]:
        """Call scheduler to merge forecast_serve into demand_serve for the active switch."""
        url = (
            f"{settings.SCHEDULER_SERVICE_URL.rstrip('/')}"
            "/api/v1/scheduler/idf-forecast-merge/trigger"
        )
        timeout = float(getattr(settings, "SCHEDULER_IDF_FORECAST_MERGE_TIMEOUT", 60))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url)
            response.raise_for_status()
            return response.json()

    async def set_switch(self, use_rtm_api: bool, updated_by: Optional[str] = None) -> Dict[str, Any]:
        previous = await self.get_switch()

        if not use_rtm_api and previous.get("use_rtm_api"):
            await self._validate_manual_switch()

        para_value = switch_para_value(use_rtm_api)
        extra: Dict[str, Any] = {}
        if updated_by:
            extra["updated_by"] = updated_by
        await self.parameter_repository.upsert_by_key(
            IDF_FORECAST_SWITCH_KEY, para_value, extra_set=extra or None
        )
        await self._clear_auto_fallback_user_message()
        logger.info(
            "IDF forecast switch set to use_rtm_api=%s (para_value=%s) by %s "
            "(was use_rtm_api=%s active_source=%s)",
            use_rtm_api,
            para_value,
            updated_by or "system",
            previous.get("use_rtm_api"),
            previous.get("active_source"),
        )
        result = await self.get_switch()
        result["updated_by"] = updated_by
        result["previous_use_rtm_api"] = previous.get("use_rtm_api")
        result["previous_active_source"] = previous.get("active_source")
        result["previous_para_value"] = previous.get("para_value")

        if previous.get("use_rtm_api") != use_rtm_api:
            try:
                result["merge"] = await self._trigger_idf_forecast_merge()
            except Exception as merge_err:
                logger.error(
                    "IDF forecast merge failed after switch update use_rtm_api=%s: %s",
                    use_rtm_api,
                    merge_err,
                    exc_info=True,
                )
                result["merge"] = {
                    "status": "error",
                    "message": str(merge_err),
                }

        return result


idf_forecast_switch_service = IdfForecastSwitchService()
