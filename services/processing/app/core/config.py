"""
Configuration settings for Processing Service
"""

import os
import logging
from pathlib import Path
from typing import List, Optional
from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings"""
    
    # Service Configuration
    SERVICE_NAME: str = "processing-service"
    SERVICE_PORT: int = 8003
    SERVICE_HOST: str = "0.0.0.0"
    DEBUG: bool = False
    ENABLE_SWAGGER: bool = False
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"  # "json", "text", or "colored"
    LOG_USE_COLORS: bool = True  # Enable colored console output
    LOG_REQUESTS: bool = True
    LOG_RESPONSES: bool = True
    LOG_BODY: bool = False  # Set to True in development if needed
    
    # Database Configuration
    MONGODB_URI: str = "mongodb://localhost:27017" 
    MONGODB_DATABASE: str = "epms"  # Default, overridden by .env
    AUDIT_DB_NAME: str = "audit_db"
    AUTH_DB_NAME: str = "auth_db"   # overridden by .env
    MONGODB_AUTH_COLLECTION: str = "users"
    MONGODB_SESSIONS_COLLECTION: str = "sessions"
    MONGODB_AUDIT_COLLECTION: str = "audit_logs"
    AUDIT_COLLECTION_NAME: str = "audit_logs"
    QUEUE_COLLECTION_NAME: str = "audit_queue_jobs"
    MONGODB_PROCESSED_DATA_COLLECTION: str = "processed_data"
    MONGODB_VALIDATION_RULES_COLLECTION: str = "validation_rules"
    MONGODB_TRANSFORMATION_RULES_COLLECTION: str = "transformation_rules"
    MONGODB_PRICE_GROUP_COLLECTION: str = "price_group"

    # Plants Collection
    MONGODB_PLANTS_COLLECTION: str = "plant_master"
    # Redis Configuration
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 2
    REDIS_PASSWORD: str = ""
    
    # File Storage — demand manual CSV uploads.
    # Default is relative to app working directory. Override in .env to an absolute
    # path outside the project if you want uploads to survive full redeploys.
    DEMAND_MANUAL_UPLOAD_DIR: str = "uploads/demand_manual"

    # Processing Configuration
    MAX_CONCURRENT_PROCESSES: int = 4
    PROCESSING_TIMEOUT_SECONDS: int = 600
    BATCH_SIZE: int = 500
    MEMORY_LIMIT_MB: int = 1024
    
    # Validation Configuration
    VALIDATION_ENABLED: bool = True
    STRICT_VALIDATION: bool = False
    ALLOW_PARTIAL_VALIDATION: bool = True
    
    # Transformation Configuration
    TRANSFORMATION_ENABLED: bool = True
    CACHE_TRANSFORMATIONS: bool = True
    TRANSFORMATION_CACHE_TTL: int = 3600
    
    # Data Quality Configuration
    DATA_QUALITY_ENABLED: bool = True
    MIN_DATA_QUALITY_SCORE: float = 0.8
    QUALITY_METRICS: List[str] = ["completeness", "accuracy", "consistency", "timeliness"]
    
    # JWT Configuration (for local token validation)
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_DELTA: int = 3600  # 1 hour
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # Authentication Service
    AUTH_SERVICE_URL: str = "http://localhost:8001/api/v1/auth"
    AUTH_SERVICE_TIMEOUT: int = 10
    
    # Ingestion Service
    INGESTION_SERVICE_URL: str = "http://localhost:8002"
    INGESTION_SERVICE_TIMEOUT: int = 30
    
    # Scheduler Service
    SCHEDULER_SERVICE_URL: str = "http://localhost:8004"
    SCHEDULER_SERVICE_TIMEOUT: int = 10
    
    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 30
    RATE_LIMIT_WINDOW_SECONDS: int = 3600

    # RabbitMQ Configuration
    RABBITMQ_HOST: str = "127.0.0.1"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"
    RABBITMQ_VHOST: str = "/"
    USE_RABBITMQ_JOBS: bool = True
    USE_RABBITMQ_AUDIT: bool = False

    # HMAC Authentication Configuration
    HMAC_ALLOWED_TIME_DRIFT: int = 300  # seconds (5 minutes)
    HMAC_NONCE_TTL: int = 300  # seconds (5 minutes)
    HMAC_RATE_LIMIT_REQUESTS: int = 100  # requests per window
    HMAC_RATE_LIMIT_WINDOW: int = 60  # seconds
    HMAC_REQUIRE_HTTPS: bool = True  # Require HTTPS in production
    MONGODB_API_KEYS_COLLECTION: str = "api_keys"
    
    # CORS Configuration
    CORS_ORIGINS: List[str] = [
        "http://localhost:3005", 
        "http://localhost:8080", 
        "https://dev.infiscopelabs.com"
    ]
    CORS_ALLOW_CREDENTIALS: bool = True
    
    # Health Check
    HEALTH_CHECK_ENABLED: bool = True
    HEALTH_CHECK_INTERVAL_SECONDS: int = 30

    # API Version Control
    ENABLE_V1_API: bool = False  # Set to False to disable v1 APIs

    # Environment Configuration
    ENVIRONMENT: Optional[str] = None  # Auto-detected if not set in .env
    
    MONGODB_WEATHER_SERVE: str = "weather_serve"

    MONGODB_DEMAND_SERVE_COLLECTION: str = "demand_serve"
    MONGODB_FORECAST_SERVE_COLLECTION: str = "forecast_serve"
    MONGODB_FORECAST_SERVE_HISTORICAL_COLLECTION: str = "forecast_serve_historical"
    MONGODB_TEMP_DEMAND_SERVE_COLLECTION: str = "temp_demand_serve"
    MONGODBT_EMP_DEMAND_SERVE_HISTORICAL_COLLECTION: str = "temp_demand_serve_historical" 
    MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION: str = "demand_serve_historical" 

    MONGODB_SUPPLY_SERVE_COLLECTION: str = "supply_serve"
    MONGODB_SUPPLY_SERVE_HISTORICAL_COLLECTION: str = "supply_serve_historical"
    
    # Market Collections
    MONGODB_SERVE_EXCHANGE_COLLECTION: str = "exchange_serve"
    MONGODB_EXCHANGE_SERVE_HISTORICAL_COLLECTION: str = "exchange_serve_historical"
    MONGODB_SERVE_PX_BID_COLLECTION: str = "bid_trade_serve"

    MONGODB_PEAK_DEMAND_SERVE_COLLECTION: str = "peak_demand_serve"

    MONGODB_PLANT_MASTER_COLLECTION: str = "plant_master"

    # Limit Values Collection
    MONGODB_LIMIT_VALUES_COLLECTION: str = "limit_values"
    MONGODB_LIMIT_VALUES_HISTORICAL_COLLECTION: str = "limit_values_historical"
    MONGODB_LIMIT_APPROVAL_COLLECTION: str = "limit_approval"
    MONGODB_LIMIT_AUDIT_COLLECTION: str = "limit_audit_log"

    MONGODB_DAM_BOOK_COLLECTION: str = "dam_book_collection"
    MONGODB_RTM_BOOK_COLLECTION: str = "rtm_book_collection"

    MONGODB_TIME_BLOCK_COLLECTION: str = "time_block"

    # DAM Bidding Collections
    MONGODB_DAM_SINGLE_BID_DATA_FOR_APPROVAL_COLLECTION: str = "dam_single_bid_data_for_approval"
    MONGODB_DAM_PRODUCT_HARDCODED_DATA_COLLECTION: str = "rtm_product_hardCoded_data"
    MONGODB_DAM_BID_CAPTURE_COLLECTION: str = "dam_bid_capture"
    MONGODB_DAM_BID_CAPTURE_MANUAL_COLLECTION: str = "dam_bid_capture_manual"

    # GDAM Bidding Collections
    MONGODB_GDAM_BOOK_COLLECTION: str = "gdam_book_collection"
    MONGODB_GDAM_SINGLE_BID_DATA_FOR_APPROVAL_COLLECTION: str = "gdam_single_bid_data_for_approval"
    MONGODB_GDAM_BID_CAPTURE_COLLECTION: str = "gdam_bid_capture"
    MONGODB_GDAM_BID_CAPTURE_MANUAL_COLLECTION: str = "gdam_bid_capture_manual"

    # HPDAM Bidding Collections
    MONGODB_HPDAM_BOOK_COLLECTION: str = "hpdam_book_collection"
    MONGODB_HPDAM_SINGLE_BID_DATA_FOR_APPROVAL_COLLECTION: str = "hpdam_single_bid_data_for_approval"
    MONGODB_HPDAM_BID_CAPTURE_COLLECTION: str = "hpdam_bid_capture"
    MONGODB_HPDAM_BID_CAPTURE_MANUAL_COLLECTION: str = "hpdam_bid_capture_manual"

    # RTM Bidding Collections
    MONGODB_RTM_SINGLE_BID_DATA_FOR_APPROVAL_COLLECTION: str = "rtm_single_bid_data_for_approval"
    MONGODB_RTM_BLOCK_BID_COLLECTION: str = "block_bid_current_data"
    MONGODB_RTM_BLOCK_BID_BOOK_COLLECTION: str = "rtm_block_bid_book_status"
    MONGODB_RTM_PRODUCT_HARDCODED_DATA_COLLECTION: str = "rtm_product_hardCoded_data"
    MONGODB_RTM_BID_CAPTURE_COLLECTION: str = "rtm_bid_capture"
    MONGODB_RTM_BID_CAPTURE_MANUAL_COLLECTION: str = "rtm_bid_capture_manual"
    MONGODB_RTM_OPTIMIZATION_RAW_INPUTS_SNAPSHOT_COLLECTION: str = "rtm_optimization_raw_inputs_snapshot"

    MONGODB_RENEWABLE_SERVE: str = "renewable_serve"
    MONGODB_RENEWABLE_SERVE_HISTORICAL: str = "renewable_serve_historical"

    # DAM Scheduling Collections
    MONGODB_DAM_SCHEDULING_COLLECTION: str = "serve_dam_scheduling"
    MONGODB_DAM_SCHEDULING_STAGING_COLLECTION: str = "dam_scheduling_staging"

    SERVER_DEPLOYMENT: bool = True
    
    @model_validator(mode='after')
    def clean_cors_origins(self):
        """
        Removes the wildcard '*' from CORS_ORIGINS if credentials are allowed.
        This is an invalid configuration for browsers and causes CORS errors.
        This validator prevents such misconfigurations from being deployed.
        """
        if self.CORS_ALLOW_CREDENTIALS and "*" in self.CORS_ORIGINS:
            logging.warning(
                "Removing wildcard ('*') from CORS_ORIGINS because "
                "CORS_ALLOW_CREDENTIALS is True. This is an invalid combination."
            )
            self.CORS_ORIGINS = [origin for origin in self.CORS_ORIGINS if origin != "*"]
        return self

    @model_validator(mode='after')
    def set_environment_config(self):
        """Set environment-aware configurations after validation"""
        if self.ENVIRONMENT is None:
            # Use proper environment detection with defaults
            docker_container = os.getenv("DOCKER_CONTAINER", "").lower() in ("true", "1", "yes")
            has_processing_port = os.getenv("PROCESSING_PORT") is not None
            server_deployment = os.getenv("SERVER_DEPLOYMENT", "").lower() in ("true", "1", "yes")
            nginx_proxy = os.getenv("NGINX_PROXY") is not None
            
            if docker_container or has_processing_port:
                self.ENVIRONMENT = "docker"
            elif server_deployment or nginx_proxy:
                self.ENVIRONMENT = "server"
            else:
                self.ENVIRONMENT = "local"
        
        return self
    
    @property
    def BASE_PATH(self) -> str:
        """Get base path for current environment (computed dynamically)"""
        return "/processing" if self.ENVIRONMENT in ["server", "docker"] else ""

    class Config:
        # Find .env file in the project root, not the service directory
        _project_root = Path(__file__).parent.parent.parent.parent
        extra = "ignore"  # Ignore extra fields from environment
        env_file = str(_project_root / ".env")
        case_sensitive = True
        extra = "ignore"  # Ignore extra environment variables that aren't defined as fields


# Create settings instance
settings = Settings() 
