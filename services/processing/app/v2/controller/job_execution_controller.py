"""
Job Execution Controller
Handles HTTP request/response for job execution monitoring
"""

import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException, status

from ..services.job_execution_service import job_execution_service

logger = logging.getLogger(__name__)


class JobExecutionController:
    """Controller for job execution endpoints"""
    
    def __init__(self):
        self.service = job_execution_service
    
    async def get_job_executions(
        self,
        page: int,
        page_size: int,
        job_id: Optional[str] = None,
        job_type: Optional[str] = None,
        status_filter: Optional[str] = None,
        job_name: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get paginated job executions with filters"""
        try:
            result = await self.service.get_job_executions_paginated(
                page=page,
                page_size=page_size,
                job_id=job_id,
                job_type=job_type,
                status_filter=status_filter,
                job_name=job_name,
                start_date=start_date,
                end_date=end_date
            )
            return result
            
        except ValueError as e:
            # Validation errors (e.g., invalid date format)
            logger.warning(f"Validation error in get_job_executions: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
        except Exception as e:
            logger.error(f"Error in get_job_executions: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch job executions: {str(e)}"
            )
    
    async def get_job_execution_detail(self, execution_id: str) -> Dict[str, Any]:
        """Get detailed information about a specific job execution"""
        try:
            result = await self.service.get_job_execution_detail(execution_id)
            
            if result is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Job execution with ID '{execution_id}' not found"
                )
            
            return result
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error in get_job_execution_detail: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch job execution detail: {str(e)}"
            )
    
    async def get_jobs_summary(self) -> Dict[str, Any]:
        """Get summary of all jobs with their latest execution status"""
        try:
            result = await self.service.get_jobs_summary()
            return result
            
        except Exception as e:
            logger.error(f"Error in get_jobs_summary: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch jobs summary: {str(e)}"
            )


# Singleton instance
job_execution_controller = JobExecutionController()
