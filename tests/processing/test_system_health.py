"""
Tests for the System Health service's pure parts: where each service's health
check lives, and how the page's one-line verdict is reached.
"""

import pytest

from services.processing.app.v2.helpers import job_health as jh
from services.processing.app.v2.services import system_health_service as shs


@pytest.mark.parametrize(
    "service_url, expected",
    [
        ("http://localhost:8001/api/v1/auth", "http://localhost:8001/health"),
        ("http://localhost:8002", "http://localhost:8002/health"),
        ("http://localhost:8004/", "http://localhost:8004/health"),
        ("https://epms.example/auth/api/v1/auth", "https://epms.example/auth/health"),
    ],
)
def test_health_url_is_the_service_root(service_url, expected):
    assert shs.health_url(service_url) == expected


def _service(status=shs.STATUS_UP):
    return {"key": "auth", "status": status}


def _job(state=jh.STATE_OK):
    return {"state": state}


def test_everything_up_and_no_job_in_trouble_is_ok():
    services = [_service(), _service()]
    jobs = [_job(), _job(jh.STATE_PAUSED)]
    assert shs.overall_status(services, jobs) == shs.OVERALL_OK


@pytest.mark.parametrize("state", sorted(jh.ATTENTION_STATES))
def test_any_job_in_trouble_needs_attention(state):
    assert shs.overall_status([_service()], [_job(), _job(state)]) == shs.OVERALL_ATTENTION


@pytest.mark.parametrize("status", [shs.STATUS_DOWN, shs.STATUS_DEGRADED])
def test_a_service_not_fully_up_outranks_job_trouble(status):
    services = [_service(), _service(status)]
    assert shs.overall_status(services, [_job(jh.STATE_FAILED)]) == shs.OVERALL_DOWN


def test_long_error_messages_are_cut_for_the_page():
    trimmed = shs._trim("x" * 1000)
    assert len(trimmed) == shs.ERROR_MESSAGE_LIMIT
    assert trimmed.endswith("…")
    assert shs._trim("  short  ") == "short"
    assert shs._trim(None) is None
