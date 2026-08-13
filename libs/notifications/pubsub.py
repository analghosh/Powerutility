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
import random
import time
from typing import Any, Dict, List, Tuple, Type

logger = logging.getLogger(__name__)

CHANNEL = "epms:notifications"

SUBSCRIBER_BASE_BACKOFF_SECONDS = 1.0
SUBSCRIBER_MAX_BACKOFF_SECONDS = 30.0

# How long a connection has to survive before it counts as genuinely healthy
# and the backoff is allowed to start over. Resetting on a successful
# subscribe() alone is not enough: a flapping Redis (subscribe succeeds, the
# connection dies immediately -- a server still loading its dataset, or a
# proxy that accepts then drops) would reset the counter every cycle and
# retry at the base delay forever, which is the hammering the backoff exists
# to prevent.
SUBSCRIBER_HEALTHY_SECONDS = 60.0

# A read timeout is NOT a connection failure. The pub/sub client carries
# socket_timeout=5 (services/processing/app/main.py), and this loop polls a
# channel that is idle most of the time, so a timeout is the expected quiet
# case -- treating it as fatal would tear down a perfectly healthy
# subscription every few seconds. redis-py's TimeoutError is a sibling of its
# ConnectionError under RedisError (verified against redis 5.0.1: neither
# derives from the other, and redis.exceptions.TimeoutError is NOT the builtin
# TimeoutError), so it has to be named explicitly. The builtin TimeoutError --
# which asyncio.TimeoutError aliases from 3.11 on -- is included for the case
# where a timeout surfaces before redis-py wraps it.
_BENIGN_READ_TIMEOUTS: Tuple[Type[BaseException], ...]
try:
    from redis.exceptions import TimeoutError as _RedisTimeoutError

    _BENIGN_READ_TIMEOUTS = (_RedisTimeoutError, TimeoutError, asyncio.TimeoutError)
except Exception:  # pragma: no cover - redis is installed in every environment
    _BENIGN_READ_TIMEOUTS = (TimeoutError, asyncio.TimeoutError)


def subscriber_backoff_seconds(attempt: int) -> float:
    """Exponential backoff with equal jitter, capped.

    Jitter is not decoration: a Redis restart drops every worker's subscriber
    at the same instant, and without jitter they all reconnect in the same
    millisecond, repeatedly, against a server that has only just come back.
    Equal jitter keeps the spread even once the cap is reached.

    Same shape as the client's nextBackoffMs (client/src/utils/
    notificationStream.js), arrived at independently -- nothing is shared
    across the two repositories.
    """
    ceiling = min(
        SUBSCRIBER_MAX_BACKOFF_SECONDS,
        SUBSCRIBER_BASE_BACKOFF_SECONDS * (2 ** min(attempt, 32)),
    )
    return ceiling / 2 + random.random() * (ceiling / 2)


async def _stopped_within(stop_event: asyncio.Event, delay: float) -> bool:
    """Wait up to `delay` seconds, returning True if stop_event was set.

    Waits on the event rather than sleeping blindly: shutdown must never be
    made to sit out a 30s backoff. asyncio.CancelledError propagates from here
    untouched, which is what keeps task cancellation working.
    """
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
        return True
    except asyncio.TimeoutError:
        return False


async def _close_pubsub(pubsub) -> None:
    """Release one pub/sub connection. Never raises a teardown error.

    Both steps are swallowed independently and deliberately: this runs from a
    finally that also executes while a CancelledError is in flight, and an
    exception raised here would replace that cancellation -- masking shutdown.
    """
    try:
        await pubsub.unsubscribe(CHANNEL)
    except Exception:
        logger.exception("Error unsubscribing from notification channel")
    try:
        await pubsub.close()
    except Exception:
        logger.exception("Error closing pubsub connection")


class ConnectionRegistry:
    """user_id -> the queues feeding that user's open streams in this process."""

    def __init__(self):
        self._by_user: Dict[str, List[asyncio.Queue]] = {}

    def add(self, user_id: str, queue: asyncio.Queue) -> None:
        queues = self._by_user.setdefault(user_id, [])
        if queue not in queues:
            queues.append(queue)

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

    def count_for(self, user_id: str) -> int:
        """How many streams this one user currently holds in this process.

        The /stream route caps on this rather than on count(): a process-wide
        ceiling would let one busy session lock every other user out of
        notifications.
        """
        return len(self._by_user.get(user_id, []))

    def count(self) -> int:
        return sum(len(v) for v in self._by_user.values())


def _envelope(user_ids: List[str], notification: Dict[str, Any]) -> str:
    """The single place the wire format is built, so publish and publish_many
    cannot drift apart -- _consume parses exactly this shape."""
    return json.dumps(
        {"user_ids": user_ids, "notification": notification}, default=str
    )


