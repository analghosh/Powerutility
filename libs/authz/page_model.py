"""
The page permission model (VAPT F6, revised 2026-08-18).

PTC's rule: if a role has been given a page, it may do anything on that page. The
vendor's data model already carried a per-page action list, but the Role Management
screen hardcodes every grant to ["read"] and hides the Write/Delete checkboxes, so no
role could ever hold a write. Rather than un-hide a control PTC does not want, the verb
decision moves off the grant row and onto the role.

A `menu_roles` row therefore means one thing only: this role has this page. The verbs
come from `roles.is_read_only`.

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


def verbs_for_role(is_read_only: bool) -> Tuple[str, ...]:
    """The verbs a role holds on every page it has been granted."""
    return READ_ONLY_VERBS if is_read_only else PAGE_VERBS


def permission_strings(resource_names: Iterable[str], is_read_only: bool) -> List[str]:
    """
    Build the flat `resource:action` list stored on `roles.permissions`.

    Duplicates collapse while preserving first-seen order: two menus can resolve to the
    same resource name (`Setup` and `Administration` both become `admin`), and a stable
    order keeps a re-sync from producing a spurious diff.
    """
    verbs = verbs_for_role(is_read_only)
    out: List[str] = []
    for name in resource_names:
        if not name:
            continue
        for verb in verbs:
            out.append(f"{name}:{verb}")
    return list(dict.fromkeys(out))
