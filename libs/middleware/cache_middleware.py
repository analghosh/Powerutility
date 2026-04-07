"""
Cache Middleware for EPMS
Makes cache service available throughout request lifecycle
"""

import logging
from typing import Callable, Optional
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from ..cache import CacheService, init_cache_service, get_cache_service

logger = logging.getLogger(__name__)


class CacheMiddleware(BaseHTTPMiddleware):
    """
    Middleware to inject cache service into request context
    """
    
    def __init__(
        self,
        app: ASGIApp,
        redis_host: str = "localhost",
        redis_port: int = 6379,
        redis_db: int = 0,
        redis_password: Optional[str] = None,
        default_ttl: int = 3600,
        ttl_config: Optional[dict] = None
    ):
        """
        Initialize cache middleware
        
        Args:
            app: ASGI application
            redis_host: Redis server host
            redis_port: Redis server port
            redis_db: Redis database number
            redis_password: Redis password (if required)
            default_ttl: Default TTL in seconds
            ttl_config: TTL configuration for different key patterns
        """
        super().__init__(app)
        
        # Initialize global cache service
        self.cache_service = init_cache_service(
            redis_host=redis_host,
            redis_port=redis_port,
            redis_db=redis_db,
            redis_password=redis_password,
            default_ttl=default_ttl,
            ttl_config=ttl_config
        )
        
        if self.cache_service and self.cache_service.redis_client:
            logger.info(f"✅ Cache middleware initialized with Redis: {redis_host}:{redis_port}/{redis_db}")
        else:
            logger.warning("⚠️ Cache middleware initialized, but Redis connection failed. Cache will be disabled.")
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process request and inject cache service into context
        
        Args:
            request: FastAPI request object
            call_next: Next middleware/endpoint in chain
            
        Returns:
            Response object
        """
        # Add cache service to request state for easy access
        request.state.cache = self.cache_service
        
        try:
            # Process request
            response = await call_next(request)
            return response
            
        except Exception as e:
            logger.error(f"Error in cache middleware: {str(e)}")
            raise


def setup_cache_middleware(
    app,
    redis_host: str = "localhost",
    redis_port: int = 6379,
    redis_db: int = 0,
    redis_password: Optional[str] = None,
    default_ttl: int = 3600,
    ttl_config: Optional[dict] = None
) -> CacheMiddleware:
    """
    Setup cache middleware with configuration
    
    Args:
        app: FastAPI application
        redis_host: Redis server host
        redis_port: Redis server port
        redis_db: Redis database number
        redis_password: Redis password
        default_ttl: Default TTL in seconds
        ttl_config: TTL configuration for different key patterns
        
    Returns:
        Configured CacheMiddleware instance
    """
    middleware = CacheMiddleware(
        app=app,
        redis_host=redis_host,
        redis_port=redis_port,
        redis_db=redis_db,
        redis_password=redis_password,
        default_ttl=default_ttl,
        ttl_config=ttl_config
    )
    
    return middleware


# Dependency injection functions for FastAPI

def get_cache_dependency() -> Optional[CacheService]:
    """
    FastAPI dependency to get cache service
    
    Returns:
        Cache service instance or None if not available
    """
    return get_cache_service()


def get_cache_from_request(request: Request) -> Optional[CacheService]:
    """
    Get cache service from request state
    
    Args:
        request: FastAPI request object
        
    Returns:
        Cache service instance or None if not available
    """
    return getattr(request.state, 'cache', None)


# Cache health check functions

async def check_cache_health() -> dict:
    """
    Check cache service health
    
    Returns:
        Dictionary with cache health status
    """
    try:
        cache = get_cache_service()
        if not cache:
            return {
                "status": "unavailable",
                "message": "Cache service not initialized"
            }
        
        # Test cache operations
        test_key = "health_check_test"
        test_value = "test_value"
        
        # Test set operation
        set_success = cache.set(test_key, test_value, ttl=60)
        if not set_success:
            return {
                "status": "error",
                "message": "Failed to set test value"
            }
        
        # Test get operation
        retrieved_value = cache.get(test_key)
        if retrieved_value != test_value:
            return {
                "status": "error",
                "message": "Failed to retrieve test value"
            }
        
        # Test invalidate operation
        invalidate_success = cache.invalidate(test_key)
        if not invalidate_success:
            logger.warning("Failed to invalidate test key during health check")
        
        return {
            "status": "healthy",
            "message": "Cache service is working properly"
        }
        
    except Exception as e:
        logger.error(f"Cache health check failed: {e}")
        return {
            "status": "error",
            "message": f"Cache health check failed: {str(e)}"
        }


# Cache monitoring functions

def get_cache_metrics() -> dict:
    """
    Get cache metrics for monitoring
    
    Returns:
        Dictionary with cache metrics
    """
    try:
        cache = get_cache_service()
        if not cache:
            return {"error": "Cache service not available"}
        
        # Basic metrics for our simple cache service
        metrics = {
            "cache_service_available": True,
            "redis_host": cache.redis_client.connection_pool.connection_kwargs.get('host'),
            "redis_port": cache.redis_client.connection_pool.connection_kwargs.get('port'),
            "default_ttl": cache.default_ttl
        }
        
        # Test Redis connection
        try:
            cache.redis_client.ping()
            metrics["redis_available"] = True
        except Exception:
            metrics["redis_available"] = False
        
        return metrics
        
    except Exception as e:
        logger.error(f"Error getting cache metrics: {e}")
        return {"error": str(e)}


# Cache utility functions

def clear_cache_pattern(pattern: str) -> int:
    """
    Clear cache keys matching a pattern
    
    Args:
        pattern: Key pattern to match
        
    Returns:
        Number of keys cleared
    """
    try:
        cache = get_cache_service()
        if not cache:
            return 0
        
        # Get keys matching pattern
        keys = cache.redis_client.keys(pattern)
        if keys:
            deleted_count = cache.redis_client.delete(*keys)
            logger.info(f"Cleared {deleted_count} cache keys matching pattern '{pattern}'")
            return deleted_count
        return 0
        
    except Exception as e:
        logger.error(f"Error clearing cache pattern {pattern}: {e}")
        return 0


def invalidate_cache_keys(keys: list) -> int:
    """
    Invalidate specific cache keys
    
    Args:
        keys: List of cache keys to invalidate
        
    Returns:
        Number of keys invalidated
    """
    try:
        cache = get_cache_service()
        if not cache:
            return 0
        
        invalidated = 0
        for key in keys:
            if cache.invalidate(key):
                invalidated += 1
        
        logger.info(f"Invalidated {invalidated} cache keys")
        return invalidated
        
    except Exception as e:
        logger.error(f"Error invalidating cache keys: {e}")
        return 0