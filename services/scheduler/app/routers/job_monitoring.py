"""
Job Monitoring and Tracing Router for Scheduler Service
"""

from fastapi import APIRouter, HTTPException, Query, Path
from typing import Dict, Any, Optional
from datetime import datetime
import logging
import sys
import os

# Add project root to path for audit queue import
project_root = os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..')
if project_root not in sys.path:
    sys.path.append(project_root)

from libs.audit_queue.simple_logger import AuditLogger

# Import DTOs
from ..schemas.job_monitoring_dto import (
    RunningJobsResponse,
    JobTraceResponse,
    JobsByServiceResponse,
    JobStatisticsResponse,
    JobProgressUpdateResponse,
    DashboardResponse,
    SystemHealthResponse,
    JobExecutionDetailsResponse,
    AuditLogsResponse,
    AuditLogSearchResponse
)

# Import dependency injection container
from ..infrastructure.di.container import container
from ..core.dependencies import get_audit_logger
from fastapi import Depends

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["job-monitoring"])


@router.get("/running", response_model=RunningJobsResponse)
async def get_running_jobs(audit_logger: AuditLogger = Depends(get_audit_logger)) -> RunningJobsResponse:
    """
    Get all currently running jobs with real-time status
    
    Returns:
        Dict containing list of running jobs with execution details
    """
    try:
        # Log user action for job_monitoring_running
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="job_monitoring_running",
            message=f"User accessed get_running_jobs",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass
    
    try:
        # Use application service instead of direct repository access
        job_monitoring_service = container.get_job_monitoring_service()
        running_jobs = await job_monitoring_service.get_running_jobs()
        
        return RunningJobsResponse(
            status="success",
            running_jobs=running_jobs,
            count=len(running_jobs),
            timestamp=datetime.now().isoformat()
        )
        
    except Exception as e:
        logger.error("Failed to get running jobs: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get running jobs: {str(e)}"
        )


@router.get("/trace/{job_id}", response_model=JobTraceResponse)
async def get_job_trace(job_id: str):
    """Get detailed trace information for a specific job"""
    try:
        # Log user action for job_monitoring_trace
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="job_monitoring_trace",
            message=f"User accessed get_job_trace",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass
    
    try:
        # Use application service instead of direct repository access
        job_monitoring_service = container.get_job_monitoring_service()
        trace_info = await job_monitoring_service.get_job_trace(job_id)
        
        if not trace_info:
            raise HTTPException(
                status_code=404,
                detail=f"Job {job_id} not found"
            )
        
        return JobTraceResponse(
            status="success",
            job_trace=trace_info,
            timestamp=datetime.now().isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get job trace for %s: %s", job_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get job trace: {str(e)}"
        )


@router.get("/service/{service_name}")
async def get_jobs_by_service(service_name: str):
    """Get all jobs for a specific service"""
    try:
        # Log user action for job_monitoring_by_service
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="job_monitoring_by_service",
            message=f"User accessed get_jobs_by_service",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass

    """
    Get jobs for a specific service
    
    Args:
        service_name: Name of the service
        limit: Maximum number of jobs to return
        
    Returns:
        Dict containing jobs for the service
    """
    try:
        # Use application service instead of direct repository access
        job_monitoring_service = container.get_job_monitoring_service()
        jobs = await job_monitoring_service.get_jobs_by_service(service_name, limit)
        
        return JobsByServiceResponse(
            status="success",
            service=service_name,
            jobs=jobs,
            count=len(jobs),
            limit=limit,
            timestamp=datetime.now().isoformat()
        )
        
    except Exception as e:
        logger.error("Failed to get jobs for service %s: %s", service_name, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get jobs for service: {str(e)}"
        )


@router.get("/statistics", response_model=JobStatisticsResponse)
async def get_job_statistics(hours: int = 24):
    """Get job statistics for the specified time period"""
    try:
        # Log user action for job_monitoring_statistics
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="job_monitoring_statistics",
            message=f"User accessed get_job_statistics",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass

    try:
        # Use application service instead of direct repository access
        job_monitoring_service = container.get_job_monitoring_service()
        stats = await job_monitoring_service.get_job_statistics(hours)
        
        return JobStatisticsResponse(
            status="success",
            statistics=stats,
            timestamp=datetime.now().isoformat()
        )
        
    except Exception as e:
        logger.error("Failed to get job statistics: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get job statistics: {str(e)}"
        )


