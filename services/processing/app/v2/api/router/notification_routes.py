"""Notification endpoints: ticket, stream, list, read.

EventSource cannot set an Authorization header and this client stores tokens in
sessionStorage with no cookie auth, so the stream authenticates with a
short-lived single-use ticket. What reaches nginx access logs is opaque and
already dead, and is not the access token.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import secrets
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

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


HEARTBEAT_SECONDS = 25
SYNC_LIMIT = 50
MAX_STREAM_SECONDS = 1800  # 30 minutes, matching the access-token lifetime.
# Conservative fallback whenever a ticket's exp is missing, zero, or
# unparseable -- exp can legitimately be absent (it comes from the decoded
# user payload, which may not carry it), so the stream must still get a
# bounded lifetime rather than running forever.
QUEUE_MAXSIZE = 100  # Bounds memory for a client that stalls (TCP
# backpressure, no FIN/RST) without disconnecting. run_subscriber's
# put_nowait already drops-and-logs on a full queue; the next reconnect's
# sync frame repairs whatever that one stalled connection missed.


def format_sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def format_heartbeat() -> str:
    """A named SSE event ("ping"), not a comment.

    The conventional SSE keep-alive is a comment line (starting with ':'),
    and that was tried first here -- but the WHATWG event-stream parser
    ignores comment lines entirely. They never reach EventSource's JS
    listeners, not even as a generic "message" event, so a comment-only
    heartbeat is completely unobservable to the client. Combined with the
    fact that EventSource.onerror does NOT fire when the underlying socket
    dies silently (a closed laptop lid, a dropped VPN, a NAT timeout with no
    TCP FIN/RST), a comment-form heartbeat leaves the client with no signal
    at all when the connection is truly dead: nothing arrives, nothing
    errors, and the bell just goes quiet until the page is reloaded.

    A named event keeps nginx/any proxy from reaping an idle stream exactly
    as well as a comment would -- it is bytes on the wire either way -- but
    it is also something `EventSource.addEventListener("ping", ...)` can
    see, so the client can pet a watchdog on it and notice a socket that has
    gone silently dead instead of only ever noticing on the next page load.
    """
    return format_sse("ping", {})


def blacklist_key(token: str) -> str:
    """The key the auth service's logout path actually writes.

    services/auth/app/services/redis_blacklist.py:invalidate_access_token()
    does `SETEX blacklist:access:{token} ttl user_id` on logout, in the Redis
    logical db settings.REDIS_BLACKLIST_DB (matches settings.REDIS_BLACKLIST_DB
    here) -- not the notifications db used for tickets/pub-sub, and not the
    "jwt_blacklist" SET that libs/core/jwt_validator.py:blacklist_token() can
    write to but that has zero call sites anywhere in this codebase.
    """
    return f"blacklist:access:{token}"


async def is_revoked(blacklist_client, token: str) -> bool:
    """Fails open when Redis is unreachable, matching
    libs/core/jwt_validator.py:213-214. Inherited behaviour, not introduced.

    `blacklist_client` is a separate Redis connection on REDIS_BLACKLIST_DB
    (see main.py's lifespan), not the notification_redis client used for
    tickets/pub-sub -- they are different logical databases on the same
    Redis server. A None client (blacklist subsystem failed to start, or
    Redis briefly unavailable) fails open the same as a raising one: a
    revoked token simply isn't caught mid-stream rather than the stream
    endpoint breaking.
    """
    if blacklist_client is None:
        return False
    try:
        return bool(await blacklist_client.exists(blacklist_key(token)))
    except Exception:
        logger.error("Blacklist check failed; treating token as valid")
        return False


def compute_stream_deadline(exp: Any) -> float:
    """Absolute unix time the stream must close by.

    exp can legitimately be absent -- the ticket's payload is read from the
    decoded user token, which may not carry it -- so a missing, zero,
    non-finite, or unparseable value falls back to a conservative
    MAX_STREAM_SECONDS cap rather than disabling the lifetime limit or
    rejecting the connection outright. The stream must always be bounded.
    """
    fallback = time.time() + MAX_STREAM_SECONDS
    try:
        exp_val = float(exp)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(exp_val) or exp_val <= 0:
        return fallback
    return exp_val


def new_stream_queue() -> "asyncio.Queue":
    """Bounded so a stalled client can't grow memory without limit."""
    return asyncio.Queue(maxsize=QUEUE_MAXSIZE)


async def stream_events(
    request: Request,
    blacklist_client,
    registry,
    repository,
    user_id: str,
    token: str,
    deadline: float,
    queue: "asyncio.Queue",
):
    """The SSE event generator for one connection.

    Extracted from the route handler so it can be driven directly with fakes
    in tests, without a real ASGI request or app.state.

    `blacklist_client` is only ever used for the revocation recheck below --
    it is the separate Redis connection on REDIS_BLACKLIST_DB, not the
    notification_redis client used for tickets/pub-sub (that one is consumed
    entirely by the /stream route handler before this generator starts, to
    resolve the ticket into a session).

    The revocation re-check runs on a time basis -- at least once every
    HEARTBEAT_SECONDS of wall-clock time -- regardless of whether a
    notification was delivered or the wait timed out. Gating the check on the
    timeout branch alone means a stream that never goes idle for
    HEARTBEAT_SECONDS never re-checks the blacklist: exactly the busy-stream
    case the check exists to cover.
    """
    last_revocation_check = time.monotonic()
    try:
        items = await repository.list_for_user(user_id, limit=SYNC_LIMIT)
        unread = await repository.unread_count(user_id)
        yield format_sse("sync", {"items": items, "unreadCount": unread})

        while True:
            if await request.is_disconnected():
                break
            if time.time() >= deadline:
                break

            # Wait only until the next revocation check is due, not always a
            # full HEARTBEAT_SECONDS -- otherwise a notification delivered
            # partway through would silently reset how long it's been since
            # the blacklist was last checked.
            wait_budget = max(
                0.0,
                HEARTBEAT_SECONDS - (time.monotonic() - last_revocation_check),
            )
            try:
                notification = await asyncio.wait_for(
                    queue.get(), timeout=wait_budget
                )
                delivered = True
            except asyncio.TimeoutError:
                notification = None
                delivered = False

            # Time-based, independent of which branch above ran: a stream
            # that keeps delivering notifications faster than
            # HEARTBEAT_SECONDS must still notice a revoked token, not only
            # a stream that goes idle long enough to hit the except branch.
            due_for_check = (
                time.monotonic() - last_revocation_check
            ) >= HEARTBEAT_SECONDS
            if due_for_check:
                last_revocation_check = time.monotonic()
                if token and await is_revoked(blacklist_client, token):
                    break

            if delivered:
                yield format_sse("notification", notification)
            elif due_for_check:
                yield format_heartbeat()
    finally:
        registry.remove(user_id, queue)


@router.get("/stream")
async def stream(request: Request, ticket: str = Query(...)):
    redis_client = request.app.state.notification_redis
    blacklist_client = request.app.state.notification_blacklist_redis
    registry = request.app.state.notification_registry
    repository = request.app.state.notification_repository

    session = await consume_ticket(redis_client, ticket)
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid or expired ticket")

    user_id = session["user_id"]
    token = session.get("token", "")
    if not token:
        # A session with no usable token can never be checked against the
        # revocation blacklist -- is_revoked's `if token and ...` guard would
        # silently skip every check for the stream's whole lifetime. Reject
        # at the point the session is unpacked rather than establishing a
        # stream that can never be revoked.
        raise HTTPException(status_code=401, detail="Invalid or expired ticket")
    deadline = compute_stream_deadline(session.get("exp"))

    queue = new_stream_queue()
    registry.add(user_id, queue)

    return StreamingResponse(
        stream_events(
            request, blacklist_client, registry, repository, user_id, token, deadline, queue
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # tells nginx not to buffer, even if the
                                        # location block is missed
        },
    )


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
