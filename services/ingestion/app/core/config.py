"""
Configuration settings for Ingestion Service
"""
import os
from typing import List, Optional
from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings"""
    
    # Service Configuration
    SERVICE_NAME: str = "ingestion-service"
    SERVICE_PORT: int = 8002
    SERVICE_HOST: str = "0.0.0.0"
    DEBUG: bool = False  # Default to False for security; set to True explicitly for development
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "colored"
    
    # Environment Configuration
    ENVIRONMENT: Optional[str] = None  # Auto-detected if not set in .env
    
    # Database Configuration
    MONGODB_URI: str = "mongodb://localhost:27017/epms"
    MONGODB_DATABASE: str = "epms"
    MONGODB_RAW_DATA_COLLECTION: str = "raw_data"
    MONGODB_FORECAST_SERVE_COLLECTION: str = "forecast_serve"
    
    # Audit Database Configuration
    AUDIT_DB_URI: str = "mongodb://localhost:27017/audit_db"
    AUDIT_DB_NAME: str = "audit_db"
    AUDIT_COLLECTION_NAME: str = "audit_logs"
    QUEUE_COLLECTION_NAME: str = "audit_queue_jobs"
    JOB_DATA_COLLECTION: str = "job_data"
    
    # Timeblock Master Collection
    MONGODB_TIMEBLOCK_MASTER_COLLECTION: str = "time_block"
    
    # Limit Values Raw Data Collection
    MONGODB_LIMIT_VALUES_RAW_DATA_COLLECTION: str = "limit_values_raw_data"
    
    # Redis Configuration
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 1
    REDIS_PASSWORD: str = ""
    
    # File Upload Configuration
    MAX_FILE_SIZE_MB: int = 100
    ALLOWED_FILE_TYPES: List[str] = ["csv"]
    UPLOAD_DIR: str = "uploads"
    UPLOAD_BACKUP_DIR: str = "uploads_backup"
    TEMP_DIR: str = "temp"
    
    # Data Processing Configuration
    BATCH_SIZE: int = 1000
    BACKGROUND_PROCESSING_BATCH_SIZE: int = 10  # Number of files to process per background batch
    PROCESSING_TIMEOUT_SECONDS: int = 300
    RETRY_ATTEMPTS: int = 3
    RETRY_DELAY_SECONDS: int = 5
    
    # External API Configuration
    EXTERNAL_API_TIMEOUT: int = 30
    EXTERNAL_API_RETRY_ATTEMPTS: int = 3
    EXTERNAL_API_RETRY_DELAY: int = 1
    
    # Authentication Service
    AUTH_SERVICE_URL: str = "http://localhost:8001"
    AUTH_SERVICE_TIMEOUT: int = 10
    
    # JWT Configuration - must be set via env var; no insecure fallback
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "")
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # Processing Service
    PROCESSING_SERVICE_URL: str = "http://localhost:8003"
    PROCESSING_SERVICE_TIMEOUT: int = 30
    
    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 50
    RATE_LIMIT_WINDOW_SECONDS: int = 3600
    
    # Proxy Configuration
    # Comma-separated trusted reverse-proxy IPs. XFF/X-Real-IP only honoured from these peers.
    TRUSTED_PROXIES: str = os.getenv("TRUSTED_PROXIES", "127.0.0.1")

    # CORS Configuration
    # When credentials are allowed, wildcard "*" is not permitted for security
    CORS_ORIGINS: List[str] = ["http://localhost:3005", "http://localhost:8080"]
    CORS_ALLOW_CREDENTIALS: bool = True
    
    # Health Check
    HEALTH_CHECK_ENABLED: bool = True
    HEALTH_CHECK_INTERVAL_SECONDS: int = 30
    
    # Time-Series Configuration
    TIMESERIES_TIME_FIELD: str = "created_at"
    TIMESERIES_META_FIELD: str = "metadata"
    TIMESERIES_GRANULARITY: str = "seconds"  # seconds, minutes, hours

    # Cron Job Configuration
    CRON_TIMEZONE: str = "Asia/Kolkata"
    UPLOAD_RAW_DATA_PROCESSOR_ENABLED: bool = True
    SCRAPE_RAW_DATA_PROCESSOR_ENABLED: bool = True
    FTP_RAW_DATA_PROCESSOR_ENABLED: bool = True
    API_RAW_DATA_PROCESSOR_ENABLED: bool = True
    UPLOAD_RAW_DATA_PROCESSOR_CRON_SCHEDULE: str = "*/15 * * * *"  # Every 2 minutes
    SCRAPE_RAW_DATA_PROCESSOR_CRON_SCHEDULE: str = "* * * * *"  # Every 15 minutes
    FTP_RAW_DATA_PROCESSOR_CRON_SCHEDULE: str = "*/15 * * * *"  # Every 15 minutes
    API_RAW_DATA_PROCESSOR_CRON_SCHEDULE: str = "*/15 * * * *"  # Every 15 minutes
    RTM_FORECAST_API_PROCESSOR_ENABLED: bool = True
    RTM_FORECAST_API_PROCESSOR_CRON_SCHEDULE: str = "*/30 * * * *"  # Every 30 minutes

    # Upload Processing Configuration
    UPLOAD_MAX_RETRIES: int = 3  # Maximum retry attempts before marking as permanently failed

    # Scraper Configuration
    SCRAPER_TIMEOUT: int = 30
    SCRAPER_RETRY_ATTEMPTS: int = 3
    SCRAPER_RETRY_DELAY: int = 5

    # FTP Configuration
    FTP_TIMEOUT: int = 30
    FTP_RETRY_ATTEMPTS: int = 3
    FTP_RETRY_DELAY: int = 5
    FTP_PASSIVE_MODE: bool = True
    FTP_USE_TLS: bool = False
    PLANT_MASTER_COLLECTION: str = "plant_master"

    @model_validator(mode='after')
    def set_environment_config(self):
        """Set environment-aware configurations after validation"""
        if self.ENVIRONMENT is None:
            # Use proper environment detection with defaults
            docker_container = os.getenv("DOCKER_CONTAINER", "").lower() in ("true", "1", "yes")
            server_deployment = os.getenv("SERVER_DEPLOYMENT", "").lower() in ("true", "1", "yes")
            nginx_proxy = os.getenv("NGINX_PROXY") is not None
            
            if docker_container:
                self.ENVIRONMENT = "docker"
            elif server_deployment or nginx_proxy:
                self.ENVIRONMENT = "server"
            else:
                self.ENVIRONMENT = "local"
        
        return self
    
    @property
    def BASE_PATH(self) -> str:
        """Get base path for current environment (computed dynamically)"""
        return "/ingestion" if self.ENVIRONMENT in ["server", "docker"] else ""
    
    def check_security_warnings(self):
        """Validate security configuration and fail startup if insecure.
        Call this at application startup.
        """
        import logging
        logger = logging.getLogger(__name__)
        
        # Check JWT_SECRET_KEY
        if not self.JWT_SECRET_KEY:
            raise ValueError(
                "SECURITY ERROR: JWT_SECRET_KEY must be set via environment variable. "
                "Set JWT_SECRET_KEY to a secure random string (>=32 chars)."
            )
        
        if len(self.JWT_SECRET_KEY) < 32:
            raise ValueError(
                f"SECURITY ERROR: JWT_SECRET_KEY must be at least 32 characters long. "
                f"Current length: {len(self.JWT_SECRET_KEY)}"
            )
        
        if "your-super-secret-jwt-key-change-in-production" in self.JWT_SECRET_KEY:
            raise ValueError(
                "SECURITY ERROR: JWT_SECRET_KEY contains insecure default value. "
                "Set a secure random string via JWT_SECRET_KEY environment variable."
            )
        
        # Check CORS configuration security
        if "*" in self.CORS_ORIGINS and self.CORS_ALLOW_CREDENTIALS:
            raise ValueError(
                "SECURITY ERROR: CORS configuration insecure. "
                "Wildcard origin '*' cannot be used with CORS_ALLOW_CREDENTIALS=True. "
                "Specify explicit origins instead."
            )
        
        logger.info("Security configuration validation passed")

    class Config:
        """Pydantic configuration for Settings class"""
        extra = "ignore"
        env_file = ".env"
        case_sensitive = True
        


# Create settings instance
settings = Settings() 