@router.put("/progress/{job_id}")
async def update_job_progress(job_id: str):
    """Update job progress"""
    try:
        # Log user action for job_monitoring_update_progress
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="job_monitoring_update_progress",
            message=f"User accessed update_job_progress",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass

    """
    Update job progress and current step
    
    Args:
        job_id: ID of the job to update
        progress: Progress percentage (0-100)
        current_step: Current execution step description
        estimated_completion: Estimated completion time
        
    Returns:
        Dict containing update result
    """
    try:
        # Use application service instead of direct repository access
        job_monitoring_service = container.get_job_monitoring_service()
        success = await job_monitoring_service.update_job_progress(
            job_id, progress, current_step, estimated_completion
        )
        
        if success:
            return {
                "status": "success",
                "message": f"Progress updated for job {job_id}",
                "job_id": job_id,
                "progress": progress,
                "current_step": current_step,
                "timestamp": datetime.now().isoformat()
            }
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Job {job_id} not found or not running"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update job progress for %s: %s", job_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update job progress: {str(e)}"
        )


@router.get("/dashboard", response_model=DashboardResponse)
async def get_job_dashboard() -> DashboardResponse:
    """
    Get comprehensive job dashboard data
    
    Returns:
        Dict containing dashboard data including running jobs, statistics, and recent activity
    """
    try:
        # Log user action for job_monitoring_dashboard
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="job_monitoring_dashboard",
            message=f"User accessed get_job_dashboard",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass
    

    try:
        # Use application service instead of direct repository access
        job_monitoring_service = container.get_job_monitoring_service()
        dashboard_data = await job_monitoring_service.get_dashboard_data()
        
        return DashboardResponse(
            status="success",
            dashboard=dashboard_data
        )
        
    except Exception as e:
        logger.error("Failed to get job dashboard: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get job dashboard: {str(e)}"
        )


@router.get("/health")
async def get_job_system_health() -> Dict[str, Any]:
    """
    Get job system health status
    
    Returns:
        Dict containing job system health information
    """
    try:
        # Log user action for job_monitoring_health
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="job_monitoring_health",
            message=f"User accessed get_job_system_health",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass
    

    try:
        # Use application service instead of direct repository access
        job_monitoring_service = container.get_job_monitoring_service()
        
        # Get running jobs and statistics
        running_jobs = await job_monitoring_service.get_running_jobs()
        stats_1h = await job_monitoring_service.get_job_statistics(1)
        
        # Check for stuck jobs (running for more than 2 hours)
        current_time = datetime.now()
        stuck_jobs = []
        for job in running_jobs:
            if job.get("execution_start"):
                runtime = (current_time - job["execution_start"]).total_seconds()
                if runtime > 7200:  # 2 hours
                    stuck_jobs.append({
                        "job_id": job["job_id"],
                        "job_name": job.get("job_name", ""),
                        "runtime_seconds": runtime,
                        "runtime_formatted": job.get("runtime_formatted", "")
                    })
        
        # Determine health status
        health_status = "healthy"
        issues = []
        
        if len(stuck_jobs) > 0:
            health_status = "warning"
            issues.append(f"{len(stuck_jobs)} jobs appear to be stuck")
        
        if stats_1h.get("success_rate", 100) < 80:
            health_status = "warning"
            issues.append(f"Low success rate: {stats_1h.get('success_rate', 0)}%")
        
        if len(running_jobs) > 20:  # Arbitrary threshold
            health_status = "warning"
            issues.append(f"High number of running jobs: {len(running_jobs)}")
        
        health_data = {
            "status": health_status,
            "running_jobs": len(running_jobs),
            "stuck_jobs": len(stuck_jobs),
            "stuck_jobs_details": stuck_jobs,
            "success_rate_1h": stats_1h.get("success_rate", 0),
            "issues": issues,
            "timestamp": datetime.now().isoformat()
        }
        
        return {
            "status": "success",
            "health": health_data
        }
        
    except Exception as e:
        logger.error("Failed to get job system health: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get job system health: {str(e)}"
        )


