"""
Scheduler Application Service
Implements business logic for scheduler operations
"""

import logging
from typing import Dict, Any, List
from datetime import datetime

from ..interfaces.scheduler_service import ISchedulerService


class SchedulerService(ISchedulerService):
    """Implementation of scheduler service operations"""
    
    def __init__(self, cron_manager, dummy_data_service):
        """
        Initialize scheduler service
        
        Args:
            cron_manager: Cron manager instance
            dummy_data_service: Dummy data service instance
        """
        self._cron_manager = cron_manager
        self._dummy_data_service = dummy_data_service
        self._logger = logging.getLogger(__name__)
    
    async def get_scheduler_status(self) -> Dict[str, Any]:
        """Get current scheduler status and job counts"""
        try:
            jobs_info = await self._cron_manager.get_all_jobs()
            
            return {
                "status": "active" if self._cron_manager.is_running() else "inactive",
                "active_jobs": jobs_info.get('active_jobs', 0),
                "total_jobs": jobs_info.get('total_jobs', 0),
                "last_activity": datetime.now().isoformat()
            }
        except Exception as e:
            self._logger.error(f"Failed to get scheduler status: {str(e)}")
            raise
    
    async def get_all_jobs(self) -> Dict[str, Any]:
        """Get all scheduled jobs with their current status"""
        try:
            return await self._cron_manager.get_all_jobs()
        except Exception as e:
            self._logger.error(f"Failed to get all jobs: {str(e)}")
            raise
    
    async def get_job_execution_history(self, job_type: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get execution history for a specific job type"""
        try:
            return await self._cron_manager.get_job_execution_history(job_type, limit)
        except Exception as e:
            self._logger.error(f"Failed to get job history for {job_type}: {str(e)}")
            raise
    
    async def enable_job(self, job_type: str) -> bool:
        """Enable a specific job type"""
        try:
            return await self._cron_manager.enable_job(job_type)
        except Exception as e:
            self._logger.error(f"Failed to enable job {job_type}: {str(e)}")
            raise
    
    async def disable_job(self, job_type: str) -> bool:
        """Disable a specific job type"""
        try:
            return await self._cron_manager.disable_job(job_type)
        except Exception as e:
            self._logger.error(f"Failed to disable job {job_type}: {str(e)}")
            raise
    
    async def trigger_job(self, job_type: str) -> bool:
        """Manually trigger a specific job type"""
        try:
            return self._cron_manager.trigger_job_now(job_type)
        except Exception as e:
            self._logger.error(f"Failed to trigger job {job_type}: {str(e)}")
            raise
    
    async def reconcile_and_rerun_crons(self) -> Dict[str, Any]:
        """Reconcile data gaps and rerun all cron jobs"""
        try:
            return await self._dummy_data_service.reconcile_and_rerun_crons()
        except Exception as e:
            self._logger.error(f"Reconciliation failed: {str(e)}")
            raise