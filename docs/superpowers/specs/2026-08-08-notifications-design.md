# Live notifications (SSE) — EPMS

Date: 2026-08-08
Status: design agreed, spec under review

## Problem

EPMS has no notification mechanism of any kind. There is no WebSocket, SSE, or polling
path in either repository — verified by grep across `backend/**/*.py` and `client/src`.

The bell UI already exists and is fully styled, but its data is dead template content
shipped with the Bootstrap admin theme:

```jsx
// client/src/components/common/header/Header.jsx:125-172
const initialNotifications = [
  { id: 1, icon: "ti-gift", text1: "Your Order Has Been Shipped", ... },
  { id: 2, icon: "ti-discount-2", text1: "Discount Available", ... },
];
```

The dropdown markup (`Header.jsx:191-296`), the unread badge, and the empty state are all
present and usable. Two links point at `/pages/notifications/` (`Header.jsx:249,280`),
a route that does not exist and currently 404s.

Consequently a checker has no way to learn that a maker submitted bids for approval other
than navigating to the approval screen and looking. On a trading desk working against
market gate-closure times, that is the wrong interaction.

## Scope

Phase 1 builds **the delivery pipe, not the event catalogue**. Which business actions
raise notifications is deliberately deferred; the design makes adding one a single
registry entry plus a single `notify()` call, not a refactor.

The two motivating examples, retained as the shape the pipe must support:

- "2 pending approved bids" — a durable, countable, actionable item surviving refresh
- "popup when another user places a bid while I am on the page" — a live push

## Architecture

### New shared library — `backend/libs/notifications/`

Placed in `libs/` rather than inside a service so that any of the four services can emit
later without an HTTP hop.

| File | Responsibility |
|---|---|
| `events.py` | The registry. One entry per event type: key, title/body template, audience rule, severity, deep link. The only file that changes when an event is added. |
| `audience.py` | Resolves an audience rule to concrete `user_id`s against auth_db RBAC. Isolated so it is unit-testable without emitting. |
| `emitter.py` | `await notify(event_key, context, actor)` — resolve audience, write N docs, publish to Redis. The single entry point callers use. |
| `repository.py` | Mongo access, matching the shape of existing `libs/repository/*_repository.py` files. That directory has no base class; each repository is standalone, so this one is too. |
| `models.py` | Pydantic `Notification`. |

### Host: processing service, port 8003

Every bidding event already originates here — `submit-for-approval` exists in 8 routers
(`new_dam_bidding_routes.py`, `new_gdam_bidding_routes.py`, `new_hpdam_bidding_routes.py`,
`new_rtm_bidding_routes.py`, `rtm_bidding_routes.py`, `dam_bidding_routes.py`,
`block_bid_routes.py`, `dayaheadmarket_routes.py`).

The new router registers alongside the existing ~25 `v2_app.include_router(...)` calls in
`services/processing/app/main.py:314-336`, inheriting the `/api/v2` prefix already in
place. No new systemd unit, no new port, no new `VITE_` variable.

### Fan-out via Redis pub/sub, from day one

The emitter publishes to channel `epms:notifications`. Every process hosting streams
subscribes; each open connection filters for its own `user_id` via a
`dict[user_id, set[connection]]` registry, so dispatch is O(1) per event.

This is deliberate rather than an in-process dictionary. `run_services.py:273-280` starts
uvicorn with **no `--workers` flag**, so there is exactly one worker per service today and
an in-process registry would work. The moment anyone adds `--workers 2`, half of all users
would silently stop receiving notifications with no error raised anywhere — the same
failure shape as the Motor event-loop misbind: correct by luck, silent when wrong.

Redis pub/sub costs roughly 40 lines and removes the trap. It also means relocating the
stream to its own service later is a `main.py` and a deployment change, not a rewrite.

`libs/cache/redis_cache.py:9` imports the **synchronous** `redis` client, which cannot be
awaited inside an SSE loop. The new module uses `redis.asyncio`, from the same already
installed package. No new dependency.

## Data model

### Collection `epms.notifications` — one document per recipient per event

