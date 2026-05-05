"""
Scheduler Data Transfer Objects
DTOs for scheduler API requests and responses
"""

from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from datetime import datetime


class SchedulerStatusResponse(BaseModel):
    """Scheduler status response DTO"""
    status: str = Field(..., description="Scheduler status (active/inactive)")
    active_jobs: int = Field(..., description="Number of active jobs")
    total_jobs: int = Field(..., description="Total number of jobs")
    last_activity: str = Field(..., description="Last activity timestamp")


class JobManagementResponse(BaseModel):
    """Job management response DTO"""
    success: bool = Field(..., description="Operation success status")
    message: str = Field(..., description="Operation result message")
    job_type: Optional[str] = Field(None, description="Job type that was operated on")


class JobExecutionHistoryResponse(BaseModel):
    """Job execution history response DTO"""
    job_type: str = Field(..., description="Type of job")
    executions: List[Dict[str, Any]] = Field(..., description="List of job executions")
    total_executions: int = Field(..., description="Total number of executions")


class ReconciliationResponse(BaseModel):
    """System reconciliation response DTO"""
    success: bool = Field(..., description="Reconciliation success status")
    message: str = Field(..., description="Reconciliation result message")
    details: Dict[str, Any] = Field(..., description="Detailed reconciliation results")


class AllJobsResponse(BaseModel):
    """All jobs response DTO"""
    service: str = Field(..., description="Service name")
    scheduler_running: bool = Field(..., description="Whether scheduler is running")
    total_jobs: int = Field(..., description="Total number of jobs")
    active_jobs: int = Field(..., description="Number of active jobs")
    registered_jobs: int = Field(..., description="Number of registered jobs")
    jobs: List[Dict[str, Any]] = Field(..., description="List of jobs")
    message: Optional[str] = Field(None, description="Additional message")


# Request DTOs
class JobManagementRequest(BaseModel):
    """Job management request DTO"""
    job_type: str = Field(..., description="Type of job to manage")


class JobHistoryRequest(BaseModel):
    """Job history request DTO"""
    job_type: str = Field(..., description="Type of job")
    limit: int = Field(10, ge=1, le=100, description="Maximum number of executions to return")
