"""
Configuration for Audit Queue System
"""

import os
from pydantic_settings import BaseSettings


class AuditQueueConfig(BaseSettings):
    """Audit Queue Configuration"""
    
    # Redis Configuration (still needed for Celery broker)
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 4  # Use DB 4 to avoid conflicts
    REDIS_PASSWORD: str = ""
    
    # Queue Configuration
    QUEUE_NAME: str = "audit_queue"
    MAX_RETRY_COUNT: int = 3
    RETRY_DELAY_SECONDS: int = 60
    MAX_WORKERS: int = 1  # Workers per service (1 = sequential processing per service)
    # Logging Configuration
    DEFAULT_LOG_DESTINATION: str = "mongo"  # "mongo" or "file"
    
    class Config:
        env_prefix = "AUDIT_"


# Global config instance
config = AuditQueueConfig()