```js
{
  _id:        ObjectId,
  user_id:    "…",                          // recipient; fan-out on write
  event_key:  "bid.submitted_for_approval", // registry lookup key
  title:      "DAM block bid awaiting approval",
  body:       "A. Kumar submitted 3 DAM bids for 2026-08-09.",
  payload:    { market_type: "DAM", schedule_date: "2026-08-09", bid_count: 3 },
  link:       "/dam/dam-block-bid-approve?date=2026-08-09",
  actor:      { user_id: "…", name: "A. Kumar" },
  severity:   "action_required",            // info | action_required | error
  read_at:    null,                         // null means unread
  created_at: ISODate("…"),
}
```

`title` and `body` are rendered at emit time and stored, not computed on read. This costs
a few bytes per document and guarantees that editing a template later cannot silently
rewrite what a user was historically told — which matters in a maker-checker trail.

### Indexes

```
{ user_id: 1, created_at: -1 }                    // bell list
{ user_id: 1, read_at: 1 }                        // unread count and sync frame
{ created_at: 1 }  expireAfterSeconds: 7776000    // 90-day TTL
```

### Emit granularity: action level, never row level

A maker submitting 96 blocks produces **one** notification carrying `bid_count: 96`, not
96 documents. Stated explicitly because the natural first implementation of any new event
fans out per row and floods the bell.

## Event registry

```python
# libs/notifications/events.py
REGISTRY = {
    "bid.submitted_for_approval": EventType(
        severity = Severity.ACTION_REQUIRED,
        title    = "{market_type} block bid awaiting approval",
        body     = "{actor_name} submitted {bid_count} {market_type} bid(s) for {schedule_date}.",
        audience = HasPermission("{market_type_lower}_block_bid:write"),
        link     = "/dam/dam-block-bid-approve?date={schedule_date}",
    ),
}
```

Audience rules interpolate from the same context dict as the title, body and link
templates, so one entry covers DAM, GDAM and HPDAM rather than needing three.

The entry above is illustrative. The agreed catalogue is decided separately.

### Audience rules

| Rule | Resolves to |
|---|---|
| `HasPermission("dam_block_bid:write")` | users whose role grants that permission, via `menu_role.permissions` → `role_id` → users |
| `SpecificUsers([...])` | e.g. notifying the maker of an approve/reject outcome |
| `Everyone()` | system-wide operational events, later |

RBAC shape this relies on, confirmed in
`services/auth/app/models/menu_role.py:6-13`:

```python
class MenuRoleInDB(BaseModel):
    menu_id: str
    role_id: str
    permissions: List[str] = ["read"]
```

with permission strings drawn from `libs/core/authz/permissions_catalog.py`
(`dam_block_bid:read|write`, `rtm_block_bid:read|write`, and siblings at lines 66-99).

**The emitter always subtracts the actor.** No user is notified of their own action.

## API surface

All under the existing `/api/v2` prefix on 8003:

```
POST /api/v2/notifications/ticket        mint a 30s single-use stream ticket
GET  /api/v2/notifications/stream        SSE, ?ticket=…
GET  /api/v2/notifications               paginated list for bell and page
GET  /api/v2/notifications/unread-count  badge; also the Redis-down fallback
POST /api/v2/notifications/{id}/read
POST /api/v2/notifications/read-all
```

### Stream authentication

`EventSource` cannot set an `Authorization` header, and the client stores tokens in
sessionStorage with no cookie auth anywhere (`client/src/utils/authTokens.js:63,83`).

The client therefore POSTs to `/notifications/ticket` with its normal Bearer JWT, using
the same `Depends(get_user_context)` every processing route already uses, and receives a
random single-use ticket held in Redis with a 30-second TTL:

```python
ticket = secrets.token_urlsafe(32)
await redis.setex(f"notif:ticket:{ticket}", 30, json.dumps({
    "user_id": user_context["user_id"],
    "token":   token,          # retained for the revocation re-check below
    "exp":     user_context["exp"],
}))
```

It then opens `EventSource('/api/v2/notifications/stream?ticket=…')`. The server consumes
the ticket atomically on connect, so a replayed ticket fails:

```python
raw = await redis.getdel(f"notif:ticket:{ticket}")   # GETDEL requires Redis 6.2+
if not raw:
    raise HTTPException(401)                          # expired, or already used
```

The deployed Redis version needs confirming; below 6.2 this becomes a Lua script or a
`MULTI` block, since a non-atomic `GET` then `DEL` reintroduces the replay window.

