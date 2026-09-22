"""
Scheduler Service Cron Job Manager
Handles scheduling and execution of scheduler-specific cron jobs
Updated: Added missing methods to libs JobDetailRepository
"""

import logging
import asyncio
import time
import re
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from collections import defaultdict
import sys
import os

# Add project root to path for audit queue import
project_root = os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..')
if project_root not in sys.path:
    sys.path.append(project_root)

from libs.audit_queue.simple_logger import AuditLogger
from libs.core.cron_manager import BaseCronManager
from libs.core.job_entities import JobDetailQuery, JobDetail
from libs.core.job_factory import create_job_data_repository, create_job_detail_repository
from libs.core.job_repositories import JobDataRepository, JobDetailRepository
from services.scheduler.app.seeder.job_seeder import create_scheduler_job_seeder
from services.scheduler.app.core.config import settings
from services.scheduler.app.services.dummy_data_service import dummy_data_service, DummyDataService
# Temporarily commented out due to import issues
# from services.scheduler.app.exchange.services.iex.data_scrapper import data_scrapper
# from services.scheduler.app.exchange.services.iex.data_processor import data_processor
from services.scheduler.app.services.date_update_service import DateUpdate
from services.scheduler.app.processors.weather_processor import WeatherProcessor
from services.scheduler.app.processors.bid_processor import BidProcessor
from services.scheduler.app.processors.exchange_processor import ExchangeProcessor
from services.scheduler.app.processors.demand_processor import DemandProcessor
from services.scheduler.app.processors.demand_schedule_processor import DemandScheduleProcessor
from services.scheduler.app.processors.supply_processor import SupplyProcessor
from services.scheduler.app.processors.renewable_processor import RenewableProcessor
from services.scheduler.app.processors.dsm_processor import DSMProcessor
from services.scheduler.app.processors.new_dsm_processor import NewDSMProcessor
from services.scheduler.app.processors.limit_values_processor import LimitValuesProcessor

from services.scheduler.app.processors.rtm_scheduling_processor import RTMSchedulingProcessor
from services.scheduler.app.processors.supply_fill_processor import SupplyFillProcessor
from services.scheduler.app.processors.bid_status_sync_processor import BidStatusSyncProcessor
from services.scheduler.app.processors.single_bid_book_processor import SingleBidBookProcessor
from services.scheduler.app.processors.block_bid_book_processor import BlockBidBookProcessor
from services.scheduler.app.processors.bid_trade_live_sync_processor import BidTradeLiveSyncProcessor
from services.scheduler.app.processors.supply_tomorrow_sync_processor import SupplyTomorrowSyncProcessor
from services.scheduler.app.processors.demand_temp_sync_processor import DemandTempSyncProcessor
from services.scheduler.app.processors.idf_forecast_merge_processor import IdfForecastMergeProcessor
from services.scheduler.app.processors.demand_serve_pipeline_processor import DemandServePipelineProcessor
from services.scheduler.app.processors.exchange_dsm_processor import ExchangeDsmProcessor
from services.scheduler.app.processors.rtm_bid_lapse_processor import RTMBidLapseProcessor
from services.scheduler.app.processors.dam_bid_capture_processor import DAMBidCaptureProcessor
from services.scheduler.app.processors.gdam_bid_capture_processor import GDAMBidCaptureProcessor
from services.scheduler.app.processors.hpdam_bid_capture_processor import HPDAMBidCaptureProcessor
from services.scheduler.app.processors.rtm_bid_capture_processor import RTMBidCaptureProcessor
from services.scheduler.app.processors.analytics_export_processor import analytics_export_processor
from services.scheduler.app.services.peak_demand_service import peak_demand_service
from services.scheduler.app.utils.log_capture import context_aware_handler
logger = logging.getLogger(__name__)


