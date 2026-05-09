"""
Job Factory
Creates job-related instances (repositories) for services
Note: Job seeders are now service-specific and imported directly from each service
"""

from .job_repositories import JobDataRepository, JobDetailRepository, JobExecutionLogRepository


def create_job_data_repository(audit_db_uri: str, audit_db_name: str, job_data_collection: str) -> JobDataRepository:
    """
    Create a JobDataRepository instance
    
    Args:
        audit_db_uri: Audit database URI
        audit_db_name: Audit database name
        job_data_collection: Job data collection name
        
    Returns:
        JobDataRepository instance
    """
    return JobDataRepository(audit_db_uri, audit_db_name, job_data_collection)


def create_job_detail_repository(audit_db_uri: str, audit_db_name: str, job_detail_collection: str) -> JobDetailRepository:
    """
    Create a JobDetailRepository instance
    
    Args:
        audit_db_uri: Audit database URI
        audit_db_name: Audit database name
        job_detail_collection: Job detail collection name
        
    Returns:
        JobDetailRepository instance
    """
    return JobDetailRepository(audit_db_uri, audit_db_name, job_detail_collection)


def create_job_execution_log_repository(audit_db_uri: str, audit_db_name: str, collection_name: str = "job_execution_logs") -> JobExecutionLogRepository:
    """
    Create a JobExecutionLogRepository instance
    
    Args:
        audit_db_uri: Audit database URI
        audit_db_name: Audit database name
        collection_name: Collection name for execution logs
        
    Returns:
        JobExecutionLogRepository instance
    """
    return JobExecutionLogRepository(audit_db_uri, audit_db_name, collection_name)
