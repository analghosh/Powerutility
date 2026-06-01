"""
Auth Client for JWT Validation
Provides a unified interface for all services to validate tokens with the auth service
"""

import httpx
import asyncio
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import logging
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

class AuthValidationResponse:
    """Response from auth service token validation"""
    
    def __init__(self, data: Dict[str, Any]):
        self.valid = data.get("valid", False)
        self.user_id = data.get("user_id")
        self.username = data.get("username")
        self.email = data.get("email")
        self.roles = data.get("roles", [])
        self.permissions = data.get("permissions", [])
        self.expires_at = data.get("expires_at")
        self.session_id = data.get("session_id")
        self.cached = data.get("cached", False)
    
    def has_permission(self, permission: str) -> bool:
        """Check if user has a specific permission"""
        return permission in self.permissions or "*" in self.permissions
    
    def has_any_permission(self, permissions: List[str]) -> bool:
        """Check if user has any of the specified permissions"""
        return any(self.has_permission(perm) for perm in permissions)
    
    def has_all_permissions(self, permissions: List[str]) -> bool:
        """Check if user has all of the specified permissions"""
        return all(self.has_permission(perm) for perm in permissions)
    
    def has_role(self, role: str) -> bool:
        """Check if user has a specific role"""
        return role in self.roles
    
    def has_any_role(self, roles: List[str]) -> bool:
        """Check if user has any of the specified roles"""
        return any(self.has_role(role) for role in roles)


