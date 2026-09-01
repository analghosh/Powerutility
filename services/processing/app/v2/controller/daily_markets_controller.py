"""Controller for the daily-markets report API."""

import logging
from typing import Any, Dict, Optional, Tuple

from ..services.daily_markets_service import FORMAT_CSV, daily_markets_service

logger = logging.getLogger(__name__)


class DailyMarketsController:
    async def get_report(
        self,
        report_date: Optional[str] = None,
        market: Optional[str] = None,
        exchange: Optional[str] = None,
        response_format: str = "json",
    ) -> Dict[str, Any]:
        return await daily_markets_service.get_daily_markets(
            report_date=report_date,
            market=market,
            exchange=exchange,
            response_format=response_format,
        )

    async def download_csv(
        self,
        report_date: Optional[str] = None,
        market: Optional[str] = None,
        exchange: Optional[str] = None,
    ) -> Tuple[str, str]:
        result = await daily_markets_service.get_daily_markets(
            report_date=report_date,
            market=market,
            exchange=exchange,
            response_format=FORMAT_CSV,
        )
        csv_content = result["csv_export"].get("content") or daily_markets_service.generate_csv(
            result["trade_details"]
        )
        filename = result["csv_export"].get(
            "filename"
        ) or daily_markets_service.build_csv_filename(
            result["query"]["report_date"], result["query"]["market"]
        )
        return csv_content, filename


daily_markets_controller = DailyMarketsController()
