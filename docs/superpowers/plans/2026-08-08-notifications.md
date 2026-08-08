# EPMS Live Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver live in-app notifications to EPMS users over SSE, backed by a durable Mongo log, so a checker learns of a maker's submission without refreshing.

**Architecture:** A new shared library `libs/notifications/` resolves recipients from RBAC, writes one document per recipient to Mongo, and publishes to a Redis channel. The processing service (8003) hosts an SSE endpoint; every worker subscribes to Redis and writes frames to the connections it holds. The client owns one `EventSource` per browser via a `SharedWorker`, closes it while hidden, and renders into the bell that already exists in `Header.jsx`.

**Tech Stack:** Python 3.13, FastAPI, Motor (async MongoDB), `redis.asyncio`, pytest. React 18, Vite, Redux Toolkit, axios, `node --test`.

## Global Constraints

- **Design spec:** `docs/superpowers/specs/2026-08-08-notifications-design.md`. Read it before starting.
- **Target scale:** 50–60 concurrent users. No nginx capacity tuning required.
- **Emitting must never fail the business request.** Every `notify()` call site wraps in `try/except Exception`, logs, and continues.
- **Emit at action level, never row level.** 96 blocks produce 1 notification with `bid_count: 96`.
- **The actor is always subtracted** from the audience. Nobody is notified of their own action.
- **No `dangerouslySetInnerHTML` anywhere in notification UI.** Rendered text contains usernames; 100 usernames in the live database hold stored XSS payloads.
- **Redis must remain bound to localhost.** It holds session tickets and the JWT blacklist.
- **All local DB work targets the Docker container `epms-mongo` only.** Never a remote database.
- **Python async only.** `libs/cache/redis_cache.py` is the *synchronous* client — do not use it in any stream path. Use `redis.asyncio`.
- **Mongo access pattern:** subclass `libs.database.base_repository.BaseRepository`, constructor `(collection_name, mongodb_uri, database_name)`, exposing `self.collection` as `AsyncIOMotorCollection`.
- Backend tests live in `libs/tests/`, named `test_*.py`, run with `python -m pytest`.
- Client has no test runner configured and `"type": "module"`; tests run with `node --test`.
- Commit after every task. Never `--force` push.

## Verified Facts (do not re-derive)

| Fact | Evidence |
|---|---|
| uvicorn runs with **no `--workers`** flag | `run_services.py:273-280`; nothing in the repo sets it |
| `get_user_context(request) -> Dict` returns `user_id`, `username`, `roles`, `permissions`, `correlation_id` | `services/processing/app/v2/dependencies.py:36-68` |
| `get_user_context` returns `user_id: "anonymous"` for unauthenticated — **it does not raise** | same, lines 58-68 |
| `users.permissions` is **empty on all 815 users**; `users.roles` holds role **names** | verified against `auth_db` |
| Auth DB config: `AUTH_DB_NAME` default `dev_auth_db`, collections `users`, `roles` | `services/auth/app/core/config.py:32-39` |
| Processing config: `MONGODB_URI`, `MONGODB_DATABASE="epms"`, `REDIS_HOST`, `REDIS_PORT` | `services/processing/app/core/config.py:30-47` |
| JWT blacklist is a Redis **set** named `jwt_blacklist`, keyed by raw token | `libs/core/jwt_validator.py:209-212` |
| Dead notification placeholders to replace | `block_bid_services.py:125-133` and `:200-209` |
| Dead bell data to delete | `client/src/components/common/header/Header.jsx:125-172` |
| Bell markup to keep and wire | `Header.jsx:191-296`; links to `/pages/notifications/` at `:249,280` |
| Client tokens are in sessionStorage, no cookies | `client/src/utils/authTokens.js:63,83` |
| Client API base for processing v2 | `client/src/config/apiEndpoints.js:43` (`VITE_API_BASE_URL`) |

---

## File Structure

**Backend — new**

| File | Responsibility |
|---|---|
| `libs/notifications/__init__.py` | Public exports: `notify`, `Severity` |
| `libs/notifications/models.py` | `Severity`, `EventType`, `Notification` dataclasses |
| `libs/notifications/events.py` | The registry. Only file that changes per new event. |
| `libs/notifications/audience.py` | Audience rules → `user_id` list, via auth_db |
| `libs/notifications/repository.py` | `epms.notifications` Mongo access + indexes |
| `libs/notifications/pubsub.py` | Redis async publish + subscribe + connection registry |
| `libs/notifications/emitter.py` | `notify()` — orchestrates the above |

**Backend — modified**

| File | Change |
|---|---|
| `services/processing/app/v2/api/router/notification_routes.py` | **new** — 6 endpoints |
| `services/processing/app/main.py` | register router; start/stop the Redis subscriber |
| `services/processing/app/v2/services/block_bid_services.py` | replace 2 dead placeholders with `notify()` |

**Client — new**

| File | Responsibility |
|---|---|
| `src/store/slices/notificationsSlice.js` | `items`, `unreadCount`, `connectionState` |
| `src/utils/notificationStream.js` | Pure logic: frame handling, backoff, watchdog |
| `src/workers/notificationStream.shared.js` | SharedWorker owning the single EventSource |
| `src/hooks/useNotificationStream.js` | React glue: lifecycle, visibility, dispatch |
| `src/components/notifications/NotificationToast.jsx` | Toast on live frames |
| `src/pages/Notifications.jsx` | Full list page |

**Client — modified**

| File | Change |
|---|---|
| `src/config/apiEndpoints.js` | add 6 endpoints |
| `src/components/common/header/Header.jsx` | delete `:125-172`, wire `:191-296` |
| `src/store/store.jsx` | register the slice |
| router config | add `/notifications` |

---

## Task 1: Notification models and severity

**Files:**
- Create: `libs/notifications/models.py`
- Create: `libs/notifications/__init__.py`
- Test: `libs/tests/test_notification_models.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Severity` — str enum: `INFO="info"`, `ACTION_REQUIRED="action_required"`, `ERROR="error"`
  - `EventType(key: str, severity: Severity, title: str, body: str, audience: Audience, link: str)` — frozen dataclass with `render(context: dict) -> tuple[str, str, str]` returning `(title, body, link)`
  - `Audience` — abstract base with `async resolve(context: dict) -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
# libs/tests/test_notification_models.py
import pytest
from libs.notifications.models import EventType, Severity


class _FixedAudience:
    async def resolve(self, context):
        return ["u1"]


def _event():
    return EventType(
        key="bid.submitted",
        severity=Severity.ACTION_REQUIRED,
        title="{market_type} bid awaiting approval",
        body="{actor_name} submitted {bid_count} {market_type} bid(s) for {schedule_date}.",
        audience=_FixedAudience(),
        link="/dam/dam-block-bid-approve?date={schedule_date}",
    )


def test_render_substitutes_every_placeholder():
    title, body, link = _event().render({
        "market_type": "DAM",
        "actor_name": "A. Kumar",
        "bid_count": 3,
        "schedule_date": "2026-08-09",
    })
    assert title == "DAM bid awaiting approval"
    assert body == "A. Kumar submitted 3 DAM bid(s) for 2026-08-09."
    assert link == "/dam/dam-block-bid-approve?date=2026-08-09"


def test_missing_placeholder_raises_rather_than_emitting_broken_text():
    with pytest.raises(KeyError):
        _event().render({"market_type": "DAM"})


def test_severity_values_are_the_stored_strings():
    assert Severity.ACTION_REQUIRED.value == "action_required"
    assert Severity.INFO.value == "info"
    assert Severity.ERROR.value == "error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest libs/tests/test_notification_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'libs.notifications'`

- [ ] **Step 3: Write minimal implementation**

```python
# libs/notifications/models.py
"""Core types for the notification system.

Kept free of I/O so the registry and rendering can be tested without Mongo,
Redis, or an event loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(str, Enum):
    INFO = "info"
    ACTION_REQUIRED = "action_required"
    ERROR = "error"


class Audience(ABC):
    """A rule describing who should receive an event."""

    @abstractmethod
    async def resolve(self, context: Dict[str, Any]) -> List[str]:
        """Return recipient user_ids. The actor is subtracted by the emitter."""


@dataclass(frozen=True)
class EventType:
    key: str
    severity: Severity
    title: str
    body: str
    audience: Audience
    link: str

    def render(self, context: Dict[str, Any]) -> tuple[str, str, str]:
        """Render title, body and link.

        Raises KeyError on a missing placeholder. Failing loudly at emit time is
        preferable to storing half-rendered text in a maker-checker trail.
        """
        return (
            self.title.format(**context),
            self.body.format(**context),
            self.link.format(**context),
        )


@dataclass
class Notification:
    user_id: str
    event_key: str
    title: str
    body: str
    payload: Dict[str, Any]
    link: str
    actor: Dict[str, Any]
    severity: str
    created_at: datetime
    read_at: Optional[datetime] = None
    id: Optional[str] = None

    def to_document(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "event_key": self.event_key,
            "title": self.title,
            "body": self.body,
            "payload": self.payload,
            "link": self.link,
            "actor": self.actor,
            "severity": self.severity,
            "read_at": self.read_at,
            "created_at": self.created_at,
        }
```

```python
# libs/notifications/__init__.py
from libs.notifications.models import Audience, EventType, Notification, Severity

__all__ = ["Audience", "EventType", "Notification", "Severity"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest libs/tests/test_notification_models.py -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Commit**

```bash
git add libs/notifications/models.py libs/notifications/__init__.py libs/tests/test_notification_models.py
git commit -m "feat(notifications): add core models and severity"
```

---

## Task 2: Audience resolution against auth_db

**Files:**
- Create: `libs/notifications/audience.py`
- Test: `libs/tests/test_notification_audience.py`

**Interfaces:**
- Consumes: `Audience` from Task 1
- Produces:
  - `HasPermission(permission: str)` — implements `Audience`; templated via `context`
  - `SpecificUsers(user_ids: list[str])` — implements `Audience`
  - `Everyone()` — implements `Audience`
  - `AuthDirectory(users_collection, roles_collection)` — injected into resolution; `.users_with_permission(perm) -> list[str]`, `.all_active_user_ids() -> list[str]`

**Critical:** the obvious query `users.find({"permissions": perm})` returns **nothing** — that field is empty on all 815 live users. The chain must go through `roles`.

- [ ] **Step 1: Write the failing test**

```python
# libs/tests/test_notification_audience.py
import pytest

