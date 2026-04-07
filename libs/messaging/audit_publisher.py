
import json
import logging
import pika
from datetime import datetime
from typing import Dict, Any, Optional
from .rabbitmq_manager import RabbitMQManager
from .rabbitmq_config import config
from .message_models import AuditLogMessage
from .circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)

class AuditPublisher:
    """Publisher for Audit events with Circuit Breaker support"""
    
    def __init__(self):
        self.manager = RabbitMQManager()
        self.circuit_breaker = CircuitBreaker()
        
    def publish_audit_log(
        self,
        service_name: str,
        action: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None
    ) -> bool:
        """
        Publish an audit log to RabbitMQ.
        
        Returns:
            bool: True if published successfully, False if failed or circuit open
        """
        if not config.USE_RABBITMQ_AUDIT:
            return False
            
        if not self.circuit_breaker.allow_request():
            logger.warning("Circuit breaker OPEN: Scheduling audit log for fallback")
            return False
            
        try:
            channel = self.manager.get_channel()
            if not channel:
                raise Exception("No RabbitMQ channel available")
                
            message = AuditLogMessage(
                service_name=service_name,
                action=action,
                user_id=user_id,
                username=username,
                details=details,
                ip_address=ip_address
            )
            
            channel.basic_publish(
                exchange=config.EXCHANGE_AUDIT,
                routing_key="audit.log",
                body=message.json(),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # Persistent
                    content_type='application/json'
                )
            )
            
            self.circuit_breaker.record_success()
            return True
            
        except Exception as e:
            logger.error(f"Failed to publish audit log: {str(e)}")
            self.circuit_breaker.record_failure()
            return False
