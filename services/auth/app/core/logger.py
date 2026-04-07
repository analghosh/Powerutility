"""
Logger configuration for Auth Service
"""

import logging
import sys
import json
import traceback
from datetime import datetime
from typing import Optional, Dict, Any
from .config import settings


def get_auth_logger(name: str = "auth_service") -> logging.Logger:
    """Get configured logger for auth service"""
    
    logger = logging.getLogger(name)
    
    # Avoid adding handlers if they already exist
    if logger.handlers:
        return logger
    
    # Set log level
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logger.setLevel(log_level)
    
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    
    # Create formatter with enhanced information
    if settings.LOG_FORMAT.lower() == "json":
        formatter = logging.Formatter(
            '{"timestamp": "%(asctime)s", "level": "%(levelname)s", "service": "auth", "message": "%(message)s", "module": "%(name)s", "function": "%(funcName)s", "line": %(lineno)d, "thread": "%(threadName)s"}'
        )
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s'
        )
    
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # Create file handler for errors
    try:
        error_handler = logging.FileHandler("logs/auth_errors.log")
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        logger.addHandler(error_handler)
    except Exception:
        # If logs directory doesn't exist, skip file handler
        pass
    
    # Prevent propagation to root logger
    logger.propagate = False
    
    return logger


def log_auth_event(logger: logging.Logger, event_type: str, user_id: str, details: Optional[Dict[str, Any]] = None):
    """Log authentication-related events with structured data"""
    log_data = {
        "event_type": event_type,
        "user_id": user_id,
        "service": "auth",
        "timestamp": datetime.now().isoformat()
    }
    
    if details:
        log_data.update(details)
    
    logger.info(f"Auth event: {event_type} for user {user_id}", extra={"audit_data": log_data})


def log_error_with_context(logger: logging.Logger, error: Exception, context: Optional[Dict[str, Any]] = None):
    """Log errors with full context and stack trace"""
    error_data = {
        "error_type": type(error).__name__,
        "error_message": str(error),
        "stack_trace": traceback.format_exc(),
        "service": "auth",
        "timestamp": datetime.now().isoformat()
    }
    
    if context:
        error_data.update(context)
    
    logger.error(f"Error occurred: {type(error).__name__}: {str(error)}", extra={"error_data": error_data})


def log_api_request(logger: logging.Logger, method: str, endpoint: str, status_code: int, 
                   user_id: Optional[str] = None, duration_ms: Optional[float] = None,
                   request_data: Optional[Dict[str, Any]] = None):
    """Log API requests with performance and audit information"""
    log_data = {
        "method": method,
        "endpoint": endpoint,
        "status_code": status_code,
        "service": "auth",
        "timestamp": datetime.now().isoformat()
    }
    
    if user_id:
        log_data["user_id"] = user_id
    
    if duration_ms:
        log_data["duration_ms"] = duration_ms
    
    if request_data:
        # Sanitize sensitive data
        sanitized_data = sanitize_request_data(request_data)
        log_data["request_data"] = sanitized_data
    
    level = logging.ERROR if status_code >= 400 else logging.INFO
    logger.log(level, f"API {method} {endpoint} - {status_code}", extra={"api_data": log_data})


def sanitize_request_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize request data to remove sensitive information"""
    sensitive_fields = ["password", "token", "secret", "key", "authorization"]
    sanitized = data.copy()
    
    for field in sensitive_fields:
        if field in sanitized:
            if isinstance(sanitized[field], str):
                sanitized[field] = "***REDACTED***"
            else:
                sanitized[field] = "***REDACTED***"
    
    return sanitized


def log_database_operation(logger: logging.Logger, operation: str, collection: str, 
                          success: bool, duration_ms: Optional[float] = None,
                          error: Optional[Exception] = None):
    """Log database operations for auditing and debugging"""
    log_data = {
        "operation": operation,
        "collection": collection,
        "success": success,
        "service": "auth",
        "timestamp": datetime.now().isoformat()
    }
    
    if duration_ms:
        log_data["duration_ms"] = duration_ms
    
    if error:
        log_data["error"] = str(error)
    
    level = logging.ERROR if not success else logging.DEBUG
    logger.log(level, f"DB {operation} on {collection} - {'SUCCESS' if success else 'FAILED'}", 
               extra={"db_data": log_data})


def log_security_event(logger: logging.Logger, event_type: str, user_id: Optional[str] = None,
                      ip_address: Optional[str] = None, user_agent: Optional[str] = None,
                      details: Optional[Dict[str, Any]] = None):
    """Log security-related events for monitoring"""
    security_data = {
        "event_type": event_type,
        "service": "auth",
        "timestamp": datetime.now().isoformat()
    }
    
    if user_id:
        security_data["user_id"] = user_id
    
    if ip_address:
        security_data["ip_address"] = ip_address
    
    if user_agent:
        security_data["user_agent"] = user_agent
    
    if details:
        security_data.update(details)
    
    logger.warning(f"Security event: {event_type}", extra={"security_data": security_data})


# Create main logger instance
auth_logger = get_auth_logger("auth_service") 