from libs.notifications.audience import (
    AuthDirectory,
    Everyone,
    HasPermission,
    SpecificUsers,
)


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class _FakeCollection:
    def __init__(self, docs):
        self.docs = docs
        self.last_filter = None

    def find(self, flt, projection=None):
        self.last_filter = flt
        return _FakeCursor(self.docs)


@pytest.mark.asyncio
async def test_has_permission_resolves_via_roles_not_the_empty_user_field():
    roles = _FakeCollection([{"name": "admin"}, {"name": "operator"}])
    users = _FakeCollection([{"_id": "u1"}, {"_id": "u2"}])
    directory = AuthDirectory(users_collection=users, roles_collection=roles)

    result = await HasPermission("dam_block_bid_approval:read").resolve(
        {"_directory": directory}
    )

    assert result == ["u1", "u2"]
    assert roles.last_filter == {"permissions": "dam_block_bid_approval:read"}
    assert users.last_filter == {
        "roles": {"$in": ["admin", "operator"]},
        "is_active": True,
        "is_deleted": {"$ne": True},
    }


@pytest.mark.asyncio
async def test_has_permission_templates_from_context():
    roles = _FakeCollection([])
    users = _FakeCollection([])
    directory = AuthDirectory(users_collection=users, roles_collection=roles)

    await HasPermission("{market_lower}_block_bid_approval:read").resolve(
        {"_directory": directory, "market_lower": "rtm"}
    )

    assert roles.last_filter == {"permissions": "rtm_block_bid_approval:read"}


@pytest.mark.asyncio
async def test_no_role_grants_it_means_no_recipients_and_no_user_query():
    roles = _FakeCollection([])
    users = _FakeCollection([{"_id": "should_not_be_reached"}])
    directory = AuthDirectory(users_collection=users, roles_collection=roles)

    assert await HasPermission("nobody:has_this").resolve({"_directory": directory}) == []
    assert users.last_filter is None


@pytest.mark.asyncio
async def test_specific_users_passes_through():
    assert await SpecificUsers(["a", "b"]).resolve({}) == ["a", "b"]


