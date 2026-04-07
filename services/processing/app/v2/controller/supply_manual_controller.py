"""
Supply Manual V2 Controller
Handles HTTP requests for manual supply operations.
"""

import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException, status

from ..services.supply_manual_services import supply_manual_service
from libs.enum.updated_flag import UpdatedFlag

logger = logging.getLogger(__name__)


class SupplyManualController:
    """
    Controller for Manual Supply V2 operations.
    Handles HTTP requests and delegates business logic to service layer.
    """

    def __init__(self):
        self.service = supply_manual_service

    async def get_manual_supply(
        self,
        plant_id: Optional[str] = None,
        dc_sg: str = "DC",
        process_type: str = "today",
        updated_flag: str = UpdatedFlag.OLD.value,
        time_block: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get manual supply data from dc_sg collection.
        
        Args:
            plant_id: Optional plant ID filter.
            dc_sg: DC or SG type. Defaults to "DC".
            process_type: "today" or "tomorrow". Defaults to "today".
            updated_flag: Filter by updated_flag. Defaults to "OLD".
            time_block: Optional time block filter.
        
        Returns:
            Dict containing dc_sg records.
        """
        try:
            # Normalize process_type (handle typo)
            if process_type in ["tommorow", "tommorrow"]:
                process_type = "today"
            
            return await self.service.get_manual_supply(
                plant_id=plant_id,
                dc_sg=dc_sg,
                process_type=process_type,
                updated_flag=updated_flag,
                time_block=time_block
            )
        except Exception as e:
            logger.error(f"Error in get_manual_supply: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch manual supply data: {str(e)}"
            )

    async def update_manual_supply(
        self,
        plant_id: Optional[str],
        dc_sg: Optional[str],
        process_type: Optional[str],
        time_block: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Update dc_sg records to volume=0, flag=REPLACED, sync to supply_serve.
        
        Args:
            plant_id: Plant ID to update (required).
            dc_sg: DC or SG type (required).
            process_type: "today" or "tomorrow" (required).
            time_block: Optional time block filter.
        
        Returns:
            Dict containing update results.
        
        Raises:
            HTTPException: If required parameters are missing.
        """
        # Validate required parameters
        if not plant_id or not dc_sg or not process_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing required query parameters: plant_id, dc_sg, and process_type are all required."
            )
        
        try:
            # Normalize process_type (handle typo)
            if process_type in ["tommorow", "tommorrow"]:
                process_type = "tomorrow"
            
            return await self.service.update_manual_supply(
                plant_id=plant_id,
                dc_sg=dc_sg,
                process_type=process_type,
                time_block=time_block
            )
        except Exception as e:
            logger.error(f"Error in update_manual_supply: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update manual supply data: {str(e)}"
            )


# Global instance
supply_manual_controller = SupplyManualController()
