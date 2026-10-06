# Notifications Wave 3 — Rich Rows, Bidding Coverage, Categories

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the notification list from a two-line title/body preview into a scannable record
(type, time, description, area, actor, jump-to-page button), extend coverage from block bids to
single bids on DAM and RTM, group everything under category tabs, and land the deferred
`auth_db.users` audience index.

**Architecture:** Two new rendered fields (`category`, `area`) join `title`/`body`/`link` on
`EventType`, so every label a row shows comes from the one registry file and is frozen into the
Mongo document at emit time — no client-side event-key→label map that can drift from the server.
Single-bid events are emitted from the four thin market **controllers**, not the 3,000-line vendor
service files, which keeps the client-fenced `new_rtm_bidding_services.py` untouched. The
`/notifications` page stops reading the 50-item SSE sync slice and fetches the paginated list API
with a `category` filter.

**Tech Stack:** FastAPI / Motor / Redis pub-sub (backend), React 18 + Redux Toolkit + Bootstrap 5
(client), `node --test` (client tests), `pytest` + `pytest-asyncio` (backend tests).

---

## Status of the world (read before Task 1)

Wave 1+2 shipped and is uncommitted on `feat/notifications` in **both** repos. What exists:

| Piece | File | State |
|---|---|---|
| Event registry | `backend/libs/notifications/events.py` | 2 events, both block-bid |
| Audience rules | `backend/libs/notifications/audience.py` | `HasPermission`, `UsernamesFromContext`, `AnyOf`, `Everyone`, `SpecificUsers` |
| Emitter | `backend/libs/notifications/emitter.py` | writes Mongo + publishes per-recipient Redis frames |
| Repository | `backend/libs/notifications/repository.py` | `list_for_user`, `unread_count`, `mark_read`, `mark_all_read`, 90-day TTL |
| SSE + REST | `backend/services/processing/app/v2/api/router/notification_routes.py` | `/stream`, `/ticket`, `GET ""`, `/unread-count`, `/read-all`, `/{id}/read` |
| Emit call sites | `backend/services/processing/app/v2/services/block_bid_services.py:181,381` | block bids only |
| Redux slice | `client/src/store/slices/notificationsSlice.js` | `items`, `unreadCount`, `liveArrivals`, resets on logout |
| Bell | `client/src/components/common/header/Header.jsx:234` | top-5 preview via `recentForBell` |
| Page | `client/src/pages/Notifications.jsx` | renders `state.notifications.items` only — **never calls the list API** |

Load-tested green at 60 concurrent users. See `epms_bcrypt_offload_fix` / `epms_auth_login_blocks_event_loop`
memories for the auth fix riding in the same working tree — **it must not be committed with this work.**

### Facts verified against live local data on 2026-08-09

These drove the design. Do not re-derive them; do re-verify if the DB is re-imported.

1. **Single-bid approval docs are one collection per market**, not one collection with a
   `market_type` field like block bids: `dam_single_bid_data_for_approval` (30,678 docs),
   `gdam_…` (14), `hpdam_…` (14), and `rtm_single_bid_data_for_approval` — **which does not
   exist locally at all.** So the market is known from the *call site*, and needs no lookup.
2. Those docs carry `schedule_date`, `created_by` (maker username), `block_no`, `bid_status`,
   `approved_by`, `revision_no`. No `market_type` field.
3. **Live permissions that exist**: `dam_single_bid_approval:read`, `rtm_single_bid_approval:read`,
   `dam_block_bid_approval:read`, `rtm_block_bid_approval:read`, `dam_bid_submission_status:read`,
   `rtm_bid_submission_status:read`. **No GDAM or HPDAM single/block approval permission exists** —
   confirming `approval_permission_market()`'s existing rule that GDAM and HPDAM are approved on
   the DAM screen and must reuse `dam_*` permissions.
4. **`submitForPreExchangeData` already returns an `updated` list** containing only references
   whose `modified_count > 0` (`new_dam_bidding_services.py:3408-3450`). Notify from **that**, never
   from the request — approving an already-decided bid is a silent no-op that still returns HTTP 200
   (a known live defect), and notifying on it would announce decisions that never happened.
5. **`auth_db.users` has only `_id_` and `username_1`.** The vendor *does* write a `roles` index
   (`services/auth/app/core/database_indexes.py:75`) but never gets there: `_create_user_indexes`
   wraps its whole body in one `except OperationFailure`, and the **second** statement
   (`create_index("email", unique=True)`) fails permanently because 813 of 816 users have
   `email: null` and two share `admin@epms.com`. Every index after it is silently skipped and the
   failure is logged at WARNING. This — not a missing line of code — is why the audience query
   COLLSCANs. `auth_db.roles` is fine (`name_1`, `is_active_1`, `permissions_1`) because it is a
   separate function whose first index succeeds.

---

## Global Constraints

- Every `notify()` call site wraps the call in `try/except` and logs. **A failed notification must
  never fail the business action that triggered it.** No exceptions.
- Never emit from a market that cannot be resolved. Log and skip. A missing notification is
  recoverable; one sent to the wrong market's approvers is not.
- Never emit for a bid the write did not actually change. Drive off `updated`, not the request.
- Do not edit `backend/services/processing/app/v2/services/new_rtm_bidding_services.py`. PTC India
  fenced it verbatim: *"Don't touch the code of new_rtm_bidding_services.py (Frontend & Backend)."*
  All RTM single-bid emission goes in `new_rtm_bidding_controller.py`, which is outside the fence.
- All DB writes run against local Docker Mongo (`epms-mongo`) only.
- New rendered fields are template strings resolved from the event context, exactly like
  `title`/`body`/`link`. `EventType.render()` raises `KeyError` on a missing placeholder — keep that.
- Documents written before this wave have no `category`/`area`. Every client reader must degrade
  gracefully to a neutral label. They age out on the 90-day TTL; no backfill.
- Commits: the user commits and pushes. Never run `git commit`, `git push`, or `git merge`.
- No new client build-time env var. Endpoints derive from `VITE_API_BASE_URL` via `apiEndpoints.js`.

---

## Open decisions — raise these before Task 1, do not guess

**D1 — What "notification type" means in the row.** *Recommended:* drop the market name out of
`title` and let `title` BE the type ("Awaiting approval", "Bid approved"), because the new Area
column already carries the market and today's titles duplicate it. Alternatives: (a) add a third
`type_label` field and keep titles as-is; (b) derive the type from `severity` with a label map,
which is cheapest but collapses "submitted" and "approved" into one word, "Info". This plan is
written for the recommendation; switching to (a) adds one field to Tasks 1 and 5 and changes
nothing else.

**D2 — GDAM and HPDAM single bids.** The user named DAM and RTM. GDAM and HPDAM run through the
identical `submit_for_approval_post_data` / `submitForPreExchangeData` pair with their own thin
controllers, so covering them is Task 9 and costs a substitution table — but they route to the
**DAM** approval screen and permission (fact 3). Included as an optional task; drop it if PTC only
wants DAM/RTM.

**D3 — Bell dropdown row.** The user asked for the richer layout in "the notifications list". The
bell is a 5-item popup capped at `max-height: 20rem`. *Recommended:* the full six-field row goes on
`/notifications` only; the bell gains relative time and area, keeps its compact shape. Task 6 is
written that way.

