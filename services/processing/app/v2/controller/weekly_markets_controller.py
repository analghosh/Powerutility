"""Controller for the weekly-markets report API."""

import logging
from typing import Any, Dict, Optional, Tuple

from ..services.daily_markets_service import FORMAT_CSV
from ..services.weekly_markets_service import weekly_markets_service

logger = logging.getLogger(__name__)


class WeeklyMarketsController:
    async def get_report(
        self,
        year: Optional[int] = None,
        week: Optional[int] = None,
        market: Optional[str] = None,
        exchange: Optional[str] = None,
        response_format: str = "json",
    ) -> Dict[str, Any]:
        return await weekly_markets_service.get_weekly_markets(
            year=year,
            week=week,
            market=market,
            exchange=exchange,
            response_format=response_format,
        )

    async def download_csv(
        self,
        year: Optional[int] = None,
        week: Optional[int] = None,
        market: Optional[str] = None,
        exchange: Optional[str] = None,
    ) -> Tuple[str, str]:
        result = await weekly_markets_service.get_weekly_markets(
            year=year,
            week=week,
            market=market,
            exchange=exchange,
            response_format=FORMAT_CSV,
        )
        export = result["csv_export"]
        content = export.get("content") or weekly_markets_service.generate_csv(
            result["rows"]
        )
        filename = export.get("filename") or weekly_markets_service.build_csv_filename(
            result["query"]["year"], result["query"]["week"], result["query"]["market"]
        )
        return content, filename


weekly_markets_controller = WeeklyMarketsController()
