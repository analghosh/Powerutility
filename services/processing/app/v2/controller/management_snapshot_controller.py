"""
Management Snapshot V2 Controller
Enhanced management snapshot controller with proper error handling and logging
"""

import logging
from typing import Dict, Any, Optional, List
from fastapi import HTTPException, status

from ..services.management_snapshot_services import management_snapshot_service

logger = logging.getLogger(__name__)


class ManagementSnapshotController:
    """
    Controller for Management Snapshot V2 operations.
    Handles HTTP requests and delegates business logic to service layer.
    """

    def __init__(self):
        self.management_snapshot_service = management_snapshot_service

    async def get_demand_supply_snapshot(
        self,
        schedule_date: Optional[str] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get demand-supply snapshot.
        
        Args:
            schedule_date: Optional date filter in YYYY-MM-DD format
            from_block: Start time block (1-96)
            to_block: End time block (1-96)
            
        Returns:
            Dict containing demand-supply snapshot data
        """
        try:
            logger.info(f"Fetching demand-supply snapshot for schedule_date: {schedule_date}, blocks: {from_block}-{to_block}")
            result = await self.management_snapshot_service.get_demand_supply_snapshot(
                schedule_date=schedule_date,
                from_block=from_block,
                to_block=to_block
            )
            return result
        except Exception as e:
            logger.error(f"Error in demand-supply snapshot controller: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch demand-supply snapshot: {str(e)}"
            )


    async def get_iex_dam_rtm_snapshot(
        self,
        schedule_date: Optional[str],
        from_block: Optional[int],
        to_block: Optional[int],
    ) -> Dict[str, Any]:
        try:
            return await self.management_snapshot_service.iex_dam_rtm_snapshot(
                schedule_date=schedule_date,
                from_block=from_block,
                to_block=to_block,
            )
        except Exception as e:
            logger.exception("iex_dam_rtm_snapshot failed in controller")
            raise HTTPException(status_code=500, detail="Failed to fetch IEX DAM/RTM snapshot.")

    async def get_variable_cost_snapshot(
        self,
        schedule_date: Optional[str],
        from_block: Optional[int],
        to_block: Optional[int],
        exchange: Optional[str],
    ) -> Dict[str, Any]:
        try:
            return await self.management_snapshot_service.variable_cost_snapshot(
                schedule_date=schedule_date,
                from_block=from_block,
                to_block=to_block,
                exchange=exchange,
            )
        except Exception as e:
            logger.exception("variable_cost_snapshot failed in controller")
            raise HTTPException(status_code=500, detail="Failed to fetch variable cost snapshot.")

    async def get_variable_cost_snapshot_range(
        self,
        date_from: str,
        date_to: str,
        from_block: Optional[int],
        to_block: Optional[int],
        exchange: Optional[str],
    ) -> List[Dict[str, Any]]:
        try:
            return await self.management_snapshot_service.variable_cost_snapshot_range(
                date_from=date_from,
                date_to=date_to,
                from_block=from_block,
                to_block=to_block,
                exchange=exchange,
            )
        except Exception as e:
            logger.exception("variable_cost_snapshot_range failed in controller")
            raise HTTPException(status_code=500, detail="Failed to fetch variable cost snapshot range.")



# Global instance
management_snapshot_controller = ManagementSnapshotController()