The value that reaches nginx access logs is therefore already dead by the time anyone
reads them, and is not the access token. Introducing cookie auth was rejected: it would
mean a second auth mechanism alongside sessionStorage, `credentials: true` CORS across
four origins, SameSite decisions, and fresh CSRF surface — a large blast radius for one
feature. Putting the raw JWT in the query string was rejected outright; this codebase
already carries an open credential-exposure finding with the client.

### Reconnect

On connect the server emits one `event: sync` frame carrying the latest 50 notifications
and the unread count, then streams live `event: notification` frames.

Client rule: `sync` **replaces** bell state; `notification` **prepends and toasts**.

This supersedes a `Last-Event-ID` cursor. It gives the same "nothing lost" guarantee with
no sequence counter and no ObjectId-ordering edge case across processes, and the bell
self-corrects after every reconnect — deploy, laptop sleep, and network change are all
handled by the same path.

The sync frame carries the latest 50 notifications regardless of read state, so the bell
shows recent history rather than only outstanding items, plus a separately computed
`unreadCount`. Because the count is computed independently of the 50, a user with more
than 50 unread still sees an accurate badge; the overflow is reachable through the
paginated `/notifications` page.

### Heartbeat

The server writes an SSE comment (`: ping\n\n`) every 25 seconds. Without it, nginx
`proxy_read_timeout` reaps an idle stream every 60 seconds and the feature appears broken
precisely during the quiet periods it exists to cover.

## Client

No new `VITE_` variable — `client/src/config/apiEndpoints.js:43` already resolves
`VITE_API_BASE_URL` to processing `/api/v2`.

| File | Change |
|---|---|
| `src/config/apiEndpoints.js` | add the six notification endpoints |
| `src/store/slices/notificationsSlice.js` | new — `items[]`, `unreadCount`, `connectionState` |
| `src/hooks/useNotificationStream.js` | new — EventSource lifecycle, ticket minting, watchdog, backoff |
| `src/workers/notificationStream.shared.js` | new — SharedWorker owning the single connection |
| `src/components/common/header/Header.jsx` | delete the dead array at `:125-172`; wire the existing dropdown at `:191-296` to the slice |
| `src/components/notifications/NotificationToast.jsx` | new — toast on live frames only |
| `src/pages/Notifications.jsx` + route | new — the target of the existing links at `Header.jsx:249,280` |

### One stream per browser, not per tab

HTTP/1.1 permits 6 connections per origin. An SSE stream holds one open permanently, so a
trader with 6 EPMS tabs would exhaust the budget and the sixth tab's ordinary API calls to
8003 would hang with no error surfaced.

A `SharedWorker` owns the single `EventSource` and rebroadcasts frames to sibling tabs
over `BroadcastChannel`. Roughly 60 lines, no server change, and it does not depend on the
vendor enabling TLS or HTTP/2. It also divides server-side connection count by the tab
multiplier, pushing the fd and nginx ceilings further out at no cost.

Tabs fall back to a direct `EventSource` where `SharedWorker` is unavailable.

### Lifecycle

```
login  →  POST /notifications/ticket (Bearer JWT)  →  EventSource(stream?ticket=…)
       →  event: sync          → replace bell state
       →  event: notification  → prepend + toast
       →  : ping (every 25s)   → reset watchdog
logout →  close()
```

**Half-open sockets.** `EventSource.onerror` does not fire when a socket dies silently —
a closed laptop lid or a dropped VPN. The client runs a 45-second watchdog against the
25-second heartbeat and forces a reconnect. Without it, the bell looks alive and is dead.

**Token expiry mid-stream.** Access tokens last 30 minutes; a stream outlives that. The
server caps stream lifetime at the JWT's remaining `exp` and closes cleanly; the client
mints a fresh ticket with a refreshed token and reconnects.

**Revocation mid-stream.** The `exp` cap handles expiry but *not* logout. A user who logs
out two minutes into a 30-minute token would otherwise keep receiving notifications for
the remaining 28. The stream therefore re-checks the blacklist on every heartbeat, using
the retained token from the ticket payload:

```python
# libs/core/jwt_validator.py:209-212, via the async client rather than the sync one
if await redis.sismember("jwt_blacklist", token):
    break   # close the stream
```

