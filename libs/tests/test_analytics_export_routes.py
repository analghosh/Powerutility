"""The export half of the analytics HTTP surface: start, list, poll, download, cancel.

`POST /export` is three responses wearing one URL — a streamed CSV, a finished
workbook, or a 202 receipt — and the other four routes exist only to get a queued
file back to the person who asked for it. That handover is where the security of
this whole feature lives, so most of what is asserted below is about who is
allowed to touch a job rather than about what the file contains.

**1. The job id is not a capability.** Job ids are short and they travel — in a
URL, in a support ticket, in a screenshot of the My Exports panel. An export can
hold five months of plant-level scheduling for the whole portfolio, so a download
route that served any job to any logged-in caller would be the same class of hole
as `submit-to-exchange` shipping with no authorization: authenticated, therefore
assumed authorized. Every one of the four job routes is checked against the
`user_id` on the row, and `test_downloading_another_users_job_is_403` is the
assertion this task exists to make.

**2. Ownership is checked before the token, and a token is checked before the
file.** The token is a *second* factor, not a substitute for the session: it binds
a link to one job and dies with the file. Checking it first would let a wrong
token turn an authorization failure into "bad link", which reads as a client bug
and gets retried rather than reported.

**3. Expiry is read as UTC whether or not Mongo hands back a timezone.** Motor
returns naive datetimes unless the client is built `tz_aware`; comparing one of
those against an aware `now()` raises, and comparing it against a *naive local*
now silently shifts every expiry by the UTC offset — the defect that made every
EPMS token last six hours instead of thirty minutes. Both shapes are tested.

**4. A finished job that cannot be cancelled says so.** The house defect this
avoids is `update-status` on a lapsed bid: it matches nothing, logs a skip, and
still answers 200, so the checker sees success and the row never changes. A cancel
that changed nothing must not be reported as a cancel.

**5. An expired file is 410 with the filters that would rebuild it,** not 404.
The row outlives the file by 87 days precisely so Regenerate can work; answering
404 throws that away and tells the user their export never existed.

Mongo is a fake job store and the audit logger is a recorder — the house
convention. `POST /export` runs the *real* export service over a stub repository,
so the streamed bytes and the queued job row are the ones the service actually
produces.
"""
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.core.config import settings as real_settings  # noqa: E402
from services.processing.app.v2.api.router import analytics_routes as routes  # noqa: E402
from services.processing.app.v2.dependencies import get_user_context  # noqa: E402
from services.processing.app.v2.helpers.analytics_xlsx import ExportWriteError  # noqa: E402
from services.processing.app.v2.repository.analytics_job_repository import (  # noqa: E402
    build_job_document)
from services.processing.app.v2.services import analytics_export_service as export_svc  # noqa: E402
from services.processing.app.v2.services.analytics_service import AnalyticsService  # noqa: E402

OWNER = "u-owner"
INTRUDER = "u-intruder"


def _ctx(user_id=OWNER, *permissions):
    return {"user": {"id": user_id}, "user_id": user_id, "username": f"user-{user_id}",
            "roles": ["user"], "permissions": list(permissions) or ["*"],
            "correlation_id": "test-corr"}


#: What `get_user_context` hands a route when AuthMiddleware found no token. It is
#: a context, not a refusal, so every route here has to reject it itself.
ANONYMOUS = {"user": None, "user_id": "anonymous", "username": "anonymous",
             "roles": [], "permissions": [], "correlation_id": "test-corr"}


# --------------------------------------------------------------------- fakes


class StubRepo:
    """The analytics Mongo repository reduced to a recorder of `run_pipeline`."""

    def __init__(self, docs=None):
        self.docs = docs or {}
        self.calls = []

    async def run_pipeline(self, collection, pipeline, max_time_ms=None):
        self.calls.append(collection)
        return self.docs.get(collection, [])


