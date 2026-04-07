"""
Redis Cache Service for EPMS
Provides centralized caching functionality using Redis
"""

import json
import logging
from typing import Any, Optional, Dict, List
import redis
from redis.exceptions import ConnectionError, TimeoutError, RedisError

logger = logging.getLogger(__name__)


class RedisCache:
    """
    Redis cache service with connection pooling and error handling
    """
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        decode_responses: bool = True,
        socket_timeout: int = 5,
        socket_connect_timeout: int = 5,
        max_connections: int = 10
    ):
        """
        Initialize Redis cache connection
        
        Args:
            host: Redis server host
            port: Redis server port
            db: Redis database number
            password: Redis password (if required)
            decode_responses: Whether to decode responses to strings
            socket_timeout: Socket timeout in seconds
            socket_connect_timeout: Socket connection timeout in seconds
            max_connections: Maximum connections in pool
        """
        self.host = host
        self.port = port
        self.db = db
        
        # Create connection pool
        self.pool = redis.ConnectionPool(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=decode_responses,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            max_connections=max_connections
        )
        
        # Create Redis client
        self.client = redis.Redis(connection_pool=self.pool)
        
        # Test connection
        self._test_connection()
    
    def _test_connection(self) -> bool:
        """Test Redis connection"""
        try:
            self.client.ping()
            logger.info(f"✅ Redis connection established: {self.host}:{self.port}/{self.db}")
            return True
        except (ConnectionError, TimeoutError) as e:
            logger.error(f"❌ Redis connection failed: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected Redis error: {e}")
            return False
    
    def is_connected(self) -> bool:
        """Check if Redis is connected"""
        try:
            self.client.ping()
            return True
        except Exception:
            return False
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        Set a value in Redis cache
        
        Args:
            key: Cache key
            value: Value to cache (will be JSON serialized)
            ttl: Time to live in seconds (None for no expiration)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Serialize value to JSON
            serialized_value = json.dumps(value, default=str)
            
            if ttl:
                result = self.client.setex(key, ttl, serialized_value)
            else:
                result = self.client.set(key, serialized_value)
            
            logger.debug(f"Cache SET: {key} (TTL: {ttl}s)")
            return bool(result)
            
        except (ConnectionError, TimeoutError) as e:
            logger.warning(f"Redis connection error during SET {key}: {e}")
            return False
        except Exception as e:
            logger.error(f"Error setting cache key {key}: {e}")
            return False
    
    def get(self, key: str) -> Optional[Any]:
        """
        Get a value from Redis cache
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found/error
        """
        try:
            value = self.client.get(key)
            if value is None:
                logger.debug(f"Cache MISS: {key}")
                return None
            
            # Deserialize JSON value
            deserialized_value = json.loads(value)
            logger.debug(f"Cache HIT: {key}")
            return deserialized_value
            
        except (ConnectionError, TimeoutError) as e:
            logger.warning(f"Redis connection error during GET {key}: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Error deserializing cache value for {key}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error getting cache key {key}: {e}")
            return None
    
    def delete(self, key: str) -> bool:
        """
        Delete a key from Redis cache
        
        Args:
            key: Cache key to delete
            
        Returns:
            True if deleted, False otherwise
        """
        try:
            result = self.client.delete(key)
            logger.debug(f"Cache DELETE: {key}")
            return bool(result)
        except Exception as e:
            logger.error(f"Error deleting cache key {key}: {e}")
            return False
    
    def exists(self, key: str) -> bool:
        """
        Check if a key exists in Redis cache
        
        Args:
            key: Cache key to check
            
        Returns:
            True if exists, False otherwise
        """
        try:
            return bool(self.client.exists(key))
        except Exception as e:
            logger.error(f"Error checking cache key existence {key}: {e}")
            return False
    
    def set_hash(self, key: str, mapping: Dict[str, Any], ttl: Optional[int] = None) -> bool:
        """
        Set a hash in Redis cache
        
        Args:
            key: Cache key
            mapping: Dictionary to store as hash
            ttl: Time to live in seconds
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Serialize all values in the mapping
            serialized_mapping = {
                field: json.dumps(value, default=str) 
                for field, value in mapping.items()
            }
            
            result = self.client.hset(key, mapping=serialized_mapping)
            
            if ttl:
                self.client.expire(key, ttl)
            
            logger.debug(f"Cache HSET: {key} ({len(mapping)} fields, TTL: {ttl}s)")
            return True
            
        except Exception as e:
            logger.error(f"Error setting hash {key}: {e}")
            return False
    
    def get_hash(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Get a hash from Redis cache
        
        Args:
            key: Cache key
            
        Returns:
            Dictionary or None if not found/error
        """
        try:
            hash_data = self.client.hgetall(key)
            if not hash_data:
                logger.debug(f"Cache HMISS: {key}")
                return None
            
            # Deserialize all values
            deserialized_data = {
                field: json.loads(value) 
                for field, value in hash_data.items()
            }
            
            logger.debug(f"Cache HHIT: {key} ({len(deserialized_data)} fields)")
            return deserialized_data
            
        except Exception as e:
            logger.error(f"Error getting hash {key}: {e}")
            return None
    
    def get_hash_field(self, key: str, field: str) -> Optional[Any]:
        """
        Get a specific field from a hash
        
        Args:
            key: Cache key
            field: Hash field name
            
        Returns:
            Field value or None if not found/error
        """
        try:
            value = self.client.hget(key, field)
            if value is None:
                return None
            
            return json.loads(value)
            
        except Exception as e:
            logger.error(f"Error getting hash field {key}.{field}: {e}")
            return None
    
    def clear_pattern(self, pattern: str) -> int:
        """
        Clear all keys matching a pattern
        
        Args:
            pattern: Redis key pattern (e.g., "timeblock:*")
            
        Returns:
            Number of keys deleted
        """
        try:
            keys = self.client.keys(pattern)
            if keys:
                deleted = self.client.delete(*keys)
                logger.info(f"Cache CLEAR: {deleted} keys matching '{pattern}'")
                return deleted
            return 0
        except Exception as e:
            logger.error(f"Error clearing pattern {pattern}: {e}")
            return 0
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get Redis cache statistics
        
        Returns:
            Dictionary with cache stats
        """
        try:
            info = self.client.info()
            return {
                "connected": True,
                "used_memory": info.get("used_memory_human", "unknown"),
                "connected_clients": info.get("connected_clients", 0),
                "total_commands_processed": info.get("total_commands_processed", 0),
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
                "hit_rate": self._calculate_hit_rate(
                    info.get("keyspace_hits", 0),
                    info.get("keyspace_misses", 0)
                )
            }
        except Exception as e:
            logger.error(f"Error getting Redis stats: {e}")
            return {"connected": False, "error": str(e)}
    
    def _calculate_hit_rate(self, hits: int, misses: int) -> float:
        """Calculate cache hit rate percentage"""
        total = hits + misses
        if total == 0:
            return 0.0
        return round((hits / total) * 100, 2)
    
    def close(self):
        """Close Redis connection"""
        try:
            self.client.close()
            logger.info("Redis connection closed")
        except Exception as e:
            logger.error(f"Error closing Redis connection: {e}")


class TimeblockCache:
    """
    Specialized cache for timeblock mappings using Redis
    """
    
    CACHE_KEY = "timeblock:mappings"
    DEFAULT_TTL = 86400  # 24 hours (timeblocks are daily and relatively static)
    
    def __init__(self, redis_cache: RedisCache):
        """
        Initialize timeblock cache
        
        Args:
            redis_cache: Redis cache instance
        """
        self.cache = redis_cache
        self.logger = logging.getLogger(f"{__name__}.TimeblockCache")
    
    def load_timeblocks(self, timeblock_data: Dict[str, int]) -> bool:
        """
        Load timeblock mappings into Redis cache
        
        Args:
            timeblock_data: Dictionary mapping time ranges to timeblock numbers
                           e.g., {"00:00-00:15": 1, "00:15-00:30": 2, ...}
        
        Returns:
            True if successful, False otherwise
        """
        try:
            success = self.cache.set_hash(
                self.CACHE_KEY, 
                timeblock_data, 
                ttl=self.DEFAULT_TTL
            )
            
            if success:
                self.logger.info(f"✅ Loaded {len(timeblock_data)} timeblock mappings to Redis cache")
            else:
                self.logger.error("❌ Failed to load timeblock mappings to Redis cache")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error loading timeblocks to cache: {e}")
            return False
    
    def get_timeblock(self, time_range: str) -> Optional[int]:
        """
        Get timeblock number for a time range
        
        Args:
            time_range: Time range string (e.g., "00:00-00:15")
            
        Returns:
            Timeblock number or None if not found
        """
        try:
            timeblock = self.cache.get_hash_field(self.CACHE_KEY, time_range)
            
            if timeblock is not None:
                self.logger.debug(f"Cache HIT: {time_range} -> {timeblock}")
                return int(timeblock)
            else:
                self.logger.debug(f"Cache MISS: {time_range}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error getting timeblock for {time_range}: {e}")
            return None
    
    def get_all_timeblocks(self) -> Optional[Dict[str, int]]:
        """
        Get all timeblock mappings from cache
        
        Returns:
            Dictionary of all timeblock mappings or None if error
        """
        try:
            data = self.cache.get_hash(self.CACHE_KEY)
            if data:
                # Convert values to integers
                return {k: int(v) for k, v in data.items()}
            return None
        except Exception as e:
            self.logger.error(f"Error getting all timeblocks: {e}")
            return None
    
    def refresh_cache(self, timeblock_data: Dict[str, int]) -> bool:
        """
        Refresh timeblock cache with new data
        
        Args:
            timeblock_data: New timeblock mappings
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Clear existing cache
            self.cache.delete(self.CACHE_KEY)
            
            # Load new data
            return self.load_timeblocks(timeblock_data)
            
        except Exception as e:
            self.logger.error(f"Error refreshing timeblock cache: {e}")
            return False
    
    def is_cache_loaded(self) -> bool:
        """
        Check if timeblock cache is loaded
        
        Returns:
            True if cache exists and has data, False otherwise
        """
        try:
            return self.cache.exists(self.CACHE_KEY)
        except Exception as e:
            self.logger.error(f"Error checking cache status: {e}")
            return False
    
    def clear_cache(self) -> bool:
        """
        Clear timeblock cache
        
        Returns:
            True if successful, False otherwise
        """
        try:
            result = self.cache.delete(self.CACHE_KEY)
            if result:
                self.logger.info("Timeblock cache cleared")
            return result
        except Exception as e:
            self.logger.error(f"Error clearing timeblock cache: {e}")
            return False


# Global Redis cache instance (will be initialized by services)
_redis_cache: Optional[RedisCache] = None
_timeblock_cache: Optional[TimeblockCache] = None


def get_redis_cache(
    host: str = "localhost",
    port: int = 6379,
    db: int = 0,
    password: Optional[str] = None
) -> RedisCache:
    """
    Get or create Redis cache instance (singleton pattern)
    
    Args:
        host: Redis server host
        port: Redis server port  
        db: Redis database number
        password: Redis password
        
    Returns:
        Redis cache instance
    """
    global _redis_cache
    
    if _redis_cache is None:
        _redis_cache = RedisCache(
            host=host,
            port=port,
            db=db,
            password=password
        )
    
    return _redis_cache


def get_timeblock_cache() -> TimeblockCache:
    """
    Get timeblock cache instance
    
    Returns:
        Timeblock cache instance
    """
    global _timeblock_cache, _redis_cache
    
    if _timeblock_cache is None:
        if _redis_cache is None:
            # Initialize with default Redis settings
            _redis_cache = get_redis_cache()
        
        _timeblock_cache = TimeblockCache(_redis_cache)
    
    return _timeblock_cache