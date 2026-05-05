"""
Redis Blacklist Service for JWT Token Invalidation
"""

import redis
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from ..core.config import settings

# Configure logging
logger = logging.getLogger("redis-blacklist")

class RedisBlacklistService:
    """Redis-based token blacklist service"""
    
    def __init__(self):
        self.enabled = settings.REDIS_BLACKLIST_ENABLED
        self.ttl_seconds = settings.REDIS_BLACKLIST_TTL * 24 * 60 * 60
        
        if self.enabled:
            try:
                self.redis_client = redis.Redis(
                    host=settings.REDIS_HOST,
                    port=settings.REDIS_PORT,
                    db=settings.REDIS_BLACKLIST_DB,
                    password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
                    decode_responses=True,
                    socket_connect_timeout=5,
                    socket_timeout=5,
                    retry_on_timeout=True
                )
                # Test connection
                self.redis_client.ping()
                logger.info("Redis blacklist service initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize Redis blacklist service: {e}")
                self.enabled = False
                self.redis_client = None
        else:
            logger.info("Redis blacklist service disabled")
            self.redis_client = None
    
    async def invalidate_refresh_token(self, refresh_token: str, user_id: str) -> bool:
        """
        Add refresh token to Redis blacklist with TTL
        
        Args:
            refresh_token: The refresh token to blacklist
            user_id: User ID for audit purposes
            
        Returns:
            bool: True if successfully blacklisted, False otherwise
        """
        if not self.enabled or not self.redis_client:
            logger.warning("Redis blacklist service not available")
            return False
        
        try:
            # Set with expiry (same as token expiry)
            key = f"blacklist:refresh:{refresh_token}"
            self.redis_client.setex(key, self.ttl_seconds, user_id)
            
            logger.info(f"Refresh token blacklisted successfully for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to blacklist refresh token: {e}")
            return False
    
    async def invalidate_access_token(self, access_token: str, user_id: str) -> bool:
        """
        Add access token to Redis blacklist with TTL
        
        Args:
            access_token: The access token to blacklist
            user_id: User ID for audit purposes
            
        Returns:
            bool: True if successfully blacklisted, False otherwise
        """
        if not self.enabled or not self.redis_client:
            logger.warning("Redis blacklist service not available")
            return False
        
        try:
            # Set with expiry (same as token expiry)
            key = f"blacklist:access:{access_token}"
            self.redis_client.setex(key, self.ttl_seconds, user_id)
            
            logger.info(f"Access token blacklisted successfully for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to blacklist access token: {e}")
            return False
    
    async def invalidate_temporary_token(self, temporary_token: str, user_id: str) -> bool:
        """
        Add temporary token to Redis blacklist with TTL
        
        Args:
            temporary_token: The temporary token to blacklist
            user_id: User ID for audit purposes
            
        Returns:
            bool: True if successfully blacklisted, False otherwise
        """
        if not self.enabled or not self.redis_client:
            logger.warning("Redis blacklist service not available")
            return False
        
        try:
            # Set with expiry (5 minutes for temporary tokens)
            key = f"blacklist:temp:{temporary_token}"
            self.redis_client.setex(key, 300, user_id)  # 5 minutes TTL
            
            logger.info(f"Temporary token blacklisted successfully for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to blacklist temporary token: {e}")
            return False
    
    async def is_temporary_token_blacklisted(self, temporary_token: str) -> bool:
        """
        Check if temporary token is blacklisted in Redis
        
        Args:
            temporary_token: The temporary token to check
            
        Returns:
            bool: True if blacklisted, False otherwise
        """
        if not self.enabled or not self.redis_client:
            logger.warning("Redis blacklist service not available")
            return False
        
        try:
            key = f"blacklist:temp:{temporary_token}"
            return self.redis_client.exists(key) > 0
            
        except Exception as e:
            logger.error(f"Failed to check temporary token blacklist: {e}")
            return False
    
    async def is_refresh_token_blacklisted(self, refresh_token: str) -> bool:
        """
        Check if refresh token is blacklisted in Redis
        
        Args:
            refresh_token: The refresh token to check
            
        Returns:
            bool: True if blacklisted, False otherwise
        """
        if not self.enabled or not self.redis_client:
            logger.warning("Redis blacklist service not available")
            return False
        
        try:
            key = f"blacklist:refresh:{refresh_token}"
            return self.redis_client.exists(key) > 0
            
        except Exception as e:
            logger.error(f"Failed to check refresh token blacklist: {e}")
            return False
    
    async def is_access_token_blacklisted(self, access_token: str) -> bool:
        """
        Check if access token is blacklisted in Redis
        
        Args:
            access_token: The access token to check
            
        Returns:
            bool: True if blacklisted, False otherwise
        """
        if not self.enabled or not self.redis_client:
            logger.warning("Redis blacklist service not available")
            return False
        
        try:
            key = f"blacklist:access:{access_token}"
            return self.redis_client.exists(key) > 0
            
        except Exception as e:
            logger.error(f"Failed to check access token blacklist: {e}")
            return False
    
    async def get_blacklist_stats(self) -> Dict[str, Any]:
        """
        Get blacklist statistics (optimized to avoid expensive KEYS operations)
        
        Returns:
            dict: Statistics about the blacklist
        """
        if not self.enabled or not self.redis_client:
            return {"enabled": False, "error": "Service not available"}
        
        try:
            # Use Redis INFO command instead of KEYS for better performance
            info = self.redis_client.info()
            
            return {
                "enabled": True,
                "redis_connected": True,
                "redis_memory_used": info.get('used_memory_human', 'unknown'),
                "redis_connected_clients": info.get('connected_clients', 0),
                "note": "Detailed token counting disabled for performance (KEYS operations are expensive)"
            }
            
        except Exception as e:
            logger.error(f"Failed to get blacklist stats: {e}")
            return {"enabled": True, "error": str(e)}
    
    async def clear_expired_tokens(self) -> int:
        """
        Clear expired tokens (Redis handles this automatically with TTL)
        Optimized to avoid expensive KEYS operations
        
        Returns:
            int: Number of tokens cleared (always 0 since Redis auto-expires)
        """
        if not self.enabled or not self.redis_client:
            return 0
        
        try:
            # Redis automatically handles TTL expiration, no manual cleanup needed
            # This method is kept for compatibility but doesn't do expensive operations
            logger.info("Redis TTL handles token expiration automatically")
            return 0
            
        except Exception as e:
            logger.error(f"Failed to clear expired tokens: {e}")
            return 0
    
    async def health_check(self) -> Dict[str, Any]:
        """
        Check Redis blacklist service health
        
        Returns:
            dict: Health status information
        """
        if not self.enabled:
            return {
                "status": "disabled",
                "enabled": False,
                "message": "Redis blacklist service is disabled"
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
            
            # Get basic stats
            stats = await self.get_blacklist_stats()
            
            return {
                "status": "healthy",
                "enabled": True,
                "message": "Redis blacklist service is healthy",
                "stats": stats
            }
            
        except Exception as e:
            return {
                "status": "error",
                "enabled": True,
                "message": f"Redis blacklist service error: {str(e)}"
            }


# Create global Redis blacklist service instance
redis_blacklist_service = RedisBlacklistService() 
