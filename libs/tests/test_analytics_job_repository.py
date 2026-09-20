"""The export job row is the only thing that outlives the file it produced.

The file is deleted at 72 hours; the row lives 90 days. Everything the Regenerate
button needs must therefore be inside the row itself — specifically inside
``filters``, which has to be a complete, self-contained description of the query.
A user who exported 34 named plants over five months will not remember which 34
three days later, and there is nowhere else to recover them from: the page they
picked them on is long gone and the request was never logged in full.

So these tests are mostly about what ``create`` refuses. A job written with half a
filter set is not a job, it is a row that will fail at Regenerate time, days later,
in front of the person who needed it.

The rest locks the reaper arithmetic (a stale worker must not be able to resurrect a
job another worker has taken over), and the two update shapes that silently destroy
work if they are written as ``$set``: ``record_part`` must append, and
``expire_before`` must never delete the row.

Mongo is mocked — ``.collection`` is an AsyncMock and the assertions are on
``call_args``, the house convention (see tests/libs/test_forecast_serve_repository.py).
"""
import copy
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.repository import (  # noqa: E402
    analytics_job_repository as jobs,
)

FIXED_NOW = datetime(2026, 9, 17, 10, 30, 0, tzinfo=timezone.utc)

# The complete filter set, exactly as the spec's §8 schema demands it: enough to
# rebuild the query without the originating request.
FILTERS = {
    "datasets": ["ACTUAL_DEMAND", "DC_PLANT"],
    "from_date": "2026-04-01",
    "to_date": "2026-08-31",
    "from_block": 1,
    "to_block": 96,
    "date_grouping": "BLOCK_WISE",
    "block_grouping": "INDIVIDUAL",
    "plants": ["plant-a", "plant-b"],
    "format": "xlsx",
}

JOB = {
    "user_id": "u-1",
    "request_hash": "h-abc",
    "filters": FILTERS,
    "label": "Actual Demand + Plant-wise DC · 01 Apr–31 Aug · blocks 1–96 · XLSX",
    "cells_estimate": 499_392,
    "chunks_total": 11,
}


@pytest.fixture
def repo(monkeypatch):
    monkeypatch.setattr(jobs, "_utcnow", lambda: FIXED_NOW)
    r = jobs.AnalyticsJobRepository()
    r.collection = AsyncMock()
    return r


def _job(**overrides):
    j = copy.deepcopy(JOB)
    j.update(overrides)
    return j


def _inserted(repo):
    return repo.collection.insert_one.call_args[0][0]


# ----------------------------------------------------- create: lifecycle ---

@pytest.mark.asyncio
async def test_create_stamps_the_queued_lifecycle_defaults(repo):
    job_id = await repo.create(_job())

    doc = _inserted(repo)
    assert doc["job_id"] == job_id
    assert doc["status"] == jobs.STATUS_QUEUED
    assert doc["attempts"] == 0
    assert doc["max_attempts"] == 3
    assert doc["chunks_done"] == 0
    assert doc["parts"] == []
    assert doc["created_at"] == FIXED_NOW
    # Nothing has run yet, so every completion field is explicitly empty rather
    # than absent — the reaper and the status route both read these.
    assert doc["worker_id"] is None
    assert doc["heartbeat_at"] is None
    assert doc["started_at"] is None
    assert doc["finished_at"] is None
    assert doc["expires_at"] is None
    assert doc["result"] is None
    assert doc["error"] is None
    assert doc["download_token"] is None


@pytest.mark.asyncio
async def test_create_writes_exactly_the_spec_schema(repo):
    """No stray fields. The document shape is the contract the worker reads back."""
    await repo.create(_job())

    assert set(_inserted(repo)) == {
        "job_id", "user_id", "request_hash", "filters", "label", "status",
        "cells_estimate", "chunks_total", "chunks_done", "parts", "result",
        "download_token", "token_expires_at", "error", "attempts", "max_attempts",
        "worker_id", "heartbeat_at", "created_at", "started_at", "finished_at",
        "expires_at",
        # Rows are what an in-process background build counts (2026-09-20); the
        # chunk counters belong to the scheduler worker, which still owns the
        # queued rows. Both are written, and the status route reads rows first.
        "rows_total", "rows_done",
    }


# ------------------------------------- create: the complete filter echo ----

