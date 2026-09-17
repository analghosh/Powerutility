"""Export mode selection and job enqueue.

This layer makes one decision — *serve it now, or queue it* — and four things
about that decision are load-bearing enough that a silent regression in any of
them would not show up until a user complained.

1. **The threshold differs by format, and the difference is not cosmetic.** CSV
   yields rows as it goes, so a 500k-cell stream costs constant memory and a
   disconnect wastes nothing. XLSX emits no bytes at all until openpyxl finalises
   the zip central directory, so the same request holds a web worker for a minute
   with nothing to show for it. A single shared threshold would either queue CSVs
   that could have streamed or block a worker on a large XLSX. `MODE_THRESHOLDS`
   is the whole point of this module and these tests are what hold the two numbers
   apart.

2. **The disk budget is checked before the row is written, not after.** This is a
   single EC2 box with no HA; filling the root volume takes the bidding screens
   down with it, and bidding is the money path. A job that is created and *then*
   found to be unaffordable is a job the worker will pick up.

3. **RabbitMQ is a doorbell, not a dependency.** `USE_RABBITMQ_JOBS` is false
   locally and the broker path has never worked in this codebase, so a publish
   failure is the *normal* case, not an edge case. If a broker outage could fail
   the enqueue, exports would stop working on the box where they have to work.
   The blocking `publish_job_trigger` must never be reached either: it opens a
   pika TCP connection inline, and a blocking call in an async handler is exactly
   the defect that makes a login rush 401 the whole platform.

4. **The stored filters must be complete.** The row outlives its file by 87 days
   so that Regenerate can rebuild the export from the row alone. The repository
   enforces that with `IncompleteFilters`, so the fake job repository here runs
   the real `build_job_document` rather than accepting any dict — otherwise this
   test would pass against a service that stores half a filter set and the failure
   would surface days later, at Regenerate time, in front of the person who
   needed it.

Mongo, the broker and the clock are all mocked; nothing here touches a database.
"""
import inspect
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.core.config import settings as real_settings  # noqa: E402
from services.processing.app.v2.analytics import budget  # noqa: E402
from services.processing.app.v2.analytics.models import (  # noqa: E402
    AnalyticsQueryRequest, ExportRequest,
)
from services.processing.app.v2.repository.analytics_job_repository import (  # noqa: E402
    ACTIVE_STATUSES, REQUIRED_FILTER_KEYS, build_job_document,
)
from services.processing.app.v2.services import analytics_service as query_svc  # noqa: E402
from services.processing.app.v2.services import analytics_export_service as svc  # noqa: E402


# --------------------------------------------------------------------- fakes


class RecordingRepo:
    """The analytics Mongo repository, reduced to a call recorder."""

    def __init__(self, docs=None):
        self.docs = docs or {}
        self.calls = []

    async def run_pipeline(self, collection, pipeline, max_time_ms=None):
        self.calls.append(collection)
        return self.docs.get(collection, [])


class FakeJobs:
    """The job queue, in memory.

    `create` runs the *real* `build_job_document`, so an incomplete filter echo
    raises `IncompleteFilters` here exactly as it would against Mongo.
    """

    def __init__(self, active=None):
        self.active = active
        self.created = []
        self.hash_lookups = []

    async def find_active_by_hash(self, user_id, request_hash):
        self.hash_lookups.append((user_id, request_hash))
        if self.active and self.active.get("request_hash") == request_hash \
                and self.active.get("user_id") == user_id:
            return self.active
        return None

    async def create(self, job):
        doc = build_job_document(job)
        self.created.append(doc)
        return doc["job_id"]


