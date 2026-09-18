"""publish_job_trigger opens a blocking pika connection; an async caller must not
run it on the event loop. publish_job_trigger_async offloads it to a thread.
"""
import asyncio
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from libs.messaging.job_publisher import JobPublisher  # noqa: E402


@pytest.mark.asyncio
async def test_async_publish_runs_off_the_event_loop_thread():
    loop_thread = threading.current_thread().name
    seen = {}

    def _fake_publish(**kwargs):
        seen["thread"] = threading.current_thread().name
        return True

    p = JobPublisher()
    with patch.object(JobPublisher, "publish_job_trigger", side_effect=_fake_publish):
        result = await p.publish_job_trigger_async(
            job_type="analytics_export", source_service="processing", payload={"job_id": "x"}
        )

    assert result is True
    assert seen["thread"] != loop_thread


@pytest.mark.asyncio
async def test_async_publish_forwards_every_argument():
    captured = {}

    def _fake_publish(**kwargs):
        captured.update(kwargs)
        return False

    p = JobPublisher()
    with patch.object(JobPublisher, "publish_job_trigger", side_effect=_fake_publish):
        result = await p.publish_job_trigger_async(
            job_type="analytics_export",
            source_service="processing",
            payload={"job_id": "abc"},
            correlation_id="corr-1",
        )

    assert result is False
    assert captured == {
        "job_type": "analytics_export",
        "source_service": "processing",
        "payload": {"job_id": "abc"},
        "correlation_id": "corr-1",
    }


@pytest.mark.asyncio
async def test_async_publish_returns_false_instead_of_raising():
    p = JobPublisher()
    with patch.object(JobPublisher, "publish_job_trigger", side_effect=RuntimeError("broker down")):
        assert await p.publish_job_trigger_async(job_type="j", source_service="s") is False


@pytest.mark.asyncio
async def test_event_loop_stays_responsive_during_a_slow_publish():
    def _slow_publish(**kwargs):
        import time
        time.sleep(0.3)
        return True

    p = JobPublisher()
    ticks = 0

    async def _ticker():
        nonlocal ticks
        for _ in range(10):
            await asyncio.sleep(0.02)
            ticks += 1

    with patch.object(JobPublisher, "publish_job_trigger", side_effect=_slow_publish):
        await asyncio.gather(p.publish_job_trigger_async(job_type="j", source_service="s"), _ticker())

    assert ticks == 10