@pytest.mark.asyncio
async def test_create_echoes_the_complete_filter_set(repo):
    """This is what Regenerate runs off, once the file is gone."""
    await repo.create(_job())

    assert _inserted(repo)["filters"] == FILTERS


@pytest.mark.asyncio
async def test_create_preserves_filter_keys_it_does_not_know_about(repo):
    """A filter added next quarter must survive the round trip untouched."""
    filters = dict(FILTERS, exchanges=["IEX", "HPX"], custom_blocks=None,
                   some_future_filter={"nested": [1, 2]})
    await repo.create(_job(filters=filters))

    assert _inserted(repo)["filters"] == filters


@pytest.mark.asyncio
async def test_create_deep_copies_the_filters(repo):
    """The caller reusing its request object must not mutate a queued job."""
    filters = copy.deepcopy(FILTERS)
    await repo.create(_job(filters=filters))

    filters["datasets"].append("FREQUENCY")
    filters["to_date"] = "2026-12-31"

    stored = _inserted(repo)["filters"]
    assert stored["datasets"] == ["ACTUAL_DEMAND", "DC_PLANT"]
    assert stored["to_date"] == "2026-08-31"


@pytest.mark.asyncio
async def test_create_rejects_an_incomplete_filter_set(repo):
    incomplete = {k: v for k, v in FILTERS.items() if k != "plants"}

    with pytest.raises(jobs.IncompleteFilters) as exc:
        await repo.create(_job(filters=incomplete))

    assert "plants" in str(exc.value)
    repo.collection.insert_one.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", sorted(jobs.REQUIRED_FILTER_KEYS))
async def test_every_required_filter_key_is_enforced(repo, missing):
    with pytest.raises(jobs.IncompleteFilters):
        await repo.create(_job(filters={k: v for k, v in FILTERS.items()
                                        if k != missing}))


@pytest.mark.asyncio
async def test_custom_block_grouping_must_carry_its_ranges(repo):
    """CUSTOM without the ranges is not reconstructible, only plausible."""
    with pytest.raises(jobs.IncompleteFilters) as exc:
        await repo.create(_job(filters=dict(FILTERS, block_grouping="CUSTOM")))

    assert "custom_blocks" in str(exc.value)

    await repo.create(_job(filters=dict(FILTERS, block_grouping="CUSTOM",
                                        custom_blocks=[[1, 12], [13, 24]])))
    assert _inserted(repo)["filters"]["custom_blocks"] == [[1, 12], [13, 24]]


@pytest.mark.asyncio
async def test_create_normalises_dates_so_the_filters_survive_bson(repo):
    """bson cannot encode datetime.date at all — the insert would raise."""
    await repo.create(_job(filters=dict(FILTERS, from_date=date(2026, 4, 1),
                                        to_date=date(2026, 8, 31))))

    stored = _inserted(repo)["filters"]
    assert stored["from_date"] == "2026-04-01"
    assert stored["to_date"] == "2026-08-31"


@pytest.mark.asyncio
async def test_create_requires_an_owner_and_a_hash(repo):
    with pytest.raises(ValueError):
        await repo.create(_job(user_id=""))
    with pytest.raises(ValueError):
        await repo.create(_job(request_hash=None))
    repo.collection.insert_one.assert_not_called()


# ------------------------------------------------------------- dedupe ------

@pytest.mark.asyncio
async def test_find_active_by_hash_matches_only_unfinished_jobs(repo):
    repo.collection.find_one = AsyncMock(return_value=None)

    await repo.find_active_by_hash("u-1", "h-abc")

    query = repo.collection.find_one.call_args[0][0]
    assert query["user_id"] == "u-1"
    assert query["request_hash"] == "h-abc"
    assert set(query["status"]["$in"]) == {
        jobs.STATUS_QUEUED, jobs.STATUS_RUNNING, jobs.STATUS_ASSEMBLING,
    }


@pytest.mark.asyncio
async def test_a_finished_job_never_blocks_a_fresh_request(repo):
    """ready / failed / expired / cancelled must fall outside the dedupe window."""
    repo.collection.find_one = AsyncMock(return_value=None)

    await repo.find_active_by_hash("u-1", "h-abc")

    active = set(repo.collection.find_one.call_args[0][0]["status"]["$in"])
    assert active.isdisjoint({jobs.STATUS_READY, jobs.STATUS_FAILED,
                              jobs.STATUS_EXPIRED, jobs.STATUS_CANCELLED})


