"""
Simple Redis Cache Service
"""

import json
import logging
from typing import Any, Optional, Dict
import redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class CacheService:
    """Simple Redis cache service with set, get, and invalidate operations"""
    
    def __init__(
        self,
        redis_host: str = "localhost",
        redis_port: int = 6379,
        redis_db: int = 0,
        redis_password: Optional[str] = None,
        default_ttl: int = 3600,
        ttl_config: Optional[Dict[str, int]] = None
    ):
        """
        Initialize cache service
        
        Args:
            redis_host: Redis server host
            redis_port: Redis server port  
            redis_db: Redis database number
            redis_password: Redis password (if required)
            default_ttl: Default TTL in seconds
            ttl_config: Dictionary mapping key patterns to TTL values
                       e.g., {"user:*": 1800, "session:*": 7200, "temp:*": 300}
        """
        self.default_ttl = default_ttl
        self.ttl_config = ttl_config or {}
        
        try: # Lazy connection: attempt to connect but don't fail if Redis is down
            self.redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                password=redis_password,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5
            )
            
            # Test connection
            self.redis_client.ping()
            logger.info(f"Connected to Redis: {redis_host}:{redis_port}/{redis_db}")
            
        except RedisError as e:
            # Log a warning instead of raising an error to allow the app to start
            logger.warning(f"Could not connect to Redis at {redis_host}:{redis_port}. Cache will be unavailable. Error: {e}")
            self.redis_client = None
    
    def _get_ttl_for_key(self, key: str) -> int:
        """
        Get TTL for a key based on configuration patterns
        
        Args:
            key: Cache key
            
        Returns:
            TTL in seconds
        """
        import fnmatch
        
        # Check if key matches any configured patterns
        for pattern, ttl in self.ttl_config.items():
            if fnmatch.fnmatch(key, pattern):
                return ttl
        
        # Return default TTL if no pattern matches
        return self.default_ttl
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        Set a value in Redis
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds (uses config-based TTL if None)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_client:
            logger.warning("Cache unavailable. Cannot SET key: %s", key)
            return False
            
        try:
            if ttl is None:
                ttl = self._get_ttl_for_key(key)
            
            serialized_value = json.dumps(value, default=str)
            result = self.redis_client.setex(key, ttl, serialized_value)
            logger.debug(f"Cache SET: {key} (TTL: {ttl}s)")
            return bool(result)
        except Exception as e:
            logger.error(f"Error setting cache key {key}: {e}")
            return False
    
    def get(self, key: str) -> Optional[Any]:
        """
        Get a value from Redis
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found
        """
        if not self.redis_client:
            logger.warning("Cache unavailable. Cannot GET key: %s", key)
            return None
            
        try:
            value = self.redis_client.get(key)
            if value is not None:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Error getting cache key {key}: {e}")
            return None
    
    def invalidate(self, key: str) -> bool:
        """
        Invalidate (delete) a key from Redis
        
        Args:
            key: Cache key to delete
            
        Returns:
            True if deleted, False otherwise
        """
        if not self.redis_client:
            logger.warning("Cache unavailable. Cannot INVALIDATE key: %s", key)
            return False
            
        try:
            result = self.redis_client.delete(key)
            return bool(result)
        except Exception as e:
            logger.error(f"Error invalidating cache key {key}: {e}")
            return False


# Global cache service instance
_cache_service: Optional[CacheService] = None


def init_cache_service(
    redis_host: str = "localhost",
    redis_port: int = 6379,
    redis_db: int = 0,
    redis_password: Optional[str] = None,
    default_ttl: int = 3600,
    ttl_config: Optional[Dict[str, int]] = None
) -> CacheService:
    """
    Initialize global cache service
    
    Args:
        redis_host: Redis server host
        redis_port: Redis server port
        redis_db: Redis database number
        redis_password: Redis password
        default_ttl: Default TTL in seconds
        ttl_config: Dictionary mapping key patterns to TTL values
    """
    global _cache_service
    _cache_service = CacheService(
        redis_host=redis_host,
        redis_port=redis_port,
        redis_db=redis_db,
        redis_password=redis_password,
        default_ttl=default_ttl,
        ttl_config=ttl_config
    )
    return _cache_service


def get_cache_service() -> Optional[CacheService]:
    """Get global cache service instance"""
    return _cache_service


# Convenience functions
def cache_set(key: str, value: Any, ttl: Optional[int] = None) -> bool:
    """Set a value in cache"""
    cache = get_cache_service()
    return cache.set(key, value, ttl) if cache else False


def cache_get(key: str) -> Optional[Any]:
    """Get a value from cache"""
    cache = get_cache_service()
    return cache.get(key) if cache else None


def cache_invalidate(key: str) -> bool:
    """Invalidate a key from cache"""
    cache = get_cache_service()
    return cache.invalidate(key) if cache else False
