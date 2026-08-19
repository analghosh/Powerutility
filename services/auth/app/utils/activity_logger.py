"""
Activity Logging Utility for Auth Service
Provides consistent activity logging across all auth endpoints
"""

import logging
from typing import Dict, Any, Optional
from fastapi import Request
from libs.audit_queue.simple_logger import AuditLogger, AuditLogLevel

# Import timezone utilities
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
from libs.audit_queue.timezone_utils import format_ist_timestamp
from libs.middleware.utils import get_client_ip_or_none

logger = logging.getLogger(__name__)


def _extract_request_info(request: Request) -> Dict[str, Any]:
    """Extract request information for logging"""
    try:
        # Get client IP address
        ip_address = get_client_ip_or_none(request)
        
        # Get user agent
        user_agent = request.headers.get("user-agent")
        
        # Get session ID from JWT if available
        session_id = None
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix
            try:
                from ..services.jwt_service import jwt_service
                session_id = jwt_service.get_session_id(token)
            except Exception:
                pass  # Ignore token parsing errors for logging
        
        return {
            "ip_address": ip_address,
            "user_agent": user_agent,
            "session_id": session_id,
            "endpoint": f"{request.method} {request.url.path}",
            "timestamp": format_ist_timestamp()
        }
    except Exception as e:
        logger.warning("Failed to extract request info: %s", e)
        return {
            "ip_address": None,
            "user_agent": None,
            "session_id": None,
            "endpoint": f"{request.method} {request.url.path}",
            "timestamp": format_ist_timestamp()
        }


async def log_auth_activity(
    audit_logger: AuditLogger,
    activity_type: str,
    request: Request,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    success: bool = True,
    details: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    priority: str = "Medium"
) -> None:
    """
    Log authentication activity with consistent format
    
    Args:
        audit_logger: An instance of the AuditLogger
        activity_type: Type of activity (login, logout, password_change, etc.)
        request: FastAPI request object
        user_id: User ID if available
        username: Username if available
        success: Whether the activity was successful
        details: Additional activity details
        error_message: Error message if activity failed
        priority: Log priority (Critical, High, Medium, Low)
    """
    try:
        request_info = _extract_request_info(request)
        
        # Merge request info with provided details
        activity_details = {
            "endpoint": request_info["endpoint"],
            "ip_address": request_info["ip_address"],
            "user_agent": request_info["user_agent"],
            "timestamp": request_info["timestamp"]
        }
        
        if details:
            activity_details.update(details)
        
        if session_id := request_info.get("session_id"):
            activity_details["session_id"] = session_id

        if error_message:
            activity_details["error_message"] = error_message

        log_level_map = {
            "Critical": AuditLogLevel.CRITICAL,
            "High": AuditLogLevel.HIGH,
            "Medium": AuditLogLevel.MEDIUM,
            "Low": AuditLogLevel.LOW,
        }
        log_level = log_level_map.get(priority, AuditLogLevel.MEDIUM)
        
        # Log the activity
        audit_logger.log_user_action(
            service="auth",
            user_id=user_id or "anonymous",
            action=activity_type,
            message=f"Auth activity: {activity_type}",
            session_id=request_info.get("session_id"),
            success=success,
            level=log_level,
            **activity_details
        )
        
        logger.info("Auth activity logged: %s for user %s", activity_type, username or "anonymous")
        
    except Exception as e:
        logger.error("Failed to log auth activity: %s", e)


async def log_login_attempt(
    audit_logger: AuditLogger,
    request: Request,
    username: str,
    success: bool = True,
    mfa_required: bool = False,
    error_message: Optional[str] = None,
    session_id: Optional[str] = None
) -> None:
    """Log login attempt with specific details"""
    details = {
        "login_method": "password",
        "mfa_required": mfa_required,
        "attempt_timestamp": format_ist_timestamp()
    }
    
    if session_id:
        details["session_id"] = session_id
    
    await log_auth_activity(
        audit_logger=audit_logger,
        activity_type="login_attempt",
        request=request,
        username=username,
        success=success,
        details=details,
        error_message=error_message,
        priority="High" if not success else "Medium"
    )


async def log_logout(
    audit_logger: AuditLogger,
    request: Request,
    user_id: str,
    username: str,
    session_id: Optional[str] = None
) -> None:
    """Log user logout"""
    details = {
        "logout_timestamp": format_ist_timestamp(),
        "logout_method": "token_revocation"
    }
    
    if session_id:
        details["session_id"] = session_id
    
    await log_auth_activity(
        audit_logger=audit_logger,
        activity_type="logout",
        request=request,
        user_id=user_id,
        username=username,
        success=True,
        details=details,
        priority="Medium"
    )


async def log_password_change(
    audit_logger: AuditLogger,
    request: Request,
    user_id: str,
    username: str,
    success: bool = True,
    error_message: Optional[str] = None,
    session_id: Optional[str] = None
) -> None:
    """Log password change attempt"""
    details = {
        "change_timestamp": format_ist_timestamp(),
        "change_method": "admin_endpoint"
    }
    
    if session_id:
        details["session_id"] = session_id
    
    await log_auth_activity(
        audit_logger=audit_logger,
        activity_type="password_change",
        request=request,
        user_id=user_id,
        username=username,
        success=success,
        details=details,
        error_message=error_message,
        priority="High"
    )


async def log_user_management(
    audit_logger: AuditLogger,
    request: Request,
    activity_type: str,
    user_id: str,
    username: str,
    target_user_id: Optional[str] = None,
    target_username: Optional[str] = None,
    success: bool = True,
    details: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    session_id: Optional[str] = None
) -> None:
    """Log user management activities"""
    activity_details = {
        "management_timestamp": format_ist_timestamp(),
        "target_user_id": target_user_id,
        "target_username": target_username
    }
    
    if details:
        activity_details.update(details)
    
    if session_id:
        activity_details["session_id"] = session_id
    
    await log_auth_activity(
        audit_logger=audit_logger,
        activity_type=f"user_management_{activity_type}",
        request=request,
        user_id=user_id,
        username=username,
        success=success,
        details=activity_details,
        error_message=error_message,
        priority="High"
    )


async def log_mfa_activity(
    audit_logger: AuditLogger,
    request: Request,
    activity_type: str,
    user_id: str,
    username: str,
    success: bool = True,
    details: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    session_id: Optional[str] = None
) -> None:
    """Log MFA-related activities"""
    activity_details = {
        "mfa_timestamp": format_ist_timestamp()
    }
    
    if details:
        activity_details.update(details)
    
    if session_id:
        activity_details["session_id"] = session_id
    
    await log_auth_activity(
        audit_logger=audit_logger,
        activity_type=f"mfa_{activity_type}",
        request=request,
        user_id=user_id,
        username=username,
        success=success,
        details=activity_details,
        error_message=error_message,
        priority="High"
    )


async def log_token_activity(
    audit_logger: AuditLogger,
    request: Request,
    activity_type: str,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    success: bool = True,
    details: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    session_id: Optional[str] = None
) -> None:
    """Log token-related activities (refresh, validation, etc.)"""
    activity_details = {
        "token_timestamp": format_ist_timestamp()
    }
    
    if details:
        activity_details.update(details)
    
    if session_id:
        activity_details["session_id"] = session_id
    
    await log_auth_activity(
        audit_logger=audit_logger,
        activity_type=f"token_{activity_type}",
        request=request,
        user_id=user_id,
        username=username,
        success=success,
        details=activity_details,
        error_message=error_message,
        priority="Medium"
    )
