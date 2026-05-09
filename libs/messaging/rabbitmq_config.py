
import os
try:
    from pydantic_settings import BaseSettings
except ImportError:
    from pydantic import BaseSettings

class RabbitMQConfig(BaseSettings):
    """Configuration settings for RabbitMQ connection and features"""
    
    # Connection details - MUST be set via environment variables
    RABBITMQ_HOST: str = os.getenv("RABBITMQ_HOST", "127.0.0.1")
    RABBITMQ_PORT: int = int(os.getenv("RABBITMQ_PORT", "5672"))
    RABBITMQ_USER: str = os.getenv("RABBITMQ_USER", "")
    RABBITMQ_PASSWORD: str = os.getenv("RABBITMQ_PASSWORD", "")
    RABBITMQ_VHOST: str = os.getenv("RABBITMQ_VHOST", "/")
    
    # Feature flags
    USE_RABBITMQ_AUDIT: bool = os.getenv("USE_RABBITMQ_AUDIT", "false").lower() == "true"
    USE_RABBITMQ_JOBS: bool = os.getenv("USE_RABBITMQ_JOBS", "true").lower() == "true"
    RABBITMQ_FALLBACK_TO_MONGODB: bool = os.getenv("RABBITMQ_FALLBACK_TO_MONGODB", "true").lower() == "true"
    USE_QUORUM_QUEUES: bool = os.getenv("USE_QUORUM_QUEUES", "true").lower() == "true"
    
    # Circuit Breaker Config
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = int(os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5"))
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT: int = int(os.getenv("CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "60"))
    CIRCUIT_BREAKER_SUCCESS_THRESHOLD: int = int(os.getenv("CIRCUIT_BREAKER_SUCCESS_THRESHOLD", "2"))
    
    # Consumer Settings
    RABBITMQ_CONSUMER_PREFETCH: int = int(os.getenv("RABBITMQ_CONSUMER_PREFETCH", "10"))
    AUDIT_BATCH_SIZE: int = int(os.getenv("AUDIT_BATCH_SIZE", "100"))
    AUDIT_BATCH_TIMEOUT: int = int(os.getenv("AUDIT_BATCH_TIMEOUT", "5"))
    
    # Queue Definitions
    EXCHANGE_JOBS: str = "epms.jobs"
    EXCHANGE_AUDIT: str = "epms.audit"
    EXCHANGE_DLQ: str = "epms.dlq"
    
    QUEUE_JOBS: str = "scheduler.jobs"
    QUEUE_AUDIT: str = "scheduler.audit"
    
    class Config:
        env_file = ".env"
        extra = "ignore"

config = RabbitMQConfig()
