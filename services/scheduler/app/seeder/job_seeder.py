"""
Scheduler Job Seeder
Seeds scheduler-specific cron job configurations
"""

import logging
from typing import List, Dict, Any, Tuple
from libs.core.job_seeder import BaseJobSeeder
from libs.core.job_repositories import JobDataRepository
from services.scheduler.app.core.config import settings

logger = logging.getLogger(__name__)

# Standalone crons superseded by demand_serve_pipeline when pipeline is enabled
PIPELINE_STANDALONE_JOB_TYPES: Tuple[str, ...] = (
    "idf_forecast_merge",
    "demand_data_processing",
    "new_dsm_rate_calculation",
)


class SchedulerJobSeeder(BaseJobSeeder):
    """Seeder for scheduler-specific cron jobs"""
    
    def __init__(self, job_data_repository: JobDataRepository):
        """Initialize scheduler job seeder"""
        super().__init__(job_data_repository, "scheduler")
        self.settings = settings

    def _pipeline_enabled(self) -> bool:
        return getattr(self.settings, "DEMAND_SERVE_PIPELINE_CRON_ENABLED", True)

    def _standalone_step_cron_enabled(self, setting_attr: str, default: bool = True) -> bool:
        """When pipeline is on, standalone step crons are disabled to avoid races."""
        if self._pipeline_enabled():
            return False
        return getattr(self.settings, setting_attr, default)

    async def seed_default_jobs(self) -> Dict[str, Any]:
        results = await super().seed_default_jobs()
        await self._sync_pipeline_job_policy()
        return results

    async def _sync_pipeline_job_policy(self) -> None:
        """Align enabled flags in job_data with pipeline on/off."""
        pipeline_enabled = self._pipeline_enabled()
        for job_type in PIPELINE_STANDALONE_JOB_TYPES:
            existing = await self.job_data_repository.get_job(job_type)
            if not existing:
                continue
            desired = not pipeline_enabled
            if existing.get("enabled", True) != desired:
                await self.job_data_repository.update_job_enabled_status(job_type, desired)
                logger.info(
                    "Pipeline policy: set %s enabled=%s (pipeline_enabled=%s)",
                    job_type,
                    desired,
                    pipeline_enabled,
                )

        pipeline_job = await self.job_data_repository.get_job("demand_serve_pipeline")
        if pipeline_job and pipeline_job.get("enabled", True) != pipeline_enabled:
            await self.job_data_repository.update_job_enabled_status(
                "demand_serve_pipeline", pipeline_enabled
            )
            logger.info(
                "Pipeline policy: set demand_serve_pipeline enabled=%s",
                pipeline_enabled,
            )
    
    def get_default_jobs(self) -> List[Dict[str, Any]]:
        """Get default scheduler jobs"""
        return [
            # Core Data Management Jobs
            {
                "job_type": "date_update",
                "name": "Date Update for Scheduled Entries",
                "type": "data_management",
                "schedule": getattr(self.settings, 'DATA_UPDATE_CRON', "0 0 * * *"),  # Daily at midnight
                "enabled": getattr(self.settings, 'DATA_UPDATE_CRON_ENABLED', False),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Updates scheduled_date fields in serve collections",
                "priority": 3
            },
            # 
            # Data Processing Jobs
            {
                "job_type": "demand_data_processing",
                "name": "Demand Data Processing",
                "type": "data_processing",
                "schedule": getattr(
                    self.settings,
                    'DEMAND_DATA_PROCESSING_CRON',
                    getattr(self.settings, 'DEMAND_DATA_PROCESSING_CRON_SCHEDULE', '*/15 * * * *')
                ),
                "enabled": self._standalone_step_cron_enabled(
                    "DEMAND_DATA_PROCESSING_CRON_ENABLED", True
                ),
                "timezone": getattr(self.settings, 'DEMAND_DATA_PROCESSING_CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Processes demand data from raw data to serve collections",
                "priority": 4
            },
            {
                "job_type": "demand_schedule_update",
                "name": "Demand Schedule Update",
                "type": "data_processing",
                "schedule": "*/5 * * * *",  # Every 5 minutes
                "enabled": True,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Updates the schedule field in demand_serve based on aggregated supply data",
                "priority": 5
            },
            {
                "job_type": "demand_temp_sync",
                "name": "Demand Temp Sync",
                "type": "data_processing",
                "schedule": "*/15 * * * *",  # Every 15 minutes
                "enabled": True,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Processes current time block from temp_demand_serve into demand_serve with full calculations",
                "priority": 5
            },
            {
                "job_type": "idf_forecast_merge",
                "name": "IDF Forecast Merge",
                "type": "data_processing",
                "schedule": getattr(
                    self.settings,
                    "IDF_FORECAST_MERGE_CRON_SCHEDULE",
                    "*/15 * * * *",
                ),
                "enabled": self._standalone_step_cron_enabled(
                    "IDF_FORECAST_MERGE_CRON_ENABLED", True
                ),
                "timezone": getattr(self.settings, "CRON_TIMEZONE", "Asia/Kolkata"),
                "description": "Merges forecast_serve (RTM API or CSV) into demand_serve IDF per idf_forecast_switch",
                "priority": 5,
            },
            {
                "job_type": "demand_serve_pipeline",
                "name": "Demand Serve Pipeline (IDF → Demand → DSM)",
                "type": "data_processing",
                "schedule": getattr(
                    self.settings,
                    "DEMAND_SERVE_PIPELINE_CRON_SCHEDULE",
                    "*/15 * * * *",
                ),
                "enabled": getattr(self.settings, "DEMAND_SERVE_PIPELINE_CRON_ENABLED", True),
                "timezone": getattr(self.settings, "CRON_TIMEZONE", "Asia/Kolkata"),
                "description": (
                    "Runs idf_forecast_merge, demand_data_processing, and new_dsm_rate_calculation "
                    "sequentially every 15 minutes"
                ),
                "priority": 3,
            },
            {
                "job_type": "supply_data_processing",
                "name": "Supply Data Processing",
                "type": "data_processing",
                "schedule": getattr(
                    self.settings,
                    'SUPPLY_DATA_PROCESSING_CRON',
                    getattr(self.settings, 'SUPPLY_DATA_PROCESSING_CRON_SCHEDULE', '*/15 * * * *')
                ),
                "enabled": getattr(self.settings, 'SUPPLY_DATA_PROCESSING_CRON_ENABLED', True),
                "timezone": getattr(self.settings, 'SUPPLY_DATA_PROCESSING_CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Processes supply data from raw data to serve collections",
                "priority": 4
            },
            # New Processor Jobs
            {
                "job_type": "weather_data_processing",
                "name": "Weather Data Processing",
                "type": "data_processing",
                "schedule": getattr(self.settings, 'WEATHER_DATA_PROCESSING_CRON', '*/15 * * * *'),  # Every 15 minutes
                "enabled": getattr(self.settings, 'WEATHER_DATA_PROCESSING_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Processes weather data from raw data to weather_serve collection",
                "priority": 5
            },
            {
                "job_type": "bid_data_processing",
                "name": "Bid Data Processing",
                "type": "data_processing",
                "schedule": getattr(self.settings, 'BID_DATA_PROCESSING_CRON', "*/15 * * * *"),  # Every 15 minutes
                "enabled": getattr(self.settings, 'BID_DATA_PROCESSING_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Processes bid data from raw data to bid_trade_serve collection",
                "priority": 5
            },
            {
                "job_type": "exchange_data_processing",
                "name": "Exchange Data Processing",
                "type": "data_processing",
                "schedule": getattr(self.settings, 'EXCHANGE_DATA_PROCESSING_CRON', "*/15 * * * *"),
                "enabled": getattr(self.settings, 'EXCHANGE_DATA_PROCESSING_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Processes exchange data from raw data to exchange_serve collection with complex logic",
                "priority": 5
            },
            {
                "job_type": "exchange_trade_processing",
                "name": "Exchange Trade Processing",
                "type": "data_processing",
                "schedule": getattr(self.settings, 'EXCHANGE_TRADE_PROCESSING_CRON', "*/15 * * * *"),
                "enabled": getattr(self.settings, 'EXCHANGE_TRADE_PROCESSING_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Processes exchange trade data from raw data to exchange_trade_serve collection",
                "priority": 5
            },
            {
                "job_type": "exchange_dsm_processing",
                "name": "Exchange DSM Processing",
                "type": "data_processing",
                "schedule": getattr(self.settings, 'EXCHANGE_DSM_PROCESSING_CRON', "*/15 * * * *"),
                "enabled": getattr(self.settings, 'EXCHANGE_DSM_PROCESSING_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Processes exchange DSM data from raw data to exchange_dsm_serve collection",
                "priority": 5
            },
            {
                "job_type": "renewable_data_processing",
                "name": "Renewable Data Processing",
                "type": "data_processing",
                "schedule": getattr(self.settings, 'RENEWABLE_DATA_PROCESSING_CRON', "*/15 * * * *"),
                "enabled": getattr(self.settings, 'RENEWABLE_DATA_PROCESSING_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Processes renewable energy data from raw data to renewable_serve collection",
                "priority": 5
            },
            {
                "job_type": "exchange_serve_archival",
                "name": "Exchange Serve Data Archival",
                "type": "maintenance",
                "schedule": "15 3 * * *",  # Daily at 03:15 AM after final upstream processing
                "enabled": True,  # Enabled for 4-day rolling window archival
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Archives exchange serve data older than 4 days to historical collection after final upstream processing (4-day rolling window: today, tomorrow, yesterday, day before yesterday)",
                "priority": 7
            },
            # 
            # System Maintenance Jobs
            {
                "job_type": "system_cleanup",
                "name": "System Cleanup",
                "type": "maintenance",
                "schedule": getattr(self.settings, 'SYSTEM_CLEANUP_SCHEDULE', '0 2 * * *'),
                "enabled": getattr(self.settings, 'SYSTEM_CLEANUP_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'UTC'),
                "description": "Cleans up old integration apilog data dynamically across all *_integration_apilog collections",
                "priority": 8
            },
            {
                "job_type": "urs_backfill",
                "name": "URS Backfill (one-time)",
                "type": "maintenance",
                # Never scheduled. Seeded only so it can be triggered by hand
                # from the scheduler API; the work is a one-time recovery of
                # days that predate URS storage, not something to repeat.
                "schedule": "0 0 31 2 *",
                "enabled": False,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'UTC'),
                "description": "Recovers un-requisitioned surplus for a pinned past window into urs_serve. Trigger manually; skips days already done.",
                "priority": 9
            },
            {
                "job_type": "integration_apilog_cleanup",
                "name": "Integration API Log Cleanup",
                "type": "maintenance",
                "schedule": getattr(self.settings, 'INTEGRATION_API_LOG_CLEANUP_SCHEDULE', '0 2 * * *'),
                "enabled": getattr(self.settings, 'INTEGRATION_API_LOG_CLEANUP_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'UTC'),
                "description": "Deletes integration API logs older than 7 days from all *_integration_apilog collections",
                "priority": 8
            },
            {
                "job_type": "scheduling_history_cleanup",
                "name": "Scheduling History Cleanup",
                "type": "maintenance",
                "schedule": getattr(self.settings, 'SCHEDULING_HISTORY_CLEANUP_SCHEDULE', '0 1 * * *'),
                "enabled": getattr(self.settings, 'SCHEDULING_HISTORY_CLEANUP_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'UTC'),
                "description": "Deletes scheduling requisition calculation history older than 90 days",
                "priority": 8
            },
            {
                "job_type": "retry_failed_jobs",
                "name": "Retry Failed Jobs",
                "type": "recovery",
                "schedule": getattr(self.settings, 'RETRY_FAILED_JOBS_SCHEDULE', '*/15 * * * *'),
                "enabled": getattr(self.settings, 'RETRY_FAILED_JOBS_ENABLED', False),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'UTC'),
                "description": "Retries failed job executions",
                "priority": 9
            },
            {
                "job_type": "demand_serve_archival",
                "name": "Demand Serve Data Archival",
                "type": "maintenance",
                "schedule": "15 3 * * *",  # Daily at 03:15 AM after final DSM run
                "enabled": True,  # Enabled for 4-day rolling window archival
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Archives demand serve data older than 4 days to historical collection after DSM finalisation (4-day rolling window: today, tomorrow, yesterday, day before yesterday)",
                "priority": 7
            },
            {
                "job_type": "forecast_serve_archival",
                "name": "Forecast Serve Data Archival",
                "type": "maintenance",
                "schedule": "15 3 * * *",
                "enabled": True,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Mirrors every forecast_serve document into forecast_serve_historical; deletes live rows older than the rolling window (same pattern as demand_serve)",
                "priority": 7,
            },
            {
                "job_type": "supply_serve_archival",
                "name": "Supply Serve Data Archival",
                "type": "maintenance",
                "schedule": "15 3 * * *",  # Daily at 03:15 AM after final upstream processing
                "enabled": True,  # Enabled for 4-day rolling window archival
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Archives supply serve data older than 4 days to historical collection after final upstream processing (4-day rolling window: today, tomorrow, yesterday, day before yesterday)",
                "priority": 7
            },
            {
                "job_type": "weather_serve_archival",
                "name": "Weather Serve Data Archival",
                "type": "maintenance",
                "schedule": "15 3 * * *",  # Daily at 03:15 AM after final upstream processing
                "enabled": True,  # Enabled for 4-day rolling window archival
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Archives weather serve data older than 4 days to historical collection after final upstream processing (4-day rolling window: today, tomorrow, yesterday, day before yesterday)",
                "priority": 7
            },
            {
                "job_type": "bid_serve_archival",
                "name": "Bid Serve Data Archival",
                "type": "maintenance",
                "schedule": "15 3 * * *",  # Daily at 03:15 AM after final upstream processing
                "enabled": True,  # Enabled for 4-day rolling window archival
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Archives bid serve data older than 4 days to historical collection after final upstream processing (4-day rolling window: today, tomorrow, yesterday, day before yesterday)",
                "priority": 7
            },
            {
                "job_type": "renewable_serve_archival",
                "name": "Renewable Serve Data Archival",
                "type": "maintenance",
                "schedule": "15 3 * * *",  # Daily at 03:15 AM after final upstream processing
                "enabled": True,  # Enabled for 4-day rolling window archival
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Archives renewable serve data older than 4 days to historical collection after final upstream processing (4-day rolling window: today, tomorrow, yesterday, day before yesterday)",
                "priority": 7
            },
            # 
            # Data Archival Jobs
            {
                "job_type": "data_migration",
                "name": "Data Migration to Historical",
                "type": "maintenance",
                "schedule": getattr(self.settings, 'JOB_LOGS_MIGRATION_SCHEDULE', '0 3 * * *'),
                "enabled": False,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'UTC'),
                "description": "Migrates old data to historical collection",
                "priority": 10
            },
            {
                "job_type": "job_logs_cleanup",
                "name": "Job Logs Cleanup",
                "type": "maintenance",
                "schedule": getattr(self.settings, 'JOB_LOGS_CLEANUP_SCHEDULE', '0 4 * * *'),
                "enabled": True,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'UTC'),
                "description": "Cleans up old job execution logs from job_detail older than 60 days",
                "priority": 11
            }
            ,
            {
                "job_type": "peak_demand_compute",
                "name": "Peak Demand Compute",
                "type": "data_processing",
                "schedule": "0 0 * * *",  # Daily at 12 AM
                "enabled": True,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Computes yesterday's peak comp_act_demand and stores in peak_demand_serve",
                "priority": 6
            },
            {
                "job_type": "dsm_rate_calculation",
                "name": "DSM Rate and Net DSM Amount Calculation",
                "type": "data_processing",
                "schedule": getattr(self.settings, 'DSM_RATE_CALCULATION_CRON', "*/15 * * * *"),  # Every 15 minutes
                "enabled": getattr(self.settings, 'DSM_RATE_CALCULATION_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Calculates DSM rates from exchange data and net DSM amounts for demand serve data every 15 minutes",
                "priority": 5
            },
            {
                "job_type": "new_dsm_rate_calculation",
                "name": "New DSM Rate and Net DSM Amount Calculation",
                "type": "data_processing",
                "schedule": getattr(self.settings, 'NEW_DSM_RATE_CALCULATION_CRON', "*/15 * * * *"),  # Every 15 minutes
                "enabled": self._standalone_step_cron_enabled(
                    "NEW_DSM_RATE_CALCULATION_ENABLED", True
                ),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Calculates DSM rates using new DSM engine with multi-exchange aggregation (IEX, PXIL, HPX) every 15 minutes",
                "priority": 5
            },
            {
                "job_type": "limit_values_approval_processing",
                "name": "Limit Values Approval Processing",
                "type": "data_processing",
                "schedule": getattr(self.settings, 'LIMIT_VALUES_APPROVAL_CRON', "0 0 * * *"),  # Daily at midnight
                "enabled": getattr(self.settings, 'LIMIT_VALUES_APPROVAL_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Processes approved future-dated limit values and moves them to final collection when date arrives",
                "priority": 2
            },
            {
                "job_type": "limit_values_rtm_approval_processing",
                "name": "Limit Values RTM Early Approval Processing",
                "type": "data_processing",
                "schedule": getattr(self.settings, 'LIMIT_VALUES_RTM_APPROVAL_CRON', "15 22 * * *"),  # RTM rolls over at 22:15
                "enabled": getattr(self.settings, 'LIMIT_VALUES_RTM_APPROVAL_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Early rollover for RTM limit values to tomorrow so they are not re-rolled at midnight",
                "priority": 2
            },
            {
                "job_type": "rtm_scheduling_calculation",
                "name": "RTM Scheduling Calculation",
                "type": "data_processing",
                "schedule": getattr(self.settings, 'RTM_SCHEDULING_CRON', "*/30 * * * *"),  # Every 30 minutes
                "enabled": getattr(self.settings, 'RTM_SCHEDULING_ENABLED', True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Calculates RTM scheduling table data independently with complete calculation flow",
                "priority": 5
            },
            {
                "job_type": "supply_data_midnight_fill",
                "name": "Supply Data Midnight Fill",
                "type": "data_management",
                "schedule": "0 0 * * *",  # Daily at midnight
                "enabled": True,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Auto-fills missing supply data for today from historical dc_sg records",
                "priority": 1
            },
            {
                "job_type": "dam_bid_status_sync",
                "name": "DAM Bid Status Sync",
                "type": "data_sync",
                "schedule": "0,30 14-15 * * *",  # At 14:00, 14:30, 15:00, 15:30 IST (covers required times)
                "enabled": False,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Syncs DAM bid status by fetching bid details for today's submitted bids",
                "priority": 6
            },
            {
                "job_type": "gdam_bid_status_sync",
                "name": "GDAM Bid Status Sync",
                "type": "data_sync",
                "schedule": "0,30 14-15 * * *",  # At 14:00, 14:30, 15:00, 15:30 IST (covers required times)
                "enabled": False,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Syncs GDAM bid status by fetching bid details for today's submitted bids",
                "priority": 6
            },
            {
                "job_type": "hpdam_bid_status_sync",
                "name": "HPDAM Bid Status Sync",
                "type": "data_sync",
                "schedule": "0,30 14-15 * * *",  # At 14:00, 14:30, 15:00, 15:30 IST (covers required times)
                "enabled": False,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Syncs HPDAM bid status by fetching bid details for today's submitted bids",
                "priority": 6
            },
            {
                "job_type": "rtm_bid_status_sync",
                "name": "RTM Bid Status Sync",
                "type": "data_sync",
                "schedule": "*/30 * * * *",  # Every 30 minutes
                "enabled": False,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Syncs RTM bid status by fetching bid details for today's submitted bids",
                "priority": 6
            },
            {
                "job_type": "dam_single_bid_book_sync",
                "name": "DAM Single Bid Book Sync",
                "type": "data_sync",
                "schedule": "0,30 14-16 * * *",  # 2:30 PM to 4:30 PM (Runs 14:00, 14:30, 15:00, 15:30, 16:00, 16:30)
                "enabled": True,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Syncs DAM Single Bid Book from Exchange API to BookCollection",
                "priority": 5
            },
            {
                "job_type": "gdam_single_bid_book_sync",
                "name": "GDAM Single Bid Book Sync",
                "type": "data_sync",
                "schedule": "0,30 14-16 * * *",  # 2:30 PM to 4:30 PM (Runs 14:00, 14:30, 15:00, 15:30, 16:00, 16:30)
                "enabled": True,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Syncs GDAM Single Bid Book from Exchange API to BookCollection",
                "priority": 5
            },
            {
                "job_type": "hpdam_single_bid_book_sync",
                "name": "HPDAM Single Bid Book Sync",
                "type": "data_sync",
                "schedule": "0,30 14-16 * * *",  # 2:30 PM to 4:30 PM (Runs 14:00, 14:30, 15:00, 15:30, 16:00, 16:30)
                "enabled": True,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Syncs HPDAM Single Bid Book from Exchange API to BookCollection",
                "priority": 5
            },
            {
                "job_type": "rtm_single_bid_book_sync",
                "name": "RTM Single Bid Book Sync",
                "type": "data_sync",
                "schedule": "*/30 * * * *",  # Every 30 minutes
                "enabled": True,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Syncs RTM Single Bid Book from Exchange API to BookCollection",
                "priority": 5
            },
            {
                "job_type": "bid_trade_live_sync",
                "name": "Bid Trade Live Sync",
                "type": "data_sync",
                "schedule": "*/30 * * * *",  # Every 30 minutes
                "enabled": True,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Syncs live bid trade data from DAM and RTM",
                "priority": 5
            },
            {
                "job_type": "supply_tomorrow_sync",
                "name": "Supply Tomorrow Sync",
                "type": "data_sync",
                "schedule": "*/15 * * * *",  # Every 15 minutes
                "enabled": True,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Force syncs tomorrow's supply data from dc_sg to supply_serve",
                "priority": 5
            },
            {
                "job_type": "rtm_bid_lapse_processing",
                "name": "RTM Bid Lapse Processing",
                "type": "data_processing",
                "schedule": "*/15 * * * *",  # Every 15 minutes
                "enabled": True,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Marks RTM bids as lapsed when session passes active window (current session + 3)",
                "priority": 6
            },
            {
                "job_type": "rtm_block_bid_lapse_processing",
                "name": "RTM Block Bid Lapse Processing",
                "type": "data_processing",
                "schedule": "*/15 * * * *",  # Every 15 minutes
                "enabled": True,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Marks RTM block bids as lapsed when trade_session passes active window (current session + 3)",
                "priority": 7
            },
            {
                "job_type": "rtm_block_bid_book_sync",
                "name": "RTM Block Bid Book Sync",
                "type": "data_sync",
                "schedule": "*/15 * * * *",  # Every 15 minutes
                "enabled": True,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Syncs RTM block bid book data every 15 minutes",
                "priority": 7
            },
            {
                "job_type": "dam_bid_capture_snapshot",
                "name": "DAM Bid Capture Snapshot",
                "type": "data_sync",
                "schedule": "59 10 * * *",
                "enabled": True,
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Runs DAM bid capture snapshot daily at 10:59 IST",
                "priority": 6
            },
            {
                # Analytics Dashboard export worker. Also registered in
                # CronManager._get_job_function and _register_fallback_jobs — miss
                # any one and the job runs once at boot and never fires again.
                # Every minute is the floor: _add_job_to_scheduler rejects anything
                # that is not exactly five cron fields.
                "job_type": "analytics_export_sweep",
                "name": "Analytics Export Sweep",
                "type": "system_maintenance",
                "schedule": getattr(self.settings, "ANALYTICS_EXPORT_SWEEP_CRON", "* * * * *"),
                "enabled": getattr(self.settings, "ANALYTICS_EXPORT_SWEEP_ENABLED", True),
                "timezone": getattr(self.settings, 'CRON_TIMEZONE', 'Asia/Kolkata'),
                "description": "Claims queued analytics export jobs, writes their files, requeues abandoned ones and deletes expired exports",
                "priority": 9
            }
        ]


# Global scheduler job seeder instance
def create_scheduler_job_seeder(job_data_repository: JobDataRepository) -> SchedulerJobSeeder:
    """Create scheduler job seeder instance"""
    return SchedulerJobSeeder(job_data_repository)