# -------------------------------------------------------------- reaper -----

@pytest.mark.asyncio
async def test_claim_orphans_requeues_only_stale_running_jobs(repo):
    stale_before = FIXED_NOW - timedelta(minutes=2)
    repo.collection.find_one_and_update = AsyncMock(side_effect=[{"job_id": "j1"}, None])

    requeued = await repo.claim_orphans(stale_before, limit=5)

    assert requeued == [{"job_id": "j1"}]
    query, update = repo.collection.find_one_and_update.call_args_list[0][0][:2]
    assert query["status"] == jobs.STATUS_RUNNING
    assert query["heartbeat_at"] == {"$lt": stale_before}
    # attempts is compared against a sibling field, so it has to be an $expr.
    assert query["$expr"] == {"$lt": ["$attempts", "$max_attempts"]}
    assert update["$set"]["status"] == jobs.STATUS_QUEUED
    assert update["$inc"] == {"attempts": 1}


@pytest.mark.asyncio
async def test_claim_orphans_releases_the_dead_workers_claim(repo):
    """Leaving worker_id set would let the dead worker's heartbeat win it back."""
    repo.collection.find_one_and_update = AsyncMock(side_effect=[{"job_id": "j1"}, None])

    await repo.claim_orphans(FIXED_NOW, limit=5)

    update = repo.collection.find_one_and_update.call_args_list[0][0][1]
    assert update["$set"]["worker_id"] is None
    assert update["$set"]["heartbeat_at"] is None


@pytest.mark.asyncio
async def test_claim_orphans_stops_as_soon_as_nothing_is_stale(repo):
    repo.collection.find_one_and_update = AsyncMock(side_effect=[{"job_id": "j1"}, None])

    requeued = await repo.claim_orphans(FIXED_NOW, limit=50)

    assert len(requeued) == 1
    assert repo.collection.find_one_and_update.call_count == 2


@pytest.mark.asyncio
async def test_claim_orphans_honours_its_limit(repo):
    repo.collection.find_one_and_update = AsyncMock(return_value={"job_id": "j"})

    requeued = await repo.claim_orphans(FIXED_NOW, limit=3)

    assert len(requeued) == 3


@pytest.mark.asyncio
async def test_claim_orphans_fails_jobs_that_exhausted_their_attempts(repo):
    """The lifecycle ends at failed after 3 attempts, not in a requeue loop."""
    stale_before = FIXED_NOW - timedelta(minutes=2)
    repo.collection.find_one_and_update = AsyncMock(return_value=None)

    await repo.claim_orphans(stale_before, limit=5)

    query, update = repo.collection.update_many.call_args[0][:2]
    assert query["status"] == jobs.STATUS_RUNNING
    assert query["heartbeat_at"] == {"$lt": stale_before}
    assert query["$expr"] == {"$gte": ["$attempts", "$max_attempts"]}
    assert update["$set"]["status"] == jobs.STATUS_FAILED
    assert update["$set"]["error"]["code"] == "max_attempts_exceeded"
    assert update["$set"]["finished_at"] == FIXED_NOW


# ----------------------------------------------------------- heartbeat -----

@pytest.mark.asyncio
async def test_heartbeat_only_touches_the_job_this_worker_owns(repo):
    repo.collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

    assert await repo.heartbeat("j1", "worker-7") is True

    query, update = repo.collection.update_one.call_args[0][:2]
    assert query == {"job_id": "j1", "worker_id": "worker-7"}
    assert update == {"$set": {"heartbeat_at": FIXED_NOW}}


@pytest.mark.asyncio
async def test_heartbeat_reports_a_lost_claim(repo):
    """A requeued job has a new owner; the old worker must learn to stop."""
    repo.collection.update_one = AsyncMock(return_value=MagicMock(matched_count=0))

    assert await repo.heartbeat("j1", "worker-7") is False


# ---------------------------------------------------------------- mark -----

