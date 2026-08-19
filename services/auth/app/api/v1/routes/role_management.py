from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import List, Optional
from pydantic import BaseModel
from libs.core.authz import get_current_user, UserPrincipal
from ....utils.activity_logger import log_auth_activity

# Import secure audit logging
from ....core.dependencies import get_audit_logger
# Removed AuthUserDTO import - using UserPrincipal from centralized system
from datetime import datetime
from libs.middleware.utils import get_client_ip_or_none

# Role Master APIs
class RoleCreateRequest(BaseModel):
    name: str
    description: str
    # Caps the role at `read` on every page it holds. See libs/authz/page_model.
    is_read_only: bool = False
    # Note: permissions are auto-generated from menu assignments

class RoleUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    # None means "not supplied" -- an update that only renames must leave the flag alone.
    is_read_only: Optional[bool] = None
    # Note: permissions are auto-synced from menu assignments, not directly editable

class RoleResponse(BaseModel):
    id: str
    name: str
    description: str
    permissions: List[str]
    is_read_only: bool = False
    is_active: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

router = APIRouter(prefix="/roles", tags=["Role Management"])

# Role Master APIs
@router.get("/", response_model=List[RoleResponse])
async def list_roles(
    request: Request,
    current_user: UserPrincipal = Depends(get_current_user)
) -> List[RoleResponse]:
    """Get list of all roles"""
    try:
        from ....repository.role_repository import RoleRepository
        role_repo = RoleRepository()
        
        roles = await role_repo.get_all_active_roles()
        
        # Log role list access with secure audit logging
        client_ip = get_client_ip_or_none(request)
        user_agent = request.headers.get("user-agent")
        
        get_audit_logger().log_user_action(
            service="auth",
            user_id=current_user.user_id,
            action="list_roles",
            message=f"Admin accessed role list: {current_user.user_id}",
            client_ip=client_ip,
            username=current_user.user_id,
            roles_returned=len(roles),
            user_agent=user_agent,
            success=True
        )
        
        return [
            RoleResponse(
                id=str(role.id),
                name=role.name,
                description=role.description,
                permissions=role.permissions,
                is_read_only=role.is_read_only,
                is_active=role.is_active,
                created_at=role.created_at.isoformat() if role.created_at else None,
                updated_at=role.updated_at.isoformat() if role.updated_at else None
            )
            for role in roles
        ]
    except Exception as e:
        # Log failed role list access with secure audit logging
        client_ip = get_client_ip_or_none(request)
        user_agent = request.headers.get("user-agent")
        
        get_audit_logger().log_error_event(
            service="auth",
            error_name="RoleListAccessError",
            error_message=f"Failed to access role list: {str(e)}",
            user_id=getattr(current_user, 'user_id', 'unknown'),
            client_ip=client_ip,
            username=getattr(current_user, 'user_id', 'unknown'),
            access_failed=True,
            exception_type=type(e).__name__,
            user_agent=user_agent
        )
        raise HTTPException(status_code=500, detail=f"Error listing roles: {str(e)}")

