# libs/core/authz/principal.py
from __future__ import annotations
from typing import Set

class UserPrincipal:
    def __init__(self, user_id: str, tenant_id: str, is_super_admin: bool, authz_version: int, effective_permissions: Set[str], roles: list = None, must_change_password: bool = False):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.is_super_admin = is_super_admin
        self.authz_version = authz_version
        self.effective_permissions = effective_permissions
        self.roles = roles or []
        self.must_change_password = must_change_password

async def compute_effective_permissions(user_doc: dict, tenant_id: str, db) -> Set[str]:  # noqa: ARG001
    """
    Compute effective permissions for single-tenant user structure.
    
    user_doc shape (Auth Service format):
    {
      _id: "...",
      roles: ["admin", "user"],
      permissions: ["dashboard:read", "users:write"],
      is_super_admin: bool,
      ...
    }
    """
    # Get direct permissions from user document
    direct_perms = set(user_doc.get("permissions", []))
    
    # Get role names from user document  
    role_names = user_doc.get("roles", [])
    
    # Load role documents by name (not key)
    role_docs = await db.roles.find({"name": {"$in": role_names}}).to_list(length=10000)
    role_perms = set()
    for role_doc in role_docs:
        perms = role_doc.get("permissions", [])
        if perms is not None:  # Handle null permissions
            role_perms.update(perms)

    # Union of role permissions and direct permissions
    all_perms = role_perms | direct_perms

    # Expand wildcard if present
    if "*" in all_perms:
        # Try to get permissions catalog from database
        cat = await db.permissions_catalog.find_one({}, {"_id":0, "permissions":1})
        if cat:
            return set(cat.get("permissions", []))
        else:
            # Fallback: import default permissions catalog
            from .permissions_catalog import get_expanded_permissions
            return get_expanded_permissions()

    return all_perms
