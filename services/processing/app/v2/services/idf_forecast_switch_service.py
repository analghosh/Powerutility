"""
IDF forecast switch service — read/update idf_forecast_switch parameter.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

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
from libs.repository.forecast_serve_repository import (
    ForecastServeRepository as LibForecastServeRepository,
)
from libs.services.idf_forecast_merge_service import (
    IdfForecastMergeService,
    merge_result_status,
)
from ...core.config import settings
from ...repository.idf_forecast_parameter_repository import (
    idf_forecast_parameter_repository,
)
from ..repository.demand_serve_repository import DemandServeRepository
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
        self._demand_repository = DemandServeRepository()
        self._lib_forecast_repository = LibForecastServeRepository(
            mongodb_uri=settings.MONGODB_URI,
            database_name=settings.MONGODB_DATABASE,
            collection_name=settings.MONGODB_FORECAST_SERVE_COLLECTION,
        )
        self._merge_service = IdfForecastMergeService(
            parameter_repository=self.parameter_repository,
            forecast_repository=self._lib_forecast_repository,
            demand_serve_collection=self._demand_repository.collection,
        )

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

    async def validate_parameter_switch(
        self,
        previous_para_value: Any,
        new_para_value: Any,
    ) -> None:
        """Validate manual switch before persisting via generic parameters API."""
        previous_use_rtm = parse_switch_use_rtm_api(
            str(previous_para_value) if previous_para_value is not None else None
        )
        use_rtm_api = parse_switch_use_rtm_api(
            str(new_para_value) if new_para_value is not None else None
        )
        if not use_rtm_api and previous_use_rtm:
            await self._validate_manual_switch()

    async def merge_after_parameter_switch(
        self,
        new_para_value: Any,
        updated_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run instant demand_serve merge after idf_forecast_switch saved via parameters API."""
        use_rtm_api = parse_switch_use_rtm_api(
            str(new_para_value) if new_para_value is not None else None
        )
        await self._clear_auto_fallback_user_message()
        logger.info(
            "IDF forecast switch updated via parameters API use_rtm_api=%s (para_value=%s) by %s",
            use_rtm_api,
            switch_para_value(use_rtm_api),
            updated_by or "system",
        )
        return await self._run_merge_after_switch(use_rtm_api)

    async def _run_merge_after_switch(self, use_rtm_api: bool) -> Dict[str, Any]:
        tz_name = getattr(settings, "CRON_TIMEZONE", None) or "Asia/Kolkata"
        min_ahead = max(
            0,
            int(getattr(settings, "RTM_FORECAST_MIN_AHEAD_BLOCKS", 5)),
        )
        merged = skipped = failed = 0
        status = "error"
        try:
            merged, skipped, failed = await self._merge_service.run_merge(
                skip_daily_reset=True,
                skip_auto_flip=not use_rtm_api,
                tz_name=tz_name,
                min_ahead_blocks=min_ahead,
            )
            status = merge_result_status(merged, skipped, failed)
        except Exception as merge_err:
            logger.error(
                "IDF forecast merge after switch failed: %s", merge_err, exc_info=True
            )
        return {
            "merged": merged,
            "skipped": skipped,
            "failed": failed,
            "status": status,
        }

    async def set_switch(self, use_rtm_api: bool, updated_by: Optional[str] = None) -> Dict[str, Any]:
        previous = await self.get_switch()
        para_value = switch_para_value(use_rtm_api)

        if not use_rtm_api and previous.get("use_rtm_api"):
            await self.validate_parameter_switch(
                previous.get("para_value"),
                para_value,
            )

        extra: Dict[str, Any] = {}
        if updated_by:
            extra["updated_by"] = updated_by
        await self.parameter_repository.upsert_by_key(
            IDF_FORECAST_SWITCH_KEY, para_value, extra_set=extra or None
        )
        logger.info(
            "IDF forecast switch set to use_rtm_api=%s (para_value=%s) by %s "
            "(was use_rtm_api=%s active_source=%s)",
            use_rtm_api,
            para_value,
            updated_by or "system",
            previous.get("use_rtm_api"),
            previous.get("active_source"),
        )

        merge_result = await self.merge_after_parameter_switch(
            para_value,
            updated_by=updated_by,
        )

        result = await self.get_switch()
        result["updated_by"] = updated_by
        result["previous_use_rtm_api"] = previous.get("use_rtm_api")
        result["previous_active_source"] = previous.get("active_source")
        result["previous_para_value"] = previous.get("para_value")
        result["merge_result"] = merge_result
        return result


idf_forecast_switch_service = IdfForecastSwitchService()
