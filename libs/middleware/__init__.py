"""
Middleware utilities for EPMS services
"""

from .correlation import CorrelationMiddleware, get_correlation_id, set_correlation_id
from .auth_middleware import AuthMiddleware, get_current_user, create_auth_middleware, has_permission, has_role, get_user_context
from .logging_middleware import LoggingMiddleware, CorrelationIDFilter, ColoredFormatter, setup_logging_middleware, configure_logging
from .cache_middleware import (
    CacheMiddleware,
    setup_cache_middleware,
    get_cache_dependency,
    get_cache_from_request,
    check_cache_health,
    get_cache_metrics,
    clear_cache_pattern,
    invalidate_cache_keys
)
from .utils import extract_bearer_token, generate_correlation_id

__all__ = [
    "CorrelationMiddleware",
    "get_correlation_id",
    "set_correlation_id", 
    "AuthMiddleware",
    "get_current_user",
    "create_auth_middleware",
    "has_permission",
    "has_role",
    "get_user_context",
    "LoggingMiddleware",
    "CorrelationIDFilter",
    "ColoredFormatter",
    "setup_logging_middleware",
    "configure_logging",
    "CacheMiddleware",
    "setup_cache_middleware",
    "get_cache_dependency",
    "get_cache_from_request",
    "check_cache_health",
    "get_cache_metrics",
    "clear_cache_pattern",
    "invalidate_cache_keys",
    "extract_bearer_token",
    "generate_correlation_id"
]
