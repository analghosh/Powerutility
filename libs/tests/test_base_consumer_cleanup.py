"""BaseConsumer.run() must survive a broker disconnect instead of killing its thread.

base_consumer.py:100 calls self._cleanup() in a finally: block, but no class in the
hierarchy defines it. The finally runs on the first loop iteration — so the very first
time start_consuming() returns or raises, the AttributeError propagates out of run()
and the consumer thread dies. Observed in logs/scheduler.log on 2026-09-15 for both
scheduler.jobs and scheduler.audit; the live broker showed consumers=0 as a result.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.messaging.base_consumer import BaseConsumer  # noqa: E402


class _Probe(BaseConsumer):
    def process_message(self, message, properties):
        return True


def _consumer():
    c = _Probe(queue_name="q", exchange_name="x", routing_key="r")
    c.manager = MagicMock()
    return c


def test_cleanup_is_defined():
    assert hasattr(_consumer(), "_cleanup")


def test_cleanup_closes_open_channel_and_connection():
    c = _consumer()
    c.channel = MagicMock(is_open=True)
    c.connection = MagicMock(is_open=True)
    chan, conn = c.channel, c.connection

    c._cleanup()

    chan.close.assert_called_once()
    conn.close.assert_called_once()
    assert c.channel is None
    assert c.connection is None


def test_cleanup_skips_already_closed_handles():
    c = _consumer()
    c.channel = MagicMock(is_open=False)
    c.connection = MagicMock(is_open=False)
    chan, conn = c.channel, c.connection

    c._cleanup()

    chan.close.assert_not_called()
    conn.close.assert_not_called()


def test_cleanup_never_raises_when_close_fails():
    c = _consumer()
    c.channel = MagicMock(is_open=True)
    c.channel.close.side_effect = RuntimeError("stream lost")
    c.connection = MagicMock(is_open=True)

    c._cleanup()  # must not raise

    assert c.channel is None


def test_cleanup_does_not_tear_down_the_shared_manager():
    """RabbitMQManager is a process-wide singleton shared by every consumer thread."""
    c = _consumer()
    c.channel = MagicMock(is_open=True)
    c.connection = MagicMock(is_open=True)
    manager = c.manager

    c._cleanup()

    manager.close.assert_not_called()
    assert c.manager is manager


def test_cleanup_tolerates_never_connected_state():
    c = _consumer()
    c.channel = None
    c.connection = None

    c._cleanup()  # must not raise

    assert c.channel is None