**D4 — Per-tab unread counts.** Not in scope. Tabs filter; the single global unread badge stays.
Adding per-category counts means either N `count_documents` calls per page load or an aggregation,
for a number nobody has asked for.

**D5 — The audience is still every active user.** `dam_single_bid_approval:read` resolves to all
116 active users, same as the block permissions, because no `:approve` or `:write` permission
exists for any bid screen. This wave does not change that; it inherits it. The index in Task 4 is
correct for the query either way (`roles` + `is_active` are in every variant), which is what makes
it safe to land now despite the earlier deferral.

---

## File Structure

**Backend — modified**

| File | Responsibility after this wave |
|---|---|
| `libs/notifications/models.py` | `EventType` gains `category` + `area`; `Notification` gains both and writes them in `to_document()`; `render()` returns 5 strings |
| `libs/notifications/events.py` | Registry grows 2 → 6 events; screen-path maps grow to cover single-bid screens |
| `libs/notifications/emitter.py` | Passes the two new rendered fields into the document and the Redis frame |
| `libs/notifications/repository.py` | `list_for_user` accepts an optional `category`; new `{user_id, category, created_at}` index |
| `services/processing/app/v2/api/router/notification_routes.py` | `GET ""` accepts `category` |
| `services/processing/app/v2/controller/new_dam_bidding_controller.py` | Emits both DAM single-bid events |
| `services/processing/app/v2/controller/new_rtm_bidding_controller.py` | Emits both RTM single-bid events |
| `services/processing/app/v2/controller/new_gdam_bidding_controller.py` | Task 9 only |
| `services/processing/app/v2/controller/new_hpdam_bidding_controller.py` | Task 9 only |
| `services/auth/app/core/database_indexes.py` | Per-index failure isolation + the audience compound index |

**Backend — created**

| File | Responsibility |
|---|---|
| `libs/notifications/single_bid_context.py` | Pure helper: turn an `updated` reference list + looked-up docs into `(status → {bid_count, maker_usernames, schedule_date})`. No I/O, so it is testable without Mongo. |
| `tests/notifications/test_single_bid_context.py` | Grouping, empty input, missing fields |
| `tests/notifications/test_event_metadata.py` | `category`/`area` rendering for all 6 events |
| `tests/notifications/test_single_bid_emission.py` | Controller emits once per status group, never on skipped bids, never fails the request |

**Client — modified**

| File | Responsibility after this wave |
|---|---|
| `src/utils/notificationView.js` | Adds the pure row formatters: timestamp, area, category, actor, type |
| `src/pages/Notifications.jsx` | Server-backed paginated list + category tabs + the six-field row |
| `src/components/common/header/Header.jsx` | Bell row gains relative time + area (D3) |
| `src/config/apiEndpoints.js` | `notifications` endpoint takes query params |

**Client — created**

| File | Responsibility |
|---|---|
| `src/components/notifications/NotificationRow.jsx` | One list row. Shared by the page; kept out of `Notifications.jsx` so the page stays about fetching and tabs. |
| `src/hooks/useNotificationList.js` | Fetch + paginate + category filter + merge live arrivals from the slice |
| `src/utils/notificationView.test.js` (extend) | Formatter tests |
| `src/hooks/useNotificationList.test.js` | Fetch, filter, pagination, live-merge |

---

## Task 1: Event metadata — `category` and `area`

**Files:**
- Modify: `backend/libs/notifications/models.py:30-49` (`EventType`), `:52-78` (`Notification`)
- Modify: `backend/libs/notifications/emitter.py:49-107`
- Modify: `backend/libs/notifications/events.py:61-81`
- Test: `backend/tests/notifications/test_event_metadata.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `EventType(key, severity, title, body, audience, link, category, area)`;
  `EventType.render(context) -> (title, body, link, category, area)`;
  `Notification(..., category: str, area: str)` whose `to_document()` includes both;
  the Redis frame gains `"category"` and `"area"` keys. Tasks 5-9 depend on all three.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/notifications/test_event_metadata.py
"""Every label a notification row shows is rendered server-side and frozen into
the document, so the client never needs an event-key -> label map that can drift
from the registry."""

import pytest

from libs.notifications.events import REGISTRY, get_event
from libs.notifications.models import Notification, Severity
from datetime import datetime


def test_every_registered_event_declares_a_category_and_an_area():
    for key, event in REGISTRY.items():
        assert event.category, f"{key} has no category"
        assert event.area, f"{key} has no area"


def test_render_returns_category_and_area():
    event = get_event("block_bid.submitted_for_approval")
    title, body, link, category, area = event.render(
        {
            "market_type": "DAM",
            "approval_market": "dam",
            "approval_path": "/dam/block-bid-approval",
            "bid_count": 3,
            "schedule_date": "2026-08-10",
            "actor_name": "mukul",
        }
    )
    assert category == "bidding"
    assert area == "DAM Block Bid"


def test_document_carries_category_and_area():
    doc = Notification(
        user_id="u1",
        event_key="block_bid.submitted_for_approval",
        title="Awaiting approval",
        body="body",
        payload={},
        link="/x",
        actor={"user_id": "u2", "name": "mukul"},
        severity=Severity.ACTION_REQUIRED.value,
        category="bidding",
        area="DAM Block Bid",
        created_at=datetime(2026, 8, 9, 10, 0, 0),
    ).to_document()

    assert doc["category"] == "bidding"
    assert doc["area"] == "DAM Block Bid"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && python -m pytest tests/notifications/test_event_metadata.py -v`
Expected: FAIL — `TypeError: EventType.__init__() got an unexpected keyword argument 'category'`
is what you want to see *after* Step 3's registry edit; before any edit it fails on
`AttributeError: 'EventType' object has no attribute 'category'`. Either is a correct RED.

- [ ] **Step 3: Add the fields to `models.py`**

```python
@dataclass(frozen=True)
class EventType:
    key: str
    severity: Severity
    title: str
    body: str
    audience: Audience
    link: str
    # Rendered like title/body/link so one registry entry can serve several
    # markets: category is the /notifications tab this row belongs to, area is
    # the human-readable module label the row shows.
    category: str
    area: str

    def render(self, context: Dict[str, Any]) -> tuple[str, str, str, str, str]:
        """Render title, body, link, category and area.

        Raises KeyError on a missing placeholder. Failing loudly at emit time is
        preferable to storing half-rendered text in a maker-checker trail.
        """
        return (
            self.title.format(**context),
            self.body.format(**context),
            self.link.format(**context),
            self.category.format(**context),
            self.area.format(**context),
        )
```

```python
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
    category: str
    area: str
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
            "category": self.category,
            "area": self.area,
            "read_at": self.read_at,
            "created_at": self.created_at,
        }
```

- [ ] **Step 4: Thread them through `emitter.py`**

Change the unpack at line 49 and add the fields in both the `Notification(...)` construction and
the Redis delivery dict:

```python
        title, body, link, category, area = event.render(render_context)
```
```python
                severity=event.severity.value,
                category=category,
                area=area,
                created_at=created_at,
```
```python
                    "severity": event.severity.value,
                    "category": category,
                    "area": area,
                    "read_at": None,
```

