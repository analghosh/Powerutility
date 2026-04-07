"""
Job Data Entity Model
Represents job configuration data stored in job_data collection
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
    
    class Config:
        """Pydantic configuration for JobDataUpdate class"""
        populate_by_name = True
