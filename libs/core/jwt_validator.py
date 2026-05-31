"""
JWT Validator for common JWT validation across services
"""

import jwt
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple, List
from dataclasses import dataclass

# NEW: Gracefully handle optional Redis dependency
try:
    from redis import Redis  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    class _DummyRedis:  # pylint: disable=too-few-public-methods
        """Fallback no-op Redis client used when the redis package is unavailable.

        This avoids ImportError during unit-test discovery on machines that don’t
        have *redis-py* installed. We only implement the subset of methods used
        inside *JWTValidator*.
        """

        def __init__(self, *args, **kwargs):
            pass

        # The blacklist is represented by a Redis *set* named "jwt_blacklist" in
        # production. For tests, we simply return the default values that keep
        # validation logic working without a live Redis server.
        def sismember(self, *_args, **_kwargs):  # noqa: D401,E501
            return False

        def sadd(self, *_args, **_kwargs):  # noqa: D401
            return 1

        # Added: mimic Redis.from_url constructor used elsewhere in this file
        @classmethod
        def from_url(cls, *_args, **_kwargs):  # noqa: D401
            return cls()

    Redis = _DummyRedis  # type: ignore[assignment]

logger = logging.getLogger(__name__)


@dataclass
class TokenPayload:
    """Token payload structure"""
    user_id: str
    username: str
    email: str
    roles: List[str]
    permissions: List[str]
    token_type: str
    exp: datetime


@dataclass
class UserInfo:
    """User information from token"""
    user_id: str
    username: str
    email: str
    roles: List[str]
    permissions: List[str]


class JWTValidator:
    """Common JWT validator for all services"""
    
    def __init__(self):
        # Import settings from the auth service for now
        # In the future, this should be a common config
        try:
            from services.auth.app.core.config import settings
        except ImportError:
            # Fallback for testing
            class MockSettings:
                JWT_SECRET_KEY = "test-secret-key"
                JWT_ALGORITHM = "HS256"
                ACCESS_TOKEN_EXPIRE_MINUTES = 30
                REDIS_URL = "redis://localhost:6379/0"
            settings = MockSettings()
        
        self.secret_key = settings.JWT_SECRET_KEY
        self.algorithm = settings.JWT_ALGORITHM
        self.redis_client = Redis.from_url(settings.REDIS_URL)
        self.access_token_expire_minutes = settings.ACCESS_TOKEN_EXPIRE_MINUTES
    
    def validate_token(self, token: str) -> Optional[TokenPayload]:
        """Validate JWT token and return payload"""
        try:
            # Decode token — verify signature, expiry, and nbf if present
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                options={"verify_exp": True, "verify_nbf": True},
            )
            
            # Check if token is blacklisted
            if self.is_token_blacklisted(token):
                logger.warning(f"Token blacklisted: {token[:20]}...")
                return None
            
            # Check expiration
            if payload.get("exp") < datetime.now().timestamp():
                logger.warning(f"Token expired: {token[:20]}...")
                return None
            
            # Create TokenPayload
            return TokenPayload(
                user_id=payload["sub"],
                username=payload["username"],
                email=payload["email"],
                roles=payload["roles"],
                permissions=payload["permissions"],
                token_type=payload["type"],
                exp=datetime.fromtimestamp(payload["exp"])
            )
            
        except jwt.ExpiredSignatureError:
            logger.warning(f"Token expired: {token[:20]}...")
            return None
        except jwt.InvalidTokenError:
            logger.warning(f"Invalid token: {token[:20]}...")
            return None
        except Exception as e:
            logger.error(f"Token validation error: {e}")
            return None
    
    def validate_token_with_details(self, token: str) -> Tuple[Optional[TokenPayload], Optional[str]]:
        """Validate JWT token and return payload with error details"""
        try:
            # Decode token — verify signature, expiry, and nbf if present
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                options={"verify_exp": True, "verify_nbf": True},
            )
            
            # Check if token is blacklisted
            if self.is_token_blacklisted(token):
                return None, "Token is blacklisted"
            
            # Check expiration
            if payload.get("exp") < datetime.now().timestamp():
                return None, "Token has expired"
            
            # Create TokenPayload
            token_payload = TokenPayload(
                user_id=payload["sub"],
                username=payload["username"],
                email=payload["email"],
                roles=payload["roles"],
                permissions=payload["permissions"],
                token_type=payload["type"],
                exp=datetime.fromtimestamp(payload["exp"])
            )
            
            return token_payload, None
            
        except jwt.ExpiredSignatureError:
            return None, "Token has expired"
        except jwt.InvalidTokenError:
            return None, "Invalid token"
        except Exception as e:
            logger.error(f"Token validation error: {e}")
            return None, f"Validation error: {str(e)}"
    
    def get_user_from_token(self, token: str) -> Optional[UserInfo]:
        """Extract user information from token"""
        payload = self.validate_token(token)
        if not payload:
            return None
        
        return UserInfo(
            user_id=payload.user_id,
            username=payload.username,
            email=payload.email,
            roles=payload.roles,
            permissions=payload.permissions
        )
    
    def has_permission(self, token: str, permission: str) -> bool:
        """Check if token has specific permission"""
        payload = self.validate_token(token)
        if not payload:
            return False
        
        return permission in payload.permissions
    
    def has_role(self, token: str, role: str) -> bool:
        """Check if token has specific role"""
        payload = self.validate_token(token)
        if not payload:
            return False
        
        return role in payload.roles
    
    def has_any_role(self, token: str, roles: List[str]) -> bool:
        """Check if token has any of the specified roles"""
        payload = self.validate_token(token)
        if not payload:
            return False
        
        return any(role in payload.roles for role in roles)
    
    def is_token_blacklisted(self, token: str) -> bool:
        """Check if token is blacklisted in Redis"""
        try:
            return bool(self.redis_client.sismember("jwt_blacklist", token))
        except Exception as e:
            logger.error(f"Redis blacklist check error: {e}")
            return False
    
    def is_token_expired(self, token: str) -> bool:
        """Check if token is expired"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return payload.get("exp", 0) < datetime.now().timestamp()
        except Exception:
            return True  # Consider invalid tokens as expired
    
    def blacklist_token(self, token: str) -> bool:
        """Add token to blacklist"""
        try:
            self.redis_client.sadd("jwt_blacklist", token)
            return True
        except Exception as e:
            logger.error(f"Redis blacklist error: {e}")
            return False


# Global instance
jwt_validator = JWTValidator() 