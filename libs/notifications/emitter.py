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
