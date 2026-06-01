"""
RTM Scheduling Snapshot Controller (V2)
Standalone controller for RTM scheduling snapshot APIs.
"""

import logging
from typing import Dict, Any, Optional

from fastapi import HTTPException

from ..services.rtm_scheduling_snapshot_services import (
    DEFAULT_PAGE_SIZE,
    rtm_scheduling_snapshot_service,
)

logger = logging.getLogger(__name__)


class RTMSchedulingSnapshotController:
    def __init__(self):
        self.service = rtm_scheduling_snapshot_service

    async def get_rtm_scheduling_snapshot_data(
        self,
        session_id: Optional[str],
        schedule_date: str,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return await self.service.get_rtm_scheduling_snapshot_data(
                session_id=session_id,
                schedule_date=schedule_date,
                page=page,
                page_size=page_size,
                run_id=run_id,
                skip_pagination=False,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception("get_rtm_scheduling_snapshot_data failed in controller")
            raise HTTPException(status_code=500, detail="Failed to fetch RTM scheduling snapshot data.")

    async def get_rtm_scheduling_snapshot_excel(
        self,
        session_id: Optional[str],
        schedule_date: str,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return await self.service.get_rtm_scheduling_snapshot_excel(
                session_id=session_id,
                schedule_date=schedule_date,
                run_id=run_id,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception("get_rtm_scheduling_snapshot_excel failed in controller")
            raise HTTPException(status_code=500, detail="Failed to export RTM scheduling snapshot excel.")


rtm_scheduling_snapshot_controller = RTMSchedulingSnapshotController()