Revocation then takes effect within 25 seconds, at a cost of one `SISMEMBER` per
connection per heartbeat.

Two pre-existing properties of that blacklist are inherited rather than introduced here,
and are worth raising separately. `jwt_blacklist` is a plain Redis set whose members carry
no individual TTL, so it grows without bound. And `is_token_blacklisted` returns `False`
when Redis is unreachable (`jwt_validator.py:213-214`), meaning revocation **fails open** —
during a Redis outage, revoked tokens are treated as valid. That is true of every
authenticated endpoint today, not only this one.

## Failure modes

**Emitting must never fail the business request.**

```python
try:
    await notify("bid.submitted_for_approval", ctx, actor)
except Exception:
    logger.exception("notification emit failed")   # swallow, continue
```

This is the one place in the design where swallowing an exception is correct, and it is
the exact opposite of the existing `submit-for-approval` defect where a genuine *failure*
is swallowed into an HTTP 200 and the trader believes the bid landed. Here the business
action truly succeeded and only the notification was lost.

| Failure | Behaviour |
|---|---|
| Redis down | Documents still written to Mongo. No live push. Client falls back to polling `/unread-count` every 30s; bell stays correct, popups stop being instant. |
| Mongo write fails | Logged. Business request unaffected. Notification lost. |
| Ticket expired or reused | 401 → client mints once more, then exponential backoff |
| Redis unreachable during a heartbeat blacklist check | Fails open, per existing `jwt_validator.py:213-214`. Stream stays up; a revoked token is not detected until Redis returns. |
| Processing restart | `sync` frame on reconnect rebuilds state |
| Audience query fails | Logged. No notification. Never blocks. |

## Decisions

| Decision | Chosen | Why |
|---|---|---|
| Persistence | Durable log plus live push | The normal case is a checker away from their desk when a maker submits. An ephemeral design loses that event permanently. |
| Targeting | Role/permission-derived, fan-out on write | Reuses existing RBAC, no new subscription concept, per-user read state is trivial. Explicit recipient lists were rejected because each of the emit sites would re-implement "who are the checkers" and drift silently. |
| Transport | SSE | Nothing needs to travel browser→server over the socket. Plain HTTP, no protocol upgrade, native auto-reconnect. WebSocket carries the same auth constraint plus a hand-written reconnect path and an nginx `Upgrade` block. |
| Host | Processing, 8003 | Fewest deployment units on a box we do not control. A separate service fixes only 1 of the 4 scaling walls (see below) and none of the config-level ones. |
| Stream auth | Redis single-use ticket, consumed with `GETDEL` | Bounded log exposure without introducing a second auth mechanism. What reaches the logs is opaque and already dead. |
| Revocation | Blacklist re-check on every heartbeat | The `exp` cap alone leaves a logged-out user receiving notifications for up to 30 minutes. Re-checking bounds that to 25 seconds for one `SISMEMBER` per connection per heartbeat. |
| Retention | 90 days TTL | Covers a trading quarter and any month-end dispute. `audit_db` remains the authoritative trail; this collection is not a substitute. |
| Collapsing | None — one per action | Each submission is a distinct decision for a checker. Collapsing makes an already-read item silently unread and destroys "when was I told". |
| Multi-tab | SharedWorker | The 6-tab limit triggers at realistic usage, and its failure mode — a tab hanging with no error — is very hard to diagnose from a user report. |

## Deployment requirements

None of these live in either repository; nginx, systemd, and PM2 configuration is all
server-side and vendor-controlled. They must go in the deployment ask.

```nginx
location /api/v2/notifications/stream {
    proxy_pass http://127.0.0.1:8003;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;          # without this the feature is entirely broken
    proxy_cache off;
    gzip off;
    proxy_read_timeout 3600s;     # the 60s default kills the stream
    chunked_transfer_encoding off;
}
```

```nginx
worker_rlimit_nofile 65535;
events { worker_connections 8192; }
```

```ini
# processing systemd unit
LimitNOFILE=65535
```

`proxy_buffering off` is the single most important line. With buffering enabled nginx
holds the response until its buffer fills: the browser receives nothing, the connection
appears healthy, and the feature looks completely dead. No unit test will catch it.

## Performance and scaling

