"""
Token validation routes for service-to-service communication
"""

from fastapi import APIRouter, HTTPException, status, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Dict, Any, Optional
from pydantic import BaseModel
import time
from datetime import datetime, timedelta

from ....services.jwt_service import jwt_service
from ....services.redis_blacklist import redis_blacklist_service
from ....core.dto import AuthUserDTO
from ....controllers.auth_controller import auth_controller

# Import secure audit logging
from ....core.dependencies import get_audit_logger

router = APIRouter(prefix="/token", tags=["Token Validation"])
security = HTTPBearer()

# Response models
class TokenValidationResponse(BaseModel):
    valid: bool
    user_id: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    roles: Optional[list] = None
    permissions: Optional[list] = None
    expires_at: Optional[str] = None
    session_id: Optional[str] = None

class UserInfoResponse(BaseModel):
    user_id: str
    username: str
    email: str
    roles: list
    permissions: list
    profile: Optional[Dict[str, Any]] = None
    is_active: bool
    last_login: Optional[str] = None


# No caching - validate fresh for security


@router.post(
    "/validate",
    response_model=TokenValidationResponse,
    responses={
        200: {
            "description": "Token validation result",
            "content": {
                "application/json": {
                    "examples": {
                        "valid_token": {
                            "summary": "Valid Token",
                            "value": {
                                "valid": True,
                                "user_id": "user123",
                                "username": "john.doe",
                                "email": "john@example.com",
                                "roles": ["user", "manager"],
                                "permissions": ["dashboard:read", "users:read"],
                                "expires_at": "2025-01-15T11:00:00Z",
                                "session_id": "sess_abc123"
                            }
                        },
                        "invalid_token": {
                            "summary": "Invalid Token",
                            "value": {
                                "valid": False
                            }
                        }
                    }
                }
            }
        }
    }
)
async def validate_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> TokenValidationResponse:
    """
    Validate JWT token and return user information
    
    ## 🔐 Service-to-Service Authentication:
    This endpoint is designed for other services to validate JWT tokens and get user information.
    
    ## Purpose:
    - Centralized token validation for all services
    - Fresh validation for security (no caching)
    - Consistent authentication across microservices
    
    ## Request:
    - **Authorization Header**: `Bearer <jwt_token>`
    
    ## Response:
    - `valid`: Boolean indicating if token is valid
    - `user_id`: User ID if token is valid
    - `username`: Username if token is valid
    - `email`: User email if token is valid
    - `roles`: User roles if token is valid
    - `permissions`: User permissions if token is valid
    - `expires_at`: Token expiration time
    - `session_id`: Session ID from token
    
    ## Security:
    - No caching for security - fresh validation every time
    - Immediate token revocation/expiration handling
    
    ## Use Cases:
    - **Middleware Authentication**: Validate tokens in request middleware
    - **Service Authorization**: Check user permissions before operations
    - **User Context**: Get current user information for business logic
    """
    token = credentials.credentials
    client_ip = request.client.host if request.client else None
    
    try:
        # Validate token directly - no caching for security
        payload = jwt_service.verify_access_token(token)
        
        if not payload:
            get_audit_logger().log_security_event(
                service="auth",
                event_type="token_validation_failed",
                message="Invalid or expired token",
                client_ip=client_ip,
                token_prefix=token[:20] + "...",
                success=False
            )
            
            return TokenValidationResponse(valid=False)
        
        # Get user information
        user_id = payload.get("sub") or payload.get("id")
        if not user_id:
            return TokenValidationResponse(valid=False)
        
        # Get full user data
        user_dto = await auth_controller.get_current_user(token)
        if not user_dto:
            return TokenValidationResponse(valid=False)
        
        # Prepare response data
        expires_at = None
        if "exp" in payload:
            expires_at = datetime.fromtimestamp(payload["exp"]).isoformat() + "Z"
        
        response_data = {
            "user_id": user_dto.id,
            "username": user_dto.username,
            "email": user_dto.email,
            "roles": user_dto.roles,
            "permissions": user_dto.permissions,
            "expires_at": expires_at,
            "session_id": payload.get("session_id")
        }
        
        # Log successful validation
        get_audit_logger().log_security_event(
            service="auth",
            event_type="token_validation_success",
            message=f"Token validated successfully for user: {user_dto.username}",
            client_ip=client_ip,
            user_id=user_dto.id,
            username=user_dto.username,
            success=True
        )
        
        return TokenValidationResponse(
            valid=True,
            **response_data
        )
        
    except Exception as e:
        get_audit_logger().log_error_event(
            service="auth",
            error_name="TokenValidationError",
            error_message=f"Token validation failed: {str(e)}",
            client_ip=client_ip,
            token_prefix=token[:20] + "...",
            exception_type=type(e).__name__
        )
        
        return TokenValidationResponse(valid=False)


@router.get(
    "/user-info/{user_id}",
    response_model=UserInfoResponse,
    responses={
        200: {
            "description": "User information retrieved successfully"
        },
        404: {
            "description": "User not found"
        }
    }
)
async def get_user_info(
    user_id: str,
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> UserInfoResponse:
    """
    Get detailed user information by user ID
    
    ## 🔐 Service-to-Service Authentication:
    This endpoint requires a valid JWT token for service-to-service communication.
    
    ## Purpose:
    - Get detailed user information for other services
    - Cached user data for performance
    - Consistent user data across microservices
    
    ## Request:
    - **Authorization Header**: `Bearer <jwt_token>`
    - **Path Parameter**: `user_id` - The user ID to retrieve information for
    
    ## Response:
    - Complete user information including profile data
    - Roles and permissions
    - Account status and last login
    - Cache status indicator
    
    ## Caching:
    - User data is cached for 5 minutes after successful retrieval
    - Improves performance for frequent user data requests
    
    ## Use Cases:
    - **User Profile Display**: Get user data for UI components
    - **Authorization Checks**: Verify user roles and permissions
    - **Audit Logging**: Include user context in operation logs
    """
    token = credentials.credentials
    client_ip = request.client.host if request.client else None
    
    try:
        # Validate the requesting token first
        payload = jwt_service.verify_access_token(token)
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token"
            )
        
        # Get user from database (no caching for security)
        from ....repository.user_repository import UserRepository
        user_repo = UserRepository()
        user = user_repo.get_user_by_id(user_id)
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Prepare response data
        response_data = {
            "user_id": user.id,
            "username": user.username,
            "email": user.email,
            "roles": user.roles or [],
            "permissions": user.permissions or [],
            "profile": user.profile,
            "is_active": user.is_active,
            "last_login": user.last_login.isoformat() + "Z" if user.last_login else None
        }
        
        # Log successful retrieval
        get_audit_logger().log_system_operation(
            service="auth",
            operation="user_info_retrieval",
            message=f"User info retrieved successfully: {user.username}",
            client_ip=client_ip,
            target_user_id=user_id,
            requesting_user_id=payload.get("sub") or payload.get("id"),
            success=True
        )
        
        return UserInfoResponse(**response_data)
        
    except HTTPException:
        raise
    except Exception as e:
        get_audit_logger().log_error_event(
            service="auth",
            error_name="UserInfoRetrievalError",
            error_message=f"Failed to retrieve user info: {str(e)}",
            client_ip=client_ip,
            target_user_id=user_id,
            exception_type=type(e).__name__
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user information"
        )


# Cache invalidation endpoints removed - no caching for security