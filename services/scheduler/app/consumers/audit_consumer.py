
import logging
import json
from typing import Any
import pika

from libs.messaging.base_consumer import BaseConsumer
from libs.messaging.rabbitmq_config import config
from libs.messaging.message_models import AuditLogMessage
from libs.audit_queue.unified_queue_service import UnifiedQueueService
from libs.audit_queue.models import LogPriority
from services.scheduler.app.core.config import settings

logger = logging.getLogger(__name__)

class AuditConsumer(BaseConsumer):
    """
    Consumer for audit log messages.
    Persists audit logs to MongoDB via UnifiedQueueService.
    """
    
    def __init__(self):
        super().__init__(
            queue_name=config.QUEUE_AUDIT,
            exchange_name=config.EXCHANGE_AUDIT,
            routing_key="audit.#"
        )
        
        # Initialize UnifiedQueueService for persistence
        try:
            self.unified_queue_service = UnifiedQueueService(
                mongodb_uri=settings.MONGODB_URI,
                audit_db_name=settings.AUDIT_DB_NAME,
                audit_collection_name=settings.AUDIT_COLLECTION_NAME,
                queue_collection_name=settings.QUEUE_COLLECTION_NAME
            )
        except Exception as e:
            logger.error(f"Failed to initialize UnifiedQueueService in AuditConsumer: {e}")
            self.unified_queue_service = None

    def process_message(self, message: Any, properties: pika.BasicProperties) -> bool:
        """
        Process audit message.
        """
        if not self.unified_queue_service:
            logger.error("UnifiedQueueService not initialized, cannot process audit message")
            return False

        try:
            # Ensure we have a valid AuditLogMessage object
            if isinstance(message, dict):
                audit_msg = AuditLogMessage(**message)
            elif isinstance(message, str):
                audit_msg = AuditLogMessage(**json.loads(message))
            else:
                # Try to cast whatever it is
                audit_msg = AuditLogMessage(**dict(message))

            # AuditLogMessage fields: service_name, action, user_id, username, details, ip_address, status
            
            # Map 'status' to success boolean if possible, though log_audit_event treats status as success boolean.
            # Usually strict audit logs might require specialized mapping, but let's assume 'status'="success" -> True
            is_success = True
            error_msg = None
            if hasattr(audit_msg, 'status') and audit_msg.status:
                if str(audit_msg.status).lower() in ['failed', 'error', 'failure']:
                    is_success = False
                    
            self.unified_queue_service.log_audit_event(
                event_type=audit_msg.action,
                service_name=audit_msg.service_name,
                user_id=audit_msg.user_id,
                username=audit_msg.username,
                ip_address=audit_msg.ip_address,
                details=audit_msg.details,
                success=is_success,
                destination="mongo", # Explicitly save to Mongo
                priority=LogPriority.MEDIUM # Default priority
            )
            
            logger.debug(f"Processed audit message: {audit_msg.action}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to process audit message: {e}")
            return False
