"""
Auth Client Service for Ingestion Service
Delegates all token validation to the Auth Service via libs.auth_client.
No local JWT secret needed.
"""

import logging
from typing import Optional, Dict, Any, List

from libs.auth_client import AuthClient as _LibAuthClient
from ..core.config import settings

logger = logging.getLogger(__name__)


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

    async def check_page_access(self, user_roles: List[str], page_name: str, token: str) -> bool:
        """Check if user has access to a specific page based on roles."""
        page_permissions = {
            "Data Ingestion": ["admin", "operator"],
            "Provider Management": ["admin", "config_manager"],
            "System Administration": ["admin"],
            "User Management": ["admin"],
            "Reports": ["admin", "data_analyst", "manager"],
        }
        required_roles = page_permissions.get(page_name, ["admin"])
        return any(role in user_roles for role in required_roles)

    async def get_user_accessible_pages(self, user_roles: List[str], token: str) -> List[str]:
        """Get list of pages accessible to user based on their roles."""
        page_permissions = {
            "Data Ingestion": ["admin", "data_analyst", "operator"],
            "Provider Management": ["admin", "config_manager"],
            "System Administration": ["admin"],
            "User Management": ["admin"],
            "Reports": ["admin", "data_analyst", "manager"],
        }
        return [
            page for page, roles in page_permissions.items()
            if any(role in user_roles for role in roles)
        ]


# Global auth client instance
auth_client = AuthClient()
