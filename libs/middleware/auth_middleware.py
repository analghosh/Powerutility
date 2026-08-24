"""
Authentication middleware for JWT token validation using centralized auth service
"""

import logging
import os
import secrets
from contextvars import ContextVar
from typing import Callable, Optional, Dict, Any
from fastapi import Request, Response, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from ..auth_client import AuthClient, AuthValidationResponse, get_auth_client
from ..core.local_auth import local_token_validator
from ..auth.exceptions import AuthenticationError, MissingTokenError, InvalidAuthHeaderError
from .utils import extract_bearer_token, log_request_info
from libs.authz.service_auth import INTERNAL_TOKEN_HEADER
from .correlation import get_correlation_id

logger = logging.getLogger(__name__)


def _local_validation_enabled() -> bool:
    """
    Validate tokens in-process (default) or by HTTP call to the auth service.

    Read per call rather than cached at import so the flag can be flipped by a restart
    alone, and so tests can toggle it without reloading the module.
    """
    return os.getenv("AUTH_LOCAL_VALIDATION", "true").strip().lower() in ("true", "1", "yes")

# Context variable to store current user throughout request lifecycle
_current_user: ContextVar[Optional[Dict[str, Any]]] = ContextVar("current_user", default=None)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware for JWT authentication and user context management
    """
    
    def __init__(
        self,
        app,
        auth_service_url: str = None,
        settings: Any = None,
        auth_client: AuthClient = None,
        exclude_paths: Optional[list] = None,
        require_auth: bool = True,
        internal_token: Optional[str] = None,
    ):
        super().__init__(app)

        # Shared secret for service-to-service calls. The scheduler shipped with this
        # middleware commented out entirely -- "removed to allow internal trigger without
        # authentication" -- because five call sites in the processing service call it
        # with no credentials. Rather than leave the whole service open for them, they now
        # present this header. Unset means the door stays shut: no header is ever accepted.
        self.internal_token = internal_token or None
        # Local validation needs JWT_SECRET_KEY / MONGODB_URI / REDIS_*, and pydantic
        # BaseSettings never writes those into os.environ. See LocalTokenValidator.configure.
        local_token_validator.configure(settings)
        
        # Initialize auth client
        if auth_client:
            self.auth_client = auth_client
        elif auth_service_url:
            self.auth_client = get_auth_client(auth_service_url)
        else:
            raise ValueError("Either auth_client or auth_service_url must be provided")
        
        self.exclude_paths = exclude_paths or [
            "/health",
            "/docs", 
            "/redoc",
            "/openapi.json",
            "/processing_api.json"
        ]
        self.require_auth = require_auth
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process request and validate authentication
        
        Args:
            request: FastAPI request object
            call_next: Next middleware/endpoint in chain
            
        Returns:
            Response after authentication validation
        """
        correlation_id = get_correlation_id() or getattr(request.state, "correlation_id", "unknown")
        
        # Skip authentication for excluded paths
        request_path = request.url.path
        if self._should_skip_auth(request_path):
            logger.debug(f"✅ Skipping auth for path: {request_path} | CorrelationID: {correlation_id}")
            return await call_next(request)
        
        # Log when auth is required (for debugging)
        logger.debug(f"🔒 Auth required for path: {request_path} | CorrelationID: {correlation_id}")
        
        # Service-to-service call: a matching shared secret stands in for a login.
        service_context = self._authenticate_service(request)
        if service_context is not None:
            _current_user.set(service_context)
            request.state.current_user = service_context
            try:
                return await call_next(request)
            finally:
                _current_user.set(None)

        try:
            # Extract and validate JWT token
            user_context = await self._authenticate_request(request)
            
            # Set user context for request lifecycle
            _current_user.set(user_context)
            request.state.current_user = user_context
            
            # Log request info
            log_request_info(request, correlation_id, user_context.get("user_id"))
            
            # Process request
            response = await call_next(request)
            
            return response
            
        except (AuthenticationError, MissingTokenError, InvalidAuthHeaderError) as e:
            logger.warning(f"Authentication failed: {e.detail} | CorrelationID: {correlation_id}")
            # Return proper HTTP 401 response instead of raising exception
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=401,
                content={"detail": e.detail},
                headers={"X-Correlation-ID": correlation_id} if correlation_id != "unknown" else {}
            )
        except Exception as e:
            logger.error(f"Error in auth middleware: {str(e)} | CorrelationID: {correlation_id}")
            if self.require_auth:
                raise HTTPException(status_code=500, detail="Authentication service error")
            else:
                # Continue without authentication if not required
                return await call_next(request)
        finally:
            # Clean up context
            _current_user.set(None)
    
    def _authenticate_service(self, request: Request) -> Optional[Dict[str, Any]]:
        """
        Recognise a trusted internal caller by shared secret.

        Returns a service context, or None to fall through to normal JWT handling.
        Compared with `secrets.compare_digest` so a wrong header cannot be narrowed down
        by timing, and short-circuited when no secret is configured so an empty
        environment variable can never match an empty header.
        """
        if not self.internal_token:
            return None

        presented = request.headers.get(INTERNAL_TOKEN_HEADER)
        if not presented:
            return None

        if not secrets.compare_digest(presented, self.internal_token):
            logger.warning(
                "Rejected %s with a wrong internal token on %s",
                INTERNAL_TOKEN_HEADER, request.url.path,
            )
            return None

        return {
            "user_id": "internal-service",
            "username": "internal-service",
            "email": None,
            "roles": ["service"],
            "permissions": ["*"],
            "session_id": None,
            "token": None,
            "is_service_account": True,
        }

    def _should_skip_auth(self, path: str) -> bool:
        """
        Check if authentication should be skipped for given path
        
        Args:
            path: Request path
            
        Returns:
            True if auth should be skipped, False otherwise
        """
        # Debug logging to help troubleshoot
        logger.debug(f"🔍 Checking auth skip for path: '{path}'")
        logger.debug(f"   Exclude paths: {self.exclude_paths}")
        
        # Normalize path (remove trailing slashes for comparison)
        normalized_path = path.rstrip('/')
        
        for exclude_path in self.exclude_paths:
            # Normalize exclude path
            normalized_exclude = exclude_path.rstrip('/')
            
            # Check if path starts with exclude path
            if normalized_path.startswith(normalized_exclude):
                logger.debug(f"✅ Skipping auth for path '{path}' (matches exclude '{exclude_path}')")
                return True
            
            # Also check if exclude path is contained in the path (for mounted apps)
            if normalized_exclude in normalized_path:
                # Additional check: ensure it's at a path boundary (not just substring)
                if normalized_path == normalized_exclude or normalized_path.startswith(normalized_exclude + '/'):
                    logger.debug(f"✅ Skipping auth for path '{path}' (contains exclude '{exclude_path}')")
                    return True
        
        logger.debug(f"❌ Auth required for path: '{path}' (no exclude match)")
        return False
    
    async def _authenticate_request(self, request: Request) -> Dict[str, Any]:
        """
        Authenticate request and return user context using centralized auth service
        
        Args:
            request: FastAPI request object
            
        Returns:
            User context dictionary
            
        Raises:
            AuthenticationError: If authentication fails
        """
        try:
            # Extract Bearer token from Authorization header
            token = extract_bearer_token(request)
            
            # Validate locally unless explicitly told not to. The remote path is kept as
            # a one-variable rollback (AUTH_LOCAL_VALIDATION=false) because this is the
            # authentication path for every request in the service.
            if _local_validation_enabled():
                payload, reason = await local_token_validator.validate(token)
                if payload is None:
                    # Logged, never returned: telling the caller which check failed
                    # distinguishes "expired" from "deactivated" from "no such account".
                    logger.warning("Token rejected: %s", reason)
                    raise AuthenticationError("Invalid or expired token")

                return {
                    "user_id": payload.get("sub") or payload.get("id"),
                    "username": payload.get("username"),
                    "email": payload.get("email"),
                    "roles": payload.get("roles") or [],
                    "permissions": payload.get("permissions") or [],
                    "session_id": payload.get("session_id"),
                    "token": token,
                }

            # Legacy path: ask the auth service over HTTP. One network round trip, one
            # Mongo read and two audit writes per request, and a timeout here becomes a
            # 401 for the caller rather than a degraded response -- which is how a login
            # storm on auth turned into a platform-wide 401 storm.
            if self.auth_client:
                correlation_id = get_correlation_id()
                validation_result = await self.auth_client.validate_token(token, correlation_id=correlation_id)

                if not validation_result.valid:
                    raise AuthenticationError("Invalid or expired token")

                return {
                    "user_id": validation_result.user_id,
                    "username": validation_result.username,
                    "email": validation_result.email,
                    "roles": validation_result.roles,
                    "permissions": validation_result.permissions,
                    "session_id": validation_result.session_id,
                    "token": token
                }

            # Fallback: If no auth client configured, raise error
            else:
                raise AuthenticationError("Authentication service not configured")
            
        except (MissingTokenError, InvalidAuthHeaderError) as e:
            raise e
        except AuthenticationError as e:
            raise e
        except Exception as e:
            logger.error(f"Unexpected error during authentication: {str(e)}")
            raise AuthenticationError("Authentication failed")