@router.post("/", response_model=RoleResponse)
async def create_role(
    role_data: RoleCreateRequest,
    current_user: UserPrincipal = Depends(get_current_user)
) -> RoleResponse:
    """Create a new role"""
    try:
        from ....repository.role_repository import RoleRepository
        role_repo = RoleRepository()
        
        # Check if role already exists
        existing_role = await role_repo.get_role_by_name(role_data.name)
        if existing_role:
            raise HTTPException(status_code=400, detail="Role name already exists")
        
        # Create role (permissions will be auto-generated from menu assignments)
        role = await role_repo.create_role(
            name=role_data.name,
            description=role_data.description,
            permissions=[],  # Empty initially, will be populated when menus are assigned
            is_read_only=role_data.is_read_only
        )

        # Role is already returned from create_role method

        return RoleResponse(
            id=str(role.id),
            name=role.name,
            description=role.description,
            permissions=role.permissions,
            is_read_only=role.is_read_only,
            is_active=role.is_active,
            created_at=role.created_at.isoformat() if role.created_at else None,
            updated_at=role.updated_at.isoformat() if role.updated_at else None
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating role: {str(e)}")

@router.get("/{role_id}", response_model=RoleResponse)
async def get_role(
    role_id: str,
    current_user: UserPrincipal = Depends(get_current_user)
) -> RoleResponse:
    """Get role by ID"""
    try:
        from ....repository.role_repository import RoleRepository
        role_repo = RoleRepository()
        
        role = await role_repo.get_role_by_id(role_id)
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        
        return RoleResponse(
            id=str(role.id),
            name=role.name,
            description=role.description,
            permissions=role.permissions,
            is_read_only=role.is_read_only,
            is_active=role.is_active,
            created_at=role.created_at.isoformat() if role.created_at else None,
            updated_at=role.updated_at.isoformat() if role.updated_at else None
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving role: {str(e)}")

@router.put("/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: str,
    role_data: RoleUpdateRequest,
    current_user: UserPrincipal = Depends(get_current_user)
) -> RoleResponse:
    """Update role by ID"""
    try:
        from ....repository.role_repository import RoleRepository
        role_repo = RoleRepository()
        
        # Check if role exists
        role = await role_repo.get_role_by_id(role_id)
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        
        # Update role (permissions are managed through menu assignments)
        update_data = {}
        if role_data.name is not None:
            # Check if new name conflicts with existing role
            existing_role = await role_repo.get_role_by_name(role_data.name)
            if existing_role and str(existing_role.id) != role_id:
                raise HTTPException(status_code=400, detail="Role name already exists")
            update_data['name'] = role_data.name
        if role_data.description is not None:
            update_data['description'] = role_data.description
        if role_data.is_read_only is not None:
            update_data['is_read_only'] = role_data.is_read_only
        # Note: permissions are not directly updatable - use menu assignment endpoints

        success = await role_repo.update_role(role_id, update_data)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to update role")

        # Re-sync unconditionally rather than only when is_read_only changed. The sync is
        # idempotent -- it recomputes from current state -- so an unconditional call costs
        # one menu walk on a rare admin operation and buys two things: a failed sync can be
        # healed by re-saving the role, and a role can never be left asserting a read-only
        # flag its permission strings do not reflect. _sync_role_permissions swallows its
        # own exceptions and reports False, so the result must be checked, not awaited and
        # discarded.
        from ....repository.menu_role_repository import MenuRoleRepository
        if not await MenuRoleRepository()._sync_role_permissions(role_id):
            raise HTTPException(
                status_code=500,
                detail="Role saved but its permissions could not be re-synced. "
                       "Save the role again to retry.",
            )

        # Get updated role
        updated_role = await role_repo.get_role_by_id(role_id)

        return RoleResponse(
            id=str(updated_role.id),
            name=updated_role.name,
            description=updated_role.description,
            permissions=updated_role.permissions,
            is_read_only=updated_role.is_read_only,
            is_active=updated_role.is_active,
            created_at=updated_role.created_at.isoformat() if updated_role.created_at else None,
            updated_at=updated_role.updated_at.isoformat() if updated_role.updated_at else None
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating role: {str(e)}")

@router.delete("/{role_id}")
async def delete_role(
    role_id: str,
    current_user: UserPrincipal = Depends(get_current_user)
):
    """Delete role by ID"""
    try:
        from ....repository.role_repository import RoleRepository
        role_repo = RoleRepository()
        
        # Check if role exists
        role = await role_repo.get_role_by_id(role_id)
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")
        
        # Check if role is being used by users
        from ....repository.user_repository import UserRepository
        user_repo = UserRepository()
        users_with_role = await user_repo.get_users_by_role(role.name)
        if users_with_role:
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot delete role '{role.name}' as it is assigned to {len(users_with_role)} user(s)"
            )
        
        # Delete role
        success = await role_repo.delete_role(role_id)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete role")
        
        return {"message": "Role deleted successfully", "role_id": role_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting role: {str(e)}")
