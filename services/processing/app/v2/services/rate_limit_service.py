"""
Rate Limiting Service for HMAC Authentication
Redis-based rate limiting per API key
"""

import redis
import logging
import time
from typing import Optional
from ...core.config import settings

logger = logging.getLogger("rate-limit")


class RateLimitService:
    """Redis-based rate limiting service using sliding window algorithm"""
    
    def __init__(self):
        self.enabled = True
        self.max_requests = settings.HMAC_RATE_LIMIT_REQUESTS
        self.window_seconds = settings.HMAC_RATE_LIMIT_WINDOW
        
        try:
            self.redis_client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True
            )
            # Test connection
            self.redis_client.ping()
            logger.info(
                f"Rate limit service initialized: {self.max_requests} requests per {self.window_seconds}s"
            )
        except Exception as e:
            logger.error(f"Failed to initialize rate limit service: {e}")
            self.enabled = False
            self.redis_client = None
    
    def _get_rate_limit_key(self, api_key: str) -> str:
        """
        Generate Redis key for rate limiting
        
        Args:
            api_key: The API key
            
        Returns:
            Redis key string
        """
        return f"hmac:ratelimit:{api_key}"
    
    def check_rate_limit(self, api_key: str) -> bool:
        """
        Check if API key is within rate limit
        
        Args:
            api_key: The API key to check
            
        Returns:
            True if within limit, False if rate limited
        """
        if not self.enabled or not self.redis_client:
            logger.warning("Rate limit service not available, allowing request")
            # Fail open for availability
            return True
        
        try:
            key = self._get_rate_limit_key(api_key)
            now = time.time()
            window_start = now - self.window_seconds
            
            # Use Redis sorted set to track requests in time window
            # Remove old entries outside the window
            self.redis_client.zremrangebyscore(key, 0, window_start)
            
            # Count requests in current window
            current_count = self.redis_client.zcard(key)
            
            if current_count >= self.max_requests:
                logger.warning(
                    f"Rate limit exceeded for {api_key[:8]}...: "
                    f"{current_count}/{self.max_requests} requests"
                )
                return False
            
            # Add current request to sorted set with timestamp as score
            self.redis_client.zadd(key, {str(now): now})
            
            # Set expiry on the key (window_seconds + some buffer)
            self.redis_client.expire(key, self.window_seconds + 60)
            
            return True
        except Exception as e:
            logger.error(f"Error checking rate limit: {e}")
            # Fail open for availability
            return True
    
    def get_rate_limit_info(self, api_key: str) -> dict:
        """
        Get rate limit information for an API key
        
        Args:
            api_key: The API key
            
        Returns:
            Dictionary with rate limit information
        """
        if not self.enabled or not self.redis_client:
            return {
                "enabled": False,
                "current_count": 0,
                "max_requests": self.max_requests,
                "window_seconds": self.window_seconds
            }
        
        try:
            key = self._get_rate_limit_key(api_key)
            now = time.time()
            window_start = now - self.window_seconds
            
            # Remove old entries
            self.redis_client.zremrangebyscore(key, 0, window_start)
            
            # Get current count
            current_count = self.redis_client.zcard(key)
            
            return {
                "enabled": True,
                "current_count": current_count,
                "max_requests": self.max_requests,
                "window_seconds": self.window_seconds,
                "remaining": max(0, self.max_requests - current_count)
            }
        except Exception as e:
            logger.error(f"Error getting rate limit info: {e}")
            return {
                "enabled": True,
                "error": str(e)
            }
    
    def reset_rate_limit(self, api_key: str) -> bool:
        """
        Reset rate limit for an API key (for testing/admin purposes)
        
        Args:
            api_key: The API key
            
        Returns:
            True if reset successfully, False otherwise
        """
        if not self.enabled or not self.redis_client:
            return False
        
        try:
            key = self._get_rate_limit_key(api_key)
            self.redis_client.delete(key)
            logger.info(f"Rate limit reset for {api_key[:8]}...")
            return True
        except Exception as e:
            logger.error(f"Error resetting rate limit: {e}")
            return False


# Create global rate limit service instance
rate_limit_service = RateLimitService()
