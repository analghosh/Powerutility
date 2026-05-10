"""
Configuration settings for Scheduler Service

IMPORTANT DATABASE ARCHITECTURE:
================================

This service uses TWO separate databases:

1. EPMS DATABASE (epms):
   - Used for business data (demand_serve, supply_serve, etc.)
   - Legacy configurations (MONGODB_URI, MONGODB_DATABASE)
   - NOT used for job data

2. AUDIT DATABASE (audit_db):
   - EXCLUSIVELY used for job-related data
   - Collections: job_data, job_detail, job_detail_historical
   - All job execution logs and configurations go here

CRITICAL RULE: Job data must NEVER be stored in the epms database.
All job repositories are configured to use audit_db only.
"""

import os
from pickle import TRUE
from typing import List
from pydantic_settings import BaseSettings
from pydantic import field_validator
import json
from dotenv import load_dotenv

# Load .env file explicitly
load_dotenv(override=True)


class Settings(BaseSettings):
    """Application settings"""
    
    # Service Configuration
    SERVICE_NAME: str = "scheduler-service"
    SERVICE_PORT: int = 8004
    SERVICE_HOST: str = "0.0.0.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "colored"
    DEDUPLICATION_ONLY: bool = True
    
    # URL Configuration
    BASE_PATH: str = ""  # Set to "/scheduler" in production, empty for local
    
    # ⚠️  LEGACY DATABASE CONFIGURATION ⚠️
    # These settings are for backward compatibility ONLY
    # JOB DATA MUST NEVER BE STORED IN EPMS DATABASE
    # All job data goes to audit_db via AUDIT_DB_URI and AUDIT_DB_NAME
    MONGODB_URI: str = "mongodb://localhost:27017/epms"
    MONGODB_DATABASE: str = "epms"
    MONGODB_JOBS_COLLECTION: str = "scheduled_jobs"
    MONGODB_JOB_HISTORY_COLLECTION: str = "job_history"
    MONGODB_CRON_JOBS_COLLECTION: str = "cron_jobs"
    
    # Redis Configuration
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 3
    REDIS_PASSWORD: str = ""
    REDIS_SOCKET_TIMEOUT: int = 5
    REDIS_SOCKET_CONNECT_TIMEOUT: int = 5
    REDIS_MAX_CONNECTIONS: int = 10
    
    # Scheduler Configuration
    MAX_CONCURRENT_JOBS: int = 10
    JOB_TIMEOUT_SECONDS: int = 3600
    JOB_RETRY_ATTEMPTS: int = 3
    JOB_RETRY_DELAY_SECONDS: int = 60
    
    # Cron Configuration
    CRON_ENABLED: bool = True
    CRON_CHECK_INTERVAL_SECONDS: int = 30
    CRON_TIMEZONE: str = "Asia/Kolkata"
    
    # Startup Execution Configuration
    RUN_ALL_JOBS_ON_STARTUP: bool = True  # Set to True to run all jobs during startup
    STARTUP_JOB_DELAY_SECONDS: int = 5  # Delay before running jobs on startup
    STARTUP_JOB_INTERVAL_SECONDS: int = 2  # Delay between individual jobs during startup
    
    # System Cleanup Configuration
    SYSTEM_CLEANUP_ENABLED: bool = True
    SYSTEM_CLEANUP_RETENTION_DAYS: int = 90
    SYSTEM_CLEANUP_SCHEDULE: str = "0 2 * * *"  # Daily at 2 AM
    
    RETRY_FAILED_JOBS_ENABLED: bool = True
    RETRY_FAILED_JOBS_SCHEDULE: str = "*/15 * * * *"  # Every 15 minutes
    

    
    # Demand Data Management Cron Job
    DEMAND_DATA_CRON_ENABLED: bool = False
    DEMAND_DATA_CRON_SCHEDULE: str = "0 1 * * *"  # Daily at 1 AM
    DEMAND_DATA_CRON_TIMEZONE: str = "Asia/Kolkata"
    
    # Supply Data Management Cron Job
    SUPPLY_DATA_CRON_ENABLED: bool = False
    SUPPLY_DATA_CRON_SCHEDULE: str = "0 1 * * *"  # Daily at 1 AM
    SUPPLY_DATA_CRON_TIMEZONE: str = "Asia/Kolkata"

    
    # Demand Data Processing Management Cron Job
    DEMAND_DATA_PROCESSING_CRON_ENABLED: bool = True
    DEMAND_DATA_PROCESSING_CRON_SCHEDULE: str = "*/15 * * * *"  # Every 15 minutes
    DEMAND_DATA_PROCESSING_CRON_TIMEZONE: str = "Asia/Kolkata"
    
    # Supply Data Processing Management Cron Job
    SUPPLY_DATA_PROCESSING_CRON_ENABLED: bool = True
    SUPPLY_DATA_PROCESSING_CRON_SCHEDULE: str = "*/15 * * * *"  # Every 15 minutes
    SUPPLY_DATA_PROCESSING_CRON_TIMEZONE: str = "Asia/Kolkata"
    
    # MongoDB Collections (Time Series)
    MONGODB_DUMMY_COLLECTION: str = "dummy"
    MONGODB_DEMAND_SERVE_COLLECTION: str = "demand_serve"
    MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION: str = "demand_serve_historical"
    MONGODB_SUPPLY_SERVE_COLLECTION: str = "supply_serve"
    MONGODB_SUPPLY_SERVE_HISTORICAL_COLLECTION: str = "supply_serve_historical"
    MONGODB_DC_SG_COLLECTION: str = "dc_sg"  # 1-day temporary storage for supply data
    
    # Audit Database Configuration
    AUDIT_DB_URI: str = "mongodb://localhost:27017/audit_db"
    AUDIT_DB_NAME: str = "audit_db"
    AUDIT_COLLECTION_NAME: str = "audit_logs"
    QUEUE_COLLECTION_NAME: str = "audit_queue_jobs"
    AUDIT_CRON_LOGS_COLLECTION: str = "cron_job_logs"
    AUDIT_SYSTEM_FLAGS_COLLECTION: str = "system_flags"
    
    # Job Data Collections (EXCLUSIVELY in audit_db - never in epms database)
    JOB_DATA_COLLECTION: str = "job_data"
    JOB_DETAIL_COLLECTION: str = "job_detail"
    JOB_DETAIL_HISTORICAL_COLLECTION: str = "job_detail_historical"
    MONGODB_DAM_SINGLE_BID_DATA_FOR_APPROVAL_COLLECTION: str = "dam_single_bid_data_for_approval"
    MONGODB_RTM_SINGLE_BID_DATA_FOR_APPROVAL_COLLECTION: str = "rtm_single_bid_data_for_approval"

    
    # Job Execution Logs Lifecycle Configuration
    JOB_LOGS_MIGRATION_THRESHOLD_DAYS: int = 7
    JOB_LOGS_HISTORICAL_RETENTION_DAYS: int = 60
    JOB_LOGS_MIGRATION_SCHEDULE: str = "0 2 * * *"  # Daily at 2 AM
    JOB_LOGS_CLEANUP_SCHEDULE: str = "0 3 * * *"  # Daily at 3 AM
    JOB_LOGS_MIGRATION_BATCH_SIZE: int = 1000
    JOB_LOGS_MIGRATION_TIMEOUT: int = 3600
    
    # Time Series Configuration
    TIME_SERIES_WINDOW_DAYS: int = 4  # Keep 4 days in demand_serve
    TIME_SERIES_BATCH_SIZE: int = 1000  # Batch size for time series operations
    
    # Authentication Service
    AUTH_SERVICE_URL: str = "http://localhost:8001"
    AUTH_SERVICE_TIMEOUT: int = 10
    
    # Ingestion Service
    INGESTION_SERVICE_URL: str = "http://localhost:8002"
    INGESTION_SERVICE_TIMEOUT: int = 30
    
    # Processing Service
    PROCESSING_SERVICE_URL: str = "http://localhost:8003"
    PROCESSING_SERVICE_TIMEOUT: int = 30
    
    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 20
    RATE_LIMIT_WINDOW_SECONDS: int = 3600
    
    # CORS Configuration
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8080"]
    CORS_ALLOW_CREDENTIALS: bool = True

    @field_validator('CORS_ORIGINS', mode='before')
    @classmethod
    def parse_cors_origins(cls, v):
        """Parse CORS_ORIGINS from string to list if needed"""
        if isinstance(v, str):
            try:
                # Try to parse as JSON array
                return json.loads(v)
            except json.JSONDecodeError:
                # If JSON parsing fails, split by comma and strip whitespace
                return [origin.strip() for origin in v.split(',')]
        return v

    # Timeblock Master Collection
    MONGODB_TIMEBLOCK_MASTER_COLLECTION: str = "time_block"
    
    # Health Check
    HEALTH_CHECK_ENABLED: bool = True
    HEALTH_CHECK_INTERVAL_SECONDS: int = 30

    MONGODB_BID_TRADE_SERVE_HISTORICAL_COLLECTION: str = "bid_trade_serve_historical"
    MONGODB_BID_TRADE_SERVE_COLLECTION: str = "bid_trade_serve"

    MONGODB_WEATHER_SERVE_COLLECTION: str = "weather_serve"
    MONGODB_WEATHER_SERVE_HISTORICAL_COLLECTION: str = "weather_serve_historical"

    
    # Market Data Scraper Configuration
    MONGODB_IEX_EXCHANGE_RAW_COLLECTION: str = "iex_exchange_raw"
    MONGODB_EXCHANGE_SERVE_COLLECTION: str = "exchange_serve"
    MONGODB_EXCHANGE_SERVE_HISTORICAL_COLLECTION: str = "exchange_serve_historical"
    # Market Data Scraper Settings
    IEX_EXCHANGE_SCRAPER_TIMEOUT: int = 30
    IEX_EXCHANGE_SCRAPER_RETRY_ATTEMPTS: int = 3
    IEX_EXCHANGE_SCRAPER_RETRY_DELAY: int = 5
    IEX_EXCHANGE_SCRAPER_ARCHIVAL_DAYS: int = 15
    IEX_EXCHANGE_SCRAPER_RETENTION_DAYS: int = 3

    # Demand Supply Date Update Configuration
    DEMAND_COLLECTION: str = "serve_demand_table"
    DEMAND_HISTORICAL_COLLECTION: str = "serve_demand_table_historical"
    SUPPLY_COLLECTION: str = "serve_supply_table"
    SUPPLY_HISTORICAL_COLLECTION: str = "serve_supply_table_historical"
    PX_BID_TABLE: str = "serve_px_bid_trade"
    EXCHANGE_TABLE: str = "serve_exchange_table"

    # Plant Master Collection
    MONGODB_PLANTS_COLLECTION: str = "plant_master"
    
    # Market Data Queue Configuration
    IEX_EXCHANGE_SCRAPER_QUEUE_NAME: str = "iex_exchange_scraper_queue"

    MONGODB_DEMAND_RAW_DATA_COLLECTION: str = "demand_raw_data"
    MONGODB_LIMIT_VALUES_RAW_DATA_COLLECTION: str = "limit_values_raw_data"
    MONGODB_LIMIT_VALUES_COLLECTION: str = "limit_values"

    TIMESERIES_TIME_FIELD: str = "created_at"
    TIMESERIES_META_FIELD: str = "metadata"
    TIMESERIES_GRANULARITY: str = "seconds" 

    # Processing crons
    DEMAND_DATA_PROCESSING_CRON: str = "*/15 * * * *"

    WEATHER_DATA_PROCESSING_CRON: str = "*/15 * * * *"
    WEATHER_DATA_PROCESSING_ENABLED: bool = True

    BID_DATA_PROCESSING_CRON: str = "*/15 * * * *"
    BID_DATA_PROCESSING_ENABLED: bool = True

    EXCHANGE_DATA_PROCESSING_CRON: str = "*/15 * * * *"
    EXCHANGE_DATA_PROCESSING_ENABLED: bool = True

    SUPPLY_DATA_PROCESSING_CRON: str = "*/15 * * * *"
    SUPPLY_DATA_PROCESSING_ENABLED: bool = True

    EXCHANGE_DSM_PROCESSING_CRON: str = "*/15 * * * *"
    EXCHANGE_DSM_PROCESSING_ENABLED: bool = True

    SERVE_DATA_RECONCILE_CRON_ENABLED: bool = True
    SERVE_DATA_RECONCILE_CRON: str = "0 */6 * * *"
    
    DATA_UPDATE_CRON: str = "0 0 * * *"
    DATA_UPDATE_CRON_ENABLED: bool = True
    
    # DSM Rate Calculation Configuration
    DSM_RATE_CALCULATION_CRON: str = "*/15 * * * *"  # Every 15 minutes
    DSM_RATE_CALCULATION_ENABLED: bool = False
    NEW_DSM_RATE_CALCULATION_CRON: str = "*/15 * * * *"  # Every 15 minutes
    NEW_DSM_RATE_CALCULATION_ENABLED: bool = True
    
    # Limit Values Data Processing Configuration
    LIMIT_VALUES_DATA_PROCESSING_CRON: str = "*/15 * * * *"  # Every 15 minutes
    LIMIT_VALUES_DATA_PROCESSING_ENABLED: bool = True
    
    MONGODB_RENEWABLE_SERVE_COLLECTION: str = "renewable_serve"
    MONGODB_RENEWABLE_SERVE_HISTORICAL_COLLECTION: str = "renewable_serve_historical"
    MONGODB_DC_SG_RENEWABLE_COLLECTION: str = "dc_sg_renewable"
    
    # Single Bid Book Collections
    MONGODB_DAM_BOOK_COLLECTION: str = "dam_book_status"
    MONGODB_RTM_BOOK_COLLECTION: str = "rtm_book_status"
    # Block Bid Book Collection
    MONGODB_RTM_BLOCK_BID_BOOK_COLLECTION: str = "rtm_block_bid_book_status"
    MONGODB_DAM_SINGLE_BID_DATA_FOR_APPROVAL_COLLECTION: str = "dam_single_bid_data_for_approval"
    MONGODB_RTM_SINGLE_BID_DATA_FOR_APPROVAL_COLLECTION: str = "rtm_single_bid_data_for_approval"
    
    class Config:
        """Pydantic configuration for Settings class"""
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"


# Create settings instance
settings = Settings() 
