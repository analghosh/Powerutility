"""
Local access-token validation for the non-auth services.

Why this exists
---------------
Processing, ingestion and scheduler validated every authenticated request by making an
HTTP call to the auth service's `/api/v1/token/validate`. That is one network round trip,
one FastAPI route, one Mongo read and **two audit writes** per request, and it made every
service's availability a function of auth's. Measured on the running stack, 2026-08-19,
against a path that authenticates and then 404s (so the number is auth cost and nothing
else):

| concurrency | processing (HTTP to auth) | auth itself (validates locally) |
|---|---|---|
| 1 | 44 ms | 12 ms |
| 60 | **2,036 ms** | 528 ms |

Worse than the latency is the failure mode. `AuthClient.validate_token` converts a timeout
or connection error into `{"valid": False}`, which the middleware turns into **HTTP 401**.
So when auth is slow -- a login storm, a GC pause, a restart -- every other service does
not degrade, it rejects its users. That is the mechanism behind the measured platform-wide
401 storm.

What this checks, and why it is equivalent
------------------------------------------
`auth_service.get_current_user`, which is what `/token/validate` actually calls, does
exactly four things. This does the same four:

1. **Signature, expiry and token type** -- `jwt.decode` with the shared HS256 secret.
2. **Revocation by logout** -- `blacklist:access:{token}` in `REDIS_BLACKLIST_DB`, the key
   `redis_blacklist.invalidate_access_token()` actually writes. (Not the `jwt_blacklist`
   SET in `libs/core/jwt_validator.py`, which has zero writers anywhere.)
3. **Account still enabled** -- `is_active` on the user document.
4. **Revocation by rights change** -- `token_version` on the token against the account's
   current value. `bump_token_version()` is called on password change, role change,
   permission change, deactivation and deletion, so this one field covers every way a
   session is meant to end early.

Roles and permissions are read from the token payload, not the database -- that is what
the auth service does too (`get_current_user` overwrites them from `payload`), so nothing
is staler here than it was before.

**Nothing is cached.** Step 3 and 4 need the user document, so this still reads Mongo on
every request. That is deliberate: the point of the exercise is to remove the HTTP hop and
the availability coupling, not to trade away the revocation guarantees that were built
in `f05e765`. A user who is deactivated is still refused on their very next request.

Failure behaviour
-----------------
* **Redis unreachable -> fail open** (the token is not treated as revoked). Inherited from
  `jwt_validator.is_token_blacklisted` and from the notification stream's `is_revoked`;
  a Redis outage must not log out the entire platform, and revocation is a smaller loss
  than total unavailability.
* **Mongo unreachable -> fail closed** (reject). This matches today exactly: an exception
  inside `get_current_user` returns None and the caller 401s. Failing open here would let a
  deactivated account keep working during a database blip.

Clients are created lazily, on first use, inside the running event loop. Motor binds its
client to the loop that created it, and this codebase has already been bitten by a client
built at import time on a different loop -- every query then returns nothing, silently.
"""

import logging
import os
from typing import Any, Dict, Optional, Tuple

import jwt

logger = logging.getLogger(__name__)

_BLACKLIST_KEY = "blacklist:access:{}"