def get_current_user() -> Optional[Dict[str, Any]]:
    """
    Get current authenticated user from context
    
    Returns:
        Current user context or None if not authenticated
    """
    return _current_user.get()


def require_authentication():
    """
    Dependency to require authentication for an endpoint
    
    Returns:
        Current user context
        
    Raises:
        HTTPException: If user is not authenticated
    """
    user = get_current_user()
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def create_auth_middleware(
    auth_service_url: str,
    exclude_paths: Optional[list] = None,
    require_auth: bool = True
) -> type:
    """
    Factory function to create auth middleware with auth service URL
    
    Args:
        auth_service_url: URL of the auth service
        exclude_paths: Paths to exclude from authentication
        require_auth: Whether authentication is required
        
    Returns:
        Configured AuthMiddleware class
    """
    class ConfiguredAuthMiddleware(AuthMiddleware):
        def __init__(self, app):
            super().__init__(
                app=app,
                auth_service_url=auth_service_url,
                exclude_paths=exclude_paths,
                require_auth=require_auth
            )
    
    return ConfiguredAuthMiddleware


# Convenience function for getting current user
def get_current_user() -> Optional[Dict[str, Any]]:
    """
    Get current authenticated user from context
    
    Returns:
        Current user context or None if not authenticated
    """
    return _current_user.get()


