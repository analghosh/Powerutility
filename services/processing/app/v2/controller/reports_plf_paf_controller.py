"""Controller for reports-plf-paf API."""

import logging
from typing import Any, Dict, Optional, Tuple

from libs.helpers.reports_forecast_format import REPORTS_FORECAST_FORMAT_CSV

from ..services.reports_plf_paf_service import reports_plf_paf_service

logger = logging.getLogger(__name__)


class ReportsPlfPafController:
    async def get_report(
        self,
        from_block: int,
        to_block: int,
        schedule_date: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        response_format: str = "json",
    ) -> Dict[str, Any]:
        return await reports_plf_paf_service.get_reports_plf_paf(
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
            response_format=response_format,
        )

    async def download_csv(
        self,
        from_block: int,
        to_block: int,
        schedule_date: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Tuple[str, str]:
        result = await reports_plf_paf_service.get_reports_plf_paf(
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
            response_format=REPORTS_FORECAST_FORMAT_CSV,
        )
        return result["csv_export"]["content"], result["csv_export"]["filename"]

    async def get_blocks(
        self,
        plant_id: str,
        from_block: int,
        to_block: int,
        schedule_date: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await reports_plf_paf_service.get_plant_blocks(
            plant_id=plant_id,
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
        )


reports_plf_paf_controller = ReportsPlfPafController()
