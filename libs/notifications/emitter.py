"""The single entry point callers use: notify().

Call sites know nothing beyond this function. They must always wrap the call in
try/except -- a failed notification must never fail the business action that
triggered it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from libs.notifications.audience import DIRECTORY_KEY
from libs.notifications.events import get_event
from libs.notifications.models import Notification
from libs.notifications.pubsub import publish_many

logger = logging.getLogger(__name__)

# How long the live push is allowed to hold up the caller's request.
#
# notify() is awaited inline between the service call and the HTTP response on
# every bid write path (the four market controllers and block_bid_services), so
# whatever this publish costs is added to the latency of submitting or
# approving a bid. The publish client carries socket_timeout=5 with
# retry_on_timeout=True (services/processing/app/main.py), which bounds a
# *hung* Redis -- one that accepts the connection but never answers, as in a
# BGSAVE stall, a failover, or a packet-dropping partition -- at 5-10s per
# call, and a mixed block-bid decision issues several calls in a row. At gate
# closure that is the worst possible moment, and the frontend's own axios
# timeout then fires on requests that actually succeeded, inviting duplicate
# resubmits.
#
# 1s is 150x the 6.5ms measured for 116 recipients pipelined against the real
# stack, so it leaves ample room for PROD, where Redis is a network hop rather
# than localhost, while never approaching the socket timeout it pre-empts.
# Abandoning a slow publish is cheap: the Mongo write above it is already
# durable, so the cost is at most a delayed live push, which the next sync
# frame repairs -- never the notification itself.
PUBLISH_TIMEOUT_SECONDS = 1.0


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

        title, body, link, category, area = event.render(render_context)
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
                category=category,
                area=area,
                created_at=created_at,
            )
            for user_id in recipients
        ]

        ids = await self.repository.insert_many_for(notifications)

        # One message PER RECIPIENT, but all of them in ONE round trip.
        #
        # Per recipient, because the payload is not identical across recipients --
        # each must carry their own document _id. insert_many_for preserves order,
        # so ids[i] belongs to recipients[i]; a shared broadcast would give every
        # recipient but the first a foreign _id, which mark_read (filtering on
        # {"_id": ..., "user_id": user_id}) can never match, silently orphaning
        # their notification forever.
        #
        # One round trip, because issuing them serially did not stay negligible as
        # this comment used to claim: measured at 116 recipients on the real stack,
        # 75.3ms serial versus 6.5ms pipelined, inside the request of the user
        # approving the bid. It also grows with the audience and again in PROD,
        # where Redis is a network hop rather than localhost.
        #
        # The trade: a failing pipeline now drops the live push for the whole
        # batch instead of just one recipient. That costs nothing real -- publish
        # failures are connection-level, not per-recipient -- and the Mongo write
        # above is already durable, so every recipient still receives the
        # notification on their next sync frame.
        deliveries = [
            (
                [user_id],
                {
                    "_id": notification_id,
                    "event_key": event_key,
                    "title": title,
                    "body": body,
                    "payload": dict(context),
                    "link": link,
                    "actor": actor_doc,
                    "severity": event.severity.value,
                    "category": category,
                    "area": area,
                    "read_at": None,
                    "created_at": created_at.isoformat(),
                },
            )
            for user_id, notification_id in zip(recipients, ids)
        ]
        try:
            await asyncio.wait_for(
                publish_many(self.redis_client, deliveries),
                timeout=PUBLISH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            # Deliberately separate from the generic handler below: a hung
            # Redis is the case this budget exists for, and it must be
            # distinguishable in the logs from a connection that failed fast.
            logger.error(
                "Notification publish for %s exceeded %.1fs and was abandoned "
                "(%d recipient(s)); the Mongo write is durable, so delivery "
                "falls back to the next sync frame",
                event_key,
                PUBLISH_TIMEOUT_SECONDS,
                len(deliveries),
            )
        except Exception:
            logger.exception(
                "Notification publish failed for %s (%d recipient(s)); the Mongo "
                "write is durable, so delivery falls back to the next sync frame",
                event_key,
                len(deliveries),
            )

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
