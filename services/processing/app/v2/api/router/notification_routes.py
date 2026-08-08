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
