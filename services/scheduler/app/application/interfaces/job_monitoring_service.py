"""
Job Monitoring Service Interface
Defines the contract for job monitoring operations
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional


class IJobMonitoringService(ABC):
    """Interface for job monitoring service operations"""
    
    @abstractmethod
    async def get_running_jobs(self) -> List[Dict[str, Any]]:
        """Get all currently running jobs"""
        pass
    
    @abstractmethod
    async def get_job_trace(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed trace information for a specific job"""
        pass
    
    @abstractmethod
    async def get_jobs_by_service(self, service_name: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get jobs for a specific service"""
        pass
    
    @abstractmethod
    async def get_job_statistics(self, hours: int = 24) -> Dict[str, Any]:
        """Get job statistics for the specified time period"""
        pass
    
    @abstractmethod
    async def update_job_progress(self, job_id: str, progress: int, 
                                current_step: str, estimated_completion: str) -> bool:
        """Update job progress and current step"""
        pass
    
    @abstractmethod
    async def get_dashboard_data(self) -> Dict[str, Any]:
        """Get comprehensive job dashboard data"""
        pass
    
    @abstractmethod
    async def get_system_health(self) -> Dict[str, Any]:
        """Get job system health status"""
        pass
    
    @abstractmethod
    async def get_job_execution_details(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed execution information for a job"""
        pass
    
    @abstractmethod
    async def get_audit_logs_by_job(self, job_id: str, priority: Optional[str] = None, 
                                  status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Get audit logs for a specific job ID"""
        pass
    
    @abstractmethod
    async def search_audit_logs(self, search_text: str, job_id: Optional[str] = None, 
                              service: str = "scheduler", limit: int = 100) -> List[Dict[str, Any]]:
        """Search job audit logs by text"""
        pass
    
    @abstractmethod
    async def get_jobs_by_service_from_data(self, service: str, enabled_only: bool = True) -> List[Dict[str, Any]]:
        """Get jobs for a specific service from job data collection"""
        pass
    
    @abstractmethod
    async def get_all_jobs_from_job_data(self) -> List[Dict[str, Any]]:
        """Get all jobs from job data collection"""
        pass