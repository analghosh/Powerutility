
from typing import Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field

class AuditLogMessage(BaseModel):
    """Schema for audit log messages"""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    service_name: str
    action: str
    user_id: Optional[str] = None
    username: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    status: str = "info"
    
class JobTriggerMessage(BaseModel):
    """Schema for job trigger messages"""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    job_type: str
    source_service: str
    payload: Dict[str, Any] = {}
    correlation_id: Optional[str] = None
    retry_count: int = 0
