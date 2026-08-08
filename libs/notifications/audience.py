"""Audience rules: turn "who should hear about this" into user_ids.

Deliberately isolated from the emitter so recipient logic is testable without
writing or publishing anything.

The resolution chain is not the obvious one. `users.permissions` exists on every
user document and is empty on all 815 of them in live data, and `users.roles`
holds role *names* rather than ids. So the only working path is:

    roles.permissions -> roles.name -> users.roles
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from libs.notifications.models import Audience

logger = logging.getLogger(__name__)

DIRECTORY_KEY = "_directory"


class AuthDirectory:
    """Read-only view of auth_db used for recipient lookup."""

    def __init__(self, users_collection, roles_collection):
        self.users = users_collection
        self.roles = roles_collection

    async def users_with_permission(self, permission: str) -> List[str]:
        role_names = [
            doc["name"]
            async for doc in self.roles.find({"permissions": permission}, {"name": 1})
            if doc.get("name")
        ]
        if not role_names:
            logger.warning("No role grants %s; nobody will be notified", permission)
            return []

        return [
            str(doc["_id"])
            async for doc in self.users.find(
                {
                    "roles": {"$in": role_names},
                    "is_active": True,
                    "is_deleted": {"$ne": True},
                },
                {"_id": 1},
            )
        ]

    async def all_active_user_ids(self) -> List[str]:
        return [
            str(doc["_id"])
            async for doc in self.users.find(
                {"is_active": True, "is_deleted": {"$ne": True}}, {"_id": 1}
            )
        ]


class HasPermission(Audience):
    """Everyone whose role grants this permission.

    `permission` may contain format placeholders resolved from the event context,
    so one registry entry covers DAM, GDAM and HPDAM.
    """

    def __init__(self, permission: str):
        self.permission = permission

    async def resolve(self, context: Dict[str, Any]) -> List[str]:
        directory: AuthDirectory = context[DIRECTORY_KEY]
        return await directory.users_with_permission(self.permission.format(**context))


class SpecificUsers(Audience):
    def __init__(self, user_ids: List[str]):
        self.user_ids = user_ids

    async def resolve(self, context: Dict[str, Any]) -> List[str]:
        return list(self.user_ids)


class Everyone(Audience):
    async def resolve(self, context: Dict[str, Any]) -> List[str]:
        directory: AuthDirectory = context[DIRECTORY_KEY]
        return await directory.all_active_user_ids()
