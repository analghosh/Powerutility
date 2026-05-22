
import os
try:
    from pydantic_settings import BaseSettings
except ImportError:
    from pydantic import BaseSettings

from pydantic import model_validator


class RabbitMQConfig(BaseSettings):
    """Configuration settings for RabbitMQ connection and features"""

    # Connection details — defaults to RabbitMQ's built-in guest/guest user.
    # Override in .env: RABBITMQ_USER=myuser, RABBITMQ_PASSWORD=mypass
    # If left blank in .env they fall back to guest/guest automatically.
    RABBITMQ_HOST: str = "127.0.0.1"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"
    RABBITMQ_VHOST: str = "/"

    @model_validator(mode="after")
    def _coerce_blank_credentials(self) -> "RabbitMQConfig":
        """Fall back to guest/guest if credentials are blank in .env"""
        if not self.RABBITMQ_USER or not self.RABBITMQ_USER.strip():
            self.RABBITMQ_USER = "guest"
        if not self.RABBITMQ_PASSWORD or not self.RABBITMQ_PASSWORD.strip():
            self.RABBITMQ_PASSWORD = "guest"
        return self
    
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
