"""
Redis Blacklist Service for JWT Token Invalidation
"""

import hashlib
import redis
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from ..core.config import settings

# Configure logging
logger = logging.getLogger("redis-blacklist")

# Temporary tokens live five minutes and are consumed once, so the whole population at any
# instant is "logins in flight". Small enough to keep a local copy of, which is what lets
# single-use survive a Redis outage instead of silently lapsing.
_TEMP_FALLBACK_TTL = 300
_TEMP_FALLBACK_MAX = 20_000


class _TemporaryTokenFallback:
    """In-process record of consumed temporary tokens, used when Redis is unavailable."""

    def __init__(self):
        self._lock = threading.Lock()
        # sha256 -> expiry. Hashed so a heap dump is not a list of live login tokens.
        self._seen: Dict[str, float] = {}

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def add(self, token: str) -> None:
        now = time.time()
        with self._lock:
            for key in [k for k, exp in self._seen.items() if exp <= now]:
                del self._seen[key]
            if len(self._seen) >= _TEMP_FALLBACK_MAX:
                # Only reachable under an attack or an extreme login storm while Redis is
                # down. Drop the oldest rather than stop recording, which would silently
                # turn single use back off.
                for key in sorted(self._seen, key=self._seen.get)[: _TEMP_FALLBACK_MAX // 2]:
                    del self._seen[key]
            self._seen[self._key(token)] = now + _TEMP_FALLBACK_TTL

    def contains(self, token: str) -> bool:
        now = time.time()
        key = self._key(token)
        with self._lock:
            expiry = self._seen.get(key)
            if expiry is None:
                return False
            if expiry <= now:
                del self._seen[key]
                return False
            return True


class RedisBlacklistService:
    """Redis-based token blacklist service"""

    def __init__(self):
        # `configured` is what the operator asked for; `enabled` is what is actually
        # working. Keeping them apart is what lets callers tell "switched off on purpose"
        # from "supposed to be on and broken" -- the same return value meant both before.
        self.configured = settings.REDIS_BLACKLIST_ENABLED
        self.enabled = settings.REDIS_BLACKLIST_ENABLED
        self.ttl_seconds = settings.REDIS_BLACKLIST_TTL * 24 * 60 * 60
        self._temp_fallback = _TemporaryTokenFallback()

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
    
    def invalidate_refresh_token(self, refresh_token: str, user_id: str) -> bool:
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
    
    def invalidate_access_token(self, access_token: str, user_id: str) -> bool:
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
    
    def invalidate_temporary_token(self, temporary_token: str, user_id: str) -> bool:
        """
        Consume a temporary token so it cannot complete a second login.

        Always records the token in the in-process fallback as well, so single use holds
        within this worker even with no Redis at all.

        Returns:
            True when the token is durably consumed -- either Redis accepted it, or the
            blacklist is switched off by configuration and the local record is all that was
            ever promised.
            **False means degraded**: Redis was supposed to store this and did not, so the
            token is replayable against any *other* worker until it expires. Callers are
            expected to raise the alarm rather than ignore it, which is what
            `AuthService.verify_login` now does.
        """
        self._temp_fallback.add(temporary_token)

        if not self.configured:
            # Deliberately disabled. Nothing is broken and there is nothing to warn about
            # on every login; the operator chose in-process semantics.
            logger.debug("Redis blacklist disabled by configuration; temporary token consumed locally")
            return True

        if not self.enabled or not self.redis_client:
            logger.error(
                "Redis blacklist is enabled in configuration but unavailable -- temporary "
                "token for user %s consumed in-process only", user_id
            )
            return False

        try:
            # Set with expiry (5 minutes for temporary tokens)
            key = f"blacklist:temp:{temporary_token}"
            self.redis_client.setex(key, _TEMP_FALLBACK_TTL, user_id)

            logger.info(f"Temporary token blacklisted successfully for user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to blacklist temporary token: {e}")
            return False

    def is_temporary_token_blacklisted(self, temporary_token: str) -> bool:
        """
        Has this temporary token already been used?

        Consults the in-process record first, so a token consumed by this worker is refused
        even if Redis was unavailable at the moment it was consumed.
        """
        if self._temp_fallback.contains(temporary_token):
            return True

        if not self.enabled or not self.redis_client:
            return False

        try:
            key = f"blacklist:temp:{temporary_token}"
            return self.redis_client.exists(key) > 0

        except Exception as e:
            logger.error(f"Failed to check temporary token blacklist: {e}")
            return False
    
    def is_refresh_token_blacklisted(self, refresh_token: str) -> bool:
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
    
    def is_access_token_blacklisted(self, access_token: str) -> bool:
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
    
    def get_blacklist_stats(self) -> Dict[str, Any]:
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
    
    def clear_expired_tokens(self) -> int:
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
    
    def health_check(self) -> Dict[str, Any]:
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
            stats = self.get_blacklist_stats()
            
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