@pytest.mark.asyncio
async def test_everyone_returns_all_active():
    users = _FakeCollection([{"_id": "u1"}, {"_id": "u2"}])
    directory = AuthDirectory(users_collection=users, roles_collection=_FakeCollection([]))

    assert await Everyone().resolve({"_directory": directory}) == ["u1", "u2"]
    assert users.last_filter == {"is_active": True, "is_deleted": {"$ne": True}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest libs/tests/test_notification_audience.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'libs.notifications.audience'`

- [ ] **Step 3: Write minimal implementation**

```python
# libs/notifications/audience.py
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
```

Note: `HasPermission.permission.format(**context)` receives `_directory` in the context dict. `str.format` ignores unused keys, so this is safe.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest libs/tests/test_notification_audience.py -v`
Expected: PASS, 5 tests

If `pytest.mark.asyncio` errors with "async def functions are not natively supported", install the plugin: `pip install pytest-asyncio` and add to `setup.cfg` under `[tool:pytest]`: `asyncio_mode = auto`

- [ ] **Step 5: Commit**

```bash
git add libs/notifications/audience.py libs/tests/test_notification_audience.py
git commit -m "feat(notifications): resolve audiences via the roles chain"
```

---

## Task 3: Notification repository

**Files:**
- Create: `libs/notifications/repository.py`
- Test: `libs/tests/test_notification_repository.py`

**Interfaces:**
- Consumes: `Notification` from Task 1
- Produces: `NotificationRepository(mongodb_uri, database_name)` with
  - `async ensure_indexes() -> None`
  - `async insert_many_for(notifications: list[Notification]) -> list[str]`
  - `async list_for_user(user_id, limit=50, skip=0) -> list[dict]`
  - `async unread_count(user_id) -> int`
  - `async mark_read(user_id, notification_id) -> bool`
  - `async mark_all_read(user_id) -> int`

`TTL_SECONDS = 7776000` (90 days).

- [ ] **Step 1: Write the failing test**

```python
# libs/tests/test_notification_repository.py
from datetime import datetime

import pytest

from libs.notifications.models import Notification
from libs.notifications.repository import TTL_SECONDS, NotificationRepository


class _FakeIndexRecorder:
    def __init__(self):
        self.calls = []

    async def create_index(self, keys, **kwargs):
        self.calls.append((keys, kwargs))


def _repo_with(collection):
    repo = NotificationRepository.__new__(NotificationRepository)
    repo.collection = collection
    return repo


def _notification(user_id="u1"):
    return Notification(
        user_id=user_id,
        event_key="bid.submitted",
        title="t",
        body="b",
        payload={"market_type": "DAM"},
        link="/x",
        actor={"user_id": "u9", "name": "A"},
        severity="action_required",
        created_at=datetime(2026, 8, 8, 12, 0, 0),
    )


@pytest.mark.asyncio
async def test_ensure_indexes_creates_a_90_day_ttl():
    recorder = _FakeIndexRecorder()
    await _repo_with(recorder).ensure_indexes()

    ttl = [c for c in recorder.calls if "expireAfterSeconds" in c[1]]
    assert len(ttl) == 1
    assert ttl[0][1]["expireAfterSeconds"] == TTL_SECONDS
    assert TTL_SECONDS == 7776000


@pytest.mark.asyncio
async def test_ensure_indexes_creates_the_list_and_unread_indexes():
    recorder = _FakeIndexRecorder()
    await _repo_with(recorder).ensure_indexes()

    keys = [c[0] for c in recorder.calls]
    assert [("user_id", 1), ("created_at", -1)] in keys
    assert [("user_id", 1), ("read_at", 1)] in keys


@pytest.mark.asyncio
async def test_insert_many_writes_one_document_per_recipient():
    class _Coll:
        def __init__(self):
            self.documents = None

        async def insert_many(self, docs):
            self.documents = docs

            class _R:
                inserted_ids = ["id1", "id2"]
            return _R()

    coll = _Coll()
    ids = await _repo_with(coll).insert_many_for([_notification("u1"), _notification("u2")])

    assert ids == ["id1", "id2"]
    assert [d["user_id"] for d in coll.documents] == ["u1", "u2"]
    assert coll.documents[0]["read_at"] is None


@pytest.mark.asyncio
async def test_insert_many_with_no_recipients_does_not_touch_mongo():
    class _Exploding:
        async def insert_many(self, docs):
            raise AssertionError("must not be called for an empty audience")

    assert await _repo_with(_Exploding()).insert_many_for([]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest libs/tests/test_notification_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'libs.notifications.repository'`

- [ ] **Step 3: Write minimal implementation**

```python
# libs/notifications/repository.py
"""Mongo access for the epms.notifications collection."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from bson import ObjectId

from libs.database.base_repository import BaseRepository
from libs.notifications.models import Notification

logger = logging.getLogger(__name__)

COLLECTION_NAME = "notifications"
TTL_SECONDS = 7776000  # 90 days


class NotificationRepository(BaseRepository):
    def __init__(self, mongodb_uri: str, database_name: str):
        super().__init__(COLLECTION_NAME, mongodb_uri, database_name)

    async def ensure_indexes(self) -> None:
        """Idempotent. Called once at service startup, never at import time.

        Creating indexes in a constructor that runs at import is what caused the
        Motor event-loop misbind elsewhere in this codebase; do not move this.
        """
        await self.collection.create_index([("user_id", 1), ("created_at", -1)])
        await self.collection.create_index([("user_id", 1), ("read_at", 1)])
        await self.collection.create_index(
            [("created_at", 1)], expireAfterSeconds=TTL_SECONDS
        )

    async def insert_many_for(self, notifications: List[Notification]) -> List[str]:
        if not notifications:
            return []
        result = await self.collection.insert_many(
            [n.to_document() for n in notifications]
        )
        return [str(i) for i in result.inserted_ids]

    async def list_for_user(
        self, user_id: str, limit: int = 50, skip: int = 0
    ) -> List[Dict[str, Any]]:
        cursor = (
            self.collection.find({"user_id": user_id})
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        return [self._serialise(doc) async for doc in cursor]

    async def unread_count(self, user_id: str) -> int:
        return await self.collection.count_documents(
            {"user_id": user_id, "read_at": None}
        )

    async def mark_read(self, user_id: str, notification_id: str) -> bool:
        result = await self.collection.update_one(
            {"_id": ObjectId(notification_id), "user_id": user_id, "read_at": None},
            {"$set": {"read_at": datetime.now()}},
        )
        return result.modified_count > 0

    async def mark_all_read(self, user_id: str) -> int:
        result = await self.collection.update_many(
            {"user_id": user_id, "read_at": None},
            {"$set": {"read_at": datetime.now()}},
        )
        return result.modified_count

    @staticmethod
    def _serialise(doc: Dict[str, Any]) -> Dict[str, Any]:
        doc["_id"] = str(doc["_id"])
        for field in ("created_at", "read_at"):
            if isinstance(doc.get(field), datetime):
                doc[field] = doc[field].isoformat()
        return doc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest libs/tests/test_notification_repository.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Commit**

```bash
git add libs/notifications/repository.py libs/tests/test_notification_repository.py
git commit -m "feat(notifications): add repository with 90-day TTL"
```

---

## Task 4: Redis pub/sub and the connection registry

**Files:**
- Create: `libs/notifications/pubsub.py`
- Test: `libs/tests/test_notification_pubsub.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `CHANNEL = "epms:notifications"`
  - `ConnectionRegistry()` with `add(user_id, queue)`, `remove(user_id, queue)`, `queues_for(user_id) -> list`, `count() -> int`
  - `async publish(redis_client, user_ids: list[str], notification: dict) -> None`
  - `async run_subscriber(redis_client, registry, stop_event) -> None`

- [ ] **Step 1: Write the failing test**

```python
# libs/tests/test_notification_pubsub.py
import asyncio
import json

import pytest

from libs.notifications.pubsub import CHANNEL, ConnectionRegistry, publish


def test_registry_returns_only_the_matching_users_queues():
    registry = ConnectionRegistry()
    q_a1, q_a2, q_b = asyncio.Queue(), asyncio.Queue(), asyncio.Queue()
    registry.add("A", q_a1)
    registry.add("A", q_a2)
    registry.add("B", q_b)

    assert set(registry.queues_for("A")) == {q_a1, q_a2}
    assert registry.queues_for("B") == [q_b]
    assert registry.queues_for("nobody") == []
    assert registry.count() == 3


def test_removing_the_last_queue_drops_the_user_entry():
    registry = ConnectionRegistry()
    q = asyncio.Queue()
    registry.add("A", q)
    registry.remove("A", q)

    assert registry.queues_for("A") == []
    assert registry.count() == 0
    assert "A" not in registry._by_user


def test_removing_an_unknown_queue_is_a_no_op():
    registry = ConnectionRegistry()
    registry.remove("ghost", asyncio.Queue())
    assert registry.count() == 0


@pytest.mark.asyncio
async def test_publish_sends_one_message_naming_every_recipient():
    class _FakeRedis:
        def __init__(self):
            self.published = []

        async def publish(self, channel, payload):
            self.published.append((channel, payload))

    redis = _FakeRedis()
    await publish(redis, ["u1", "u2"], {"title": "t", "_id": "n1"})

    assert len(redis.published) == 1
    channel, payload = redis.published[0]
    assert channel == CHANNEL
    assert json.loads(payload) == {
        "user_ids": ["u1", "u2"],
        "notification": {"title": "t", "_id": "n1"},
    }


@pytest.mark.asyncio
async def test_publish_with_no_recipients_sends_nothing():
    class _Exploding:
        async def publish(self, channel, payload):
            raise AssertionError("must not publish to an empty audience")

    await publish(_Exploding(), [], {"title": "t"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest libs/tests/test_notification_pubsub.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'libs.notifications.pubsub'`

- [ ] **Step 3: Write minimal implementation**

```python
# libs/notifications/pubsub.py
"""Redis pub/sub fan-out and the per-process connection registry.

Redis never talks to a browser. It carries a message from the worker that
emitted an event to the worker holding the recipient's SSE connection. With one
uvicorn worker that hop is redundant; it exists so that adding --workers 2 does
not silently deliver to the wrong process.

Delivery is at-most-once by design. The notification is durably in Mongo before
this publishes, so a dropped message is repaired by the next reconnect's sync
frame.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

CHANNEL = "epms:notifications"


class ConnectionRegistry:
    """user_id -> the queues feeding that user's open streams in this process."""

    def __init__(self):
        self._by_user: Dict[str, List[asyncio.Queue]] = {}

    def add(self, user_id: str, queue: asyncio.Queue) -> None:
        self._by_user.setdefault(user_id, []).append(queue)

    def remove(self, user_id: str, queue: asyncio.Queue) -> None:
        queues = self._by_user.get(user_id)
        if not queues:
            return
        if queue in queues:
            queues.remove(queue)
        if not queues:
            self._by_user.pop(user_id, None)

    def queues_for(self, user_id: str) -> List[asyncio.Queue]:
        return list(self._by_user.get(user_id, []))

    def count(self) -> int:
        return sum(len(v) for v in self._by_user.values())


async def publish(redis_client, user_ids: List[str], notification: Dict[str, Any]) -> None:
    if not user_ids:
        return
    await redis_client.publish(
        CHANNEL,
        json.dumps({"user_ids": user_ids, "notification": notification}, default=str),
    )


async def run_subscriber(redis_client, registry: ConnectionRegistry, stop_event: asyncio.Event) -> None:
    """Long-lived task started at service startup.

    Every process receives every message and discards those naming users it does
    not hold -- one JSON parse and one dict lookup per miss. Cost scales with
    worker count, not user count.
    """
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(CHANNEL)
    logger.info("Notification subscriber listening on %s", CHANNEL)
    try:
        while not stop_event.is_set():
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message is None:
                continue
            try:
                envelope = json.loads(message["data"])
            except (ValueError, TypeError):
                logger.exception("Malformed notification envelope; discarding")
                continue

            notification = envelope.get("notification")
            for user_id in envelope.get("user_ids", []):
                for queue in registry.queues_for(user_id):
                    queue.put_nowait(notification)
    finally:
        await pubsub.unsubscribe(CHANNEL)
        await pubsub.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest libs/tests/test_notification_pubsub.py -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Commit**

```bash
git add libs/notifications/pubsub.py libs/tests/test_notification_pubsub.py
git commit -m "feat(notifications): add Redis fan-out and connection registry"
```

---

## Task 5: The event registry

**Files:**
- Create: `libs/notifications/events.py`
- Test: `libs/tests/test_notification_events.py`

**Interfaces:**
- Consumes: `EventType`, `Severity` (Task 1); `HasPermission`, `SpecificUsers` (Task 2)
- Produces: `REGISTRY: dict[str, EventType]`, `get_event(key) -> EventType`

Copy chosen deliberately from the vendor's own abandoned placeholders at `block_bid_services.py:125-133` and `:200-209`, with the hardcoded `"RTM"` replaced by a template — that hardcoding is the same market-hardcoding bug already fixed twice in this codebase.

- [ ] **Step 1: Write the failing test**

```python
# libs/tests/test_notification_events.py
import pytest

from libs.notifications.audience import HasPermission, SpecificUsers
from libs.notifications.events import REGISTRY, get_event
from libs.notifications.models import Severity


def test_block_bid_submitted_notifies_approval_screen_holders():
    event = get_event("block_bid.submitted_for_approval")
    assert isinstance(event.audience, HasPermission)
    assert event.severity == Severity.ACTION_REQUIRED
    assert event.audience.permission == "{market_lower}_block_bid_approval:read"


def test_block_bid_status_changed_is_informational_not_actionable():
    event = REGISTRY["block_bid.status_changed"]
    assert event.severity == Severity.INFO
    assert isinstance(event.audience, HasPermission)


def test_specific_users_is_available_for_maker_targeted_events():
    """Not used by the current registry, but the rule must exist for the
    approve/reject-notifies-the-maker event once bid ownership is threaded
    through. Guards against the import being dropped."""
    assert SpecificUsers(["u1"]) is not None


def test_titles_are_templated_never_hardcoded_to_one_market():
    """block_bid_services.py:128 hardcoded 'RTM' on a path serving four markets."""
    for key, event in REGISTRY.items():
        assert "RTM" not in event.title, f"{key} hardcodes RTM"
        assert "DAM" not in event.title, f"{key} hardcodes DAM"


def test_every_registry_key_matches_its_event_key():
    for key, event in REGISTRY.items():
        assert event.key == key


def test_unknown_event_key_raises_with_a_useful_message():
    with pytest.raises(KeyError, match="not_a_real_event"):
        get_event("not_a_real_event")


def test_block_bid_submitted_renders_for_every_day_ahead_market():
    event = get_event("block_bid.submitted_for_approval")
    for market in ("DAM", "GDAM", "HPDAM", "RTM"):
        title, body, link = event.render({
            "market_type": market,
            "market_lower": market.lower(),
            "actor_name": "A. Kumar",
            "bid_count": 3,
            "schedule_date": "2026-08-09",
        })
        assert market in title
        assert "3" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest libs/tests/test_notification_events.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'libs.notifications.events'`

- [ ] **Step 3: Write minimal implementation**

```python
# libs/notifications/events.py
"""The event registry.

This is the only file that changes when a new notification event is added:
one entry here plus one notify() call at the source.

Wording is adapted from the vendor's own abandoned notification payloads at
block_bid_services.py:125-133 and :200-209, which were written and never
delivered. Their hardcoded "RTM" is replaced with a template -- that path also
serves DAM, GDAM and HPDAM, and market-hardcoding is a bug already fixed twice
in this codebase.
"""

from __future__ import annotations

from typing import Dict

from libs.notifications.audience import HasPermission
from libs.notifications.models import EventType, Severity

REGISTRY: Dict[str, EventType] = {
    "block_bid.submitted_for_approval": EventType(
        key="block_bid.submitted_for_approval",
        severity=Severity.ACTION_REQUIRED,
        title="{market_type} block bid awaiting approval",
        body="{actor_name} submitted {bid_count} {market_type} block bid(s) for {schedule_date}.",
        audience=HasPermission("{market_lower}_block_bid_approval:read"),
        link="/dam/dam-block-bid-approve?date={schedule_date}",
    ),
    "block_bid.status_changed": EventType(
        key="block_bid.status_changed",
        severity=Severity.INFO,
        title="{market_type} block bid {status_lower}",
        body="{actor_name} {status_lower} {bid_count} {market_type} block bid(s) for {schedule_date}.",
        audience=HasPermission("{market_lower}_block_bid_approval:read"),
        link="/dam/dam-block-bid-submission-status?date={schedule_date}",
    ),
}


def get_event(key: str) -> EventType:
    if key not in REGISTRY:
        raise KeyError(f"Unknown notification event: {key}")
    return REGISTRY[key]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest libs/tests/test_notification_events.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add libs/notifications/events.py libs/tests/test_notification_events.py
git commit -m "feat(notifications): add the event registry"
```

---

## Task 6: The emitter

**Files:**
- Create: `libs/notifications/emitter.py`
- Modify: `libs/notifications/__init__.py`
- Test: `libs/tests/test_notification_emitter.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5
- Produces:
  - `NotificationEmitter(repository, directory, redis_client)` with `async notify(event_key, context, actor) -> list[str]`
  - `set_emitter(emitter)` / `get_emitter() -> NotificationEmitter | None` — module-level singleton set at startup
  - `async notify(event_key, context, actor) -> list[str]` — module-level convenience that no-ops if no emitter is configured

- [ ] **Step 1: Write the failing test**

```python
# libs/tests/test_notification_emitter.py
import pytest

from libs.notifications.emitter import NotificationEmitter


class _Repo:
    def __init__(self):
        self.written = None

    async def insert_many_for(self, notifications):
        self.written = notifications
        return [f"id{i}" for i, _ in enumerate(notifications)]


class _Redis:
    def __init__(self):
        self.published = []

    async def publish(self, channel, payload):
        self.published.append((channel, payload))


class _Directory:
    def __init__(self, ids):
        self.ids = ids

    async def users_with_permission(self, permission):
        return list(self.ids)

    async def all_active_user_ids(self):
        return list(self.ids)


def _emitter(ids, repo=None, redis=None):
    return NotificationEmitter(
        repository=repo or _Repo(),
        directory=_Directory(ids),
        redis_client=redis or _Redis(),
    )


def _context():
    return {
        "market_type": "DAM",
        "market_lower": "dam",
        "bid_count": 3,
        "schedule_date": "2026-08-09",
    }


@pytest.mark.asyncio
async def test_writes_one_document_per_recipient():
    repo = _Repo()
    emitter = _emitter(["u1", "u2", "u3"], repo=repo)

    await emitter.notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    assert [n.user_id for n in repo.written] == ["u1", "u2", "u3"]


@pytest.mark.asyncio
async def test_the_actor_never_notifies_themselves():
    repo = _Repo()
    emitter = _emitter(["u1", "u2"], repo=repo)

    await emitter.notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u1", "username": "A"}
    )

    assert [n.user_id for n in repo.written] == ["u2"]


@pytest.mark.asyncio
async def test_one_notification_per_action_not_per_bid():
    """96 blocks must produce one document per recipient, carrying the count."""
    repo = _Repo()
    emitter = _emitter(["u1"], repo=repo)
    context = _context()
    context["bid_count"] = 96

    await emitter.notify(
        "block_bid.submitted_for_approval", context, {"user_id": "u9", "username": "A"}
    )

    assert len(repo.written) == 1
    assert "96" in repo.written[0].body
    assert repo.written[0].payload["bid_count"] == 96


@pytest.mark.asyncio
async def test_actor_name_falls_back_to_username_then_system():
    repo = _Repo()
    await _emitter(["u1"], repo=repo).notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9"}
    )
    assert "System" in repo.written[0].body


@pytest.mark.asyncio
async def test_publishes_once_naming_all_recipients():
    redis = _Redis()
    await _emitter(["u1", "u2"], redis=redis).notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    assert len(redis.published) == 1


@pytest.mark.asyncio
async def test_empty_audience_writes_nothing_and_publishes_nothing():
    repo, redis = _Repo(), _Redis()
    await _emitter([], repo=repo, redis=redis).notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    assert repo.written in (None, [])
    assert redis.published == []


@pytest.mark.asyncio
async def test_a_redis_failure_does_not_lose_the_mongo_write():
    class _BrokenRedis:
        async def publish(self, channel, payload):
            raise ConnectionError("redis down")

    repo = _Repo()
    ids = await _emitter(["u1"], repo=repo, redis=_BrokenRedis()).notify(
        "block_bid.submitted_for_approval", _context(), {"user_id": "u9", "username": "A"}
    )

    assert len(repo.written) == 1
    assert ids == ["id0"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest libs/tests/test_notification_emitter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'libs.notifications.emitter'`

- [ ] **Step 3: Write minimal implementation**

```python
# libs/notifications/emitter.py
"""The single entry point callers use: notify().

Call sites know nothing beyond this function. They must always wrap the call in
try/except -- a failed notification must never fail the business action that
triggered it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from libs.notifications.audience import DIRECTORY_KEY
from libs.notifications.events import get_event
from libs.notifications.models import Notification
from libs.notifications.pubsub import publish

logger = logging.getLogger(__name__)


class NotificationEmitter:
    def __init__(self, repository, directory, redis_client):
        self.repository = repository
        self.directory = directory
        self.redis_client = redis_client

    async def notify(
        self, event_key: str, context: Dict[str, Any], actor: Dict[str, Any]
    ) -> List[str]:
        event = get_event(event_key)

        actor_id = str(actor.get("user_id") or "")
        actor_name = actor.get("username") or actor.get("name") or "System"

        render_context = dict(context)
        render_context["actor_name"] = actor_name
        render_context[DIRECTORY_KEY] = self.directory

        recipients = [
            user_id
            for user_id in await event.audience.resolve(render_context)
            if user_id != actor_id
        ]
        if not recipients:
            logger.info("No recipients for %s after removing the actor", event_key)
            return []

        title, body, link = event.render(render_context)
        created_at = datetime.now()
        actor_doc = {"user_id": actor_id, "name": actor_name}

        notifications = [
            Notification(
                user_id=user_id,
                event_key=event_key,
                title=title,
                body=body,
                payload=dict(context),
                link=link,
                actor=actor_doc,
                severity=event.severity.value,
                created_at=created_at,
            )
            for user_id in recipients
        ]

        ids = await self.repository.insert_many_for(notifications)

        # Durably stored above. A publish failure costs liveness, never the record.
        try:
            await publish(
                self.redis_client,
                recipients,
                {
                    "_id": ids[0] if ids else None,
                    "event_key": event_key,
                    "title": title,
                    "body": body,
                    "payload": dict(context),
                    "link": link,
                    "actor": actor_doc,
                    "severity": event.severity.value,
                    "read_at": None,
                    "created_at": created_at.isoformat(),
                },
            )
        except Exception:
            logger.exception("Notification published failed for %s", event_key)

        return ids


_emitter: Optional[NotificationEmitter] = None


def set_emitter(emitter: Optional[NotificationEmitter]) -> None:
    global _emitter
    _emitter = emitter


def get_emitter() -> Optional[NotificationEmitter]:
    return _emitter


async def notify(
    event_key: str, context: Dict[str, Any], actor: Dict[str, Any]
) -> List[str]:
    """No-ops when notifications are not configured, so importing this module
    never makes a service depend on Redis being up."""
    if _emitter is None:
        logger.debug("Notification emitter not configured; skipping %s", event_key)
        return []
    return await _emitter.notify(event_key, context, actor)
```

Update the package exports:

```python
# libs/notifications/__init__.py
from libs.notifications.emitter import (
    NotificationEmitter,
    get_emitter,
    notify,
    set_emitter,
)
from libs.notifications.models import Audience, EventType, Notification, Severity

__all__ = [
    "Audience",
    "EventType",
    "Notification",
    "NotificationEmitter",
    "Severity",
    "get_emitter",
    "notify",
    "set_emitter",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest libs/tests/test_notification_emitter.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Run the whole notification suite**

Run: `python -m pytest libs/tests/ -k notification -v`
Expected: PASS, 28 tests

- [ ] **Step 6: Commit**

```bash
git add libs/notifications/emitter.py libs/notifications/__init__.py libs/tests/test_notification_emitter.py
git commit -m "feat(notifications): add the emitter"
```

---

## Task 7: Stream tickets

**Files:**
- Create: `services/processing/app/v2/api/router/notification_routes.py`
- Test: `libs/tests/test_notification_tickets.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces:
  - `TICKET_TTL_SECONDS = 30`
  - `ticket_key(ticket: str) -> str` → `f"notif:ticket:{ticket}"`
  - `async mint_ticket(redis_client, user_id, token, exp) -> str`
  - `async consume_ticket(redis_client, ticket) -> dict | None`
  - `router` — `APIRouter(prefix="/notifications", tags=["notifications"])`

`EventSource` cannot set headers, so the JWT cannot ride the stream request. A single-use ticket keeps the live token out of nginx access logs.

- [ ] **Step 1: Write the failing test**

```python
# libs/tests/test_notification_tickets.py
import json

import pytest

from services.processing.app.v2.api.router.notification_routes import (
    TICKET_TTL_SECONDS,
    consume_ticket,
    mint_ticket,
    ticket_key,
)


class _FakeRedis:
    """GETDEL semantics: read and delete atomically."""

    def __init__(self):
        self.store = {}
        self.setex_calls = []

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl))
        self.store[key] = value

    async def getdel(self, key):
        return self.store.pop(key, None)


@pytest.mark.asyncio
async def test_ticket_is_random_and_stored_with_a_30_second_ttl():
    redis = _FakeRedis()
    ticket = await mint_ticket(redis, "u1", "jwt-abc", 1234567890)

    assert len(ticket) >= 32
    assert redis.setex_calls == [(ticket_key(ticket), TICKET_TTL_SECONDS)]
    assert TICKET_TTL_SECONDS == 30


@pytest.mark.asyncio
async def test_stored_payload_retains_the_token_for_revocation_checks():
    redis = _FakeRedis()
    ticket = await mint_ticket(redis, "u1", "jwt-abc", 999)

    payload = json.loads(redis.store[ticket_key(ticket)])
    assert payload == {"user_id": "u1", "token": "jwt-abc", "exp": 999}


@pytest.mark.asyncio
async def test_two_mints_never_collide():
    redis = _FakeRedis()
    tickets = {await mint_ticket(redis, "u1", "t", 1) for _ in range(50)}
    assert len(tickets) == 50


@pytest.mark.asyncio
async def test_consume_returns_the_payload_once_then_never_again():
    redis = _FakeRedis()
    ticket = await mint_ticket(redis, "u1", "jwt-abc", 999)

    assert (await consume_ticket(redis, ticket))["user_id"] == "u1"
    assert await consume_ticket(redis, ticket) is None


@pytest.mark.asyncio
async def test_unknown_ticket_returns_none():
    assert await consume_ticket(_FakeRedis(), "never-issued") is None


@pytest.mark.asyncio
async def test_corrupt_payload_returns_none_rather_than_raising():
    redis = _FakeRedis()
    redis.store[ticket_key("t1")] = "not json"
    assert await consume_ticket(redis, "t1") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest libs/tests/test_notification_tickets.py -v`
Expected: FAIL — `ModuleNotFoundError: ... notification_routes`

- [ ] **Step 3: Write minimal implementation**

```python
# services/processing/app/v2/api/router/notification_routes.py
"""Notification endpoints: ticket, stream, list, read.

EventSource cannot set an Authorization header and this client stores tokens in
sessionStorage with no cookie auth, so the stream authenticates with a
short-lived single-use ticket. What reaches nginx access logs is opaque and
already dead, and is not the access token.
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from services.processing.app.v2.dependencies import get_user_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])

TICKET_TTL_SECONDS = 30


def ticket_key(ticket: str) -> str:
    return f"notif:ticket:{ticket}"


async def mint_ticket(redis_client, user_id: str, token: str, exp: Any) -> str:
    ticket = secrets.token_urlsafe(32)
    await redis_client.setex(
        ticket_key(ticket),
        TICKET_TTL_SECONDS,
        json.dumps({"user_id": user_id, "token": token, "exp": exp}),
    )
    return ticket


async def consume_ticket(redis_client, ticket: str) -> Optional[Dict[str, Any]]:
    """Atomic read-and-delete, so a replayed ticket fails.

    GETDEL needs Redis 6.2+. On older servers replace with a Lua script -- a
    non-atomic GET then DEL reopens the replay window this exists to close.
    """
    raw = await redis_client.getdel(ticket_key(ticket))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("Corrupt ticket payload discarded")
        return None


def require_authenticated(user_context: Dict[str, Any]) -> str:
    """get_user_context is safe-for-public-routes: it returns user_id
    'anonymous' rather than raising. Stream endpoints must reject that."""
    user_id = user_context.get("user_id")
    if not user_id or user_id == "anonymous":
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(user_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest libs/tests/test_notification_tickets.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add services/processing/app/v2/api/router/notification_routes.py libs/tests/test_notification_tickets.py
git commit -m "feat(notifications): add single-use stream tickets"
```

---

## Task 8: SSE stream endpoint

**Files:**
- Modify: `services/processing/app/v2/api/router/notification_routes.py`
- Test: `libs/tests/test_notification_stream.py`

**Interfaces:**
- Consumes: `ConnectionRegistry` (Task 4), ticket helpers (Task 7)
- Produces:
  - `HEARTBEAT_SECONDS = 25`
  - `format_sse(event: str, data: dict) -> str`
  - `format_heartbeat() -> str`
  - `async is_revoked(redis_client, token) -> bool`
  - `GET /notifications/stream?ticket=…`

- [ ] **Step 1: Write the failing test**

```python
# libs/tests/test_notification_stream.py
import json

import pytest

from services.processing.app.v2.api.router.notification_routes import (
    HEARTBEAT_SECONDS,
    format_heartbeat,
    format_sse,
    is_revoked,
)


def test_sse_frame_has_event_data_and_a_blank_line_terminator():
    frame = format_sse("notification", {"title": "t"})
    assert frame == 'event: notification\ndata: {"title": "t"}\n\n'


def test_sse_data_is_single_line_json_so_the_frame_cannot_be_split():
    frame = format_sse("sync", {"items": [{"body": "line one\nline two"}]})
    body = frame.split("data: ", 1)[1]
    assert body.count("\n") == 3  # the escaped newline is \\n, not a real one
    assert json.loads(body.strip())["items"][0]["body"] == "line one\nline two"


def test_heartbeat_is_a_comment_the_browser_ignores():
    assert format_heartbeat() == ": ping\n\n"


def test_heartbeat_interval_leaves_margin_under_nginx_60s_default():
    assert HEARTBEAT_SECONDS == 25
    assert HEARTBEAT_SECONDS * 2 < 60


@pytest.mark.asyncio
async def test_revoked_token_is_detected():
    class _Redis:
        async def sismember(self, key, member):
            assert key == "jwt_blacklist"
            return member == "revoked-token"

    assert await is_revoked(_Redis(), "revoked-token") is True
    assert await is_revoked(_Redis(), "live-token") is False


@pytest.mark.asyncio
async def test_revocation_check_fails_open_when_redis_is_down():
    """Matches existing jwt_validator.py:213-214 behaviour. Documented, not new."""

    class _BrokenRedis:
        async def sismember(self, key, member):
            raise ConnectionError("redis down")

    assert await is_revoked(_BrokenRedis(), "any") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest libs/tests/test_notification_stream.py -v`
Expected: FAIL — `ImportError: cannot import name 'format_sse'`

- [ ] **Step 3: Write minimal implementation**

Append to `notification_routes.py`:

```python
import asyncio
import time

from fastapi import Query
from fastapi.responses import StreamingResponse

HEARTBEAT_SECONDS = 25
SYNC_LIMIT = 50
BLACKLIST_SET = "jwt_blacklist"


def format_sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def format_heartbeat() -> str:
    """An SSE comment. Keeps nginx and any proxy from reaping an idle stream;
    without it a quiet stream dies once a minute and the feature looks broken
    exactly during the periods it exists to cover."""
    return ": ping\n\n"


async def is_revoked(redis_client, token: str) -> bool:
    """Fails open when Redis is unreachable, matching
    libs/core/jwt_validator.py:213-214. Inherited behaviour, not introduced."""
    try:
        return bool(await redis_client.sismember(BLACKLIST_SET, token))
    except Exception:
        logger.error("Blacklist check failed; treating token as valid")
        return False


@router.get("/stream")
async def stream(request: Request, ticket: str = Query(...)):
    redis_client = request.app.state.notification_redis
    registry = request.app.state.notification_registry
    repository = request.app.state.notification_repository

    session = await consume_ticket(redis_client, ticket)
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid or expired ticket")

    user_id = session["user_id"]
    token = session.get("token", "")
    expires_at = session.get("exp") or 0

    queue: asyncio.Queue = asyncio.Queue()
    registry.add(user_id, queue)

    async def events():
        try:
            items = await repository.list_for_user(user_id, limit=SYNC_LIMIT)
            unread = await repository.unread_count(user_id)
            yield format_sse("sync", {"items": items, "unreadCount": unread})

            while True:
                if await request.is_disconnected():
                    break
                if expires_at and time.time() >= float(expires_at):
                    break
                try:
                    notification = await asyncio.wait_for(
                        queue.get(), timeout=HEARTBEAT_SECONDS
                    )
                    yield format_sse("notification", notification)
                except asyncio.TimeoutError:
                    if token and await is_revoked(redis_client, token):
                        break
                    yield format_heartbeat()
        finally:
            registry.remove(user_id, queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # tells nginx not to buffer, even if the
                                        # location block is missed
        },
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest libs/tests/test_notification_stream.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add services/processing/app/v2/api/router/notification_routes.py libs/tests/test_notification_stream.py
git commit -m "feat(notifications): add the SSE stream endpoint"
```

---

## Task 9: REST endpoints and service wiring

**Files:**
- Modify: `services/processing/app/v2/api/router/notification_routes.py`
- Modify: `services/processing/app/main.py`
- Test: `libs/tests/test_notification_routes_wiring.py`

**Interfaces:**
- Consumes: everything above
- Produces: `POST /ticket`, `GET ""`, `GET /unread-count`, `POST /{id}/read`, `POST /read-all`; `app.state.notification_*` populated at startup

- [ ] **Step 1: Write the failing test**

```python
# libs/tests/test_notification_routes_wiring.py
from services.processing.app.v2.api.router.notification_routes import router


def test_router_is_mounted_under_notifications():
    assert router.prefix == "/notifications"


def test_every_documented_endpoint_exists():
    paths = {(r.path, tuple(sorted(r.methods - {"HEAD", "OPTIONS"}))) for r in router.routes}
    assert ("/notifications/ticket", ("POST",)) in paths
    assert ("/notifications/stream", ("GET",)) in paths
    assert ("/notifications", ("GET",)) in paths
    assert ("/notifications/unread-count", ("GET",)) in paths
    assert ("/notifications/{notification_id}/read", ("POST",)) in paths
    assert ("/notifications/read-all", ("POST",)) in paths


def test_read_all_is_declared_before_the_id_route():
    """Otherwise /read-all matches /{notification_id}/read patterns first."""
    order = [r.path for r in router.routes]
    assert order.index("/notifications/read-all") < order.index(
        "/notifications/{notification_id}/read"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest libs/tests/test_notification_routes_wiring.py -v`
Expected: FAIL — only `/stream` exists

- [ ] **Step 3: Write minimal implementation**

Append to `notification_routes.py` — note `read-all` **before** the `{notification_id}` route:

```python
from fastapi import Body


@router.post("/ticket")
async def create_ticket(
    request: Request, user_context: Dict[str, Any] = Depends(get_user_context)
):
    user_id = require_authenticated(user_context)
    auth_header = request.headers.get("authorization", "")
    token = auth_header[7:] if auth_header.lower().startswith("bearer ") else ""
    exp = (user_context.get("user") or {}).get("exp")

    ticket = await mint_ticket(
        request.app.state.notification_redis, user_id, token, exp
    )
    return {"ticket": ticket, "expires_in": TICKET_TTL_SECONDS}


@router.get("")
async def list_notifications(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    user_context: Dict[str, Any] = Depends(get_user_context),
):
    user_id = require_authenticated(user_context)
    repository = request.app.state.notification_repository
    return {
        "items": await repository.list_for_user(user_id, limit=limit, skip=skip),
        "unreadCount": await repository.unread_count(user_id),
    }


@router.get("/unread-count")
async def unread_count(
    request: Request, user_context: Dict[str, Any] = Depends(get_user_context)
):
    user_id = require_authenticated(user_context)
    count = await request.app.state.notification_repository.unread_count(user_id)
    return {"unreadCount": count}


@router.post("/read-all")
async def read_all(
    request: Request, user_context: Dict[str, Any] = Depends(get_user_context)
):
    user_id = require_authenticated(user_context)
    updated = await request.app.state.notification_repository.mark_all_read(user_id)
    return {"updated": updated}


@router.post("/{notification_id}/read")
async def mark_read(
    request: Request,
    notification_id: str,
    user_context: Dict[str, Any] = Depends(get_user_context),
):
    user_id = require_authenticated(user_context)
    ok = await request.app.state.notification_repository.mark_read(
        user_id, notification_id
    )
    return {"updated": bool(ok)}
```

In `services/processing/app/main.py`, add the import beside the other router imports:

```python
from services.processing.app.v2.api.router.notification_routes import (
    router as notification_router,
)
```

Register it beside the other `v2_app.include_router(...)` calls near line 336:

```python
v2_app.include_router(notification_router)
```

Add startup and shutdown wiring. Indexes are created here, never at import time — an unawaited `create_index` in a constructor at import is what caused the Motor event-loop misbind elsewhere in this codebase:

```python
import asyncio

import redis.asyncio as aioredis

from libs.notifications.audience import AuthDirectory
from libs.notifications.emitter import NotificationEmitter, set_emitter
from libs.notifications.pubsub import ConnectionRegistry, run_subscriber
from libs.notifications.repository import NotificationRepository


@app.on_event("startup")
async def start_notifications():
    settings = get_settings()
    app.state.notification_redis = aioredis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        decode_responses=True,
    )
    app.state.notification_registry = ConnectionRegistry()

    repository = NotificationRepository(settings.MONGODB_URI, settings.MONGODB_DATABASE)
    await repository.ensure_indexes()
    app.state.notification_repository = repository

    auth_db = repository.client[settings.AUTH_DB_NAME]
    directory = AuthDirectory(
        users_collection=auth_db["users"], roles_collection=auth_db["roles"]
    )
    set_emitter(
        NotificationEmitter(repository, directory, app.state.notification_redis)
    )

    app.state.notification_stop = asyncio.Event()
    app.state.notification_task = asyncio.create_task(
        run_subscriber(
            app.state.notification_redis,
            app.state.notification_registry,
            app.state.notification_stop,
        )
    )


@app.on_event("shutdown")
async def stop_notifications():
    set_emitter(None)
    app.state.notification_stop.set()
    app.state.notification_task.cancel()
    await app.state.notification_redis.aclose()
```

If `services/processing/app/core/config.py` has no `AUTH_DB_NAME`, add it:

```python
    AUTH_DB_NAME: str = "auth_db"   # overridden by .env
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest libs/tests/test_notification_routes_wiring.py -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Verify the service starts and the endpoint answers**

```bash
python run_services.py --service processing &
sleep 8
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8003/api/v2/notifications/unread-count
```
Expected: `401` (unauthenticated, not 404 or 500)

- [ ] **Step 6: Commit**

```bash
git add services/processing/app/v2/api/router/notification_routes.py services/processing/app/main.py services/processing/app/core/config.py libs/tests/test_notification_routes_wiring.py
git commit -m "feat(notifications): add REST endpoints and service wiring"
```

---

## Task 10: Emit from block bid submit and approve

**Files:**
- Modify: `services/processing/app/v2/services/block_bid_services.py:125-133` and `:200-209`
- Test: `libs/tests/test_block_bid_notification_emit.py`

**Interfaces:**
- Consumes: `notify` (Task 6)
- Produces: nothing new

These are the two dead `notification_data` placeholders. They are assigned and never read — the typo `"bid_for_Approvel"` at line 132 proves nothing consumes them.

- [ ] **Step 1: Write the failing test**

```python
# libs/tests/test_block_bid_notification_emit.py
import inspect

from services.processing.app.v2.services import block_bid_services


def test_the_dead_placeholders_are_gone():
    source = inspect.getsource(block_bid_services)
    assert "notification_data = {" not in source
    assert "bid_for_Approvel" not in source


def test_notify_is_called_from_the_service():
    assert "notify(" in inspect.getsource(block_bid_services)


def test_every_notify_call_is_wrapped_so_it_cannot_fail_a_bid():
    """A bid submission must never fail because Redis hiccuped."""
    source = inspect.getsource(block_bid_services)
    for index, line in enumerate(source.splitlines()):
        if "await notify(" in line:
            preceding = source.splitlines()[max(0, index - 6):index]
            assert any("try:" in p for p in preceding), (
                f"unguarded notify() at source line {index + 1}"
            )


def test_the_hardcoded_rtm_title_is_not_reintroduced():
    """block_bid_services.py:128 said 'RTM Block Bid Submitted for Approval'
    on a path that also serves DAM, GDAM and HPDAM."""
    source = inspect.getsource(block_bid_services)
    assert "RTM Block Bid Submitted for Approval" not in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest libs/tests/test_block_bid_notification_emit.py -v`
Expected: FAIL — the placeholders are still present

- [ ] **Step 3: Write minimal implementation**

Add the import at the top of `block_bid_services.py`:

```python
from libs.notifications import notify
```

Replace lines 124-134 (the `# Notification placeholder` block) with:

```python
        # Notify every user who can see this market's approval screen.
        # Action-level: one notification carrying the count, never one per bid.
        try:
            market = str(request.get("market_type", ["RTM"])[0]).upper()
            await notify(
                "block_bid.submitted_for_approval",
                {
                    "market_type": market,
                    "market_lower": market.lower(),
                    "bid_count": len(data),
                    "schedule_date": formatted_date,
                },
                actor={
                    "user_id": request.get("user_id"),
                    "username": request.get("current_user") or request.get("username"),
                },
            )
        except Exception:
            logger.exception("Notification emit failed for block bid submission")
```

Replace lines 200-209 (the second placeholder, currently **inside** the loop) by deleting it there and adding this **after** the loop, immediately before `total = len(updated) + len(skipped)`:

```python
            # After the loop: one notification for the whole action.
            if updated:
                try:
                    market = str(request.get("market_type") or "RTM").upper()
                    status_label = updated[0]["status"]
                    await notify(
                        "block_bid.status_changed",
                        {
                            "market_type": market,
                            "market_lower": market.lower(),
                            "status_lower": status_label.lower(),
                            "bid_count": len(updated),
                            "schedule_date": request.get("schedule_date")
                            or request.get("biding_date")
                            or "",
                        },
                        actor={
                            "user_id": request.get("user_id"),
                            "username": request.get("current_user")
                            or request.get("username"),
                        },
                    )
                except Exception:
                    logger.exception("Notification emit failed for block bid status change")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest libs/tests/test_block_bid_notification_emit.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Confirm nothing else regressed**

Run: `python -m pytest libs/tests/ -v`
Expected: PASS, all tests including the existing 4 in `test_block_bid_market_routing.py`

- [ ] **Step 6: Commit**

```bash
git add services/processing/app/v2/services/block_bid_services.py libs/tests/test_block_bid_notification_emit.py
git commit -m "feat(notifications): emit on block bid submit and status change"
```

---

## Task 11: Client endpoints and Redux slice

**Files:**
- Modify: `client/src/config/apiEndpoints.js`
- Create: `client/src/store/slices/notificationsSlice.js`
- Modify: `client/src/store/store.jsx`
- Test: `client/src/store/slices/notificationsSlice.test.js`

**Interfaces:**
- Produces:
  - Actions: `syncReceived({items, unreadCount})`, `notificationReceived(notification)`, `markedRead(id)`, `markedAllRead()`, `connectionStateChanged(state)`
  - State: `{items: [], unreadCount: 0, connectionState: "idle"}`

`syncReceived` **replaces** state; `notificationReceived` **prepends**. That difference is what stops a replayed backlog from re-toasting.

- [ ] **Step 1: Write the failing test**

```js
// client/src/store/slices/notificationsSlice.test.js
import test from "node:test";
import assert from "node:assert/strict";

import reducer, {
  connectionStateChanged,
  markedAllRead,
  markedRead,
  notificationReceived,
  syncReceived,
} from "./notificationsSlice.js";

const initial = reducer(undefined, { type: "@@INIT" });
const item = (id, read = null) => ({ _id: id, title: `t${id}`, body: "b", read_at: read });

test("starts empty and disconnected", () => {
  assert.deepEqual(initial, { items: [], unreadCount: 0, connectionState: "idle" });
});

test("sync replaces state rather than appending", () => {
  const seeded = reducer(initial, syncReceived({ items: [item("a")], unreadCount: 1 }));
  const resynced = reducer(seeded, syncReceived({ items: [item("b")], unreadCount: 5 }));

  assert.deepEqual(resynced.items.map((i) => i._id), ["b"]);
  assert.equal(resynced.unreadCount, 5);
});

test("a live notification prepends and increments", () => {
  const seeded = reducer(initial, syncReceived({ items: [item("a")], unreadCount: 1 }));
  const next = reducer(seeded, notificationReceived(item("b")));

  assert.deepEqual(next.items.map((i) => i._id), ["b", "a"]);
  assert.equal(next.unreadCount, 2);
});

test("a duplicate id is ignored so a reconnect cannot double-count", () => {
  const seeded = reducer(initial, syncReceived({ items: [item("a")], unreadCount: 1 }));
  const next = reducer(seeded, notificationReceived(item("a")));

  assert.equal(next.items.length, 1);
  assert.equal(next.unreadCount, 1);
});

test("marking read decrements once and never below zero", () => {
  const seeded = reducer(initial, syncReceived({ items: [item("a")], unreadCount: 1 }));
  const once = reducer(seeded, markedRead("a"));
  const twice = reducer(once, markedRead("a"));

  assert.equal(once.unreadCount, 0);
  assert.equal(twice.unreadCount, 0);
  assert.ok(once.items[0].read_at);
});

test("mark all read zeroes the count and stamps every item", () => {
  const seeded = reducer(
    initial,
    syncReceived({ items: [item("a"), item("b")], unreadCount: 2 }),
  );
  const next = reducer(seeded, markedAllRead());

  assert.equal(next.unreadCount, 0);
  assert.ok(next.items.every((i) => i.read_at));
});

test("connection state is tracked for the polling fallback", () => {
  assert.equal(
    reducer(initial, connectionStateChanged("open")).connectionState,
    "open",
  );
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd client && node --test src/store/slices/notificationsSlice.test.js`
Expected: FAIL — `Cannot find module './notificationsSlice.js'`

- [ ] **Step 3: Write minimal implementation**

```js
// client/src/store/slices/notificationsSlice.js
import { createSlice } from "@reduxjs/toolkit";

/**
 * Bell state.
 *
 * `syncReceived` replaces everything; `notificationReceived` prepends. The
 * distinction matters: a reconnect replays the backlog through sync, and only
 * live frames should raise a toast. Without it, every laptop-lid reconnect
 * would re-toast everything unread.
 */
const initialState = {
  items: [],
  unreadCount: 0,
  connectionState: "idle", // idle | connecting | open | polling
};

const notificationsSlice = createSlice({
  name: "notifications",
  initialState,
  reducers: {
    syncReceived: (state, action) => {
      state.items = action.payload?.items ?? [];
      state.unreadCount = action.payload?.unreadCount ?? 0;
    },
    notificationReceived: (state, action) => {
      const incoming = action.payload;
      if (!incoming?._id) return;
      if (state.items.some((i) => i._id === incoming._id)) return;
      state.items.unshift(incoming);
      if (!incoming.read_at) state.unreadCount += 1;
    },
    markedRead: (state, action) => {
      const target = state.items.find((i) => i._id === action.payload);
      if (!target || target.read_at) return;
      target.read_at = new Date().toISOString();
      state.unreadCount = Math.max(0, state.unreadCount - 1);
    },
    markedAllRead: (state) => {
      const now = new Date().toISOString();
      state.items.forEach((i) => {
        if (!i.read_at) i.read_at = now;
      });
      state.unreadCount = 0;
    },
    connectionStateChanged: (state, action) => {
      state.connectionState = action.payload;
    },
  },
});

export const {
  syncReceived,
  notificationReceived,
  markedRead,
  markedAllRead,
  connectionStateChanged,
} = notificationsSlice.actions;

export default notificationsSlice.reducer;
```

Add to `client/src/config/apiEndpoints.js` inside the `processing` object:

```js
    notifications: joinPath(apiBase, "notifications"),
    notificationTicket: joinPath(apiBase, "notifications/ticket"),
    notificationStream: joinPath(apiBase, "notifications/stream"),
    notificationUnreadCount: joinPath(apiBase, "notifications/unread-count"),
    notificationReadAll: joinPath(apiBase, "notifications/read-all"),
    notificationRead: (id) => joinPath(apiBase, `notifications/${id}/read`),
```

Register the reducer in `client/src/store/store.jsx` alongside the existing slices:

```js
import notificationsReducer from "./slices/notificationsSlice";
// ... inside configureStore reducer object:
    notifications: notificationsReducer,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd client && node --test src/store/slices/notificationsSlice.test.js`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add client/src/store/slices/notificationsSlice.js client/src/store/slices/notificationsSlice.test.js client/src/config/apiEndpoints.js client/src/store/store.jsx
git commit -m "feat(notifications): add client slice and endpoints"
```

---

## Task 12: Stream logic — backoff, watchdog, idle

**Files:**
- Create: `client/src/utils/notificationStream.js`
- Test: `client/src/utils/notificationStream.test.js`

**Interfaces:**
- Produces:
  - `HEARTBEAT_TIMEOUT_MS = 45000`, `HIDDEN_IDLE_MS = 300000`
  - `nextBackoffMs(attempt) -> number`
  - `parseFrame(event) -> {kind, payload}`
  - `createWatchdog({timeoutMs, onTimeout, now})` → `{ pet(), stop(), isExpired(at) }`

Pure functions with no browser APIs, so they can be tested under `node --test`.

- [ ] **Step 1: Write the failing test**

```js
// client/src/utils/notificationStream.test.js
import test from "node:test";
import assert from "node:assert/strict";

import {
  HEARTBEAT_TIMEOUT_MS,
  HIDDEN_IDLE_MS,
  createWatchdog,
  nextBackoffMs,
  parseFrame,
} from "./notificationStream.js";

test("backoff grows then caps at 30s", () => {
  assert.ok(nextBackoffMs(0) >= 1000 && nextBackoffMs(0) <= 2000);
  assert.ok(nextBackoffMs(3) > nextBackoffMs(1));
  assert.ok(nextBackoffMs(50) <= 30000);
});

test("backoff is jittered so reconnects do not stampede after a deploy", () => {
  const samples = new Set(Array.from({ length: 20 }, () => nextBackoffMs(4)));
  assert.ok(samples.size > 1);
});

test("parses a sync frame", () => {
  const parsed = parseFrame({
    type: "sync",
    data: JSON.stringify({ items: [], unreadCount: 3 }),
  });
  assert.equal(parsed.kind, "sync");
  assert.equal(parsed.payload.unreadCount, 3);
});

test("parses a notification frame", () => {
  const parsed = parseFrame({
    type: "notification",
    data: JSON.stringify({ _id: "n1", title: "t" }),
  });
  assert.equal(parsed.kind, "notification");
  assert.equal(parsed.payload._id, "n1");
});

test("malformed json yields an error kind rather than throwing", () => {
  assert.equal(parseFrame({ type: "sync", data: "{not json" }).kind, "error");
});

test("watchdog fires only after the timeout with no heartbeat", () => {
  let firedAt = null;
  let clock = 0;
  const watchdog = createWatchdog({
    timeoutMs: HEARTBEAT_TIMEOUT_MS,
    onTimeout: () => { firedAt = clock; },
    now: () => clock,
  });

  watchdog.pet();
  clock = HEARTBEAT_TIMEOUT_MS - 1;
  assert.equal(watchdog.isExpired(clock), false);

  clock = HEARTBEAT_TIMEOUT_MS + 1;
  assert.equal(watchdog.isExpired(clock), true);
});

test("petting the watchdog resets it", () => {
  let clock = 0;
  const watchdog = createWatchdog({
    timeoutMs: HEARTBEAT_TIMEOUT_MS,
    onTimeout: () => {},
    now: () => clock,
  });

  watchdog.pet();
  clock = HEARTBEAT_TIMEOUT_MS - 1;
  watchdog.pet();
  clock = HEARTBEAT_TIMEOUT_MS + 1;

  assert.equal(watchdog.isExpired(clock), false);
});

test("timeouts leave margin over the 25s server heartbeat", () => {
  assert.equal(HEARTBEAT_TIMEOUT_MS, 45000);
  assert.ok(HEARTBEAT_TIMEOUT_MS > 25000);
  assert.equal(HIDDEN_IDLE_MS, 300000);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd client && node --test src/utils/notificationStream.test.js`
Expected: FAIL — `Cannot find module './notificationStream.js'`

- [ ] **Step 3: Write minimal implementation**

```js
// client/src/utils/notificationStream.js
/**
 * Pure stream logic, deliberately free of browser APIs so it is testable.
 *
 * The watchdog exists because EventSource.onerror does NOT fire when a socket
 * dies silently -- a closed laptop lid, a dropped VPN. Without it the bell
 * looks alive and is dead.
 */

/** Server sends a heartbeat every 25s; 45s gives margin without being sleepy. */
export const HEARTBEAT_TIMEOUT_MS = 45000;

/** Close the stream after this long hidden. A toast in an invisible tab is
 *  invisible, and reconnecting replays via the sync frame, so this is free. */
export const HIDDEN_IDLE_MS = 300000;

const BASE_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30000;

/** Exponential with jitter. Jitter matters: without it every stream dropped by
 *  a deploy reconnects in the same millisecond. */
export const nextBackoffMs = (attempt) => {
  const ceiling = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** attempt);
  return Math.min(MAX_BACKOFF_MS, ceiling + Math.random() * ceiling);
};

export const parseFrame = (event) => {
  try {
    return { kind: event.type, payload: JSON.parse(event.data) };
  } catch {
    return { kind: "error", payload: null };
  }
};

export const createWatchdog = ({ timeoutMs, onTimeout, now = Date.now }) => {
  let lastSeen = now();
  let stopped = false;

  return {
    pet: () => { lastSeen = now(); },
    stop: () => { stopped = true; },
    isExpired: (at = now()) => !stopped && at - lastSeen > timeoutMs,
    fire: () => { if (!stopped) onTimeout(); },
  };
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd client && node --test src/utils/notificationStream.test.js`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add client/src/utils/notificationStream.js client/src/utils/notificationStream.test.js
git commit -m "feat(notifications): add stream backoff, parsing and watchdog"
```

---

## Task 13: SharedWorker and the React hook

**Files:**
- Create: `client/src/workers/notificationStream.shared.js`
- Create: `client/src/hooks/useNotificationStream.js`

**Interfaces:**
- Consumes: Task 11 actions, Task 12 helpers
- Produces: `useNotificationStream()` — call once, high in the tree

HTTP/1.1 allows 6 connections per origin. Without the SharedWorker, a trader with 6 tabs exhausts the budget and the sixth tab's ordinary API calls hang with no error.

- [ ] **Step 1: Write the SharedWorker**

```js
// client/src/workers/notificationStream.shared.js
/**
 * Owns the single EventSource for the whole browser and rebroadcasts frames to
 * every tab. One connection per browser, not per tab.
 *
 * Tabs post: {type:"connect", url} | {type:"disconnect"}
 * Worker posts: {type:"sync"|"notification"|"state", payload}
 */
const ports = [];
let source = null;

const broadcast = (message) => {
  ports.forEach((port) => {
    try {
      port.postMessage(message);
    } catch {
      /* port closed with its tab */
    }
  });
};

const connect = (url) => {
  if (source) return;
  source = new EventSource(url);
  broadcast({ type: "state", payload: "connecting" });

  source.addEventListener("open", () => broadcast({ type: "state", payload: "open" }));
  source.addEventListener("sync", (e) =>
    broadcast({ type: "sync", payload: JSON.parse(e.data) }),
  );
  source.addEventListener("notification", (e) =>
    broadcast({ type: "notification", payload: JSON.parse(e.data) }),
  );
  source.addEventListener("error", () => {
    disconnect();
    broadcast({ type: "state", payload: "idle" });
  });
};

const disconnect = () => {
  if (source) {
    source.close();
    source = null;
  }
};

// eslint-disable-next-line no-restricted-globals
self.onconnect = (event) => {
  const port = event.ports[0];
  ports.push(port);

  port.onmessage = ({ data }) => {
    if (data?.type === "connect") connect(data.url);
    if (data?.type === "disconnect") disconnect();
  };

  port.start();
};
```

- [ ] **Step 2: Write the hook**

```js
// client/src/hooks/useNotificationStream.js
import { useEffect, useRef } from "react";
import { useDispatch } from "react-redux";

import { API_ENDPOINTS } from "../config/apiEndpoints";
import apiClient from "../utils/apiClient";
import {
  connectionStateChanged,
  notificationReceived,
  syncReceived,
} from "../store/slices/notificationsSlice";
import { HIDDEN_IDLE_MS, nextBackoffMs } from "../utils/notificationStream";

/**
 * Opens the notification stream for the logged-in user.
 *
 * Call once, high in the tree. Safe to call when logged out -- it simply does
 * not connect.
 */
export const useNotificationStream = (isAuthenticated) => {
  const dispatch = useDispatch();
  const workerRef = useRef(null);
  const idleTimerRef = useRef(null);
  const attemptRef = useRef(0);

  useEffect(() => {
    if (!isAuthenticated) return undefined;

    let cancelled = false;

    const openStream = async () => {
      try {
        const { data } = await apiClient.post(API_ENDPOINTS.processing.notificationTicket);
        if (cancelled || !data?.ticket) return;

        const url = `${API_ENDPOINTS.processing.notificationStream}?ticket=${encodeURIComponent(data.ticket)}`;

        if (typeof SharedWorker !== "undefined") {
          if (!workerRef.current) {
            workerRef.current = new SharedWorker(
              new URL("../workers/notificationStream.shared.js", import.meta.url),
              { type: "module" },
            );
            workerRef.current.port.onmessage = ({ data: message }) => {
              if (message.type === "sync") dispatch(syncReceived(message.payload));
              if (message.type === "notification")
                dispatch(notificationReceived(message.payload));
              if (message.type === "state") {
                dispatch(connectionStateChanged(message.payload));
                if (message.payload === "idle" && !cancelled) scheduleRetry();
              }
            };
            workerRef.current.port.start();
          }
          workerRef.current.port.postMessage({ type: "connect", url });
        }
        attemptRef.current = 0;
      } catch {
        scheduleRetry();
      }
    };

    const scheduleRetry = () => {
      if (cancelled) return;
      const delay = nextBackoffMs(attemptRef.current++);
      setTimeout(() => { if (!cancelled) openStream(); }, delay);
    };

    const closeStream = () => {
      workerRef.current?.port.postMessage({ type: "disconnect" });
      dispatch(connectionStateChanged("idle"));
    };

    // A window nobody is looking at should not hold a connection. Reconnecting
    // replays through the sync frame, so nothing is lost by closing.
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        idleTimerRef.current = setTimeout(closeStream, HIDDEN_IDLE_MS);
      } else {
        clearTimeout(idleTimerRef.current);
        openStream();
      }
    };

    openStream();
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      cancelled = true;
      clearTimeout(idleTimerRef.current);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      closeStream();
    };
  }, [isAuthenticated, dispatch]);
};

