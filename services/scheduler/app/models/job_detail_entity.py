"""
Job Detail Entity Model
Represents job execution logs stored in time series collections
"""

from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from bson import ObjectId


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
    service: str = Field("scheduler", description="Service that executed the job")
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
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    limit: int = Field(100, description="Maximum number of records to return")
    skip: int = Field(0, description="Number of records to skip")
    
    class Config:
        """Pydantic configuration for JobDetailQuery class"""
        populate_by_name = True
