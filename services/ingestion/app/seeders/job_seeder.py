"""
Ingestion Job Seeder
Seeds ingestion-specific cron job configurations
"""

import logging
from typing import List, Dict, Any
from libs.core.job_seeder import BaseJobSeeder
from libs.core.job_repositories import JobDataRepository
from ..core.config import settings

logger = logging.getLogger(__name__)


class IngestionJobSeeder(BaseJobSeeder):
    """Seeder for ingestion-specific cron jobs"""
    
    def __init__(self, job_data_repository: JobDataRepository):
        """Initialize ingestion job seeder"""
        super().__init__(job_data_repository, "ingestion")
        self.settings = settings
    
    def get_default_jobs(self) -> List[Dict[str, Any]]:
        """Get default ingestion jobs"""
        return [
            {
                "job_type": "upload_raw_data_processor",
                "name": "Upload Raw Data Processor",
                "type": "raw_data_processor",
                "schedule": getattr(self.settings, 'UPLOAD_RAW_DATA_PROCESSOR_CRON_SCHEDULE', "*/2 * * * *"),  # Every 2 minutes
                "enabled": getattr(self.settings, 'UPLOAD_RAW_DATA_PROCESSOR_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Process uploaded files in the background queue",
                "priority": 1  # High priority for file processing
            },
            {
                "job_type": "scrape_raw_data_processor",
                "name": "Scrape Raw Data Processor",
                "type": "raw_data_processor",
                "schedule": getattr(self.settings, 'SCRAPE_RAW_DATA_PROCESSOR_CRON_SCHEDULE', "*/15 * * * *"),  # Every 15 minutes
                "enabled": getattr(self.settings, 'SCRAPE_RAW_DATA_PROCESSOR_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Process scraped files in the background queue",
                "priority": 1  # High priority for file processing
            },
            {
                "job_type": "ftp_raw_data_processor",
                "name": "FTP Raw Data Processor",
                "type": "raw_data_processor",
                "schedule": getattr(self.settings, 'FTP_RAW_DATA_PROCESSOR_CRON_SCHEDULE', "*/15 * * * *"),  # Every 30 minutes
                "enabled": getattr(self.settings, 'FTP_RAW_DATA_PROCESSOR_ENABLED', True),  # Enabled now that it's implemented
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Download and process files from FTP/SFTP servers with dynamic schema mapping",
                "priority": 2  # Medium priority for FTP processing
            },
            {
                "job_type": "api_raw_data_processor",
                "name": "API Raw Data Processor",
                "type": "raw_data_processor",
                "schedule": getattr(self.settings, 'API_RAW_DATA_PROCESSOR_CRON_SCHEDULE', "0,15,30,45 * * * *"),
                "enabled": getattr(self.settings, 'API_RAW_DATA_PROCESSOR_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Fetch and process data from REST APIs with weather data transposition support",
                "priority": 2
            },
            {
                "job_type": "api_raw_data_processor_offset",
                "name": "API Raw Data Processor (Offset)",
                "type": "raw_data_processor",
                "schedule": getattr(self.settings, 'API_RAW_DATA_PROCESSOR_OFFSET_CRON_SCHEDULE', "8,23,38,53 * * * *"),
                "enabled": getattr(self.settings, 'API_RAW_DATA_PROCESSOR_OFFSET_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Fetch and process data from REST APIs for providers with ingestion_type=api-v2",
                "priority": 2
            },
            {
                "job_type": "forecast_api_processor",
                "name": "Forecast API Processor",
                "type": "raw_data_processor",
                "schedule": getattr(self.settings, 'FORECAST_API_PROCESSOR_CRON_SCHEDULE', "0 7 * * *"),  # Every day at 7 AM
                "enabled": getattr(self.settings, 'FORECAST_API_PROCESSOR_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Fetch forecast data from external API and store in limit_values and demand_serve",
                "priority": 2  # Medium priority for forecast processing
            },
            {
                "job_type": "schedule_api_processor",
                "name": "Schedule API Processor",
                "type": "raw_data_processor",
                "schedule": getattr(self.settings, 'SCHEDULE_API_PROCESSOR_CRON_SCHEDULE', "15 8,14 * * *"),  # Every day at 08:15 and 14:15
                "enabled": getattr(self.settings, 'SCHEDULE_API_PROCESSOR_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Fetch schedule data from external API and store in supply_serve",
                "priority": 2  # Medium priority for schedule processing
            },
            {
                "job_type": "rtm_forecast_api_processor",
                "name": "RTM Forecast API Processor",
                "type": "raw_data_processor",
                "schedule": getattr(self.settings, 'RTM_FORECAST_API_PROCESSOR_CRON_SCHEDULE', "*/30 * * * *"),  # Every 30 minutes
                "enabled": getattr(self.settings, 'RTM_FORECAST_API_PROCESSOR_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Fetch RTM forecast/schedule from external API into serve_dam_scheduling (market_type=RTM)",
                "priority": 2
            }
        ]


# Global ingestion job seeder instance
def create_ingestion_job_seeder(job_data_repository: JobDataRepository) -> IngestionJobSeeder:
    """Create ingestion job seeder instance"""
    return IngestionJobSeeder(job_data_repository)