export default useNotificationStream;
```

- [ ] **Step 3: Verify the build compiles**

Run: `cd client && npm run build`
Expected: build succeeds with no errors referencing the new files

- [ ] **Step 4: Commit**

```bash
git add client/src/workers/notificationStream.shared.js client/src/hooks/useNotificationStream.js
git commit -m "feat(notifications): add SharedWorker stream and hook"
```

---

## Task 14: Wire the bell, add the toast and the page

**Files:**
- Modify: `client/src/components/common/header/Header.jsx` — delete `:125-172`, wire `:191-296`
- Create: `client/src/components/notifications/NotificationToast.jsx`
- Create: `client/src/pages/Notifications.jsx`
- Modify: router config — add `/notifications`

**Interfaces:**
- Consumes: Tasks 11–13

**No `dangerouslySetInnerHTML` in any file in this task.** `title` and `body` contain usernames, and 100 usernames in the live database hold stored XSS payloads.

- [ ] **Step 1: Delete the dead theme data**

In `Header.jsx`, delete the entire `initialNotifications` array at lines 125-172 (`"Your Order Has Been Shipped"`, `"Discount Available"`, …) and the `useState([...initialNotifications])` at line 173.

- [ ] **Step 2: Wire the existing dropdown to real state**

Replace the deleted state with:

```jsx
import { useDispatch, useSelector } from "react-redux";
import { API_ENDPOINTS } from "../../../config/apiEndpoints";
import apiClient from "../../../utils/apiClient";
import { markedAllRead, markedRead } from "../../../store/slices/notificationsSlice";

