
"""
DAM Scheduling Controller for V2 API
Handles HTTP requests and delegates to the service layer.
"""

import logging
from typing import Any, Dict, Optional, List
from fastapi import HTTPException, status, UploadFile

from ..services.dam_scheduling_service import dam_scheduling_service

logger = logging.getLogger(__name__)


class DamSchedulingController:
    """
    Controller for DAM scheduling operations.
    Handles HTTP requests and delegates to the service layer.
    """
    
    def __init__(self):
        self.service = dam_scheduling_service

    async def create_dam_scheduling(self, scheduling_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new DAM scheduling record.
        
        Args:
            scheduling_data: Dictionary containing scheduling data
            
        Returns:
            Dictionary with creation result
        """
        return await self.service.create_dam_scheduling(scheduling_data)

    async def get_dam_scheduling_by_id(self, scheduling_id: str) -> Dict[str, Any]:
        """
        Get a DAM scheduling record by ID.
        
        Args:
            scheduling_id: String ID of the scheduling record
            
        Returns:
            Dictionary containing the scheduling record
        """
        return await self.service.get_dam_scheduling_by_id(scheduling_id)

    async def get_dam_scheduling_list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        page_size: int = 50,
        sort_by: Optional[str] = None,
        sort_order: str = "desc"
    ) -> Dict[str, Any]:
        """
        Get paginated list of DAM scheduling records.
        
        Args:
            filters: Optional filters to apply
            page: Page number
            page_size: Number of records per page
            sort_by: Field to sort by
            sort_order: Sort order (asc/desc)
            
        Returns:
            Dictionary containing paginated results
        """
        return await self.service.get_dam_scheduling_list(
            filters=filters,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order
        )

    async def update_dam_scheduling(self, scheduling_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update a DAM scheduling record.
        
        Args:
            scheduling_id: String ID of the scheduling record
            update_data: Dictionary containing fields to update
            
        Returns:
            Dictionary with update result
        """
        return await self.service.update_dam_scheduling(scheduling_id, update_data)

    async def delete_dam_scheduling(self, scheduling_id: str) -> Dict[str, Any]:
        """
        Delete a DAM scheduling record.
        
        Args:
            scheduling_id: String ID of the scheduling record
            
        Returns:
            Dictionary with deletion result
        """
        return await self.service.delete_dam_scheduling(scheduling_id)

    async def get_dam_scheduling_by_plant_id(
        self, 
        plant_id: str, 
        start_date: Optional[str] = None, 
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get DAM scheduling records for a specific plant with date range filtering.
        
        Args:
            plant_id: Plant identifier
            start_date: Optional start date in YYYY-MM-DD format (defaults to today)
            end_date: Optional end date in YYYY-MM-DD format (defaults to tomorrow)
            
        Returns:
            Dictionary containing scheduling records for the plant
        """
        return await self.service.get_dam_scheduling_by_plant_id(plant_id, start_date, end_date)

    async def process_scheduling_file(self, file: UploadFile, actor: Dict[str, Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process uploaded DAM scheduling file.
        
        Args:
            file: Uploaded file
            actor: User information
            request_info: Request metadata
            
        Returns:
            Dictionary with processing result
        """
        return await self.service.process_scheduling_file(file, actor, request_info)

    async def bulk_upsert_dam_scheduling(self, scheduling_records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Bulk upsert multiple DAM scheduling records.
        
        Args:
            scheduling_records: List of scheduling data dictionaries
            
        Returns:
            Dictionary with upsert result
        """
        return await self.service.bulk_upsert_dam_scheduling(scheduling_records)

    def get_csv_format_template(self) -> str:
        """
        Get CSV format template with headers only.
        
        Returns:
            CSV template string
        """
        return self.service.get_csv_format_template()

    async def validate_plants(self, plant_ids: List[str]) -> Dict[str, Any]:
        """
        Validate multiple plant IDs against plant_master collection.
        
        Args:
            plant_ids: List of plant IDs to validate
            
        Returns:
            Dictionary with validation results
        """
        return await self.service.validate_plants(plant_ids)

    async def commit_staged_scheduling(self, user_id: str) -> Dict[str, Any]:
        """
        Commit staged DAM scheduling data to main collection.
        
        Args:
            user_id: User ID to identify staged data
            
        Returns:
            Dictionary with commit result
        """
        return await self.service.commit_staged_scheduling(user_id)

    async def get_staged_preview(self, user_id: str) -> Dict[str, Any]:
        """
        Get preview of staged DAM scheduling data (limited to 50 records).
        
        Args:
            user_id: User ID to identify staged data
            
        Returns:
            Dictionary with preview data
        """
        return await self.service.get_staged_preview(user_id)

    async def get_staged_data_paginated(
        self, 
        user_id: str, 
        page: int = 1, 
        page_size: int = 50
    ) -> Dict[str, Any]:
        """
        Get paginated staged DAM scheduling data for full review.
        
        Args:
            user_id: User ID to identify staged data
            page: Page number (1-based)
            page_size: Number of records per page
            
        Returns:
            Dictionary with paginated staged data
        """
        return await self.service.get_staged_data_paginated(user_id, page, page_size)

    async def cancel_staged_data(self, user_id: str) -> Dict[str, Any]:
        """
        Cancel/clear staged DAM scheduling data.
        
        Args:
            user_id: User ID to identify staged data
            
        Returns:
            Dictionary with cancellation result
        """
        return await self.service.cancel_staged_data(user_id)

    async def get_dam_scheduling_summary(
        self,
        start_date: str,
        end_date: str
    ) -> Dict[str, Any]:
        """
        Get DAM scheduling summary for a date range.
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            
        Returns:
            Dictionary containing summary statistics
        """
        return await self.service.get_dam_scheduling_summary(start_date, end_date)

    def schedule_by_date(self, schedule_date: Optional[str]) -> Dict[str, Any]:
        try:
            return self.service.schedule_by_date(schedule_date)
        except HTTPException:
            raise
        except Exception:
            logger.exception("schedule_by_date failed | schedule_date=%s", schedule_date)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch DAM schedule.")


dam_scheduling_controller = DamSchedulingController()