@pytest.mark.asyncio
async def test_mark_ready_sets_finished_at_and_expires_at(repo):
    repo.collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    result = {"path": "/var/lib/epms/exports/j1.xlsx", "filename": "x.xlsx",
              "bytes": 1024, "row_count": 40}

    await repo.mark("j1", jobs.STATUS_READY, result=result)

    fields = repo.collection.update_one.call_args[0][1]["$set"]
    retention = jobs.settings.ANALYTICS_EXPORT_RETENTION_HOURS
    assert fields["status"] == jobs.STATUS_READY
    assert fields["finished_at"] == FIXED_NOW
    assert fields["expires_at"] == FIXED_NOW + timedelta(hours=retention)
    assert fields["result"] == result


@pytest.mark.asyncio
async def test_mark_ready_mints_a_download_token_that_dies_with_the_file(repo):
    repo.collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

    await repo.mark("j1", jobs.STATUS_READY, result={"path": "/tmp/j1.csv"})

    fields = repo.collection.update_one.call_args[0][1]["$set"]
    assert fields["download_token"]
    assert fields["token_expires_at"] == fields["expires_at"]


@pytest.mark.asyncio
async def test_mark_running_records_the_worker_and_opens_the_heartbeat(repo):
    repo.collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

    await repo.mark("j1", jobs.STATUS_RUNNING, worker_id="worker-7")

    fields = repo.collection.update_one.call_args[0][1]["$set"]
    assert fields["worker_id"] == "worker-7"
    assert fields["started_at"] == FIXED_NOW
    assert fields["heartbeat_at"] == FIXED_NOW
    assert "finished_at" not in fields


@pytest.mark.asyncio
async def test_mark_failed_records_the_error_and_closes_the_job(repo):
    repo.collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    err = {"code": "query_failed", "message": "boom", "dataset": "DC_PLANT"}

    await repo.mark("j1", jobs.STATUS_FAILED, error=err)

    fields = repo.collection.update_one.call_args[0][1]["$set"]
    assert fields["error"] == err
    assert fields["finished_at"] == FIXED_NOW


@pytest.mark.asyncio
async def test_mark_explicit_fields_win_over_the_derived_ones(repo):
    repo.collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    explicit = datetime(2026, 1, 1, tzinfo=timezone.utc)

    await repo.mark("j1", jobs.STATUS_READY, expires_at=explicit)

    assert repo.collection.update_one.call_args[0][1]["$set"]["expires_at"] == explicit


@pytest.mark.asyncio
async def test_mark_rejects_a_status_outside_the_lifecycle(repo):
    with pytest.raises(ValueError):
        await repo.mark("j1", "finished-ish")
    repo.collection.update_one.assert_not_called()


# ----------------------------------------------------------- parts ---------

@pytest.mark.asyncio
async def test_record_part_appends_and_never_rewrites_existing_parts(repo):
    repo.collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    part = {"idx": 8, "path": "/tmp/j1.part8", "rows": 4800, "bytes": 90_112,
            "sha256": "deadbeef", "done_at": FIXED_NOW}

    await repo.record_part("j1", part)

    update = repo.collection.update_one.call_args[0][1]
    assert update["$push"] == {"parts": part}
    assert "parts" not in update.get("$set", {})
    assert update["$inc"] == {"chunks_done": 1}


@pytest.mark.asyncio
async def test_record_part_is_idempotent_on_a_resumed_chunk(repo):
    """A retry that resumes at chunk 8 must not double-count chunk 8."""
    repo.collection.update_one = AsyncMock(return_value=MagicMock(matched_count=0))

    appended = await repo.record_part("j1", {"idx": 8, "path": "/tmp/p8"})

    query = repo.collection.update_one.call_args[0][0]
    assert query["job_id"] == "j1"
    assert query["parts.idx"] == {"$ne": 8}
    assert appended is False


@pytest.mark.asyncio
async def test_record_part_refreshes_the_heartbeat(repo):
    """A chunk can take minutes; finishing one proves the worker is alive."""
    repo.collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

    await repo.record_part("j1", {"idx": 1, "path": "/tmp/p1"})

    assert repo.collection.update_one.call_args[0][1]["$set"]["heartbeat_at"] == FIXED_NOW


# --------------------------------------------------------- expiry ----------

