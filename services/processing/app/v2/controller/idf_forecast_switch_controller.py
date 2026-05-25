"""Controller for IDF forecast switch API."""

import logging
from typing import Any, Dict, Optional

from ..services.idf_forecast_switch_service import idf_forecast_switch_service

logger = logging.getLogger(__name__)


class IdfForecastSwitchController:
    async def get_switch(self) -> Dict[str, Any]:
        return await idf_forecast_switch_service.get_switch()

    async def set_switch(self, use_rtm_api: bool, updated_by: Optional[str] = None) -> Dict[str, Any]:
        return await idf_forecast_switch_service.set_switch(use_rtm_api, updated_by=updated_by)


idf_forecast_switch_controller = IdfForecastSwitchController()
