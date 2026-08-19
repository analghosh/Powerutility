"""
User Management Routes - Only required endpoints
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from libs.core.authz import get_current_user, UserPrincipal
from ....core.dto import UserDTO, AuthUserDTO
from ....utils.activity_logger import log_user_management, log_auth_activity

# Import secure audit logging
from ....core.dependencies import get_audit_logger
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from datetime import datetime


from libs.audit_queue.simple_logger import AuditLogger
from libs.middleware.utils import get_client_ip_or_none

# Pydantic models for user operations
class UserCreateRequest(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    profile: Optional[Dict[str, Any]] = None

class UserUpdateRequest(BaseModel):
    # username is deliberately not updatable: renaming the seeded super-admin frees
    # the name and the startup seeder mints a second super-admin on the next restart.
    email: Optional[str] = None
    roles: Optional[List[str]] = None
    permissions: Optional[List[str]] = None
    profile: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None

class UserResponse(BaseModel):
    id: str
    username: str
    email: Optional[str] = None
    is_active: bool
    is_verified: bool
    roles: List[str]
    permissions: List[str]
    profile: Dict[str, Any]
    created_at: Optional[str] = None
    last_login: Optional[str] = None
    # The real MFA state. The User Management screen used to show `is_verified` under an
    # "MFA" heading -- an unrelated flag -- so it reported two-factor as on for accounts
    # that had none. Login gates on both of these together.
    mfa_enabled: bool = False
    mfa_setup_complete: bool = False

class RoleAssignmentRequest(BaseModel):
    roles: List[str]

# REMOVED: PermissionAssignmentRequest - permissions come from roles only



router = APIRouter(prefix="/users", tags=["User Management"])

# User CRUD Operations
@router.get("/", response_model=List[UserResponse])
async def list_users(
    request: Request,
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records to return"),
    role: Optional[str] = Query(None, description="Filter by role"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    current_user: UserPrincipal = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
) -> List[UserResponse]:
    """Get list of users with pagination and filtering"""
    try:
        from ....repository.user_repository import UserRepository
        user_repo = UserRepository()
        
        # Get users with filters
        users = await user_repo.get_users_with_pagination(skip=skip, limit=limit, role_filter=role, is_active_filter=is_active)
        
        # Log user list access with secure audit logging
        client_ip = get_client_ip_or_none(request)
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_user_action(
            service="auth",
            user_id=current_user.user_id,
            action="list_users",
            message=f"Admin accessed user list: {current_user.user_id}",
            client_ip=client_ip,
            username=current_user.user_id,
            users_returned=len(users),
            pagination={"skip": skip, "limit": limit},
            filters={"role": role, "is_active": is_active},
            user_agent=user_agent,
            success=True
        )
        
        # Convert to response format
        return [
            UserResponse(
                id=str(user.id),
                username=user.username,
                email=user.email,
                is_active=user.is_active,
                is_verified=user.is_verified,
                roles=user.roles,
                permissions=user.permissions,
                profile=user.profile,
                created_at=user.created_at.isoformat() if user.created_at else None,
                last_login=user.last_login.isoformat() if user.last_login else None,
                mfa_enabled=getattr(user, "mfa_enabled", False),
                mfa_setup_complete=getattr(user, "mfa_setup_complete", False)
            )
            for user in users
        ]
    except Exception as e:
        # Log failed user list access with secure audit logging
        client_ip = get_client_ip_or_none(request)
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_error_event(
            service="auth",
            error_name="UserListAccessError",
            error_message=f"Failed to access user list: {str(e)}",
            user_id=getattr(current_user, 'user_id', 'unknown'),
            client_ip=client_ip,
            username=getattr(current_user, 'user_id', 'unknown'),
            access_failed=True,
            exception_type=type(e).__name__,
            user_agent=user_agent
        )
        raise HTTPException(status_code=500, detail=f"Error listing users: {str(e)}")

@router.post("/", response_model=UserResponse)
async def create_user(
    request: Request,
    user_data: UserCreateRequest,
    current_user: UserPrincipal = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
) -> UserResponse:
    """Create a new user with username and password (email optional)"""
    try:
        from ....repository.user_repository import UserRepository
        user_repo = UserRepository()
        
        # Check if user already exists
        existing_user = await user_repo.get_user_by_username(user_data.username)
        if existing_user:
            raise HTTPException(status_code=400, detail="Username already exists")
        
        # Check email uniqueness only if email is provided
        if user_data.email:
            existing_email = await user_repo.get_user_by_email(user_data.email)
            if existing_email:
                raise HTTPException(status_code=400, detail="Email already exists")
        
        first_name = ""
        last_name = ""

        if user_data.profile:
            first_name = user_data.profile.get("first_name", "")
            last_name = user_data.profile.get("last_name", "")
        # Create user with simple defaults
        user = await user_repo.create_user(
            username=user_data.username,
            email=user_data.email or "",  # Empty string if no email provided
            password=user_data.password,
            roles=["user"],  # Default role
            permissions=[],  # Default permissions
            profile={"first_name": first_name, "last_name": last_name},  # Empty profile
            is_active=True,  # Active by default
            is_verified=False  # Not verified by default
        )
        
        # Log successful user creation with secure audit logging
        client_ip = get_client_ip_or_none(request)
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_user_action(
            service="auth",
            user_id=current_user.user_id,
            action="create_user",
            message=f"Admin created new user: {user.username}",
            client_ip=client_ip,
            username=current_user.user_id,
            target_user_id=str(user.id),
            target_username=user.username,
            new_user_email=user_data.email,
            created_by_admin=True,
            default_role="user",
            user_agent=user_agent,
            success=True
        )
        
        return UserResponse(
            id=str(user.id),
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_verified=user.is_verified,
            roles=user.roles,
            permissions=user.permissions,
            profile=user.profile,
            created_at=user.created_at.isoformat() if user.created_at else None,
            last_login=user.last_login.isoformat() if user.last_login else None,
            mfa_enabled=getattr(user, "mfa_enabled", False),
            mfa_setup_complete=getattr(user, "mfa_setup_complete", False)
        )
    except HTTPException:
        raise
    except Exception as e:
        # Log failed user creation with secure audit logging
        client_ip = get_client_ip_or_none(request)
        user_agent = request.headers.get("user-agent")
        
        audit_logger.log_error_event(
            service="auth",
            error_name="UserCreationError",
            error_message=f"Failed to create user {user_data.username}: {str(e)}",
            user_id=current_user.user_id,
            client_ip=client_ip,
            username=current_user.user_id,
            target_username=user_data.username,
            creation_failed=True,
            exception_type=type(e).__name__,
            attempted_email=user_data.email,
            user_agent=user_agent
        )
        raise HTTPException(status_code=500, detail=f"Error creating user: {str(e)}")

@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    current_user: UserPrincipal = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
) -> UserResponse:
    """Get user by ID"""
    try:
        from ....repository.user_repository import UserRepository
        user_repo = UserRepository()
        
        user: Optional[UserDTO] = await user_repo.get_user_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        return UserResponse(
            id=str(user.id),
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_verified=user.is_verified,
            roles=user.roles,
            permissions=user.permissions,
            profile=user.profile,
            created_at=user.created_at.isoformat() if user.created_at else None,
            last_login=user.last_login.isoformat() if user.last_login else None,
            mfa_enabled=getattr(user, "mfa_enabled", False),
            mfa_setup_complete=getattr(user, "mfa_setup_complete", False)
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving user: {str(e)}")

@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    user_data: UserUpdateRequest,
    current_user: UserPrincipal = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
) -> UserResponse:
    """Update user by ID"""
    try:
        from ....repository.user_repository import UserRepository
        user_repo = UserRepository()
        
        current_user_id = current_user.user_id
        # Check if user exists. This was three calls: one un-awaited (a coroutine that was
        # created, never run, and immediately overwritten -- a "coroutine was never awaited"
        # warning per request), then two real round trips for the same document.
        user = await user_repo.get_user_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Determine whether this is a self-update
        is_self_update = (user_id == current_user_id)

        # Update user
        update_data = {}
        if user_data.email is not None:
            update_data['email'] = user_data.email
        if user_data.profile is not None:
            update_data['profile'] = user_data.profile

        # Privileged fields: only allow if caller is NOT updating their own account
        if not is_self_update:
            if user_data.roles is not None:
                update_data['roles'] = user_data.roles
            if user_data.permissions is not None:
                update_data['permissions'] = user_data.permissions
            if user_data.is_active is not None:
                update_data['is_active'] = user_data.is_active
            if user_data.is_verified is not None:
                update_data['is_verified'] = user_data.is_verified

        # Track role changes for logging
        user_roles = set(user.roles)
        new_roles = update_data.get('roles', None)
        
        if new_roles is not None:
            new_roles_set = set(new_roles)
            # Find roles that are being added (not previously assigned)
            added_roles = new_roles_set - user_roles
            removed_roles = user_roles - new_roles_set
            
            # Log role changes if any
            if added_roles or removed_roles:
                audit_logger.log_user_action(
                    service="auth",
                    user_id=current_user_id,
                    action="update",
                    message=f"Admin {current_user_id} updated roles for user {user.username}",
                    target_user_id=user_id,
                    target_username=user.username,
                    roles=added_roles,
                    removed_roles=list(removed_roles),
                    previous_roles=list(user_roles),
                    new_roles=new_roles,
                    success=True
                )
        
        # The un-awaited call that used to sit above this one never executed -- an
        # un-awaited coroutine does nothing -- so the update ran once, not twice. It was
        # still a per-request RuntimeWarning and a trap for the next reader.
        if not await user_repo.update_user(user_id, update_data, modified_by=current_user_id):
            raise HTTPException(status_code=500, detail="Failed to update user")
        
        # Get updated user
        updated_user = await user_repo.get_user_by_id(user_id)
        
        return UserResponse(
            id=str(updated_user.id),
            username=updated_user.username,
            email=updated_user.email,
            is_active=updated_user.is_active,
            is_verified=updated_user.is_verified,
            roles=updated_user.roles,
            permissions=updated_user.permissions,
            profile=updated_user.profile,
            created_at=updated_user.created_at.isoformat() if updated_user.created_at else None,
            last_login=updated_user.last_login.isoformat() if updated_user.last_login else None,
            mfa_enabled=getattr(updated_user, "mfa_enabled", False),
            mfa_setup_complete=getattr(updated_user, "mfa_setup_complete", False)
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating user: {str(e)}")

@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    current_user: UserPrincipal = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Delete user by ID"""
    try:
        from ....repository.user_repository import UserRepository
        user_repo = UserRepository()
        
        # Check if user exists
        user = await user_repo.get_user_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Delete user
        success = await user_repo.delete_user(user_id)

        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete user")
        
        audit_logger.log_user_action(
            service="auth",
            user_id=current_user.user_id,
            client_ip=get_client_ip_or_none(request),
            action="delete",
            message=f"Admin {current_user.user_id} deleted user {user.username}",
            target_user_id=user_id,
            target_username=user.username,
            success=True
        )
        
        return {"message": "User deleted successfully", "user_id": user_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting user: {str(e)}")

# Role and Permission Management
@router.put("/{user_id}/roles")
async def assign_roles_to_user(
    user_id: str,
    role_data: RoleAssignmentRequest,
    request: Request,
    current_user: UserPrincipal = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Assign roles to user"""
    try:
        from ....repository.user_repository import UserRepository
        user_repo = UserRepository()
        
        # Check if user exists
        user = await user_repo.get_user_by_id(user_id)

        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Update user roles (now async)
        success = await user_repo.update_user(user_id, {"roles": role_data.roles})
        audit_logger.log_user_action( # audit_logger methods are sync (do not await)
            service="auth",
            user_id=current_user.user_id,
            client_ip=get_client_ip_or_none(request),
            action="assign_roles",
            message=f"Admin {current_user.user_id} updated roles for user {user.username}",
            target_user_id=user_id,
            target_username=user.username,
            roles=role_data.roles,
            success=True
        )
        if not success:
            raise HTTPException(status_code=500, detail="Failed to assign roles")
        
        return {"message": "Roles assigned successfully", "user_id": user_id, "roles": role_data.roles}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error assigning roles: {str(e)}")

@router.get("/{user_id}/roles")
async def get_user_roles(
    user_id: str,
    current_user: UserPrincipal = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get user roles with detailed information"""
    try:
        from ....repository.user_repository import UserRepository
        from ....repository.role_repository import RoleRepository
        
        user_repo = UserRepository()
        role_repo = RoleRepository()
        
        user = await user_repo.get_user_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get detailed role information
        role_details = []
        for role_name in user.roles:
            role = await role_repo.get_role_by_name(role_name)
            if role:
                role_details.append({
                    "id": role.id,
                    "name": role.name,
                    "description": role.description,
                    "created_at": role.created_at.isoformat()
                })
        
        return {
            "user_id": user_id,
            "username": user.username,
            "roles": user.roles,
            "role_details": role_details
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving user roles: {str(e)}")

# Note: Direct permission assignment removed. 
# Permissions are now automatically derived from role menu assignments.
# Use: 1) Assign menus to roles, 2) Assign roles to users

@router.get("/{user_id}/accessible-menus")
async def get_user_accessible_menus(
    user_id: str,
    current_user: UserPrincipal = Depends(get_current_user),
    audit_logger: AuditLogger = Depends(get_audit_logger)
):
    """Get all menus accessible to a user based on their roles"""
    try:
        from ....repository.user_repository import UserRepository
        from ....repository.menu_role_repository import MenuRoleRepository
        from ....repository.menu_repository import MenuRepository
        from ....repository.role_repository import RoleRepository
        
        user_repo = UserRepository()
        menu_role_repo = MenuRoleRepository()
        menu_repo = MenuRepository()
        role_repo = RoleRepository()
        
        user = await user_repo.get_user_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get all accessible menus based on user roles
        accessible_menus = []
        menu_permissions_map = {}

        # Get role IDs
        role_ids = []
        for role_name in user.roles:
            role = await role_repo.get_role_by_name(role_name)
            if role:
                role_ids.append(role.id)
        
        # Get menu permissions for all roles
        for role_id in role_ids:
            menu_roles = await menu_role_repo.get_menu_roles_by_role_id(role_id)
            for menu_role in menu_roles:
                menu = await menu_repo.get_menu_by_id(menu_role.menu_id)
                if menu:
                    if menu.id not in menu_permissions_map:
                        menu_permissions_map[menu.id] = {
                            "menu": menu,
                            "permissions": set()
                        }
                    menu_permissions_map[menu.id]["permissions"].update(menu_role.permissions)
        
        # Build response
        for menu_data in menu_permissions_map.values():
            menu = menu_data["menu"]
            permissions = list(menu_data["permissions"])
            
            accessible_menus.append({
                "menu_id": menu.id,
                "menu_name": menu.name,
                "menu_path": menu.path,
                "menu_icon": menu.icon,
                "parent_id": menu.parent_id,
                "permissions": permissions
            })
        
        return {
            "user_id": user_id,
            "username": user.username,
            "accessible_menus": accessible_menus,
            "total_accessible_menus": len(accessible_menus)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting user accessible menus: {str(e)}")

# REMOVED: Direct user permissions endpoint
# 
# Use these endpoints instead to understand user access:
# - GET /users/{user_id}/roles → See user's assigned roles  
# - GET /users/{user_id}/accessible-menus → See user's accessible menus with permissions
# - GET /roles/{role_id}/menus → See what menus are assigned to specific roles
#
# Permissions are automatically calculated and don't need a separate endpoint.
