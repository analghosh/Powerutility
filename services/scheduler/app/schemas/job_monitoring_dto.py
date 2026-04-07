"""
Job Monitoring Data Transfer Objects
DTOs for job monitoring API requests and responses
"""

from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from datetime import datetime


class RunningJobResponse(BaseModel):
    """Running job response DTO"""
    job_id: str = Field(..., description="Job identifier")
    job_name: str = Field(..., description="Job name")
    status: str = Field(..., description="Current job status")
    started_at: Optional[str] = Field(None, description="Job start time")
    duration: Optional[str] = Field(None, description="Job duration")
    service: str = Field(..., description="Service name")


class RunningJobsResponse(BaseModel):
    """Running jobs response DTO"""
    status: str = Field(..., description="Response status")
    running_jobs: List[RunningJobResponse] = Field(..., description="List of running jobs")
    count: int = Field(..., description="Number of running jobs")
    timestamp: str = Field(..., description="Response timestamp")


class JobTraceResponse(BaseModel):
    """Job trace response DTO"""
    status: str = Field(..., description="Response status")
    job_trace: Dict[str, Any] = Field(..., description="Job trace information")
    timestamp: str = Field(..., description="Response timestamp")


class JobsByServiceResponse(BaseModel):
    """Jobs by service response DTO"""
    status: str = Field(..., description="Response status")
    service: str = Field(..., description="Service name")
    jobs: List[Dict[str, Any]] = Field(..., description="List of jobs")
    count: int = Field(..., description="Number of jobs")
    limit: int = Field(..., description="Query limit")
    timestamp: str = Field(..., description="Response timestamp")


class JobStatisticsResponse(BaseModel):
    """Job statistics response DTO"""
    status: str = Field(..., description="Response status")
    statistics: Dict[str, Any] = Field(..., description="Job statistics")
    timestamp: str = Field(..., description="Response timestamp")


class JobProgressUpdateRequest(BaseModel):
    """Job progress update request DTO"""
    progress: int = Field(..., ge=0, le=100, description="Progress percentage (0-100)")
    current_step: str = Field(..., description="Current execution step description")
    estimated_completion: str = Field(..., description="Estimated completion time")


class JobProgressUpdateResponse(BaseModel):
    """Job progress update response DTO"""
    status: str = Field(..., description="Response status")
    message: str = Field(..., description="Update result message")
    job_id: str = Field(..., description="Job ID")
    progress: int = Field(..., description="Updated progress")
    current_step: str = Field(..., description="Current step")
    timestamp: str = Field(..., description="Update timestamp")


class DashboardResponse(BaseModel):
    """Dashboard response DTO"""
    status: str = Field(..., description="Response status")
    dashboard: Dict[str, Any] = Field(..., description="Dashboard data")


class SystemHealthResponse(BaseModel):
    """System health response DTO"""
    status: str = Field(..., description="Response status")
    health: Dict[str, Any] = Field(..., description="System health data")


class JobExecutionDetailsResponse(BaseModel):
    """Job execution details response DTO"""
    status: str = Field(..., description="Response status")
    execution_details: Dict[str, Any] = Field(..., description="Execution details")
    timestamp: str = Field(..., description="Response timestamp")


class AuditLogsResponse(BaseModel):
    """Audit logs response DTO"""
    status: str = Field(..., description="Response status")
    job_id: str = Field(..., description="Job ID")
    audit_logs: List[Dict[str, Any]] = Field(..., description="Audit logs")
    count: int = Field(..., description="Number of logs")
    filters: Dict[str, Any] = Field(..., description="Applied filters")
    timestamp: str = Field(..., description="Response timestamp")


class AuditLogSearchResponse(BaseModel):
    """Audit log search response DTO"""
    status: str = Field(..., description="Response status")
    search_text: str = Field(..., description="Search text")
    results: List[Dict[str, Any]] = Field(..., description="Search results")
    count: int = Field(..., description="Number of results")
    filters: Dict[str, Any] = Field(..., description="Applied filters")
    timestamp: str = Field(..., description="Response timestamp")


# Request DTOs
class AuditLogsRequest(BaseModel):
    """Audit logs request DTO"""
    job_id: str = Field(..., description="Job ID to get audit logs for")
    limit: int = Field(100, ge=1, le=1000, description="Maximum number of logs to return")
    priority: Optional[str] = Field(None, description="Filter by priority level")
    status: Optional[str] = Field(None, description="Filter by status")


class AuditLogSearchRequest(BaseModel):
    """Audit log search request DTO"""
    search_text: str = Field(..., description="Text to search for")
    job_id: Optional[str] = Field(None, description="Optional job ID filter")
    service: str = Field("scheduler", description="Service to search in")
    limit: int = Field(100, ge=1, le=1000, description="Maximum number of results")