"""
Auth Client Service for Ingestion Service
Delegates all token validation to the Auth Service via libs.auth_client.
No local JWT secret needed.
"""

import logging
from typing import Optional, Dict, Any, List

from libs.auth_client import AuthClient as _LibAuthClient
from libs.authz.route_guard import ACTION_WRITE, is_allowed
from ..core.config import settings

logger = logging.getLogger(__name__)

# Which permission resources stand behind each ingestion page. Names match the ones the
# auth service synthesises from menus, so a grant made on the Roles & Permissions screen
# is the same fact this check reads. Holding any one of them is enough.
PAGE_RESOURCES = {
    "Data Ingestion": ("upload_csv", "scada_data", "dc_sg_template"),
    "Provider Management": ("admin",),
    "System Administration": ("admin",),
    "User Management": ("users", "admin"),
    "Reports": ("reports", "standard_reports"),
}


class AuthClient:
    """Thin wrapper around the shared libs.auth_client.AuthClient."""

    def __init__(self):
        self._client = _LibAuthClient(
            settings.AUTH_SERVICE_URL,
            timeout=settings.AUTH_SERVICE_TIMEOUT,
        )

    async def validate_token_and_get_user(self, token: str) -> Optional[Dict[str, Any]]:
        """Validate token via Auth Service and return user info, or None if invalid."""
        result = await self._client.validate_token(token)
        if not result.valid:
            return None
        return {
            "user_id": result.user_id or "unknown",
            "username": result.username or "unknown",
            "roles": result.roles,
            "permissions": result.permissions,
            "token": token,
        }

    async def check_page_access(
        self,
        user_roles: List[str],
        page_name: str,
        token: str,
        user_permissions: Optional[List[str]] = None,
    ) -> bool:
        """
        Check whether the caller may use a page, against their real permissions.

        This used to compare against a dictionary written into this file, listing role
        names like `config_manager` and `data_analyst` that exist nowhere in the system.
        Administrators configure permissions on the Roles & Permissions screen, which
        writes to the database; this check never looked there. It also accepted a `token`
        argument and ignored it. The VAPT report concluded there was no server-side check
        at all -- there was one, it was just reading a hardcoded list.

        Using a page is treated as a write, deliberately. The old dictionary was
        all-or-nothing per page, so translating it to `read` would have handed every
        account that can see the screen the ability to upload -- a widening, not a fix.
        """
        required = PAGE_RESOURCES.get(page_name)
        if not required:
            # Unknown page: fall back to administrator, as the old default did.
            required = ("admin",)

        user = {"roles": user_roles or [], "permissions": user_permissions or []}
        return is_allowed(user, required, ACTION_WRITE)

    async def get_user_accessible_pages(
        self,
        user_roles: List[str],
        token: str,
        user_permissions: Optional[List[str]] = None,
    ) -> List[str]:
        """List the pages this caller may use, by the same rule as check_page_access."""
        user = {"roles": user_roles or [], "permissions": user_permissions or []}
        return [
            page for page, resources in PAGE_RESOURCES.items()
            if is_allowed(user, resources, ACTION_WRITE)
        ]


# Global auth client instance
auth_client = AuthClient()
