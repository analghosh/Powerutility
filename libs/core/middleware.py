"""
FastAPI Authentication Middleware
Provides authentication middleware for all services
"""

from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional, List
import logging

from .auth import AuthDependency, TokenPayload, get_token_manager

logger = logging.getLogger("auth_middleware")

# Security scheme
security = HTTPBearer(auto_error=False)


class AuthMiddleware:
    """Authentication middleware for FastAPI applications"""
    
    def __init__(self, required_permissions: Optional[List[str]] = None):
        self.required_permissions = required_permissions or []
        self.token_manager = get_token_manager()
        self.auth_dependency = AuthDependency(self.token_manager, self.required_permissions)
    
    async def __call__(self, request: Request) -> TokenPayload:
        """Middleware callable"""
        try:
            # Extract authorization header
            authorization = request.headers.get("Authorization")
            if not authorization:
                raise HTTPException(
                    status_code=401,
                    detail="Authorization header required"
                )
            
            # Validate token
            payload = self.auth_dependency(authorization)
            if not payload:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid or expired token"
                )
            
            # Add user info to request state
            request.state.user = payload
            request.state.user_id = payload.user_id
            request.state.username = payload.username
            request.state.roles = payload.roles
            request.state.permissions = payload.permissions
            
            return payload
            
        except ValueError as e:
            raise HTTPException(status_code=401, detail=str(e))
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            raise HTTPException(status_code=500, detail="Authentication failed")


def require_auth(required_permissions: Optional[List[str]] = None):
    """Decorator for requiring authentication"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # This will be handled by the middleware
            return await func(*args, **kwargs)
        return wrapper
    return decorator


def require_role(required_roles: List[str]):
    """Decorator for requiring specific roles"""
    def decorator(func):
        async def wrapper(request: Request, *args, **kwargs):
            user = getattr(request.state, 'user', None)
            if not user:
                raise HTTPException(status_code=401, detail="Authentication required")
            
            if not any(role in user.roles for role in required_roles):
                raise HTTPException(
                    status_code=403,
                    detail=f"Required roles: {required_roles}"
                )
            
            return await func(request, *args, **kwargs)
        return wrapper
    return decorator


def require_permission(required_permissions: List[str]):
    """Decorator for requiring specific permissions"""
    def decorator(func):
        async def wrapper(request: Request, *args, **kwargs):
            user = getattr(request.state, 'user', None)
            if not user:
                raise HTTPException(status_code=401, detail="Authentication required")
            
            if not all(permission in user.permissions for permission in required_permissions):
                raise HTTPException(
                    status_code=403,
                    detail=f"Required permissions: {required_permissions}"
                )
            
            return await func(request, *args, **kwargs)
        return wrapper
    return decorator


# FastAPI dependency functions
async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> TokenPayload:
    """Get current authenticated user"""
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Authorization header required"
        )
    
    token_manager = get_token_manager()
    auth_dependency = AuthDependency(token_manager)
    
    try:
        return auth_dependency(credentials.credentials)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[TokenPayload]:
    """Get current user (optional - doesn't raise error if not authenticated)"""
    if not credentials:
        return None
    
    token_manager = get_token_manager()
    auth_dependency = AuthDependency(token_manager)
    
    try:
        return auth_dependency(credentials.credentials)
    except ValueError:
        return None


def require_roles(required_roles: List[str]):
    """Dependency for requiring specific roles"""
    async def role_checker(current_user: TokenPayload = Depends(get_current_user)):
        if not any(role in current_user.roles for role in required_roles):
            raise HTTPException(
                status_code=403,
                detail=f"Required roles: {required_roles}"
            )
        return current_user
    return role_checker


def require_permissions(required_permissions: List[str]):
    """Dependency for requiring specific permissions"""
    async def permission_checker(current_user: TokenPayload = Depends(get_current_user)):
        if not all(permission in current_user.permissions for permission in required_permissions):
            raise HTTPException(
                status_code=403,
                detail=f"Required permissions: {required_permissions}"
            )
        return current_user
    return permission_checker


# Utility functions
def get_user_from_request(request: Request) -> Optional[TokenPayload]:
    """Get user from request state"""
    return getattr(request.state, 'user', None)


def get_user_id_from_request(request: Request) -> Optional[str]:
    """Get user ID from request state"""
    return getattr(request.state, 'user_id', None)


def get_username_from_request(request: Request) -> Optional[str]:
    """Get username from request state"""
    return getattr(request.state, 'username', None)


def get_roles_from_request(request: Request) -> List[str]:
    """Get roles from request state"""
    return getattr(request.state, 'roles', [])


def get_permissions_from_request(request: Request) -> List[str]:
    """Get permissions from request state"""
    return getattr(request.state, 'permissions', []) 
