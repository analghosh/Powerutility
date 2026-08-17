#!/usr/bin/env python
"""
Seed the permission matrix the F6 route guards check against.

Why this exists
---------------
Before this script, `auth_db.menu_roles` held 385 grants and every one of them was
`read`. Not a single write grant existed anywhere in the database. Turning the guards on
against that data would have returned 403 for every write by every account that is not
`admin` or `superadmin` -- which is to say, the platform.

What it writes
--------------
Two collections, because the runtime reads one and the UI rewrites it from the other:

* `menu_roles.permissions` -- the source of truth the Roles & Permissions screen edits.
* `roles.permissions`      -- the flattened `resource:action` list the auth service puts
  in the token. It is a stale snapshot of menu_roles (the sync only fires on
  grant/revoke), so writing only menu_roles would change nothing at runtime, and writing
  only roles would be silently undone the next time an admin touches a menu grant.

The matrix
----------
Interim, and explicitly not a business decision -- PTC has not yet answered which roles
should hold write and approve on each screen. It is the least-privilege reading of what
the existing UI already enforces client-side:

* MASTERS and ADMIN screens  -> read for everyone who has the menu; write/delete for
  nobody but admin/superadmin. This is what closes VAPT F2's second half, F3 and F4.
* APPROVAL screens           -> read+write; `approve` for nobody but admin/superadmin,
  so no ordinary account can push a bid to the exchange.
* OPERATIONAL screens        -> read+write, matching what those roles can do today.
* `viewer`                   -> read everywhere. It is named "Read-only user".
* admin / superadmin         -> unaffected; they resolve to `*` in the auth service.

Usage
-----
    python tools/seed_authz_permissions.py --dry-run     # print the diff, change nothing
    python tools/seed_authz_permissions.py --apply       # write, after taking a backup
    python tools/seed_authz_permissions.py --revert FILE # restore a backup exactly

Every --apply writes a timestamped backup of both collections first. --revert replays it
document by document, so an unwanted rollout is undone without a deploy. For an immediate
stop that needs no database access at all, set AUTHZ_MODE=warn on the processing service.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

from bson import ObjectId
from pymongo import MongoClient

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
AUTH_DB_NAME = os.getenv("AUTH_DB_NAME", "auth_db")


def _safe_url(url: str) -> str:
    """mongodb://user:secret@host/ -> mongodb://user:***@host/"""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    creds, _, host = rest.rpartition("@")
    user = creds.split(":")[0]
    return f"{scheme}://{user}:***@{host}"


def announce_target(db) -> None:
    """
    Say out loud which database is about to be changed.

    The defaults are localhost/auth_db, and on the production box those defaults are
    *correct* -- which is exactly what makes a mistake quiet. Both UAT and production run
    Mongo on localhost, so the only thing distinguishing them is which machine the shell
    is on.
    """
    print(f"target : {_safe_url(MONGODB_URL)}  db={AUTH_DB_NAME}")
    print(f"         {db['roles'].count_documents({})} roles, "
          f"{db['menu_roles'].count_documents({})} menu grants, "
          f"{db['users'].count_documents({})} users")