// inside the component:
const dispatch = useDispatch();
const notifications = useSelector((state) => state.notifications.items);
const unreadCount = useSelector((state) => state.notifications.unreadCount);

const handleNotificationClose = async (id) => {
  dispatch(markedRead(id));
  try {
    await apiClient.post(API_ENDPOINTS.processing.notificationRead(id));
  } catch {
    /* optimistic; the next sync frame corrects it */
  }
};

const handleMarkAllRead = async () => {
  dispatch(markedAllRead());
  try {
    await apiClient.post(API_ENDPOINTS.processing.notificationReadAll);
  } catch {
    /* optimistic; the next sync frame corrects it */
  }
};
```

In the existing markup at `:191-296`, replace `notifications.length` with `unreadCount` for the badge at `:208` and the `"N Unread"` label at `:223`, and change the map body to read `notification.title` and `notification.body` in place of `text1` and `text2`. Point the `Link` at `notification.link`.

- [ ] **Step 3: Create the toast**

```jsx
// client/src/components/notifications/NotificationToast.jsx
import { useEffect, useState } from "react";
import { useSelector } from "react-redux";
import { Toast, ToastContainer } from "react-bootstrap";

/**
 * Toasts only genuinely new notifications.
 *
 * Frames replayed through `sync` on reconnect must not toast -- a popup for
 * something that happened twenty minutes ago is noise. This tracks ids seen in
 * this session and shows only unseen ones that arrive while mounted.
 *
 * Text is rendered as children, never via dangerouslySetInnerHTML: title and
 * body contain usernames, and usernames in this system have contained stored
 * XSS payloads.
 */
