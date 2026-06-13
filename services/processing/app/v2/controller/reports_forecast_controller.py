"""Controller for reports-forecast API."""

import logging
from typing import Any, Dict, Optional, Tuple

from ..services.reports_forecast_service import reports_forecast_service

logger = logging.getLogger(__name__)


class ReportsForecastController:
    async def get_report(
        self,
        from_block: int,
        to_block: int,
        schedule_date: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await reports_forecast_service.get_reports_forecast(
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
        )

    async def download_csv(
        self,
        from_block: int,
        to_block: int,
        schedule_date: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Tuple[str, str]:
        result = await reports_forecast_service.get_reports_forecast(
            schedule_date=schedule_date,
            from_date=from_date,
            to_date=to_date,
            from_block=from_block,
            to_block=to_block,
        )
        csv_content = reports_forecast_service.generate_csv(result.get("records", []))
        filename = (
            f"reports_forecast_{result['query']['from_date']}_to_{result['query']['to_date']}"
            f"_blocks_{from_block}-{to_block}.csv"
        )
        return csv_content, filename


reports_forecast_controller = ReportsForecastController()
