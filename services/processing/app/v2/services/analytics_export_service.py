"""Export mode selection and job enqueue for the analytics dashboard.

`AnalyticsService` answers the question; this module decides what shape the answer
leaves the building in.

**The dashboard no longer queues** (PTC, 2026-09-20). A file is generated inside the
request and downloaded, or it is refused with concrete narrowings — never promised
for later. `export()` therefore has one outcome: serve, or 413. The queue below it
(`_enqueue`, the worker, the retry route) is intact and still owns the rows already
in Mongo, but nothing in the filter panel reaches it.

That leaves the threshold doing a different job: it is now the line above which a
request is refused rather than deferred. Four things about it are worth stating
because none of them is obvious from the code alone.

**1. The threshold is per format, and the gap between the two numbers is the
point.** CSV yields rows as it goes: a 500k-cell stream costs constant memory,
the first byte is out in ~200ms, and a disconnect wastes nothing. XLSX emits no
bytes at all until openpyxl finalises the zip central directory, so the identical
request holds a web worker for a minute with nothing to show for it. One shared
threshold would either queue CSVs that could have streamed or block a worker on a
large workbook. Hence `MODE_THRESHOLDS`: CSV rides `ANALYTICS_CELLS_INLINE_CAPPED`
(500k), XLSX rides `ANALYTICS_CELLS_INLINE` (150k).

The CSV ceiling is deliberately the *same* number the inline query path refuses
above, so a request that streams here is always one `AnalyticsService.query` will
serve; the two move together because both read the one setting.

**2. RabbitMQ is a doorbell, not a dependency.** The row is written first and the
broker is rung second, and a failure to ring it changes nothing about the outcome.
`USE_RABBITMQ_JOBS` is false locally and the broker path has never worked in this
codebase, so the scheduler's Mongo sweep is the *primary* path and a publish
returning False is the normal case. The publish also has to be the async variant:
`publish_job_trigger` opens a blocking pika connection, and a blocking call inside
an async handler is precisely the defect that makes a login rush 401 the platform.

**3. The disk budget is checked before the row is written.** This is a single EC2
box with no HA; filling the root volume takes the bidding screens down with it,
and bidding is the money path. A job created and *then* found unaffordable is a
job the worker will still pick up. Dedupe runs first, because attaching to an
in-flight job adds no bytes — refusing it would only hide a running export from
the person waiting for it.

**4. Permissions are applied before the estimate, not after.** A queued job runs
in the scheduler, hours later, without the caller's token; whatever datasets land
in the stored filter set are what the worker exports. A denied dataset that
survived into the row would be an authorization hole with a 90-day lifetime. It
also has to happen before the budget, or a dataset the user may not see could push
an affordable export into the queue.

Inline XLSX is written to a **private temp directory**, not `ANALYTICS_EXPORT_DIR`.
That directory defaults to `/var/lib/epms/exports`, which does not exist until a
job creates it and is not writable by an unprivileged service user on the PROD box;
making the synchronous in-request path depend on it would fail the small exports
that are supposed to be the easy case. The outcome carries `cleanup_dir` so the
route can delete it in a background task once the file has been sent.
"""
import asyncio
import hashlib
import logging
import math
import os
import shutil
import tempfile
from datetime import date
from typing import Any, Dict, Optional, Sequence

from ...core.config import settings as default_settings
from ..analytics import budget, registry
from ..analytics.models import AnalyticsQueryRequest, ExportRequest
from ..helpers import analytics_csv, analytics_layout, analytics_xlsx
from ..repository.analytics_job_repository import (STATUS_CANCELLED, STATUS_FAILED,
                                                   STATUS_READY, STATUS_RUNNING)
from .analytics_service import AnalyticsService, BudgetExceeded

logger = logging.getLogger(__name__)

MODE_STREAM = "stream"
MODE_INLINE = "inline"
MODE_JOB = "job"

#: Default cell ceiling for an in-request export, by format. The values are read
#: from the process settings at import so the constant matches what the service
#: will actually do; `AnalyticsExportService.threshold_for` re-reads the injected
#: settings and is the authoritative answer for a given instance.
MODE_SETTING_KEYS = {
    "csv": "ANALYTICS_CELLS_INLINE_CAPPED",
    "xlsx": "ANALYTICS_CELLS_INLINE",
}
MODE_THRESHOLDS = {
    fmt: int(getattr(default_settings, key))
    for fmt, key in MODE_SETTING_KEYS.items()
}