def confirm(action: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(f"refusing to {action} without --yes when stdin is not a terminal")
        return False
    return input(f"{action} against {AUTH_DB_NAME} at {_safe_url(MONGODB_URL)}? [y/N] ").strip().lower() == "y"

READ = "read"
WRITE = "write"
DELETE = "delete"
APPROVE = "approve"

# Roles that bypass the matrix entirely in the auth service.
SUPER_ROLES = {"admin", "superadmin"}

# Menu names whose screens administer master data or the platform itself. Write access
# here is what an attacker wants: exchange credentials, plants, price groups, users.
MASTERS_MENUS = {
    "Masters", "Plant Details", "Price Groups", "IEX API Detail", "Power Exchange",
    "Parameter", "Bid Market", "DAM Limit Values", "RTM Limit Values", "OCF Limit Values",
}
ADMIN_MENUS = {
    "Administration", "Users", "Roles & Permissions", "Audit Log", "Setup",
}

# Data-entry screens served by the ingestion service. Its own check used to be a
# hardcoded dict granting "Data Ingestion" to admin and operator; these grants reproduce
# that in data, so replacing the dict with a real permission check does not quietly
# change who can upload. `user` and `viewer` keep read, as before.
INGESTION_MENUS = {
    "Upload CSV", "DC SG Template", "SCADA Data", "SCADA - Data Input",
}
INGESTION_WRITE_ROLES = {"operator"}

# Read-only roles keep exactly what they have.
READ_ONLY_ROLES = {"viewer"}


def classify(menu_name: str) -> str:
    if menu_name in MASTERS_MENUS:
        return "masters"
    if menu_name in ADMIN_MENUS:
        return "admin"
    if menu_name in INGESTION_MENUS:
        return "ingestion"
    if "Approval" in (menu_name or ""):
        return "approval"
    return "operational"


def permissions_for(role_name: str, menu_name: str) -> list:
    """The interim matrix, in one place."""
    if role_name.lower() in SUPER_ROLES:
        # Harmless to grant, and it keeps the UI honest if the `*` shortcut is ever removed.
        return [READ, WRITE, DELETE, APPROVE]
    if role_name.lower() in READ_ONLY_ROLES:
        return [READ]

    kind = classify(menu_name)
    if kind in {"masters", "admin"}:
        return [READ]
    if kind == "ingestion":
        # Matches the dict this replaces: admin and operator could ingest, nobody else.
        return [READ, WRITE] if role_name.lower() in INGESTION_WRITE_ROLES else [READ]
    # operational and approval screens: makers may act, only admins may approve
    return [READ, WRITE]


def resource_name(menu_name: str, mapping: dict) -> str:
    if menu_name in mapping:
        return mapping[menu_name]
    return (menu_name or "").lower().replace(" ", "_").replace("-", "_")


# Mirrors MenuRoleRepository._get_resource_name_from_menu. Kept in step deliberately: if
# the two ever diverge, permissions are granted under names no guard asks for.
MENU_TO_RESOURCE = {
    "Users": "users", "Roles": "roles", "Administration": "admin", "Permissions": "admin",
    "Audit Logs": "admin", "Setup": "admin", "Dashboard": "dashboard", "Overview": "overview",
    "Management Snapshot": "management_snapshot", "DSM Overview": "dsm_overview",
    "Market Integrator": "market_integrator", "Forecast": "forecast",
    "Forecast Analysis": "forecast_analysis", "Demand Forecast": "demand_forecast",
    "Supply": "supply", "Non-Renewable Supply": "non_renewable_supply",
    "Renewable Supply": "renewable_supply", "URS": "urs",
    "Day Ahead Marketing": "day_ahead_marketing", "DAM Scheduling": "dam_scheduling",
    "DAM Block Bid": "dam_block_bid", "DAM Single Bid": "dam_single_bid",
    "DAM Bid Status": "dam_bid_status", "Real Time Market": "real_time_market",
    "RTM Scheduling": "rtm_scheduling", "RTM Block Bid": "rtm_block_bid",
    "RTM Single Bid": "rtm_single_bid", "RTM Bid Status": "rtm_bid_status",
    "Weather": "weather", "Masters": "masters", "Plant Detail": "plant_detail",
    "DAM Limit Values": "dam_limit_values", "RTM Limit Values": "rtm_limit_values",
    "Reports": "reports", "Standard Reports": "standard_reports",
}


def build_plan(db):
    roles = {str(r["_id"]): r for r in db["roles"].find({})}
    menus = {str(m["_id"]): m for m in db["menus"].find({})}

    grant_updates = []          # (menu_role _id, new permissions)
    role_permissions = defaultdict(list)

    for mr in db["menu_roles"].find({}):
        role = roles.get(str(mr.get("role_id")))
        menu = menus.get(str(mr.get("menu_id")))
        if not role or not menu:
            continue
        role_name = role.get("name") or ""
        menu_name = menu.get("name") or ""

        wanted = permissions_for(role_name, menu_name)
        if sorted(wanted) != sorted(mr.get("permissions") or []):
            grant_updates.append((mr["_id"], wanted))

        resource = resource_name(menu_name, MENU_TO_RESOURCE)
        for action in wanted:
            role_permissions[str(role["_id"])].append(f"{resource}:{action}")

    role_updates = []
    for role_id, perms in role_permissions.items():
        unique = list(dict.fromkeys(perms))
        current = roles[role_id].get("permissions") or []
        if sorted(unique) != sorted(current):
            role_updates.append((ObjectId(role_id), roles[role_id].get("name"), unique, len(current)))

    return grant_updates, role_updates, roles, menus


def catalog_permissions(db) -> list:
    """Every resource:action the matrix can produce, so the catalog stays a superset."""
    menus = list(db["menus"].find({}, {"name": 1}))
    wanted = set()
    for m in menus:
        resource = resource_name(m.get("name") or "", MENU_TO_RESOURCE)
        for action in (READ, WRITE, DELETE, APPROVE):
            wanted.add(f"{resource}:{action}")
    return sorted(wanted)


def backup(db, path):
    payload = {
        "taken_at": datetime.now().isoformat(),
        "menu_roles": [
            {"_id": str(d["_id"]), "permissions": d.get("permissions")}
            for d in db["menu_roles"].find({}, {"permissions": 1})
        ],
        "roles": [
            {"_id": str(d["_id"]), "name": d.get("name"), "permissions": d.get("permissions")}
            for d in db["roles"].find({}, {"permissions": 1, "name": 1})
        ],
        "permissions_catalog": [
            {"_id": str(d["_id"]), "permissions": d.get("permissions")}
            for d in db["permissions_catalog"].find({}, {"permissions": 1})
        ],
    }
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=1)
    return payload


