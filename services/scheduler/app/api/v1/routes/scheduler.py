"""
Scheduler API routes
Essential endpoints for job management and monitoring
Clean Architecture Implementation
"""

from fastapi import APIRouter, HTTPException, status, Path, Query, Depends
from typing import Dict, Any, List, Optional
import sys
import os

# Import DTOs
from ....schemas.scheduler_dto import (
    SchedulerStatusResponse,
    JobManagementResponse,
    JobExecutionHistoryResponse,
    ReconciliationResponse,
    AllJobsResponse
)

# Import dependency injection container
from ....infrastructure.di.container import container

# Import audit logger
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger

router = APIRouter(prefix="/scheduler", tags=["Job Scheduling"])


@router.get("/status", response_model=SchedulerStatusResponse)
async def get_scheduler_status(audit_logger: AuditLogger = Depends(get_audit_logger)):
    """
    Get scheduler status endpoint
    
    Returns:
        Current scheduler status and job counts
    """
    try:
        # Log user action for scheduler_status
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="scheduler_status",
            message=f"User accessed get_scheduler_status",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass
    
    try:
        # Use application service instead of direct cron_manager access
        scheduler_service = container.get_scheduler_service()
        status_data = await scheduler_service.get_scheduler_status()
        
        return SchedulerStatusResponse(**status_data)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get scheduler status: {str(e)}"
        ) from e


@router.get("/jobs/all", response_model=AllJobsResponse)
async def get_all_jobs(audit_logger: AuditLogger = Depends(get_audit_logger)):
    """
    Get all scheduled jobs with their current status
    
    Returns:
        List of all jobs with their configuration and status
    """
    try:
        # Log user action for scheduler_get_all_jobs
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="scheduler_get_all_jobs",
            message=f"User accessed get_all_jobs",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass
    
    try:
        # Use application service instead of direct cron_manager access
        scheduler_service = container.get_scheduler_service()
        jobs_data = await scheduler_service.get_all_jobs()
        
        # Convert to DTO format
        return AllJobsResponse(
            service=jobs_data.get('service', 'scheduler'),
            scheduler_running=jobs_data.get('scheduler_running', False),
            total_jobs=jobs_data.get('total_jobs', 0),
            active_jobs=jobs_data.get('active_jobs', 0),
            registered_jobs=jobs_data.get('registered_jobs', 0),
            jobs=jobs_data.get('jobs', []),
            message=jobs_data.get('message')
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get all jobs: {str(e)}"
        ) from e


@router.get("/jobs/{job_type}/history", response_model=JobExecutionHistoryResponse)
async def get_job_execution_history(audit_logger: AuditLogger = Depends(get_audit_logger),
    
    job_type: str = Path(..., description="Type of job", min_length=1, max_length=100),
    limit: int = Query(10, ge=1, le=100, description="Maximum number of executions to return")
):
    """
    Get execution history for a specific job type
    
    Args:
        job_type: Type of job (e.g., demand_data_management, supply_data_management)
        limit: Maximum number of executions to return (default: 10)
    
    Returns:
        Execution history for the job
    """
    try:
        # Log user action for scheduler_job_history
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="scheduler_job_history",
            message=f"User accessed get_job_execution_history",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass
    
    try:
        # Use application service instead of direct cron_manager access
        scheduler_service = container.get_scheduler_service()
        history = await scheduler_service.get_job_execution_history(job_type, limit)
        
        return JobExecutionHistoryResponse(
            job_type=job_type,
            executions=history,
            total_executions=len(history)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get job history: {str(e)}"
        ) from e


@router.post("/jobs/{job_type}/enable", response_model=JobManagementResponse)
async def enable_job(audit_logger: AuditLogger = Depends(get_audit_logger),
    job_type: str = Path(..., description="Type of job to enable", min_length=1, max_length=100)):
    """
    Enable a specific job type
    
    Args:
        job_type: Type of job to enable
    
    Returns:
        Success status
    """
    try:
        # Log user action for scheduler_enable_job
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="scheduler_enable_job",
            message=f"User accessed enable_job",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass
    """
    Enable a specific job type
    
    Args:
        job_type: Type of job to enable
    
    Returns:
        Success status
    """
    try:
        # Use application service instead of direct cron_manager access
        scheduler_service = container.get_scheduler_service()
        success = await scheduler_service.enable_job(job_type)
        
        if success:
            return JobManagementResponse(
                success=True,
                message=f"Job {job_type} enabled successfully",
                job_type=job_type
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_type} not found"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to enable job: {str(e)}"
        ) from e


@router.post("/jobs/{job_type}/disable", response_model=JobManagementResponse)
async def disable_job(audit_logger: AuditLogger = Depends(get_audit_logger),
    job_type: str = Path(..., description="Type of job to disable", min_length=1, max_length=100)):
    """
    Disable a specific job type
    
    Args:
        job_type: Type of job to disable
    
    Returns:
        Success status
    """
    try:
        # Log user action for scheduler_disable_job
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="scheduler_disable_job",
            message=f"User accessed disable_job",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass
    """
    Disable a specific job type
    
    Args:
        job_type: Type of job to disable
    
    Returns:
        Success status
    """
    try:
        # Use application service instead of direct cron_manager access
        scheduler_service = container.get_scheduler_service()
        success = await scheduler_service.disable_job(job_type)
        
        if success:
            return JobManagementResponse(
                success=True,
                message=f"Job {job_type} disabled successfully",
                job_type=job_type
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_type} not found"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to disable job: {str(e)}"
        ) from e


@router.post("/jobs/{job_type}/trigger", response_model=JobManagementResponse)
async def trigger_job(audit_logger: AuditLogger = Depends(get_audit_logger),
    job_type: str = Path(..., description="Type of job to trigger", min_length=1, max_length=100)):
    """
    Manually trigger a specific job type
    
    Args:
        job_type: Type of job to trigger
    
    Returns:
        Success status
    """
    try:
        # Log user action for scheduler_trigger_job
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="scheduler_trigger_job",
            message=f"User accessed trigger_job",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass
    """
    Trigger a specific job type to run immediately
    
    Args:
        job_type: Type of job to trigger
    
    Returns:
        Success status
    """
    try:
        # Use application service instead of direct cron_manager access
        scheduler_service = container.get_scheduler_service()
        success = await scheduler_service.trigger_job(job_type)
        
        if success:
            return JobManagementResponse(
                success=True,
                message=f"Job {job_type} triggered successfully",
                job_type=job_type
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_type} not found"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger job: {str(e)}"
        ) from e


@router.post("/jobs/reconcile-and-rerun", response_model=ReconciliationResponse)
async def reconcile_and_rerun_crons(audit_logger: AuditLogger = Depends(get_audit_logger)):
    """
    Reconcile data gaps and rerun all cron jobs to ensure system consistency
    
    Returns:
        Reconciliation results
    """
    try:
        # Log user action for scheduler_reconcile_rerun
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="scheduler_reconcile_rerun",
            message=f"User accessed reconcile_and_rerun_crons",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass
    

    try:
        # Use application service instead of direct dummy_data_service access
        scheduler_service = container.get_scheduler_service()
        result = await scheduler_service.reconcile_and_rerun_crons()
        
        return ReconciliationResponse(
            success=result.get('status') == 'success',
            message=result.get('message', 'Reconciliation completed'),
            details=result
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Reconciliation failed: {str(e)}"
        ) from e 