"""Controller for the URS Availed API."""

import logging
from typing import Any, Dict, List, Tuple

from ..services.urs_service import urs_service

logger = logging.getLogger(__name__)


class UrsController:
    async def get_urs_availed(
        self,
        schedule_date: str,
        from_block: int,
        to_block: int,
    ) -> Dict[str, Any]:
        return await urs_service.get_urs_availed(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
        )

    async def get_dates(self, limit: int = 60) -> List[Dict[str, Any]]:
        from ..repository.urs_repository import urs_repository

        return await urs_repository.dates_with_data(limit=limit)

    async def download_csv(
        self,
        schedule_date: str,
        from_block: int,
        to_block: int,
    ) -> Tuple[str, str]:
        report = await urs_service.get_urs_availed(
            schedule_date=schedule_date,
            from_block=from_block,
            to_block=to_block,
        )
        return (
            urs_service.generate_csv(report),
            urs_service.build_csv_filename(schedule_date, from_block, to_block),
        )


urs_controller = UrsController()
