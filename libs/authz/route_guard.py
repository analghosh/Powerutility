"""
Route-level authorization for the processing service (VAPT F6).

The service authenticated every request and authorized none of them: 72 write routes
across 45 routers, not one of them checking what the caller was allowed to do. This
module is the check.

Design notes, because the obvious implementations here are all wrong:

* **No I/O.** `AuthMiddleware` already resolved the caller against the auth service and
  left the result on `request.state.current_user`, permissions included. This guard reads
  that dict and nothing else. `libs/core/authz/guard.py` -- which ships in the repo and
  has never been called -- runs a Mongo query and a synchronous Redis round trip per
  request; on a service that already pays an HTTP call to auth per request, that would
  have tripled the cost of every read.

* **The action comes from the HTTP method**, not from a per-route declaration. 72 write
  routes cannot be annotated by hand without missing some, and the ones missed would be
  the dangerous ones. One guard per router, applied at `include_router`, covers every
  route the router will ever have -- including routes added later.

* **Role bypass for admin/superadmin is deliberate.** `roles.permissions` in the live
  database is a stale snapshot of `menu_roles` (the sync only runs on grant/revoke), and
  `superadmin` resolves to a read-only permission set today. Gating the top two roles on
  that data would lock the platform owners out of their own installation.

`AUTHZ_MODE` controls enforcement, so a bad permission matrix is a config change and not
a deploy:

    enforce  (default) -- 403 on a missing permission
    warn               -- allow, log at WARNING with the permission that would be needed
    off                -- no check at all
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, Iterable, Optional, Set

from fastapi import Depends, HTTPException, Request, status

logger = logging.getLogger(__name__)

MODE_ENFORCE = "enforce"
MODE_WARN = "warn"
MODE_OFF = "off"
_VALID_MODES = {MODE_ENFORCE, MODE_WARN, MODE_OFF}

# Roles that are allowed everything regardless of the permission matrix.
SUPER_ROLES = frozenset({"admin", "superadmin"})

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH"})

ACTION_READ = "read"
ACTION_WRITE = "write"
ACTION_DELETE = "delete"
ACTION_APPROVE = "approve"

# Checker-side endpoints. These decide whether a bid reaches the exchange, which is a
# different authority from creating one, so they demand `approve` rather than `write`.
# "/submit-for-approval" is the maker's action and deliberately absent -- note that
# "/approve" is not a substring of it.
APPROVAL_MARKERS = (
    "/approve",
    "/reject",
    "/update-status",
    "/submit-to-exchange",
    "/submit-for-pre-exchange",
    "/submit-pre-exchange-data",
)

# DELETE routes that cancel working state rather than removing a master record.
WRITE_NOT_DELETE_PATHS = ("/staged/cancel",)


def get_mode() -> str:
    """Read AUTHZ_MODE at call time so tests and ops can change it without a reimport."""
    mode = (os.getenv("AUTHZ_MODE") or MODE_ENFORCE).strip().lower()
    if mode not in _VALID_MODES:
        logger.warning("Unknown AUTHZ_MODE %r, falling back to %s", mode, MODE_ENFORCE)
        return MODE_ENFORCE
    return mode


def action_for(method: str, path: str) -> str:
    """Map an HTTP method and path onto one of read/write/delete/approve."""
    method = (method or "").upper()
    path = path or ""

    if method in WRITE_METHODS and any(marker in path for marker in APPROVAL_MARKERS):
        return ACTION_APPROVE
    if method in READ_METHODS:
        return ACTION_READ
    if method == "DELETE":
        if any(path.endswith(suffix) for suffix in WRITE_NOT_DELETE_PATHS):
            return ACTION_WRITE
        return ACTION_DELETE
    return ACTION_WRITE


def _permission_set(user: Dict[str, Any]) -> Set[str]:
    raw = user.get("permissions") or []
    return {p for p in raw if isinstance(p, str)}


def _role_set(user: Dict[str, Any]) -> Set[str]:
    raw = user.get("roles") or []
    return {r.lower() for r in raw if isinstance(r, str)}


def _as_resources(resource: "str | Iterable[str]") -> tuple:
    if isinstance(resource, str):
        return (resource,)
    return tuple(resource)


def is_allowed(user: Dict[str, Any], resource: "str | Iterable[str]", action: str) -> bool:
    """Does this user context satisfy `resource:action` for any of the named resources?"""
    if not user:
        return False
    if _role_set(user) & SUPER_ROLES:
        return True

    perms = _permission_set(user)
    if "*" in perms:
        return True
    for name in _as_resources(resource):
        if f"{name}:*" in perms or f"{name}:{action}" in perms:
            return True
    return False


def require(
    resource: "str | Iterable[str]",
    *,
    skip_path_suffixes: Optional[Iterable[str]] = None,
) -> Callable:
    """
    Build a dependency enforcing `resource:<action>` where the action follows the method.

    Args:
        resource: permission resource name, e.g. "power_exchange", or several names when
            one router serves screens that are separate menus (limit values are DAM, RTM
            and OCF). Holding any one of them is enough. Names must match what the auth
            service synthesises from menus (`_get_resource_name_from_menu`).
        skip_path_suffixes: paths this router serves that carry their own authentication
            and never see `request.state.current_user` -- the HMAC and SSE-ticket routes.
    """
    resources = _as_resources(resource)
    skips = tuple(skip_path_suffixes or ())

    async def _guard(request: Request) -> None:
        mode = get_mode()
        if mode == MODE_OFF:
            return

        path = request.url.path
        if any(path.endswith(suffix) for suffix in skips):
            return

        action = action_for(request.method, path)
        user = getattr(request.state, "current_user", None)

        if not user:
            # AuthMiddleware rejects anonymous callers before routing, so this means the
            # path was added to its exclude list without being added to skip_path_suffixes.
            logger.error(
                "authz: no user context on guarded path %s %s -- exclude_paths and "
                "skip_path_suffixes have diverged",
                request.method, path,
            )
            if mode == MODE_WARN:
                return
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )

        if is_allowed(user, resources, action):
            return

        needed = " or ".join(f"{name}:{action}" for name in resources)
        if mode == MODE_WARN:
            logger.warning(
                "authz WARN: would deny user=%s roles=%s on %s %s (needs %s)",
                user.get("user_id"), user.get("roles"), request.method, path, needed,
            )
            return

        logger.warning(
            "authz DENY: user=%s roles=%s on %s %s (needs %s)",
            user.get("user_id"), user.get("roles"), request.method, path, needed,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing required permission: {needed}",
        )

    _guard.__authz_resources__ = resources
    return _guard


def guard(resource: "str | Iterable[str]", **kwargs) -> Depends:
    """`include_router(r, dependencies=[guard("resource")])` -- the call site sugar."""
    return Depends(require(resource, **kwargs))


# Routers that legitimately carry no JWT user context, so no permission can be checked.
# Anything else appearing unguarded is a mistake, not a decision.
UNGUARDED_PATH_PREFIXES = (
    "/power-optimization",   # HMAC-authenticated
    "/notifications/stream",  # SSE, authenticated by short-lived ticket
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def _is_guard(dependency) -> bool:
    return hasattr(getattr(dependency, "dependency", None), "__authz_resources__")


def walk_routes(app, _prefix: str = "", _inherited: tuple = ()) -> list:
    """
    Yield (methods, path, guarded) for every route, following included routers.

    FastAPI 0.140 stopped flattening `include_router` into `app.routes` and stores a lazy
    `_IncludedRouter` instead, so the routes and the dependencies passed at include time
    live in different objects (`original_router.routes` and `include_context.dependencies`).
    Everything here is getattr-guarded: a future FastAPI that changes those names should
    make this return nothing rather than break startup.
    """
    found = []
    for route in getattr(app, "routes", []) or []:
        included = getattr(route, "original_router", None)
        context = getattr(route, "include_context", None)

        if included is not None and context is not None:
            deps = tuple(getattr(context, "dependencies", None) or ())
            prefix = _prefix + (getattr(context, "prefix", None) or "")
            found.extend(walk_routes(included, prefix, _inherited + deps))
            continue

        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if methods is None or path is None:
            continue

        own = tuple(getattr(route, "dependencies", None) or ())
        guarded = any(_is_guard(d) for d in _inherited + own)
        found.append((sorted(methods), _prefix + path, guarded))
    return found


def unguarded_routes(app) -> list:
    """Return (methods, path) for every route with no authz guard attached."""
    return [
        (methods, path)
        for methods, path, guarded in walk_routes(app)
        if not guarded
        and not any(path.startswith(prefix) for prefix in UNGUARDED_PATH_PREFIXES)
    ]


def assert_every_route_is_guarded(app) -> None:
    """
    Fail startup if a route slipped in without authorization.

    A router registered without `dependencies=[guard(...)]` is invisible -- it just works,
    for everybody. This turns that silence into a startup error. Set
    AUTHZ_ALLOW_UNGUARDED_ROUTES=true to downgrade it to a warning during a migration.
    """
    all_routes = walk_routes(app)
    missing = unguarded_routes(app)
    if not missing:
        logger.info(
            "authz: %d of %d routes carry a guard (%d exempt by path)",
            sum(1 for _, _, g in all_routes if g), len(all_routes),
            sum(1 for _, _, g in all_routes if not g),
        )
        return

    listing = "\n".join(f"  {','.join(m)} {p}" for m, p in missing)
    message = f"{len(missing)} route(s) registered without an authorization guard:\n{listing}"

    if (os.getenv("AUTHZ_ALLOW_UNGUARDED_ROUTES") or "").strip().lower() in {"1", "true", "yes"}:
        logger.warning("authz: %s", message)
        return
    raise RuntimeError(message)
