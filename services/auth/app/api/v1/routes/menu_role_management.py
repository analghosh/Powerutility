"""
Menu-Role Management API routes
Handles assignment of menu permissions to roles
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from pydantic import BaseModel
from libs.core.authz import get_current_user, UserPrincipal
from ....models.menu_role import MenuRoleCreate, MenuRoleUpdate, MenuPermissionResponse, RoleMenuResponse
from ....repository.menu_role_repository import MenuRoleRepository
from ....repository.menu_repository import MenuRepository
from ....repository.role_repository import RoleRepository

# Request/Response Models
class MenuRoleAssignRequest(BaseModel):
    """Request model for assigning menu permissions to role"""
    menu_id: str
    permissions: List[str] = ["read"]

class MenuRolePermissionRequest(BaseModel):
    """Request model for updating menu permissions for role"""
    permissions: List[str]

class BulkMenuRoleAssignRequest(BaseModel):
    """Request model for bulk assigning menus to role"""
    menu_assignments: List[MenuRoleAssignRequest]

class MenuRoleResponse(BaseModel):
    """Response model for menu-role relationship"""
    id: str
    menu_id: str
    menu_name: str
    menu_path: Optional[str] = None
    role_id: str
    role_name: str
    permissions: List[str]
    created_at: str
    updated_at: str

router = APIRouter(prefix="/menu-roles", tags=["Menu-Role Management"])

# Initialize repositories
menu_role_repo = MenuRoleRepository()
menu_repo = MenuRepository()
role_repo = RoleRepository()

@router.post("/roles/{role_id}/menus")
async def assign_menu_to_role(
    role_id: str,
    menu_assignment: MenuRoleAssignRequest,
    current_user: UserPrincipal = Depends(get_current_user)
):
    """Assign a menu with permissions to a role"""
    try:
        # Verify role exists
        role = await role_repo.get_role_by_id(role_id)
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")

        # Verify menu exists
        menu = await menu_repo.get_menu_by_id(menu_assignment.menu_id)
        if not menu:
            raise HTTPException(status_code=404, detail="Menu not found")

        # Assign menu to role
        success = await menu_role_repo.grant_role_menu_access(
            role_id=role_id,
            menu_id=menu_assignment.menu_id,
            permissions=menu_assignment.permissions
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to assign menu to role")

        return {
            "message": "Menu assigned to role successfully",
            "role_id": role_id,
            "menu_id": menu_assignment.menu_id,
            "permissions": menu_assignment.permissions
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error assigning menu to role: {str(e)}")

@router.post("/roles/{role_id}/menus/bulk")
async def bulk_assign_menus_to_role(
    role_id: str,
    bulk_assignment: BulkMenuRoleAssignRequest,
    current_user: UserPrincipal = Depends(get_current_user)
):
    """Bulk assign multiple menus to a role"""
    try:
        # Verify role exists
        role = await role_repo.get_role_by_id(role_id)
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")

        successful_assignments = []
        failed_assignments = []

        for assignment in bulk_assignment.menu_assignments:
            try:
                # Verify menu exists
                menu = await menu_repo.get_menu_by_id(assignment.menu_id)
                if not menu:
                    failed_assignments.append({
                        "menu_id": assignment.menu_id,
                        "error": "Menu not found"
                    })
                    continue

                # Assign menu to role
                success = await menu_role_repo.grant_role_menu_access(
                    role_id=role_id,
                    menu_id=assignment.menu_id,
                    permissions=assignment.permissions
                )

                if success:
                    successful_assignments.append({
                        "menu_id": assignment.menu_id,
                        "permissions": assignment.permissions
                    })
                else:
                    failed_assignments.append({
                        "menu_id": assignment.menu_id,
                        "error": "Failed to assign"
                    })
            except Exception as e:
                failed_assignments.append({
                    "menu_id": assignment.menu_id,
                    "error": str(e)
                })

        return {
            "message": f"Bulk assignment completed. {len(successful_assignments)} successful, {len(failed_assignments)} failed",
            "role_id": role_id,
            "successful_assignments": successful_assignments,
            "failed_assignments": failed_assignments
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in bulk assignment: {str(e)}")

@router.get("/roles/{role_id}/menus", response_model=List[MenuPermissionResponse])
async def get_role_menu_permissions(
    role_id: str,
    current_user: UserPrincipal = Depends(get_current_user)
):
    """Get all menu permissions for a specific role"""
    try:
        # Verify role exists
        role = await role_repo.get_role_by_id(role_id)
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")

        # Get menu-role relationships
        menu_roles = await menu_role_repo.get_menu_roles_by_role_id(role_id)

        menu_permissions = []
        for menu_role in menu_roles:
            menu = await menu_repo.get_menu_by_id(menu_role.menu_id)
            if menu:
                menu_permissions.append(MenuPermissionResponse(
                    menu_id=menu_role.menu_id,
                    menu_name=menu.name,
                    menu_path=menu.path,
                    permissions=menu_role.permissions,
                    assigned_at=menu_role.created_at.isoformat()
                ))

        return menu_permissions
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting role menu permissions: {str(e)}")

@router.put("/roles/{role_id}/menus/{menu_id}")
async def update_role_menu_permissions(
    role_id: str,
    menu_id: str,
    permission_update: MenuRolePermissionRequest,
    current_user: UserPrincipal = Depends(get_current_user)
):
    """Update permissions for a role on a specific menu"""
    try:
        # Verify role exists
        role = await role_repo.get_role_by_id(role_id)
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")

        # Verify menu exists
        menu = await menu_repo.get_menu_by_id(menu_id)
        if not menu:
            raise HTTPException(status_code=404, detail="Menu not found")

        # Get existing menu-role relationship
        menu_role = await menu_role_repo.get_menu_role(menu_id, role_id)
        if not menu_role:
            raise HTTPException(status_code=404, detail="Menu-role relationship not found")

        # Update permissions
        success = await menu_role_repo.update_menu_role(
            menu_role.id,
            MenuRoleUpdate(permissions=permission_update.permissions)
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to update menu permissions")

        # Sync role permissions so users get the updated access on next login
        await menu_role_repo._sync_role_permissions(role_id)

        return {
            "message": "Menu permissions updated successfully",
            "role_id": role_id,
            "menu_id": menu_id,
            "permissions": permission_update.permissions
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating menu permissions: {str(e)}")

@router.delete("/roles/{role_id}/menus/{menu_id}")
async def revoke_role_menu_access(
    role_id: str,
    menu_id: str,
    current_user: UserPrincipal = Depends(get_current_user)
):
    """Revoke a role's access to a menu"""
    try:
        # Verify role exists
        role = await role_repo.get_role_by_id(role_id)
        if not role:
            raise HTTPException(status_code=404, detail="Role not found")

        # Verify menu exists
        menu = await menu_repo.get_menu_by_id(menu_id)
        if not menu:
            raise HTTPException(status_code=404, detail="Menu not found")

        # Revoke access
        success = await menu_role_repo.revoke_role_menu_access(role_id, menu_id)
        if not success:
            raise HTTPException(status_code=404, detail="Menu-role relationship not found or already revoked")

        return {
            "message": "Menu access revoked successfully",
            "role_id": role_id,
            "menu_id": menu_id
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error revoking menu access: {str(e)}")

@router.get("/menus/{menu_id}/roles")
async def get_menu_role_assignments(
    menu_id: str,
    current_user: UserPrincipal = Depends(get_current_user)
):
    """Get all role assignments for a specific menu"""
    try:
        # Verify menu exists
        menu = await menu_repo.get_menu_by_id(menu_id)
        if not menu:
            raise HTTPException(status_code=404, detail="Menu not found")

        # Get menu-role relationships
        menu_roles = await menu_role_repo.get_menu_roles_by_menu_id(menu_id)

        role_assignments = []
        for menu_role in menu_roles:
            role = await role_repo.get_role_by_id(menu_role.role_id)
            if role:
                role_assignments.append({
                    "role_id": menu_role.role_id,
                    "role_name": role.name,
                    "permissions": menu_role.permissions,
                    "assigned_at": menu_role.created_at.isoformat()
                })

        return {
            "menu_id": menu_id,
            "menu_name": menu.name,
            "role_assignments": role_assignments
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting menu role assignments: {str(e)}")

@router.get("/summary", response_model=List[RoleMenuResponse])
async def get_all_role_menu_assignments(
    role_name: Optional[str] = Query(None, description="Filter by role name"),
    current_user: UserPrincipal = Depends(get_current_user)
):
    """Get complete summary of all role-menu assignments"""
    try:
        # Get all roles or filter by name
        if role_name:
            role = await role_repo.get_role_by_name(role_name)
            roles = [role] if role else []
        else:
            roles = await role_repo.get_all_active_roles()

        role_menu_summary = []
        for role in roles:
            # Get menu permissions for this role
            menu_roles = await menu_role_repo.get_menu_roles_by_role_id(role.id)

            menu_permissions = []
            for menu_role in menu_roles:
                menu = await menu_repo.get_menu_by_id(menu_role.menu_id)
                if menu:
                    menu_permissions.append(MenuPermissionResponse(
                        menu_id=menu_role.menu_id,
                        menu_name=menu.name,
                        menu_path=menu.path,
                        permissions=menu_role.permissions,
                        assigned_at=menu_role.created_at.isoformat()
                    ))

            role_menu_summary.append(RoleMenuResponse(
                role_id=role.id,
                role_name=role.name,
                menus=menu_permissions
            ))

        return role_menu_summary
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting role-menu summary: {str(e)}")