class LocalTokenValidator:
    """Validates access tokens in-process. One instance per service is enough."""

    def __init__(self, settings: Any = None):
        self._settings = settings
        self._redis = None
        self._redis_failed = False
        self._mongo = None
        self._users = None

    def configure(self, settings: Any) -> None:
        """
        Adopt a service's settings object as the primary config source.

        This is not optional plumbing. Every service loads `.env` through pydantic
        `BaseSettings`, and **pydantic does not write what it reads back into
        `os.environ`** — the same trap `start.sh` documents for `AUTH_DB_NAME`. A
        validator that only consulted `os.getenv` therefore found no `JWT_SECRET_KEY`
        and rejected every token as "server misconfigured". Settings first, environment
        second.
        """
        if settings is not None:
            self._settings = settings

    # ------------------------------------------------------------------ config

    def _cfg(self, name: str, default: Any = None) -> Any:
        value = getattr(self._settings, name, None) if self._settings is not None else None
        if value is None or value == "":
            value = os.getenv(name)
        return default if value is None or value == "" else value

    def _secret(self) -> str:
        return self._cfg("JWT_SECRET_KEY", "") or ""

    def _algorithm(self) -> str:
        return self._cfg("JWT_ALGORITHM", "HS256")

    # ------------------------------------------------------------------ clients

    async def _blacklist(self):
        """Async Redis on the blacklist db, or None if it cannot be reached."""
        if self._redis is not None or self._redis_failed:
            return self._redis
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.Redis(
                host=self._cfg("REDIS_HOST", "localhost"),
                port=int(self._cfg("REDIS_PORT", 6379)),
                db=int(self._cfg("REDIS_BLACKLIST_DB", 1)),
                password=self._cfg("REDIS_PASSWORD") or None,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            await self._redis.ping()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Local token validation cannot reach Redis (%s); logout revocation will "
                "not be enforced until it recovers. Signature, expiry, is_active and "
                "token_version checks are unaffected.", exc
            )
            self._redis = None
            self._redis_failed = True
        return self._redis

    def _user_collection(self):
        """Motor collection for auth_db.users, created inside the running loop."""
        if self._users is None:
            from motor.motor_asyncio import AsyncIOMotorClient

            self._mongo = AsyncIOMotorClient(
                self._cfg("MONGODB_URI", "mongodb://localhost:27017"),
                serverSelectionTimeoutMS=3000,
            )
            self._users = self._mongo[self._cfg("AUTH_DB_NAME", "auth_db")]["users"]
        return self._users

    # ------------------------------------------------------------------ checks

    async def _is_revoked(self, token: str) -> bool:
        """True when logout has blacklisted this token. Fails open — see module docstring."""
        client = await self._blacklist()
        if client is None:
            return False
        try:
            return bool(await client.exists(_BLACKLIST_KEY.format(token)))
        except Exception as exc:  # noqa: BLE001
            logger.error("Blacklist check failed (%s); treating token as not revoked", exc)
            return False

    async def _user_state(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        The two fields that decide whether an otherwise-valid token still counts.

        `_id` is looked up as a string first and then as an ObjectId, matching
        `libs/core/authz/guard.py:_load_user_doc` — this database holds both shapes.
        """
        projection = {"is_active": 1, "is_deleted": 1, "token_version": 1}
        users = self._user_collection()

        # Order matters for cost, not just correctness. This runs on every authenticated
        # request, and `_id` is an ObjectId for every account the auth service creates, so
        # trying the string form first meant a guaranteed miss and *two* round trips per
        # request. Shape-check the id and try the likely form first; the other form is
        # still attempted, because the collection genuinely holds both
        # (`libs/core/authz/guard.py:_load_user_doc` handles the same split).
        candidates = []
        if len(user_id) == 24:
            try:
                from bson import ObjectId

                candidates.append(ObjectId(user_id))
            except Exception:  # noqa: BLE001  (not an ObjectId after all)
                pass
        candidates.append(user_id)

        for candidate in candidates:
            doc = await users.find_one({"_id": candidate}, projection)
            if doc is not None:
                return doc
        return None

    # ------------------------------------------------------------------ entry point

    async def validate(self, token: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Returns `(payload, None)` for a good token, or `(None, reason)`.

        The reason is for logs only. Callers must return one undifferentiated 401 — telling
        a caller *which* check failed distinguishes "expired" from "deactivated" from
        "no such account".
        """
        secret = self._secret()
        if not secret:
            # Refusing is the only safe answer: an empty secret would make
            # `jwt.decode` reject everything anyway, but silently and confusingly.
            logger.critical("JWT_SECRET_KEY is not set; local token validation cannot run")
            return None, "server misconfigured"

        try:
            payload = jwt.decode(
                token,
                secret,
                algorithms=[self._algorithm()],
                options={"verify_exp": True, "verify_nbf": True},
            )
        except jwt.ExpiredSignatureError:
            return None, "token expired"
        except jwt.InvalidTokenError as exc:
            return None, f"invalid token ({exc})"

        if payload.get("type") != "access":
            # A refresh or temporary MFA token must not authenticate an API call.
            return None, f"wrong token type ({payload.get('type')!r})"

        user_id = payload.get("sub") or payload.get("id")
        if not user_id:
            return None, "token carries no subject"

        if await self._is_revoked(token):
            return None, "token revoked by logout"

        try:
            user = await self._user_state(str(user_id))
        except Exception as exc:  # noqa: BLE001
            # Fail closed. See module docstring.
            logger.error("User lookup failed during token validation (%s); refusing", exc)
            return None, "user lookup failed"

        if user is None:
            return None, "account not found"
        if user.get("is_deleted"):
            return None, "account deleted"
        if not user.get("is_active", False):
            return None, "account inactive"

        current = int(user.get("token_version", 0) or 0)
        presented = int(payload.get("token_version", 0) or 0)
        if presented < current:
            # Tokens minted before this field existed carry no version and read as 0, so
            # they are rejected the moment the account's version is bumped — which is the
            # intended behaviour, not an accident.
            return None, f"token superseded (v{presented} < v{current})"

        return payload, None


local_token_validator = LocalTokenValidator()