const NotificationToast = () => {
  const items = useSelector((state) => state.notifications.items);
  const [seen, setSeen] = useState(() => new Set());
  const [visible, setVisible] = useState([]);

  useEffect(() => {
    const fresh = items.filter((i) => i._id && !seen.has(i._id) && !i.read_at);
    if (fresh.length === 0) return;

    setSeen((previous) => {
      const next = new Set(previous);
      fresh.forEach((i) => next.add(i._id));
      return next;
    });
    setVisible((previous) => [...fresh, ...previous].slice(0, 3));
  }, [items, seen]);

  const dismiss = (id) => setVisible((previous) => previous.filter((i) => i._id !== id));

  return (
    <ToastContainer position="top-end" className="p-3" style={{ zIndex: 1080 }}>
      {visible.map((item) => (
        <Toast key={item._id} onClose={() => dismiss(item._id)} delay={6000} autohide>
          <Toast.Header>
            <strong className="me-auto">{item.title}</strong>
          </Toast.Header>
          <Toast.Body>{item.body}</Toast.Body>
        </Toast>
      ))}
    </ToastContainer>
  );
};

export default NotificationToast;
```

- [ ] **Step 4: Create the page**

```jsx
// client/src/pages/Notifications.jsx
import { useSelector } from "react-redux";
import { Link } from "react-router-dom";

