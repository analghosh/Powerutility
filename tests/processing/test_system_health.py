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

    async def run_rows(self, job_id, execution_id):
        return self.rows.get("run", [])

    async def recent_runs(self, job_id, statuses, limit):
        if "recent" in self.rows:
            return self.rows["recent"]
        return [row for row in (self.rows.get("success"),) if row]

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
            {"provider_code": f"P{i}", "provider_name": f"Provider {i}", "error": "Cannot connect to host"}
            for i in range(14)
        ]},
    }
    every_15 = {**DAILY_0730, "job_type": "api_raw_data_processor", "schedule": "*/15 * * * *"}
    repository = StubRepository(rows={"any": recent, "finished": recent})
    rated = _rate(repository, job=every_15)
    assert rated["state"] == jh.STATE_PARTIAL
    assert rated["failures_total"] == 14
    assert len(rated["failures"]) == jh.FAILURE_LIST_LIMIT
    assert rated["failures"][0] == {"name": "Provider 0", "code": "P0", "reason": "Cannot connect to host"}
    assert rated["missed_reason"] is None



# ---- judged by what the scheduler registered --------------------------------

TEST_SITE_NOW = datetime(2026, 9, 28, 10, 23, tzinfo=timezone.utc)  # 15:53 IST
PEAK = {
    "_id": "694e8adc54a8416eaf1156df",
    "job_type": "peak_demand_compute",
    "name": "Peak Demand Compute",
    "service": "scheduler",
    "schedule": "*/15 * * * *",  # job_data; the scheduler registers 0 */6
    "timezone": "Asia/Kolkata",
    "enabled": True,
    "created_at": datetime(2026, 1, 1),
}
# Started 13:47 IST (a restart), last success 12:00 IST: as on the test site.
PEAK_MARKER = {"status": "processing", "created_at": datetime(2026, 9, 28, 8, 17, 38)}
PEAK_SUCCESS = {"status": "success", "created_at": datetime(2026, 9, 28, 6, 30, 1)}
LIVE = {"scheduler": {"peak_demand_compute": "0 */6 * * *"}, "ingestion": {}}


def _rate_at(repository, job, registered):
    service = _service_with(repository)
    return asyncio.run(service._rate_job(job, TEST_SITE_NOW, {}, registered))


def test_a_job_on_its_registered_schedule_is_not_called_missed():
    repository = StubRepository(rows={"any": PEAK_MARKER, "finished": PEAK_SUCCESS})
    rated = _rate_at(repository, PEAK, LIVE)
    assert rated["state"] == jh.STATE_OK
    assert rated["schedule"] == "0 */6 * * *"
    assert rated["configured_schedule"] == "*/15 * * * *"
    assert rated["schedule_source"] == "scheduler"


def test_without_the_listing_the_page_falls_back_to_job_data():
    repository = StubRepository(rows={"any": PEAK_MARKER, "finished": PEAK_SUCCESS})
    rated = _rate_at(repository, PEAK, {"scheduler": None, "ingestion": None})
    assert rated["state"] == jh.STATE_MISSED
    assert rated["schedule_source"] == "job_data"


def test_a_job_enabled_in_job_data_but_never_registered_is_turned_off():
    migration = {**PEAK, "job_type": "data_migration", "name": "Data Migration", "schedule": "0 2 * * *"}
    repository = StubRepository()
    rated = _rate_at(repository, migration, LIVE)
    assert rated["state"] == jh.STATE_PAUSED
    assert rated["paused_reason"] == "not_scheduled"


def test_the_cron_status_url_sits_beside_health():
    assert shs.cron_status_url("http://localhost:8004") == "http://localhost:8004/cron/status"
    assert shs.cron_status_url("http://localhost:8002/api/v1/") == "http://localhost:8002/cron/status"



# ---- "no data returned" is not a failure --------------------------------------

EVERY_15 = {**DAILY_0730, "job_type": "api_raw_data_processor", "schedule": "*/15 * * * *"}


def _run(status, details, message="Processed 20, failed 6"):
    return {
        "status": status,
        "created_at": datetime(2026, 9, 28, 6, 15),
        "error_message": message,
        "result_data": {"failed_provider_details": details},
    }


def _source(name, error):
    return {"provider_code": name.upper().replace(" ", "_"), "provider_name": name, "error": error}


NO_DATA = "No data fetched from API"
PXIL_EMPTY = [_source(f"PXIL Exchange DSM {market} Provider", NO_DATA) for market in ("DAM", "GDAM", "HPDAM")] * 2


