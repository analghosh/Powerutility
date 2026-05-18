"""
IDF forecast switch service — read/update idf_forecast_switch parameter.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from libs.constants.idf_forecast import (
    IDF_FORECAST_SWITCH_KEY,
    parse_switch_use_rtm_api,
    switch_para_value,
)
from libs.models.forecast_serve_entity import FORECAST_SOURCE_CSV, FORECAST_SOURCE_RTM_API
from ...repository.idf_forecast_parameter_repository import (
    idf_forecast_parameter_repository,
)

logger = logging.getLogger(__name__)


class IdfForecastSwitchService:
    def __init__(self):
        self.parameter_repository = idf_forecast_parameter_repository

    async def get_switch(self) -> Dict[str, Any]:
        await self.parameter_repository.ensure_idf_forecast_switch_default()
        doc = await self.parameter_repository.get_by_key(IDF_FORECAST_SWITCH_KEY)
        use_rtm_api = parse_switch_use_rtm_api((doc or {}).get("para_value"))
        para_value = switch_para_value(use_rtm_api)
        active_source = FORECAST_SOURCE_RTM_API if use_rtm_api else FORECAST_SOURCE_CSV
        updated_at = (doc or {}).get("updated_at")
        return {
            "use_rtm_api": use_rtm_api,
            "active_source": active_source,
            "para_key": IDF_FORECAST_SWITCH_KEY,
            "para_value": para_value,
            "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else updated_at,
        }

    async def set_switch(self, use_rtm_api: bool, updated_by: Optional[str] = None) -> Dict[str, Any]:
        previous = await self.get_switch()
        para_value = switch_para_value(use_rtm_api)
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
        result = await self.get_switch()
        result["updated_by"] = updated_by
        result["previous_use_rtm_api"] = previous.get("use_rtm_api")
        result["previous_active_source"] = previous.get("active_source")
        result["previous_para_value"] = previous.get("para_value")
        return result


idf_forecast_switch_service = IdfForecastSwitchService()
