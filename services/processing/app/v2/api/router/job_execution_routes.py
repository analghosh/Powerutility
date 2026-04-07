"""
Job Execution Monitoring Router
Provides endpoints to monitor scheduled job executions from audit database
"""

from fastapi import APIRouter, Request, HTTPException, status, Depends, Query
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from datetime import datetime
import logging

from ...controller.job_execution_controller import job_execution_controller
from ...dependencies import get_user_context
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger

router = APIRouter(
    prefix="/job-executions",
    tags=["Job Executions"]
)

logger = logging.getLogger(__name__)


class JobExecutionItem(BaseModel):
    """Single job execution record"""
    execution_id: str = Field(..., description="Unique execution ID")
    job_id: str = Field(..., description="Job ID reference")
    job_name: str = Field(..., description="Job name")
    job_type: str = Field(..., description="Job type")
    schedule: str = Field(..., description="Cron schedule expression")
    timezone: str = Field(..., description="Job timezone")
    execution_start: datetime = Field(..., description="Execution start time")
    execution_end: Optional[datetime] = Field(None, description="Execution end time")
    duration_seconds: Optional[float] = Field(None, description="Execution duration in seconds")
    status: str = Field(..., description="Execution status (success/failed)")
    error_message: Optional[str] = Field(None, description="Error message if failed")
    next_execution: Optional[datetime] = Field(None, description="Next scheduled execution time")
    
    class Config:
        json_schema_extra = {
            "example": {
                "execution_id": "limit_values_approval_processing_20251130_133800",
                "job_id": "6918b6946b9d7cc614c1d692",
                "job_name": "Limit Values Approval Processing",
                "job_type": "limit_values_approval_processing",
                "schedule": "* * * * *",
                "timezone": "Asia/Kolkata",
                "execution_start": "2025-11-30T13:38:00.468Z",
                "execution_end": "2025-11-30T13:38:00.514Z",
                "duration_seconds": 0.046,
                "status": "success",
                "error_message": None,
                "next_execution": "2025-11-30T13:39:00.000Z"
            }
        }


class JobExecutionListResponse(BaseModel):
    """Paginated response for job executions"""
    success: bool = True
    data: List[JobExecutionItem]
    pagination: Dict[str, Any] = Field(..., description="Pagination metadata")
    
    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "data": [],
                "pagination": {
                    "page": 1,
                    "page_size": 20,
                    "total_records": 100,
                    "total_pages": 5
                }
            }
        }


# IMPORTANT: More specific routes must come BEFORE generic path parameter routes
# Otherwise /{execution_id} will match everything including "/summary/all"

@router.get(
    "/summary/all",
    status_code=status.HTTP_200_OK,
    summary="Get summary of all jobs with their latest execution status",
    description="Retrieve a summary of all scheduled jobs with their latest execution status and next scheduled time"
)
async def get_jobs_summary(
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get summary of all jobs with their latest execution status"""
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Call controller
        result = await job_execution_controller.get_jobs_summary()
        
        # Log the operation
        audit_logger.log_system_operation(
            service="processing",
            operation="jobs_summary",
            message=f"Fetched jobs summary (total_jobs={result['total_jobs']})",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            metadata={
                "total_jobs": result["total_jobs"]
            }
        )
        
        return {
            "success": True,
            "data": result["data"],
            "total_jobs": result["total_jobs"]
        }
        
    except Exception as e:
        logger.error(f"Error fetching jobs summary: {e}", exc_info=True)
        
        audit_logger.log_system_operation(
            service="processing",
            operation="jobs_summary_error",
            message=f"Error fetching jobs summary: {str(e)}",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            metadata={
                "error": str(e)
            }
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch jobs summary: {str(e)}"
        )


@router.get(
    "",
    response_model=JobExecutionListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get job execution history with pagination",
    description="""
    Retrieve paginated list of job executions from audit database.
    Shows execution start/end times, status, and next scheduled execution.
    """
)
async def get_job_executions(
    request: Request,
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    page_size: int = Query(20, ge=1, le=100, description="Number of records per page"),
    job_id: Optional[str] = Query(None, description="Filter by job ID"),
    job_type: Optional[str] = Query(None, description="Filter by job type"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status (success/failed)"),
    job_name: Optional[str] = Query(None, description="Filter by job name (partial match)"),
    start_date: Optional[str] = Query(None, description="Filter by start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Filter by end date (YYYY-MM-DD)"),
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get paginated job execution history"""
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Call controller
        result = await job_execution_controller.get_job_executions(
            page=page,
            page_size=page_size,
            job_id=job_id,
            job_type=job_type,
            status_filter=status_filter,
            job_name=job_name,
            start_date=start_date,
            end_date=end_date
        )
        
        # Convert to response items
        response_items = [JobExecutionItem(**item) for item in result["data"]]
        
        # Log the operation
        audit_logger.log_system_operation(
            service="processing",
            operation="job_executions_list",
            message=f"Fetched job executions list (page={page}, size={page_size}, total={result['pagination']['total_records']})",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            metadata={
                "page": page,
                "page_size": page_size,
                "total_records": result["pagination"]["total_records"]
            }
        )
        
        return JobExecutionListResponse(
            success=True,
            data=response_items,
            pagination=result["pagination"]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching job executions: {e}", exc_info=True)
        
        audit_logger.log_system_operation(
            service="processing",
            operation="job_executions_list_error",
            message=f"Error fetching job executions: {str(e)}",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            metadata={
                "error": str(e),
                "page": page,
                "page_size": page_size
            }
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch job executions: {str(e)}"
        )


@router.get(
    "/{execution_id}",
    status_code=status.HTTP_200_OK,
    summary="Get detailed information about a specific job execution",
    description="Retrieve detailed execution information including logs and metadata"
)
async def get_job_execution_detail(
    execution_id: str,
    request: Request,
    user_context: Dict[str, Any] = Depends(get_user_context),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get detailed information about a specific job execution"""
    user_id = user_context.get("user_id", "anonymous")
    username = user_context.get("username", "anonymous")
    correlation_id = user_context.get("correlation_id", "unknown")
    
    try:
        # Call controller
        result = await job_execution_controller.get_job_execution_detail(execution_id)
        
        # Log the operation
        audit_logger.log_system_operation(
            service="processing",
            operation="job_execution_detail",
            message=f"Fetched job execution detail for: {execution_id}",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            metadata={
                "execution_id": execution_id
            }
        )
        
        return {
            "success": True,
            "data": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching job execution detail: {e}", exc_info=True)
        
        audit_logger.log_system_operation(
            service="processing",
            operation="job_execution_detail_error",
            message=f"Error fetching job execution detail: {str(e)}",
            user_id=user_id,
            username=username,
            correlation_id=correlation_id,
            metadata={
                "error": str(e),
                "execution_id": execution_id
            }
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch job execution detail: {str(e)}"
        )
