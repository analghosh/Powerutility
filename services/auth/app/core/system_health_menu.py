"""
Put the System Health page in the Administration menu when auth starts.

``DatabaseSeeder.seed_menus`` only seeds an empty menus collection, so a page
added after a deployment's first boot never reaches its menu without someone
editing the database. On a deployment where nobody can reach the database,
the page is simply unreachable. This does that one edit itself:

* If the menu row is missing, it is created under Administration and granted
  (read) to every admin-type role: admin, superadmin, super_admin, the same
  roles the System Health API lets in by name.
* If the row already exists, in any state, nothing is touched. Grants made or
  removed later in Roles & Permissions are left alone, and a row someone
  deleted stays deleted.

It writes ``menus`` and ``menu_roles`` only, never ``roles.permissions``.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict

logger = logging.getLogger(__name__)

MENU_NAME = "System Health"  # mints system_health:read
MENU_PATH = "/admin/system-health"
MENU_ICON = "🩺"
PARENT_PATH = "/admin"

# Matches SUPER_ADMIN_ROLES in the processing system_health_service.
ADMIN_ROLE_NAMES = frozenset({"admin", "superadmin", "super_admin"})


async def ensure_system_health_menu(db) -> Dict[str, Any]:
    """
    Create the System Health menu row and its admin grants if the row is
    missing. Returns what it did, for the startup log.
    """
    parent = await db["menus"].find_one({"path": PARENT_PATH, "parent_id": None})
    if not parent:
        return {"created": False, "granted": [], "reason": "no Administration menu"}
    parent_id = str(parent["_id"])

    if await db["menus"].find_one({"path": MENU_PATH}, {"_id": 1}):
        return {"created": False, "granted": [], "reason": "already present"}

    orders = [menu.get("order") or 0 async for menu in db["menus"].find({"parent_id": parent_id}, {"order": 1})]
    now = datetime.now(timezone.utc)
    row = {
        "name": MENU_NAME,
        "icon": MENU_ICON,
        "parent_id": parent_id,
        "path": MENU_PATH,
        "order": max(orders, default=0) + 1,
        "is_active": True,
        "is_deleted": False,
        "created_at": now,
        "updated_at": now,
    }
    # An upsert rather than an insert, so two processes starting together
    # can't both add the row; only the one that created it grants it.
    result = await db["menus"].update_one({"path": MENU_PATH}, {"$setOnInsert": row}, upsert=True)
    if result.upserted_id is None:
        return {"created": False, "granted": [], "reason": "created by another process"}
    menu_id = str(result.upserted_id)

    granted = []
    async for role in db["roles"].find({}, {"name": 1}):
        name = str(role.get("name") or "")
        if name.lower() not in ADMIN_ROLE_NAMES:
            continue
        role_id = str(role["_id"])
        await db["menu_roles"].update_one(
            {"menu_id": menu_id, "role_id": role_id},
            {"$setOnInsert": {
                "menu_id": menu_id,
                "role_id": role_id,
                "permissions": ["read"],
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }},
            upsert=True,
        )
        granted.append(name)

    return {"created": True, "granted": granted, "reason": None}