def apply_plan(db, grant_updates, role_updates, catalog):
    for mr_id, perms in grant_updates:
        db["menu_roles"].update_one(
            {"_id": mr_id},
            {"$set": {"permissions": perms, "updated_at": datetime.now()}},
        )
    for role_id, _name, perms, _old in role_updates:
        db["roles"].update_one(
            {"_id": role_id},
            {"$set": {"permissions": perms, "updated_at": datetime.now()}},
        )
    existing = db["permissions_catalog"].find_one({}) or {}
    merged = sorted(set(existing.get("permissions") or []) | set(catalog))
    if merged != sorted(existing.get("permissions") or []):
        db["permissions_catalog"].update_one(
            {"_id": existing["_id"]} if existing.get("_id") else {},
            {"$set": {"permissions": merged, "updated_at": datetime.now()}},
            upsert=not existing.get("_id"),
        )
    return len(grant_updates), len(role_updates), len(merged)


def revert(db, path):
    with open(path) as fh:
        payload = json.load(fh)
    for doc in payload["menu_roles"]:
        db["menu_roles"].update_one(
            {"_id": ObjectId(doc["_id"])}, {"$set": {"permissions": doc["permissions"]}}
        )
    for doc in payload["roles"]:
        db["roles"].update_one(
            {"_id": ObjectId(doc["_id"])}, {"$set": {"permissions": doc["permissions"]}}
        )
    for doc in payload["permissions_catalog"]:
        db["permissions_catalog"].update_one(
            {"_id": ObjectId(doc["_id"])}, {"$set": {"permissions": doc["permissions"]}}
        )
    return len(payload["menu_roles"]), len(payload["roles"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="print the diff only")
    group.add_argument("--apply", action="store_true", help="write the matrix")
    group.add_argument("--revert", metavar="BACKUP", help="restore a backup file")
    parser.add_argument("--backup-dir", default=".", help="where --apply writes its backup")
    parser.add_argument("--yes", action="store_true",
                        help="skip the confirmation prompt (required for non-interactive runs)")
    args = parser.parse_args()

    db = MongoClient(MONGODB_URL)[AUTH_DB_NAME]
    announce_target(db)
    print()

    if args.revert:
        # A backup records _id values. Those differ between environments, so a UAT backup
        # applied to production silently matches nothing -- or, worse, matches the wrong
        # documents. Check before writing.
        with open(args.revert) as fh:
            payload = json.load(fh)
        ids = [ObjectId(d["_id"]) for d in payload["roles"]]
        present = db["roles"].count_documents({"_id": {"$in": ids}})
        print(f"backup taken {payload.get('taken_at')}: {len(ids)} roles, "
              f"{present} of them exist in this database")
        if present == 0:
            print("none of the backup's documents exist here -- this backup is from a "
                  "different environment. Refusing.")
            return 1
        if present < len(ids):
            print(f"WARNING: {len(ids) - present} of the backup's roles are missing here")

        if not confirm("revert", args.yes):
            return 1
        grants, roles = revert(db, args.revert)
        print(f"reverted {grants} menu_role and {roles} role documents from {args.revert}")
        return 0

    grant_updates, role_updates, roles, _menus = build_plan(db)
    catalog = catalog_permissions(db)

    print(f"menu_role grants to change: {len(grant_updates)}")
    print("role permission sets to rewrite:")
    for _id, name, perms, old in sorted(role_updates, key=lambda r: r[1] or ""):
        writes = sum(1 for p in perms if p.endswith(":write"))
        approves = sum(1 for p in perms if p.endswith(":approve"))
        print(f"  {name:24s} {old:3d} -> {len(perms):3d} permissions "
              f"({writes} write, {approves} approve)")
    print(f"catalog entries after merge: {len(catalog)}")

    if args.dry_run:
        print("\ndry run, nothing written")
        return 0

    if not confirm("apply the permission matrix", args.yes):
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.abspath(os.path.join(args.backup_dir, f"authz-backup-{AUTH_DB_NAME}-{stamp}.json"))
    backup(db, path)
    print(f"\nbackup written to {path}")
    print("KEEP THIS FILE -- it is the only way to put the permission data back, and it "
          "only works against this database.")

    grants, roles_changed, catalog_size = apply_plan(db, grant_updates, role_updates, catalog)
    print(f"applied: {grants} menu_role grants, {roles_changed} roles, "
          f"catalog now {catalog_size} entries")
    print("\nUsers must obtain a new token for this to take effect -- permissions are "
          "resolved at login and at every /validate call.")
    print(f"To undo: python {sys.argv[0]} --revert {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
