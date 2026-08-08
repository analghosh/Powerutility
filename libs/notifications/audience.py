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

    async def user_ids_for_usernames(self, usernames: List[str]) -> List[str]:
        """Resolve usernames to user ids.

        Bid documents record their maker in `created_by` as a username string --
        verified in live data: "admin", "mukul", "ptcindia", "Ashutosh" -- not as an
        id, so those names must be resolved before they can be used as recipients.
        """
        if not usernames:
            return []
        return [
            str(doc["_id"])
            async for doc in self.users.find(
                {
                    "username": {"$in": list(usernames)},
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


class UsernamesFromContext(Audience):
    """Recipients the caller names by username in the event context.

    Distinct from SpecificUsers, whose list is fixed when the registry is defined:
    these names are only known at emit time, e.g. the makers of the bids being approved.
    """

    def __init__(self, context_key: str):
        self.context_key = context_key

    async def resolve(self, context: Dict[str, Any]) -> List[str]:
        names = [n for n in (context.get(self.context_key) or []) if n]
        if not names:
            return []
        directory: AuthDirectory = context[DIRECTORY_KEY]
        return await directory.user_ids_for_usernames(names)


class AnyOf(Audience):
    """Union of several rules, order preserved and de-duplicated.

    Some events have more than one interested party: an approval both tells the maker
    their bid was decided and tells the other checkers it has been handled.
    """

    def __init__(self, *rules: Audience):
        self.rules = rules

    async def resolve(self, context: Dict[str, Any]) -> List[str]:
        seen, ordered = set(), []
        for rule in self.rules:
            for user_id in await rule.resolve(context):
                if user_id not in seen:
                    seen.add(user_id)
                    ordered.append(user_id)
        return ordered
