"""ConsumerManager must honour USE_RABBITMQ_JOBS / USE_RABBITMQ_AUDIT.

backend/.env sets both to false to run the local stack without the broker, but
ConsumerManager.start() constructs and starts both consumers regardless, so the
"disabled" broker still gets two live consumer threads.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.scheduler.app.consumers.consumer_manager import ConsumerManager  # noqa: E402


def _manager():
    return ConsumerManager(cron_manager=MagicMock(), event_loop=MagicMock())


def test_jobs_disabled_means_no_job_consumer():
    m = _manager()
    with patch("services.scheduler.app.consumers.consumer_manager.config") as cfg:
        cfg.USE_RABBITMQ_JOBS = False
        cfg.USE_RABBITMQ_AUDIT = False
        m.start()
    assert m.job_consumer is None
    assert m.audit_consumer is None


def test_jobs_enabled_starts_only_the_job_consumer():
    m = _manager()
    with patch("services.scheduler.app.consumers.consumer_manager.config") as cfg, \
         patch("services.scheduler.app.consumers.consumer_manager.JobConsumer") as JC, \
         patch("services.scheduler.app.consumers.consumer_manager.AuditConsumer") as AC:
        cfg.USE_RABBITMQ_JOBS = True
        cfg.USE_RABBITMQ_AUDIT = False
        m.start()
    JC.return_value.start.assert_called_once()
    AC.assert_not_called()
    assert m.audit_consumer is None


def test_both_enabled_starts_both():
    m = _manager()
    with patch("services.scheduler.app.consumers.consumer_manager.config") as cfg, \
         patch("services.scheduler.app.consumers.consumer_manager.JobConsumer") as JC, \
         patch("services.scheduler.app.consumers.consumer_manager.AuditConsumer") as AC:
        cfg.USE_RABBITMQ_JOBS = True
        cfg.USE_RABBITMQ_AUDIT = True
        m.start()
    JC.return_value.start.assert_called_once()
    AC.return_value.start.assert_called_once()


def test_stop_tolerates_consumers_that_never_started():
    m = _manager()
    with patch("services.scheduler.app.consumers.consumer_manager.config") as cfg:
        cfg.USE_RABBITMQ_JOBS = False
        cfg.USE_RABBITMQ_AUDIT = False
        m.start()
        m.stop()  # must not raise
    assert m.is_running is False
