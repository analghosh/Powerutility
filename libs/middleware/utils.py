"""
Common middleware utilities
"""

import os
import uuid
import logging
from typing import Optional, FrozenSet
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


def _get_trusted_proxies() -> FrozenSet[str]:
    """
    Read trusted proxy IPs from TRUSTED_PROXIES env var (comma-separated).
    Returns an empty frozenset when not configured — XFF headers are then ignored.
    Example: TRUSTED_PROXIES=127.0.0.1,10.0.0.1
    """
    raw = os.getenv("TRUSTED_PROXIES", "")
    return frozenset(ip.strip() for ip in raw.split(",") if ip.strip())


def get_client_ip(request: Request) -> str:
    """
    Get client IP address from request.

    X-Forwarded-For / X-Real-IP are only trusted when the direct socket peer
    is listed in the TRUSTED_PROXIES environment variable.  This prevents
    attackers from spoofing their IP to evade rate limiting or forge audit logs.
    """
    peer = request.client.host if (hasattr(request, "client") and request.client) else None

    if peer and peer in _get_trusted_proxies():
        # Proxy is trusted — honour forwarded headers
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()

    # Untrusted or unknown peer — use the socket address directly
    return peer or "unknown"


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