"""
Ingestion Service Cron Job Manager
Handles scheduling and execution of ingestion-specific cron jobs
"""

import os
import sys
from datetime import datetime
from typing import Optional, Callable

from libs.audit_queue.simple_logger import AuditLogger
from libs.core.cron_manager import BaseCronManager
from libs.core.job_factory import create_job_data_repository

from services.ingestion.app.core.config import settings
from services.ingestion.app.core.logger import get_ingestion_logger
from services.ingestion.app.processors.scrape_raw_data_processor import ScrapeRawDataProcessor
from services.ingestion.app.processors.upload_raw_data_processor import UploadRawDataProcessor
from services.ingestion.app.processors.ftp_raw_data_processor import FTPRawDataProcessor
from services.ingestion.app.processors.api_raw_data_processor import APIRawDataProcessor
from services.ingestion.app.processors.forecast_api_processor import ForecastAPIProcessor
from services.ingestion.app.processors.schedule_api_processor import ScheduleAPIProcessor
from services.ingestion.app.processors.rtm_forecast_api_processor import RTMForecastAPIProcessor
from services.ingestion.app.seeders.job_seeder import create_ingestion_job_seeder

logger = get_ingestion_logger("ingestion_cron_manager")

class CronManager(BaseCronManager):
    """Ingestion service cron manager with ingestion-specific jobs"""
    
    def __init__(self, audit_logger: AuditLogger):
        """Initialize the ingestion cron manager"""
        self.audit_logger = audit_logger
        # Create job data repository using config settings
        try:
            job_data_repository = create_job_data_repository(
                getattr(settings, 'AUDIT_DB_URI', settings.MONGODB_URI),
                getattr(settings, 'AUDIT_DB_NAME', 'audit_db'),
                getattr(settings, 'JOB_DATA_COLLECTION', 'job_data')
            )
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning("Could not initialize job data repository for ingestion: %s", str(e))
            job_data_repository = None
        
        # Create ingestion-specific job seeder using the repository
        cron_job_seeder = None
        try:
            if job_data_repository:
                cron_job_seeder = create_ingestion_job_seeder(
                    job_data_repository
                )
            else:
                logger.warning("Job data repository not available, skipping job seeder initialization")
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning("Could not initialize job seeder for ingestion: %s", str(e))
        
        # Initialize base class with the repository
        super().__init__(
            service_name="ingestion",
            timezone=getattr(settings, 'CRON_TIMEZONE', 'UTC'),
            job_data_repository=job_data_repository,
            audit_logger=audit_logger
        )
        
        # Set the seeder after base class initialization
        self.cron_job_seeder = cron_job_seeder
        
        # Now register the jobs since seeder is available
        self._register_service_jobs()
        
        logger.info("Initialized ingestion cron manager")
    
    
    def _get_job_function(self, job_type: str) -> Optional[Callable]:
        """Get the execution function for an ingestion job type"""
        job_functions = {
            "upload_raw_data_processor": self._execute_background_file_processing_job,
            "scrape_raw_data_processor": self._execute_scrape_processing_job,
            "ftp_raw_data_processor": self._execute_ftp_processing_job,
            "api_raw_data_processor": self._execute_api_processing_job,
            "forecast_api_processor": self._execute_forecast_api_processing_job,
            "schedule_api_processor": self._execute_schedule_api_processing_job,
            "rtm_forecast_api_processor": self._execute_rtm_forecast_api_processing_job,
        }
        return job_functions.get(job_type)
    
    def _register_service_jobs(self):
        """Register ingestion-specific jobs"""
        logger.info("Registering ingestion-specific cron jobs...")
        
        # Skip if seeder is not available yet (will be called again after initialization)
        if not hasattr(self, 'cron_job_seeder') or self.cron_job_seeder is None:
            logger.info("Job seeder not available yet, skipping job registration")
            return
        
        # Try to seed jobs from shared seeder first
        try:
            self.cron_job_seeder.seed_default_jobs()
            # Get jobs from database if seeder is available
            if self.job_data_repository:
                jobs = self.job_data_repository.get_all_jobs(enabled_only=True, service="ingestion")
                registered_count = 0
                for job_config in jobs:
                    job_function = self._get_job_function(job_config['job_type'])
                    if job_function:
                        success = self.register_job(
                            job_type=job_config['job_type'],
                            name=job_config['name'],
                            schedule=job_config['schedule'],
                            func=job_function,
                            enabled=job_config['enabled'],
                            timezone=job_config.get('timezone', 'UTC')
                        )
                        if success:
                            registered_count += 1
                logger.info("Registered %s ingestion jobs from database", registered_count)
                return
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning("Failed to register jobs from database: %s", str(e))
    
    async def _execute_background_file_processing_job(self):
        """Execute background file processing job by delegating to UploadRawDataProcessor"""
        logger.info("=== EXECUTING BACKGROUND FILE PROCESSING JOB ===")
        logger.info("Job started at: %s", datetime.now().isoformat())
        
        try:
            # Delegate to the UploadRawDataProcessor service
            result = await UploadRawDataProcessor(self.audit_logger).execute_cron_job()
            logger.info("Job completed successfully with result: %s", result)
            return result
            
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.error("Error executing background file processing cron job: %s", str(e))
            return {'status': 'error', 'error': str(e)}
    
    async def _execute_scrape_processing_job(self):
        """Execute scrape processing job by delegating to ScrapeRawDataProcessor"""
        logger.info("=== EXECUTING SCRAPE PROCESSING JOB ===")
        logger.info("Job started at: %s", datetime.now().isoformat())
        
        try:
            # Delegate to the ScrapeRawDataProcessor service
            result = await ScrapeRawDataProcessor(self.audit_logger).execute_cron_job()
            logger.info("Job completed successfully with result: %s", result)
            return result
            
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.error("Error executing scrape processing cron job: %s", str(e))
            return {'status': 'error', 'error': str(e)}

    async def _execute_ftp_processing_job(self):
        """Execute FTP processing job by delegating to FTPRawDataProcessor"""
        logger.info("=== EXECUTING FTP PROCESSING JOB ===")
        logger.info("Job started at: %s", datetime.now().isoformat())
        
        try:
            # Delegate to the FTPRawDataProcessor service
            result = await FTPRawDataProcessor(self.audit_logger).execute_cron_job()
            logger.info("Job completed successfully with result: %s", result)
            return result
            
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.error("Error executing FTP processing cron job: %s", str(e))
            return {'status': 'error', 'error': str(e)}

    async def _execute_api_processing_job(self):
        """Execute API processing job by delegating to APIRawDataProcessor"""
        logger.info("=== EXECUTING API PROCESSING JOB ===")
        logger.info("Job started at: %s", datetime.now().isoformat())
        
        try:
            # Delegate to the APIRawDataProcessor service
            result = await APIRawDataProcessor(self.audit_logger).execute_cron_job()
            logger.info("Job completed successfully with result: %s", result)
            return result
            
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.error("Error executing API processing cron job: %s", str(e))
            return {'status': 'error', 'error': str(e)}

    async def _execute_forecast_api_processing_job(self):
        """Execute Forecast API processing job by delegating to ForecastAPIProcessor"""
        logger.info("=== EXECUTING FORECAST API PROCESSING JOB ===")
        logger.info("Job started at: %s", datetime.now().isoformat())
        
        try:
            # Delegate to the ForecastAPIProcessor service
            result = await ForecastAPIProcessor(self.audit_logger).execute_cron_job()
            logger.info("Job completed successfully with result: %s", result)
            return result
            
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.error("Error executing Forecast API processing cron job: %s", str(e))
            return {'status': 'error', 'error': str(e)}

    async def _execute_schedule_api_processing_job(self):
        """Execute Schedule API processing job by delegating to ScheduleAPIProcessor"""
        logger.info("=== EXECUTING SCHEDULE API PROCESSING JOB ===")
        logger.info("Job started at: %s", datetime.now().isoformat())
        
        try:
            # Delegate to the ScheduleAPIProcessor service
            result = await ScheduleAPIProcessor(self.audit_logger).execute_cron_job()
            logger.info("Job completed successfully with result: %s", result)
            return result
            
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.error("Error executing Schedule API processing cron job: %s", str(e))
            return {'status': 'error', 'error': str(e)}

    async def _execute_rtm_forecast_api_processing_job(self):
        """Execute RTM Forecast API processing job by delegating to RTMForecastAPIProcessor"""
        logger.info("=== EXECUTING RTM FORECAST API PROCESSING JOB ===")
        logger.info("Job started at: %s", datetime.now().isoformat())
        try:
            result = await RTMForecastAPIProcessor(self.audit_logger).execute_cron_job()
            logger.info("Job completed successfully with result: %s", result)
            return result
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.error("Error executing RTM Forecast API processing cron job: %s", str(e))
            return {'status': 'error', 'error': str(e)}

    async def execute_job_immediately(self, job_id: str):
        """Execute a job immediately without waiting for scheduler"""
        try:
            if job_id in self._registered_jobs:
                job_func = self._registered_jobs[job_id].func
                logger.info("Executing job '%s' immediately...", job_id)
                result = await job_func()
                logger.info("Job '%s' executed immediately with result: %s", job_id, result)
                return result
            else:
                logger.warning("Job '%s' not found in registered jobs", job_id)
                return None
        except Exception as e:
            logger.error("Error executing job '%s' immediately: %s", job_id, str(e))
            return None
    
    def _execute_job_with_logging(self, cron_job):
        """Execute a job with smart logging - override to match base class signature"""
        import time
        start_time = time.time()
        
        job_name = cron_job.name
        job_func = cron_job.func
        job_detail_id = f"ingestion_{int(time.time())}"
        
        try:
            logger.info("Starting ingestion job: %s", job_name)

            # Log job start
            self.audit_logger.log_user_action(
                user_id="system",
                action="cron_job_started",
                service="ingestion",
                event_type="cron_job_started",
                message=f"Cron job started: {job_name}",
                job_detail_id=job_detail_id,
                job_name=job_name,
                job_type="data_ingestion",
                execution_mode="scheduled"
            )

            # Execute the job
            result = job_func()
            # Calculate duration
            duration_ms = int((time.time() - start_time) * 1000)
            # Log successful completion
            self.audit_logger.log_user_action(
                user_id="system",
                action="cron_job_completed",
                service="ingestion",
                event_type="cron_job_completed",
                message=f"Cron job completed: {job_name}",
                job_detail_id=job_detail_id,
                job_name=job_name,
                job_type="data_ingestion",
                execution_mode="scheduled",
                duration_ms=duration_ms,
                duration_seconds=duration_ms / 1000,
                success=True
            )

            logger.info("Completed ingestion job: %s", job_name)
            return result
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)

            # Log job failure
            self.audit_logger.log_user_action(
                user_id="system",
                action="cron_job_failed",
                service="ingestion",
                event_type="cron_job_failed",
                message=f"Cron job failed: {job_name} - {str(e)}",
                job_detail_id=job_detail_id,
                job_name=job_name,
                job_type="data_ingestion",
                execution_mode="scheduled",
                duration_ms=duration_ms,
                duration_seconds=duration_ms / 1000,
                success=False,
                exception_type=type(e).__name__
            )

            logger.error("Failed ingestion job %s: %s", job_name, str(e))
            raise
