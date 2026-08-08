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