class FakeJobs:
    """The job queue in memory. `create` runs the real document builder."""

    def __init__(self, jobs=(), active=None, mark_result=True):
        self.jobs = {j["job_id"]: j for j in jobs}
        self.active = active
        self.mark_result = mark_result
        self.created = []
        self.marks = []
        self.listed = []

    async def get(self, job_id):
        job = self.jobs.get(job_id)
        return dict(job) if job else None

    async def list_for_user(self, user_id, limit=50):
        self.listed.append((user_id, limit))
        rows = [dict(j) for j in self.jobs.values() if j["user_id"] == user_id]
        return rows[:limit]

    async def find_active_by_hash(self, user_id, request_hash):
        if self.active and self.active["user_id"] == user_id:
            return dict(self.active)
        return None

    async def create(self, job):
        doc = build_job_document(job)
        self.created.append(doc)
        self.jobs[doc["job_id"]] = doc
        return doc["job_id"]

    async def mark(self, job_id, status, **fields):
        self.marks.append((job_id, status, fields))
        if not self.mark_result:
            return False
        if job_id in self.jobs:
            self.jobs[job_id].update({"status": status, **fields})
        return True


class FakePublisher:
    """Records the async publish; detonates on the blocking one."""

    def __init__(self):
        self.calls = []

    async def publish_job_trigger_async(self, **kwargs):
        self.calls.append(kwargs)
        return False  # USE_RABBITMQ_JOBS is false here: the sweep is the path

    def publish_job_trigger(self, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the blocking publisher was called from an async route")


class FakeAudit:
    """`AuditLogger`, reduced to the one method these routes call."""

    def __init__(self, raises=None):
        self.entries = []
        self.raises = raises

    def log_user_action(self, **kwargs):
        if self.raises is not None:
            raise self.raises
        self.entries.append(kwargs)
        return "audit-1"

    def actions(self):
        return [e["action"] for e in self.entries]


class RaisingService:
    """An export service that only fails, for the error-mapping tests."""

    def __init__(self, exc):
        self.exc = exc
        self.jobs = FakeJobs()

    async def export(self, request, user_context=None):
        raise self.exc


def _settings(**over):
    base = {name: getattr(real_settings, name) for name in dir(real_settings)
            if name.startswith("ANALYTICS_") or name.startswith("MONGODB_ANALYTICS")}
    base.update(over)
    return SimpleNamespace(**base)


def _service(jobs=None, repo=None, settings=None):
    """The real export service over a stub repository and an in-memory queue."""
    settings = settings or _settings()
    query = AnalyticsService(repository=repo or StubRepo(), cache=None,
                             settings=settings)
    return export_svc.AnalyticsExportService(
        service=query,
        jobs=FakeJobs() if jobs is None else jobs,
        publisher=FakePublisher(),
        settings=settings,
    )


@pytest.fixture(autouse=True)
def _restore_singleton():
    """The routes talk to a module singleton; one test's fake must not be the next's."""
    original = routes.analytics_export_service
    yield
    routes.analytics_export_service = original


def _client(service, context=None, audit=None):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v2")
    app.dependency_overrides[get_user_context] = lambda: (
        _ctx() if context is None else context)
    app.dependency_overrides[routes.get_optional_audit_logger] = lambda: audit
    routes.analytics_export_service = service
    return TestClient(app)


# ------------------------------------------------------------------ requests


def _plants(n):
    return [f"PLANT_{i:03d}" for i in range(n)]


def _export_body(**over):
    """96 cells: one day, 96 blocks, one scalar series. Streams."""
    body = {"from_date": "2026-08-01", "to_date": "2026-08-01",
            "from_block": 1, "to_block": 96, "date_grouping": "BLOCK_WISE",
            "datasets": ["ACTUAL_DEMAND"], "format": "csv"}
    body.update(over)
    return body


def _big_body(days=52, plants=200, fmt="csv"):
    """days x 96 blocks x plants cells, so the band is decided by arithmetic."""
    return _export_body(
        datasets=["DC_PLANT"], plants=_plants(plants), format=fmt,
        from_date="2026-01-01",
        to_date=(date(2026, 1, 1) + timedelta(days=days - 1)).isoformat())


def _demand_docs(*values):
    return [{"_id": {"d": "2026-08-01", "b": str(block)}, "ACTUAL_DEMAND": value}
            for block, value in enumerate(values, start=1)]


# --------------------------------------------------------------------- jobs


def _filters(**over):
    """A filter echo complete enough for `AnalyticsJobRepository.create`."""
    base = {"module": "analytics", "datasets": ["ACTUAL_DEMAND"],
            "from_date": "2026-08-01", "to_date": "2026-08-31",
            "from_block": 1, "to_block": 96, "date_grouping": "BLOCK_WISE",
            "block_grouping": "INDIVIDUAL", "block_band": "INDIVIDUAL",
            "custom_blocks": [], "plants": [], "exchanges": [], "format": "csv"}
    base.update(over)
    return base


def _job(job_id="job-1", user_id=OWNER, status="queued", **over):
    doc = build_job_document({
        "job_id": job_id, "user_id": user_id, "request_hash": f"hash-{job_id}",
        "filters": _filters(), "label": "Actual Demand · 01–31 Aug · blocks 1–96 · CSV",
        "cells_estimate": 297_600, "chunks_total": 6,
    })
    doc["status"] = status
    doc.update(over)
    return doc


def _ready_job(tmp_path, job_id="job-ready", user_id=OWNER, token="tok-ready",
               token_expires_at=None, body="block,value\n1,100\n", **over):
    path = tmp_path / f"{job_id}.csv"
    path.write_text(body)
    expiry = token_expires_at or (datetime.now(timezone.utc) + timedelta(hours=12))
    return _job(
        job_id=job_id, user_id=user_id, status="ready",
        chunks_done=6,
        result={"path": str(path), "filename": "analytics_2026-08-01_to_2026-08-31.csv",
                "bytes": len(body), "row_count": 2},
        download_token=token, token_expires_at=expiry, expires_at=expiry,
        finished_at=datetime.now(timezone.utc), worker_id="worker-7",
        parts=[{"idx": 0, "path": "/tmp/part-0.csv", "rows": 2, "bytes": 10,
                "sha256": "abc", "done_at": datetime.now(timezone.utc)}],
        **over)


def _download(client, job, token=None):
    job_id = job["job_id"]
    supplied = job.get("download_token") if token is None else token
    query = f"?token={supplied}" if supplied else ""
    return client.get(f"/api/v2/analytics/export/{job_id}/download{query}")


# ============================================================ POST /export


def test_a_small_csv_export_streams_with_a_download_filename():
    repo = StubRepo(docs={"demand_serve": _demand_docs(100.0, 101.0)})
    service = _service(repo=repo)
    response = _client(service).post("/api/v2/analytics/export",
                                     json=_export_body())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    # Without this header the browser renders the CSV in the tab instead of saving
    # it, and the file has no name.
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert 'filename="analytics_2026-08-01_to_2026-08-01.csv"' in disposition
    assert "100" in response.text
    assert service.jobs.created == [], "a 96-cell export must not reach the queue"


def test_a_large_export_is_queued_and_answers_202_with_a_job_id():
    jobs = FakeJobs()
    service = _service(jobs=jobs)
    response = _client(service).post("/api/v2/analytics/export",
                                     json=_big_body())

    # 202, not 200: the frontend opens the My Exports panel on this status.
    assert response.status_code == 202
    payload = response.json()["data"]
    assert payload["mode"] == "job"
    assert payload["job_id"] == jobs.created[0]["job_id"]
    assert payload["status"] == "queued"
    assert payload["deduped"] is False
    assert payload["chunks_total"] >= 1
    assert payload["estimated_cells"] == 52 * 96 * 200
    # The row is the ownership record every later download is checked against.
    assert jobs.created[0]["user_id"] == OWNER


def test_an_identical_queued_export_attaches_to_the_job_already_running():
    service = _service()
    client = _client(service)
    first = client.post("/api/v2/analytics/export", json=_big_body())
    existing = service.jobs.created[0]
    service.jobs.active = existing

    second = client.post("/api/v2/analytics/export", json=_big_body())

    assert second.status_code == 202
    payload = second.json()["data"]
    assert payload["job_id"] == first.json()["data"]["job_id"]
    assert payload["deduped"] is True
    assert len(service.jobs.created) == 1, "a repeat click must not queue a second run"


def test_an_inline_xlsx_export_returns_the_file_and_cleans_up_after_itself():
    repo = StubRepo(docs={"demand_serve": _demand_docs(100.0)})
    service = _service(repo=repo)
    temp_root = Path(tempfile.gettempdir())
    before = set(temp_root.glob(export_svc.TEMP_PREFIX + "*"))

    response = _client(service).post("/api/v2/analytics/export",
                                     json=_export_body(format="xlsx"))

    assert response.status_code == 200
    assert response.headers["content-type"] == export_svc.MEDIA_TYPES["xlsx"]
    assert 'filename="analytics_2026-08-01_to_2026-08-01.xlsx"' in \
        response.headers["content-disposition"]
    assert response.content[:2] == b"PK", "an xlsx is a zip; this is not one"
    assert len(response.content) > 1000, "an empty body is not a workbook"
    # The temp directory is deleted by a background task once the file has been
    # sent. Deleting it any earlier serves a truncated download; never deleting it
    # fills $TMPDIR one workbook at a time.
    leftovers = set(temp_root.glob(export_svc.TEMP_PREFIX + "*")) - before
    assert leftovers == set(), f"inline export left temp directories behind: {leftovers}"


def test_an_export_above_the_hard_ceiling_is_413_with_the_estimate_and_a_way_out():
    service = _service()
    body = _big_body(days=365, plants=300)          # 10,512,000 cells

    response = _client(service).post("/api/v2/analytics/export", json=body)

    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail["code"] == "ANALYTICS_BUDGET_EXCEEDED"
    assert detail["estimate"]["cells"] == 365 * 96 * 300
    assert detail["suggestions"], "a refusal with no alternative is a dead end"
    # Queueing this would only move the failure to the worker and spend the disk
    # budget getting there.
    assert service.jobs.created == []


def test_a_full_export_disk_is_refused_with_the_numbers_not_a_500():
    exhausted = export_svc.ExportStorageExhausted(
        5 * 1024 * 1024 * 1024, 5 * 1024 * 1024 * 1024, "/var/lib/epms/exports")
    response = _client(RaisingService(exhausted)).post(
        "/api/v2/analytics/export", json=_big_body())

    assert response.status_code == 507
    detail = response.json()["detail"]
    assert detail["code"] == "ANALYTICS_EXPORT_STORAGE_FULL"
    # The message has to name the directory and the knob, or the on-call engineer
    # is left guessing which volume filled up.
    assert "/var/lib/epms/exports" in detail["message"]
    assert "ANALYTICS_EXPORT_DISK_BUDGET_MB" in detail["message"]


def test_a_failed_workbook_write_is_a_500_that_says_what_failed():
    response = _client(RaisingService(ExportWriteError("disk went away"))).post(
        "/api/v2/analytics/export", json=_export_body(format="xlsx"))

    assert response.status_code == 500
    assert "disk went away" in response.json()["detail"]["message"]


def test_exporting_a_dataset_with_no_storage_is_refused_by_name():
    service = _service()
    response = _client(service).post("/api/v2/analytics/export",
                                     json=_export_body(datasets=["WEEKLY_FORECAST"]))

    assert response.status_code == 422
    assert "WEEKLY_FORECAST" in response.json()["detail"]["datasets"]


def test_an_unauthenticated_export_is_refused_before_a_job_can_be_created():
    service = _service()
    response = _client(service, context=ANONYMOUS).post(
        "/api/v2/analytics/export", json=_big_body())

    # `get_user_context` answers "anonymous" rather than raising, so a route that
    # trusts it queues a job nobody owns — and an unowned job is downloadable by
    # the next caller who guesses its id.
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "ANALYTICS_AUTH_REQUIRED"
    assert service.jobs.created == []


# ========================================================== GET /exports


def test_the_export_list_is_scoped_to_the_caller():
    jobs = FakeJobs(jobs=[_job("mine-1"), _job("mine-2", status="ready"),
                          _job("theirs", user_id=INTRUDER)])
    response = _client(_service(jobs=jobs)).get("/api/v2/analytics/exports")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert [j["job_id"] for j in payload["jobs"]] == ["mine-1", "mine-2"]
    assert payload["count"] == 2
    # Scoped in the query, not filtered after the fact: the repository never sees
    # another user's rows.
    assert jobs.listed == [(OWNER, routes.DEFAULT_EXPORT_LIST_LIMIT)]


def test_an_unauthenticated_export_list_is_401_not_an_anonymous_users_jobs():
    jobs = FakeJobs(jobs=[_job("mine-1")])
    response = _client(_service(jobs=jobs), context=ANONYMOUS).get(
        "/api/v2/analytics/exports")

    assert response.status_code == 401
    assert jobs.listed == []


# ==================================================== GET /export/{id}


def test_a_status_payload_reports_progress_without_leaking_the_server_path(tmp_path):
    job = _ready_job(tmp_path)
    response = _client(_service(jobs=FakeJobs(jobs=[job]))).get(
        "/api/v2/analytics/export/job-ready")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["status"] == "ready"
    assert payload["chunks_done"] == 6 and payload["chunks_total"] == 6
    assert payload["progress"] == 1.0
    assert payload["result"]["filename"].endswith(".csv")
    assert payload["download_token"] == "tok-ready"

    # The row carries absolute server paths, a worker id and the temp part files.
    # None of that is the client's business, and a path is a gift to anyone probing
    # for a traversal.
    blob = response.text
    assert str(tmp_path) not in blob
    assert "worker-7" not in blob
    assert "parts" not in payload


def test_polling_another_users_job_is_403():
    jobs = FakeJobs(jobs=[_job("theirs", user_id=INTRUDER, status="running")])
    response = _client(_service(jobs=jobs)).get("/api/v2/analytics/export/theirs")

    # Labels name datasets, date ranges and plant counts: the status route leaks
    # what someone else is analysing unless it is scoped too.
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ANALYTICS_EXPORT_FORBIDDEN"
    assert "Actual Demand" not in response.text, "the label leaked through the refusal"


def test_an_unknown_job_id_is_404():
    response = _client(_service(jobs=FakeJobs())).get(
        "/api/v2/analytics/export/no-such-job")
    assert response.status_code == 404


# =========================================== GET /export/{id}/download


def test_a_ready_export_downloads_with_its_stored_filename(tmp_path):
    job = _ready_job(tmp_path)
    response = _download(_client(_service(jobs=FakeJobs(jobs=[job]))), job)

    assert response.status_code == 200
    assert response.text == "block,value\n1,100\n"
    assert 'filename="analytics_2026-08-01_to_2026-08-31.csv"' in \
        response.headers["content-disposition"]
    assert response.headers["content-type"].startswith("text/csv")


def test_downloading_another_users_job_is_403(tmp_path):
    """The authorization boundary of this whole feature."""
    job = _ready_job(tmp_path, user_id=INTRUDER)
    audit = FakeAudit()
    client = _client(_service(jobs=FakeJobs(jobs=[job])), audit=audit)

    # The caller holds the job's own valid token — they still may not have the file.
    response = _download(client, job)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ANALYTICS_EXPORT_FORBIDDEN"
    assert "block,value" not in response.text, "the file leaked through the refusal"
    # A refused download is the security event worth having in audit_db.
    assert audit.actions() == ["analytics_export_download_denied"]
    assert audit.entries[0]["success"] is False


def test_a_download_without_a_token_is_403(tmp_path):
    job = _ready_job(tmp_path)
    response = _download(_client(_service(jobs=FakeJobs(jobs=[job]))), job, token="")
    assert response.status_code == 403


def test_a_download_with_the_wrong_token_is_403(tmp_path):
    job = _ready_job(tmp_path)
    response = _download(_client(_service(jobs=FakeJobs(jobs=[job]))), job,
                         token="tok-ready-but-not-quite")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ANALYTICS_EXPORT_TOKEN_INVALID"


def test_an_expired_token_is_403(tmp_path):
    job = _ready_job(tmp_path,
                     token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    response = _download(_client(_service(jobs=FakeJobs(jobs=[job]))), job)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ANALYTICS_EXPORT_TOKEN_EXPIRED"


def test_a_naive_token_expiry_is_read_as_utc_in_both_directions(tmp_path):
    """Motor returns naive datetimes; reading one as local time shifts every expiry.

    That is not hypothetical here — the same mistake made every EPMS access token
    last six hours instead of thirty minutes. IST is UTC+5:30, so a naive expiry
    four hours out reads as already-past if it is treated as local, and one four
    hours behind reads as still-valid.
    """
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    naive_future = now_utc + timedelta(hours=4)
    naive_past = now_utc - timedelta(hours=4)

    live = _ready_job(tmp_path, job_id="job-live", token_expires_at=naive_future)
    dead = _ready_job(tmp_path, job_id="job-dead", token_expires_at=naive_past)
    client = _client(_service(jobs=FakeJobs(jobs=[live, dead])))

    assert _download(client, live).status_code == 200
    assert _download(client, dead).status_code == 403


def test_an_expired_job_is_410_with_the_filters_that_would_rebuild_it(tmp_path):
    job = _job("job-old", status="expired", download_token=None,
               expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    response = _download(_client(_service(jobs=FakeJobs(jobs=[job]))), job, token="x")

    # 410, not 404: the row is deliberately kept for 90 days after the file goes,
    # and Regenerate is the whole reason it is kept.
    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["code"] == "ANALYTICS_EXPORT_EXPIRED"
    assert detail["regenerate"]["filters"]["datasets"] == ["ACTUAL_DEMAND"]
    assert detail["regenerate"]["job_id"] == "job-old"


def test_a_ready_job_whose_file_has_vanished_is_410_not_a_500(tmp_path):
    job = _ready_job(tmp_path, job_id="job-gone")
    Path(job["result"]["path"]).unlink()
    response = _download(_client(_service(jobs=FakeJobs(jobs=[job]))), job)

    # The reaper deletes files and the row says `ready` until the sweep catches up.
    # A FileResponse on a missing path is an unhandled OSError, i.e. a 500.
    assert response.status_code == 410
    assert response.json()["detail"]["regenerate"]["filters"]


def test_downloading_a_job_that_is_still_running_is_409_not_404(tmp_path):
    job = _job("job-running", status="running", chunks_done=2)
    response = _download(_client(_service(jobs=FakeJobs(jobs=[job]))), job, token="x")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "ANALYTICS_EXPORT_NOT_READY"
    assert detail["status"] == "running"


def test_downloading_a_failed_job_returns_the_reason_it_failed(tmp_path):
    job = _job("job-bad", status="failed",
               error={"code": "pipeline_failed", "message": "supply_serve timed out",
                      "dataset": "DC_PLANT"})
    response = _download(_client(_service(jobs=FakeJobs(jobs=[job]))), job, token="x")

    assert response.status_code == 409
    assert "supply_serve timed out" in str(response.json()["detail"])


def test_every_successful_download_writes_an_audit_entry(tmp_path):
    job = _ready_job(tmp_path)
    audit = FakeAudit()
    _download(_client(_service(jobs=FakeJobs(jobs=[job])), audit=audit), job)

    assert audit.actions() == ["analytics_export_download"]
    entry = audit.entries[0]
    assert entry["success"] is True
    assert entry["user_id"] == OWNER
    assert entry["service"] == "processing"
    assert entry["job_id"] == "job-ready"
    assert entry["correlation_id"] == "test-corr"


def test_an_audit_failure_does_not_deny_a_download_that_was_authorized(tmp_path):
    job = _ready_job(tmp_path)
    audit = FakeAudit(raises=RuntimeError("audit queue is down"))
    response = _download(_client(_service(jobs=FakeJobs(jobs=[job])), audit=audit), job)

    # The audit queue falls back to Mongo and can still fail; taking downloads with
    # it would turn a logging outage into a feature outage.
    assert response.status_code == 200
    assert response.text == "block,value\n1,100\n"


def test_an_unauthenticated_download_is_401(tmp_path):
    job = _ready_job(tmp_path, user_id="anonymous")
    response = _download(
        _client(_service(jobs=FakeJobs(jobs=[job])), context=ANONYMOUS), job)

    # The row's user_id would *match* the anonymous placeholder, so without an
    # explicit refusal this is the one case where ownership accidentally passes.
    assert response.status_code == 401


# ===================================== POST /export/{id}/cancel


def test_cancelling_someone_elses_job_is_403():
    jobs = FakeJobs(jobs=[_job("theirs", user_id=INTRUDER, status="running")])
    response = _client(_service(jobs=jobs)).post(
        "/api/v2/analytics/export/theirs/cancel")

    assert response.status_code == 403
    assert jobs.marks == [], "a refused cancel must not touch the row"


def test_cancelling_a_queued_job_marks_it_cancelled():
    jobs = FakeJobs(jobs=[_job("job-1", status="queued")])
    response = _client(_service(jobs=jobs)).post(
        "/api/v2/analytics/export/job-1/cancel")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["status"] == "cancelled"
    assert payload["changed"] is True
    assert jobs.marks == [("job-1", "cancelled", {})]


def test_cancelling_a_finished_export_is_409_not_a_silent_200():
    jobs = FakeJobs(jobs=[_job("job-done", status="ready")])
    response = _client(_service(jobs=jobs)).post(
        "/api/v2/analytics/export/job-done/cancel")

    # The defect this avoids is `update-status` on a lapsed bid: it matches nothing,
    # logs a skip and answers 200, so the user sees success and nothing changed.
    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "ready"
    assert jobs.marks == []


def test_cancelling_an_already_cancelled_job_is_idempotent():
    jobs = FakeJobs(jobs=[_job("job-x", status="cancelled")])
    response = _client(_service(jobs=jobs)).post(
        "/api/v2/analytics/export/job-x/cancel")

    assert response.status_code == 200
    assert response.json()["data"]["changed"] is False
    assert jobs.marks == []


def test_a_cancel_that_loses_the_race_is_not_reported_as_a_success():
    jobs = FakeJobs(jobs=[_job("job-1", status="queued")], mark_result=False)
    response = _client(_service(jobs=jobs)).post(
        "/api/v2/analytics/export/job-1/cancel")

    # `mark` returning False means the write matched nothing — the job finished or
    # the row went away between the read and the update.
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ANALYTICS_EXPORT_CANCEL_FAILED"


def test_cancelling_an_unknown_job_is_404():
    response = _client(_service(jobs=FakeJobs())).post(
        "/api/v2/analytics/export/nope/cancel")
    assert response.status_code == 404


# ------------------------------------------------------------- registration


def test_the_five_export_paths_are_on_the_router_main_already_includes():
    paths = {(r.path, tuple(sorted(r.methods))) for r in routes.router.routes}

    assert ("/analytics/export", ("POST",)) in paths
    assert ("/analytics/exports", ("GET",)) in paths
    assert ("/analytics/export/{job_id}", ("GET",)) in paths
    assert ("/analytics/export/{job_id}/download", ("GET",)) in paths
    assert ("/analytics/export/{job_id}/cancel", ("POST",)) in paths


def test_the_download_route_is_not_excluded_from_authentication():
    # A file route is the tempting one to make public so an <a href> can fetch it.
    # The token is a second factor, never a replacement for the session.
    main_py = Path(__file__).resolve().parents[2] / "services/processing/app/main.py"
    for line in main_py.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith('"') and "analytics" in stripped:
            pytest.fail(f"analytics appears in a main.py path list: {stripped}")
