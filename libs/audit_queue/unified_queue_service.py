"""
Unified Queue Service for EPMS
Provides a consistent interface for audit logging across all services
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from libs.audit_queue.queue_manager import MongoAuditQueue
from libs.audit_queue.models import LogPriority
from libs.audit_queue.audit_models import AuditLogDoc, create_audit_log, create_error_log
from libs.audit_queue.audit_repository import AuditRepository

# Import timezone utilities
from libs.audit_queue.timezone_utils import now_ist, format_ist_timestamp

logger = logging.getLogger(__name__)


class UnifiedQueueService:
    """
    Unified service for audit logging across all EPMS services
    Provides a consistent interface and handles service-specific configurations
    """
    
    def __init__(self, mongodb_uri: str, audit_db_name: str, audit_collection_name: str, queue_collection_name: str):
        self.queue = MongoAuditQueue(
            mongodb_uri=mongodb_uri,
            audit_db_name=audit_db_name,
            audit_collection_name=audit_collection_name,
            queue_collection_name=queue_collection_name
        )
        self.audit_repository = AuditRepository(
            mongodb_uri=mongodb_uri,
            audit_db_name=audit_db_name,
            audit_collection_name=audit_collection_name
        )
        logger.info("UnifiedQueueService initialized")
    
    async def log_audit_event(self, 
                       event_type: str,
                       service_name: str,
                       user_id: Optional[str] = None,
                       username: Optional[str] = None,
                       ip_address: Optional[str] = None,
                       user_agent: Optional[str] = None,
                       details: Optional[Dict[str, Any]] = None,
                       success: bool = True,
                       error_message: Optional[str] = None,
                       priority: LogPriority = LogPriority.MEDIUM,
                       destination: str = "mongo") -> str:
        """
        Log an audit event using the unified queue service
        
        Args:
            event_type: Type of event (login, logout, data_upload, etc.)
            service_name: Name of the service logging the event
            user_id: ID of the user performing the action (if authenticated)
            username: Username of the user (if authenticated)
            ip_address: IP address of the request
            user_agent: User agent string
            details: Additional event details
            success: Whether the action was successful
            error_message: Error message if action failed
            priority: Log priority level (affects TTL expiry)
            destination: "mongo" or "file"
            
        Returns:
            str: Job ID of the queued audit log entry
        """
        try:
            audit_data = {
                "timestamp": format_ist_timestamp(),
                "record_type": "audit",  # Distinguish from messages
                "event_type": event_type,
                "service": service_name,
                "user_id": user_id,
                "username": username,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "success": success,
                "error_message": error_message,
                "details": details or {}
            }
            
            # Remove None values
            audit_data = {k: v for k, v in audit_data.items() if v is not None}
            
            # Add to queue for asynchronous processing
            job_id = await self.queue.add_to_queue(
                log_data=audit_data,
                source_service=service_name,
                destination=destination,
                job_type="audit",
                priority=priority
            )
            
            logger.debug("Audit event queued with job_id: %s for service: %s", job_id, service_name)
            return job_id
            
        except Exception as e:
            logger.error("Error queuing audit event for service %s: %s", service_name, e)
            # Fallback to standard logging if queue fails
            logger.warning("Audit event fallback log - %s: %s", event_type, audit_data)
            return ""
    
    async def log_message(self,
                   message_type: str,
                   service_name: str,
                   content: str,
                   recipient: Optional[str] = None,
                   sender: Optional[str] = None,
                   priority: LogPriority = LogPriority.MEDIUM,
                   category: Optional[str] = None,
                   metadata: Optional[Dict[str, Any]] = None,
                   destination: str = "mongo") -> str:
        """
        Log a message using the unified queue service
        
        Args:
            message_type: Type of message (notification, alert, system, user, etc.)
            service_name: Name of the service logging the message
            content: Message content/text
            recipient: Recipient of the message (user_id, email, etc.)
            sender: Sender of the message
            priority: Message priority level
            category: Message category for organization
            metadata: Additional message metadata
            destination: "mongo" or "file"
            
        Returns:
            str: Job ID of the queued message
        """
        try:
            message_data = {
                "timestamp": format_ist_timestamp(),
                "record_type": "message",  # Distinguish from audit events
                "message_type": message_type,
                "service": service_name,
                "content": content,
                "recipient": recipient,
                "sender": sender,
                "priority": priority,
                "category": category,
                "metadata": metadata or {},
                "status": "sent"  # Default status
            }
            
            # Remove None values
            message_data = {k: v for k, v in message_data.items() if v is not None}
            
            # Add to queue for asynchronous processing
            job_id = await self.queue.add_to_queue(
                log_data=message_data,
                source_service=service_name,
                destination=destination,
                job_type="message",
                priority=priority
            )
            
            logger.debug("Message queued with job_id: %s for service: %s", job_id, service_name)
            return job_id
            
        except Exception as e:
            logger.error("Error queuing message for service %s: %s", service_name, e)
            # Fallback to standard logging if queue fails
            logger.warning("Message fallback log - %s: %s", message_type, message_data)
            return ""
    
    async def log_structured_audit(self, audit_log: AuditLogDoc, destination: str = "mongo") -> str:
        """
        Log a structured audit document using the audit queue
        
        Args:
            audit_log: Structured audit log document
            destination: "mongo" or "file"
            
        Returns:
            str: Job ID of the queued audit log entry
        """
        try:
            # Convert to audit queue format
            audit_data = audit_log.to_audit_queue_format()
            
            # Map priority to LogPriority
            priority_map = {
                "critical": LogPriority.CRITICAL,
                "high": LogPriority.HIGH,
                "medium": LogPriority.MEDIUM,
                "low": LogPriority.LOW
            }
            
            priority = priority_map.get(audit_log.meta.priority.lower(), LogPriority.MEDIUM)
            
            # Add to queue for asynchronous processing
            job_id = await self.queue.add_to_queue(
                log_data=audit_data,
                source_service=audit_log.meta.service,
                destination=destination,
                job_type="audit",
                priority=priority
            )
            
            logger.debug("Structured audit log queued with job_id: %s for service: %s", 
                        job_id, audit_log.meta.service)
            return job_id
            
        except Exception as e:
            logger.error("Error queuing structured audit log for service %s: %s", 
                        audit_log.meta.service, e)
            # Fallback to standard logging if queue fails
            logger.warning("Structured audit fallback log - %s: %s", 
                          audit_log.meta.type, audit_log.message)
            return ""
    
    async def log_structured_audit_direct(self, audit_log: AuditLogDoc) -> str:
        """
        Log a structured audit document directly to MongoDB (bypass queue)
        
        Args:
            audit_log: Structured audit log document
            
        Returns:
            str: Document ID of the saved audit log
        """
        try:
            return await self.audit_repository.save_audit_log(audit_log)
        except Exception as e:
            logger.error("Error saving structured audit log directly: %s", e)
            raise
    
    async def create_and_log_audit(
        self,
        service: str,
        log_type: str,
        message: str,
        priority: str = "Medium",
        **kwargs
    ) -> str:
        """
        Create and log an audit entry in one step
        
        Args:
            service: Service name
            log_type: Type of log (app, api, job, etc.)
            message: Log message
            priority: Priority level (Critical, High, Medium, Low)
            **kwargs: Additional audit log parameters
            
        Returns:
            str: Job ID of the queued audit log entry
        """
        try:
            audit_log = create_audit_log(
                service=service,
                log_type=log_type,  # type: ignore
                message=message,
                priority=priority,  # type: ignore
                **kwargs
            )
            
            return self.log_structured_audit(audit_log)
            
        except Exception as e:
            logger.error("Error creating and logging audit: %s", e)
            return ""
    
    async def create_and_log_error(
        self,
        service: str,
        error_name: str,
        error_message: str,
        error_code: Optional[str] = None,
        stack_trace: Optional[str] = None,
        priority: str = "High",
        **kwargs
    ) -> str:
        """
        Create and log an error audit entry
        
        Args:
            service: Service name
            error_name: Error class/type name
            error_message: Error message
            error_code: Error code (optional)
            stack_trace: Stack trace (optional)
            priority: Priority level
            **kwargs: Additional parameters
            
        Returns:
            str: Job ID of the queued audit log entry
        """
        try:
            from libs.audit_queue.audit_models import ErrorInfo
            
            error_info = ErrorInfo(
                name=error_name,
                code=error_code,
                message=error_message,
                stack=stack_trace,
                severity="error"
            )
            
            audit_log = create_error_log(
                service=service,
                error=error_info,
                message=error_message,
                priority=priority,  # type: ignore
                **kwargs
            )
            
            return self.log_structured_audit(audit_log)
            
        except Exception as e:
            logger.error("Error creating and logging error audit: %s", e)
            return ""
    
    async def get_audit_logs_by_service(
        self,
        service: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get audit logs for a specific service"""
        try:
            return await self.audit_repository.find_by_service(
                service=service,
                start_time=start_time,
                end_time=end_time,
                limit=limit
            )
        except Exception as e:
            logger.error("Error getting audit logs by service: %s", e)
            return []
    
    async def get_audit_logs_by_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        """Get all audit logs for a trace ID"""
        try:
            return await self.audit_repository.find_by_trace_id(trace_id)
        except Exception as e:
            logger.error("Error getting audit logs by trace: %s", e)
            return []
    
    async def get_audit_logs_by_job(self, job_id: str) -> List[Dict[str, Any]]:
        """Get all audit logs for a job ID"""
        try:
            return await self.audit_repository.find_by_job_id(job_id)
        except Exception as e:
            logger.error("Error getting audit logs by job: %s", e)
            return []
    
    async def search_audit_logs(
        self,
        search_text: str,
        service: Optional[str] = None,
        log_type: Optional[str] = None,
        start_time: Optional[datetime] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Search audit logs using full-text search"""
        try:
            return await self.audit_repository.search_logs(
                search_text=search_text,
                service=service,
                log_type=log_type if log_type else None,  # type: ignore
                start_time=start_time,
                limit=limit
            )
        except Exception as e:
            logger.error("Error searching audit logs: %s", e)
            return []
    
    async def get_audit_statistics(
        self,
        service: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Get audit log statistics"""
        try:
            return await self.audit_repository.get_log_statistics(
                service=service,
                start_time=start_time,
                end_time=end_time
            )
        except Exception as e:
            logger.error("Error getting audit statistics: %s", e)
            return {}
    
    async def log_data_operation(self,
                          operation_type: str,
                          service_name: str,
                          data_type: str,
                          record_count: Optional[int] = None,
                          file_name: Optional[str] = None,
                          provider_id: Optional[str] = None,
                          success: bool = True,
                          error_message: Optional[str] = None,
                          details: Optional[Dict[str, Any]] = None) -> str:
        """
        Log data operation events (upload, processing, etc.)
        
        Args:
            operation_type: Type of operation (upload, process, validate, etc.)
            service_name: Name of the service performing the operation
            data_type: Type of data being processed
            record_count: Number of records processed
            file_name: Name of the file being processed
            provider_id: ID of the data provider
            success: Whether the operation was successful
            error_message: Error message if operation failed
            details: Additional operation details
            
        Returns:
            str: Job ID of the queued audit log entry
        """
        operation_details = {
            "data_type": data_type,
            "record_count": record_count,
            "file_name": file_name,
            "provider_id": provider_id
        }
        
        # Merge with additional details
        if details:
            operation_details.update(details)
        
        return await self.log_audit_event(
            event_type=f"data_{operation_type}",
            service_name=service_name,
            details=operation_details,
            success=success,
            error_message=error_message
        )
    
    async def log_system_event(self,
                        event_type: str,
                        service_name: str,
                        details: Optional[Dict[str, Any]] = None,
                        success: bool = True,
                        error_message: Optional[str] = None) -> str:
        """
        Log system events (startup, shutdown, errors, etc.)
        
        Args:
            event_type: Type of system event
            service_name: Name of the service
            details: Additional event details
            success: Whether the event was successful
            error_message: Error message if event failed
            
        Returns:
            str: Job ID of the queued audit log entry
        """
        return await self.log_audit_event(
            event_type=f"system_{event_type}",
            service_name=service_name,
            details=details,
            success=success,
            error_message=error_message
        )
    
    def get_queue_status(self) -> Dict[str, Any]:
        """
        Get current queue status and metrics
        
        Returns:
            Dict containing queue statistics and metrics
        """
        return self.queue.get_queue_stats()
    
    def health_check(self) -> Dict[str, Any]:
        """
        Perform health check on the queue system
        
        Returns:
            Dict containing health status information
        """
        try:
            stats = self.get_queue_status()
            
            # Check if workers are alive
            workers_alive = stats.get("workers_alive", 0)
            worker_count = stats.get("worker_count", 0)
            
            health_status = {
                "status": "healthy" if workers_alive > 0 else "unhealthy",
                "workers_alive": workers_alive,
                "worker_count": worker_count,
                "queue_size": stats.get("queue_size", 0),
                "metrics": stats.get("metrics", {}),
                "timestamp": format_ist_timestamp()
            }
            
            return health_status
            
        except Exception as e:
            logger.error("Health check failed: %s", e)
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": format_ist_timestamp()
            }


# Export the main service
__all__ = [
    'UnifiedQueueService',
]