- [ ] **Step 5: Retitle the two existing registry entries (D1) and add the new fields**

```python
CATEGORY_BIDDING = "bidding"

REGISTRY: Dict[str, EventType] = {
    "block_bid.submitted_for_approval": EventType(
        key="block_bid.submitted_for_approval",
        severity=Severity.ACTION_REQUIRED,
        # The market moved out of the title and into `area`: the row now shows
        # both, and "DAM block bid awaiting approval" beside an Area column
        # reading "DAM Block Bid" said it twice.
        title="Awaiting approval",
        body="{actor_name} submitted {bid_count} {market_type} block bid(s) for {schedule_date}.",
        audience=HasPermission("{approval_market}_block_bid_approval:read"),
        link="{approval_path}?date={schedule_date}",
        category=CATEGORY_BIDDING,
        area="{market_type} Block Bid",
    ),
    "block_bid.status_changed": EventType(
        key="block_bid.status_changed",
        severity=Severity.INFO,
        title="Bid {status_lower}",
        body="{actor_name} {status_lower} {bid_count} {market_type} block bid(s) for {schedule_date}.",
        audience=AnyOf(
            UsernamesFromContext("maker_usernames"),
            HasPermission("{approval_market}_block_bid_approval:read"),
        ),
        link="{submission_path}?date={schedule_date}",
        category=CATEGORY_BIDDING,
        area="{market_type} Block Bid",
    ),
}
```

- [ ] **Step 6: Run the new test and the whole notification suite**

Run: `cd backend && python -m pytest tests/notifications -v`
Expected: PASS. Wave 1+2 tests that construct `Notification(...)` or unpack `render()` will fail
first — update each to the new signature; that is the intended blast radius of this task, not a
regression.

- [ ] **Step 7: Commit** (stage only — the user commits)

```
feat(notifications): add category and area to the event registry
```

---

## Task 2: Category filter on the list API

**Files:**
- Modify: `backend/libs/notifications/repository.py:25-58`
- Modify: `backend/services/processing/app/v2/api/router/notification_routes.py:322-335`
- Test: `backend/tests/notifications/test_repository.py` (extend)

**Interfaces:**
- Consumes: the `category` field written in Task 1.
- Produces: `NotificationRepository.list_for_user(user_id, limit, skip, category=None)`;
  `GET /api/v2/notifications?category=bidding`. Task 7 consumes the HTTP shape.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_list_for_user_filters_by_category(repository):
    await repository.insert_many_for([
        _notification(user_id="u1", category="bidding"),
        _notification(user_id="u1", category="scheduling"),
    ])

    rows = await repository.list_for_user("u1", category="bidding")

    assert [r["category"] for r in rows] == ["bidding"]


