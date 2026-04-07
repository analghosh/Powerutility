"""
Custom Exceptions for Scheduler Service
Provides domain-specific exceptions for better error handling
"""

from typing import Optional, Dict, Any


class SchedulerServiceException(Exception):
    """Base exception for scheduler service"""
    
    def __init__(self, message: str, error_code: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        super().__init__(self.message)


class JobNotFoundException(SchedulerServiceException):
    """Exception raised when a job is not found"""
    
    def __init__(self, job_id: str, message: Optional[str] = None):
        self.job_id = job_id
        message = message or f"Job '{job_id}' not found"
        super().__init__(message, "JOB_NOT_FOUND", {"job_id": job_id})


class JobExecutionException(SchedulerServiceException):
    """Exception raised when job execution fails"""
    
    def __init__(self, job_id: str, message: str, execution_id: Optional[str] = None):
        self.job_id = job_id
        self.execution_id = execution_id
        details = {"job_id": job_id}
        if execution_id:
            details["execution_id"] = execution_id
        super().__init__(message, "JOB_EXECUTION_FAILED", details)


class RepositoryException(SchedulerServiceException):
    """Exception raised when repository operations fail"""
    
    def __init__(self, operation: str, message: str, entity: Optional[str] = None):
        self.operation = operation
        self.entity = entity
        details = {"operation": operation}
        if entity:
            details["entity"] = entity
        super().__init__(message, "REPOSITORY_ERROR", details)


class ValidationException(SchedulerServiceException):
    """Exception raised when validation fails"""
    
    def __init__(self, field: str, message: str, value: Optional[Any] = None):
        self.field = field
        self.value = value
        details = {"field": field}
        if value is not None:
            details["value"] = str(value)
        super().__init__(message, "VALIDATION_ERROR", details)


class ServiceUnavailableException(SchedulerServiceException):
    """Exception raised when a service is unavailable"""
    
    def __init__(self, service_name: str, message: Optional[str] = None):
        self.service_name = service_name
        message = message or f"Service '{service_name}' is currently unavailable"
        super().__init__(message, "SERVICE_UNAVAILABLE", {"service": service_name})