MEDIA_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

#: What a request that names no format gets. XLSX, because the dashboard is read
#: in Excel: one sheet per dataset with a block-by-date grid is what PTC opens,
#: and the flat CSV long form is the machine-readable half nobody asked for by
#: default. The cost is deliberate — XLSX rides the lower of the two thresholds
#: (see MODE_THRESHOLDS), so a range that would have streamed as CSV now queues.
DEFAULT_FORMAT = "xlsx"

#: The job type the scheduler's sweep and the broker consumer both key on.
JOB_TYPE = "analytics_export"
SOURCE_SERVICE = "processing"

#: §8: jobs shard by date window sized to ~50k cells per chunk. The worker owns the
#: real split (`chunks_for`); this only sizes `chunks_total` so the progress bar has
#: a denominator from the moment the row is created.
CHUNK_CELLS = 50_000

#: How often a background export writes its progress. Every row would cost a Mongo
#: round trip per row — more than the export itself; every 500 keeps a 35,000-row
#: file to 70 writes and the bar moving smoothly enough to read.
PROGRESS_EVERY_ROWS = 500

#: Temp directory prefix for an inline XLSX, so a stray one is identifiable.
TEMP_PREFIX = "epms-analytics-"

#: Filename slug, and the `module` echo stored with the job so Regenerate rebuilds
#: the same name.
MODULE_NAME = "analytics"

#: Datasets named in a label before it collapses into "+N more".
LABEL_DATASET_LIMIT = 3

MEGABYTE = 1024 * 1024


class ExportStorageExhausted(RuntimeError):
    """The export directory is over its budget, so no new job may be queued.

    Carries the numbers so the route can tell the user how much is in use rather
    than only that they were refused.
    """

    def __init__(self, used_bytes: int, budget_bytes: int, directory: str):
        self.used_bytes = used_bytes
        self.budget_bytes = budget_bytes
        self.directory = directory
        super().__init__(
            f"Export storage is full: {used_bytes / MEGABYTE:,.0f} MB of the "
            f"{budget_bytes / MEGABYTE:,.0f} MB budget is in use in {directory!r}. "
            f"Existing exports are deleted automatically after "
            f"ANALYTICS_EXPORT_RETENTION_HOURS; raise "
            f"ANALYTICS_EXPORT_DISK_BUDGET_MB or wait for one to expire."
        )


def as_export_request(request: Any) -> ExportRequest:
    """Accept a model, an export model, or the raw filter echo a job row holds.

    The stored echo says `block_grouping` (the repository's required key) while the
    request model says `block_band`; Regenerate replays the row verbatim, so the
    bridge belongs here rather than at every call site.
    """
    if isinstance(request, ExportRequest):
        return request
    if isinstance(request, AnalyticsQueryRequest):
        # A plain query promoted to an export: `format` takes its default.
        return ExportRequest(**request.model_dump())
    if isinstance(request, dict):
        data = dict(request)
        if "block_band" not in data and data.get("block_grouping"):
            data["block_band"] = data["block_grouping"]
        return ExportRequest(**data)
    raise TypeError(
        f"Expected an ExportRequest, an AnalyticsQueryRequest or a filter dict, "
        f"got {type(request).__name__}")


def cleanup(outcome: Dict[str, Any]) -> None:
    """Remove the temp directory an inline export wrote into.

    The route calls this from a `BackgroundTask` *after* the FileResponse has been
    sent — deleting the file before that would serve a truncated download.
    """
    directory = (outcome or {}).get("cleanup_dir")
    if directory:
        shutil.rmtree(directory, ignore_errors=True)


def _format_day(value: date) -> str:
    return value.strftime("%d %b")