class AuthClient:
    """
    Client for auth service communication
    Handles token validation, user info retrieval, and caching
    """
    
    def __init__(self, auth_service_url: str, timeout: float = 10.0):
        """
        Initialize the auth client
        
        Args:
            auth_service_url: Base URL of the auth service (e.g., "http://auth-service:8001")
            timeout: Request timeout in seconds
        """
        self.auth_service_url = auth_service_url.rstrip('/')
        self.timeout = timeout
        self.session = None
        
        # No caching - always validate with auth service for security
    
    async def __aenter__(self):
        """Async context manager entry"""
        self.session = httpx.AsyncClient(timeout=self.timeout)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.aclose()
    
    # Caching methods removed for security - always validate fresh
    
    async def validate_token(self, token: str, correlation_id: Optional[str] = None) -> AuthValidationResponse:
        """
        Validate JWT token with auth service (no caching for security)
        
        Args:
            token: JWT token to validate
            correlation_id: Optional correlation ID for request tracing
            
        Returns:
            AuthValidationResponse with validation result and user info
        """
        try:
            
            # Make request to auth service
            url = urljoin(self.auth_service_url, "/api/v1/token/validate")
            headers = {"Authorization": f"Bearer {token}"}
            
            # Debug logging for token validation
            logger.info(f"🔍 AUTH DEBUG - Validating token with auth service: {self.auth_service_url}")
            logger.info(f"🔍 AUTH DEBUG - Token preview: {token[:20]}...")
            
            if not self.session:
                self.session = httpx.AsyncClient(
                    timeout=self.timeout,
                    verify=False  # Temporarily disable SSL verification for debugging
                )
            
            start_time = time.time()
            response = await self.session.post(url, headers=headers)
            request_duration = time.time() - start_time
            
            logger.info(f"🔍 AUTH DEBUG - Auth service response: {response.status_code} ({request_duration:.2f}s)")
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"🔍 AUTH DEBUG - Validation successful for user: {data.get('username', 'unknown')}")
                return AuthValidationResponse(data)
            else:
                logger.error(f"🔍 AUTH DEBUG - Auth service error: {response.status_code}")
                logger.error(f"🔍 AUTH DEBUG - Response body: {response.text}")
                return AuthValidationResponse({"valid": False})
                
        except httpx.TimeoutException as e:
            logger.error(f"🔍 AUTH DEBUG - Timeout connecting to auth service: {str(e)}")
            logger.error(f"🔍 AUTH DEBUG - Timeout value: {self.timeout}s")
            return AuthValidationResponse({"valid": False})
        except httpx.ConnectError as e:
            logger.error(f"🔍 AUTH DEBUG - Failed to connect to auth service: {str(e)}")
            logger.error(f"🔍 AUTH DEBUG - Auth service URL: {self.auth_service_url}")
            return AuthValidationResponse({"valid": False})
        except httpx.HTTPStatusError as e:
            logger.error(f"🔍 AUTH DEBUG - HTTP status error: {str(e)}")
            logger.error(f"🔍 AUTH DEBUG - Response: {e.response.text if hasattr(e, 'response') else 'No response'}")
            return AuthValidationResponse({"valid": False})
        except Exception as e:
            logger.error(f"🔍 AUTH DEBUG - Unexpected error validating token: {str(e)}")
            logger.error(f"🔍 AUTH DEBUG - Error type: {type(e).__name__}")
            import traceback
            logger.error(f"🔍 AUTH DEBUG - Traceback: {traceback.format_exc()}")
            return AuthValidationResponse({"valid": False})
    
    async def get_user_info(self, user_id: str, auth_token: str, correlation_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get detailed user information by user ID
        
        Args:
            user_id: User ID to retrieve information for
            auth_token: Valid JWT token for authentication
            correlation_id: Optional correlation ID for request tracing
            
        Returns:
            User information dictionary or None if not found
        """
        try:
            url = urljoin(self.auth_service_url, f"/api/v1/token/user-info/{user_id}")
            headers = {"Authorization": f"Bearer {auth_token}"}
            
            # Add correlation ID if provided
            if correlation_id:
                headers["X-Correlation-ID"] = correlation_id
            
            if not self.session:
                self.session = httpx.AsyncClient(timeout=self.timeout)
            
            response = await self.session.get(url, headers=headers)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                logger.warning(f"User {user_id} not found")
                return None
            else:
                logger.warning(f"Auth service returned status {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting user info: {str(e)}")
            return None
    
    async def invalidate_user_cache(self, user_id: str, auth_token: str, correlation_id: Optional[str] = None) -> bool:
        """
        Invalidate cached user data in auth service
        
        Args:
            user_id: User ID to invalidate cache for
            auth_token: Valid JWT token for authentication
            correlation_id: Optional correlation ID for request tracing
            
        Returns:
            True if successful, False otherwise
        """
        try:
            url = urljoin(self.auth_service_url, f"/api/v1/token/invalidate-cache/{user_id}")
            headers = {"Authorization": f"Bearer {auth_token}"}
            
            # Add correlation ID if provided
            if correlation_id:
                headers["X-Correlation-ID"] = correlation_id
            
            if not self.session:
                self.session = httpx.AsyncClient(timeout=self.timeout)
            
            response = await self.session.post(url, headers=headers)
            
            if response.status_code == 200:
                logger.info(f"Cache invalidated for user {user_id}")
                return True
            else:
                logger.warning(f"Failed to invalidate cache: status {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Error invalidating cache: {str(e)}")
            return False
    
    # Cache methods removed - no caching for security


# Global client instance
_auth_client = None

def get_auth_client(auth_service_url: str = None) -> AuthClient:
    """
    Get global auth client instance
    
    Args:
        auth_service_url: Auth service URL (required on first call)
        
    Returns:
        AuthClient instance
    """
    global _auth_client
    
    if _auth_client is None:
        if not auth_service_url:
            raise ValueError("auth_service_url is required for first initialization")
        _auth_client = AuthClient(auth_service_url)
    
    return _auth_client


# Convenience functions for common operations
async def validate_token_simple(token: str, auth_service_url: str = None, correlation_id: Optional[str] = None) -> AuthValidationResponse:
    """
    Simple token validation function (no caching)
    
    Args:
        token: JWT token to validate
        auth_service_url: Auth service URL (optional if client already initialized)
        correlation_id: Optional correlation ID for request tracing
        
    Returns:
        AuthValidationResponse with validation result
    """
    client = get_auth_client(auth_service_url)
    async with client:
        return await client.validate_token(token, correlation_id=correlation_id)


async def check_user_permission(token: str, permission: str, auth_service_url: str = None, correlation_id: Optional[str] = None) -> bool:
    """
    Check if user has a specific permission
    
    Args:
        token: JWT token
        permission: Permission to check (e.g., "users:read")
        auth_service_url: Auth service URL (optional if client already initialized)
        correlation_id: Optional correlation ID for request tracing
        
    Returns:
        True if user has permission, False otherwise
    """
    validation_result = await validate_token_simple(token, auth_service_url, correlation_id)
    return validation_result.valid and validation_result.has_permission(permission)


async def check_user_role(token: str, role: str, auth_service_url: str = None, correlation_id: Optional[str] = None) -> bool:
    """
    Check if user has a specific role
    
    Args:
        token: JWT token
        role: Role to check (e.g., "admin")
        auth_service_url: Auth service URL (optional if client already initialized)
        correlation_id: Optional correlation ID for request tracing
        
    Returns:
        True if user has role, False otherwise
    """
    validation_result = await validate_token_simple(token, auth_service_url, correlation_id)
    return validation_result.valid and validation_result.has_role(role)