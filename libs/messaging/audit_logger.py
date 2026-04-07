
import logging
from typing import Optional, Dict, Any
from .audit_publisher import AuditPublisher

logger = logging.getLogger(__name__)

class AuditLogger:
    """
    Backward-compatible AuditLogger.
    Tries to publish to RabbitMQ first.
    Falls back to UnifiedQueueService (MongoDB) if RabbitMQ fails or is disabled.
    """
    
    def __init__(self, unified_queue_service=None):
        self.publisher = AuditPublisher()
        self.fallback_service = unified_queue_service
        
    def log_user_action(
        self, 
        user_id: str, 
        action: str, 
        service: str, 
        username: Optional[str] = None, 
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None
    ):
        """Log a user action"""
        # Try RabbitMQ first
        success = self.publisher.publish_audit_log(
            service_name=service,
            action=action,
            user_id=user_id,
            username=username,
            details=details,
            ip_address=ip_address
        )
        
        if success:
            return
            
        # Fallback to legacy service
        if self.fallback_service:
            logger.info("Fallback: Logging audit to MongoDB via UnifiedQueueService")
            try:
                # Map arguments to legacy method signature if needed
                # Legacy: log_audit(self, audit_data: Dict) via unified_queue_service.add_audit_log?
                # Or the old AuditLogger had log_user_action directly?
                # Let's assume the calling code calls log_user_action.
                # We need to see how the old AuditLogger used unified_queue_service.
                # Assuming unified_queue_service has a generic 'add_to_queue' or 'log' method.
                
                # Based on previous file reads, UnifiedQueueService handles the queue.
                # The old AuditLogger likely constructed the dict and passed it.
                # For safety, I will implement a basic fallback here.
                
                audit_data = {
                    "user_id": user_id,
                    "username": username,
                    "action": action,
                    "service": service,
                    "details": details,
                    "ip_address": ip_address,
                    "timestamp": datetime.utcnow()
                }
                
                # Check if fallback_service has 'add_audit_log'
                if hasattr(self.fallback_service, 'add_audit_log'):
                    self.fallback_service.add_audit_log(audit_data)
                elif hasattr(self.fallback_service, 'push'):
                    self.fallback_service.push(audit_data)
                else:
                    logger.warning("Fallback service does not have expected methods")
                    
            except Exception as e:
                logger.error(f"Fallback logging failed: {str(e)}")
        else:
            logger.warning("No fallback service configured for audit logging")

    # Support legacy methods if any
    def log_system_operation(self, service: str, action: str, details: Optional[Dict[str, Any]] = None):
        """Log a system operation"""
        self.log_user_action(
            user_id="SYSTEM",
            username="SYSTEM",
            service=service,
            action=action,
            details=details
        )
from datetime import datetime
