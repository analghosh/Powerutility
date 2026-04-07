"""
Demand Manual V2 Controller
Handles HTTP requests for manual demand operations.
"""

import logging
from typing import Dict, Any, List
from fastapi import HTTPException, status

from ..services.demand_manual_services import demand_manual_service

logger = logging.getLogger(__name__)


class DemandManualController:
    """
    Controller for Manual Demand V2 operations.
    Handles HTTP requests and delegates business logic to service layer.
    """

    def __init__(self):
        self.service = demand_manual_service

    async def update_manual_demand(
        self,
        demand_data: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Update demand serve records with manual data.
        
        Args:
            demand_data: List of demand data items.
        
        Returns:
            Dict containing update results.
        """
        try:
            return await self.service.process_manual_demand_data(demand_data)
        except Exception as e:
            logger.error(f"Error in update_manual_demand: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update manual demand data: {str(e)}"
            )
    
    async def upload_temp_demand_csv(
        self,
        file_contents: bytes,
        original_filename: str,
    ) -> Dict[str, Any]:
        """
        Persist CSV to disk, parse rows, and bulk-upsert temp demand records.
        """
        try:
            return await self.service.process_temp_demand_csv(file_contents, original_filename)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        except Exception as e:
            logger.error(f"Error in upload_temp_demand_csv: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to process CSV upload: {str(e)}"
            )

    async def update_temp_manual_demand(
        self,
        demand_data: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Update demand serve records with manual data.
        
        Args:
            demand_data: List of demand data items.
        
        Returns:
            Dict containing update results.
        """
        try:
            return await self.service.process_manual_temp_demand_data(demand_data)
        except Exception as e:
            logger.error(f"Error in update_manual_demand: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to update manual demand data: {str(e)}"
            ) 

    async def export_temp_demand_csv(self) -> str:
        try:
            return await self.service.export_temp_demand_csv()
        except Exception as e:
            logger.error(f"Error in export_temp_demand_csv: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to export temp demand CSV: {str(e)}"
            )

# Global instance
demand_manual_controller = DemandManualController()