class CronManager(BaseCronManager):
    """Scheduler service cron manager with scheduler-specific jobs"""
    
    def __init__(self):
        """Initialize the scheduler cron manager"""
        # Type annotations for repository instances
        self.job_data_repository: Optional[JobDataRepository] = None
        self.job_detail_repository: Optional[JobDetailRepository] = None

        # Coalescing state: prevents the same job_type from running concurrently
        # (cron + RabbitMQ triggers stampeding the same processing). See
        # _execute_job_with_logging_async for the lock + rerun-if-dirty logic.
        self._jobs_in_flight: set = set()
        self._jobs_pending_rerun: set = set()

        # Initialize audit logger
        try:
            from libs.audit_queue.unified_queue_service import UnifiedQueueService
            unified_queue_service = UnifiedQueueService(
                mongodb_uri=settings.MONGODB_URI,
                audit_db_name=settings.AUDIT_DB_NAME,
                audit_collection_name=settings.AUDIT_COLLECTION_NAME,
                queue_collection_name=settings.QUEUE_COLLECTION_NAME
            )
            self.audit_logger = AuditLogger(unified_queue_service)
        except Exception as audit_init_error:
            logger.warning("Failed to initialize audit logger: %s", audit_init_error)
            self.audit_logger = None
        
        # Initialize scheduler-specific services
        # Linter refresh comment
        # self.data_scrapper = data_scrapper  # Temporarily commented out
        # self.data_processor = data_processor  # Temporarily commented out
        self.date_updater = DateUpdate()
        
        # Initialize processor instances
        self.weather_processor = WeatherProcessor()
        self.bid_processor = BidProcessor()
        self.exchange_processor = ExchangeProcessor()
        self.demand_processor = DemandProcessor()
        self.demand_schedule_processor = DemandScheduleProcessor()
        self.peak_demand_service = peak_demand_service  
        self.supply_processor = SupplyProcessor()  # Add supply processor
        self.renewable_processor = RenewableProcessor()  # Add renewable processor
        self.dsm_processor = DSMProcessor()  # Add DSM processor
        self.new_dsm_processor = NewDSMProcessor()  # Add New DSM processor
        self.limit_values_processor = LimitValuesProcessor()  # Add limit values processor

        self.rtm_scheduling_processor = RTMSchedulingProcessor()  # Add RTM scheduling processor
        self.supply_fill_processor = SupplyFillProcessor()  # Add Supply Fill processor
        self.bid_status_sync_processor = BidStatusSyncProcessor()  # Add Bid Status Sync processor
        self.single_bid_book_processor = SingleBidBookProcessor()  # Add Single Bid Book processor
        self.block_bid_book_processor = BlockBidBookProcessor()   # Add Block Bid Book processor
        self.bid_trade_live_sync_processor = BidTradeLiveSyncProcessor() # Add live sync processor
        self.supply_tomorrow_sync_processor = SupplyTomorrowSyncProcessor() # Add supply tomorrow sync processor
        self.demand_temp_sync_processor = DemandTempSyncProcessor() # Add demand temp sync processor
        self.idf_forecast_merge_processor = IdfForecastMergeProcessor()
        self.demand_serve_pipeline_processor = DemandServePipelineProcessor(
            idf_processor=self.idf_forecast_merge_processor,
            demand_processor=self.demand_processor,
            dsm_processor=self.new_dsm_processor,
        )
        self.exchange_dsm_processor = ExchangeDsmProcessor() # Add Exchange DSM processor
        self.rtm_bid_lapse_processor = RTMBidLapseProcessor() # Add RTM Bid Lapse processor
        self.dam_bid_capture_processor = DAMBidCaptureProcessor()
        self.gdam_bid_capture_processor = GDAMBidCaptureProcessor()
        self.hpdam_bid_capture_processor = HPDAMBidCaptureProcessor()
        self.rtm_bid_capture_processor = RTMBidCaptureProcessor()
        self.analytics_export_processor = analytics_export_processor
        # First job in the scheduler that writes files. ANALYTICS_EXPORT_DIR defaults
        # to /var/lib/epms/exports, which does not exist on a fresh box; create it at
        # startup so a permissions problem is named in the boot log rather than at
        # the first export. Non-fatal on purpose: a broken export directory must not
        # stop the bidding crons from registering.
        try:
            self.analytics_export_processor.ensure_directories()
        except Exception as export_dir_error:
            logger.error("Analytics export directory unavailable at startup: %s",
                         export_dir_error)
        
        # New HPX Exchange Trade Processor
        from services.scheduler.app.processors.exchange_trade_processor import ExchangeTradeProcessor
        self.exchange_trade_processor = ExchangeTradeProcessor()
        
        # Create shared repository instances
        try:
            logger.info("Creating job repositories with URI: %s, DB: %s, Collection: %s", 
                       settings.AUDIT_DB_URI, settings.AUDIT_DB_NAME, settings.JOB_DATA_COLLECTION)
            self.job_data_repository = create_job_data_repository(
                settings.AUDIT_DB_URI,
                settings.AUDIT_DB_NAME,
                settings.JOB_DATA_COLLECTION
            )
            self.job_detail_repository = create_job_detail_repository(
                settings.AUDIT_DB_URI,
                settings.AUDIT_DB_NAME,
                settings.JOB_DETAIL_COLLECTION
            )
            logger.info("Job repositories created successfully - job_data: %s, job_detail: %s", 
                       self.job_data_repository is not None, self.job_detail_repository is not None)
        except Exception as e:
            logger.error("Failed to create job repositories: %s", e, exc_info=True)
            self.job_data_repository = None
            self.job_detail_repository = None
        
        # Create job seeder only if repository is available
        self.cron_job_seeder = None
        if self.job_data_repository:
            try:
                self.cron_job_seeder = create_scheduler_job_seeder(
                    self.job_data_repository
                )
                logger.info("Job seeder created successfully")
            except Exception as e:
                logger.error("Failed to create job seeder: %s", e)
                self.cron_job_seeder = None
        
        # Reference to the FastAPI event loop — set in _run_all_jobs_on_startup
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

        # Initialize the parent BaseCronManager with audit logger
        super().__init__(
            service_name="scheduler",
            timezone=settings.CRON_TIMEZONE,
            job_data_repository=self.job_data_repository,
            audit_logger=self.audit_logger
        )
        
        
        logger.info("Initialized scheduler cron manager")

        # Attach context-aware log handler to root logger
        logging.getLogger().addHandler(context_aware_handler)
    
    def _register_service_jobs(self):
        """Register scheduler-specific jobs from database"""
        try:
            # Check if repository and seeder are available
            if not self.job_data_repository:
                logger.warning("Job data repository not available, skipping job registration")
                return
                
            if not self.cron_job_seeder:
                logger.warning("Job seeder not available, skipping job registration")
                return
                
            # First, ensure jobs are seeded
            logger.info("Seeding default jobs...")
            seeding_result = self._safe_run_async(self.cron_job_seeder.seed_default_jobs())
            logger.info("Job seeding result: %s", seeding_result)
            
            # Get all enabled jobs from database
            jobs = self._safe_run_async(self.job_data_repository.get_all_jobs(enabled_only=True))
            
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
                        timezone=job_config.get('timezone', 'Asia/Kolkata')
                    )
                    if success:
                        registered_count += 1
                else:
                    logger.warning("No execution function found for job: %s", job_config['job_type'])
                    
            logger.info("Registered %s scheduler jobs from database", registered_count)
            
        except (ValueError, TypeError, OSError, RuntimeError) as e:
            logger.error("Failed to register jobs from database: %s", str(e))
            # Fallback to hardcoded jobs for backward compatibility
            self._register_fallback_jobs()
    
    def _get_job_function(self, job_type: str):
        """Get the execution function for a job type"""
        job_functions = {
            # Core Data Management Jobs
            # "serve_data_reconciliation": self._execute_serve_data_reconciliation_job,
            # "demand_data_management": self._execute_demand_data_management_job,
            # "supply_data_management": self._execute_supply_data_management_job,
            "date_update": self.date_updater.date_update_job,

            # Data Processing Jobs
            "demand_data_processing": self.demand_processor.execute_cron_job,
            "demand_serve_pipeline": self.demand_serve_pipeline_processor.execute_cron_job,
            "demand_schedule_update": self.demand_schedule_processor.execute_cron_job,
            "raw_data_processing": self._execute_raw_data_processing_job,
            "supply_data_processing": self.supply_processor.execute_cron_job,
            "supply_data_midnight_fill": self.supply_fill_processor.execute_cron_job,
            
            # New Processor Jobs
            "weather_data_processing": self.weather_processor.execute_cron_job,
            "bid_data_processing": self.bid_processor.execute_cron_job,
            "exchange_data_processing": self.exchange_processor.execute_cron_job,
            "exchange_trade_processing": self.exchange_trade_processor.execute_cron_job,
            "exchange_dsm_processing": self.exchange_dsm_processor.execute_cron_job,
            "renewable_data_processing": self.renewable_processor.execute_cron_job,
            "dsm_rate_calculation": self.dsm_processor.execute_cron_job,
            "new_dsm_rate_calculation": self.new_dsm_processor.execute_cron_job,
            "limit_values_approval_processing": self.limit_values_processor.execute_cron_job,
            "limit_values_rtm_approval_processing": self.limit_values_processor.execute_rtm_early_cron_job,

            "limit_values_rename_id_field": self.limit_values_processor.rename_id_field,
            "rtm_scheduling_calculation": self.rtm_scheduling_processor.execute_cron_job,
            # Data Scraping Jobs (disabled due to import issues)
            "exchange_serve_archival": self._execute_exchange_serve_archival_job,
            "demand_serve_archival": self._execute_demand_serve_archival_job,
            "forecast_serve_archival": self._execute_forecast_serve_archival_job,
            "supply_serve_archival": self._execute_supply_serve_archival_job,
            "weather_serve_archival": self._execute_weather_serve_archival_job,
            "bid_serve_archival": self._execute_bid_serve_archival_job,
            "renewable_serve_archival": self._execute_renewable_serve_archival_job,
            "peak_demand_compute": self.peak_demand_service.compute_and_store_peak,
            # System Maintenance Jobs
            "system_cleanup": self._execute_system_cleanup_job,
            "integration_apilog_cleanup": self._execute_integration_apilog_cleanup_job,
            "urs_backfill": self._execute_urs_backfill_job,
            "scheduling_history_cleanup": self._execute_scheduling_history_cleanup_job,
            "retry_failed_jobs": self._execute_retry_failed_jobs,
            "data_migration": self._execute_data_migration_job,
            "job_logs_cleanup": self._execute_job_logs_cleanup_job,
            # Cross-service jobs (from other services)
            "upload_raw_data_processor": self._execute_upload_raw_data_processor_job,
            "scrape_raw_data_processor": self._execute_scrape_raw_data_processor_job,
            # Bid Status Sync Jobs
            "dam_bid_status_sync": self.bid_status_sync_processor.execute_dam_sync,
            "gdam_bid_status_sync": self.bid_status_sync_processor.execute_gdam_sync,
            "hpdam_bid_status_sync": self.bid_status_sync_processor.execute_hpdam_sync,
            "rtm_bid_status_sync": self.bid_status_sync_processor.execute_rtm_sync,

            # Single Bid Book Sync Jobs
            "dam_single_bid_book_sync": self.single_bid_book_processor.execute_dam_cron,
            "gdam_single_bid_book_sync": self.single_bid_book_processor.execute_gdam_cron,
            "hpdam_single_bid_book_sync": self.single_bid_book_processor.execute_hpdam_cron,
            "rtm_single_bid_book_sync": self.single_bid_book_processor.execute_rtm_cron,
            # Block Bid Book Sync Jobs
            "rtm_block_bid_book_sync": self.block_bid_book_processor.execute_cron_job,

            # Bid Trade Live Sync
            "bid_trade_live_sync": self.bid_trade_live_sync_processor.execute_cron_job,

            # Supply Tomorrow Sync
            "supply_tomorrow_sync": self.supply_tomorrow_sync_processor.execute_cron_job,

            # Demand Temp Sync
            "demand_temp_sync": self.demand_temp_sync_processor.execute_cron_job,
            "idf_forecast_merge": self.idf_forecast_merge_processor.execute_cron_job,

            # RTM Bid Lapse
            "rtm_bid_lapse_processing": self.rtm_bid_lapse_processor.execute_cron_job,

            # RTM Block Bid Lapse
            "rtm_block_bid_lapse_processing": self.rtm_bid_lapse_processor.execute_block_bid_lapse_job,
            "dam_bid_capture_snapshot": self.dam_bid_capture_processor.execute_cron_job,
            "dam_bid_capture_approval": self.dam_bid_capture_processor.execute_block_capture_job,
            "gdam_bid_capture_approval": self.gdam_bid_capture_processor.execute_block_capture_job,
            "hpdam_bid_capture_approval": self.hpdam_bid_capture_processor.execute_block_capture_job,
            "rtm_bid_capture_approval": self.rtm_bid_capture_processor.execute_block_capture_job,

            # Analytics Dashboard export worker. Registered here, in
            # SchedulerJobSeeder.get_default_jobs and in _register_fallback_jobs —
            # miss any one and the job runs once at boot and never fires again.
            "analytics_export_sweep": self.analytics_export_processor.sweep,
        }
        return job_functions.get(job_type)
    
    def _register_fallback_jobs(self):
        """Register fallback jobs for backward compatibility"""
        logger.info("Registering fallback scheduler jobs...")
        
        fallback_jobs = [
            {
                "job_type": "demand_data_management",
                "name": "Demand Data Management",
                "schedule": settings.DEMAND_DATA_CRON_SCHEDULE,
                "enabled": settings.DEMAND_DATA_CRON_ENABLED,
                "timezone": settings.DEMAND_DATA_CRON_TIMEZONE
            },
            {
                "job_type": "supply_data_management",
                "name": "Supply Data Management",
                "schedule": settings.DEMAND_DATA_CRON_SCHEDULE,
                "enabled": settings.DEMAND_DATA_CRON_ENABLED,
                "timezone": settings.DEMAND_DATA_CRON_TIMEZONE
            },
            {
                "job_type": "demand_data_processing",
                "name": "Demand Data Processing",
                "schedule": "*/15 * * * *",  # Every 15 minutes
                "enabled": not getattr(settings, "DEMAND_SERVE_PIPELINE_CRON_ENABLED", True),
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "demand_schedule_update",
                "name": "Demand Schedule Update",
                "schedule": "*/5 * * * *",  # Every 5 minutes
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "supply_data_processing",
                "name": "Supply Data Processing",
                "schedule": "*/15 * * * *",  # Every 15 minutes
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "system_cleanup",
                "name": "System Cleanup",
                "schedule": settings.SYSTEM_CLEANUP_SCHEDULE,
                "enabled": settings.SYSTEM_CLEANUP_ENABLED,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "retry_failed_jobs",
                "name": "Retry Failed Jobs",
                "schedule": settings.RETRY_FAILED_JOBS_SCHEDULE,
                "enabled": settings.RETRY_FAILED_JOBS_ENABLED,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "limit_values_rename_id_field",
                "name": "Limit Values Rename ID Field",
                "schedule": "0 0 * * *", # Every day at midnight
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "exchange_data_processing",
                "name": "Exchange Data Processing",
                "schedule": "*/15 * * * *",  # Every 15 minutes
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "exchange_trade_processing",
                "name": "Exchange Trade Processing",
                "schedule": "*/15 * * * *",  # Every 15 minutes
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "exchange_dsm_processing",
                "name": "Exchange DSM Processing",
                "schedule": "*/15 * * * *",  # Every 15 minutes
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "weather_data_processing",
                "name": "Weather Data Processing",
                "schedule": "*/15 * * * *",  # Every 15 minutes
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "bid_data_processing",
                "name": "Bid Data Processing",
                "schedule": "*/15 * * * *",  # Every 15 minutes
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "renewable_data_processing",
                "name": "Renewable Data Processing",
                "schedule": "*/15 * * * *",  # Every 15 minutes
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "supply_data_midnight_fill",
                "name": "Supply Data Midnight Fill",
                "schedule": "0 0 * * *",  # Every day at midnight
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "peak_demand_compute",
                "name": "Peak Demand Compute",
                "schedule": "0 */6 * * *",  # Every 6 hours
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "exchange_serve_archival",
                "name": "Exchange Serve Archival",
                "schedule": "15 3 * * *",  # Every day at 03:15 AM after final upstream processing
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            # Demand/Supply Sync Jobs
            {
                "job_type": "demand_temp_sync",
                "name": "Demand Temp Sync",
                "schedule": "*/15 * * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "idf_forecast_merge",
                "name": "IDF Forecast Merge",
                "schedule": "*/15 * * * *",
                "enabled": not getattr(settings, "DEMAND_SERVE_PIPELINE_CRON_ENABLED", True),
                "timezone": settings.CRON_TIMEZONE,
            },
            {
                "job_type": "demand_serve_pipeline",
                "name": "Demand Serve Pipeline (IDF → Demand → DSM)",
                "schedule": getattr(settings, "DEMAND_SERVE_PIPELINE_CRON_SCHEDULE", "*/15 * * * *"),
                "enabled": getattr(settings, "DEMAND_SERVE_PIPELINE_CRON_ENABLED", True),
                "timezone": settings.CRON_TIMEZONE,
            },
            {
                "job_type": "supply_tomorrow_sync",
                "name": "Supply Tomorrow Sync",
                "schedule": "*/15 * * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            # Live Sync Jobs
            {
                "job_type": "bid_trade_live_sync",
                "name": "Bid Trade Live Sync",
                "schedule": "*/30 * * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "rtm_single_bid_book_sync",
                "name": "RTM Single Bid Book Sync",
                "schedule": "*/30 * * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "dam_single_bid_book_sync",
                "name": "DAM Single Bid Book Sync",
                "schedule": "0,30 14-16 * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "gdam_single_bid_book_sync",
                "name": "GDAM Single Bid Book Sync",
                "schedule": "0,30 14-16 * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "hpdam_single_bid_book_sync",
                "name": "HPDAM Single Bid Book Sync",
                "schedule": "0,30 14-16 * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            # DSM and RTM Calculation Jobs
            {
                "job_type": "dsm_rate_calculation",
                "name": "DSM Rate and Net DSM Amount Calculation",
                "schedule": "*/15 * * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "new_dsm_rate_calculation",
                "name": "New DSM Rate and Net DSM Amount Calculation",
                "schedule": "*/15 * * * *",
                "enabled": not getattr(settings, "DEMAND_SERVE_PIPELINE_CRON_ENABLED", True),
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "rtm_scheduling_calculation",
                "name": "RTM Scheduling Calculation",
                "schedule": "*/30 * * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            # Bid Lapse Processing Jobs
            {
                "job_type": "rtm_bid_lapse_processing",
                "name": "RTM Bid Lapse Processing",
                "schedule": "*/15 * * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "rtm_block_bid_lapse_processing",
                "name": "RTM Block Bid Lapse Processing",
                "schedule": "*/15 * * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "rtm_block_bid_book_sync",
                "name": "RTM Block Bid Book Sync",
                "schedule": "*/15 * * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            # Bid Capture Jobs
            {
                "job_type": "dam_bid_capture_snapshot",
                "name": "DAM Bid Capture Snapshot",
                "schedule": "59 10 * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            # Limit Values Jobs
            {
                "job_type": "limit_values_approval_processing",
                "name": "Limit Values Approval Processing",
                "schedule": "0 0 * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "limit_values_rtm_approval_processing",
                "name": "Limit Values RTM Early Approval Processing",
                "schedule": "15 22 * * *",  # RTM rolls over to tomorrow at 22:15 IST
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            # Data Archival Jobs (missing from original fallback)
            {
                "job_type": "demand_serve_archival",
                "name": "Demand Serve Data Archival",
                "schedule": "15 3 * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "forecast_serve_archival",
                "name": "Forecast Serve Data Archival",
                "schedule": "15 3 * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "supply_serve_archival",
                "name": "Supply Serve Data Archival",
                "schedule": "15 3 * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "weather_serve_archival",
                "name": "Weather Serve Data Archival",
                "schedule": "15 3 * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "bid_serve_archival",
                "name": "Bid Serve Data Archival",
                "schedule": "15 3 * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "renewable_serve_archival",
                "name": "Renewable Serve Data Archival",
                "schedule": "15 3 * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            # Additional Maintenance Jobs (missing from original fallback)
            {
                "job_type": "scheduling_history_cleanup",
                "name": "Scheduling History Cleanup",
                "schedule": "0 1 * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "integration_apilog_cleanup",
                "name": "Integration API Log Cleanup",
                "schedule": "0 2 * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "job_logs_cleanup",
                "name": "Job Logs Cleanup",
                "schedule": "0 4 * * *",
                "enabled": True,
                "timezone": settings.CRON_TIMEZONE
            },
            # Disabled by default
            {
                "job_type": "dam_bid_status_sync",
                "name": "DAM Bid Status Sync",
                "schedule": "0,30 14-15 * * *",
                "enabled": False,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "gdam_bid_status_sync",
                "name": "GDAM Bid Status Sync",
                "schedule": "0,30 14-15 * * *",
                "enabled": False,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "hpdam_bid_status_sync",
                "name": "HPDAM Bid Status Sync",
                "schedule": "0,30 14-15 * * *",
                "enabled": False,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "rtm_bid_status_sync",
                "name": "RTM Bid Status Sync",
                "schedule": "*/30 * * * *",
                "enabled": False,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                "job_type": "data_migration",
                "name": "Data Migration to Historical",
                "schedule": "0 3 * * *",
                "enabled": False,
                "timezone": settings.CRON_TIMEZONE
            },
            {
                # This is the list that actually gets registered at import, because
                # _register_service_jobs() runs before the event loop exists.
                "job_type": "analytics_export_sweep",
                "name": "Analytics Export Sweep",
                "schedule": getattr(settings, "ANALYTICS_EXPORT_SWEEP_CRON", "* * * * *"),
                "enabled": getattr(settings, "ANALYTICS_EXPORT_SWEEP_ENABLED", True),
                "timezone": settings.CRON_TIMEZONE
            },
        ]
        
        registered_count = 0
        for job_config in fallback_jobs:
            job_function = self._get_job_function(job_config['job_type'])
            if job_function:
                success = self.register_job(
                    job_type=job_config['job_type'],
                    name=job_config['name'],
                    schedule=job_config['schedule'],
                    func=job_function,
                    enabled=job_config['enabled'],
                    timezone=job_config.get('timezone', 'Asia/Kolkata')
                )
                if success:
                    registered_count += 1
            else:
                logger.warning("No execution function found for fallback job: %s", job_config['job_type'])
        
        logger.info("Registered %s fallback scheduler jobs", registered_count)
    
    # Enhanced methods from base class with audit database logging
    def start(self):
        """Start the cron scheduler with scheduler-specific startup logic"""
        if not self._is_running:
            try:
                logger.info("Starting scheduler cron manager...")
                
                # Call base class start method
                super().start()
                
                # Add scheduler-specific startup logic if needed
                if settings.RUN_ALL_JOBS_ON_STARTUP:
                    try:
                        # Try to get the current event loop
                        loop = asyncio.get_running_loop()
                        if loop.is_running():
                            # If loop is running, create a task
                            asyncio.create_task(self._run_all_jobs_on_startup())
                        else:
                            # If no loop is running, run in a new event loop
                            self._safe_run_async(self._run_all_jobs_on_startup())
                    except RuntimeError:
                        # No event loop exists, create one
                        self._safe_run_async(self._run_all_jobs_on_startup())
                
            except (ValueError, TypeError, OSError, RuntimeError) as e:
                logger.error("Failed to start scheduler cron manager: %s", str(e))
                raise
    
    async def _run_all_jobs_on_startup(self):
        """Run all enabled jobs immediately on startup according to priority with parallel execution for same priority"""
        try:
            self._main_loop = asyncio.get_running_loop()
            logger.info("Running all enabled jobs on startup...")
            logger.info("   Startup delay: %s seconds", settings.STARTUP_JOB_DELAY_SECONDS)

            # Re-seed here (async context, event loop is running) so any new jobs
            # added to get_default_jobs() since the last deployment get inserted.
            # _register_service_jobs() runs during __init__ before the loop is
            # available, so it falls back to hardcoded jobs and skips DB seeding.
            if self.cron_job_seeder and self.job_data_repository:
                try:
                    seeding_result = await self.cron_job_seeder.seed_default_jobs()
                    logger.info("Startup re-seed result: %s", seeding_result)
                    # Register any newly seeded jobs that weren't in _registered_jobs yet
                    all_jobs = await self.job_data_repository.get_all_jobs(enabled_only=True)
                    for job_config in all_jobs:
                        jt = job_config["job_type"]
                        if jt not in self._registered_jobs:
                            job_fn = self._get_job_function(jt)
                            if job_fn:
                                self.register_job(
                                    job_type=jt,
                                    name=job_config["name"],
                                    schedule=job_config["schedule"],
                                    func=job_fn,
                                    enabled=job_config["enabled"],
                                    timezone=job_config.get("timezone", "Asia/Kolkata"),
                                )
                                logger.info("Registered newly seeded job: %s", jt)
                except Exception as seed_err:
                    logger.error("Startup re-seed failed: %s", seed_err)

            # Wait for the configured delay without blocking the FastAPI event loop
            await asyncio.sleep(settings.STARTUP_JOB_DELAY_SECONDS)
            
            # Get all enabled jobs with their priorities from database
            enabled_jobs_with_priority = []
            
            for job_type, cron_job in self._registered_jobs.items():
                if cron_job.enabled:
                    # Get job data from database to get priority
                    job_data = await self.job_data_repository.get_job(job_type)
                    priority = job_data.get('priority', 999) if job_data else 999  # Default to low priority
                    
                    enabled_jobs_with_priority.append({
                        'job_type': job_type,
                        'cron_job': cron_job,
                        'priority': priority
                    })
            
            # Sort jobs by priority (lower number = higher priority)
            enabled_jobs_with_priority.sort(key=lambda x: x['priority'])
            
            # Group jobs by priority
            jobs_by_priority = defaultdict(list)
            for job_info in enabled_jobs_with_priority:
                jobs_by_priority[job_info['priority']].append(job_info)
            
            logger.info("   Found %s enabled jobs to run on startup", len(enabled_jobs_with_priority))
            logger.info("   Jobs will run in priority order (parallel within same priority):")
            for priority in sorted(jobs_by_priority.keys()):
                jobs = jobs_by_priority[priority]
                logger.info("     Priority %s (%s jobs): %s", 
                          priority, len(jobs), 
                          ", ".join([job['cron_job'].name for job in jobs]))
            
            # Run jobs by priority groups (parallel within same priority, sequential across priorities)
            for priority in sorted(jobs_by_priority.keys()):
                jobs_in_priority = jobs_by_priority[priority]
                
                if len(jobs_in_priority) == 1:
                    # Single job - run sequentially
                    job_info = jobs_in_priority[0]
                    await self._execute_single_startup_job(job_info)
                else:
                    # Multiple jobs with same priority - run in parallel
                    logger.info("   Running %s jobs with priority %s in parallel", len(jobs_in_priority), priority)
                    await self._execute_parallel_startup_jobs(jobs_in_priority)
                
                # Add delay between priority groups (except for the last priority group)
                if priority != max(jobs_by_priority.keys()):
                    logger.info("   Waiting %s seconds before next priority group...", settings.STARTUP_JOB_INTERVAL_SECONDS)
                    await asyncio.sleep(settings.STARTUP_JOB_INTERVAL_SECONDS)
            
            logger.info("Completed startup job execution")
            
        except (ValueError, TypeError, OSError, RuntimeError) as e:
            logger.error("Failed to run jobs on startup: %s", str(e))
    
    async def _execute_single_startup_job(self, job_info: dict):
        """Execute a single startup job asynchronously"""
        
        job_type = job_info['job_type']
        cron_job = job_info['cron_job']
        priority = job_info['priority']
        
        try:
            logger.info("   Running startup job: %s (Type: %s, Priority: %s)", 
                      cron_job.name, job_type, priority)
            
            # Execute job function asynchronously
            if asyncio.iscoroutinefunction(cron_job.func):
                # Function is already async
                await cron_job.func()
            else:
                # Function is sync, run in thread pool
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, cron_job.func)
            
            logger.info("   Completed startup job: %s (Priority: %s)", cron_job.name, priority)
            
        except Exception as job_error:  # pylint: disable=broad-except
            logger.error("   Failed to run startup job %s (Priority: %s): %s", 
                       cron_job.name, priority, str(job_error))
    
    async def _execute_parallel_startup_jobs(self, jobs_in_priority: list):
        """Execute multiple startup jobs with the same priority in parallel"""
        
        async def execute_job(job_info: dict):
            """Execute a single job and return result with smart logging"""
            job_type = job_info['job_type']
            cron_job = job_info['cron_job']
            priority = job_info['priority']
            job_detail_id = None
            start_time = time.time()
            
            try:
                # Record job start and get job detail ID for correlation
                execution_start = datetime.now()
                job_detail_id = await self._record_job_start(job_type, cron_job.name, "parallel", execution_start)
                
                logger.info("     Starting parallel job: %s (Type: %s, Priority: %s)", 
                          cron_job.name, job_type, priority)
                
                # Log job start
                if self.audit_logger:
                    self.audit_logger.log_system_operation(
                        service="scheduler",
                        operation="cron_job_started",
                        message=f"Cron job started: {cron_job.name}",
                        correlation_id=job_detail_id or f"job_{int(time.time())}",
                        job_name=cron_job.name,
                        job_type=job_type,
                        priority=priority,
                        execution_mode="parallel"
                    )
                
                # Execute job function asynchronously
                if asyncio.iscoroutinefunction(cron_job.func):
                    # Function is already async
                    await cron_job.func()
                else:
                    # Function is sync, run in thread pool
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, cron_job.func)
                
                # Calculate duration
                duration_ms = int((time.time() - start_time) * 1000)

                # Log successful completion
                if self.audit_logger:
                    self.audit_logger.log_system_operation(
                        service="scheduler",
                        operation="cron_job_completed",
                    message=f"Cron job completed: {cron_job.name}",
                    job_detail_id=job_detail_id or f"job_{int(time.time())}",
                    job_name=cron_job.name,
                    job_type=job_type,
                    priority=priority,
                    execution_mode="parallel",
                    duration_ms=duration_ms,
                    duration_seconds=duration_ms / 1000,
                    success=True
                )

                logger.info("     Completed parallel job: %s (Priority: %s)", cron_job.name, priority)
                return {"status": "success", "job_name": cron_job.name, "priority": priority, "job_detail_id": job_detail_id}
            except Exception as job_error:  # pylint: disable=broad-except  # pylint: disable=broad-except
                duration_ms = int((time.time() - start_time) * 1000)
                
                # Log job failure
                if self.audit_logger:
                    self.audit_logger.log_system_operation(
                        service="scheduler",
                        operation="cron_job_failed",
                    message=f"Cron job failed: {cron_job.name} - {str(job_error)}",
                    job_detail_id=job_detail_id or f"job_{int(time.time())}",
                    job_name=cron_job.name,
                    job_type=job_type,
                    priority=priority,
                    execution_mode="parallel",
                    duration_ms=duration_ms,
                    duration_seconds=duration_ms / 1000,
                    success=False,
                    exception_type=type(job_error).__name__
                )
                
                logger.error("     Failed to run parallel job %s (Priority: %s): %s", 
                           cron_job.name, priority, str(job_error))
                return {"status": "error", "job_name": cron_job.name, "priority": priority, "error": str(job_error), "job_detail_id": job_detail_id}
        
        # Create tasks for all jobs in this priority group
        tasks = [execute_job(job_info) for job_info in jobs_in_priority]
        
        # Execute all tasks in parallel and wait for all to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Log summary of parallel execution
        successful_jobs = [r for r in results if isinstance(r, dict) and r.get("status") == "success"]
        failed_jobs = [r for r in results if isinstance(r, dict) and r.get("status") == "error"]
        
        logger.info("     Parallel execution completed - Success: %s, Failed: %s", 
                  len(successful_jobs), len(failed_jobs))
    
    # Enhanced job status methods with audit database integration
    async def get_all_jobs(self) -> Dict[str, Any]:
        """Get status of all jobs with scheduler-specific enhancements"""
        base_result = super().get_all_jobs()
        
        # Add scheduler-specific job statistics
        if base_result.get('jobs'):
            for job in base_result['jobs']:
                try:
                    job_stats = await self.job_detail_repository.get_job_statistics(job['job_id'], days=1)
                    if job_stats:
                        job.update({
                            'total_executions': job_stats.get('total_executions', 0),
                            'success_rate': job_stats.get('success_rate', 0.0),
                            'average_duration': job_stats.get('average_duration_seconds', 0.0)
                        })
                except (ValueError, TypeError, OSError, RuntimeError) as e:
                    logger.error("Error getting job statistics for %s: %s", job['job_id'], str(e))
        
        return base_result
    
    async def _record_job_start(self, job_type: str, job_name: str, schedule: str, 
                         execution_start: datetime) -> Optional[str]:
        """Record job start in audit database and return execution_id"""
        try:
            # Get the job_id (ObjectId) from job_data collection using job_type (now async)
            job_data = await self.job_data_repository.get_job(job_type)
            if not job_data:
                logger.error("Job data not found for job_type: %s", job_type)
                return None
            
            job_id = job_data.get('_id')  # This is the ObjectId reference
            
            # Generate unique execution ID
            execution_id = f"{job_type}_{execution_start.strftime('%Y%m%d_%H%M%S')}"
            
            job_detail = JobDetail(
                job_id=str(job_id),  # Convert ObjectId to string for storage
                execution_id=execution_id,
                job_name=job_name,
                execution_start=execution_start,
                execution_end=execution_start,  # Same as start for processing entry
                duration_seconds=0.0,  # Will be updated on completion
                status="processing",
                success=False,  # Default to False for processing entry
                error_message=None,
                error_details=None,
                result_data={},
                service=self.service_name,
                metadata={
                    "timezone": settings.CRON_TIMEZONE,
                    "schedule": schedule,
                    "job_type": job_type,  # Store job_type for easy querying
                    "processing_started": True
                }
            )
            
            await self.job_detail_repository.log_execution(job_detail)
            logger.info("Recorded job start for %s (job_type: %s, execution_id: %s) in audit database", 
                       job_id, job_type, execution_id)
            return execution_id
                
        except Exception as e:  # pylint: disable=broad-except
            logger.error("Failed to record job start for %s: %s", job_type, str(e))
            return None

    async def _record_job_completion(self, job_type: str, job_name: str, schedule: str, 
                              execution_start: datetime, execution_end: datetime,
                              execution_id: str, status: str, success: bool, 
                              result: Optional[Dict[str, Any]] = None,
                              error_message: Optional[str] = None):
        """Record job completion in audit database"""
        try:
            duration_seconds = (execution_end - execution_start).total_seconds()
            
            # Get the job_id (ObjectId) from job_data collection using job_type (now async)
            job_data = await self.job_data_repository.get_job(job_type)
            if not job_data:
                logger.error("Job data not found for job_type: %s", job_type)
                return
            
            job_id = job_data.get('_id')  # This is the ObjectId reference
            
            job_detail = JobDetail(
                job_id=str(job_id),  # Convert ObjectId to string for storage
                execution_id=execution_id,  # Use the same execution_id from start
                job_name=job_name,
                execution_start=execution_start,
                execution_end=execution_end,
                duration_seconds=duration_seconds,
                status=status,
                success=success,
                error_message=error_message,
                error_details=None,
                result_data=result if result is not None else {},
                service=self.service_name,
                metadata={
                    "timezone": settings.CRON_TIMEZONE,
                    "schedule": schedule,
                    "job_type": job_type,  # Store job_type for easy querying
                    "processing_completed": True,
                    "final_status": status
                }
            )
            
            await self.job_detail_repository.log_execution(job_detail)
            logger.info("Recorded job completion for %s (job_type: %s, execution_id: %s, status: %s) in audit database", 
                       job_id, job_type, execution_id, status)
                
        except Exception as e:  # pylint: disable=broad-except
            logger.error("Failed to record job completion for %s: %s", job_type, str(e))
    
    async def _record_job_completion_with_logs(self, job_type: str, job_name: str, execution_id: str, 
                                       status: str, duration_seconds: float, logs: list, 
                                       result_data: Optional[Dict[str, Any]] = None, error_message: Optional[str] = None):
        """Record job completion with captured execution logs"""
        try:
            # Try to get job data, but proceed even if not found
            job_id = job_type  # Default to job_type as job_id
            if hasattr(self, 'job_data_repository') and self.job_data_repository: # job_data_repository.get_job is async
                job_data = await self.job_data_repository.get_job(job_type)
                if job_data:
                    job_id = job_data.get("_id", job_type)
                else:
                    logger.warning("Job data not found for job_type: %s, using job_type as job_id", job_type)
            
            # Ensure result_data is properly formatted
            if result_data is not None:
                # Handle coroutines and other non-dict types
                if hasattr(result_data, '__await__'):  # It's a coroutine
                    result_data = {"status": "async_result", "type": "coroutine"}
                elif not isinstance(result_data, dict):
                    result_data = {"status": "success", "result": str(result_data)}
            else:
                result_data = {}
            
            # Create job detail record with logs
            job_detail = JobDetail(
                job_id=job_id,
                execution_id=execution_id,
                job_name=job_name,
                execution_start=datetime.now() - timedelta(seconds=duration_seconds),  # Approximate start time
                execution_end=datetime.now(),
                duration_seconds=duration_seconds,
                status=status,
                success=(status == "success"),
                error_message=error_message,
                error_details=None,
                result_data=result_data,
                service="scheduler",
                metadata={"logs": logs}  # Store the captured logs in metadata
            )
            
            await self.job_detail_repository.log_execution(job_detail)
            logger.info("Recorded job completion with logs for %s (job_type: %s, execution_id: %s, status: %s, logs: %d)", 
                       job_name, job_type, execution_id, status, len(logs))
                
        except Exception as e:  # pylint: disable=broad-except
            logger.error("Failed to record job completion with logs for %s: %s", job_type, str(e))
    
    def _run_async_job(self, coroutine):
        """Safely run an async job in sync context"""
        try:
            # Create new event loop for async job execution
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(coroutine)
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"Error running async job: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _execute_job_with_logging_async(self, cron_job):
        """Coalescing wrapper around job execution.

        Ensures a given job_type never runs concurrently. If a run for this
        job_type is already in flight (cron + a RabbitMQ trigger, or two triggers),
        the incoming call is coalesced instead of starting an overlapping run — the
        in-flight run reads ALL pending data anyway, so nothing is missed. When it
        finishes, if any trigger arrived during the run, it re-runs exactly once
        (rerun-if-dirty) so the latest data is never stranded.
        """
        import asyncio

        job_type = getattr(cron_job, "job_id", None) or getattr(cron_job, "name", "unknown")

        # Already running -> mark dirty and skip (no overlap).
        if job_type in self._jobs_in_flight:
            self._jobs_pending_rerun.add(job_type)
            logger.info(
                "Job '%s' already running — coalescing this trigger (will re-run after current finishes)",
                job_type,
            )
            return {"status": "coalesced", "job_type": job_type}

        self._jobs_in_flight.add(job_type)
        try:
            return await self._run_job_with_logging_async(cron_job)
        finally:
            self._jobs_in_flight.discard(job_type)
            # Rerun-if-dirty: something was triggered while we were running, so run
            # once more to pick up data that arrived after this run read its pending.
            if job_type in self._jobs_pending_rerun:
                self._jobs_pending_rerun.discard(job_type)
                logger.info(
                    "Job '%s' received triggers while running — re-running once to catch new data",
                    job_type,
                )
                asyncio.create_task(self._execute_job_with_logging_async(cron_job))

    async def _run_job_with_logging_async(self, cron_job):
        """Override to capture execution logs and store them in job detail records - ASYNC VERSION"""
        import time
        import asyncio
        from datetime import datetime
        from services.scheduler.app.utils.log_capture import LogCapture
        
        job_type = cron_job.job_id
        job_name = cron_job.name
        start_time = datetime.now()
        execution_start_timestamp = time.time()
        
        # Generate execution ID
        execution_id = f"{job_type}_{start_time.strftime('%Y%m%d_%H%M%S')}"
        
        # Create log capture instance
        log_capture = LogCapture()
        
        try:
            # Import audit logger (lazy import to avoid circular dependencies)
            import sys
            import os
            sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
            from libs.audit_queue.simple_logger import AuditLogger
            
            # Record job start in job detail repository if available
            if hasattr(self, '_record_job_start'): # _record_job_start is async
                execution_id = await self._record_job_start(job_type, job_name, "scheduled", start_time)
                
                # Set correlation ID so processors can reuse it
                from services.scheduler.app.processors.base_processor import set_correlation_id as set_proc_correlation_id
                if execution_id:
                    set_proc_correlation_id(execution_id)
            
            # Log job start
            if self.audit_logger:
                self.audit_logger.log_scheduled_job(
                    service=self.service_name,
                    job_id=execution_id or job_type,
                status="started",
                message=f"Cron job started: {job_name}",
                job_name=job_name
            )
            
            logger.info("[ASYNC-EXEC] Starting scheduled job: %s (ID: %s)", job_name, job_type)
            log_capture.add_log("INFO", f"[ASYNC-EXEC] Starting scheduled job: {job_name} (ID: {job_type})")
            
            # Execute the actual job function with log capture
            # The context manager now sets the context var, it doesn't redirect stdout/stderr
            with log_capture.capture_execution_logs():
                # Handle both sync and async job functions
                if asyncio.iscoroutinefunction(cron_job.func):
                    # For async functions, await them directly
                    result = await cron_job.func()
                else:
                    # For sync functions, run in thread pool
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(None, cron_job.func)
            
            # Calculate duration
            duration_ms = int((time.time() - execution_start_timestamp) * 1000)
            
            # Log successful completion
            if self.audit_logger: # audit_logger methods are sync (do not await)
                self.audit_logger.log_scheduled_job(
                    service=self.service_name,
                    job_id=execution_id or job_type,
                status="completed",
                message=f"Cron job completed: {job_name}",
                job_name=job_name,
                duration_ms=duration_ms,
                result_data=result if isinstance(result, dict) else None
            )
            
            logger.info("[ASYNC-SUCCESS] Completed scheduled job: %s (Duration: %dms)", job_name, duration_ms)
            log_capture.add_log("INFO", f"[ASYNC-SUCCESS] Completed scheduled job: {job_name} (Duration: {duration_ms}ms)")
            
            # Update job completion record with captured logs
            if hasattr(self, 'job_detail_repository') and self.job_detail_repository:
                # Ensure result_data is a dict or None
                result_data = result if isinstance(result, dict) else None # _record_job_completion_with_logs is async
                await self._record_job_completion_with_logs(job_type, job_name, execution_id or job_type, "success", 
                                                    duration_ms / 1000, log_capture.get_logs(), result_data)
            
            return result
            
        except Exception as e:
            # Calculate duration even for failed jobs
            duration_ms = int((time.time() - execution_start_timestamp) * 1000)
            
            # Add error to captured logs
            log_capture.add_log("ERROR", f"[ASYNC-FAILED] Job execution failed: {str(e)}")
            
            # Log job failure
            try:
                if self.audit_logger:
                    self.audit_logger.log_scheduled_job(
                        service=self.service_name,
                        job_id=execution_id or job_type,
                    status="failed",
                    message=f"Cron job failed: {job_name} - {str(e)}",
                    job_name=job_name,
                    duration_ms=duration_ms,
                    exception_type=type(e).__name__,
                    error_message=str(e)
                )
            except Exception as audit_error:
                logger.error("Failed to log job failure: %s", audit_error)
                log_capture.add_log("ERROR", f"Failed to log job failure: {audit_error}")
            
            logger.error("[ASYNC-FAILED] Failed scheduled job: %s (Duration: %dms) - %s", job_name, duration_ms, str(e))
            
            # Update job completion record with captured logs (including error logs)
            if hasattr(self, 'job_detail_repository') and self.job_detail_repository:
                await self._record_job_completion_with_logs(job_type, job_name, execution_id or job_type, "error", 
                                                    duration_ms / 1000, log_capture.get_logs(), None, str(e))
            
            raise
    
    # Job execution methods (delegates to target services following business rule)
    async def _execute_demand_data_job_delegate(self, payload: Optional[Dict[str, Any]] = None):
        """Delegate demand data management job to DummyDataService (follows business rule)"""
        logger.info("Delegating demand data management cron job to DummyDataService...")
        
        try:
            # Delegate to the target service which handles the complete lifecycle
            result = await dummy_data_service.execute_cron_job_demand_data_management(
                self.job_data_repository,
                self.job_detail_repository,
                payload
            )
            return result
                
        except Exception as e:  # pylint: disable=broad-except
            logger.error("Error delegating demand data management cron job: %s", str(e))
            return {'status': 'error', 'error': str(e)}
     
    async def _execute_supply_data_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute the supply data management job with audit logging"""
        job_type = "supply_data_management"
        job_name = "Supply Data Management"
        schedule = settings.DEMAND_DATA_CRON_SCHEDULE
        start_time = datetime.now() # _record_job_start is async
        
        # Record job start
        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        
        # Log job start with audit logging
        if execution_id and self.audit_logger:
            self.audit_logger.log_system_operation(
                service="scheduler", # audit_logger methods are sync (do not await)
                operation="cron_job_started",
                message=f"Cron job started: {job_name}",
                job_detail_id=execution_id,
                job_name=job_name,
                job_type=job_type,
                execution_mode="scheduled"
            )
        
        try:
            logger.info("Starting %s cron job", job_type)
            
            # Delegate to dummy data service
            result = await dummy_data_service.execute_cron_job_supply_data_management(
                self.job_data_repository,
                self.job_detail_repository,
                payload
            )
            
            end_time = datetime.now()
            success = result.get('status') in ['success', 'partial_success']
            
            # Log job completion
            if execution_id and self.audit_logger:
                self.audit_logger.log_system_operation(
                    service="scheduler",
                    operation="cron_job_completed",
                    message=f"Cron job completed: {job_name}",
                    job_detail_id=execution_id,
                    job_name=job_name,
                    job_type=job_type,
                    execution_mode="scheduled",
                    duration_ms=int((end_time - start_time).total_seconds() * 1000),
                    success=success
                )
            
            # Record job completion
            await self._record_job_completion(
                job_type, job_name, schedule, start_time, end_time, execution_id or job_type,
                "success" if success else "error", success, result
            )
            
            logger.info("Completed %s cron job: %s", job_type, result.get('status'))
            return result
            
        except Exception as e:
            end_time = datetime.now()
            logger.error("Error in %s cron job: %s", job_type, str(e))
            
            # Log job failure
            if execution_id and self.audit_logger:
                self.audit_logger.log_system_operation(
                    service="scheduler",
                    operation="cron_job_failed",
                    message=f"Cron job failed: {job_name} - {str(e)}",
                    job_detail_id=execution_id,
                    job_name=job_name,
                    job_type=job_type,
                    execution_mode="scheduled",
                    duration_ms=int((end_time - start_time).total_seconds() * 1000),
                    success=False,
                    exception_type=type(e).__name__
                )
            
            # Record job failure
            await self._record_job_completion(
                job_type, job_name, schedule, start_time, end_time, execution_id or job_type,
                "error", False, error_message=str(e)
            )
            
            return {'status': 'error', 'error': str(e)}

    # Missing job execution methods
    async def _execute_demand_data_management_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute demand data management job"""
        # Log job execution
        if self.audit_logger:
            self.audit_logger.log_system_operation( # audit_logger methods are sync (do not await)
                service="scheduler", # type: ignore
                operation="cron_job_started",
            message=f"Cron job started: _execute_demand_data_management_job",
            job_type="_execute_demand_data_management_job",
            execution_mode="scheduled"
        )
        
        try:
            # Await the async delegate
            return await self._execute_demand_data_job_delegate(payload)
        except Exception as e:
            logger.error("Error delegating demand data management job: %s", str(e))
            return {'status': 'error', 'error': str(e)}

    async def _execute_supply_data_management_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute supply data management job"""
        method_name = "_execute_supply_data_management_job"
        try:
            logger.info(f"Starting {method_name}")
            
            # Execute the async job
            result = self._safe_run_async(self._execute_supply_data_job(payload))
            
            # Log successful completion
            if self.audit_logger:
                self.audit_logger.log_system_operation( # audit_logger methods are sync (do not await)
                    service="scheduler",
                    operation="cron_job_completed",
                message=f"Cron job completed: {method_name}",
                job_type=method_name,
                execution_mode="scheduled",
                success=True
            )
            
            return result
            
        except Exception as e:
            # Log job failure
            if self.audit_logger:
                self.audit_logger.log_system_operation( # audit_logger methods are sync (do not await)
                    service="scheduler",
                    operation="cron_job_failed",
                    message=f"Cron job failed: {method_name} - {str(e)}",
                    job_type=method_name,
                    execution_mode="scheduled",
                    success=False,
                    exception_type=type(e).__name__
                )
            logger.error("Error in %s: %s", method_name, str(e))
            return {'status': 'error', 'error': str(e)}
    
    async def _execute_serve_data_reconciliation_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute serve data reconciliation job"""
        method_name = "_execute_serve_data_reconciliation_job"
        try:
            logger.info(f"Starting {method_name}")
            
            # Execute reconciliation logic
            from ..services.dummy_data_service import dummy_data_service # dummy_data_service methods are async
            result = await dummy_data_service.reconcile_and_rerun_crons()
            
            # Log successful completion
            if self.audit_logger:
                self.audit_logger.log_system_operation(
                    service="scheduler",
                    operation="cron_job_completed",
                    message=f"Cron job completed: {method_name}",
                    job_type=method_name,
                    execution_mode="scheduled",
                    success=True
                )
            
            return result
            
        except Exception as e:
            # Log job failure
            if self.audit_logger:
                self.audit_logger.log_system_operation( # audit_logger methods are sync (do not await)
                    service="scheduler",
                    operation="cron_job_failed",
                message=f"Cron job failed: {method_name} - {str(e)}",
                job_type=method_name,
                execution_mode="scheduled",
                success=False,
                exception_type=type(e).__name__
            )
            logger.error("Error in %s: %s", method_name, str(e))
            return {'status': 'error', 'error': str(e)}
    
    async def _execute_demand_data_processing_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute demand data processing job"""
        logger.info("Executing demand data processing job")
        try:
            # Import and use demand data processing service
            from ..services.demand_data_processing import DemandDataProcessor # process_demand_data is async
            processor = DemandDataProcessor()
            return processor.process_demand_data(payload)
        except Exception as e:
            logger.error("Error in demand data processing job: %s", str(e))
            return {'status': 'error', 'error': str(e)}
    
    def _execute_supply_data_processing_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute supply data processing job""" # process_supply_data is async
        logger.info("Executing supply data processing job")
        try:
            # Import and use supply data processing service
            from ..services.supply_data_processing import SupplyDataProcessing
            processor = SupplyDataProcessing() # process_supply_data is async
            return processor.process_supply_data(payload)
        except Exception as e:
            logger.error("Error in supply data processing job: %s", str(e))
            return {'status': 'error', 'error': str(e)}
    
    def _execute_raw_data_processing_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute raw data processing job"""
        logger.info("Executing raw data processing job")
        try:
            # This would delegate to ingestion service processors
            result = {
                'status': 'success',
                'message': 'Raw data processing delegated to ingestion service',
                'processed_records': 0
            }
            return result
        except Exception as e:
            logger.error("Error in raw data processing job: %s", str(e))
            return {'status': 'error', 'error': str(e)}
    
    def _execute_upload_raw_data_processor_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute upload raw data processor job (delegates to ingestion service)"""
        logger.info("Executing upload raw data processor job")
        try:
            # This delegates to the ingestion service
            result = {
                'status': 'success',
                'message': 'Upload processing delegated to ingestion service',
                'processed_files': 0
            }
            return result
        except Exception as e:
            logger.error("Error in upload raw data processor job: %s", str(e))
            return {'status': 'error', 'error': str(e)}
    
    def _execute_scrape_raw_data_processor_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute scrape raw data processor job (delegates to ingestion service)"""
        logger.info("🔍 DEBUG: Starting scrape raw data processor job")
        try:
            logger.info("🔍 DEBUG: Inside try block of scrape job")
            # This delegates to the ingestion service
            result = {
                'status': 'success',
                'message': 'Scrape processing delegated to ingestion service',
                'processed_providers': 0
            }
            logger.info("🔍 DEBUG: Scrape job result: %s", result)
            return result
        except Exception as e:
            logger.error("🔍 DEBUG: Exception in scrape raw data processor job: %s", str(e))
            logger.error("🔍 DEBUG: Exception type: %s", type(e).__name__)
            import traceback
            logger.error("🔍 DEBUG: Full traceback: %s", traceback.format_exc())
            return {'status': 'error', 'error': str(e)}
    
    def _execute_iex_daf_data_scraping_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute IEX DAF data scraping job"""
        logger.info("Executing IEX DAF data scraping job")
        try:
            # Placeholder for IEX DAF scraping (currently disabled due to import issues)
            result = {
                'status': 'success',
                'message': 'IEX DAF scraping job placeholder - implementation pending',
                'scraped_records': 0
            }
            return result
        except Exception as e:
            logger.error("Error in IEX DAF scraping job: %s", str(e))
            return {'status': 'error', 'error': str(e)}
    
    def _execute_iex_idf_data_scraping_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute IEX IDF data scraping job"""
        logger.info("Executing IEX IDF data scraping job")
        try:
            # Placeholder for IEX IDF scraping (currently disabled due to import issues)
            result = {
                'status': 'success',
                'message': 'IEX IDF scraping job placeholder - implementation pending',
                'scraped_records': 0
            }
            return result
        except Exception as e:
            logger.error("Error in IEX IDF scraping job: %s", str(e))
            return {'status': 'error', 'error': str(e)}
    
    def _safe_run_async(self, coro):
        """Safely run an async coroutine from a sync (executor thread) context.

        When called from run_in_executor threads, submits to the FastAPI event loop
        via run_coroutine_threadsafe so Motor clients (bound to that loop) work correctly.
        """
        import asyncio
        try:
            asyncio.get_running_loop()
            # Called from within the event loop thread — cannot block synchronously
            coro.close()
            raise RuntimeError(
                "_safe_run_async cannot synchronously wait while an event loop is running; "
                "await the coroutine or schedule it with asyncio.create_task instead"
            )
        except RuntimeError as exc:
            if "cannot synchronously wait" in str(exc):
                raise
            # No running loop in this thread (called from executor) — proceed below

        # Prefer the captured FastAPI loop so Motor clients work across the boundary
        if self._main_loop is not None and self._main_loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, self._main_loop).result()

        # Fallback: create a new event loop (safe only before the FastAPI loop starts)
        return asyncio.run(coro)

    def _cleanup_job_connections(self):
        """
        Cleanup database connections after job execution.
        This helps prevent connection leaks during scheduled job runs.
        """
        try:
            # Cleanup processor connections
            processors = [
                self.demand_processor, self.supply_processor, self.exchange_processor,
                self.weather_processor, self.bid_processor, self.renewable_processor
            ]
            
            for processor in processors:
                if processor and hasattr(processor, '_cleanup_connections'):
                    try:
                        processor._cleanup_connections()
                    except Exception as cleanup_error:
                        logger.warning(f"Error cleaning up processor connections: {str(cleanup_error)}")
            
            # Force garbage collection
            import gc
            gc.collect()
            
            logger.debug("Job connection cleanup completed")
            
        except Exception as e:
            logger.warning(f"Error during job connection cleanup: {str(e)}")

    def _execute_exchange_serve_archival_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute exchange serve archival job"""
        logger.info("Executing exchange serve archival job")
        try:
            # Use the exchange processor's archival method
            result = self._safe_run_async(self.exchange_processor.execute_archival_job())
            return result
        except Exception as e:
            logger.error("Error in exchange archival job: %s", str(e))
            return {'status': 'error', 'error': str(e)}
        finally:
            self._cleanup_job_connections()
    
    def _execute_demand_serve_archival_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute demand serve archival job""" # execute_archival_job is async
        logger.info("Executing demand serve archival job")
        try:
            # Use the demand processor's archival method
            result = self._safe_run_async(self.demand_processor.execute_archival_job())
            return result
        except Exception as e:
            logger.error("Error in demand archival job: %s", str(e))
            return {'status': 'error', 'error': str(e)}
        finally:
            self._cleanup_job_connections()

    def _execute_forecast_serve_archival_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute forecast_serve archival job."""
        logger.info("Executing forecast serve archival job")
        try:
            result = self._safe_run_async(
                self.idf_forecast_merge_processor.execute_forecast_serve_archival_job()
            )
            return result
        except Exception as e:
            logger.error("Error in forecast serve archival job: %s", str(e))
            return {"status": "error", "error": str(e)}
        finally:
            self._cleanup_job_connections()
    
    def _execute_supply_serve_archival_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute supply serve archival job""" # execute_archival_job is async
        logger.info("Executing supply serve archival job")
        try:
            # Use the supply processor's archival method
            result = self._safe_run_async(self.supply_processor.execute_archival_job())
            return result
        except Exception as e:
            logger.error("Error in supply archival job: %s", str(e))
            return {'status': 'error', 'error': str(e)}
        finally:
            self._cleanup_job_connections()
    
    def _execute_weather_serve_archival_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute weather serve archival job""" # execute_archival_job is async
        logger.info("Executing weather serve archival job")
        try:
            # Use the weather processor's archival method
            result = self._safe_run_async(self.weather_processor.execute_archival_job())
            return result
        except Exception as e:
            logger.error("Error in weather archival job: %s", str(e))
            return {'status': 'error', 'error': str(e)}
        finally:
            self._cleanup_job_connections()
    
    def _execute_bid_serve_archival_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute bid serve archival job""" # execute_archival_job is async
        logger.info("Executing bid serve archival job")
        try:
            # Use the bid processor's archival method
            result = self._safe_run_async(self.bid_processor.execute_archival_job())
            return result
        except Exception as e:
            logger.error("Error in bid archival job: %s", str(e))
            return {'status': 'error', 'error': str(e)}
        finally:
            self._cleanup_job_connections()
    
    def _execute_renewable_serve_archival_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute renewable serve archival job""" # execute_archival_job is async
        logger.info("Executing renewable serve archival job")
        try:
            # Use the renewable processor's archival method
            result = self._safe_run_async(self.renewable_processor.execute_archival_job())
            return result
        except Exception as e:
            logger.error("Error in renewable archival job: %s", str(e))
            return {'status': 'error', 'error': str(e)}
        finally:
            # Cleanup connections after job execution
            self._cleanup_job_connections()
    
    def _execute_urs_backfill_job(self, payload: Optional[Dict[str, Any]] = None):
        """
        One-time URS recovery, triggered by hand.

        Seeded disabled: nothing schedules this. It exists so the backfill can
        be run without shell access to the host, and so its outcome is readable
        afterwards. Skips any day already completed, so triggering it twice
        costs nothing.
        """
        logger.info("Executing URS backfill job")
        try:
            from .urs_backfill import run_backfill_job

            return self._safe_run_async(run_backfill_job())
        except Exception as e:
            logger.error("Error in URS backfill job: %s", str(e))
            return {"status": "error", "error": str(e)}

    def _execute_system_cleanup_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute system cleanup job — dynamically cleans all *_integration_apilog collections."""
        logger.info("Executing system cleanup job")
        try:
            return self._safe_run_async(self._run_integration_apilog_cleanup())
        except Exception as e:
            logger.error("Error in system cleanup job: %s", str(e))
            return {'status': 'error', 'error': str(e)}

    async def _run_integration_apilog_cleanup(self):
        """Async helper to delete old integration apilog docs across all DBs."""
        from libs.database.mongo_client_factory import get_motor_client, get_scheduler_client
        cutoff = datetime.now() - timedelta(days=7)
        total_deleted = 0
        cleaned_collections = []

        # Databases to scan — use Motor async clients, not sync pymongo
        dbs = []
        try:
            dbs.append(("ingestion", get_motor_client(settings.MONGODB_URI, "ingestion_service")[settings.MONGODB_DATABASE]))
        except Exception as e:
            logger.warning("Could not get ingestion DB for cleanup: %s", e)
        try:
            dbs.append(("scheduler", get_scheduler_client(settings.MONGODB_URI)[settings.MONGODB_DATABASE]))
        except Exception as e:
            logger.warning("Could not get scheduler DB for cleanup: %s", e)

        apilog_pattern = re.compile(r".*_integration_apilog$", re.IGNORECASE)

        for db_name, db in dbs:
            try:
                collection_names = await db.list_collection_names()
                for coll_name in collection_names:
                    if apilog_pattern.match(coll_name):
                        collection = db[coll_name]
                        # Try both lowercase and capitalized timestamp fields
                        delete_result = await collection.delete_many({
                            "$or": [
                                {"timestamp": {"$lt": cutoff}},
                                {"Timestamp": {"$lt": cutoff}}
                            ]
                        })
                        if delete_result.deleted_count > 0:
                            total_deleted += delete_result.deleted_count
                            cleaned_collections.append(f"{db_name}.{coll_name}:{delete_result.deleted_count}")
                            logger.info("Cleaned %d docs from %s.%s", delete_result.deleted_count, db_name, coll_name)
            except Exception as e:
                logger.error("Error cleaning integration apilog in %s: %s", db_name, e)

        return {
            'status': 'success',
            'message': f'Cleaned {total_deleted} integration apilog docs',
            'total_deleted': total_deleted,
            'collections': cleaned_collections
        }

    def _execute_integration_apilog_cleanup_job(self, payload: Optional[Dict[str, Any]] = None):
        """Dedicated integration apilog cleanup cron job."""
        return self._execute_system_cleanup_job(payload)

    def _execute_scheduling_history_cleanup_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute scheduling requisition calculation history cleanup"""
        logger.info("Executing scheduling history cleanup job")
        try:
            return self._safe_run_async(self._run_scheduling_history_cleanup())
        except Exception as e:
            logger.error("Error in scheduling history cleanup job: %s", str(e))
            return {'status': 'error', 'error': str(e)}

    async def _run_scheduling_history_cleanup(self):
        """Async helper to delete old scheduling history records."""
        from libs.database.mongo_client_factory import get_scheduler_client
        cutoff = datetime.now() - timedelta(days=90)
        total_deleted = 0
        try:
            db = get_scheduler_client(settings.MONGODB_URI)[settings.MONGODB_DATABASE]
            collection = db["scheduling_requisition_calculation_history"]
            result = await collection.delete_many({"archived_at": {"$lt": cutoff}})
            total_deleted = result.deleted_count
            logger.info("Deleted %d old scheduling history records", total_deleted)
        except Exception as e:
            logger.error("Error deleting old scheduling history: %s", e)
        return {
            'status': 'success',
            'message': f'Deleted {total_deleted} old scheduling history records',
            'total_deleted': total_deleted
        }
    
    def _execute_retry_failed_jobs(self, payload: Optional[Dict[str, Any]] = None):
        """Execute retry failed jobs"""
        logger.info("Executing retry failed jobs")
        try:
            # Placeholder for retry logic
            result = {
                'status': 'success',
                'message': 'Retry failed jobs placeholder - implementation pending',
                'retried_jobs': 0
            }
            return result
        except Exception as e:
            logger.error("Error in retry failed jobs: %s", str(e))
            return {'status': 'error', 'error': str(e)}
    
    def _execute_data_migration_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute data migration job"""
        logger.info("Executing data migration job")
        try:
            # Placeholder for data migration logic
            result = {
                'status': 'success',
                'message': 'Data migration job placeholder - implementation pending',
                'migrated_records': 0
            }
            return result
        except Exception as e:
            logger.error("Error in data migration job: %s", str(e))
            return {'status': 'error', 'error': str(e)}
    
    def _execute_job_logs_cleanup_job(self, payload: Optional[Dict[str, Any]] = None):
        """Execute job logs cleanup — deletes old job_detail and job_execution_logs records."""
        logger.info("Executing job logs cleanup")
        try:
            if not self.job_detail_repository:
                return {'status': 'error', 'error': 'job_detail_repository not available'}

            deleted_job_detail = self._safe_run_async(
                self.job_detail_repository.delete_old_executions(days_old=60)
            )

            # Also clean up execution logs if repository available
            from libs.core.job_repositories import JobExecutionLogRepository
            deleted_exec_logs = 0
            try:
                exec_log_repo = JobExecutionLogRepository(
                    settings.AUDIT_DB_URI,
                    settings.AUDIT_DB_NAME
                )
                deleted_exec_logs = self._safe_run_async(
                    exec_log_repo.delete_old_logs(days_old=60)
                )
            except Exception as exec_err:
                logger.warning("Could not clean job_execution_logs: %s", exec_err)

            return {
                'status': 'success',
                'message': f'Deleted {deleted_job_detail} job_detail and {deleted_exec_logs} job_execution_logs records',
                'deleted_job_detail': deleted_job_detail,
                'deleted_exec_logs': deleted_exec_logs
            }
        except Exception as e:
            logger.error("Error in job logs cleanup: %s", str(e))
            return {'status': 'error', 'error': str(e)}
    
    async def get_job_execution_history(self, job_type: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get execution history for a specific job type"""
        try:
            # Get job execution history from the job detail repository
            if hasattr(self, 'job_detail_repository') and self.job_detail_repository:
                # The repository method expects job_id, which is the same as job_type in our case
                return await self.job_detail_repository.get_job_execution_history(job_type, limit)
            else:
                logger.warning("Job detail repository not available for history retrieval")
                return []
        except Exception as e:
            logger.error("Failed to get job execution history for %s: %s", job_type, str(e))
            return []

    async def get_all_job_executions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get all job executions across all job types"""
        try:
            if hasattr(self, 'job_detail_repository') and self.job_detail_repository:
                return await self.job_detail_repository.get_recent_job_executions(limit)
            else:
                logger.warning("Job detail repository not available for execution retrieval")
                return []
        except Exception as e:
            logger.error("Failed to get all job executions: %s", str(e))
            return []
    


# Global cron manager instance
cron_manager = CronManager()
