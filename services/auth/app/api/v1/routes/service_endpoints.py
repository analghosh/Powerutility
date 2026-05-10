"""
Service-to-Service Communication Endpoints
These endpoints are designed for inter-service communication and require proper JWT authentication.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

from ....services.jwt_service import jwt_service
from ....core.dto import AuthUserDTO
from libs.core.authz import get_current_user, UserPrincipal

# Import secure audit logging
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..'))
from libs.audit_queue.simple_logger import AuditLogger
from ....core.dependencies import get_audit_logger

router = APIRouter(prefix="/auth", tags=["user-management"])
security = HTTPBearer()

# Response models
class UserInfoResponse(BaseModel):
    id: str
    username: str
    email: str
    is_active: bool
    is_verified: bool
    roles: List[str]
    permissions: List[str]
    profile: Dict[str, Any]
    created_at: Optional[str] = None
    last_login: Optional[str] = None

class RoleResponse(BaseModel):
    id: str
    name: str
    description: str
    permissions: List[str]
    created_at: str

class PermissionResponse(BaseModel):
    name: str
    description: str
    resource: str
    action: str

class PermissionValidationRequest(BaseModel):
    permissions: List[str]

class PermissionValidationResponse(BaseModel):
    user_id: str
    has_permissions: bool
    granted_permissions: List[str]
    missing_permissions: List[str]


@router.get(
    "/user/{user_id}",
    response_model=UserInfoResponse,
    summary="Get User Info",
    description="Get user information (read-only, for service-to-service communication)",
    dependencies=[Depends(get_current_user)]
)
async def get_user_info(
    user_id: str,
    request: Request,
    current_user: UserPrincipal = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
) -> UserInfoResponse:
    """
    Get user information by ID for service-to-service communication
    
    ## 🔐 JWT Authentication Required:
    - **Authorization Header**: `Bearer <jwt_token>`
    - **Service Access**: Designed for inter-service communication
    
    ## Purpose:
    Retrieve detailed user information for other services that need user context.
    
    ## Request:
    - **Path Parameter**: `user_id` - The user ID to retrieve information for
    - **Authorization Header**: Valid JWT token
    
    ## Response:
    Complete user information including roles, permissions, and profile data.
    
    ## Use Cases:
    - **Service Authorization**: Get user context for business logic
    - **User Profile Display**: Retrieve user data for UI components
    - **Audit Logging**: Include user information in operation logs
    """
    try:
        from ....repository.user_repository import UserRepository
        user_repo = UserRepository()
        
        user = user_repo.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Log successful user info retrieval
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_system_operation(
            service="auth",
            operation="user_info_retrieval",
            message=f"User info retrieved for service communication: {user.username}",
            client_ip=client_ip,
            target_user_id=user_id,
            requesting_user_id=current_user.user_id,
            user_agent=user_agent,
            success=True
        )
        
        return UserInfoResponse(
            id=str(user.id),
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_verified=user.is_verified,
            roles=user.roles,
            permissions=user.permissions,
            profile=user.profile,
            created_at=user.created_at.isoformat() if user.created_at else None,
            last_login=user.last_login.isoformat() if user.last_login else None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="auth",
            error_name="UserInfoRetrievalError",
            error_message=f"Failed to retrieve user info: {str(e)}",
            client_ip=request.client.host if request.client else None,
            target_user_id=user_id,
            requesting_user_id=current_user.user_id,
            exception_type=type(e).__name__
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user information"
        )


@router.get(
    "/validate-token",
    summary="Validate Token And Get User",
    description="Validate current user token and return user info (for service-to-service communication)",
    dependencies=[Depends(get_current_user)]
)
async def validate_token_and_get_user(
    request: Request,
    current_user: UserPrincipal = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
) -> Dict[str, Any]:
    """
    Validate JWT token and return user information
    
    ## 🔐 JWT Authentication Required:
    - **Authorization Header**: `Bearer <jwt_token>`
    - **Service Access**: Designed for token validation across services
    
    ## Purpose:
    Validate the provided JWT token and return user information if valid.
    
    ## Request:
    - **Authorization Header**: JWT token to validate
    
    ## Response:
    User information if token is valid, error if invalid.
    
    ## Use Cases:
    - **Middleware Authentication**: Validate tokens in request middleware
    - **Service Authorization**: Verify user permissions before operations
    - **User Context**: Get current user information for business logic
    """
    try:
        # Log successful token validation
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_security_event(
            service="auth",
            event_type="token_validation_success",
            message=f"Token validated successfully for service communication: {current_user.username}",
            client_ip=client_ip,
            user_id=current_user.user_id,
            username=current_user.username,
            user_agent=user_agent,
            success=True
        )
        
        return {
            "valid": True,
            "user_id": current_user.user_id,
            "username": current_user.username,
            "email": current_user.email,
            "roles": current_user.roles,
            "permissions": current_user.permissions,
            "is_active": current_user.is_active
        }
        
    except Exception as e:
        audit_logger.log_error_event(
            service="auth",
            error_name="TokenValidationError",
            error_message=f"Token validation failed: {str(e)}",
            client_ip=request.client.host if request.client else None,
            exception_type=type(e).__name__
        )
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )


@router.get(
    "/roles",
    response_model=List[RoleResponse],
    summary="Get Available Roles",
    description="Get all available roles (for service-to-service communication)",
    dependencies=[Depends(get_current_user)]
)
async def get_available_roles(
    request: Request,
    current_user: UserPrincipal = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
) -> List[RoleResponse]:
    """
    Get all available roles in the system
    
    ## 🔐 JWT Authentication Required:
    - **Authorization Header**: `Bearer <jwt_token>`
    - **Service Access**: Designed for inter-service communication
    
    ## Purpose:
    Retrieve all available roles for service operations and UI components.
    
    ## Response:
    List of all roles with their permissions and metadata.
    
    ## Use Cases:
    - **Role Assignment**: Get available roles for user management
    - **Permission Checking**: Understand role-permission mappings
    - **UI Components**: Populate role selection dropdowns
    """
    try:
        from ....repository.role_repository import RoleRepository
        role_repo = RoleRepository()
        
        roles = await role_repo.get_all_active_roles()
        
        # Log successful roles retrieval
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_system_operation(
            service="auth",
            operation="roles_retrieval",
            message=f"Available roles retrieved for service communication",
            client_ip=client_ip,
            requesting_user_id=current_user.user_id,
            roles_count=len(roles),
            user_agent=user_agent,
            success=True
        )
        
        return [
            RoleResponse(
                id=str(role.id),
                name=role.name,
                description=role.description,
                permissions=role.permissions,
                created_at=role.created_at.isoformat() if role.created_at else ""
            )
            for role in roles
        ]
        
    except Exception as e:
        audit_logger.log_error_event(
            service="auth",
            error_name="RolesRetrievalError",
            error_message=f"Failed to retrieve roles: {str(e)}",
            client_ip=request.client.host if request.client else None,
            requesting_user_id=current_user.user_id,
            exception_type=type(e).__name__
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve roles"
        )


@router.get(
    "/permissions",
    response_model=List[PermissionResponse],
    summary="Get Available Permissions",
    description="Get all available permissions (for service-to-service communication)",
    dependencies=[Depends(get_current_user)]
)
async def get_available_permissions(
    request: Request,
    current_user: UserPrincipal = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
) -> List[PermissionResponse]:
    """
    Get all available permissions in the system
    
    ## 🔐 JWT Authentication Required:
    - **Authorization Header**: `Bearer <jwt_token>`
    - **Service Access**: Designed for inter-service communication
    
    ## Purpose:
    Retrieve all available permissions for service operations and authorization checks.
    
    ## Response:
    List of all permissions with their descriptions and metadata.
    
    ## Use Cases:
    - **Authorization**: Check available permissions for access control
    - **Role Management**: Understand what permissions can be assigned
    - **UI Components**: Display permission information in admin interfaces
    """
    try:
        # Get permissions from centralized catalog
        from libs.core.authz.permissions_catalog import get_all_permissions
        
        permissions = get_all_permissions()
        
        # Log successful permissions retrieval
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_system_operation(
            service="auth",
            operation="permissions_retrieval",
            message=f"Available permissions retrieved for service communication",
            client_ip=client_ip,
            requesting_user_id=current_user.user_id,
            permissions_count=len(permissions),
            user_agent=user_agent,
            success=True
        )
        
        return [
            PermissionResponse(
                name=perm["name"],
                description=perm.get("description", ""),
                resource=perm.get("resource", ""),
                action=perm.get("action", "")
            )
            for perm in permissions
        ]
        
    except Exception as e:
        audit_logger.log_error_event(
            service="auth",
            error_name="PermissionsRetrievalError",
            error_message=f"Failed to retrieve permissions: {str(e)}",
            client_ip=request.client.host if request.client else None,
            requesting_user_id=current_user.user_id,
            exception_type=type(e).__name__
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve permissions"
        )


@router.post(
    "/validate-permissions",
    response_model=PermissionValidationResponse,
    summary="Validate User Permissions",
    description="Validate if a user has specific permissions (for service-to-service communication)",
    dependencies=[Depends(get_current_user)]
)
async def validate_user_permissions(
    user_id: str = Query(..., description="User ID to validate permissions for"),
    permission_request: PermissionValidationRequest = ...,
    request: Request = ...,
    current_user: UserPrincipal = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
) -> PermissionValidationResponse:
    """
    Validate if a user has specific permissions
    
    ## 🔐 JWT Authentication Required:
    - **Authorization Header**: `Bearer <jwt_token>`
    - **Service Access**: Designed for inter-service authorization checks
    
    ## Purpose:
    Check if a specific user has the required permissions for service operations.
    
    ## Request:
    - **Query Parameter**: `user_id` - User ID to check permissions for
    - **Request Body**: List of required permissions to validate
    
    ## Response:
    Validation result with granted and missing permissions.
    
    ## Use Cases:
    - **Authorization Middleware**: Check user permissions before operations
    - **Feature Flags**: Determine what features a user can access
    - **UI Components**: Show/hide elements based on permissions
    """
    try:
        from ....repository.user_repository import UserRepository
        user_repo = UserRepository()
        
        user = user_repo.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Check permissions
        user_permissions = set(user.permissions)
        required_permissions = set(permission_request.permissions)
        
        granted_permissions = list(user_permissions.intersection(required_permissions))
        missing_permissions = list(required_permissions - user_permissions)
        has_all_permissions = len(missing_permissions) == 0
        
        # Log permission validation
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_system_operation(
            service="auth",
            operation="permission_validation",
            message=f"Permission validation performed for user: {user.username}",
            client_ip=client_ip,
            target_user_id=user_id,
            requesting_user_id=current_user.user_id,
            required_permissions=permission_request.permissions,
            validation_result=has_all_permissions,
            user_agent=user_agent,
            success=True
        )
        
        return PermissionValidationResponse(
            user_id=user_id,
            has_permissions=has_all_permissions,
            granted_permissions=granted_permissions,
            missing_permissions=missing_permissions
        )
        
    except HTTPException:
        raise
    except Exception as e:
        audit_logger.log_error_event(
            service="auth",
            error_name="PermissionValidationError",
            error_message=f"Failed to validate permissions: {str(e)}",
            client_ip=request.client.host if request.client else None,
            target_user_id=user_id,
            requesting_user_id=current_user.user_id,
            exception_type=type(e).__name__
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to validate permissions"
        )
