"""
Dependency Injection for Ingestion Service
"""

from __future__ import annotations

import logging

from libs.audit_queue.unified_queue_service import UnifiedQueueService
from libs.audit_queue.simple_logger import AuditLogger
from .config import settings
from ..application.services import FileUploadApplicationService
from ..repository.upload_tracking_repository import UploadTrackingRepository
from ..services.provider_service import ProviderManagementService
from ..services.file_storage_service import FileStorageService

logger = logging.getLogger(__name__)

_unified_queue_service: UnifiedQueueService | None = None
_audit_logger: AuditLogger | None = None

def get_unified_queue_service() -> UnifiedQueueService:
    """Get or create the shared UnifiedQueueService instance."""
    global _unified_queue_service
    if _unified_queue_service is None:
        _unified_queue_service = UnifiedQueueService(
            mongodb_uri=settings.AUDIT_DB_URI,
            audit_db_name=settings.AUDIT_DB_NAME,
            audit_collection_name=settings.AUDIT_COLLECTION_NAME,
            queue_collection_name=settings.QUEUE_COLLECTION_NAME
        )
        logger.info("Singleton UnifiedQueueService created")
    return _unified_queue_service

def get_audit_logger() -> AuditLogger:
    """Get or create the shared AuditLogger instance."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger(get_unified_queue_service())
        logger.info("Singleton AuditLogger created")
    return _audit_logger

def cleanup_audit_resources() -> None:
    """Cleanup shared audit logging resources on shutdown."""
    global _unified_queue_service, _audit_logger
    try:
        if _unified_queue_service:
            queue = getattr(_unified_queue_service, "queue", None)
            if queue and hasattr(queue, "shutdown"):
                queue.shutdown()
        _audit_logger = None
        _unified_queue_service = None
        logger.info("Audit resources cleanup completed")
    except Exception as exc:
        logger.error("Error during audit cleanup: %s", exc)

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

def get_upload_raw_data_processor():
    """Create and return an UploadRawDataProcessor instance"""
    # Import inside function to avoid circular imports if any
    from ..processors.upload_raw_data_processor import UploadRawDataProcessor
    return UploadRawDataProcessor(get_audit_logger())
