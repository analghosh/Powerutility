"""Turns a queued analytics export job into a file on disk.

This is the first job in EPMS that writes a file, so it establishes conventions
that did not exist before. All four are deliberate:

* **Where.** ``<ANALYTICS_EXPORT_DIR>/<job_id>/`` — one directory per job, holding
  the chunk parts while the job runs and the finished file afterwards. Retention
  deletes a job's file, not a shared bucket, so nothing has to reason about which
  stray file belonged to whom.
* **Named for the query, not the job.** The file the user downloads is
  ``analytics_2026-04-01_to_2026-08-31.csv`` (``analytics_csv.filename_for``); the
  job id keeps two identical queries apart on disk without leaking into the name
  they see. That helper also strips anything a ``Content-Disposition`` header could
  be injected with, which matters because the date range is caller-supplied.
* **Created, never assumed.** ``ANALYTICS_EXPORT_DIR`` defaults to
  ``/var/lib/epms/exports``, which does not exist on a fresh box and which the
  service user cannot create on the PROD one. That has to surface as a named
  configuration error on the job row, not as a bare ``PermissionError`` at the
  first write, so :func:`ensure_export_dir` runs before the first byte and at
  scheduler startup.
* **Deleted at 72 hours; the row lives 90 days.** :meth:`reap` unlinks the file and
  marks the row ``expired``. The row carries the filters that power Regenerate, so
  deleting it is how a user loses the 34 plants they picked three days ago.

Chunking
--------
A job is sharded by date window, each sized to about
``ANALYTICS_EXPORT_CHUNK_CELLS`` cells, and each chunk writes an ordered part file
with a receipt. Attempt two therefore resumes at chunk 8 of 11 instead of
restarting — which is the difference between a flaky chunk costing four minutes
and costing the whole export.

Two invariants make that safe:

1. **The chunks tile the range exactly** — no gap, no overlap. A gap loses days
   silently: the file looks complete and is short.
2. **A boundary never splits a grouping period.** Half a month aggregated on its own
   is a half-month average, and no assembly step can turn two of those back into
   the month the user asked for. Boundaries snap to month/week/year ends when the
   grouping is coarser than a day.

Stopping
--------
The worker checks two things before every chunk. ``heartbeat()`` returning ``False``
means the reaper requeued this job and another worker owns it now — continuing
would write parts into someone else's job. A ``cancelled`` status means the user
asked it to stop. Either one ends the run without marking the job failed.

Format note
-----------
CSV assembles by streaming: it is flat and long, every line carries its own date,
so the parts concatenate. XLSX is a grid whose columns are the dates, so the parts
have to be merged in memory before the workbook is written — openpyxl emits no
bytes until the zip directory is finalised anyway. Excel's own 1,048,576-row cap
binds well before the cell budget does.
"""

import asyncio
import calendar
import hashlib
import json
import logging
import math
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pymongo import ReturnDocument

from services.processing.app.v2.analytics import derived, pivot, planner
from services.processing.app.v2.helpers import analytics_csv, analytics_xlsx
from services.processing.app.v2.helpers.analytics_xlsx import ExportWriteError
from services.processing.app.v2.repository import analytics_job_repository as jobs
from services.processing.app.v2.repository.analytics_job_repository import (
    AnalyticsJobRepository,
)
from services.scheduler.app.core.config import settings

logger = logging.getLogger(__name__)

#: Registered in three places — see the module note in `cron_manager._get_job_function`.
JOB_TYPE = "analytics_export_sweep"

#: `_add_job_to_scheduler` rejects anything that is not exactly five cron fields and
#: never passes `second=` to CronTrigger, so sub-minute is not expressible. The
#: spec's 30-second sweep is therefore every minute; RabbitMQ's doorbell covers the
#: latency in between when the broker is up.
JOB_SCHEDULE = "* * * * *"

OUTCOME_SKIPPED = "skipped"
OUTCOME_MISSING = "missing"
OUTCOME_LOST_CLAIM = "lost_claim"
OUTCOME_CANCELLED = jobs.STATUS_CANCELLED

#: Groupings whose buckets are wider than a day. A chunk boundary inside one of
#: these splits a bucket in half; see `_period_end`.
PERIOD_GROUPINGS = ("WEEK_WISE", "MONTH_WISE", "YEAR_WISE")