/** Target of the two links already in Header.jsx:249,280, which 404 today. */
const Notifications = () => {
  const items = useSelector((state) => state.notifications.items);

  if (items.length === 0) {
    return <div className="p-5 text-center text-muted">No notifications.</div>;
  }

  return (
    <div className="card">
      <div className="card-header">
        <h5 className="card-title mb-0">Notifications</h5>
      </div>
      <ul className="list-group list-group-flush">
        {items.map((item) => (
          <li
            key={item._id}
            className={`list-group-item ${item.read_at ? "" : "fw-semibold"}`}
          >
            <Link to={item.link || "#"} className="text-decoration-none">
              <div>{item.title}</div>
              <div className="text-muted fs-12">{item.body}</div>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
};

export default Notifications;
```

- [ ] **Step 5: Register the route and mount the toast**

Add `/notifications` to the router alongside the other protected routes, rendering `Notifications`. Mount `<NotificationToast />` and call `useNotificationStream(isAuthenticated)` in the authenticated layout that wraps `Header`.

- [ ] **Step 6: Verify the build**

Run: `cd client && npm run build`
Expected: build succeeds

- [ ] **Step 7: Confirm no XSS sink was introduced**

Run: `cd client && grep -rn "dangerouslySetInnerHTML" src/components/notifications src/pages/Notifications.jsx src/components/common/header/Header.jsx`
Expected: no output

- [ ] **Step 8: Commit**

```bash
git add client/src/components/common/header/Header.jsx client/src/components/notifications/NotificationToast.jsx client/src/pages/Notifications.jsx
git commit -m "feat(notifications): wire the bell, add toast and page"
```

---

## Task 15: Two-browser acceptance test

**Files:** none — this is the gate.

Green unit tests do not prove this works. This is the acceptance criterion from the spec.

- [ ] **Step 1: Start the stack**

```bash
./start.sh
```
Confirm the client is in **local** mode, not remote. A remote client sends local credentials to `epm.ptcindia.com` and the logins fail.

- [ ] **Step 2: Confirm the stream endpoint is reachable**

```bash
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8003/api/v2/notifications/stream?ticket=bogus"
```
Expected: `401`

- [ ] **Step 3: Two sessions side by side**

Open two browsers (or one normal and one private window) and log in as **different** users, both holding a role with `dam_block_bid_approval:read`.

- [ ] **Step 4: The core assertion**

As user A, submit block bids for approval. **Without touching user B's browser**, confirm B sees a toast and the bell badge increment.

Expected: toast within ~1 second. If nothing arrives, check `proxy_buffering` first — that is the failure that looks like a dead feature.

- [ ] **Step 5: Durability**

Press F5 in B's browser. Expected: unread count survives; no toast reappears.

- [ ] **Step 6: Action-level emit**

Submit bids covering 96 blocks in one action. Expected: **one** notification saying 96, not 96 notifications.

- [ ] **Step 7: The actor is not notified**

Confirm A received nothing for A's own submission.

- [ ] **Step 8: Hidden-tab close and replay**

Leave B's tab hidden for 5+ minutes. Submit as A. Focus B's tab. Expected: badge updates from the sync frame; no toast for the old event.

- [ ] **Step 9: Six-tab check**

Open 7 EPMS tabs as B. Expected: ordinary pages still load — the SharedWorker holds one connection, not seven.

- [ ] **Step 10: Logout revocation**

Log B out while a stream is open. Expected: the stream closes within 25 seconds.

- [ ] **Step 11: Redis-down degradation**

```bash
docker stop epms-redis   # or the container name in use
```
Submit as A. Expected: the submission still succeeds; B's badge updates within 30s via polling. Restart Redis afterwards.

- [ ] **Step 12: Commit any fixes and report**

```bash
git add -A && git commit -m "fix(notifications): <what the browser test found>"
```

Report honestly which of steps 4–11 passed and which did not.

---

## Self-Review

**Spec coverage**

| Spec requirement | Task |
|---|---|
| Durable Mongo log, 90-day TTL | 3 |
| Registry-driven events | 5 |
| Role-derived audience via the roles chain | 2 |
| Actor always subtracted | 6 |
| Action-level emit | 6, 10 |
| Redis pub/sub fan-out | 4 |
| Single-use ticket, GETDEL | 7 |
| SSE stream, heartbeat, sync frame | 8 |
| Revocation on heartbeat | 8 |
| REST list / unread / read | 9 |
| Emit sites replacing dead placeholders | 10 |
| Client slice, sync vs notification | 11 |
| Backoff, watchdog | 12 |
| SharedWorker, hidden-tab close | 13 |
| Bell wiring, toast, page | 14 |
| No `dangerouslySetInnerHTML` | 14 (step 7 verifies) |
| Two-browser acceptance | 15 |

**Deferred, with reasons**

- **Capacity fallback to polling** (spec "Degrade rather than fall over") — the threshold-refusal path is not built. At 50–60 users the ceiling is ~13× away, and `connectionState: "polling"` exists in the slice so the client half is ready. Add when a real ceiling is in sight.
- **nginx `location` block** — deployment, not code. Required before this works behind nginx; `X-Accel-Buffering: no` is set in Task 8 as a second line of defence.
- **GDAM/HPDAM/RTM single-bid emit sites** — 12 further dead placeholders exist across 5 services. Task 10 does block bid only; the rest are one registry entry plus one call each, once the pattern is proven.

**Type consistency** — checked: `notify(event_key, context, actor)` is called identically in Tasks 6 and 10; `AuthDirectory` methods match between Tasks 2 and 9; slice action names match between Tasks 11, 13 and 14; `ConnectionRegistry` API matches between Tasks 4 and 8.
