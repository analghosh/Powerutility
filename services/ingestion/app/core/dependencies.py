"""
Dependency Injection for Ingestion Service
"""

from libs.audit_queue.unified_queue_service import UnifiedQueueService
from libs.audit_queue.simple_logger import AuditLogger
from .config import settings
from ..application.services import FileUploadApplicationService
from ..repository.upload_tracking_repository import UploadTrackingRepository
from ..services.provider_service import ProviderManagementService
from ..services.file_storage_service import FileStorageService

def get_unified_queue_service() -> UnifiedQueueService:
    """Create and return a UnifiedQueueService instance"""
    return UnifiedQueueService(
        mongodb_uri=settings.AUDIT_DB_URI,
        audit_db_name=settings.AUDIT_DB_NAME,
        audit_collection_name=settings.AUDIT_COLLECTION_NAME,
        queue_collection_name=settings.QUEUE_COLLECTION_NAME
    )

def get_audit_logger() -> AuditLogger:
    """Create and return an AuditLogger instance"""
    return AuditLogger(get_unified_queue_service())

def get_file_upload_service() -> FileUploadApplicationService:
    """Create and return a FileUploadApplicationService instance"""
    upload_repository = UploadTrackingRepository(
        mongodb_uri=settings.MONGODB_URI,
        database_name=settings.MONGODB_DATABASE
    )
    provider_repository = ProviderManagementService()
    file_storage = FileStorageService(upload_dir=settings.UPLOAD_DIR)
    
    return FileUploadApplicationService(
        upload_repository=upload_repository,
        provider_repository=provider_repository,
        file_storage=file_storage
    )

def get_upload_raw_data_processor() -> 'UploadRawDataProcessor':
    """Create and return an UploadRawDataProcessor instance"""
    # Import inside function to avoid circular imports if any
    from ..processors.upload_raw_data_processor import UploadRawDataProcessor
    return UploadRawDataProcessor(get_audit_logger())
