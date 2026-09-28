"""
Tests for the System Health service's pure parts: where each service's health
check lives, and how the page's one-line verdict is reached.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from services.processing.app.v2.api.router import system_health_routes
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


# ---- who may see the page --------------------------------------------------


class StubRepository:
    """Answers the service's lookups from fixed data, and counts them."""

    def __init__(self, granted_roles=(), active_logs=(), rows=None):
        self.granted_roles = set(granted_roles)
        self.active_logs = set(active_logs)
        self.rows = rows or {}
        self.grant_lookups = 0
        self.activity_lookups = 0

    async def roles_have_page(self, role_names):
        self.grant_lookups += 1
        return bool(self.granted_roles & set(role_names))

    async def any_run_between(self, collection, start, end):
        self.activity_lookups += 1
        return collection in self.active_logs

    async def latest_run(self, job_id, statuses=None):
        key = "finished" if statuses is not None else "any"
        if statuses is not None and set(statuses) == set(jh.SUCCESS_STATUSES):
            key = "success"
        return self.rows.get(key)


def _service_with(repository):
    service = shs.SystemHealthService()
    service.repository = repository
    return service


@pytest.mark.parametrize(
    "roles, permissions",
    [(["admin"], []), (["SuperAdmin"], []), (["super_admin"], []), (["user"], ["*"]),
     (["user"], ["admin:*"]), (["user"], ["system_health:read"])],
)
def test_super_admins_and_covering_permissions_need_no_lookup(roles, permissions):
    repository = StubRepository()
    service = _service_with(repository)
    assert asyncio.run(service.can_view({"roles": roles, "permissions": permissions}))
    assert repository.grant_lookups == 0


def test_other_roles_need_the_menu_grant():
    repository = StubRepository(granted_roles={"operator"})
    service = _service_with(repository)
    assert asyncio.run(service.can_view({"roles": ["operator"], "permissions": ["dam_bid:read"]}))
    assert not asyncio.run(service.can_view({"roles": ["user"], "permissions": ["dam_bid:read"]}))


def test_a_grant_answer_is_reused_for_a_minute():
    repository = StubRepository(granted_roles={"operator"})
    service = _service_with(repository)
    for _ in range(3):
        asyncio.run(service.can_view({"roles": ["operator"], "permissions": []}))
    assert repository.grant_lookups == 1


def test_an_anonymous_caller_is_refused_without_a_lookup():
    repository = StubRepository(granted_roles={"operator"})
    service = _service_with(repository)
    assert not asyncio.run(service.can_view({"roles": [], "permissions": []}))
    assert repository.grant_lookups == 0


def test_the_route_answers_403_to_a_user_without_the_page(monkeypatch):
    service = _service_with(StubRepository())
    monkeypatch.setattr(system_health_routes, "system_health_service", service)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(system_health_routes.get_system_health(
            user_context={"roles": ["user"], "permissions": []}
        ))
    assert refused.value.status_code == 403
    assert refused.value.detail["code"] == "SYSTEM_HEALTH_FORBIDDEN"


# ---- why a job needs attention ---------------------------------------------

NOW = datetime(2026, 9, 28, 6, 17, tzinfo=timezone.utc)
DAILY_0730 = {
    "_id": "694e8aca1be04a3f7ee5ff49",
    "job_type": "forecast_api_processor",
    "name": "Forecast API Processor",
    "service": "ingestion",
    "schedule": "30 7 * * *",
    "timezone": "Asia/Kolkata",
    "enabled": True,
    "created_at": datetime(2026, 1, 1),
}
STALE_RUN = {"status": "error", "created_at": datetime(2026, 9, 23, 2, 0), "error_message": "x"}


def _rate(repository, job=DAILY_0730, activity=None):
    service = _service_with(repository)
    return asyncio.run(service._rate_job(job, NOW, {} if activity is None else activity))


def test_a_missed_job_whose_service_logged_nothing_was_missed_because_it_was_down():
    repository = StubRepository(rows={"any": STALE_RUN, "finished": STALE_RUN})
    rated = _rate(repository)
    assert rated["state"] == jh.STATE_MISSED
    assert rated["missed_reason"] == jh.MISSED_RUNNER_DOWN


def test_a_missed_job_whose_service_was_busy_was_not_started():
    repository = StubRepository(active_logs={"job_details"}, rows={"any": STALE_RUN, "finished": STALE_RUN})
    assert _rate(repository)["missed_reason"] == jh.MISSED_NOT_STARTED


def test_the_activity_of_one_slot_is_looked_up_once_per_check():
    repository = StubRepository(rows={"any": STALE_RUN, "finished": STALE_RUN})
    activity = {}
    _rate(repository, activity=activity)
    _rate(repository, job={**DAILY_0730, "_id": "another"}, activity=activity)
    assert repository.activity_lookups == 1


def test_a_partly_failed_run_lists_its_failed_sources():
    recent = {
        "status": "partial_success",
        "created_at": datetime(2026, 9, 28, 6, 0),
        "error_message": "Processed 1, failed 14",
        "result_data": {"failed_provider_details": [
            {"provider_code": f"P{i}", "provider_name": f"Provider {i}", "error": "No data fetched from API"}
            for i in range(14)
        ]},
    }
    every_15 = {**DAILY_0730, "job_type": "api_raw_data_processor", "schedule": "*/15 * * * *"}
    repository = StubRepository(rows={"any": recent, "finished": recent})
    rated = _rate(repository, job=every_15)
    assert rated["state"] == jh.STATE_PARTIAL
    assert rated["failures_total"] == 14
    assert len(rated["failures"]) == jh.FAILURE_LIST_LIMIT
    assert rated["failures"][0] == {"name": "Provider 0", "code": "P0", "reason": "No data fetched from API"}
    assert rated["missed_reason"] is None