DEFAULT_CHUNK_CELLS = 50_000
DEFAULT_CHUNK_CONCURRENCY = 3
DEFAULT_STALE_AFTER_SECONDS = 120
DEFAULT_CHUNK_MAX_TIME_MS = 120_000
DEFAULT_JOBS_PER_SWEEP = 1

_NO_ID = {"_id": 0}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_date(value: Any, name: str) -> date:
    """Job filters store ISO strings — bson cannot encode `datetime.date` at all."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value).date()
    raise ValueError(f"Export job filter {name!r} is not a date: {value!r}")


def _period_end(day: date, grouping: str) -> date:
    """Last day of the grouping bucket `day` falls in.

    Week uses the ISO week (Monday-Sunday) because `grouping.date_key_expr`
    formats week keys with `%G-W%V`; snapping to a calendar week here would put
    the boundary in the middle of a key.
    """
    if grouping == "WEEK_WISE":
        return day + timedelta(days=6 - day.weekday())
    if grouping == "MONTH_WISE":
        return day.replace(day=calendar.monthrange(day.year, day.month)[1])
    if grouping == "YEAR_WISE":
        return day.replace(month=12, day=31)
    return day


def ensure_export_dir(directory: str) -> str:
    """Create `directory` if it is missing and prove it is writable.

    Raises `ExportWriteError` — never a bare `OSError` — because the realistic
    cause is `ANALYTICS_EXPORT_DIR` pointing somewhere the scheduler's service
    user does not own, and the message has to say so.
    """
    directory = os.path.abspath(directory)
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise ExportWriteError(
            f"Cannot create the analytics export directory {directory!r}: {exc}. "
            f"Check ANALYTICS_EXPORT_DIR and that the scheduler's service user "
            f"owns the path."
        ) from exc
    if not os.path.isdir(directory):  # pragma: no cover - makedirs would have raised
        raise ExportWriteError(
            f"Analytics export path {directory!r} is not a directory. "
            f"Check ANALYTICS_EXPORT_DIR."
        )
    if not os.access(directory, os.W_OK):
        raise ExportWriteError(
            f"Analytics export directory {directory!r} is not writable by uid "
            f"{os.getuid()}. Check ANALYTICS_EXPORT_DIR and its ownership."
        )
    return directory


def _split_header(stream) -> Tuple[List[str], Any]:
    """Pull the `#` filter block plus the single column header off a `csv_rows` stream.

    Written against the stream rather than against a line count so it cannot drift
    when `filter_pairs` grows or drops a row.
    """
    head: List[str] = []
    for line in stream:
        head.append(line)
        if not line.startswith(analytics_csv.COMMENT_PREFIX):
            break
    return head, stream


class AnalyticsExportProcessor:
    """Claims queued export jobs, writes their files, and reaps the leftovers."""

    def __init__(self,
                 job_repository: Optional[AnalyticsJobRepository] = None,
                 analytics_repository: Any = None,
                 query_runner: Optional[Callable] = None,
                 export_dir: Optional[str] = None,
                 worker_id: Optional[str] = None,
                 chunk_cells: Optional[int] = None,
                 chunk_concurrency: Optional[int] = None,
                 max_jobs_per_sweep: Optional[int] = None,
                 stale_after_seconds: Optional[int] = None):
        self._job_repository = job_repository
        self._analytics_repository = analytics_repository
        self._query_runner = query_runner
        self.export_dir = export_dir or getattr(
            settings, "ANALYTICS_EXPORT_DIR", "/var/lib/epms/exports")

        # One id per process. A scheduler restart therefore abandons its claims to
        # the reaper rather than silently re-adopting jobs whose parts it no longer
        # remembers writing.
        self.worker_id = worker_id or f"scheduler-{os.getpid()}-{uuid.uuid4().hex[:8]}"

        self.chunk_cells = int(chunk_cells or getattr(
            settings, "ANALYTICS_EXPORT_CHUNK_CELLS", DEFAULT_CHUNK_CELLS))
        self.chunk_concurrency = int(chunk_concurrency or getattr(
            settings, "ANALYTICS_EXPORT_CHUNK_CONCURRENCY", DEFAULT_CHUNK_CONCURRENCY))
        self.max_jobs_per_sweep = int(max_jobs_per_sweep or getattr(
            settings, "ANALYTICS_EXPORT_JOBS_PER_SWEEP", DEFAULT_JOBS_PER_SWEEP))
        self.stale_after_seconds = int(stale_after_seconds or getattr(
            settings, "ANALYTICS_JOB_STALE_SECONDS", DEFAULT_STALE_AFTER_SECONDS))
        self.chunk_max_time_ms = int(getattr(
            settings, "ANALYTICS_EXPORT_CHUNK_MAX_TIME_MS", DEFAULT_CHUNK_MAX_TIME_MS))

        #: Overridable so tests can pin the clock; the repository keeps its own.
        self.now = _utcnow

    # ------------------------------------------------------------ plumbing

    @property
    def job_repository(self) -> AnalyticsJobRepository:
        """Lazy, like every repository here: building one in `__init__` is what
        binds Motor to the wrong event loop."""
        if self._job_repository is None:
            self._job_repository = AnalyticsJobRepository()
        return self._job_repository

    @property
    def analytics_repository(self):
        if self._analytics_repository is None:
            from services.processing.app.v2.repository.analytics_repository import (
                AnalyticsRepository,
            )
            self._analytics_repository = AnalyticsRepository()
        return self._analytics_repository

    def ensure_directories(self) -> str:
        """Called at scheduler startup so a misconfigured path is loud at boot."""
        return ensure_export_dir(self.export_dir)

    # --------------------------------------------------------------- chunks

    def chunks_for(self, job: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Shard one job's date range into windows of roughly `chunk_cells` cells.

        Returns `[{idx, from_date, to_date, days, cells}]` with ISO dates, covering
        the requested range exactly once.
        """
        filters = job.get("filters") or {}
        start = _as_date(filters.get("from_date"), "from_date")
        end = _as_date(filters.get("to_date"), "to_date")
        if end < start:
            raise ValueError(
                f"Export job {job.get('job_id')} has from_date {start} after "
                f"to_date {end}")

        total_days = (end - start).days + 1
        cells = int(job.get("cells_estimate") or 0)
        # Cells are spread evenly over the range: the estimate is itself
        # date_buckets x block_buckets x series, so per-day is the only division
        # that does not need the registry again.
        per_day = max(1, math.ceil(cells / total_days)) if cells > 0 else 1
        days_per_chunk = max(1, self.chunk_cells // per_day)

        grouping = str(filters.get("date_grouping") or "").upper()

        chunks: List[Dict[str, Any]] = []
        cursor = start
        while cursor <= end:
            last = min(end, cursor + timedelta(days=days_per_chunk - 1))
            if grouping in PERIOD_GROUPINGS:
                # Extend rather than truncate: a chunk slightly over the target is
                # a performance question, a split bucket is a wrong number.
                last = min(end, _period_end(last, grouping))
            days = (last - cursor).days + 1
            chunks.append({
                "idx": len(chunks),
                "from_date": cursor.isoformat(),
                "to_date": last.isoformat(),
                "days": days,
                "cells": days * per_day,
            })
            cursor = last + timedelta(days=1)

        return chunks

    def _chunk_filters(self, filters: Dict[str, Any],
                       chunk: Dict[str, Any]) -> Dict[str, Any]:
        """The stored filter echo narrowed to one chunk's window.

        Also bridges the one key that differs between the two shapes: the job row
        stores `block_grouping` (the §8 schema) while the adapters read
        `block_band`. A job replayed by Regenerate must hit the same pipeline as
        the live request that created it.
        """
        narrowed = dict(filters)
        narrowed["from_date"] = chunk["from_date"]
        narrowed["to_date"] = chunk["to_date"]
        narrowed.setdefault(
            "block_band", filters.get("block_band") or filters.get("block_grouping"))
        return narrowed

    # ---------------------------------------------------------------- query

    async def _run_analytics_query(self, filters: Dict[str, Any],
                                   datasets: Sequence[str]):
        """One chunk's aggregation, in the same order the inline path uses."""
        from services.processing.app.v2.analytics import executor

        plans = planner.plan(list(datasets), filters,
                             {"plant_count": len(filters.get("plants") or []),
                              "exchange_count": len(filters.get("exchanges") or [])})
        result = await executor.execute(
            plans, self.analytics_repository,
            concurrency=int(getattr(settings, "ANALYTICS_GROUP_CONCURRENCY", 4)),
            max_time_ms=self.chunk_max_time_ms,
        )
        rows = derived.resolve(result.rows, list(datasets))
        return rows, list(result.failed_datasets)

    async def _fetch(self, filters: Dict[str, Any], datasets: Sequence[str]):
        runner = self._query_runner or self._run_analytics_query
        return await runner(filters, list(datasets))

    # ----------------------------------------------------------- job control

    async def _claim_next_queued(self) -> Optional[Dict[str, Any]]:
        """Take the oldest queued job, atomically.

        `find_one_and_update` rather than read-then-mark: the sweep and the
        RabbitMQ doorbell can both fire on the same second, and two workers
        running the same job would each write half its parts.
        """
        now = self.now()
        return await self.job_repository.collection.find_one_and_update(
            {"status": jobs.STATUS_QUEUED},
            {"$set": {"status": jobs.STATUS_RUNNING,
                      "worker_id": self.worker_id,
                      "started_at": now,
                      "heartbeat_at": now}},
            projection=_NO_ID,
            sort=[("created_at", 1)],
            return_document=ReturnDocument.AFTER,
        )

    async def _claim(self, job_id: str) -> Optional[Dict[str, Any]]:
        now = self.now()
        return await self.job_repository.collection.find_one_and_update(
            {"job_id": job_id, "status": jobs.STATUS_QUEUED},
            {"$set": {"status": jobs.STATUS_RUNNING,
                      "worker_id": self.worker_id,
                      "started_at": now,
                      "heartbeat_at": now}},
            projection=_NO_ID,
            return_document=ReturnDocument.AFTER,
        )

    async def _stop_reason(self, job_id: str) -> Optional[str]:
        """Why this worker should put the job down, or None to carry on.

        Heartbeat first: losing the claim outranks everything, because from that
        moment the row belongs to another worker and every write to it is a race.
        """
        if not await self.job_repository.heartbeat(job_id, self.worker_id):
            return OUTCOME_LOST_CLAIM
        current = await self.job_repository.get(job_id)
        if current is None:
            return OUTCOME_LOST_CLAIM
        if current.get("status") == jobs.STATUS_CANCELLED:
            return OUTCOME_CANCELLED
        return None

    async def _fail(self, job_id: str, code: str, message: str,
                    dataset: Optional[str] = None) -> None:
        logger.error("Analytics export %s failed (%s): %s", job_id, code, message)
        await self.job_repository.mark(
            job_id, jobs.STATUS_FAILED,
            error={"code": code, "message": message, "dataset": dataset})

    # ----------------------------------------------------------------- parts

    def _job_dir(self, job_id: str) -> str:
        return os.path.join(ensure_export_dir(self.export_dir), str(job_id))

    def _write_part(self, parts_dir: str, chunk: Dict[str, Any],
                    rows: Sequence[dict], failed: Sequence[str]) -> Dict[str, Any]:
        """One chunk's canonical rows, plus the receipt that lets a retry skip it."""
        path = os.path.join(parts_dir, f"part-{chunk['idx']:05d}.json")
        payload = json.dumps(
            {"idx": chunk["idx"], "from_date": chunk["from_date"],
             "to_date": chunk["to_date"], "failed_datasets": list(failed),
             "rows": list(rows)},
            default=str,
        ).encode("utf-8")
        with open(path, "wb") as handle:
            handle.write(payload)
        return {
            "idx": chunk["idx"],
            "path": path,
            "rows": len(rows),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "failed_datasets": list(failed),
            "done_at": _utcnow(),
        }

    @staticmethod
    def _receipts(job: Dict[str, Any]):
        """Split a job's part receipts into the ones backed by a file and the rest.

        A receipt is only worth skipping a chunk for if the file it names still
        exists; assembly reads those files, so a receipt without one is worse than
        no receipt at all.
        """
        done, stale = set(), set()
        for part in (job.get("parts") or []):
            idx = part.get("idx")
            if idx is None:
                continue
            path = part.get("path")
            (done if path and os.path.exists(path) else stale).add(idx)
        return done, stale

    @staticmethod
    def _read_part(path: str) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _discard_parts(parts: Sequence[dict]) -> None:
        """Part files go at assembly; the receipts stay on the row as evidence."""
        directories = set()
        for part in parts:
            path = part.get("path")
            if not path:
                continue
            directories.add(os.path.dirname(path))
            try:
                os.unlink(path)
            except OSError:
                pass
        for directory in directories:
            try:
                os.rmdir(directory)
            except OSError:
                # Still holding something, or already gone. Not worth failing a
                # finished export over.
                pass

    # -------------------------------------------------------------- assembly

    def _assemble(self, job: Dict[str, Any], parts: Sequence[dict],
                  job_dir: str) -> Dict[str, Any]:
        filters = job.get("filters") or {}
        fmt = str(filters.get("format") or "csv").lower()
        filename = analytics_csv.filename_for(filters, fmt)
        path = os.path.join(job_dir, filename)

        if fmt == "xlsx":
            written = self._assemble_xlsx(job, parts, path)
        else:
            written = self._assemble_csv(job, parts, path)

        written["filename"] = filename
        return written

    def _assemble_csv(self, job: Dict[str, Any], parts: Sequence[dict],
                      path: str) -> Dict[str, Any]:
        """Concatenate the parts. Every CSV line carries its own date, so the flat
        layout needs no merge — one header block for the file, then the rows."""
        filters = job.get("filters") or {}
        requested = list(filters.get("datasets") or [])
        row_count = 0
        wrote_header = False

        try:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                for part in parts:
                    payload = self._read_part(part["path"])
                    response = pivot.build_response(
                        payload.get("rows") or [], requested=requested,
                        meta={"failed_datasets": payload.get("failed_datasets") or [],
                              "partial": bool(payload.get("failed_datasets"))})
                    head, data = _split_header(
                        analytics_csv.csv_rows(response, filters))
                    if not wrote_header:
                        handle.writelines(head)
                        wrote_header = True
                    for line in data:
                        handle.write(line)
                        row_count += 1
                if not wrote_header:
                    # No parts at all: still produce a file that says what was asked
                    # for, rather than a zero-byte download.
                    empty = pivot.build_response([], requested=requested, meta={})
                    handle.writelines(analytics_csv.csv_rows(empty, filters))
        except ExportWriteError:
            raise
        except OSError as exc:
            raise ExportWriteError(
                f"Cannot write the analytics export {path!r}: {exc}. "
                f"Check ANALYTICS_EXPORT_DIR, its free space and its permissions."
            ) from exc

        size = os.path.getsize(path)
        logger.info("Analytics CSV export written: path=%s bytes=%s rows=%s parts=%s",
                    path, size, row_count, len(parts))
        return {"path": path, "bytes": size, "row_count": row_count}

    def _assemble_xlsx(self, job: Dict[str, Any], parts: Sequence[dict],
                       path: str) -> Dict[str, Any]:
        """Merge every part, then write one workbook.

        Unlike the CSV this cannot stream: the sheet's columns are the dates, so a
        later chunk adds columns rather than rows. openpyxl emits no bytes until
        the zip directory is finalised in any case.
        """
        filters = job.get("filters") or {}
        requested = list(filters.get("datasets") or [])
        rows: List[dict] = []
        failed: List[str] = []
        for part in parts:
            payload = self._read_part(part["path"])
            rows.extend(payload.get("rows") or [])
            failed.extend(payload.get("failed_datasets") or [])

        response = pivot.build_response(
            rows, requested=requested,
            meta={"failed_datasets": failed, "partial": bool(failed)})
        return analytics_xlsx.write_xlsx(response, filters, path)

    # ------------------------------------------------------------- execution

    async def _execute(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """Run a job this worker already owns, from wherever its parts left off."""
        job_id = job["job_id"]
        filters = job.get("filters") or {}

        try:
            chunks = self.chunks_for(job)
        except (ValueError, TypeError) as exc:
            await self._fail(job_id, "invalid_filters", str(exc))
            return {"job_id": job_id, "status": jobs.STATUS_FAILED, "error": str(exc)}

        try:
            job_dir = self._job_dir(job_id)
            parts_dir = os.path.join(job_dir, "parts")
            os.makedirs(parts_dir, exist_ok=True)
        except ExportWriteError as exc:
            await self._fail(job_id, "export_dir_unavailable", str(exc))
            return {"job_id": job_id, "status": jobs.STATUS_FAILED, "error": str(exc)}
        except OSError as exc:
            message = (f"Cannot create the export working directory for {job_id}: "
                       f"{exc}. Check ANALYTICS_EXPORT_DIR.")
            await self._fail(job_id, "export_dir_unavailable", message)
            return {"job_id": job_id, "status": jobs.STATUS_FAILED, "error": message}

        await self.job_repository.collection.update_one(
            {"job_id": job_id, "worker_id": self.worker_id},
            {"$set": {"chunks_total": len(chunks)}})

        done, stale = self._receipts(job)
        if stale:
            # A receipt whose part file has gone is a lie — something cleared the
            # working directory under a job that was already running. Re-run those
            # chunks rather than failing at assembly on a file that is not there,
            # and drop the stale receipts so the replacements can be recorded
            # (`record_part` is idempotent on idx and would otherwise refuse them).
            logger.warning(
                "Analytics export %s: %d part file(s) missing (%s); re-running "
                "those chunks", job_id, len(stale), sorted(stale))
            await self.job_repository.collection.update_one(
                {"job_id": job_id},
                {"$pull": {"parts": {"idx": {"$in": sorted(stale)}}},
                 "$set": {"chunks_done": len(done)}})

        pending = [chunk for chunk in chunks if chunk["idx"] not in done]
        if done:
            logger.info("Analytics export %s resuming at chunk %s of %s",
                        job_id, len(done), len(chunks))

        stop: Dict[str, Optional[str]] = {"reason": None}
        semaphore = asyncio.Semaphore(max(1, self.chunk_concurrency))
        datasets = list(filters.get("datasets") or [])

        async def _run_chunk(chunk: Dict[str, Any]) -> None:
            async with semaphore:
                if stop["reason"]:
                    return
                reason = await self._stop_reason(job_id)
                if reason:
                    stop["reason"] = reason
                    return
                rows, failed = await self._fetch(
                    self._chunk_filters(filters, chunk), datasets)
                part = self._write_part(parts_dir, chunk, rows, failed)
                await self.job_repository.record_part(job_id, part)

        settled = await asyncio.gather(
            *(_run_chunk(chunk) for chunk in pending), return_exceptions=True)

        for outcome in settled:
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, BaseException):
                message = f"{type(outcome).__name__}: {outcome}"
                await self._fail(job_id, "chunk_failed", message)
                return {"job_id": job_id, "status": jobs.STATUS_FAILED,
                        "error": message}

        # Check once more before assembling. Assembly is the expensive half for
        # XLSX (every part merged in memory), a fully-resumed job never entered the
        # chunk loop at all, and without this a cancel landing during the last
        # chunk would still produce a file.
        reason = stop["reason"] or await self._stop_reason(job_id)
        if reason:
            logger.info("Analytics export %s stopped early: %s", job_id, reason)
            return {"job_id": job_id, "status": reason,
                    "chunks_total": len(chunks)}

        await self.job_repository.mark(job_id, jobs.STATUS_ASSEMBLING)
        current = await self.job_repository.get(job_id) or job
        parts = sorted((current.get("parts") or []),
                       key=lambda p: p.get("idx", 0))

        try:
            written = self._assemble(current, parts, job_dir)
        except ExportWriteError as exc:
            await self._fail(job_id, "export_write_failed", str(exc))
            return {"job_id": job_id, "status": jobs.STATUS_FAILED, "error": str(exc)}
        except (OSError, ValueError, KeyError) as exc:
            message = f"{type(exc).__name__}: {exc}"
            await self._fail(job_id, "assembly_failed", message)
            return {"job_id": job_id, "status": jobs.STATUS_FAILED, "error": message}

        self._discard_parts(parts)
        await self.job_repository.mark(
            job_id, jobs.STATUS_READY, result=written,
            chunks_total=len(chunks), chunks_done=len(parts))

        logger.info("Analytics export %s ready: %s (%s bytes, %s rows)",
                    job_id, written["filename"], written["bytes"],
                    written["row_count"])
        return {"job_id": job_id, "status": jobs.STATUS_READY, "result": written,
                "chunks_total": len(chunks)}

    async def run_job(self, job_id: str) -> Dict[str, Any]:
        """Claim one job by id and run it. The RabbitMQ doorbell's entry point."""
        claimed = await self._claim(job_id)
        if claimed is None:
            current = await self.job_repository.get(job_id)
            if current is None:
                logger.warning("Analytics export %s no longer exists", job_id)
                return {"job_id": job_id, "status": OUTCOME_MISSING}
            logger.info("Analytics export %s not claimable (status=%s)",
                        job_id, current.get("status"))
            return {"job_id": job_id, "status": OUTCOME_SKIPPED,
                    "job_status": current.get("status")}
        return await self._execute(claimed)

    # ---------------------------------------------------------------- reaper

    def _delete_artifacts(self, job: Dict[str, Any]) -> int:
        """Unlink an expired job's file. The row itself is never touched."""
        result = job.get("result") or {}
        path = result.get("path")
        if not path:
            return 0
        try:
            os.unlink(path)
        except FileNotFoundError:
            return 0
        except OSError as exc:
            logger.warning("Could not delete expired export %s: %s", path, exc)
            return 0
        try:
            os.rmdir(os.path.dirname(path))
        except OSError:
            pass
        return 1

    async def _requeue_stuck_assembly(self, stale_before: datetime) -> List[dict]:
        """Rescue jobs abandoned during assembly.

        `claim_orphans` only looks at `running`, so a worker killed while writing
        the file (deploy, OOM, kill -9) leaves its job in `assembling` for ever.
        Nothing would ever move it, and `find_active_by_hash` counts `assembling`
        as in-flight — so the user could not even re-request the same export. The
        attempt ceiling is honoured exactly as the repository honours it: past
        `max_attempts` a job fails rather than cycling.
        """
        collection = self.job_repository.collection
        requeued: List[dict] = []

        for _ in range(jobs.DEFAULT_REAP_LIMIT):
            doc = await collection.find_one_and_update(
                {"status": jobs.STATUS_ASSEMBLING,
                 "heartbeat_at": {"$lt": stale_before},
                 "$expr": {"$lt": ["$attempts", "$max_attempts"]}},
                {"$set": {"status": jobs.STATUS_QUEUED,
                          "worker_id": None,
                          "heartbeat_at": None,
                          "started_at": None},
                 "$inc": {"attempts": 1}},
                projection=_NO_ID,
                sort=[("heartbeat_at", 1)],
                return_document=ReturnDocument.AFTER,
            )
            if not doc:
                break
            requeued.append(doc)

        await collection.update_many(
            {"status": jobs.STATUS_ASSEMBLING,
             "heartbeat_at": {"$lt": stale_before},
             "$expr": {"$gte": ["$attempts", "$max_attempts"]}},
            {"$set": {"status": jobs.STATUS_FAILED,
                      "finished_at": self.now(),
                      "error": {"code": "max_attempts_exceeded",
                                "message": "Export abandoned during assembly after "
                                           "repeated worker failures",
                                "dataset": None}}},
        )

        if requeued:
            logger.warning("Analytics export reaper: %s job(s) requeued from a "
                           "stalled assembly", len(requeued))
        return requeued

    async def reap(self) -> Dict[str, Any]:
        """Requeue abandoned jobs and delete files past their retention."""
        now = self.now()
        stale_before = now - timedelta(seconds=self.stale_after_seconds)

        requeued = await self.job_repository.claim_orphans(stale_before)
        requeued = requeued + await self._requeue_stuck_assembly(stale_before)
        expired = await self.job_repository.expire_before(now)

        deleted = 0
        for job in expired:
            deleted += self._delete_artifacts(job)

        if requeued or expired:
            logger.info(
                "Analytics export reap: requeued=%s expired=%s files_deleted=%s",
                len(requeued), len(expired), deleted)

        return {
            "requeued": len(requeued),
            "requeued_ids": [j.get("job_id") for j in requeued],
            "expired": len(expired),
            "expired_ids": [j.get("job_id") for j in expired],
            "files_deleted": deleted,
        }

    # ----------------------------------------------------------------- sweep

    async def sweep(self) -> Dict[str, Any]:
        """One tick: reap, then claim and run queued work.

        Reaping first is what lets a job abandoned by a dead worker restart on the
        same tick instead of waiting another minute.

        Only `max_jobs_per_sweep` job runs per tick by design — exports share the
        box with the bidding crons, and the doorbell triggers an extra sweep the
        moment a job is enqueued, so queue latency is not gated on the minute.
        """
        reaped = await self.reap()

        claimed: List[str] = []
        results: List[Dict[str, Any]] = []
        for _ in range(max(1, self.max_jobs_per_sweep)):
            job = await self._claim_next_queued()
            if not job:
                break
            claimed.append(job["job_id"])
            results.append(await self._execute(job))

        return {
            "success": True,
            "worker_id": self.worker_id,
            "reaped": reaped,
            "claimed": claimed,
            "results": results,
        }

    #: The cron job registers `sweep`; this alias matches the house naming so the
    #: processor reads like its neighbours.
    async def execute_cron_job(self) -> Dict[str, Any]:
        return await self.sweep()


#: Singleton, the convention every other processor in this package follows.
analytics_export_processor = AnalyticsExportProcessor()