class FakePublisher:
    """A broker that records the async publish and detonates on the blocking one."""

    def __init__(self, result=True, raises=None):
        self.result = result
        self.raises = raises
        self.calls = []
        self.blocking_calls = []

    async def publish_job_trigger_async(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return self.result

    def publish_job_trigger(self, **kwargs):  # pragma: no cover - must never run
        self.blocking_calls.append(kwargs)
        raise AssertionError(
            "The blocking publisher was called from an async handler — this opens "
            "a pika TCP connection on the event loop.")


def _settings(**over):
    """A settings stand-in carrying the real analytics defaults plus overrides."""
    base = {
        name: getattr(real_settings, name)
        for name in dir(real_settings)
        if name.startswith("ANALYTICS_") or name.startswith("MONGODB_ANALYTICS")
    }
    base.update(over)
    return SimpleNamespace(**base)


def _service(jobs=None, publisher=None, settings=None, repo=None):
    settings = settings or _settings()
    query = query_svc.AnalyticsService(repository=repo or RecordingRepo(),
                                       settings=settings)
    return svc.AnalyticsExportService(
        service=query,
        jobs=jobs if jobs is not None else FakeJobs(),
        publisher=publisher if publisher is not None else FakePublisher(),
        settings=settings,
    )


# ------------------------------------------------------------------ requests

#: Per-plant so the series count is the plant count, which is the only knob that
#: moves the cell estimate in round numbers: cells = days x blocks x plants.
PLANT_DATASET = "DC_PLANT"
PLANT_PERMISSION = "analytics:supply"


def _plants(n):
    return [f"PLANT_{i:03d}" for i in range(n)]


def _request(days=52, plants=20, fmt="csv", **over):
    """`days x 96 blocks x plants` cells, so the band is chosen by arithmetic."""
    base = {
        "from_date": date(2026, 1, 1),
        "to_date": date(2026, 1, 1) + timedelta(days=days - 1),
        "from_block": 1,
        "to_block": 96,
        "date_grouping": "BLOCK_WISE",
        "datasets": [PLANT_DATASET],
        "plants": _plants(plants),
        "format": fmt,
    }
    base.update(over)
    return ExportRequest(**base)


def _context(user_id="u-1", permissions=(PLANT_PERMISSION,)):
    return {"user_id": user_id, "permissions": list(permissions)}


def _cells(request, service=None):
    return (service or _service()).service.estimate_only(request).cells


# --------------------------------------------------------- the decision table


@pytest.mark.asyncio
async def test_a_csv_under_the_stream_ceiling_is_streamed():
    service = _service()
    request = _request(days=52, plants=80, fmt="csv")       # 399,360 cells
    assert 150_000 < _cells(request, service) <= 500_000

    outcome = await service.export(request, _context())

    assert outcome["mode"] == svc.MODE_STREAM
    assert inspect.isgenerator(outcome["iterator"])
    assert outcome["media_type"] == svc.MEDIA_TYPES["csv"]
    assert outcome["filename"].endswith(".csv")
    assert service.jobs.created == []


@pytest.mark.asyncio
async def test_a_csv_over_the_stream_ceiling_is_queued():
    service = _service()
    request = _request(days=65, plants=96, fmt="csv")       # 599,040 cells
    assert _cells(request, service) > 500_000

    outcome = await service.export(request, _context())

    assert outcome["mode"] == svc.MODE_JOB
    assert outcome["job_id"]
    assert len(service.jobs.created) == 1


@pytest.mark.asyncio
async def test_an_xlsx_under_the_inline_ceiling_is_written_in_request():
    service = _service()
    request = _request(days=52, plants=20, fmt="xlsx")      # 99,840 cells
    assert _cells(request, service) <= 150_000

    outcome = await service.export(request, _context())

    assert outcome["mode"] == svc.MODE_INLINE
    assert os.path.isfile(outcome["path"])
    assert os.path.getsize(outcome["path"]) > 0
    assert outcome["filename"].endswith(".xlsx")
    assert outcome["media_type"] == svc.MEDIA_TYPES["xlsx"]
    assert service.jobs.created == []
    svc.cleanup(outcome)
    assert not os.path.exists(outcome["path"])


@pytest.mark.asyncio
async def test_an_xlsx_over_the_inline_ceiling_is_queued():
    service = _service()
    request = _request(days=52, plants=40, fmt="xlsx")      # 199,680 cells
    assert 150_000 < _cells(request, service) <= 500_000

    outcome = await service.export(request, _context())

    assert outcome["mode"] == svc.MODE_JOB
    assert len(service.jobs.created) == 1


@pytest.mark.asyncio
async def test_the_same_question_streams_as_csv_and_queues_as_xlsx():
    """The two formats must not share one threshold — §8's whole reason for the
    table. 200k cells is a fine CSV stream and a worker-blocking XLSX."""
    service = _service()
    as_csv = await service.export(_request(days=52, plants=40, fmt="csv"), _context())
    as_xlsx = await service.export(_request(days=52, plants=40, fmt="xlsx"), _context())

    assert as_csv["mode"] == svc.MODE_STREAM
    assert as_xlsx["mode"] == svc.MODE_JOB


def test_mode_thresholds_are_the_configured_cell_bands():
    service = _service()
    assert service.threshold_for("csv") == real_settings.ANALYTICS_CELLS_INLINE_CAPPED
    assert service.threshold_for("xlsx") == real_settings.ANALYTICS_CELLS_INLINE
    assert svc.MODE_THRESHOLDS["csv"] > svc.MODE_THRESHOLDS["xlsx"]


def test_a_threshold_override_moves_the_boundary():
    """The constant is the default; the method reads the injected settings."""
    service = _service(settings=_settings(ANALYTICS_CELLS_INLINE_CAPPED=1_000))
    assert service.threshold_for("csv") == 1_000


# ----------------------------------------------------------------- the queue


@pytest.mark.asyncio
async def test_an_identical_in_flight_request_attaches_to_the_existing_job():
    jobs = FakeJobs()
    service = _service(jobs=jobs)
    request = _request(days=65, plants=96, fmt="csv")

    first = await service.export(request, _context())
    jobs.active = dict(jobs.created[0], status=ACTIVE_STATUSES[0])

    second = await service.export(request, _context())

    assert second["job_id"] == first["job_id"]
    assert second["deduped"] is True
    assert len(jobs.created) == 1, "a duplicate request created a second job"


@pytest.mark.asyncio
async def test_another_users_identical_request_gets_its_own_job():
    """Dedupe is scoped by owner — the job row is the ownership record."""
    jobs = FakeJobs()
    service = _service(jobs=jobs)
    request = _request(days=65, plants=96, fmt="csv")

    await service.export(request, _context(user_id="u-1"))
    jobs.active = dict(jobs.created[0], status=ACTIVE_STATUSES[0])

    other = await service.export(request, _context(user_id="u-2"))

    assert other["deduped"] is False
    assert len(jobs.created) == 2


@pytest.mark.asyncio
async def test_csv_and_xlsx_of_the_same_question_are_different_jobs():
    """The cache key ignores format on purpose — the same data. The job hash must
    not, or a queued CSV would hand the user an XLSX."""
    jobs = FakeJobs()
    service = _service(jobs=jobs)

    await service.export(_request(days=65, plants=96, fmt="csv"), _context())
    jobs.active = dict(jobs.created[0], status=ACTIVE_STATUSES[0])

    other = await service.export(_request(days=65, plants=96, fmt="xlsx"), _context())

    assert other["deduped"] is False
    assert len(jobs.created) == 2


@pytest.mark.asyncio
async def test_a_queued_export_needs_an_owner():
    service = _service()
    anonymous = {"permissions": [PLANT_PERMISSION]}
    with pytest.raises(ValueError):
        await service.export(_request(days=65, plants=96, fmt="csv"), anonymous)


@pytest.mark.asyncio
async def test_a_stream_does_not_need_an_owner():
    """A small CSV is served straight back; there is no row to own."""
    service = _service()
    anonymous = {"permissions": [PLANT_PERMISSION]}
    outcome = await service.export(_request(days=1, plants=1, fmt="csv"), anonymous)
    assert outcome["mode"] == svc.MODE_STREAM


@pytest.mark.asyncio
async def test_the_job_carries_its_estimate_and_a_chunk_count():
    jobs = FakeJobs()
    service = _service(jobs=jobs)
    request = _request(days=65, plants=96, fmt="csv")

    await service.export(request, _context())
    doc = jobs.created[0]

    assert doc["cells_estimate"] == _cells(request, service)
    assert doc["chunks_total"] >= 1
    assert doc["chunks_total"] == -(-doc["cells_estimate"] // svc.CHUNK_CELLS)
    assert doc["status"] == "queued"
    assert doc["label"]


# ------------------------------------------------------------- broker doorbell


@pytest.mark.asyncio
async def test_a_publish_failure_still_returns_a_job():
    """USE_RABBITMQ_JOBS is false locally, so False is the normal return."""
    publisher = FakePublisher(result=False)
    service = _service(publisher=publisher)

    outcome = await service.export(_request(days=65, plants=96, fmt="csv"), _context())

    assert outcome["mode"] == svc.MODE_JOB
    assert outcome["published"] is False
    assert len(service.jobs.created) == 1


@pytest.mark.asyncio
async def test_a_broker_that_raises_still_returns_a_job():
    publisher = FakePublisher(raises=RuntimeError("connection refused"))
    service = _service(publisher=publisher)

    outcome = await service.export(_request(days=65, plants=96, fmt="csv"), _context())

    assert outcome["mode"] == svc.MODE_JOB
    assert outcome["published"] is False


@pytest.mark.asyncio
async def test_the_publish_is_awaited_and_never_the_blocking_variant():
    publisher = FakePublisher(result=True)
    service = _service(publisher=publisher)

    outcome = await service.export(_request(days=65, plants=96, fmt="csv"), _context())

    assert outcome["published"] is True
    assert publisher.blocking_calls == []
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["job_type"] == svc.JOB_TYPE
    assert publisher.calls[0]["payload"]["job_id"] == outcome["job_id"]


@pytest.mark.asyncio
async def test_the_row_is_written_before_the_doorbell_is_rung():
    """Publish first and a fast worker looks up a job that does not exist yet."""
    order = []

    class OrderedJobs(FakeJobs):
        async def create(self, job):
            order.append("create")
            return await super().create(job)

    class OrderedPublisher(FakePublisher):
        async def publish_job_trigger_async(self, **kwargs):
            order.append("publish")
            return await super().publish_job_trigger_async(**kwargs)

    service = _service(jobs=OrderedJobs(), publisher=OrderedPublisher())
    await service.export(_request(days=65, plants=96, fmt="csv"), _context())

    assert order == ["create", "publish"]


# ---------------------------------------------------------------- disk budget


@pytest.mark.asyncio
async def test_the_disk_budget_refuses_the_job_before_it_is_created(tmp_path):
    (tmp_path / "old_export.csv").write_bytes(b"x" * (2 * 1024 * 1024))
    jobs = FakeJobs()
    service = _service(jobs=jobs, settings=_settings(
        ANALYTICS_EXPORT_DIR=str(tmp_path), ANALYTICS_EXPORT_DISK_BUDGET_MB=1))

    with pytest.raises(svc.ExportStorageExhausted) as exc:
        await service.export(_request(days=65, plants=96, fmt="csv"), _context())

    assert jobs.created == [], "an unaffordable job was queued anyway"
    message = str(exc.value)
    assert "ANALYTICS_EXPORT_DISK_BUDGET_MB" in message
    assert str(tmp_path) in message


@pytest.mark.asyncio
async def test_a_budget_with_room_left_queues_normally(tmp_path):
    (tmp_path / "old_export.csv").write_bytes(b"x" * 1024)
    service = _service(settings=_settings(
        ANALYTICS_EXPORT_DIR=str(tmp_path), ANALYTICS_EXPORT_DISK_BUDGET_MB=1))

    outcome = await service.export(_request(days=65, plants=96, fmt="csv"), _context())
    assert outcome["mode"] == svc.MODE_JOB


@pytest.mark.asyncio
async def test_a_full_disk_does_not_block_a_streamed_export(tmp_path):
    """Streaming writes nothing to the export directory, so the budget is
    irrelevant to it. Refusing here would take the whole screen down."""
    (tmp_path / "old_export.csv").write_bytes(b"x" * (2 * 1024 * 1024))
    service = _service(settings=_settings(
        ANALYTICS_EXPORT_DIR=str(tmp_path), ANALYTICS_EXPORT_DISK_BUDGET_MB=1))

    outcome = await service.export(_request(days=1, plants=1, fmt="csv"), _context())
    assert outcome["mode"] == svc.MODE_STREAM


def test_a_missing_export_directory_counts_as_empty(tmp_path):
    """ANALYTICS_EXPORT_DIR does not exist until the first job writes into it."""
    service = _service(settings=_settings(
        ANALYTICS_EXPORT_DIR=str(tmp_path / "never-created")))
    assert service.disk_usage_bytes() == 0


def test_a_zero_budget_disables_the_check(tmp_path):
    service = _service(settings=_settings(
        ANALYTICS_EXPORT_DIR=str(tmp_path), ANALYTICS_EXPORT_DISK_BUDGET_MB=0))
    assert service.disk_budget_bytes() == 0
    service.check_disk_budget()  # must not raise


# ------------------------------------------------------- stored filter echo


@pytest.mark.asyncio
async def test_the_stored_filters_are_complete_enough_to_regenerate():
    jobs = FakeJobs()
    service = _service(jobs=jobs)

    await service.export(_request(days=65, plants=96, fmt="csv"), _context())
    filters = jobs.created[0]["filters"]

    for key in REQUIRED_FILTER_KEYS:
        assert key in filters, f"stored filters are missing {key}"
    assert filters["format"] == "csv"
    # bson cannot encode datetime.date at all.
    assert isinstance(filters["from_date"], str)
    assert isinstance(filters["to_date"], str)


@pytest.mark.asyncio
async def test_custom_block_ranges_are_stored_with_the_job():
    jobs = FakeJobs()
    service = _service(jobs=jobs)
    # 365 days x 2 custom buckets x 800 plants = 584,000 cells, so it queues.
    request = _request(days=365, plants=800, fmt="csv",
                       block_band="CUSTOM", custom_blocks=[[1, 48], [49, 96]])
    assert _cells(request, service) > 500_000

    await service.export(request, _context())
    filters = jobs.created[0]["filters"]

    assert filters["block_grouping"] == "CUSTOM"
    assert filters["custom_blocks"] == [[1, 48], [49, 96]]


@pytest.mark.asyncio
async def test_a_stored_job_echo_replays_as_a_request():
    """Regenerate hands back the row's own filters — `block_grouping`, not
    `block_band`, and ISO date strings."""
    jobs = FakeJobs()
    service = _service(jobs=jobs)
    await service.export(_request(days=65, plants=96, fmt="csv"), _context())
    echo = jobs.created[0]["filters"]

    replayed = svc.as_export_request(echo)

    assert isinstance(replayed, ExportRequest)
    assert replayed.format == "csv"
    assert replayed.block_band == "INDIVIDUAL"
    assert replayed.from_date == date(2026, 1, 1)


def test_a_plain_query_request_exports_as_csv():
    plain = AnalyticsQueryRequest(
        from_date=date(2026, 1, 1), to_date=date(2026, 1, 1),
        datasets=["ACTUAL_DEMAND"])
    assert svc.as_export_request(plain).format == "csv"


# ----------------------------------------------------------------- permissions


@pytest.mark.asyncio
async def test_a_denied_dataset_never_reaches_the_queued_job():
    """Dropped in the service, per §13 — a queued job runs without the caller's
    token, so a dataset that survives into the row is a dataset the worker will
    happily export to someone not entitled to it."""
    jobs = FakeJobs()
    service = _service(jobs=jobs)
    request = _request(days=65, plants=96, fmt="csv",
                       datasets=[PLANT_DATASET, "ACTUAL_DEMAND"])

    outcome = await service.export(request, _context(permissions=[PLANT_PERMISSION]))

    assert jobs.created[0]["filters"]["datasets"] == [PLANT_DATASET]
    assert outcome["denied_datasets"] == ["ACTUAL_DEMAND"]


@pytest.mark.asyncio
async def test_the_estimate_prices_only_the_permitted_datasets():
    """Otherwise a denied dataset could push an affordable export into the queue."""
    jobs = FakeJobs()
    service = _service(jobs=jobs)
    permitted = _request(days=52, plants=40, fmt="csv")
    both = _request(days=52, plants=40, fmt="csv",
                    datasets=[PLANT_DATASET, "ACTUAL_DEMAND"])

    outcome = await service.export(both, _context(permissions=[PLANT_PERMISSION]))

    assert outcome["mode"] == svc.MODE_STREAM
    assert outcome["estimated_cells"] == _cells(permitted, service)


# ------------------------------------------------------------- hard refusal


@pytest.mark.asyncio
async def test_an_absurd_request_is_refused_rather_than_queued():
    """Above ANALYTICS_CELLS_MAX the answer is no — queueing a 10M-cell export on
    a single box with a 5GB budget only moves the failure to the worker."""
    jobs = FakeJobs()
    service = _service(jobs=jobs)
    request = _request(days=365, plants=300, fmt="csv")     # 10,512,000 cells
    assert _cells(request, service) > real_settings.ANALYTICS_CELLS_MAX

    with pytest.raises(query_svc.BudgetExceeded) as exc:
        await service.export(request, _context())

    assert jobs.created == []
    assert exc.value.band == budget.BAND_REFUSED
    assert exc.value.estimate.suggestions


# --------------------------------------------------------------- stream body


@pytest.mark.asyncio
async def test_the_stream_carries_the_applied_filter_header():
    service = _service()
    outcome = await service.export(_request(days=1, plants=2, fmt="csv"), _context())
    lines = list(outcome["iterator"])

    assert lines[0].startswith("# ")
    header = "".join(lines[:12])
    assert "Format: csv" in header
    assert "Date range: 2026-01-01 to 2026-01-01" in header
    assert "dataset,label,entity,unit,date,block,value\n" in lines


@pytest.mark.asyncio
async def test_a_stream_is_not_produced_until_it_is_consumed():
    """`csv_rows` is a generator; the response is not serialised into memory by
    the service before the route hands it to StreamingResponse."""
    service = _service()
    outcome = await service.export(_request(days=1, plants=2, fmt="csv"), _context())
    assert inspect.getgeneratorstate(outcome["iterator"]) == inspect.GEN_CREATED


@pytest.mark.asyncio
async def test_the_outcome_reports_how_the_query_was_served():
    service = _service()
    outcome = await service.export(_request(days=1, plants=2, fmt="csv"), _context())
    assert outcome["meta"]["cache"] in (query_svc.CACHE_HIT, query_svc.CACHE_MISS)
    assert outcome["meta"]["denied_datasets"] == []
