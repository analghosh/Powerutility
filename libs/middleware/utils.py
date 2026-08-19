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


# Default matches `Settings.TRUSTED_PROXIES` in the auth and ingestion services. The two
# disagreed until 2026-08-19: the settings classes defaulted to "127.0.0.1" while this
# function defaulted to "", and this function is the one that governs. The effect was that
# X-Forwarded-For was ignored on every deployment that did not set the variable
# explicitly, so behind nginx every request resolved to the proxy's own address --
# silently wrong audit IPs, and a per-IP rate limiter would have degraded into a single
# global bucket for the entire internet.
#
# Defaulting to loopback is safe: if the service is exposed directly rather than through a
# proxy, the socket peer is the real client and is not 127.0.0.1, so forwarded headers
# stay ignored. Only a process on the same host could assert one.
_DEFAULT_TRUSTED_PROXIES = "127.0.0.1"


def get_trusted_proxies() -> FrozenSet[str]:
    """
    Read trusted proxy IPs from TRUSTED_PROXIES env var (comma-separated).
    Set it to an empty string to trust nothing — XFF headers are then always ignored.
    Example: TRUSTED_PROXIES=127.0.0.1,10.0.0.1
    """
    raw = os.getenv("TRUSTED_PROXIES", _DEFAULT_TRUSTED_PROXIES)
    return frozenset(ip.strip() for ip in raw.split(",") if ip.strip())


# Kept for callers that imported the private name.
_get_trusted_proxies = get_trusted_proxies


def get_client_ip(request: Request) -> str:
    """
    Get client IP address from request.

    X-Forwarded-For / X-Real-IP are only trusted when the direct socket peer
    is listed in the TRUSTED_PROXIES environment variable.  This prevents
    attackers from spoofing their IP to evade rate limiting or forge audit logs.
    """
    peer = request.client.host if (hasattr(request, "client") and request.client) else None

    if peer and peer in get_trusted_proxies():
        # Proxy is trusted — honour forwarded headers.
        #
        # X-Real-IP is preferred over X-Forwarded-For, and the order matters. nginx sets
        # X-Real-IP to $remote_addr, overwriting whatever the client sent. The idiomatic
        # X-Forwarded-For value, $proxy_add_x_forwarded_for, *appends* to the client's own
        # header instead — so a client that sends "X-Forwarded-For: 1.2.3.4" arrives as
        # "1.2.3.4, <real client>", and taking the leftmost entry returns the address the
        # attacker chose. Preferring X-Real-IP makes this correct under either nginx
        # configuration; the XFF fallback keeps working where only that header is set.
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()

        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

    # Untrusted or unknown peer — use the socket address directly
    return peer or "unknown"


def get_client_ip_or_none(request: Request) -> Optional[str]:
    """
    Client IP, or None when it cannot be determined.

    `get_client_ip` returns the string "unknown" as its sentinel, which suits log lines but
    not audit records: persisting the literal "unknown" in a `client_ip` field changes the
    stored schema and reads like a value rather than an absence. Audit and throttling
    callers use this instead, preserving the `None` those call sites produced before they
    were switched off `request.client.host`.
    """
    ip = get_client_ip(request)
    return None if ip == "unknown" else ip


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