| Concurrent SSE connections | Assessment |
|---|---|
| ~100 (50 traders × 2 tabs) | Trivial. Well inside defaults. |
| ~750–900 | First hard wall with default configuration. |
| ~5,000–10,000 | Reachable per worker after the tuning above. Estimate, not a measurement. |
| Beyond | `--workers N`. Already safe: every worker subscribes to Redis, so no sticky sessions. |

Four walls, in the order they are hit:

1. **File descriptors, ~900.** Ubuntu systemd units default to `LimitNOFILE=1024`; each
   stream costs one fd in uvicorn, less what the Mongo and Redis pools already hold.
2. **nginx `worker_connections`, ~768.** Ubuntu ships 768 per worker and a proxied stream
   costs two — one client-side, one upstream.
3. **Event loop contention.** Pre-existing and unrelated to notifications: pandas/numpy
   run inside `async def` handlers (`dam_scheduling_service.py:5,8`,
   `block_bid_services.py:8`, `rtm_scheduling_snapshot_services.py:12`,
   `master_plants_services.py:5,8`, `graph_services.py:9`), and
   `dashboard_repository.py:12,41` instantiates the synchronous `RedisCache` in an async
   path. 235 async handlers against 22 sync ones in the v2 routers. While the loop is
   blocked, pushes queue. This degrades **latency, not correctness** — a 25-second
   heartbeat against a 60-second timeout leaves roughly 35 seconds of headroom, so a
   3-second aggregation only means a 3-second-late popup.
4. **Browser tab limit, 6.** Addressed by the SharedWorker above.

Walls 1 and 2 are configuration and should be applied regardless. Wall 3 is pre-existing
and raised separately. Wall 4 is handled in this design.

Unknowns that determine the real ceiling, none of which are in either repository: the EC2
instance type and core count, the current nginx configuration, whether TLS and HTTP/2 are
enabled, actual concurrent user count, and per-connection memory in this application. The
5,000–10,000 figure above is an estimate from FastAPI/Starlette norms and needs a load
test on the real box to become a number.

## Out of scope — raise separately with PTC

**No `:approve` permission exists.** `libs/core/authz/permissions_catalog.py:66-99`
defines `dam_block_bid:read|write`, `rtm_block_bid:read|write` and siblings, but grep
finds no approval permission anywhere in the catalogue. RBAC therefore cannot distinguish
a maker from a checker — both hold the same `*_block_bid:write`. "Notify the checkers"
can only mean *everyone holding write on that market, minus the actor*.

That is workable and matches how the screen already behaves, but it is a genuine gap in a
maker-checker control: nothing in the permission model prevents a maker approving their
own bid. Only application-level actor subtraction would.

**`jwt_blacklist` hygiene.** It is a plain Redis set with no per-member TTL, so it grows
without bound, and `is_token_blacklisted` fails open when Redis is unreachable
(`libs/core/jwt_validator.py:209-214`). Both affect every authenticated endpoint today.
This design inherits them; it does not worsen them.

**pandas on the event loop.** The fix is pushing CPU-bound work off the loop
(`await asyncio.to_thread(...)`) across roughly five service files. It benefits every
endpoint on 8003, costs no deployment surface, and is independent of where the stream
lives. Worth raising on its own merits rather than folding into this work.

## Verification

Unit and integration tests are written first, per TDD, and watched to fail.

- **pytest** — audience resolution per rule; actor subtraction; template rendering;
  action-level count (96 blocks yields 1 document, not 96); TTL index present; sync frame
  returns the latest 50 with an `unreadCount` computed independently of that limit; a
  ticket is rejected on second use; a blacklisted token closes the stream at the next
  heartbeat
- **`node --test`** — slice reducers; `sync` replaces while `notification` prepends;
  dedupe by `_id`; watchdog fires on a missed heartbeat; SharedWorker fan-out to two ports
- **Integration** — emit produces N documents, publishes to Redis, and the SSE endpoint
  yields the frame
- **Browser, and this is the acceptance criterion** — two sessions side by side: user A
  submits, user B sees the toast **without refreshing**. Then F5 and confirm the unread
  count survives. Then log out and confirm the stream closes. Then open 7 tabs and confirm
  ordinary API calls still succeed.

Green unit tests will not prove this works. The two-browser test is what decides it.