@pytest.mark.asyncio
async def test_list_for_user_without_category_returns_everything(repository):
    await repository.insert_many_for([
        _notification(user_id="u1", category="bidding"),
        _notification(user_id="u1", category="scheduling"),
    ])

    assert len(await repository.list_for_user("u1")) == 2
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && python -m pytest tests/notifications/test_repository.py -k category -v`
Expected: FAIL — `TypeError: list_for_user() got an unexpected keyword argument 'category'`

- [ ] **Step 3: Implement**

```python
    async def list_for_user(
        self,
        user_id: str,
        limit: int = 50,
        skip: int = 0,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        # Clamp limit to valid range and skip to non-negative
        clamped_limit = max(1, min(limit, MAX_LIMIT))
        clamped_skip = max(0, skip)

        query: Dict[str, Any] = {"user_id": user_id}
        if category:
            query["category"] = category

        cursor = (
            self.collection.find(query)
            .sort("created_at", -1)
            .skip(clamped_skip)
            .limit(clamped_limit)
        )
        return [self._serialise(doc) async for doc in cursor]
```

Add to `ensure_indexes`, after the existing two:

```python
        # The tabbed list filters on category and still sorts by created_at, so
        # the {user_id, created_at} index above can only serve the prefix and
        # would sort-in-memory the rest.
        await self.collection.create_index(
            [("user_id", 1), ("category", 1), ("created_at", -1)]
        )
```

- [ ] **Step 4: Add the query param to the route**

```python
@router.get("")
async def list_notifications(
    user_id: str = Depends(current_user_id),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    category: Optional[str] = Query(None),
):
    ...
        "items": await repository.list_for_user(
            user_id, limit=limit, skip=skip, category=category
        ),
```

Keep whatever the route already returns alongside `items` — do not change that shape.

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/notifications -v` → PASS

- [ ] **Step 6: Commit**

```
feat(notifications): filter the list API by category
```

---

## Task 3: Single-bid decision context helper

**Files:**
- Create: `backend/libs/notifications/single_bid_context.py`
- Test: `backend/tests/notifications/test_single_bid_context.py`

**Why it exists:** block bids get their maker usernames and schedule date from
`block_bid_repo.get_bid_decision_context()`, which groups by `market_type` — a field single-bid
docs do not have (fact 1). Single bids need the same grouping *by status only*, over documents the
caller has already fetched. Keeping it pure means the four market controllers share one tested
implementation and it can be tested without Mongo.

**Interfaces:**
- Consumes: Nothing.
- Produces:
  `group_single_bid_decisions(updated: list[dict], docs: list[dict]) -> dict[str, dict]`
  keyed by lowercased status, each value `{"bid_count": int, "maker_usernames": list[str],
  "schedule_date": str}`. Tasks 5, 8 and 9 call it.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/notifications/test_single_bid_context.py
"""Grouping the checker's decision batch for notification.

One /submit-pre-exchange-data call can approve some bids and reject others, so a
single notification describing the batch would be a lie to somebody. Group by
status first, emit once per group -- the same rule block bids already follow.
"""

from libs.notifications.single_bid_context import group_single_bid_decisions


def _doc(ref, maker, date="2026-08-10"):
    return {"_id": ref, "created_by": maker, "schedule_date": date}


def test_groups_by_status_and_counts_each_group():
    updated = [
        {"reference_number": "a", "bid_status": "approved"},
        {"reference_number": "b", "bid_status": "approved"},
        {"reference_number": "c", "bid_status": "rejected"},
    ]
    docs = [_doc("a", "mukul"), _doc("b", "mukul"), _doc("c", "ashutosh")]

    groups = group_single_bid_decisions(updated, docs)

    assert groups["approved"]["bid_count"] == 2
    assert groups["approved"]["maker_usernames"] == ["mukul"]
    assert groups["rejected"]["bid_count"] == 1
    assert groups["rejected"]["maker_usernames"] == ["ashutosh"]


def test_maker_usernames_are_deduplicated_and_order_is_stable():
    updated = [
        {"reference_number": "a", "bid_status": "approved"},
        {"reference_number": "b", "bid_status": "approved"},
    ]
    docs = [_doc("a", "mukul"), _doc("b", "mukul")]

    assert group_single_bid_decisions(updated, docs)["approved"]["maker_usernames"] == [
        "mukul"
    ]


def test_ignores_references_with_no_matching_document():
    """A reference the lookup could not resolve contributes nothing rather than a
    group with an empty schedule_date, which would render as a bid for no date."""
    updated = [{"reference_number": "ghost", "bid_status": "approved"}]

    assert group_single_bid_decisions(updated, []) == {}


def test_ignores_non_decision_statuses():
    """A pending row that slipped into the batch must not be relabelled. The
    vendor's own line coerced anything not 'Approved' into 'Rejected'."""
    updated = [{"reference_number": "a", "bid_status": "pending"}]

    assert group_single_bid_decisions(updated, [_doc("a", "mukul")]) == {}


def test_empty_input_is_empty_output():
    assert group_single_bid_decisions([], []) == {}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && python -m pytest tests/notifications/test_single_bid_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'libs.notifications.single_bid_context'`

- [ ] **Step 3: Implement**

```python
# backend/libs/notifications/single_bid_context.py
"""Turn a checker's single-bid decision batch into per-status notification context.

Single-bid approval documents live in one collection PER MARKET
(dam_single_bid_data_for_approval, rtm_single_bid_data_for_approval, ...) and carry
no market_type field, so unlike block bids the market is known from the call site
and only the status needs grouping.

Pure by design: the caller does the two I/O steps (it already has the `updated`
list from the service, and fetches the documents itself), so this is testable
without Mongo or an event loop.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

# Anything else -- "pending", "pending_withdrawal", "" -- is not a decision and is
# dropped rather than coerced. Coercing is what made the vendor's own line relabel
# an untouched Pending row as a rejection.
DECISION_STATUSES = frozenset({"approved", "rejected", "approved_withdrawal"})


def group_single_bid_decisions(
    updated: Iterable[Dict[str, Any]], docs: Iterable[Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """Group decided references by status.

    `updated` must be the service's own `updated` list -- references whose
    modified_count was greater than zero -- never the raw request. Approving a bid
    that is no longer pending is a silent no-op that still returns HTTP 200, and
    notifying on it would announce a decision that never happened.

    `docs` are the approval documents for those references, in any order.
    """
    by_id = {str(doc.get("_id")): doc for doc in docs}

    groups: Dict[str, Dict[str, Any]] = {}
    for entry in updated:
        status = str(entry.get("bid_status") or "").strip().lower()
        if status not in DECISION_STATUSES:
            continue

        doc = by_id.get(str(entry.get("reference_number")))
        if doc is None:
            continue

        group = groups.setdefault(
            status,
            {"bid_count": 0, "maker_usernames": [], "schedule_date": ""},
        )
        group["bid_count"] += 1

        maker = doc.get("created_by")
        if maker and maker not in group["maker_usernames"]:
            group["maker_usernames"].append(maker)

        # First non-empty date wins. A batch spans one screen, therefore one date;
        # if that ever stops being true the count is still truthful and the date is
        # one of the real ones, which beats blanking it.
        if not group["schedule_date"] and doc.get("schedule_date"):
            group["schedule_date"] = str(doc["schedule_date"])

    return groups


def maker_usernames_for(updated: Iterable[Dict[str, Any]]) -> List[str]:
    """Deduplicated makers, order preserved. Used by the submit path, which has
    no status dimension."""
    seen: List[str] = []
    for entry in updated:
        name = entry.get("created_by")
        if name and name not in seen:
            seen.append(name)
    return seen
```

- [ ] **Step 4: Run tests** → PASS

- [ ] **Step 5: Commit**

```
feat(notifications): add single-bid decision grouping helper
```

---

## Task 4: The audience index (and the bootstrap bug hiding it)

**Files:**
- Modify: `backend/services/auth/app/core/database_indexes.py:55-91`
- Test: `backend/tests/auth/test_database_indexes.py` (create)

**Context:** this is the deferred `epms_notification_audience_perf` item. It is safe to land now
(D5): every candidate audience query filters `roles` and `is_active`, so the compound index is
correct regardless of what PTC decides about recipients. But the missing index is a *symptom* —
fact 5 — and adding one more `create_index` line below a statement that always raises would change
nothing at all.

**Interfaces:**
- Consumes: nothing.
- Produces: `auth_db.users` carries `{roles: 1, is_active: 1}` after an auth-service restart.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/auth/test_database_indexes.py
"""One failing index must not silently cancel every index after it.

Live proof this matters: auth_db.users has 813 users with email null and two
sharing admin@epms.com, so `create_index("email", unique=True)` raises
permanently. It is the SECOND statement in _create_user_indexes, whose body is
wrapped in one try/except -- so roles, login_attempts, locked_until,
mfa_enabled, sessions.session_id and password_expires_at have never existed on
any deployment of this system, and the only trace is one WARNING line.
"""

from unittest.mock import MagicMock

from pymongo.errors import OperationFailure

from services.auth.app.core.database_indexes import _create_user_indexes


def test_one_failing_index_does_not_cancel_the_rest():
    collection = MagicMock()
    calls = []

    def create_index(keys, **kwargs):
        calls.append(keys)
        if keys == "email":
            raise OperationFailure("E11000 duplicate key error")

    collection.create_index.side_effect = create_index
    db = MagicMock()
    db.__getitem__.return_value = collection

    _create_user_indexes(db)

    assert "roles" in calls, "the roles index was skipped by an earlier failure"


def test_the_audience_compound_index_is_created():
    collection = MagicMock()
    db = MagicMock()
    db.__getitem__.return_value = collection

    _create_user_indexes(db)

    created = [call.args[0] for call in collection.create_index.call_args_list]
    assert [("roles", 1), ("is_active", 1)] in created
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && python -m pytest tests/auth/test_database_indexes.py -v`
Expected: FAIL — `assert "roles" in calls` (the `email` failure aborts the function) and the
compound index does not exist yet.

> The rest of `tests/auth` fails 10 and errors 42 both before and after this change — pre-existing
> vendor rot (`epms_vendor_test_suite_rot`). Judge this task on the new file only.

- [ ] **Step 3: Isolate each index and add the compound one**

Replace the body of `_create_user_indexes` with a per-index loop:

```python
def _create_user_indexes(db):
    """Create indexes for users collection.

    Each index is attempted independently. They used to share one try/except, so
    the first OperationFailure cancelled every index below it -- and one of them
    always fails: `email` is declared unique while 813 of 816 live users have
    email null and two share admin@epms.com. The result was that the roles index
    on line 75 had never been created on any deployment, which is why notification
    audience resolution COLLSCANs the whole users collection.
    """
    users_collection = db[settings.MONGODB_AUTH_COLLECTION]

    indexes = [
        ({"keys": "username", "unique": True}),
        ({"keys": "email", "unique": True}),
        ({"keys": [("is_deleted", ASCENDING), ("is_active", ASCENDING)]}),
        ({"keys": "login_attempts"}),
        ({"keys": "locked_until"}),
        ({"keys": "roles"}),
        # Notification audience resolution:
        #   users.find({"roles": {"$in": [...]}, "is_active": True, ...})
        # The single-field `roles` index above cannot serve the is_active
        # predicate, so the scan stayed linear in TOTAL user count rather than
        # recipient count. Measured on the UAT copy: 816 docs examined -> 144.
        ({"keys": [("roles", ASCENDING), ("is_active", ASCENDING)]}),
        ({"keys": "mfa_enabled"}),
        ({"keys": "sessions.session_id"}),
        ({"keys": "password_expires_at"}),
    ]

    for spec in indexes:
        keys = spec["keys"]
        try:
            users_collection.create_index(
                keys, background=True, **{k: v for k, v in spec.items() if k != "keys"}
            )
        except OperationFailure as e:
            logger.warning("⚠️ Could not create users index %r: %s", keys, e)

    logger.debug("✅ User collection indexes created")
```

Leave `_create_role_indexes` and the others alone — they are not in scope and their first index
succeeds, so they do not have this bug. Note it in the report; do not fix it here.

- [ ] **Step 4: Run the new test** → PASS

- [ ] **Step 5: Verify against live Mongo**

```bash
# restart auth so lifespan re-runs the bootstrap, then:
docker exec epms-mongo mongosh --quiet --eval \
  'db.getSiblingDB("auth_db").users.getIndexes().map(i => i.name).join(", ")'
```
Expected: `roles_1_is_active_1` present alongside `roles_1`, `username_1`, and the rest.

```bash
docker exec epms-mongo mongosh --quiet --eval '
  db.getSiblingDB("auth_db").users.find(
    {roles: {$in: ["operator"]}, is_active: true, is_deleted: {$ne: true}}, {_id: 1}
  ).explain("executionStats").executionStats'
```
Expected: `totalDocsExamined` well below `816`, `stage: "IXSCAN"`. Record the real numbers in the
report — the memory's projection of 144 was measured before this index existed.

- [ ] **Step 6: Commit**

```
fix(auth): stop one failing index cancelling the rest; index the audience query
```

> ⚠️ This touches `services/auth/`, like the uncommitted bcrypt fix. Both belong on branches off
> `dev`, not on `feat/notifications`. Flag it in the report.

---

## Task 5: DAM single-bid events

**Files:**
- Modify: `backend/libs/notifications/events.py` (4 new registry entries + path maps)
- Modify: `backend/services/processing/app/v2/controller/new_dam_bidding_controller.py:92-140`
- Test: `backend/tests/notifications/test_single_bid_emission.py` (create)

**Interfaces:**
- Consumes: `EventType.category`/`.area` (Task 1), `group_single_bid_decisions` and
  `maker_usernames_for` (Task 3).
- Produces: registry keys `single_bid.submitted_for_approval` and `single_bid.status_changed`;
  `SINGLE_APPROVAL_SCREEN` / `SINGLE_SUBMISSION_SCREEN` maps and their
  `single_approval_screen_path()` / `single_submission_screen_path()` accessors. Tasks 8 and 9
  reuse every one of these unchanged — they add **no** new registry entries.

**Why the controller and not the service:** `new_rtm_bidding_services.py` is fenced by PTC (Global
Constraints), and Task 8 must mirror this task exactly. The controller is four thin files, already
receives `current_user`, and already sees the service's return value — which carries the `updated`
list this task must drive off (fact 4).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/notifications/test_single_bid_emission.py
"""Single-bid notifications, emitted from the market controllers.

Three rules, each of which the block-bid path had to learn the hard way:
  1. Emit once per status group -- a batch can approve some and reject others.
  2. Emit only for bids the write actually changed. Approving an already-decided
     bid is a silent no-op that still returns HTTP 200.
  3. A notification failure must never fail the bid decision.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_approval_emits_once_per_status_group(dam_controller, emitted):
    dam_controller.dam_bidding_service.submitForPreExchangeData = AsyncMock(
        return_value={
            "updated": [
                {"reference_number": "a", "bid_status": "approved"},
                {"reference_number": "b", "bid_status": "rejected"},
            ]
        }
    )
    dam_controller.dam_bidding_service.single_bid_data_for_approval_repo.find_by_filters = (
        AsyncMock(
            return_value=[
                {"_id": "a", "created_by": "mukul", "schedule_date": "2026-08-10"},
                {"_id": "b", "created_by": "mukul", "schedule_date": "2026-08-10"},
            ]
        )
    )

    await dam_controller.submitForPreExchangeData([], _user())

    keys = [call.args[0] for call in emitted.await_args_list]
    assert keys == ["single_bid.status_changed", "single_bid.status_changed"]
    statuses = sorted(call.args[1]["status_lower"] for call in emitted.await_args_list)
    assert statuses == ["approved", "rejected"]


@pytest.mark.asyncio
async def test_skipped_bids_produce_no_notification(dam_controller, emitted):
    """The lapsed-bid case: HTTP 200, nothing changed, nobody told."""
    dam_controller.dam_bidding_service.submitForPreExchangeData = AsyncMock(
        return_value={"updated": []}
    )

    await dam_controller.submitForPreExchangeData([], _user())

    emitted.assert_not_awaited()


@pytest.mark.asyncio
async def test_notification_failure_does_not_fail_the_decision(dam_controller, emitted):
    emitted.side_effect = RuntimeError("redis is down")
    dam_controller.dam_bidding_service.submitForPreExchangeData = AsyncMock(
        return_value={"updated": [{"reference_number": "a", "bid_status": "approved"}]}
    )
    dam_controller.dam_bidding_service.single_bid_data_for_approval_repo.find_by_filters = (
        AsyncMock(return_value=[{"_id": "a", "created_by": "m", "schedule_date": "2026-08-10"}])
    )

    result = await dam_controller.submitForPreExchangeData([], _user())

    assert result["data"]["updated"]  # the decision survived


@pytest.mark.asyncio
async def test_submit_for_approval_notifies_the_approvers(dam_controller, emitted):
    dam_controller.dam_bidding_service.submit_for_approval_post_data = AsyncMock(
        return_value={"result": True}
    )

    await dam_controller.submit_for_approval_post_data(
        {"schedule_date": "2026-08-10", "block_no": ["1", "2"]}, _user()
    )

    key, context, _ = emitted.await_args.args[0], emitted.await_args.args[1], None
    assert key == "single_bid.submitted_for_approval"
    assert context["market_type"] == "DAM"
    assert context["area_kind"] == "Single Bid"
```

Write the `dam_controller` and `emitted` fixtures to patch
`services.processing.app.v2.controller.new_dam_bidding_controller.notify` with an `AsyncMock` and
to construct the controller with a `MagicMock()` service, following the pre-import
`patch("pymongo.MongoClient")` convention the existing `tests/notifications` files use.

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && python -m pytest tests/notifications/test_single_bid_emission.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'notify'`

- [ ] **Step 3: Add the registry entries and path maps**

In `events.py`, beside the existing block maps:

```python
# Single-bid screens. Verified against client/src/main.jsx: the DAM approval
# screen is /dam/dam-bid-approval and RTM's is /rtm/single-bid-approval -- the
# two are NOT symmetrical, which is exactly why this is a lookup and not a
# f-string.
SINGLE_APPROVAL_SCREEN = {
    "dam": "/dam/dam-bid-approval",
    "rtm": "/rtm/single-bid-approval",
}
SINGLE_SUBMISSION_SCREEN = {
    "dam": "/dam/bid-submission-status",
    "rtm": "/rtm/bid-submission-status",
}


def single_approval_screen_path(market_type: str) -> str:
    key = approval_permission_market(market_type)
    if key not in SINGLE_APPROVAL_SCREEN:
        raise ValueError(
            f"Unknown market type: {market_type!r}. "
            f"Supported markets: DAM, GDAM, HPDAM, RTM"
        )
    return SINGLE_APPROVAL_SCREEN[key]


def single_submission_screen_path(market_type: str) -> str:
    key = approval_permission_market(market_type)
    if key not in SINGLE_SUBMISSION_SCREEN:
        raise ValueError(
            f"Unknown market type: {market_type!r}. "
            f"Supported markets: DAM, GDAM, HPDAM, RTM"
        )
    return SINGLE_SUBMISSION_SCREEN[key]
```

And the two entries. Note the permission is `{approval_market}_single_bid_approval:read` — both
`dam_single_bid_approval:read` and `rtm_single_bid_approval:read` exist in live data (fact 3), and
GDAM/HPDAM fold onto `dam` through the existing `approval_permission_market`.

```python
    "single_bid.submitted_for_approval": EventType(
        key="single_bid.submitted_for_approval",
        severity=Severity.ACTION_REQUIRED,
        title="Awaiting approval",
        body="{actor_name} submitted {bid_count} {market_type} single bid(s) for {schedule_date}.",
        audience=HasPermission("{approval_market}_single_bid_approval:read"),
        link="{approval_path}?date={schedule_date}",
        category=CATEGORY_BIDDING,
        area="{market_type} {area_kind}",
    ),
    "single_bid.status_changed": EventType(
        key="single_bid.status_changed",
        severity=Severity.INFO,
        title="Bid {status_lower}",
        body="{actor_name} {status_lower} {bid_count} {market_type} single bid(s) for {schedule_date}.",
        audience=AnyOf(
            UsernamesFromContext("maker_usernames"),
            HasPermission("{approval_market}_single_bid_approval:read"),
        ),
        link="{submission_path}?date={schedule_date}",
        category=CATEGORY_BIDDING,
        area="{market_type} {area_kind}",
    ),
```

> `area_kind` is passed by the call site rather than hardcoded as `"Single Bid"` so the label the
> user sees ("iDAM Single Bid" vs "DAM Single Bid") is a one-word change at four call sites rather
> than a registry edit. Settle the exact wording with the user before implementing — the request
> said "idam/block or whatever".

- [ ] **Step 4: Emit from the DAM controller**

```python
from libs.notifications.emitter import notify
from libs.notifications.events import (
    approval_permission_market,
    single_approval_screen_path,
    single_submission_screen_path,
)
from libs.notifications.single_bid_context import group_single_bid_decisions

MARKET = "DAM"
AREA_KIND = "Single Bid"
```

In `submit_for_approval_post_data`, after `data = await self.dam_bidding_service...` and before
`return`:

```python
            # Tell everyone who can see the DAM single-bid approval screen. Guarded
            # so a Redis or audience failure can never fail bids already persisted.
            try:
                await notify(
                    "single_bid.submitted_for_approval",
                    {
                        "market_type": MARKET,
                        "area_kind": AREA_KIND,
                        "approval_market": approval_permission_market(MARKET),
                        "approval_path": single_approval_screen_path(MARKET),
                        "bid_count": len(request.get("block_no") or []),
                        "schedule_date": request.get("schedule_date", ""),
                    },
                    actor={
                        "user_id": current_user.user_id,
                        "username": current_user.username,
                    },
                )
            except Exception:
                logger.exception(
                    "Notification emit failed for DAM single bid submission"
                )
```

> **Verify `bid_count` and `schedule_date` against a real request body before writing this.** Log
> one `submit-for-approval` payload from the running stack and confirm the field names; the block
> path was mislabelled for exactly this reason. If `schedule_date` is absent from the request,
> read it back from the inserted documents rather than rendering an empty date.

In `submitForPreExchangeData`, after the service call:

```python
            # Drive off `updated`, never off `request`: the service returns only the
            # references whose modified_count was greater than zero. Approving a bid
            # that is no longer pending is a silent no-op that still returns HTTP
            # 200, and notifying on it would announce a decision that never happened.
            try:
                updated = (data or {}).get("updated") or []
                if updated:
                    docs = await self.dam_bidding_service.single_bid_data_for_approval_repo.find_by_filters(
                        {
                            "_id": {
                                "$in": [
                                    ObjectId(e["reference_number"])
                                    for e in updated
                                    if ObjectId.is_valid(str(e.get("reference_number")))
                                ]
                            }
                        }
                    )
                    for status_lower, group in group_single_bid_decisions(
                        updated, docs
                    ).items():
                        await notify(
                            "single_bid.status_changed",
                            {
                                "market_type": MARKET,
                                "area_kind": AREA_KIND,
                                "approval_market": approval_permission_market(MARKET),
                                "submission_path": single_submission_screen_path(MARKET),
                                "status_lower": status_lower,
                                "maker_usernames": group["maker_usernames"],
                                "bid_count": group["bid_count"],
                                "schedule_date": group["schedule_date"],
                            },
                            actor={
                                "user_id": current_user.user_id,
                                "username": current_user.username,
                            },
                        )
            except Exception:
                logger.exception(
                    "Notification emit failed for DAM single bid decision"
                )
```

- [ ] **Step 5: Run tests** → PASS. Then `python -m pytest tests/notifications -v` → all green.

- [ ] **Step 6: Commit**

```
feat(notifications): notify on DAM single bid submit and decision
```

---

## Task 6: The notification row — pure formatters

**Files:**
- Modify: `client/src/utils/notificationView.js`
- Test: `client/src/utils/notificationView.test.js` (extend)

**Interfaces:**
- Consumes: documents carrying `category`/`area` (Task 1) and legacy documents without them.
- Produces: `formatNotificationTimestamp(iso)`, `notificationArea(item)`, `notificationActor(item)`,
  `notificationType(item)`. Tasks 7 and 8 render these.

- [ ] **Step 1: Write the failing test**

```js
// appended to client/src/utils/notificationView.test.js
test("formatNotificationTimestamp renders date and time together", () => {
  assert.equal(
    formatNotificationTimestamp("2026-08-09T14:05:00"),
    "09 Aug 2026, 02:05 PM"
  );
});

test("formatNotificationTimestamp survives a missing or unparseable date", () => {
  assert.equal(formatNotificationTimestamp(undefined), "—");
  assert.equal(formatNotificationTimestamp("not a date"), "—");
});

test("notificationArea falls back for documents written before areas existed", () => {
  assert.equal(notificationArea({ area: "DAM Block Bid" }), "DAM Block Bid");
  assert.equal(notificationArea({}), "—");
});

test("notificationActor reads the actor name, not the recipient", () => {
  assert.equal(notificationActor({ actor: { name: "mukul" } }), "mukul");
  assert.equal(notificationActor({}), "System");
});

test("notificationType prefers the server title and never invents one", () => {
  assert.equal(notificationType({ title: "Awaiting approval" }), "Awaiting approval");
  assert.equal(notificationType({}), "Notification");
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd client && node --test src/utils/notificationView.test.js`
Expected: FAIL — `formatNotificationTimestamp is not a function`

- [ ] **Step 3: Implement**

```js
/**
 * `created_at` is serialised from Python's `datetime.now()` -- naive local time
 * with no zone suffix. `new Date("2026-08-09T14:05:00")` parses that as LOCAL
 * time, which is right as long as the server and the browser share a timezone.
 * In PROD both are IST, so they do. If EPMS ever serves a second timezone this
 * is the line to fix, and the fix is server-side: emit an aware UTC timestamp.
 */
const TIMESTAMP_FORMAT = {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: true,
};

export function formatNotificationTimestamp(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date
    .toLocaleString("en-GB", TIMESTAMP_FORMAT)
    .replace(",", ",")
    .toUpperCase()
    .replace(/(\d{2}) ([A-Z]{3}) (\d{4})/, (m, d, mo, y) => `${d} ${mo[0]}${mo.slice(1).toLowerCase()} ${y}`);
}

/** Documents written before this wave have no area. Blank beats a guess. */
export function notificationArea(item) {
  return item?.area || "—";
}

/** Who did the thing -- never the recipient. */
export function notificationActor(item) {
  return item?.actor?.name || "System";
}

/** The server's own title is the type; the client never derives one from event_key. */
export function notificationType(item) {
  return item?.title || "Notification";
}
```

> Simplify the `formatNotificationTimestamp` implementation to whatever makes the asserted string
> exact — the assertion is the contract, not this body. If matching "09 Aug 2026, 02:05 PM" through
> `toLocaleString` proves fiddly, build it from `getDate`/`getMonth`/`getHours` explicitly.

- [ ] **Step 4: Run tests** → PASS

- [ ] **Step 5: Commit**

```
feat(notifications): add row formatters for the notification list
```

---

## Task 7: `/notifications` — server-backed list with category tabs

**Files:**
- Create: `client/src/hooks/useNotificationList.js`, `client/src/hooks/useNotificationList.test.js`
- Create: `client/src/components/notifications/NotificationRow.jsx`
- Modify: `client/src/pages/Notifications.jsx`, `client/src/config/apiEndpoints.js`

**Interfaces:**
- Consumes: `GET /api/v2/notifications?limit&skip&category` (Task 2), the formatters (Task 6).
- Produces: the finished page. Nothing depends on it.

**The problem this fixes:** `Notifications.jsx` renders `state.notifications.items`, which is
whatever the SSE `sync` frame delivered — `SYNC_LIMIT = 50` rows, no pagination, no server filter.
Adding tabs on top of that would show an empty "Bidding" tab to a user whose bidding notifications
are the 51st row onward. The page must fetch.

- [ ] **Step 1: Write the failing test**

```js
// client/src/hooks/useNotificationList.test.js
test("fetches the selected category from the server, not the Redux slice", async () => { /* ... */ });
test("appends the next page instead of replacing", async () => { /* ... */ });
test("a live arrival for the active category appears at the top without a refetch", async () => { /* ... */ });
test("a live arrival for another category is not injected into the current tab", async () => { /* ... */ });
test("a failed fetch surfaces an error and does not blank the list", async () => { /* ... */ });
```

Fill each body against the mocked `apiClient` the existing client tests use.

- [ ] **Step 2: Run it and watch it fail**

Run: `cd client && node --test src/hooks/useNotificationList.test.js`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the hook**

`useNotificationList(category)` returns `{ items, isLoading, error, hasMore, loadMore, markOpen }`.
It fetches `limit=50, skip=items.length, category`, appends on `loadMore`, sets `hasMore` from
whether the page came back full, and subscribes to `state.notifications.liveArrivals` so a live
frame for the active category is prepended without a refetch (dispatch `liveArrivalsConsumed` after
draining, exactly as `NotificationToast` does).

- [ ] **Step 4: Implement `NotificationRow.jsx`**

Six fields, in the order the user asked for: **Type · Date & time · Description · Area · User ·
Go to page**. Unread stays bold and tinted, matching the bell. The "Go to page" control is a real
`<Link to={item.link}>` styled as `btn btn-sm btn-primary-light` — a sibling of the row's own link,
never nested inside it (nesting anchors is invalid HTML and breaks the inner control; the bell
already learned this). Both the row and the button call `markOpen(item)`.

Render as a responsive table on `md` and up and as stacked cards below it — six columns on a phone
is unreadable, and the app's other list screens already use that pattern.

- [ ] **Step 5: Rewrite `Notifications.jsx`**

Tab bar (`nav nav-tabs`) with **All** and **Bidding**, driven by a `category` state that feeds the
hook. Read the active tab from `?category=` in the URL so a notification link can deep-link into a
tab and a refresh keeps it. Empty state per tab: "No bidding notifications." Keep the existing
`markedRead` optimistic dispatch.

Add tabs from a single `CATEGORIES` array so the next category is one line.

- [ ] **Step 6: Add query-param support in `apiEndpoints.js`**

```js
    notifications: joinPath(processingV2Base, "notifications"),
```
stays; the hook builds params via axios `{ params: { limit, skip, category } }`. No endpoint change
needed — confirm this before editing the file, and if so, delete this step from the report.

- [ ] **Step 7: Run the full client suite**

Run: `cd client && node --test 'src/**/*.test.js'`
Expected: PASS, including the 64 existing notification tests.

- [ ] **Step 8: Commit**

```
feat(notifications): server-backed notifications page with category tabs
```

---

## Task 8: RTM single-bid events

**Files:**
- Modify: `backend/services/processing/app/v2/controller/new_rtm_bidding_controller.py:105-180`
- Test: `backend/tests/notifications/test_single_bid_emission.py` (extend)

**Interfaces:**
- Consumes: everything Task 5 produced. **Adds no new registry entries** — `single_bid.*` is
  market-templated.

- [ ] **Step 1: Confirm the fence with the user before touching anything**

PTC's instruction covers `new_rtm_bidding_services.py`. This task edits
`new_rtm_bidding_controller.py`, a different file. Say so explicitly in the report and get a yes.

- [ ] **Step 2: Write the failing test** — the same four cases as Task 5 against the RTM
      controller, asserting `market_type == "RTM"` and
      `link` starting `/rtm/single-bid-approval`.

- [ ] **Step 3: Run it and watch it fail.**

- [ ] **Step 4: Apply the Task 5 diff with these substitutions**

| Task 5 | Task 8 |
|---|---|
| `new_dam_bidding_controller.py` | `new_rtm_bidding_controller.py` |
| `MARKET = "DAM"` | `MARKET = "RTM"` |
| `self.dam_bidding_service` | the RTM controller's own service attribute — **read the file, do not assume the name** |
| log message `"DAM single bid …"` | `"RTM single bid …"` |

Everything else — imports, the guarded `try/except`, driving off `updated`, the
`group_single_bid_decisions` loop — is character-identical.

- [ ] **Step 5: Verify RTM's service returns the same `updated` shape.** DAM's
      `submitForPreExchangeData` returns `{result, message, data, summary, updated, skipped, errors}`
      (`new_dam_bidding_services.py:3437`). Read
      `new_rtm_bidding_services.py:2549` and confirm. **If RTM returns a different shape, do not
      adapt the notification to guess** — report it and stop; emitting from the wrong list
      announces decisions that did not happen.

- [ ] **Step 6: Run tests** → PASS

- [ ] **Step 7: Commit**

```
feat(notifications): notify on RTM single bid submit and decision
```

---

## Task 9 (optional, gated on D2): GDAM and HPDAM single-bid events

Same substitution table as Task 8 against `new_gdam_bidding_controller.py` (`MARKET = "GDAM"`) and
`new_hpdam_bidding_controller.py` (`MARKET = "HPDAM"`). No registry changes:
`approval_permission_market()` already folds both onto `dam`, so they notify DAM approvers and link
to `/dam/dam-bid-approval` — which is correct, because no `gdam_*` or `hpdam_*` approval permission
exists in live data (fact 3). Area renders "GDAM Single Bid" / "HPDAM Single Bid".

Two markets, two files, one test each. Skip entirely if PTC wants DAM and RTM only.

---

## Task 10: End-to-end verification

Not a code task. Green unit tests are not evidence the feature works — drive the real UI.

- [ ] **Step 1: Seed what the local DB is missing.** `rtm_single_bid_data_for_approval` does not
      exist locally (fact 1), so RTM single-bid notifications cannot fire. Create pending RTM
      single bids through the UI, or seed the collection from the DAM one with the market's own
      shape. Record which route was used.

- [ ] **Step 2: Two-browser run, per market.** Reuse the harness in
      `epms_notifications_test_harness` (localpatch PYTHONPATH trap, CORS on port 3000, Redis on
      6380, the real selectors). For each of DAM single, RTM single, DAM block, RTM block:
      user A submits, user B's bell increments live without a refresh, the toast fires, the row on
      `/notifications` shows all six fields, and **"Go to page" lands on the right approval screen
      with the right date**.

- [ ] **Step 3: The lapsed-bid case.** Approve a bid twice from two tabs. The second approval must
      produce HTTP 200 **and no notification**. This is the one behaviour most likely to regress
      silently.

- [ ] **Step 4: Mixed batch.** Approve some and reject others in one submission. Expect exactly two
      notifications with truthful counts, not one mislabelled batch.

- [ ] **Step 5: Tabs.** With more than 50 notifications on the account, confirm the Bidding tab
      shows bidding rows beyond the first 50 — the specific failure that server-side filtering
      exists to prevent.

- [ ] **Step 6: Legacy rows.** Confirm pre-wave documents (no `category`/`area`) still render, show
      "—" for area, and are reachable under **All** rather than vanishing.

- [ ] **Step 7: Re-run the 60-user load test** from `project_epms_notifications`. The audience index
      should improve emit latency; the row changes should not affect it. Record before/after.

---

## Implementation deltas (recorded 2026-08-09, after execution)

Four things changed once the code was written. Each is a correction to this plan, not a
deviation from the goal.

1. **`area_kind` was dropped.** The plan templated area as `"{market_name} {area_kind}"` with the
   kind passed from the call site. Unnecessary: block and single bids are separate registry
   entries, so each already knows its own kind. Area is now literally `"{market_name} Block Bid"`
   and `"{market_name} Single Bid"`. One fewer context key at every call site.

2. **`market_display_name()` was added** and DAM renders as **iDAM**. The sidebar
   (`sidemenu.jsx:321-337`) labels every DAM screen "iDAM ...". A row saying "DAM" would send the
   reader hunting for a menu entry that does not exist under that name. Only DAM diverges.

3. **The plan's assumed request shape was WRONG, and checking saved it.** Task 5 Step 4 assumed
   `request["schedule_date"]` and `request["block_no"]`. Neither exists. The real submit payload
   carries `time_block_range` (one entry per block) and `time_block_dates` (`"DD-MM-YYYY_<block>"`,
   built at `useDAMSingleBid.js:2427` from a `DD-MM-YYYY` formatter at `:2328`). The service
   computes the date internally and returns only `{"status", "code", "message"}`, so it has to be
   re-derived. `submit_batch_schedule_date()` does that, accepting `DD-MM-YYYY` first and
   `YYYY-MM-DD` second, and returning `""` rather than raising. A blank date skips the
   notification.

4. **Emission moved into a shared module, `libs/notifications/single_bid_emit.py`.** The plan put
   ~30 lines of guarded emit code in each of four controllers. That is four copies of the rules
   that matter, and — worse — four controller test suites that each have to import a module whose
   service singleton opens Mongo at import time. Instead the controllers each call two functions
   (`notify_single_bid_submitted`, `notify_single_bid_decisions`) and the logic is tested once, in
   16 tests, with no controller import at all. Tasks 8 and 9 collapsed into a 6-line edit per file.

**Vendor defects found while wiring, not fixed:**

- `new_dam_bidding_services.py`, `new_gdam_bidding_services.py` and `new_hpdam_bidding_services.py`
  each define **two `__init__` methods on the same class**. Python keeps the last one, so the first
  is dead code — and in GDAM's case the dead one points `single_bid_data_for_approval_repo` at the
  **DAM** collection while the live one points at GDAM's. It happens to work. It is one edit away
  from silently not working. RTM has only one `__init__`.
- The vendor's own fallback in `get_block_date_data` reads a `YYYY-MM-DD` segment out of
  `time_block_range` and then parses it with `"%d-%m-%Y"`, which raises. Unreachable today because
  the client always sends `time_block_dates`.

**Live verification of emission** (real registry, real audience against `auth_db`, real Mongo
write, real Redis publish; all 506 documents deleted afterwards, 0 leftover):

| event | area | link | recipients |
|---|---|---|---|
| DAM submitted | iDAM Single Bid | `/dam/dam-bid-approval?date=…` | 116 |
| RTM submitted | RTM Single Bid | `/rtm/single-bid-approval?date=…` | **42** |
| GDAM submitted | GDAM Single Bid | `/dam/dam-bid-approval?date=…` | 116 |
| DAM approved | iDAM Single Bid | `/dam/bid-submission-status?date=…` | 116 |
| DAM rejected | iDAM Single Bid | `/dam/bid-submission-status?date=…` | 116 |

506 documents written, 506 Redis frames published — one per recipient, as designed. RTM's 42 is
correct, not a bug: the `user` role holds `dam_single_bid_approval:read` but not the RTM one.

## Self-review notes

- **Spec coverage.** Every item the user listed maps to a task: type/date/description/area/link
  button/user name → Tasks 1, 6, 7; the `roles:1` index → Task 4; Bidding + single DAM + DAM block
  + single RTM + RTM block → Tasks 5, 8 (block already shipped); category tabs → Tasks 2, 7.
- **Known gap, deliberate.** `bid_count` and `schedule_date` on the *submit* path (Task 5, Step 4)
  are read from the request body, whose field names are asserted from the route signature rather
  than from a captured payload. Step 4 says to verify against a live request before writing the
  code. This is the one place in the plan where a task begins with a check instead of a test.
- **Type consistency.** `single_approval_screen_path` / `single_submission_screen_path` /
  `group_single_bid_decisions` / `maker_usernames_for` / `formatNotificationTimestamp` /
  `notificationArea` / `notificationActor` / `notificationType` / `useNotificationList` are each
  defined once and used under exactly that name.
- **Branching.** Task 4 touches `services/auth/` and belongs on its own branch off `dev` with the
  bcrypt fix, not on `feat/notifications`. Every other task is notifications work.
