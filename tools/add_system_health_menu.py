#!/usr/bin/env python
"""
Add the System Health page to the Administration menu of an existing deployment.

The auth seeder only seeds menus into an EMPTY menus collection (and its
force_reload path deletes every menu, and every grant with them), so on UAT and
PROD the row has to be added directly. This script does exactly that, and is
safe to run more than once: it creates the row only if it is missing and grants
it only to roles that don't already have it.

It writes to auth_db.menus and auth_db.menu_roles only. It does NOT touch
roles.permissions: granting through the Roles & Permissions screen rebuilds that
list from menu rows, which has dropped permissions before.

    ./.venv/bin/python tools/add_system_health_menu.py                    # dry run
    ./.venv/bin/python tools/add_system_health_menu.py --apply
    ./.venv/bin/python tools/add_system_health_menu.py --apply --role superadmin --role admin

MONGODB_URI and AUTH_DB_NAME are read from the environment (defaults:
mongodb://localhost:27017 and auth_db). Other roles can be given the page later
from Roles & Permissions.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

from pymongo import MongoClient

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
AUTH_DB_NAME = os.environ.get("AUTH_DB_NAME", "auth_db")

MENU_NAME = "System Health"  # mints system_health:read
MENU_PATH = "/admin/system-health"
MENU_ICON = "🩺"
PARENT_PATH = "/admin"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    parser.add_argument(
        "--role",
        action="append",
        dest="roles",
        help="role name to grant the page to; repeatable (default: superadmin)",
    )
    args = parser.parse_args()
    role_names = args.roles or ["superadmin"]
    mode = "APPLY" if args.apply else "DRY RUN"

    db = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)[AUTH_DB_NAME]
    print(f"[{mode}] {AUTH_DB_NAME} on {MONGODB_URI.split('@')[-1]}")

    parent = db.menus.find_one({"path": PARENT_PATH, "parent_id": None})
    if not parent:
        print(f"No root menu at {PARENT_PATH}; nothing done.", file=sys.stderr)
        return 1
    parent_id = str(parent["_id"])

    now = datetime.now(timezone.utc)
    menu = db.menus.find_one({"path": MENU_PATH})
    if menu:
        menu_id = str(menu["_id"])
        print(f"Menu row already present: {menu['name']} ({menu_id})")
    else:
        sibling_orders = [m.get("order") or 0 for m in db.menus.find({"parent_id": parent_id})]
        row = {
            "name": MENU_NAME,
            "icon": MENU_ICON,
            "parent_id": parent_id,
            "path": MENU_PATH,
            "order": max(sibling_orders, default=0) + 1,
            "is_active": True,
            "is_deleted": False,
            "created_at": now,
            "updated_at": now,
        }
        if args.apply:
            menu_id = str(db.menus.insert_one(row).inserted_id)
            print(f"Menu row created: {MENU_NAME} ({menu_id}) under {parent['name']}")
        else:
            menu_id = None
            print(f"Would create menu row: {row}")

    missing_roles = []
    for name in role_names:
        role = db.roles.find_one({"name": name})
        if not role:
            missing_roles.append(name)
            continue
        role_id = str(role["_id"])
        if menu_id and db.menu_roles.find_one({"menu_id": menu_id, "role_id": role_id}):
            print(f"Role {name!r} already has the page")
            continue
        grant = {
            "menu_id": menu_id,
            "role_id": role_id,
            "permissions": ["read"],
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        if args.apply:
            db.menu_roles.insert_one(grant)
            print(f"Granted to role {name!r}")
        else:
            print(f"Would grant to role {name!r}")

    if missing_roles:
        print(f"No role named {', '.join(map(repr, missing_roles))}; not granted.", file=sys.stderr)
        return 1
    if not args.apply:
        print("Dry run only. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
