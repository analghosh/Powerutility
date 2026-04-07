"""
Simple models for audit queue
"""

from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from enum import Enum
from pydantic import BaseModel


class JobStatus(str, Enum):
    """Job status enumeration"""
    SAVED = "saved"
    PROCESSING = "processing" 
    COMPLETED = "completed"
    FAILED = "failed"


class LogPriority(str, Enum):
    """Log priority enumeration with TTL configuration"""
    CRITICAL = "critical"  # No expiry
    HIGH = "high"         # 1 year expiry
    MEDIUM = "medium"     # 6 months expiry
    LOW = "low"          # 3 months expiry
    
    @property
    def ttl_seconds(self) -> Optional[int]:
        """Get TTL in seconds for each priority level"""
        ttl_map = {
            LogPriority.CRITICAL: None,  # No expiry
            LogPriority.HIGH: 365 * 24 * 60 * 60,  # 1 year
            LogPriority.MEDIUM: 180 * 24 * 60 * 60,  # 6 months
            LogPriority.LOW: 90 * 24 * 60 * 60,  # 3 months
        }
        return ttl_map.get(self)
    
    @property
    def ttl_days(self) -> Optional[int]:
        """Get TTL in days for each priority level"""
        ttl_map = {
            LogPriority.CRITICAL: None,  # No expiry
            LogPriority.HIGH: 365,  # 1 year
            LogPriority.MEDIUM: 180,  # 6 months
            LogPriority.LOW: 90,  # 3 months
        }
        return ttl_map.get(self)


class AuditJob(BaseModel):
    """Audit job model"""
    job_id: str
    log_data: Dict[str, Any]
    destination: str = "mongo"  # "mongo" or "file"
    source_service: str
    job_type: str = "audit"  # "audit" or "message"
    priority: LogPriority = LogPriority.MEDIUM  # Default priority
    status: JobStatus = JobStatus.SAVED
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime
    updated_at: datetime
    error_message: Optional[str] = None