class AnalyticsExportService:
    """Chooses stream / inline / queued, and owns the enqueue."""

    def __init__(self, service: Any = None, jobs: Any = None,
                 publisher: Any = None, settings: Any = None):
        self._service = service
        self._jobs = jobs
        self._publisher = publisher
        self.settings = settings or default_settings
        #: Background build tasks, held by job id. `asyncio.create_task` keeps only
        #: a weak reference, so a task nobody holds can be collected mid-write.
        self._running: Dict[str, "asyncio.Task"] = {}

    # ------------------------------------------------------------- plumbing

    @property
    def service(self) -> AnalyticsService:
        """The query service. Lazy for the same reason every v2 repository is:
        building a Motor client in `__init__` binds it to the wrong event loop."""
        if self._service is None:
            self._service = AnalyticsService(settings=self.settings)
        return self._service

    @property
    def jobs(self):
        if self._jobs is None:
            from ..repository.analytics_job_repository import AnalyticsJobRepository
            self._jobs = AnalyticsJobRepository()
        return self._jobs

    @property
    def publisher(self):
        """Imported lazily — `libs.messaging.job_publisher` pulls in pika, and the
        broker is optional at runtime."""
        if self._publisher is None:
            from libs.messaging.job_publisher import JobPublisher
            self._publisher = JobPublisher()
        return self._publisher

    @staticmethod
    def format_for(request: Any) -> str:
        fmt = str(getattr(request, "format", None) or DEFAULT_FORMAT).lower()
        return fmt if fmt in MEDIA_TYPES else DEFAULT_FORMAT

    def threshold_for(self, fmt: str) -> int:
        """The in-request cell ceiling for this format, from the live settings."""
        key = MODE_SETTING_KEYS.get(fmt, MODE_SETTING_KEYS[DEFAULT_FORMAT])
        return int(getattr(self.settings, key, MODE_THRESHOLDS[
            fmt if fmt in MODE_THRESHOLDS else DEFAULT_FORMAT]))

    @staticmethod
    def user_id_for(user_context: Optional[dict]) -> str:
        """The owner of a queued job. Absent is refused, not defaulted.

        `"anonymous"` is the house default for an audit field, but here the id *is*
        the authorization boundary: every download and cancel is checked against it,
        so a shared default would make one user's export downloadable by the next.
        """
        ctx = user_context or {}
        user = ctx.get("user") if isinstance(ctx.get("user"), dict) else {}
        for source, key in ((ctx, "user_id"), (user, "user_id"),
                            (user, "id"), (user, "_id"), (ctx, "username")):
            value = source.get(key)
            if value:
                return str(value)
        raise ValueError(
            "A queued export needs an authenticated owner: the job row is the "
            "ownership record every download and cancel is checked against.")

    # ---------------------------------------------------------- disk budget

    def export_dir(self) -> str:
        return str(getattr(self.settings, "ANALYTICS_EXPORT_DIR",
                           "/var/lib/epms/exports"))

    def disk_budget_bytes(self) -> int:
        """`0` (or less) disables the check — an explicit opt-out, not a default."""
        return max(0, int(getattr(
            self.settings, "ANALYTICS_EXPORT_DISK_BUDGET_MB", 0))) * MEGABYTE

    def disk_usage_bytes(self) -> int:
        """Bytes held by generated exports. A missing directory holds none — it is
        created by the first job, so "not there yet" is the normal cold state."""
        directory = self.export_dir()
        total = 0
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:  # pragma: no cover - raced with the reaper
                        continue
        except FileNotFoundError:
            return 0
        except OSError as exc:  # pragma: no cover - permissions on PROD only
            logger.warning("Cannot measure export directory %r: %s", directory, exc)
            return 0
        return total

    def check_disk_budget(self) -> int:
        """Raise `ExportStorageExhausted` if the directory is at or over budget."""
        limit = self.disk_budget_bytes()
        if limit <= 0:
            return 0
        used = self.disk_usage_bytes()
        if used >= limit:
            logger.warning(
                "Analytics export refused: %s bytes used of a %s byte budget in %r",
                used, limit, self.export_dir())
            raise ExportStorageExhausted(used, limit, self.export_dir())
        return used

    # -------------------------------------------------------- job identity

    def request_hash(self, request: Any, fmt: Optional[str] = None) -> str:
        """Dedupe key: the canonical query hash, plus the format.

        `AnalyticsService.cache_key` ignores `format` on purpose — a CSV and an XLSX
        of the same question are the same *data*. They are not the same *file*, so
        the job hash has to reintroduce it, or a user waiting on a CSV would be
        handed someone's workbook.

        The chosen file name is in the blob for the same reason. Dedupe attaches a
        repeat request to a job already in flight; without the name in the key, asking
        for the same data under a second name would silently hand back the first job
        and its first file name.
        """
        req = as_export_request(request)
        fmt = fmt or self.format_for(req)
        blob = f"{self.service.cache_key(req)}|{fmt}|{req.file_name}"
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def filters_for(self, request: Any, fmt: Optional[str] = None) -> Dict[str, Any]:
        """The complete, self-contained filter echo stored on the job row.

        Every key `AnalyticsJobRepository.create` demands is present, dates are ISO
        strings (bson cannot encode `datetime.date`), and both spellings of the
        block band are stored: `block_grouping` for the repository and the file
        header, `block_band` so the row replays straight back into the model.
        """
        req = as_export_request(request)
        fmt = fmt or self.format_for(req)
        return {
            "module": MODULE_NAME,
            "datasets": list(req.datasets),
            "from_date": req.from_date.isoformat(),
            "to_date": req.to_date.isoformat(),
            "from_block": req.from_block,
            "to_block": req.to_block,
            "date_grouping": req.date_grouping,
            "block_grouping": req.block_band,
            "block_band": req.block_band,
            "block_band_ranges": [list(pair) for pair in req.block_band_ranges],
            "custom_blocks": [list(pair) for pair in req.custom_blocks],
            "plants": list(req.plants),
            "exchanges": list(req.exchanges),
            "format": fmt,
            # What the user called it, raw. `filename_for` sanitises on the way out,
            # every time — storing a pre-sanitised name would mean the rule that made
            # it safe is frozen at the moment the job was queued, and a job row lives
            # 90 days.
            "file_name": req.file_name,
        }

    @staticmethod
    def label_for(request: Any, fmt: str) -> str:
        """The one-line description shown in My Exports.

        It is stored rather than rebuilt at read time because the row outlives the
        file by 87 days and the registry's labels can change under it; the user
        should see what they asked for, not what that dataset is called today.
        """
        req = as_export_request(request)
        codes = list(req.datasets)
        names = [registry.get(c).label for c in codes[:LABEL_DATASET_LIMIT]]
        if len(codes) > LABEL_DATASET_LIMIT:
            names.append(f"+{len(codes) - LABEL_DATASET_LIMIT} more")
        return " · ".join([
            " + ".join(names) or "No datasets",
            f"{_format_day(req.from_date)}–{_format_day(req.to_date)}",
            f"blocks {req.from_block}–{req.to_block}",
            fmt.upper(),
        ])

    @staticmethod
    def chunk_count(cells: int) -> int:
        return max(1, math.ceil(int(cells) / CHUNK_CELLS))

    # --------------------------------------------------------------- export

    async def export(self, request: Any,
                     user_context: Optional[dict] = None) -> Dict[str, Any]:
        """Serve the export now, or queue it. The only entry point.

        Order is the contract: permissions, then the budget, then the mode. Each
        step is cheaper than the one after it and each one can end the request.
        """
        req = as_export_request(request)
        fmt = self.format_for(req)
        # Stamp the named band's ranges before anything is priced, keyed or stored:
        # `with_band` returns the base model, so re-wrap it as an export request.
        stamped = await self.service.with_band(req)
        if stamped.block_band_ranges != req.block_band_ranges:
            req = req.model_copy(update={"block_band_ranges": stamped.block_band_ranges})

        # Same reasoning for the plant list: an empty one means every plant, and it is
        # resolved HERE so the budget prices the real series count and the stored job
        # row records the plants this file was actually built from.
        stamped = await self.service.with_plants(req)
        if list(stamped.plants) != list(req.plants):
            req = req.model_copy(update={"plants": list(stamped.plants)})

        permissions = self.service.permissions_for(user_context)
        allowed, denied = self.service.filter_by_permission(req.datasets, permissions)
        effective = req.model_copy(update={"datasets": allowed})

        estimate = self.service.estimate_only(effective)
        if estimate.band == budget.BAND_REFUSED:
            # Above ANALYTICS_CELLS_MAX, queueing only moves the failure to the
            # worker — and spends the disk budget on the way. The route turns this
            # into a 413 carrying the narrowings that would fit.
            logger.info(
                "Analytics export refused outright: %s cells (user=%s)",
                estimate.cells, (user_context or {}).get("user_id", "unknown"))
            raise BudgetExceeded(estimate)

        if estimate.cells > self.threshold_for(fmt):
            # Too long to hold an HTTP connection open for, but nothing is refused
            # for being merely large (PTC, 2026-09-20): it is built here, in this
            # service, reporting rows-written progress the caller can show, and the
            # client downloads it the moment it is ready. No scheduler sweep, so it
            # starts now rather than within the minute.
            logger.info(
                "Analytics export built in the background: %s cells > %s (%s, user=%s)",
                estimate.cells, self.threshold_for(fmt), fmt,
                (user_context or {}).get("user_id", "unknown"))
            return await self._serve_in_background(
                req, effective, fmt, estimate, denied, user_context)

        outcome = await self._serve_now(req, effective, fmt, estimate,
                                        denied, user_context)
        await self._record_history(effective, fmt, estimate, outcome, user_context)
        return outcome

    # --------------------------------------------------------- background

    async def _serve_in_background(self, request: ExportRequest,
                                   effective: ExportRequest, fmt: str,
                                   estimate: budget.Estimate,
                                   denied: Sequence[str],
                                   user_context: Optional[dict]) -> Dict[str, Any]:
        """Write the job row, start building, and answer immediately with its id.

        The row exists before the task starts, for the same reason the queue writes
        it before ringing the broker: the client polls by id, and an id that is not
        in Mongo yet reads as a failure.

        The task is deliberately held in `self._running`. `asyncio.create_task` keeps
        only a weak reference, so a task nobody holds can be garbage-collected
        mid-write — which would look exactly like an export that silently stopped.
        """
        user_id = self.user_id_for(user_context)
        self.check_disk_budget()

        job_id = await self.jobs.create({
            "user_id": user_id,
            "request_hash": self.request_hash(effective, fmt),
            "filters": self.filters_for(effective, fmt),
            "label": self.label_for(effective, fmt),
            "cells_estimate": estimate.cells,
            # Rows are only known once the query returns — the date axis carries the
            # days that HAVE data, not the days that were asked for. Seeded from the
            # estimate so the bar has something honest to show before then.
            "rows_total": estimate.date_buckets * estimate.block_buckets,
        })
        await self.jobs.mark(job_id, STATUS_RUNNING)

        task = asyncio.create_task(
            self._build(job_id, request, fmt, user_context),
            name=f"analytics-export-{job_id}")
        self._running[job_id] = task
        task.add_done_callback(lambda _t, jid=job_id: self._running.pop(jid, None))

        return {
            "mode": MODE_JOB,
            "job_id": job_id,
            "status": STATUS_RUNNING,
            "deduped": False,
            "published": False,
            "denied_datasets": list(denied),
            "estimated_cells": estimate.cells,
            "band": estimate.band,
            "rows_total": estimate.date_buckets * estimate.block_buckets,
        }

    async def _build(self, job_id: str, request: ExportRequest, fmt: str,
                     user_context: Optional[dict]) -> None:
        """Run the query, write the file, and mark the job ready. Never raises.

        This runs detached from any request, so an exception here has nobody to
        return it to: it is recorded on the job row, which is what the client is
        polling, and a failed export therefore says why rather than hanging at 99%.
        """
        try:
            # The inline-render ceiling does not apply here: this build has already
            # been admitted, and re-checking it would fail every background job at
            # the moment it starts. The memory cap was enforced before the job row
            # was written.
            response = await self.service.query(request, user_context,
                                                enforce_budget=False)
            filters = self.filters_for(request, fmt)
            filename = analytics_csv.filename_for(filters, fmt)
            directory = self.export_dir()
            os.makedirs(directory, exist_ok=True)
            # Prefixed with the job id: two users can name their file the same
            # thing, and the download route serves by id, not by name.
            path = os.path.join(directory, f"{job_id}_{filename}")

            total = analytics_layout.row_count(response)
            await self.jobs.set_progress(job_id, 0, rows_total=total)

            # The loop is captured HERE, on the event loop, and handed to the
            # worker thread. Reading it inside the thread returns None —
            # `get_running_loop()` is per-thread — which silently dropped every
            # progress update and left the bar at 0% until the file was finished.
            written = await asyncio.to_thread(
                self._write_file, response, filters, path, fmt, job_id, total,
                asyncio.get_running_loop())

            await self.jobs.mark(
                job_id, STATUS_READY,
                rows_done=total,
                result={"filename": filename, "path": written["path"],
                        "bytes": written["bytes"], "row_count": written["row_count"]},
            )
            logger.info("Analytics export %s ready: %s rows, %s bytes",
                        job_id, written["row_count"], written["bytes"])
        except asyncio.CancelledError:
            await self.jobs.mark(job_id, STATUS_CANCELLED,
                                 error={"code": "CANCELLED",
                                        "message": "The export was cancelled."})
            raise
        except Exception as exc:
            logger.exception("Analytics export %s failed", job_id)
            await self.jobs.mark(
                job_id, STATUS_FAILED,
                error={"code": type(exc).__name__, "message": str(exc)[:500]})

    @staticmethod
    def _csv_preamble_lines(filters: Dict[str, Any],
                            response: Dict[str, Any]) -> int:
        """Lines the CSV carries before its first data row: the `#` filter block
        plus the column headings. Subtracted so `row_count` means data rows, the
        same thing it means for the workbook."""
        return sum(1 for _ in analytics_csv.header_lines(filters, response)) + 1

    def _write_file(self, response: Dict[str, Any], filters: Dict[str, Any],
                    path: str, fmt: str, job_id: str, total: int,
                    loop=None) -> Dict[str, Any]:
        """Write the file on a worker thread, reporting progress as rows land.

        `loop` is the caller's event loop, passed in rather than looked up: this
        runs on a worker thread, and `asyncio.get_running_loop()` there raises.

        Progress is written back through `run_coroutine_threadsafe`, every
        `PROGRESS_EVERY_ROWS` rather than every row — a Mongo round trip per row
        would cost more than the export itself.
        """
        state = {"last": 0}

        def on_row(done: int) -> None:
            if done - state["last"] < PROGRESS_EVERY_ROWS and done != total:
                return
            state["last"] = done
            if loop is not None and not loop.is_closed():
                # Fire and forget: a dropped progress update must never stop the
                # write, and the next one supersedes it anyway.
                asyncio.run_coroutine_threadsafe(
                    self.jobs.set_progress(job_id, done), loop)

        if fmt == "csv":
            rows = 0
            with open(path, "w", encoding="utf-8", newline="") as handle:
                for line in analytics_csv.csv_rows(response, filters, on_row=on_row):
                    handle.write(line)
                    rows += 1
            size = os.path.getsize(path)
            # The header block and the column headings are not data rows.
            return {"path": path, "bytes": size,
                    "row_count": max(0, rows - self._csv_preamble_lines(filters, response))}

        return analytics_xlsx.write_xlsx(response, filters, path, on_row=on_row)

    async def _record_history(self, effective: ExportRequest, fmt: str,
                              estimate: budget.Estimate, outcome: Dict[str, Any],
                              user_context: Optional[dict]) -> None:
        """Remember the configuration this file was built from.

        My Exports lists these rows so a user can see what they generated and load
        it back with Execute. It is deliberately best-effort: the file is already on
        its way to the browser by the time this runs, and losing the history entry
        must never turn a delivered export into an error.
        """
        try:
            await self.jobs.record_completed({
                "user_id": self.user_id_for(user_context),
                "request_hash": self.request_hash(effective, fmt),
                "filters": self.filters_for(effective, fmt),
                "label": self.label_for(effective, fmt),
                "cells_estimate": estimate.cells,
                "result": {
                    "filename": outcome.get("filename"),
                    "row_count": outcome.get("row_count"),
                    "bytes": outcome.get("bytes"),
                },
            })
        except Exception as exc:
            logger.warning("Analytics export history not recorded: %s", exc)

    # ------------------------------------------------------------ in-request

    async def _serve_now(self, request: ExportRequest, effective: ExportRequest,
                         fmt: str, estimate: budget.Estimate,
                         denied: Sequence[str],
                         user_context: Optional[dict]) -> Dict[str, Any]:
        """Run the query and hand back a stream (CSV) or a finished file (XLSX).

        The *original* request goes to `query`, not the permission-filtered copy, so
        the response's own `meta.denied_datasets` stays accurate; the filtered copy
        is only what priced the request and what a job would have stored.
        """
        response = await self.service.query(request, user_context)
        filters = self.filters_for(effective, fmt)
        filename = analytics_csv.filename_for(filters, fmt)

        common = {
            "filename": filename,
            "media_type": MEDIA_TYPES[fmt],
            "meta": response.get("meta", {}),
            "denied_datasets": list(denied),
            "estimated_cells": estimate.cells,
            "band": estimate.band,
        }

        if fmt == "csv":
            # Not materialised: `csv_rows` is a generator, so the route's
            # StreamingResponse pulls lines out one at a time.
            return {"mode": MODE_STREAM,
                    "iterator": analytics_csv.csv_rows(response, filters),
                    **common}

        directory = tempfile.mkdtemp(prefix=TEMP_PREFIX)
        path = os.path.join(directory, filename)
        try:
            # openpyxl is synchronous and CPU-bound; running it on the event loop
            # would stall every other request on this worker for its duration.
            written = await asyncio.to_thread(
                analytics_xlsx.write_xlsx, response, filters, path)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

        return {"mode": MODE_INLINE,
                "path": written["path"],
                "bytes": written["bytes"],
                "row_count": written["row_count"],
                "cleanup_dir": directory,
                **common}

    # --------------------------------------------------------------- queued

    async def _enqueue(self, effective: ExportRequest, fmt: str,
                       estimate: budget.Estimate, denied: Sequence[str],
                       user_context: Optional[dict]) -> Dict[str, Any]:
        user_id = self.user_id_for(user_context)
        request_hash = self.request_hash(effective, fmt)

        # Dedupe first: attaching to an in-flight job costs no new bytes, and
        # refusing it on a full disk would hide a running export from the person
        # already waiting for it.
        existing = await self.jobs.find_active_by_hash(user_id, request_hash)
        if existing:
            logger.info("Analytics export deduped onto job %s for user %s",
                        existing.get("job_id"), user_id)
            return self._job_outcome(existing.get("job_id"), estimate, denied,
                                     deduped=True, published=False,
                                     status=existing.get("status"))

        self.check_disk_budget()

        job_id = await self.jobs.create({
            "user_id": user_id,
            "request_hash": request_hash,
            "filters": self.filters_for(effective, fmt),
            "label": self.label_for(effective, fmt),
            "cells_estimate": estimate.cells,
            "chunks_total": self.chunk_count(estimate.cells),
        })

        # The row exists before the doorbell rings. The other order races: a worker
        # woken by the broker would look up a job that has not been inserted yet.
        published = await self._publish(job_id, user_id)

        logger.info(
            "Queued analytics export %s for user %s (%s cells, format=%s, broker=%s)",
            job_id, user_id, estimate.cells, fmt, "rung" if published else "silent")
        return self._job_outcome(job_id, estimate, denied,
                                 deduped=False, published=published,
                                 status="queued")

    async def notify_queued(self, job_id: str, user_id: str = "") -> bool:
        """Public doorbell for a job that is already `queued` in Mongo — used by
        retry, where the row exists and only the broker nudge is missing."""
        return await self._publish(job_id, user_id)

    async def _publish(self, job_id: str, user_id: str) -> bool:
        """Ring the doorbell. Never raises — the sweep is the primary path.

        `USE_RABBITMQ_JOBS` is false on this deployment, so `False` is the ordinary
        answer and means "the scheduler will pick it up within the minute", not
        "the export failed".
        """
        try:
            publisher = self.publisher
        except Exception as exc:  # pragma: no cover - pika missing only
            logger.warning("Analytics export %s queued without a broker: %s",
                           job_id, exc)
            return False
        if publisher is None:
            return False
        try:
            return bool(await publisher.publish_job_trigger_async(
                job_type=JOB_TYPE,
                source_service=SOURCE_SERVICE,
                payload={"job_id": job_id, "user_id": user_id},
                correlation_id=job_id,
            ))
        except Exception as exc:
            logger.warning(
                "Analytics export %s queued but the broker was unreachable (%s); "
                "the scheduler sweep will claim it", job_id, exc)
            return False

    @staticmethod
    def _job_outcome(job_id: Optional[str], estimate: budget.Estimate,
                     denied: Sequence[str], *, deduped: bool, published: bool,
                     status: Optional[str]) -> Dict[str, Any]:
        return {
            "mode": MODE_JOB,
            "job_id": job_id,
            "status": status,
            "deduped": deduped,
            "published": published,
            "denied_datasets": list(denied),
            "estimated_cells": estimate.cells,
            "band": estimate.band,
            "chunks_total": AnalyticsExportService.chunk_count(estimate.cells),
        }
