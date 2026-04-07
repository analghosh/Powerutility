# libs/core/authz/__init__.py
"""
Authorization module for RBAC (Single-Tenant)
Centralized permission checking for all endpoints
"""

# Core RBAC components
from .guard import global_authz_guard, get_current_user
from .principal import UserPrincipal, compute_effective_permissions
from .context import RequestContext, request_context
from .config import required_permissions_for
from .permissions_catalog import ensure_permissions_catalog, get_expanded_permissions

# Centralized permission checking utilities
from .permissions import (
    # Permission checker class
    PermissionChecker,
    
    # FastAPI dependency factories
    require_permission,
    require_any_permission, 
    require_all_permissions,
    require_admin,
    require_super_admin,
    
    # Pre-defined common dependencies
    require_users_read,
    require_users_write,
    require_users_create,
    require_users_delete,
    require_roles_read,
    require_roles_write,
    require_roles_create,
    require_roles_delete,
    require_menu_read,
    require_menu_write,
    require_menu_create,
    require_menu_delete,
    require_dashboard_read,
    require_dashboard_write,
    
    # Programmatic checking utilities
    check_user_permission,
    assert_user_permission,
    get_user_permissions,
    filter_by_permission,
    
    # Permission constants
    CommonPermissions
)

__all__ = [
    # Core RBAC
    "global_authz_guard",
    "get_current_user",
    "UserPrincipal",
    "compute_effective_permissions",
    "RequestContext",
    "request_context", 
    "required_permissions_for",
    "ensure_permissions_catalog",
    "get_expanded_permissions",
    
    # Permission checking
    "PermissionChecker",
    "require_permission",
    "require_any_permission",
    "require_all_permissions", 
    "require_admin",
    "require_super_admin",
    
    # Common permission dependencies
    "require_users_read",
    "require_users_write",
    "require_users_create",
    "require_users_delete",
    "require_roles_read", 
    "require_roles_write",
    "require_roles_create",
    "require_roles_delete",
    "require_menu_read",
    "require_menu_write",
    "require_menu_create",
    "require_menu_delete",
    "require_dashboard_read",
    "require_dashboard_write",
    
    # Utilities
    "check_user_permission",
    "assert_user_permission",
    "get_user_permissions",
    "filter_by_permission",
    "CommonPermissions"
]