@router.get("/all-jobs")
async def get_all_jobs_from_job_data() -> Dict[str, Any]:
    """
    Get all jobs from job_data collection
    
    Returns:
        Dict containing all jobs from job_data collection
    """
    try:
        # Log user action for job_monitoring_all_jobs
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="job_monitoring_all_jobs",
            message=f"User accessed get_all_jobs_from_job_data",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass
    

    try:
        # Use application service instead of direct repository access
        job_monitoring_service = container.get_job_monitoring_service()
        jobs = await job_monitoring_service.get_all_jobs_from_job_data()
        
        return {
            "status": "success",
            "jobs": jobs,
            "count": len(jobs),
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error("Failed to get all jobs from job_data: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get all jobs: {str(e)}"
        )


@router.get("/execution-details/{job_id}", response_model=JobExecutionDetailsResponse)
async def get_job_execution_details(job_id: str):
    """Get detailed execution information for a job"""
    try:
        # Log user action for job_monitoring_execution_details
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="job_monitoring_execution_details",
            message=f"User accessed get_job_execution_details",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass

    try:
        # Use application service instead of direct repository access
        job_monitoring_service = container.get_job_monitoring_service()
        execution_details = await job_monitoring_service.get_job_execution_details(job_id)
        
        if not execution_details:
            raise HTTPException(
                status_code=404,
                detail=f"Job {job_id} not found or has no execution history"
            )
        
        return JobExecutionDetailsResponse(
            status="success",
            execution_details=execution_details,
            timestamp=datetime.now().isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get job execution details for %s: %s", job_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get job execution details: {str(e)}"
        ) from e


@router.get("/service-jobs/{service}")
async def get_service_jobs_from_job_data(service: str, enabled_only: bool = True):
    """Get jobs for a specific service from job data"""
    try:
        # Log user action for job_monitoring_service_jobs
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="job_monitoring_service_jobs",
            message=f"User accessed get_service_jobs_from_job_data",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass

    try:
        # Simple test first - return basic info without database call
        if service == "test":
            return {
                "status": "success",
                "service": service,
                "jobs": [],
                "count": 0,
                "enabled_only": enabled_only,
                "timestamp": datetime.now().isoformat(),
                "message": "Test endpoint working"
            }
        
        # Use application service instead of direct repository access
        job_monitoring_service = container.get_job_monitoring_service()
        jobs = await job_monitoring_service.get_jobs_by_service_from_data(service, enabled_only)
        
        return {
            "status": "success",
            "service": service,
            "jobs": jobs,
            "count": len(jobs),
            "enabled_only": enabled_only,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error("Failed to get jobs for service %s: %s", service, str(e))
        # Return a more detailed error response
        return {
            "status": "error",
            "service": service,
            "error": str(e),
            "error_type": type(e).__name__,
            "timestamp": datetime.now().isoformat()
        }


# Audit Logs Endpoints
@router.get("/audit-logs/by-job")
async def get_audit_logs_by_job_id(job_id: str):
    """Get audit logs for a specific job ID"""
    try:
        # Log user action for job_monitoring_audit_logs
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="job_monitoring_audit_logs",
            message=f"User accessed get_audit_logs_by_job_id",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass

    """
    Get audit logs for a specific job ID
    
    Args:
        job_id: Job ID to get audit logs for
        limit: Maximum number of logs to return
        priority: Filter by priority level
        status: Filter by status
        
    Returns:
        Dict containing audit logs for the job
    """
    try:
        # Get audit logs by job ID
        audit_logs = get_audit_logs_by_job(job_id)
        
        # Apply filters
        if priority:
            audit_logs = [log for log in audit_logs 
                         if log.get('meta', {}).get('priority', '').lower() == priority.lower()]
        
        if status:
            audit_logs = [log for log in audit_logs 
                         if log.get('status', '').lower() == status.lower()]
        
        # Limit results
        audit_logs = audit_logs[:limit]
        
        return {
            "status": "success",
            "job_id": job_id,
            "audit_logs": audit_logs,
            "count": len(audit_logs),
            "filters": {
                "priority": priority,
                "status": status,
                "limit": limit
            },
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error("Failed to get audit logs for job %s: %s", job_id, str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get audit logs for job {job_id}: {str(e)}"
        )


@router.get("/audit-logs/search")
async def search_job_audit_logs(search_text: str):
    """Search job audit logs by text"""
    try:
        # Log user action for job_monitoring_search_logs
        audit_logger.log_user_action(
            service="scheduler",
            user_id="system",  # TODO: Replace with actual user_id from authentication
            action="job_monitoring_search_logs",
            message=f"User accessed search_job_audit_logs",
            success=True
        )
    except Exception as audit_error:
        # Don't fail the request if audit logging fails
        pass

    """
    Search audit logs with text search
    
    Args:
        search_text: Text to search for
        job_id: Optional job ID filter
        service: Service to search in (default: scheduler)
        limit: Maximum number of results
        
    Returns:
        Dict containing search results
    """
    try:
        # Perform search
        search_results = search_audit_logs(
            search_text=search_text,
            service=service,
            log_type="job",
            limit=limit
        )
        
        # Filter by job_id if provided
        if job_id:
            search_results = [log for log in search_results 
                            if log.get('trace', {}).get('jobId') == job_id]
        
        return {
            "status": "success",
            "search_text": search_text,
            "results": search_results,
            "count": len(search_results),
            "filters": {
                "job_id": job_id,
                "service": service,
                "limit": limit
            },
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error("Failed to search audit logs: %s", str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Failed to search audit logs: {str(e)}"
        )
