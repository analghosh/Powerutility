"""
Job Monitoring Application Service
Implements business logic for job monitoring operations
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from ..interfaces.job_monitoring_service import IJobMonitoringService


class JobMonitoringService(IJobMonitoringService):
    """Implementation of job monitoring service operations"""
    
    def __init__(self, job_detail_repository, job_data_repository, audit_service):
        """
        Initialize job monitoring service
        
        Args:
            job_detail_repository: Job detail repository instance
            job_data_repository: Job data repository instance  
            audit_service: Audit service instance
        """
        from libs.core.job_repositories import JobDetailRepository, JobDataRepository
        
        self._job_detail_repository: JobDetailRepository = job_detail_repository
        self._job_data_repository: JobDataRepository = job_data_repository
        self._audit_service = audit_service
        self._logger = logging.getLogger(__name__)
    
    async def get_running_jobs(self) -> List[Dict[str, Any]]:
        """Get all currently running jobs"""
        try:
            return self._job_detail_repository.get_running_jobs()
        except Exception as e:
            self._logger.error(f"Failed to get running jobs: {str(e)}")
            raise
    
    async def get_job_trace(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed trace information for a specific job"""
        try:
            return await self._job_detail_repository.get_job_execution_trace(job_id)
        except Exception as e:
            self._logger.error(f"Failed to get job trace for {job_id}: {str(e)}")
            raise
    
    async def get_jobs_by_service(self, service_name: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get jobs for a specific service"""
        try:
            return await self._job_detail_repository.get_jobs_by_service(service_name, limit)
        except Exception as e:
            self._logger.error(f"Failed to get jobs for service {service_name}: {str(e)}")
            raise
    
    async def get_job_statistics(self, hours: int = 24) -> Dict[str, Any]:
        """Get job statistics for the specified time period"""
        try:
            return await self._job_detail_repository.get_overall_job_statistics(hours)
        except Exception as e:
            self._logger.error(f"Failed to get job statistics: {str(e)}")
            raise
    
    async def update_job_progress(self, job_id: str, progress: int, 
                                current_step: str, estimated_completion: str) -> bool:
        """Update job progress and current step"""
        try:
            return await self._job_detail_repository.update_job_progress(
                job_id, progress, current_step, estimated_completion
            )
        except Exception as e:
            self._logger.error(f"Failed to update job progress for {job_id}: {str(e)}")
            raise
    
    async def get_dashboard_data(self) -> Dict[str, Any]:
        """Get comprehensive job dashboard data"""
        try:
            # Get running jobs
            running_jobs = await self.get_running_jobs()
            
            # Get statistics for different time periods
            stats_24h = await self._job_detail_repository.get_overall_job_statistics(24, self._job_data_repository)
            stats_7d = await self._job_detail_repository.get_overall_job_statistics(168, self._job_data_repository)
            
            # Get recent jobs
            recent_jobs = await self._job_detail_repository.get_jobs_by_service("scheduler", 50)
            
            # Build dashboard data
            dashboard_data = {
                "overview": {
                    "running_jobs": len(running_jobs),
                    "total_jobs_24h": stats_24h.get("total_jobs", 0),
                    "success_rate_24h": stats_24h.get("success_rate", 0),
                    "average_execution_time": stats_24h.get("average_execution_time", 0)
                },
                "running_jobs": running_jobs,
                "statistics_24h": stats_24h,
                "statistics_7d": stats_7d,
                "recent_activity": recent_jobs[:10],
                "service_breakdown": stats_24h.get("service_breakdown", {}),
                "generated_at": datetime.now().isoformat()
            }
            
            return dashboard_data
        except Exception as e:
            self._logger.error(f"Failed to get dashboard data: {str(e)}")
            raise
    
    async def get_system_health(self) -> Dict[str, Any]:
        """Get job system health status"""
        try:
            # Get running jobs
            running_jobs = await self.get_running_jobs()
            
            # Get statistics for last hour
            stats_1h = await self._job_detail_repository.get_overall_job_statistics(1)
            
            # Analyze system health
            health_status = "healthy"
            issues = []
            
            # Check for stuck jobs (running for more than 2 hours)
            current_time = datetime.now()
            stuck_jobs = []
            for job in running_jobs:
                if job.get("execution_start"):
                    runtime = (current_time - job["execution_start"]).total_seconds()
                    if runtime > 7200:  # 2 hours
                        stuck_jobs.append({
                            "job_id": job["job_id"],
                            "job_name": job.get("job_name", ""),
                            "runtime_seconds": runtime,
                            "runtime_formatted": job.get("runtime_formatted", "")
                        })
            
            # Determine health status
            if len(stuck_jobs) > 0:
                health_status = "warning"
                issues.append(f"{len(stuck_jobs)} jobs appear to be stuck")
            
            if stats_1h.get("success_rate", 100) < 80:
                health_status = "warning"
                issues.append(f"Low success rate: {stats_1h.get('success_rate', 0)}%")
            
            if len(running_jobs) > 20:  # Configurable threshold
                health_status = "warning"
                issues.append(f"High number of running jobs: {len(running_jobs)}")
            
            return {
                "status": health_status,
                "running_jobs": len(running_jobs),
                "stuck_jobs": len(stuck_jobs),
                "stuck_jobs_details": stuck_jobs,
                "success_rate_1h": stats_1h.get("success_rate", 0),
                "issues": issues,
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            self._logger.error(f"Failed to get system health: {str(e)}")
            raise
    
    async def get_job_execution_details(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed execution information for a job"""
        try:
            return await self._job_detail_repository.get_job_execution_details(job_id, self._job_data_repository)
        except Exception as e:
            self._logger.error(f"Failed to get job execution details for {job_id}: {str(e)}")
            raise
    
    async def get_audit_logs_by_job(self, job_id: str, priority: Optional[str] = None, 
                                  status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Get audit logs for a specific job ID"""
        try:
            # Get audit logs by job ID
            audit_logs = self._audit_service.get_audit_logs_by_job(job_id)
            
            # Apply filters
            if priority:
                audit_logs = [log for log in audit_logs 
                             if log.get('meta', {}).get('priority', '').lower() == priority.lower()]
            
            if status:
                audit_logs = [log for log in audit_logs 
                             if log.get('status', '').lower() == status.lower()]
            
            # Limit results
            return audit_logs[:limit]
        except Exception as e:
            self._logger.error(f"Failed to get audit logs for job {job_id}: {str(e)}")
            raise
    
    async def search_audit_logs(self, search_text: str, job_id: Optional[str] = None, 
                              service: str = "scheduler", limit: int = 100) -> List[Dict[str, Any]]:
        """Search job audit logs by text"""
        try:
            # Perform search
            search_results = self._audit_service.search_audit_logs(
                search_text=search_text,
                service=service,
                log_type="job",
                limit=limit
            )
            
            # Filter by job_id if provided
            if job_id:
                search_results = [log for log in search_results 
                                if log.get('trace', {}).get('jobId') == job_id]
            
            return search_results
        except Exception as e:
            self._logger.error(f"Failed to search audit logs: {str(e)}")
            raise
    
    async def get_jobs_by_service_from_data(self, service: str, enabled_only: bool = True) -> List[Dict[str, Any]]:
        """Get jobs for a specific service from job data collection"""
        try:
            return await self._job_data_repository.get_jobs_by_service(service, enabled_only)
        except Exception as e:
            self._logger.error(f"Failed to get jobs for service {service}: {str(e)}")
            raise
    
    async def get_all_jobs_from_job_data(self) -> List[Dict[str, Any]]:
        """Get all jobs from job data collection"""
        try:
            return self._job_detail_repository.get_all_jobs_from_job_data()
        except Exception as e:
            self._logger.error(f"Failed to get all jobs from job data: {str(e)}")
            raise