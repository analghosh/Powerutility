"""
FastAPI Dependencies for JWT Validation and Database Connection
Provides reusable dependencies for JWT authentication and database access across all services
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional, List
import os
from .jwt_validator import JWTValidator, TokenPayload

# Security scheme
security = HTTPBearer()

# Global JWT validator instance
jwt_validator = JWTValidator()

# Async Database connection
_async_client = None

async def get_async_mongo_client():
    """Get async MongoDB client instance with connection pooling"""
    global _async_client
    if _async_client is None:
        from pymongo import AsyncMongoClient
        mongodb_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        _async_client = AsyncMongoClient(
            mongodb_uri,
            maxPoolSize=10,  # Limit connection pool
            minPoolSize=2,
            maxIdleTimeMS=45000,
            serverSelectionTimeoutMS=5000
        )
    return _async_client

async def get_db():
    """
    FastAPI dependency for async MongoDB database connection
    
    Returns:
        Database: Async MongoDB database instance
    """
    client = await get_async_mongo_client()
    db_name = os.getenv("AUTH_DB_NAME", "auth_db")
    return client[db_name]

# Synchronous database connection for backward compatibility
_sync_client = None

def get_sync_mongo_client():
    """Get synchronous MongoDB client instance with connection pooling"""
    global _sync_client
    if _sync_client is None:
        from pymongo import MongoClient
        mongodb_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        _sync_client = MongoClient(
            mongodb_uri,
            maxPoolSize=10,  # Limit connection pool
            minPoolSize=2,
            maxIdleTimeMS=45000,
            serverSelectionTimeoutMS=5000
        )
    return _sync_client

def get_sync_db():
    """
    FastAPI dependency for synchronous MongoDB database connection
    
    Returns:
        Database: Synchronous MongoDB database instance
    """
    client = get_sync_mongo_client()
    db_name = os.getenv("AUTH_DB_NAME", "auth_db")
    return client[db_name]


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> TokenPayload:
    """
    Dependency for JWT validation - returns 401 Unauthorized on failure
    
    Args:
        credentials: HTTP Bearer token from Authorization header
        
    Returns:
        TokenPayload: Valid token payload
        
    Raises:
        HTTPException: 401 Unauthorized if token is invalid
    """
    payload = jwt_validator.validate_token(credentials.credentials)
    
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )
    
    return payload


def get_current_user_with_details(credentials: HTTPAuthorizationCredentials = Depends(security)) -> tuple[TokenPayload, Optional[str]]:
    """
    Dependency for JWT validation with detailed error messages
    
    Args:
        credentials: HTTP Bearer token from Authorization header
        
    Returns:
        Tuple of (TokenPayload, error_reason)
        
    Raises:
        HTTPException: 401 Unauthorized if token is invalid
    """
    payload, error_reason = jwt_validator.validate_token_with_details(credentials.credentials)
    
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_reason or "Invalid or expired token"
        )
    
    return payload, None


def get_current_user_with_roles(required_roles: List[str]):
    """
    Dependency factory for JWT validation with role checking
    
    Args:
        required_roles: List of roles that are allowed
        
    Returns:
        Dependency function that validates JWT and checks roles
    """
    def dependency(credentials: HTTPAuthorizationCredentials = Depends(security)) -> TokenPayload:
        payload = jwt_validator.validate_token(credentials.credentials)
        
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token"
            )
        
        # Check if user has any of the required roles
        if not any(role in payload.roles for role in required_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required roles: {required_roles}"
            )
        
        return payload
    
    return dependency


def get_current_user_with_permissions(required_permissions: List[str]):
    """
    Dependency factory for JWT validation with permission checking
    
    Args:
        required_permissions: List of permissions that are required
        
    Returns:
        Dependency function that validates JWT and checks permissions
    """
    def dependency(credentials: HTTPAuthorizationCredentials = Depends(security)) -> TokenPayload:
        payload = jwt_validator.validate_token(credentials.credentials)
        
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token"
            )
        
        # Check if user has all required permissions
        if not all(permission in payload.permissions for permission in required_permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required permissions: {required_permissions}"
            )
        
        return payload
    
    return dependency


def get_current_user_with_any_permission(required_permissions: List[str]):
    """
    Dependency factory for JWT validation with any permission checking
    
    Args:
        required_permissions: List of permissions, user needs at least one
        
    Returns:
        Dependency function that validates JWT and checks permissions
    """
    def dependency(credentials: HTTPAuthorizationCredentials = Depends(security)) -> TokenPayload:
        payload = jwt_validator.validate_token(credentials.credentials)
        
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token"
            )
        
        # Check if user has any of the required permissions
        if not any(permission in payload.permissions for permission in required_permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required permissions: {required_permissions}"
            )
        
        return payload
    
    return dependency


def get_optional_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> Optional[TokenPayload]:
    """
    Optional JWT validation - doesn't raise exception if token is missing/invalid
    
    Args:
        credentials: HTTP Bearer token from Authorization header (optional)
        
    Returns:
        TokenPayload if valid token, None if invalid/missing
    """
    if not credentials:
        return None
    
    return jwt_validator.validate_token(credentials.credentials)


# Predefined role-based dependencies
require_admin = get_current_user_with_roles(["admin"])
require_user = get_current_user_with_roles(["user", "admin"])
require_operator = get_current_user_with_roles(["operator", "admin"])

# Predefined permission-based dependencies
require_read_permission = get_current_user_with_permissions(["read"])
require_write_permission = get_current_user_with_permissions(["write"])
require_delete_permission = get_current_user_with_permissions(["delete"])
require_any_data_permission = get_current_user_with_any_permission(["read", "write", "delete"])

# Export public functions
__all__ = [
    "get_db",
    "get_current_user",
    "get_current_user_with_details",
    "get_current_user_with_roles",
    "get_current_user_with_permissions",
    "get_current_user_with_any_permission",
    "get_optional_user",
    "require_admin",
    "require_user", 
    "require_operator",
    "require_read_permission",
    "require_write_permission",
    "require_delete_permission",
    "require_any_data_permission",
    "security",
    "jwt_validator"
] 