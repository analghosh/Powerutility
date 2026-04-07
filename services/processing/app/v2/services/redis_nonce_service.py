"""
Redis Nonce Service for HMAC Authentication
Prevents replay attacks by tracking used nonces
"""

import redis
import logging
from typing import Optional
from ...core.config import settings

logger = logging.getLogger("redis-nonce")


class RedisNonceService:
    """Redis-based nonce tracking service for replay attack prevention"""
    
    def __init__(self):
        self.enabled = True
        self.ttl_seconds = settings.HMAC_NONCE_TTL
        
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
            logger.info("Redis nonce service initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Redis nonce service: {e}")
            self.enabled = False
            self.redis_client = None
    
    def _get_nonce_key(self, api_key: str, nonce: str) -> str:
        """
        Generate Redis key for nonce
        
        Args:
            api_key: The API key
            nonce: The nonce value
            
        Returns:
            Redis key string
        """
        return f"hmac:nonce:{api_key}:{nonce}"
    
    def is_nonce_used(self, api_key: str, nonce: str) -> bool:
        """
        Check if a nonce has already been used
        
        Args:
            api_key: The API key
            nonce: The nonce to check
            
        Returns:
            True if nonce exists (already used), False otherwise
        """
        if not self.enabled or not self.redis_client:
            logger.warning("Redis nonce service not available")
            # If Redis is down, fail open for availability (but log warning)
            # In production, you might want to fail closed
            return False
        
        try:
            key = self._get_nonce_key(api_key, nonce)
            exists = self.redis_client.exists(key) > 0
            return exists
        except Exception as e:
            logger.error(f"Failed to check nonce: {e}")
            # Fail open for availability
            return False
    
    def mark_nonce_used(self, api_key: str, nonce: str, ttl: Optional[int] = None) -> bool:
        """
        Mark a nonce as used
        
        Args:
            api_key: The API key
            nonce: The nonce to mark as used
            ttl: Optional TTL override (defaults to configured TTL)
            
        Returns:
            True if marked successfully, False otherwise
        """
        if not self.enabled or not self.redis_client:
            logger.warning("Redis nonce service not available")
            return False
        
        try:
            key = self._get_nonce_key(api_key, nonce)
            ttl_to_use = ttl or self.ttl_seconds
            # Store with timestamp for debugging
            self.redis_client.setex(key, ttl_to_use, "used")
            logger.debug(f"Marked nonce as used: {api_key[:8]}...")
            return True
        except Exception as e:
            logger.error(f"Failed to mark nonce as used: {e}")
            return False
    
    def cleanup_expired_nonces(self) -> int:
        """
        Cleanup expired nonces (Redis handles this automatically with TTL)
        This method is kept for compatibility but doesn't do expensive operations
        
        Returns:
            Always returns 0 (Redis auto-expires with TTL)
        """
        if not self.enabled or not self.redis_client:
            return 0
        
        try:
            # Redis automatically handles TTL expiration, no manual cleanup needed
            logger.debug("Redis TTL handles nonce expiration automatically")
            return 0
        except Exception as e:
            logger.error(f"Error in cleanup_expired_nonces: {e}")
            return 0
    
    def health_check(self) -> dict:
        """
        Check Redis nonce service health
        
        Returns:
            dict: Health status information
        """
        if not self.enabled:
            return {
                "status": "disabled",
                "enabled": False,
                "message": "Redis nonce service is disabled"
            }
        
        try:
            if not self.redis_client:
                return {
                    "status": "error",
                    "enabled": True,
                    "message": "Redis client not initialized"
                }
            
            # Test connection
            self.redis_client.ping()
            
            return {
                "status": "healthy",
                "enabled": True,
                "message": "Redis nonce service is healthy",
                "ttl_seconds": self.ttl_seconds
            }
        except Exception as e:
            return {
                "status": "error",
                "enabled": True,
                "message": f"Redis nonce service error: {str(e)}"
            }


# Create global Redis nonce service instance
redis_nonce_service = RedisNonceService()
