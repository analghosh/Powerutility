"""
Simplified Audit Logging Interface

A clean, secure, and industry-standard audit logging system that follows
clean architecture principles and protects PII data.

This module provides a unified interface for all audit logging needs with
built-in PII protection and standardized logging patterns.
"""

from fastapi import Request, HTTPException
import logging
import re
from typing import Dict, Any, Optional, Union
from datetime import datetime
from enum import Enum
from dataclasses import dataclass

# Import timezone utilities
from .timezone_utils import now_ist, format_ist_timestamp

# Import the existing infrastructure
from libs.audit_queue.unified_queue_service import UnifiedQueueService
from libs.audit_queue.audit_models import Actor, Trace

# Configure module logger
_logger = logging.getLogger(__name__)


class AuditLogLevel(str, Enum):
    """
    Audit log severity levels following industry standards.
    
    Attributes:
        LOW: Routine operations, user activities
        MEDIUM: System operations, business logic
        HIGH: Errors, failures, warnings
        CRITICAL: Security events, system failures
    """
    LOW = "Low"
    MEDIUM = "Medium" 
    HIGH = "High"
    CRITICAL = "Critical"


@dataclass(frozen=True)
class PIIPatterns:
    """
    PII detection patterns for data sanitization.
    
    These patterns help identify and mask sensitive information
    before logging to ensure compliance with privacy regulations.
    """
    EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
    PHONE_PATTERN = re.compile(r'\b(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b')
    SSN_PATTERN = re.compile(r'\b\d{3}-?\d{2}-?\d{4}\b')
    CREDIT_CARD_PATTERN = re.compile(r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b')
    IP_ADDRESS_PATTERN = re.compile(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b')


class PIISanitizer:
    """
    Utility class for sanitizing PII data in log messages and data.
    
    This class implements industry-standard PII protection patterns
    to ensure sensitive data is not logged in plain text.
    """
    
    @staticmethod
    def _extract_bearer_token(request: Request) -> str:
        """Extract and validate bearer token with security checks"""
        auth = request.headers.get("Authorization", "")

        if not auth:
            raise HTTPException(status_code=401, detail="Authorization header required")

        if not auth.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Invalid authorization format")

        token = auth.split(" ", 1)[1]

        # Basic token format validation
        if len(token) < 10:  # Minimum reasonable token length
            raise HTTPException(status_code=401, detail="Invalid token format")

        return token

    # Alias for backward compatibility
    _bearer = _extract_bearer_token
    
    @staticmethod
    def sanitize_string(text: str, mask_char: str = "*") -> str:
        """
        Sanitize a string by masking PII patterns.
        
        Args:
            text: Input string to sanitize
            mask_char: Character to use for masking
            
        Returns:
            Sanitized string with PII masked
        """
        if not isinstance(text, str):
            return text
            
        # Mask email addresses
        text = PIIPatterns.EMAIL_PATTERN.sub(
            lambda m: f"{m.group().split('@')[0][:2]}{mask_char * 3}@{mask_char * 5}.com", 
            text
        )
        
        # Mask phone numbers
        text = PIIPatterns.PHONE_PATTERN.sub(f"{mask_char * 3}-{mask_char * 3}-{mask_char * 4}", text)
        
        # Mask SSN
        text = PIIPatterns.SSN_PATTERN.sub(f"{mask_char * 3}-{mask_char * 2}-{mask_char * 4}", text)
        
        # Mask credit cards
        text = PIIPatterns.CREDIT_CARD_PATTERN.sub(f"{mask_char * 4}-{mask_char * 4}-{mask_char * 4}-{mask_char * 4}", text)
        
        return text
    
    @staticmethod
    def sanitize_data(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize dictionary data by masking PII in values.
        
        Args:
            data: Dictionary to sanitize
            
        Returns:
            Sanitized dictionary with PII masked
        """
        if not isinstance(data, dict):
            return data
            
        sanitized = {}
        pii_fields = {
            'email', 'email_address', 'user_email', 'contact_email',
            'phone', 'phone_number', 'mobile', 'telephone',
            'ssn', 'social_security_number', 'tax_id',
            'credit_card', 'card_number', 'cc_number',
            'password', 'passwd', 'secret', 'token', 'api_key'
        }
        
        for key, value in data.items():
            key_lower = key.lower()
            
            # Mask known PII fields
            if any(pii_field in key_lower for pii_field in pii_fields):
                sanitized[key] = "***MASKED***"
            elif isinstance(value, str):
                sanitized[key] = PIISanitizer.sanitize_string(value)
            elif isinstance(value, dict):
                sanitized[key] = PIISanitizer.sanitize_data(value)
            elif isinstance(value, list):
                sanitized[key] = [
                    PIISanitizer.sanitize_string(item) if isinstance(item, str)
                    else PIISanitizer.sanitize_data(item) if isinstance(item, dict)
                    else item
                    for item in value
                ]
            else:
                sanitized[key] = value
                
        return sanitized


class AuditLogger:
    """
    Enterprise-grade audit logger with PII protection and clean architecture.
    """
    
    def __init__(self, unified_queue_service: UnifiedQueueService, enable_pii_protection: bool = True):
        """
        Initialize the audit logger.
        """
        self.unified_queue_service = unified_queue_service
        self._pii_protection_enabled = enable_pii_protection
        self._sanitizer = PIISanitizer()
    
    def _sanitize_if_enabled(self, message: str, data: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        """
        Sanitize message and data if PII protection is enabled.
        """
        if not self._pii_protection_enabled:
            return message, data
            
        sanitized_message = self._sanitizer.sanitize_string(message)
        sanitized_data = self._sanitizer.sanitize_data(data)
        
        return sanitized_message, sanitized_data
    
    def _create_base_metadata(self, **additional_data) -> Dict[str, Any]:
        """
        Create base metadata for all log entries.
        """
        base_metadata = {
            "timestamp": format_ist_timestamp(),
            "logger_version": "2.0.0",
            "pii_protected": self._pii_protection_enabled,
            "timezone": "IST",
            **additional_data
        }
        
        return base_metadata
    
    async def log_user_action(
        self,
        service: str,
        user_id: str,
        action: str,
        message: str,
        session_id: Optional[str] = None,
        success: bool = True,
        level: AuditLogLevel = AuditLogLevel.MEDIUM,
        **additional_data
    ) -> str:
        """
        Log user-initiated actions.
        """
        try:
            log_data = self._create_base_metadata(
                action_type=action,
                user_id=user_id,
                session_id=session_id,
                **additional_data
            )
            
            sanitized_message, sanitized_data = self._sanitize_if_enabled(message, log_data)
            actor = Actor(id=user_id, type="user")
            trace = Trace(sessionId=session_id, userId=user_id) if session_id else None
            
            return await self.unified_queue_service.create_and_log_audit(
                service=service,
                log_type="api",
                message=sanitized_message,
                priority=level.value,
                status="success" if success else "failed",
                actor=actor,
                trace=trace,
                data=sanitized_data
            )
            
        except Exception as e:
            _logger.error(f"Failed to log user action: {e}", exc_info=True)
            return ""
    
    async def log_system_operation(
        self,
        service: str,
        operation: str,
        message: str,
        correlation_id: Optional[str] = None,
        success: bool = True,
        level: AuditLogLevel = AuditLogLevel.MEDIUM,
        **additional_data
    ) -> str:
        """
        Log internal system operations.
        """
        try:
            log_data = self._create_base_metadata(
                operation_type=operation,
                correlation_id=correlation_id,
                **additional_data
            )
            
            sanitized_message, sanitized_data = self._sanitize_if_enabled(message, log_data)
            trace = Trace(jobId=correlation_id, requestId=correlation_id) if correlation_id else None
            
            return await self.unified_queue_service.create_and_log_audit(
                service=service,
                log_type="app",
                message=sanitized_message,
                priority=level.value,
                status="success" if success else "failed",
                trace=trace,
                meta_kwargs={"correlationId": correlation_id} if correlation_id else {},
                data=sanitized_data
            )
            
        except Exception as e:
            _logger.error(f"Failed to log system operation: {e}", exc_info=True)
            return ""
    
    async def log_scheduled_job(
        self,
        service: str,
        job_id: str,
        status: str,
        message: str,
        job_name: Optional[str] = None,
        duration_ms: Optional[int] = None,
        level: AuditLogLevel = AuditLogLevel.MEDIUM,
        **additional_data
    ) -> str:
        """
        Log scheduled job events.
        """
        try:
            additional_data_clean = {k: v for k, v in additional_data.items() if k != 'job_type'}
            log_data = self._create_base_metadata(
                job_type="scheduled",
                job_id=job_id,
                job_name=job_name,
                job_status=status,
                execution_duration_ms=duration_ms,
                **additional_data_clean
            )
            
            sanitized_message, sanitized_data = self._sanitize_if_enabled(message, log_data)
            trace = Trace(jobId=job_id, requestId=job_id)
            
            return await self.unified_queue_service.create_and_log_audit(
                service=service,
                log_type="job",
                message=sanitized_message,
                priority=level.value,
                status=status,
                trace=trace,
                durationMs=duration_ms,
                meta_kwargs={"correlationId": job_id},
                data=sanitized_data
            )
            
        except Exception as e:
            _logger.error(f"Failed to log scheduled job: {e}", exc_info=True)
            return ""
    
    async def log_error_event(
        self,
        service: str,
        error_name: str,
        error_message: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        level: AuditLogLevel = AuditLogLevel.MEDIUM,
        **error_context
    ) -> str:
        """
        Log error events.
        """
        try:
            safe_error_context = {k: v for k, v in error_context.items() if k != 'error_type'}
            log_data = self._create_base_metadata(
                error_category="application_error",
                error_type=error_name,
                user_id=user_id,
                session_id=session_id,
                correlation_id=correlation_id,
                **safe_error_context
            )
            
            sanitized_message, sanitized_data = self._sanitize_if_enabled(error_message, log_data)
            
            trace_data = {}
            if correlation_id: trace_data["jobId"] = correlation_id; trace_data["requestId"] = correlation_id
            if session_id: trace_data["sessionId"] = session_id
            if user_id: trace_data["userId"] = user_id
            trace = Trace(**trace_data) if trace_data else None
            
            return await self.unified_queue_service.create_and_log_error(
                service=service,
                error_name=error_name,
                error_message=sanitized_message,
                priority=level.value,
                trace=trace,
                data=sanitized_data
            )
            
        except Exception as e:
            _logger.error(f"Failed to log error event: {e}", exc_info=True)
            return ""
    
    async def log_security_event(
        self,
        service: str,
        event_type: str,
        message: str,
        user_id: Optional[str] = None,
        client_ip: Optional[str] = None,
        success: bool = True,
        level: AuditLogLevel = AuditLogLevel.MEDIUM,
        **additional_data
    ) -> str:
        """
        Log security-related events.
        """
        try:
            log_data = self._create_base_metadata(
                event_category="security",
                event_type=event_type,
                user_id=user_id,
                client_ip=client_ip,
                **additional_data
            )
            
            sanitized_message, sanitized_data = self._sanitize_if_enabled(message, log_data)
            actor = Actor(id=user_id, type="user", ip_address=client_ip) if user_id else None
            
            return await self.unified_queue_service.create_and_log_audit(
                service=service,
                log_type="security",
                message=sanitized_message,
                priority=level.value,
                status="success" if success else "failed",
                actor=actor,
                data=sanitized_data
            )
            
        except Exception as e:
            _logger.error(f"Failed to log security event: {e}", exc_info=True)
            return ""

    async def log_api_request(
        self,
        service: str,
        request: Request,
        action: str,
        message: str,
        success: bool = True,
        level: AuditLogLevel = AuditLogLevel.MEDIUM,
        **additional_data
    ) -> str:
        """
        Simplified helper to log an API request.
        """
        try:
            request_info = await self._extract_request_info(request)
            return await self.log_user_action(
                service=service,
                user_id=request_info.get("user_id") or "anonymous",
                action=action,
                message=message,
                session_id=request_info.get("session_id"),
                success=success,
                level=level,
                client_ip=request_info.get("ip_address"),
                user_agent=request_info.get("user_agent"),
                **additional_data
            )
        except Exception as e:
            _logger.error(f"Failed to log API request: {e}")
            return ""

    async def _extract_request_info(self, request: Request) -> Dict[str, Any]:
        """Helper to extract common info from FastAPI request object"""
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("user-agent", "unknown")
        
        user_id = getattr(request.state, "user_id", None)
        username = getattr(request.state, "username", None)
        
        return {
            "ip_address": client_ip,
            "user_agent": user_agent,
            "user_id": user_id,
            "username": username
        }