@pytest.mark.asyncio
async def test_expire_before_keeps_the_row_so_regenerate_still_works(repo):
    row = {"job_id": "j1", "status": jobs.STATUS_EXPIRED, "filters": FILTERS,
           "result": {"path": "/var/lib/epms/exports/j1.xlsx"}}
    repo.collection.find_one_and_update = AsyncMock(side_effect=[row, None])

    expired = await repo.expire_before(FIXED_NOW)

    repo.collection.delete_one.assert_not_called()
    repo.collection.delete_many.assert_not_called()
    # The caller deletes the file; the row — and its filters — survive.
    assert expired[0]["result"]["path"] == "/var/lib/epms/exports/j1.xlsx"
    assert expired[0]["filters"] == FILTERS


@pytest.mark.asyncio
async def test_expire_before_only_touches_ready_jobs_past_the_cutoff(repo):
    repo.collection.find_one_and_update = AsyncMock(return_value=None)

    await repo.expire_before(FIXED_NOW)

    query, update = repo.collection.find_one_and_update.call_args[0][:2]
    assert query["status"] == jobs.STATUS_READY
    assert query["expires_at"] == {"$lte": FIXED_NOW}
    assert update["$set"]["status"] == jobs.STATUS_EXPIRED


@pytest.mark.asyncio
async def test_expire_before_revokes_the_download_token(repo):
    """The file is gone; a still-valid token would 404 or, worse, serve a stale file."""
    repo.collection.find_one_and_update = AsyncMock(return_value=None)

    await repo.expire_before(FIXED_NOW)

    fields = repo.collection.find_one_and_update.call_args[0][1]["$set"]
    assert fields["download_token"] is None
    assert fields["token_expires_at"] is None


# ------------------------------------------------------------- reads -------

@pytest.mark.asyncio
async def test_get_returns_none_for_an_unknown_job(repo):
    repo.collection.find_one = AsyncMock(return_value=None)

    assert await repo.get("nope") is None
    assert repo.collection.find_one.call_args[0][0] == {"job_id": "nope"}


@pytest.mark.asyncio
async def test_get_excludes_the_mongo_id(repo):
    repo.collection.find_one = AsyncMock(return_value={"job_id": "j1"})

    await repo.get("j1")

    assert repo.collection.find_one.call_args[0][1] == {"_id": 0}


@pytest.mark.asyncio
async def test_list_for_user_is_scoped_to_that_user_and_newest_first(repo):
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[{"job_id": "j1"}])
    repo.collection.find = MagicMock(return_value=cursor)

    rows = await repo.list_for_user("u-1", limit=25)

    assert rows == [{"job_id": "j1"}]
    query, projection = repo.collection.find.call_args[0][:2]
    assert query == {"user_id": "u-1"}
    assert projection == {"_id": 0}
    cursor.sort.assert_called_once_with([("created_at", -1)])
    cursor.limit.assert_called_once_with(25)


# ------------------------------------------------------------------ requeue

@pytest.mark.asyncio
async def test_requeue_sends_the_same_job_back_to_queued(repo):
    repo.collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

    assert await repo.requeue("job-1") is True

    filt, update = repo.collection.update_one.call_args[0]
    assert filt["job_id"] == "job-1"
    assert update["$set"]["status"] == "queued"


@pytest.mark.asyncio
async def test_requeue_only_touches_terminal_failed_or_cancelled_jobs(repo):
    repo.collection.update_one = AsyncMock(return_value=MagicMock(modified_count=0))

    await repo.requeue("job-1")

    filt, _ = repo.collection.update_one.call_args[0]
    assert set(filt["status"]["$in"]) == {"failed", "cancelled"}


@pytest.mark.asyncio
async def test_requeue_wipes_everything_the_failed_run_left_behind(repo):
    """The worker must start clean: no stale error, receipts, claim, or attempts."""
    repo.collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

    await repo.requeue("job-1")

    s = repo.collection.update_one.call_args[0][1]["$set"]
    assert s["error"] is None
    assert s["parts"] == []
    assert s["chunks_done"] == 0
    assert s["attempts"] == 0
    assert s["worker_id"] is None
    assert s["heartbeat_at"] is None
    assert s["result"] is None


@pytest.mark.asyncio
async def test_requeue_reports_false_when_nothing_matched(repo):
    repo.collection.update_one = AsyncMock(return_value=MagicMock(modified_count=0))
    assert await repo.requeue("already-running") is False
