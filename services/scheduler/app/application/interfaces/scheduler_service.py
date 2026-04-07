"""
Scheduler Service Interface
Defines the contract for scheduler operations
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional


class ISchedulerService(ABC):
    """Interface for scheduler service operations"""
    
    @abstractmethod
    async def get_scheduler_status(self) -> Dict[str, Any]:
        """Get current scheduler status and job counts"""
        pass
    
    @abstractmethod
    async def get_all_jobs(self) -> Dict[str, Any]:
        """Get all scheduled jobs with their current status"""
        pass
    
    @abstractmethod
    async def get_job_execution_history(self, job_type: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get execution history for a specific job type"""
        pass
    
    @abstractmethod
    async def enable_job(self, job_type: str) -> bool:
        """Enable a specific job type"""
        pass
    
    @abstractmethod
    async def disable_job(self, job_type: str) -> bool:
        """Disable a specific job type"""
        pass
    
    @abstractmethod
    async def trigger_job(self, job_type: str) -> bool:
        """Manually trigger a specific job type"""
        pass
    
    @abstractmethod
    async def reconcile_and_rerun_crons(self) -> Dict[str, Any]:
        """Reconcile data gaps and rerun all cron jobs"""
        pass