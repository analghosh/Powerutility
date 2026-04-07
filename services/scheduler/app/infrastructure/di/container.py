"""
Dependency Injection Container
Manages dependencies and their lifecycles
"""

from typing import Dict, Any
import logging

from ...core.config import settings
from ...services.cron_manager import cron_manager
from ...services.dummy_data_service import dummy_data_service
from ...application.services.scheduler_service import SchedulerService
from ...application.services.job_monitoring_service import JobMonitoringService
from libs.core.job_factory import create_job_detail_repository, create_job_data_repository
from libs.audit_queue.unified_queue_service import UnifiedQueueService


class AuditService:
    """Wrapper for audit service operations"""
    
    def __init__(self, unified_queue_service: UnifiedQueueService):
        self.unified_queue_service = unified_queue_service

    def get_audit_logs_by_job(self, job_id: str):
        """Get audit logs by job ID"""
        return self.unified_queue_service.get_audit_logs_by_job(job_id)
    
    def search_audit_logs(self, search_text: str, service: str, log_type: str, limit: int):
        """Search audit logs"""
        return self.unified_queue_service.search_audit_logs(
            search_text=search_text,
            service=service,
            log_type=log_type,
            limit=limit
        )


class DIContainer:
    """Dependency injection container"""
    
    def __init__(self):
        """Initialize the container"""
        self._instances: Dict[str, Any] = {}
        self._logger = logging.getLogger(__name__)
        self._setup_dependencies()
    
    def _setup_dependencies(self):
        """Set up all dependencies"""
        try:
            # Infrastructure dependencies
            self._instances['job_detail_repository'] = create_job_detail_repository(
                settings.AUDIT_DB_URI,
                settings.AUDIT_DB_NAME,
                settings.JOB_DETAIL_COLLECTION
            )
            
            self._instances['job_data_repository'] = create_job_data_repository(
                settings.AUDIT_DB_URI,
                settings.AUDIT_DB_NAME,
                settings.JOB_DATA_COLLECTION
            )
            
            unified_queue_service = UnifiedQueueService(
                mongodb_uri=settings.MONGODB_URI,
                audit_db_name=settings.AUDIT_DB_NAME,
                audit_collection_name=settings.AUDIT_COLLECTION_NAME,
                queue_collection_name=settings.QUEUE_COLLECTION_NAME
            )
            self._instances['audit_service'] = AuditService(unified_queue_service)
            
            # Domain services (existing)
            self._instances['cron_manager'] = cron_manager
            self._instances['dummy_data_service'] = dummy_data_service
            
            # Application services
            self._instances['scheduler_service'] = SchedulerService(
                cron_manager=self._instances['cron_manager'],
                dummy_data_service=self._instances['dummy_data_service']
            )
            
            self._instances['job_monitoring_service'] = JobMonitoringService(
                job_detail_repository=self._instances['job_detail_repository'],
                job_data_repository=self._instances['job_data_repository'],
                audit_service=self._instances['audit_service']
            )
            
            self._logger.info("Dependency injection container initialized successfully")
            
        except Exception as e:
            self._logger.error(f"Failed to setup dependencies: {str(e)}")
            raise
    
    def get(self, service_name: str):
        """Get a service instance by name"""
        if service_name not in self._instances:
            raise ValueError(f"Service '{service_name}' not found in container")
        return self._instances[service_name]
    
    def get_scheduler_service(self) -> SchedulerService:
        """Get scheduler service instance"""
        return self.get('scheduler_service')
    
    def get_job_monitoring_service(self) -> JobMonitoringService:
        """Get job monitoring service instance"""
        return self.get('job_monitoring_service')


# Global container instance
container = DIContainer()
