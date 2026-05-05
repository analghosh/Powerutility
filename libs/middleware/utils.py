"""
Common middleware utilities
"""

import uuid
import logging
from typing import Optional
from fastapi import Request
from ..auth.exceptions import InvalidAuthHeaderError, MissingTokenError

logger = logging.getLogger(__name__)


def generate_correlation_id() -> str:
    """
    Generate a new correlation ID
    
    Returns:
        UUID string for correlation ID
    """
    return str(uuid.uuid4())


def extract_bearer_token(request: Request) -> str:
    """
    Extract Bearer token from Authorization header
    
    Args:
        request: FastAPI request object
        
    Returns:
        JWT token string
        
    Raises:
        MissingTokenError: If Authorization header is missing
        InvalidAuthHeaderError: If Authorization header format is invalid
    """
    auth_header = request.headers.get("Authorization")
    
    if not auth_header:
        raise MissingTokenError("Authorization header required")
    
    # Check if it's Bearer token format
    if not auth_header.startswith("Bearer "):
        raise InvalidAuthHeaderError("Authorization header must be in format: Bearer <token>")
    
    # Extract token (remove "Bearer " prefix)
    token = auth_header[7:].strip()
    
    if not token:
        raise InvalidAuthHeaderError("Bearer token cannot be empty")
    
    return token


def get_client_ip(request: Request) -> str:
    """
    Get client IP address from request
    
    Args:
        request: FastAPI request object
        
    Returns:
        Client IP address
    """
    # Check for forwarded headers first (for proxy/load balancer scenarios)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For can contain multiple IPs, take the first one
        return forwarded_for.split(",")[0].strip()
    
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    
    # Fallback to direct client IP
    if hasattr(request, "client") and request.client:
        return request.client.host
    
    return "unknown"


def get_user_agent(request: Request) -> str:
    """
    Get User-Agent from request headers
    
    Args:
        request: FastAPI request object
        
    Returns:
        User-Agent string
    """
    return request.headers.get("User-Agent", "unknown")


def log_request_info(request: Request, correlation_id: str, user_id: Optional[str] = None):
    """
    Log request information for audit purposes
    
    Args:
        request: FastAPI request object
        correlation_id: Request correlation ID
        user_id: Authenticated user ID (if available)
    """
    logger.info(
        f"Request: {request.method} {request.url.path} | "
        f"CorrelationID: {correlation_id} | "
        f"UserID: {user_id or 'anonymous'} | "
        f"ClientIP: {get_client_ip(request)} | "
        f"UserAgent: {get_user_agent(request)}"
    )
