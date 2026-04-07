"""
Simple Redis Cache Module
"""

from .cache_service import (
    CacheService,
    init_cache_service,
    get_cache_service,
    cache_set,
    cache_get,
    cache_invalidate
)
from .config import (
    DEFAULT_TTL_CONFIG,
    get_ttl_config,
    init_cache_with_config
)

__all__ = [
    "CacheService",
    "init_cache_service", 
    "get_cache_service",
    "cache_set",
    "cache_get",
    "cache_invalidate",
    "DEFAULT_TTL_CONFIG",
    "get_ttl_config",
    "init_cache_with_config"
]