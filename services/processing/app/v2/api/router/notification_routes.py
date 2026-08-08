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
