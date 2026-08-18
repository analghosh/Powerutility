"""
The page permission model (VAPT F6, revised 2026-08-18).

PTC's rule: if a role has been given a page, it may do anything on that page. The
vendor's data model already carried a per-page action list, but the Role Management
screen hardcodes every grant to ["read"] and hides the Write/Delete checkboxes, so no
role could ever hold a write. Rather than un-hide a control PTC does not want, the verb
decision moves off the grant row and onto the role.

A `menu_roles` row therefore means one thing only: this role has this page. The verbs
come from `roles.is_read_only`.

One exception to "hold a page, get full rights on it": the administrative resources in
ADMIN_RESOURCES never receive write or delete from a page grant, for any role. See the
comment on that constant.

`approve` is not here on purpose. Holding a bid screen grants read/write/delete, which
covers creating and editing a bid; approving it and submitting it to the exchange remain
reachable only through the guard's SUPER_ROLES bypass. That keeps maker and checker
distinct. Adding "approve" to PAGE_VERBS is a business decision for PTC, not a tidy-up.

Nothing here touches the database, settings or the network -- it is imported by the auth
service, by tools/resync_role_permissions.py and by the tests, and all three must agree.
"""
from __future__ import annotations

from typing import Iterable, List, Tuple

PAGE_VERBS: Tuple[str, ...] = ("read", "write", "delete")
READ_ONLY_VERBS: Tuple[str, ...] = ("read",)

# Resources whose write side is never conferred by holding a page.
#
# The page model's rule -- hold a page, get full rights on it -- is right for operational
# screens and wrong for the administrative ones. `global_authz_guard` resolves
# DELETE /api/v1/users/{id} to exactly `users:delete` and satisfies it by string
# membership, so minting that string from a menu grant hands user administration to every
# role holding the Users page. Measured before this guard existed: `user` (740 live
# accounts), `operator`, `checker` and `test123` all received users:delete and
# admin:delete, and a plain `user` account was able to delete another user.
#
# Withholding these costs real administrators nothing: `admin` and `superadmin` resolve to
# ["*"] at login (user_repository.py, SUPER_ROLES), so they never depend on page-derived
# strings. A non-super role that genuinely needs user administration must be granted it
# explicitly rather than acquiring it from a menu assignment.
#
# "menu" and "menus" are here for the same reason, one level down: the auth service also
# guards its menu router this way (services/auth/app/main.py), and
# libs/core/authz/config.py derives the resource "menu" for it, so DELETE
# /api/v1/menu/{menu_id} requires exactly menu:delete. This is latent, not live -- no
# seeded menu slugs to "menu", and creating one requires menu:create, which only admin
# and superadmin hold. Keep it capped anyway: the cost is one entry in this set, and it
# is exactly the shape of gap that made ADMIN_RESOURCES necessary for users/roles/admin
# in the first place. Do not delete this as "unnecessary" without re-checking that no
# menu can ever be named "Menu" or "Menus".
ADMIN_RESOURCES = frozenset({"users", "roles", "admin", "menu", "menus"})


def verbs_for_role(is_read_only: bool) -> Tuple[str, ...]:
    """The verbs a role holds on every page it has been granted."""
    return READ_ONLY_VERBS if is_read_only else PAGE_VERBS


def verbs_for_resource(resource_name: str, is_read_only: bool) -> Tuple[str, ...]:
    """
    The verbs a role holds on one particular page.

    Same as `verbs_for_role`, except that an administrative resource is capped at `read`
    for every role, read-only or not. See ADMIN_RESOURCES for why.
    """
    if resource_name in ADMIN_RESOURCES:
        return READ_ONLY_VERBS
    return verbs_for_role(is_read_only)


def permission_strings(resource_names: Iterable[str], is_read_only: bool) -> List[str]:
    """
    Build the flat `resource:action` list stored on `roles.permissions`.

    Duplicates collapse while preserving first-seen order: two menus can resolve to the
    same resource name (`Setup` and `Administration` both become `admin`), and a stable
    order keeps a re-sync from producing a spurious diff.

    The verb set is decided per resource, not once for the call, so that ADMIN_RESOURCES
    are capped at `read` while every other page in the same role keeps read/write/delete.
    Both consumers -- MenuRoleRepository._sync_role_permissions at runtime and
    tools/resync_role_permissions.py in bulk -- come through here, so they cannot drift.
    """
    out: List[str] = []
    for name in resource_names:
        if not name:
            continue
        for verb in verbs_for_resource(name, is_read_only):
            out.append(f"{name}:{verb}")
    return list(dict.fromkeys(out))
