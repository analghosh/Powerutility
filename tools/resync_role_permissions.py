#!/usr/bin/env python
"""
Resync roles.permissions from the pages each role has been granted.

Why this exists
---------------
This tool used to seed a hand-authored matrix that classified every menu as masters /
admin / ingestion / approval / operational and picked verbs per classification. Under
PTC's page model (libs/authz/page_model.py) that classification is dead: holding a page
is the whole decision. Read the grants, derive the permissions, done.

What it writes
--------------
Two collections, because the runtime reads one and the UI rewrites it from the other:

* `menu_roles.permissions` -- the source of truth the Roles & Permissions screen edits.
  Under the page model a grant row means only "this role has this page"; verbs no longer
  live here, so a normalised row is just ["read"]. Only touched with --normalise-grants.
* `roles.permissions`      -- the flattened `resource:action` list the auth service puts
  in the token. It is a stale snapshot of menu_roles (the sync only fires on
  grant/revoke), so writing only menu_roles would change nothing at runtime, and writing
  only roles would be silently undone the next time an admin touches a menu grant.

The rule
--------
Not a business decision -- PTC's page model: a role that holds a page gets read, write
and delete on it; a role flagged `is_read_only` is capped at read. `approve` is granted
by this tool to nobody; it stays reachable only through the guard's SUPER_ROLES bypass,
which keeps maker and checker distinct. The administrative resources (page_model's
ADMIN_RESOURCES: users, roles, admin, menu, menus) are capped at read for every role --
holding the Users page must not confer the right to delete users, and holding the Menu
page must not confer the right to delete a menu.

Usage
-----
    python tools/resync_role_permissions.py --dry-run            # print the diff, change nothing
    python tools/resync_role_permissions.py --apply              # write, after taking a backup
    python tools/resync_role_permissions.py --apply --normalise-grants
    python tools/resync_role_permissions.py --revert FILE        # restore a backup exactly

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

# Run from anywhere, including a production box. `libs` is only importable here because a
# development venv carries an editable-install .pth file; on the server there is no such
# .pth and the import below dies before argparse ever runs. Put the repo root on the path
# ourselves so the tool is standalone, as its predecessor was.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from libs.authz.page_model import ADMIN_RESOURCES, permission_strings  # noqa: E402

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
    """
    Rebuild every role's permissions from the pages it holds.

    Under PTC's page model, holding a page grants read/write/delete on it; a role flagged
    is_read_only is capped at read. There is no per-menu classification any more -- the
    matrix this replaced decided masters / admin / ingestion / approval / operational per
    screen, and none of that survives the rule "page access means full rights".

    The four-tuple shape is unchanged, because main(), apply_plan() and backup() all
    unpack it positionally:

      grant_updates -- (menu_role _id, ["read"]) for rows still carrying stale verbs.
                       A grant row now means "has this page" and nothing more; rows left
                       over from the earlier matrix assert an authority nothing reads.
                       Only written when --normalise-grants is passed.
      role_updates  -- (ObjectId(role_id), role_name, permissions, old_count)
      roles, menus  -- the raw documents, keyed by str(_id), for backup()
    """
    roles = {str(r["_id"]): r for r in db["roles"].find({})}
    # Mirrors MenuRepository.get_menu_by_id, which filters is_deleted the same way. The
    # runtime sync resolves menus through that repository, so reading every menu here
    # would let a soft-deleted page with a surviving active grant contribute a resource
    # under --apply but not under the incremental sync: a role's permissions would then
    # depend on which of the two paths ran last.
    menus = {str(m["_id"]): m for m in db["menus"].find({"is_deleted": {"$ne": True}})}

    grant_updates = []
    resources_by_role = defaultdict(list)

    # is_active is REQUIRED here. The old code read every grant row, active or not, and
    # got away with it because the matrix handed those rows `read`. Under the page model
    # an inactive row would become a write: `user` alone carries 20 revoked grants across
    # 740 live accounts. MenuRoleRepository.get_menu_roles_by_role_id already filters
    # this way -- the tool has to agree with it or the two produce different permissions
    # for the same role.
    for mr in db["menu_roles"].find({"is_active": True}):
        role = roles.get(str(mr.get("role_id")))
        menu = menus.get(str(mr.get("menu_id")))
        if not role or not menu:
            continue

        resources_by_role[str(mr["role_id"])].append(
            resource_name(menu.get("name") or "", MENU_TO_RESOURCE)
        )

        if sorted(mr.get("permissions") or []) != [READ]:
            grant_updates.append((mr["_id"], [READ]))

    role_updates = []
    for role_id, role in roles.items():
        resource_names = resources_by_role.get(role_id, [])
        perms = permission_strings(resource_names, bool(role.get("is_read_only", False)))
        current = role.get("permissions") or []
        if sorted(perms) != sorted(current):
            role_updates.append((ObjectId(role_id), role.get("name"), perms, len(current)))

    return grant_updates, role_updates, roles, menus


# Permissions that belong to no menu, so the menu walk below cannot produce them. They are
# granted to no role by this matrix: admin and superadmin already pass the MFA admin gate
# on their role, and removing somebody's second factor is not a delegation to make by
# default. Listing them keeps the catalogue -- what the Roles & Permissions screen offers
# an administrator -- a superset of what the code actually checks.
NON_MENU_PERMISSIONS = [
    "mfa:emergency_disable",
    "mfa:reset_rate_limit",
]


def catalog_permissions(db) -> list:
    """Every resource:action the matrix can produce, so the catalog stays a superset."""
    menus = list(db["menus"].find({}, {"name": 1}))
    wanted = set(NON_MENU_PERMISSIONS)
    for m in menus:
        resource = resource_name(m.get("name") or "", MENU_TO_RESOURCE)
        for action in (READ, WRITE, DELETE, APPROVE):
            wanted.add(f"{resource}:{action}")
    return sorted(wanted)


# Strings an operator must see in full when reading a --dry-run: the administrative
# resources, plus the two screens that hold the exchange credentials and the exchange
# switch. Everything else can be summarised; these never are.
ALWAYS_SHOWN_RESOURCES = frozenset(ADMIN_RESOURCES | {"iex_api_detail", "power_exchange"})

# How many ordinary (non-ALWAYS_SHOWN) strings to list per side before summarising.
MAX_LISTED = 12


def _resource_of(permission: str) -> str:
    return permission.split(":", 1)[0]


def _diff_lines(marker: str, permissions: list) -> list:
    """
    Render one side (added or removed) of a role's permission diff.

    `operator 92 -> 159 permissions (53 write)` is not enough to approve a rollout: that
    line is exactly what let `users:delete` reach four roles through seven reviews. So
    the strings themselves are printed -- but the risk is carried by the verb, not the
    resource. An ordinary `dam_block_bid:write` past the twelfth entry could still hide
    behind "... and N more" under a resource-only rule, and on a role picking up ~150
    strings that is most of them, including exactly the write grants this tool exists to
    create. So every :write, :delete and :approve string is always shown, on any
    resource, with no exceptions. ALWAYS_SHOWN_RESOURCES still does the rest of the job:
    it keeps :read on the administrative and exchange resources (users, roles, admin,
    menu, menus, iex_api_detail, power_exchange) always-shown too, ordered first, because
    a `:read` grant on the Users page is worth an approver seeing eagerly and not only
    its writes. Only an ordinary :read string on an ordinary resource is ever summarised.
    """
    if not permissions:
        return []

    def _always_shown(p: str) -> bool:
        return _resource_of(p) in ALWAYS_SHOWN_RESOURCES or not p.endswith(f":{READ}")

    always = [p for p in permissions if _always_shown(p)]
    ordinary = [p for p in permissions if not _always_shown(p)]
    shown = always + ordinary[:MAX_LISTED]
    lines = [f"      {marker} ({len(permissions)}): {', '.join(shown)}"]
    hidden = len(ordinary) - len(ordinary[:MAX_LISTED])
    if hidden:
        lines.append(f"          ... and {hidden} more")
    return lines


def render_role_diff(name, old_permissions, new_permissions) -> list:
    """The lines --dry-run and --apply print for one role: counts, then what changes."""
    old = list(old_permissions or [])
    new = list(new_permissions or [])
    old_set, new_set = set(old), set(new)
    added = [p for p in new if p not in old_set]
    removed = [p for p in old if p not in new_set]
    writes = sum(1 for p in new if p.endswith(f":{WRITE}"))
    approves = sum(1 for p in new if p.endswith(f":{APPROVE}"))
    lines = [f"  {(name or ''):24s} {len(old):3d} -> {len(new):3d} permissions "
             f"({writes} write, {approves} approve)"]
    lines += _diff_lines("+", added)
    lines += _diff_lines("-", removed)
    return lines


def backup(db, path):
    payload = {
        "taken_at": datetime.now().isoformat(),
        "menu_roles": [
            {"_id": str(d["_id"]), "permissions": d.get("permissions")}
            for d in db["menu_roles"].find({}, {"permissions": 1})
        ],
        # is_read_only is captured because it decides the verbs: a backup that restored
        # only `permissions` would put the strings back while leaving the flag that
        # generated them at whatever it had become, so the next sync would undo the revert.
        "roles": [
            {"_id": str(d["_id"]), "name": d.get("name"),
             "permissions": d.get("permissions"),
             "is_read_only": bool(d.get("is_read_only", False))}
            for d in db["roles"].find({}, {"permissions": 1, "name": 1, "is_read_only": 1})
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
        fields = {"permissions": doc["permissions"]}
        # Older backups predate the flag; those roles keep whatever they currently have
        # rather than being silently reset to not-read-only.
        if "is_read_only" in doc:
            fields["is_read_only"] = bool(doc["is_read_only"])
        db["roles"].update_one({"_id": ObjectId(doc["_id"])}, {"$set": fields})
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
    parser.add_argument(
        "--normalise-grants", action="store_true",
        help="also rewrite menu_roles rows to ['read'] (they no longer carry verbs)",
    )
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
    if not args.normalise_grants:
        grant_updates = []
    catalog = catalog_permissions(db)

    print(f"menu_role grants to change: {len(grant_updates)}")
    print("role permission sets to rewrite (+ granted, - withdrawn):")
    for role_id, name, perms, _old in sorted(role_updates, key=lambda r: r[1] or ""):
        current = (roles.get(str(role_id)) or {}).get("permissions") or []
        for line in render_role_diff(name, current, perms):
            print(line)
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
    # Not uniform across services, and the difference decides how fast a mistake bites.
    print("\nProcessing service: users must obtain a new token for this to take effect "
          "(permissions come from the JWT, minted at login and at /validate).")
    print("Auth service admin routes: effective immediately, including for sessions "
          "already signed in -- compute_effective_permissions reads db.roles live.")
    print(f"To undo: python {sys.argv[0]} --revert {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
