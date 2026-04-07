"""
JWT Authentication Core Layer
Provides JWT token generation, validation, and user authentication for all services
"""

import jwt
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Union
from enum import Enum
from dataclasses import dataclass
from pydantic import BaseModel
import logging

logger = logging.getLogger("auth_core")


class TokenType(str, Enum):
    """Token types"""
    ACCESS = "access"
    REFRESH = "refresh"
    API = "api"


class UserRole(str, Enum):
    """User roles"""
    ADMIN = "admin"
    USER = "user"
    VIEWER = "viewer"
    OPERATOR = "operator"


@dataclass
class TokenPayload:
    """Token payload structure"""
    user_id: str
    username: str
    email: str
    roles: list[str]
    permissions: list[str]
    token_type: TokenType
    exp: int
    iat: int
    jti: str


class JWTTokenManager:
    """JWT Token Manager for authentication across services"""
    
    def __init__(self, secret_key: str, algorithm: str = "HS256"):
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.access_token_expire_minutes = 30
        self.refresh_token_expire_days = 7
        self.api_token_expire_hours = 24
        
    def create_token_payload(
        self,
        user_id: str,
        username: str,
        email: str,
        roles: list[str],
        permissions: list[str],
        token_type: TokenType,
        expires_in: Optional[int] = None
    ) -> TokenPayload:
        """Create token payload"""
        now = int(time.time())
        
        if expires_in is None:
            if token_type == TokenType.ACCESS:
                expires_in = self.access_token_expire_minutes * 60
            elif token_type == TokenType.REFRESH:
                expires_in = self.refresh_token_expire_days * 24 * 60 * 60
            elif token_type == TokenType.API:
                expires_in = self.api_token_expire_hours * 60 * 60
        
        return TokenPayload(
            user_id=user_id,
            username=username,
            email=email,
            roles=roles,
            permissions=permissions,
            token_type=token_type,
            exp=now + expires_in,
            iat=now,
            jti=f"{user_id}_{token_type}_{now}"
        )
    
    def encode_token(self, payload: TokenPayload) -> str:
        """Encode token payload to JWT string"""
        try:
            token_data = {
                "user_id": payload.user_id,
                "username": payload.username,
                "email": payload.email,
                "roles": payload.roles,
                "permissions": payload.permissions,
                "token_type": payload.token_type,
                "exp": payload.exp,
                "iat": payload.iat,
                "jti": payload.jti
            }
            return jwt.encode(token_data, self.secret_key, algorithm=self.algorithm)
        except Exception as e:
            logger.error(f"Failed to encode token: {e}")
            raise
    
    def decode_token(self, token: str) -> Optional[TokenPayload]:
        """Decode JWT token to payload"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return TokenPayload(
                user_id=payload["user_id"],
                username=payload["username"],
                email=payload["email"],
                roles=payload["roles"],
                permissions=payload["permissions"],
                token_type=TokenType(payload["token_type"]),
                exp=payload["exp"],
                iat=payload["iat"],
                jti=payload["jti"]
            )
        except jwt.ExpiredSignatureError:
            logger.warning("Token has expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.error(f"Invalid token: {e}")
            return None
        except Exception as e:
            logger.error(f"Token decode error: {e}")
            return None
    
    def is_token_expired(self, payload: TokenPayload) -> bool:
        """Check if token is expired"""
        return int(time.time()) > payload.exp
    
    def create_access_token(
        self,
        user_id: str,
        username: str,
        email: str,
        roles: list[str],
        permissions: list[str]
    ) -> str:
        """Create access token"""
        payload = self.create_token_payload(
            user_id=user_id,
            username=username,
            email=email,
            roles=roles,
            permissions=permissions,
            token_type=TokenType.ACCESS
        )
        return self.encode_token(payload)
    
    def create_refresh_token(
        self,
        user_id: str,
        username: str,
        email: str,
        roles: list[str],
        permissions: list[str]
    ) -> str:
        """Create refresh token"""
        payload = self.create_token_payload(
            user_id=user_id,
            username=username,
            email=email,
            roles=roles,
            permissions=permissions,
            token_type=TokenType.REFRESH
        )
        return self.encode_token(payload)
    
    def create_api_token(
        self,
        user_id: str,
        username: str,
        email: str,
        roles: list[str],
        permissions: list[str]
    ) -> str:
        """Create API token"""
        payload = self.create_token_payload(
            user_id=user_id,
            username=username,
            email=email,
            roles=roles,
            permissions=permissions,
            token_type=TokenType.API
        )
        return self.encode_token(payload)


class AuthMiddleware:
    """Authentication middleware for FastAPI applications"""
    
    def __init__(self, token_manager: JWTTokenManager):
        self.token_manager = token_manager
    
    def extract_token_from_header(self, authorization: str) -> Optional[str]:
        """Extract token from Authorization header"""
        if not authorization:
            return None
        
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        
        return parts[1]
    
    def validate_token(self, token: str) -> Optional[TokenPayload]:
        """Validate JWT token"""
        if not token:
            return None
        
        payload = self.token_manager.decode_token(token)
        if not payload:
            return None
        
        if self.token_manager.is_token_expired(payload):
            return None
        
        return payload
    
    def has_permission(self, payload: TokenPayload, required_permission: str) -> bool:
        """Check if user has required permission"""
        return required_permission in payload.permissions
    
    def has_role(self, payload: TokenPayload, required_role: str) -> bool:
        """Check if user has required role"""
        return required_role in payload.roles
    
    def has_any_role(self, payload: TokenPayload, required_roles: list[str]) -> bool:
        """Check if user has any of the required roles"""
        return any(role in payload.roles for role in required_roles)


class AuthDependency:
    """FastAPI dependency for authentication"""
    
    def __init__(self, token_manager: JWTTokenManager, required_permissions: Optional[list[str]] = None):
        self.token_manager = token_manager
        self.required_permissions = required_permissions or []
        self.middleware = AuthMiddleware(token_manager)
    
    def __call__(self, authorization: str = None) -> TokenPayload:
        """FastAPI dependency callable"""
        if not authorization:
            raise ValueError("Authorization header required")
        
        token = self.middleware.extract_token_from_header(authorization)
        if not token:
            raise ValueError("Invalid authorization header")
        
        payload = self.middleware.validate_token(token)
        if not payload:
            raise ValueError("Invalid or expired token")
        
        # Check permissions
        for permission in self.required_permissions:
            if not self.middleware.has_permission(payload, permission):
                raise ValueError(f"Missing required permission: {permission}")
        
        return payload


# Pydantic models for API responses
class TokenResponse(BaseModel):
    """Token response model"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    username: str
    email: str
    roles: list[str]


class UserInfo(BaseModel):
    """User information model"""
    user_id: str
    username: str
    email: str
    roles: list[str]
    permissions: list[str]
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime] = None


class AuthError(BaseModel):
    """Authentication error model"""
    error: str
    error_description: str
    error_code: str


# Global token manager instance (to be configured per service)
token_manager = None


def init_token_manager(secret_key: str, algorithm: str = "HS256") -> JWTTokenManager:
    """Initialize global token manager"""
    global token_manager
    token_manager = JWTTokenManager(secret_key, algorithm)
    return token_manager


def get_token_manager() -> JWTTokenManager:
    """Get global token manager"""
    if token_manager is None:
        raise RuntimeError("Token manager not initialized. Call init_token_manager() first.")
    return token_manager 