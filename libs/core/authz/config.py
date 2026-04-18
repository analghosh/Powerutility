# libs/core/authz/config.py
from __future__ import annotations
from typing import List, Optional
import re, time

PUBLIC: List[tuple[str, str]] = [
    ("GET", "/health"),
    ("POST", "/auth/login"),
    ("POST", "/auth/refresh"),
    ("POST", "/auth/verify-login"),
    ("POST", "/mfa/enable"),
    ("POST", "/mfa/verify"),
    ("POST", "/mfa/disable"),
]

ADMIN_PREFIXES = ["/admin"]

METHOD_ACTION = {"GET":"read","POST":"create","PUT":"update","PATCH":"update","DELETE":"delete"}
TAIL_KEYWORDS = {"approve":"approve","status":"status","schedule":"schedule","upload":"upload"}

def is_public(method: str, path_templ: str) -> bool:
    return (method, path_templ) in PUBLIC

def is_admin_only(path_templ: str) -> bool:
    return any(path_templ.startswith(p) for p in ADMIN_PREFIXES)

def _resource_from_path(path_templ: str, max_segments: int = 3) -> str:
    parts = [p for p in path_templ.strip("/").split("/") if p and not p.startswith("{")]
    # Skip common prefixes like 'api', 'v1' to get to the actual resource
    filtered_parts = []
    for part in parts:
        if part.lower() not in ['api', 'v1', 'v2']:
            filtered_parts.append(part)
    
    # Take the first meaningful part as the resource
    if filtered_parts:
        return filtered_parts[0].lower()
    
    return ".".join(parts[:max_segments]).lower() or "root"

def _action_from_path(method: str, path_templ: str) -> str:
    tail = (path_templ.strip("/").split("/")[-1] or "").lower()
    for k, act in TAIL_KEYWORDS.items():
        if re.fullmatch(rf"{k}(s)?", tail):
            return act
    return METHOD_ACTION.get(method.upper(), "read")

# tiny in‑proc cache for overrides (swap for Redis later if you like)
_OVR_CACHE: dict[tuple[str,str], tuple[float, Optional[list[str]]]] = {}
_OVR_TTL = 60

async def fetch_override(db, method: str, path_templ: str) -> Optional[list[str]]:
    now = time.time(); key = (method, path_templ)
    hit = _OVR_CACHE.get(key)
    if hit and now - hit[0] < _OVR_TTL:
        return hit[1]
    doc = await db.endpoint_overrides.find_one(
        {"method": method, "path": path_templ},
        {"_id": 0, "required_permissions": 1}
    )
    req = (doc or {}).get("required_permissions")
    _OVR_CACHE[key] = (now, req)
    return req

async def required_permissions_for(db, method: str, path_templ: str) -> list[str]:
    if is_public(method, path_templ):
        return []
    if is_admin_only(path_templ):
        return ["admin:*"]
    
    # Special case: Allow authenticated users to access their own menus
    if method == "GET" and path_templ.endswith("/menu/"):
        return []  # No specific permission required, just authentication
    
    override = await fetch_override(db, method, path_templ)
    if override is not None:
        return override
    res = _resource_from_path(path_templ)
    act = _action_from_path(method, path_templ)
    return [f"{res}:{act}"]