async def publish(redis_client, user_ids: List[str], notification: Dict[str, Any]) -> None:
    if not user_ids:
        return
    await redis_client.publish(CHANNEL, _envelope(user_ids, notification))


async def publish_many(
    redis_client, deliveries: List[Tuple[List[str], Dict[str, Any]]]
) -> None:
    """Publish every delivery in ONE round trip.

    Each recipient needs their own message because each carries their own
    document _id (see NotificationEmitter), but they do not need their own round
    trip. Measured against the real stack at 116 recipients: 75.3ms issuing them
    one at a time versus 6.5ms pipelined, all of it inside the request of the user
    who approved the bid.

    transaction=False: this is a batch, not a MULTI/EXEC. Publishes are
    independent and there is nothing to roll back -- wrapping them in a
    transaction would only add two more commands to the round trip.
    """
    if not deliveries:
        return
    pipe = redis_client.pipeline(transaction=False)
    queued = 0
    for user_ids, notification in deliveries:
        if not user_ids:
            continue
        pipe.publish(CHANNEL, _envelope(user_ids, notification))
        queued += 1
    if queued == 0:
        return
    await pipe.execute()


async def _consume(pubsub, registry: ConnectionRegistry, stop_event: asyncio.Event) -> None:
    """Deliver messages until stop_event is set or the connection fails.

    Returns normally on stop. Anything other than a benign read timeout is
    allowed to propagate to run_subscriber, which owns the reconnect decision.
    """
    while not stop_event.is_set():
        try:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
        except _BENIGN_READ_TIMEOUTS:
            # An idle channel, not a dead one. Keep the subscription.
            continue

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
                try:
                    queue.put_nowait(notification)
                except Exception:
                    logger.exception(
                        "Failed to deliver notification to queue for user %s",
                        user_id,
                    )


async def run_subscriber(redis_client, registry: ConnectionRegistry, stop_event: asyncio.Event) -> None:
    """Long-lived task started at service startup.

    Every process receives every message and discards those naming users it does
    not hold -- one JSON parse and one dict lookup per miss. Cost scales with
    worker count, not user count.

    Reconnects on its own. This used to subscribe once and loop, with no outer
    loop at all: when Redis dropped, get_message raised
    redis.exceptions.ConnectionError, it propagated out of the while, the
    finally ran, and THE TASK ENDED PERMANENTLY. Proven against the real
    container -- after `docker stop epms-redis` then `docker start`,
    `PUBSUB NUMSUB epms:notifications` read 0, and a published notification
    never reached the browser even though Mongo held it. Every live push was
    silently dead for every user on that worker until someone restarted the
    service, and because the REST sync path still fills the bell on reload,
    nothing looked broken. Any Redis restart, failover, or transient blip did
    it.

    Four properties this loop must keep, all easy to break here:

    * stop_event stops it promptly, including mid-backoff. The retry wait is
      on the event (_stopped_within), never a bare sleep, so shutdown is never
      made to sit out a 30s wait.
    * asyncio.CancelledError still propagates. The except below is `Exception`,
      never `BaseException` -- CancelledError derives from BaseException, so
      catching the narrower type is exactly what preserves cancellation.
    * Cleanup always runs, and a teardown error can never mask cancellation:
      _close_pubsub swallows its own failures.
    * No duplicate subscription. Each cycle takes a FRESH pubsub, and the
      finally unsubscribes and closes the old one before the next subscribe --
      so PUBSUB NUMSUB reads 1 after a reconnect, not 2.
    """
    attempt = 0
    while not stop_event.is_set():
        pubsub = redis_client.pubsub()
        connected_at = None
        try:
            await pubsub.subscribe(CHANNEL)
            connected_at = time.monotonic()
            logger.info("Notification subscriber listening on %s", CHANNEL)
            await _consume(pubsub, registry, stop_event)
        except Exception:
            logger.exception(
                "Notification subscriber lost its connection; will re-subscribe"
            )
        finally:
            await _close_pubsub(pubsub)

        # A connection that stayed up long enough to be real earns a fresh
        # backoff; one that subscribed and immediately died does not (see
        # SUBSCRIBER_HEALTHY_SECONDS). Without this distinction a flapping
        # Redis pins the delay at the base value and hammers it.
        if (
            connected_at is not None
            and time.monotonic() - connected_at >= SUBSCRIBER_HEALTHY_SECONDS
        ):
            attempt = 0

        if stop_event.is_set():
            break

        delay = subscriber_backoff_seconds(attempt)
        attempt += 1
        logger.warning(
            "Reconnecting notification subscriber to %s in %.1fs (attempt %d)",
            CHANNEL,
            delay,
            attempt,
        )
        if await _stopped_within(stop_event, delay):
            break

    logger.info("Notification subscriber stopped")