def test_a_run_whose_only_failures_are_empty_sources_is_on_time():
    run = _run("partial_success", PXIL_EMPTY)
    rated = _rate(StubRepository(rows={"any": run, "finished": run}), job=EVERY_15)
    assert rated["state"] == jh.STATE_OK
    assert rated["failures"] == [] and rated["message"] is None
    assert rated["no_data_sources"] == [
        "PXIL Exchange DSM DAM Provider", "PXIL Exchange DSM GDAM Provider", "PXIL Exchange DSM HPDAM Provider",
    ]


def test_an_error_run_where_every_source_was_empty_is_on_time_too():
    run = _run("error", PXIL_EMPTY[:2], message="Processed 0, failed 2")
    rated = _rate(StubRepository(rows={"any": run, "finished": run}), job=EVERY_15)
    assert rated["state"] == jh.STATE_OK


def test_a_real_failure_among_empty_sources_still_needs_attention():
    grid = _source("Grid India Supply", "API fetch error: Cannot connect to host gateway.grid-india.in:443")
    run = _run("partial_success", PXIL_EMPTY + [grid], message="Processed 20, failed 7")
    rated = _rate(StubRepository(rows={"any": run, "finished": run}), job=EVERY_15)
    assert rated["state"] == jh.STATE_PARTIAL
    assert [failure["name"] for failure in rated["failures"]] == ["Grid India Supply"]
    assert rated["failures_total"] == 1
    assert len(rated["no_data_sources"]) == 3


def test_a_failed_run_without_source_details_is_unchanged():
    run = {"status": "error", "created_at": datetime(2026, 9, 28, 6, 15), "error_message": "Failed to fetch API data: TimeoutError"}
    rated = _rate(StubRepository(rows={"any": run, "finished": run}), job=EVERY_15)
    assert rated["state"] == jh.STATE_FAILED
    assert rated["message"] == "Failed to fetch API data: TimeoutError"



# ---- one run, two rows: judged by the worse one --------------------------------

def _row(status, minute, execution_id, **extra):
    return {"status": status, "execution_id": execution_id,
            "created_at": datetime(2026, 9, 28, 11, minute), **extra}


PIPELINE_STEPS = {"steps": [
    {"step": "idf_forecast_merge", "name": "IDF Forecast Merge", "status": "success", "message": "merged=42"},
    {"step": "demand_data_processing", "name": "Demand Data Processing", "status": "success", "message": "ok"},
    {"step": "new_dsm_rate_calculation", "name": "New DSM Rate and Net DSM Calculation", "status": "error",
     "message": "New DSM data processing failed",
     "result": {"status": "error", "failed_count": 68, "blocks_total": 68, "processed_count": 0}},
], "message": "Demand serve pipeline finished — 2/3 steps succeeded"}

NOW_1120 = datetime(2026, 9, 28, 11, 20, tzinfo=timezone.utc)
PIPELINE = {**DAILY_0730, "job_type": "demand_serve_pipeline", "service": "scheduler", "schedule": "*/15 * * * *"}


def _rate_now(repository, job):
    service = _service_with(repository)
    return asyncio.run(service._rate_job(job, NOW_1120, {}, None))


def test_a_run_whose_wrapper_said_success_but_the_job_said_error_is_failed():
    error = _row("error", 15, "dsp_1115", result_data=PIPELINE_STEPS)
    wrapper = _row("success", 15, "dsp_1115")
    repository = StubRepository(rows={
        "any": wrapper, "finished": wrapper, "run": [error, wrapper],
        "recent": [wrapper, error, _row("success", 0, "dsp_1100"), _row("error", 0, "dsp_1100")],
    })
    rated = _rate_now(repository, PIPELINE)
    assert rated["state"] == jh.STATE_FAILED
    assert rated["message"] == (
        "New DSM Rate and Net DSM Calculation: New DSM data processing failed (68 of 68 blocks failed)"
    )
    assert rated["last_success_at"] is None  # no run in the scan was clean


def test_last_worked_is_the_last_run_with_nothing_failed():
    failing = [_row("error", 15, "r3"), _row("success", 15, "r3")]
    clean = [_row("success", 0, "r2")]
    repository = StubRepository(rows={
        "any": failing[1], "finished": failing[1], "run": failing, "recent": failing + clean,
    })
    rated = _rate_now(repository, PIPELINE)
    assert rated["state"] == jh.STATE_FAILED
    assert rated["last_success_at"] == "2026-09-28T11:00:00+00:00"


def test_a_clean_run_stays_clean():
    ok = _row("success", 15, "r1")
    repository = StubRepository(rows={"any": ok, "finished": ok, "run": [ok, _row("success", 15, "r1")]})
    rated = _rate_now(repository, PIPELINE)
    assert rated["state"] == jh.STATE_OK
    assert rated["last_success_at"] == "2026-09-28T11:15:00+00:00"
