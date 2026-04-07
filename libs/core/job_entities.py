"""
Shared Job Entities
Job-related entity models that can be used across all services
"""

from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from bson import ObjectId


class JobData(BaseModel):
    """Entity model for job configuration data"""
    
    id: Optional[str] = Field(default=None, alias="_id")
    job_type: str = Field(..., description="Type identifier for the job")
    name: str = Field(..., description="Human-readable name of the job")
    type: str = Field(..., description="Type of job (data_management, maintenance, sync, etc.)")
    schedule: str = Field(..., description="Cron schedule expression")
    enabled: bool = Field(True, description="Whether the job is enabled")
    timezone: str = Field("UTC", description="Timezone for the job schedule")
    description: Optional[str] = Field(None, description="Job description")
    priority: int = Field(1, description="Job priority (1=highest, 10=lowest)")
    payload: Optional[Dict[str, Any]] = Field(None, description="Job-specific payload/configuration data")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional job metadata")
    version: int = Field(1, description="Job configuration version")
    service: str = Field(..., description="Service that owns this job")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="When the job was created")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="When the job was last updated")
    
    class Config:
        """Pydantic configuration for JobData class"""
        populate_by_name = True
        json_encoders = {
            ObjectId: str,
            datetime: lambda v: v.isoformat()
        }


class JobDataUpdate(BaseModel):
    """Model for updating job data"""
    
    name: Optional[str] = None
    type: Optional[str] = None
    schedule: Optional[str] = None
    enabled: Optional[bool] = None
    timezone: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[int] = None
    payload: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    service: Optional[str] = None
    
    class Config:
        """Pydantic configuration for JobDataUpdate class"""
        populate_by_name = True


class JobDetail(BaseModel):
    """Entity model for job execution logs (time series data)"""
    
    id: Optional[str] = Field(default=None, alias="_id")
    job_id: str = Field(..., description="Unique identifier for the job")
    execution_id: str = Field(..., description="Unique identifier for this execution")
    job_name: str = Field(..., description="Name of the job at execution time")
    execution_start: datetime = Field(..., description="When the job execution started")
    execution_end: Optional[datetime] = Field(None, description="When the job execution ended")
    duration_seconds: Optional[float] = Field(None, description="Execution duration in seconds")
    status: str = Field(..., description="Execution status: running, success, error, partial_success")
    success: bool = Field(..., description="Whether the execution was successful")
    error_message: Optional[str] = Field(None, description="Error message if execution failed")
    error_details: Optional[Dict[str, Any]] = Field(None, description="Detailed error information")
    result_data: Optional[Dict[str, Any]] = Field(None, description="Job execution result data")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional execution metadata")
    service: str = Field(..., description="Service that executed the job")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="When the log was created")
    
    class Config:
        """Pydantic configuration for JobDetail class"""
        populate_by_name = True
        json_encoders = {
            ObjectId: str,
            datetime: lambda v: v.isoformat()
        }


class JobDetailQuery(BaseModel):
    """Model for querying job details"""
    
    job_id: Optional[str] = None
    status: Optional[str] = None
    service: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    limit: int = Field(100, description="Maximum number of records to return")
    skip: int = Field(0, description="Number of records to skip")
    
    class Config:
        """Pydantic configuration for JobDetailQuery class"""
        populate_by_name = True


class JobStatistics(BaseModel):
    """Model for job execution statistics"""
    
    job_id: str
    job_name: str
    service: str
    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    success_rate: float = 0.0
    average_duration_seconds: float = 0.0
    last_execution_date: Optional[datetime] = None
    last_success_date: Optional[datetime] = None
    last_failure_date: Optional[datetime] = None
    
    class Config:
        """Pydantic configuration for JobStatistics class"""
        populate_by_name = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }
