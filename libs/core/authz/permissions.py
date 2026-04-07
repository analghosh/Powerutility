# libs/core/authz/permissions.py
"""
Centralized endpoint permission checking utilities
Single place to handle all permission logic
"""

from typing import List, Union, Callable, Any
from fastapi import Depends, HTTPException
from .guard import get_current_user
from .principal import UserPrincipal


class PermissionChecker:
    """Centralized permission checking logic"""
    
    @staticmethod
    def has_permission(user: UserPrincipal, permission: str) -> bool:
        """Check if user has a specific permission"""
        if user.is_super_admin:
            return True
        if permission is None:
            return False
        
        # Direct permission check
        if permission in user.effective_permissions:
            return True
        
        # Check for wildcard permissions
        if "*" in user.effective_permissions:
            return True
            
        # Check for resource-level wildcard (e.g., admin:* covers admin:read)
        if ":" in permission:
            resource = permission.split(":")[0]
            resource_wildcard = f"{resource}:*"
            if resource_wildcard in user.effective_permissions:
                return True
        
        return False
    
    @staticmethod
    def has_any_permission(user: UserPrincipal, permissions: List[str]) -> bool:
        """Check if user has any of the specified permissions"""
        if user.is_super_admin:
            return True
        if not permissions:
            return False
        return any(perm in user.effective_permissions for perm in permissions if perm is not None)
    
    @staticmethod
    def has_all_permissions(user: UserPrincipal, permissions: List[str]) -> bool:
        """Check if user has all of the specified permissions"""
        if user.is_super_admin:
            return True
        if not permissions:
            return True  # Vacuous truth - empty set of requirements
        return all(perm in user.effective_permissions for perm in permissions if perm is not None)
    
    @staticmethod
    def has_role(user: UserPrincipal, role: str) -> bool:
        """Check if user has a specific role (from token or computed)"""
        if user.is_super_admin:
            return True
        # Note: roles are embedded in permissions, but you could extend this
        # to check actual roles if needed by accessing the JWT payload
        return f"role:{role}" in user.effective_permissions or role == "admin"


# Reusable FastAPI Dependencies for common permission patterns
def require_permission(permission: str) -> Callable:
    """
    FastAPI dependency factory for single permission requirement
    
    Usage:
        @router.get("/users", dependencies=[Depends(require_permission("users:read"))])
        async def list_users(): ...
    """
    async def check_permission(user: UserPrincipal = Depends(get_current_user)) -> UserPrincipal:
        if not PermissionChecker.has_permission(user, permission):
            raise HTTPException(
                status_code=403, 
                detail=f"Missing required permission: {permission}"
            )
        return user
    
    return check_permission


def require_any_permission(*permissions: str) -> Callable:
    """
    FastAPI dependency factory for "any of these permissions" requirement
    
    Usage:
        @router.get("/data", dependencies=[Depends(require_any_permission("data:read", "admin:read"))])
        async def get_data(): ...
    """
    async def check_permissions(user: UserPrincipal = Depends(get_current_user)) -> UserPrincipal:
        if not PermissionChecker.has_any_permission(user, list(permissions)):
            raise HTTPException(
                status_code=403,
                detail=f"Missing required permissions. Need any of: {', '.join(permissions)}"
            )
        return user
    
    return check_permissions


def require_all_permissions(*permissions: str) -> Callable:
    """
    FastAPI dependency factory for "all of these permissions" requirement
    
    Usage:
        @router.post("/critical", dependencies=[Depends(require_all_permissions("admin:write", "system:critical"))])
        async def critical_action(): ...
    """
    async def check_permissions(user: UserPrincipal = Depends(get_current_user)) -> UserPrincipal:
        if not PermissionChecker.has_all_permissions(user, list(permissions)):
            missing = [p for p in permissions if not PermissionChecker.has_permission(user, p)]
            raise HTTPException(
                status_code=403,
                detail=f"Missing required permissions: {', '.join(missing)}"
            )
        return user
    
    return check_permissions


def require_admin() -> Callable:
    """
    FastAPI dependency for admin-only endpoints
    
    Usage:
        @router.delete("/system", dependencies=[Depends(require_admin())])
        async def delete_system(): ...
    """
    return require_permission("admin:*")


def require_super_admin() -> Callable:
    """
    FastAPI dependency for super-admin-only endpoints
    
    Usage:
        @router.post("/system/reset", dependencies=[Depends(require_super_admin())])
        async def reset_system(): ...
    """
    async def check_super_admin(user: UserPrincipal = Depends(get_current_user)) -> UserPrincipal:
        if not user.is_super_admin:
            raise HTTPException(
                status_code=403,
                detail="Super admin access required"
            )
        return user
    
    return check_super_admin


# Utility functions for programmatic permission checking
def check_user_permission(user: UserPrincipal, permission: str) -> bool:
    """Simple utility to check permission programmatically"""
    return PermissionChecker.has_permission(user, permission)


def assert_user_permission(user: UserPrincipal, permission: str) -> None:
    """Assert user has permission, raise HTTPException if not"""
    if not PermissionChecker.has_permission(user, permission):
        raise HTTPException(
            status_code=403,
            detail=f"Missing required permission: {permission}"
        )


def get_user_permissions(user: UserPrincipal) -> List[str]:
    """Get list of all user permissions"""
    return list(user.effective_permissions)


def filter_by_permission(items: List[Any], user: UserPrincipal, permission_key: str) -> List[Any]:
    """
    Filter a list of items based on user permissions
    Useful for filtering menu items, data rows, etc.
    
    Args:
        items: List of items to filter
        user: Current user
        permission_key: Permission string or function to get permission from item
    
    Example:
        menus = filter_by_permission(all_menus, user, lambda menu: f"menu:{menu.name}:read")
    """
    if user.is_super_admin:
        return items
    
    filtered = []
    for item in items:
        if callable(permission_key):
            required_perm = permission_key(item)
        else:
            required_perm = permission_key
            
        if PermissionChecker.has_permission(user, required_perm):
            filtered.append(item)
    
    return filtered


# Common permission patterns as constants
class CommonPermissions:
    """Common permission string patterns"""
    
    # Admin permissions
    ADMIN_ALL = "admin:*"
    ADMIN_READ = "admin:read"
    ADMIN_WRITE = "admin:write"
    
    # User management
    USERS_READ = "users:read"
    USERS_WRITE = "users:write"
    USERS_CREATE = "users:create"
    USERS_DELETE = "users:delete"
    USERS_ALL = "users:*"
    
    # Role management
    ROLES_READ = "roles:read"
    ROLES_WRITE = "roles:write"
    ROLES_CREATE = "roles:create"
    ROLES_DELETE = "roles:delete"
    ROLES_ALL = "roles:*"
    
    # Menu management
    MENU_READ = "menu:read"
    MENU_WRITE = "menu:write"
    MENU_CREATE = "menu:create"
    MENU_DELETE = "menu:delete"
    MENU_ALL = "menu:*"
    
    # Dashboard
    DASHBOARD_READ = "dashboard:read"
    DASHBOARD_WRITE = "dashboard:write"
    
    # System
    SYSTEM_READ = "system:read"
    SYSTEM_WRITE = "system:write"
    SYSTEM_ALL = "system:*"


# Pre-defined dependency shortcuts for common patterns
require_users_read = require_permission(CommonPermissions.USERS_READ)
require_users_write = require_permission(CommonPermissions.USERS_WRITE)
require_users_create = require_permission(CommonPermissions.USERS_CREATE)
require_users_delete = require_permission(CommonPermissions.USERS_DELETE)

require_roles_read = require_permission(CommonPermissions.ROLES_READ)
require_roles_write = require_permission(CommonPermissions.ROLES_WRITE)
require_roles_create = require_permission(CommonPermissions.ROLES_CREATE)
require_roles_delete = require_permission(CommonPermissions.ROLES_DELETE)

require_menu_read = require_permission(CommonPermissions.MENU_READ)
require_menu_write = require_permission(CommonPermissions.MENU_WRITE)
require_menu_create = require_permission(CommonPermissions.MENU_CREATE)
require_menu_delete = require_permission(CommonPermissions.MENU_DELETE)

require_dashboard_read = require_permission(CommonPermissions.DASHBOARD_READ)
require_dashboard_write = require_permission(CommonPermissions.DASHBOARD_WRITE)
