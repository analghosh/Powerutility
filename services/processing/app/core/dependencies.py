"""
Dependency Injection for Processing Service
FIXED: Singleton pattern to prevent MongoDB connection leaks
"""

from libs.audit_queue.unified_queue_service import UnifiedQueueService
from libs.audit_queue.simple_logger import AuditLogger
from .config import settings
import logging

logger = logging.getLogger(__name__)

# Global singleton instances to prevent connection leaks
_unified_queue_service = None
_audit_logger = None

def get_unified_queue_service() -> UnifiedQueueService:
    """Get or create singleton UnifiedQueueService instance"""
    global _unified_queue_service
    if _unified_queue_service is None:
        try:
            _unified_queue_service = UnifiedQueueService(
                mongodb_uri=settings.MONGODB_URI,
                audit_db_name=settings.AUDIT_DB_NAME,
                audit_collection_name=settings.AUDIT_COLLECTION_NAME,
                queue_collection_name=settings.QUEUE_COLLECTION_NAME
            )
            logger.info("✅ Singleton UnifiedQueueService created")
        except Exception as e:
            logger.error(f"❌ Failed to create UnifiedQueueService: {e}")
            raise
    return _unified_queue_service

def get_audit_logger() -> AuditLogger:
    """Get or create singleton AuditLogger instance - PREVENTS CONNECTION LEAKS"""
    global _audit_logger
    if _audit_logger is None:
        try:
            unified_queue_service = get_unified_queue_service()
            _audit_logger = AuditLogger(unified_queue_service)
            logger.info("✅ Singleton AuditLogger created (RabbitMQ + Fallback)")
        except Exception as e:
            logger.error(f"❌ Failed to create AuditLogger: {e}")
            raise
    return _audit_logger

def cleanup_audit_resources():
    """Cleanup audit logging resources on shutdown"""
    global _unified_queue_service, _audit_logger
    try:
        if _unified_queue_service:
            # Shutdown the queue service
            if hasattr(_unified_queue_service, 'queue') and hasattr(_unified_queue_service.queue, 'shutdown'):
                _unified_queue_service.queue.shutdown()
            logger.info("🔒 UnifiedQueueService shutdown")
        
        _unified_queue_service = None
        _audit_logger = None
        logger.info("🔒 Audit resources cleanup completed")
        
    except Exception as e:
        logger.error(f"❌ Error during audit cleanup: {e}")