# Convenience function for checking permissions
def has_permission(permission: str) -> bool:
    """
    Check if current user has a specific permission
    
    Args:
        permission: Permission to check (e.g., "users:read")
        
    Returns:
        True if user has permission, False otherwise
    """
    user = get_current_user()
    if not user:
        return False
    
    permissions = user.get("permissions", [])
    return permission in permissions or "*" in permissions


# Convenience function for checking roles
def has_role(role: str) -> bool:
    """
    Check if current user has a specific role
    
    Args:
        role: Role to check (e.g., "admin")
        
    Returns:
        True if user has role, False otherwise
    """
    user = get_current_user()
    if not user:
        return False
    
    roles = user.get("roles", [])
    return role in roles


def get_user_context(request: Request) -> Dict[str, Any]:
    """
    Dependency to get user context with correlation ID from request state
    
    Returns:
        Dictionary containing user info and correlation ID
    """
    user = getattr(request.state, "current_user", None)
    
    # Try getting from ContextVar if not in request state (fallback)
    if not user:
        user = get_current_user()
        
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
        
    correlation_id = getattr(request.state, "correlation_id", "unknown")
    if correlation_id == "unknown":
        from .correlation import get_correlation_id
        correlation_id = get_correlation_id() or "unknown"
    
    return {
        "user": user,
        "correlation_id": correlation_id,
        "user_id": user.get("id") or user.get("user_id"),
        "username": user.get("username"),
        "roles": user.get("roles", []),
        "permissions": user.get("permissions", [])
    }