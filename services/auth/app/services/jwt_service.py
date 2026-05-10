"""
JWT Token Service for authentication
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from jose import JWTError, jwt
import uuid
import logging
from ..core.config import settings

logger = logging.getLogger(__name__)


class JWTService:
    """JWT token service for authentication"""
    
    def __init__(self):
        self.secret_key = settings.JWT_SECRET_KEY
        self.algorithm = settings.JWT_ALGORITHM
        logger.debug("JWTService initialized")

    @property
    def access_token_expire_minutes(self):
        return settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES

    @property
    def refresh_token_expire_days(self):
        return settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS
    
    def create_access_token(self, data: Dict[str, Any], session_id: Optional[str] = None) -> str:
        """Create access token with session ID"""
        to_encode = data.copy()
        expire = datetime.now() + timedelta(minutes=self.access_token_expire_minutes)
        
        # Generate session ID if not provided
        if session_id is None:
            session_id = str(uuid.uuid4())
        
        to_encode.update({
            "exp": expire, 
            "type": "access",
            "session_id": session_id
        })
        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)
        return encoded_jwt
    
    def create_refresh_token(self, data: Dict[str, Any], session_id: Optional[str] = None) -> str:
        """Create refresh token with session ID"""
        to_encode = data.copy()
        expire = datetime.now() + timedelta(days=self.refresh_token_expire_days)
        
        # Generate session ID if not provided
        if session_id is None:
            session_id = str(uuid.uuid4())
        
        to_encode.update({
            "exp": expire, 
            "type": "refresh",
            "session_id": session_id
        })
        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)
        return encoded_jwt
    
    def create_temporary_token(self, data: Dict[str, Any], expire_minutes: int = 5) -> str:
        """Create temporary token for MFA verification"""
        to_encode = data.copy()
        expire = datetime.now() + timedelta(minutes=expire_minutes)
        to_encode.update({"exp": expire, "type": "temporary"})
        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)
        return encoded_jwt
    
    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify and decode token"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return payload
        except JWTError as e:
            logger.debug("JWT verification failed: %s", e)
            return None
        except Exception as e:
            logger.debug("JWT verification error: %s", e)
            return None
    
    def verify_access_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify access token specifically"""
        payload = self.verify_token(token)
        if payload and payload.get("type") == "access":
            # Check if token is blacklisted
            from .redis_blacklist import redis_blacklist_service
            is_blacklisted = redis_blacklist_service.is_access_token_blacklisted(token)
            
            if is_blacklisted:
                return None
            return payload
        return None
    
    def verify_refresh_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify refresh token specifically"""
        payload = self.verify_token(token)
        
        if payload and payload.get("type") == "refresh":
            # Check if token is blacklisted
            from .redis_blacklist import redis_blacklist_service
            is_blacklisted = redis_blacklist_service.is_refresh_token_blacklisted(token)
            
            if is_blacklisted:
                return None
            return payload
        return None
    
    def refresh_access_token(self, refresh_token: str) -> Optional[str]:
        """Create new access token from refresh token and rotate (blacklist) the used token"""
        payload = self.verify_refresh_token(refresh_token)
        if not payload:
            return None

        # Blacklist the consumed refresh token so it cannot be reused
        from .redis_blacklist import redis_blacklist_service
        redis_blacklist_service.blacklist_refresh_token(refresh_token)

        # Remove type, exp, and non-serializable objects from payload for new access token
        serializable_data = {}
        for k, v in payload.items():
            if k not in ["type", "exp"]:
                # Only include serializable data
                if isinstance(v, (str, int, float, bool, list, dict)) or v is None:
                    serializable_data[k] = v
                elif isinstance(v, datetime):
                    serializable_data[k] = v.isoformat()

        return self.create_access_token(serializable_data)
    
    def verify_temporary_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify temporary token specifically"""
        payload = self.verify_token(token)
        if payload and payload.get("type") == "temporary":
            # Check if token is blacklisted
            from .redis_blacklist import redis_blacklist_service
            is_blacklisted = redis_blacklist_service.is_temporary_token_blacklisted(token)
            
            if is_blacklisted:
                return None
            return payload
        return None
    
    def get_token_expiry(self, token: str) -> Optional[datetime]:
        """Get token expiry time"""
        payload = self.verify_token(token)
        if payload and "exp" in payload:
            return datetime.fromtimestamp(payload["exp"])
        return None
    
    def get_session_id(self, token: str) -> Optional[str]:
        """Get session ID from token"""
        payload = self.verify_token(token)
        if payload and "session_id" in payload:
            return payload["session_id"]
        return None
    
    def get_user_session_info(self, token: str) -> Optional[Dict[str, Any]]:
        """Get user and session information from token"""
        payload = self.verify_access_token(token)
        if payload:
            return {
                "user_id": payload.get("user_id"),
                "username": payload.get("username"),
                "session_id": payload.get("session_id"),
                "roles": payload.get("roles", []),
                "permissions": payload.get("permissions", [])
            }
        return None


# Create global JWT service instance
jwt_service = JWTService() 
