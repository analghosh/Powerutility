"""
Plain-language health rules for scheduled jobs, used by the System Health page.

Two things in the run logs shape these rules:

* Every run writes a ``processing``/``running`` marker row and, separately, a
  final row. Thousands of markers are never closed, so the latest row of a job
  is usually a marker. A job's result therefore comes from its latest
  *finished* row, and "is it still running on time" from its latest row of any
  kind.
* ``execution_start`` is the server's local wall clock stored as if it were
  UTC, so its meaning changes with the host's timezone. ``created_at`` is
  written with ``utcnow()`` and is the only timestamp compared against the
  clock here.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pytz
from croniter import croniter

from libs.utils.failure_reasons import safe_reason

# Statuses a run ends in. Anything else ("processing", "running") is a marker.
SUCCESS_STATUSES = frozenset({"success", "completed"})
PARTIAL_STATUSES = frozenset({"partial_success"})
FAILED_STATUSES = frozenset({"error", "failed", "failure", "timeout"})
FINISHED_STATUSES = SUCCESS_STATUSES | PARTIAL_STATUSES | FAILED_STATUSES

# How late a run may start before the job counts as missed. Covers slow
# starts after a restart without letting a stopped job look healthy for long.
MISSED_GRACE = timedelta(minutes=15)
# A run that starts a little before its slot (clock drift) still counts.
EARLY_START_TOLERANCE = timedelta(minutes=1)

# Job states, from the operator's point of view.
STATE_OK = "ok"
STATE_PARTIAL = "partial"
STATE_FAILED = "failed"
STATE_MISSED = "missed"
STATE_PAUSED = "paused"
ATTENTION_STATES = frozenset({STATE_PARTIAL, STATE_FAILED, STATE_MISSED})

# Why a job missed its slot, when the run logs can tell.
MISSED_RUNNER_DOWN = "runner_down"  # its service logged nothing around the slot
MISSED_NOT_STARTED = "not_started"  # its service was busy with other jobs

# Around a missed slot, how far to look for any sign of the service running.
# Every-minute jobs log constantly, so seven quiet minutes means it was down.
ACTIVITY_BEFORE = timedelta(minutes=2)
ACTIVITY_AFTER = timedelta(minutes=5)

# Failed data sources listed per job on the page; the rest are counted.
FAILURE_LIST_LIMIT = 12

GROUP_DATA_COLLECTION = "Data collection"
GROUP_DEMAND_SUPPLY = "Demand & supply"
GROUP_BIDDING = "Bidding & exchange"
GROUP_SETTLEMENT = "Settlement"
GROUP_HOUSEKEEPING = "Housekeeping"
GROUP_ORDER = (
    GROUP_DATA_COLLECTION,
    GROUP_DEMAND_SUPPLY,
    GROUP_BIDDING,
    GROUP_SETTLEMENT,
    GROUP_HOUSEKEEPING,
)

_HOUSEKEEPING_MARKERS = ("archival", "cleanup", "migration", "retry_failed", "export_sweep", "date_update")
_SETTLEMENT_MARKERS = ("dsm", "urs")
_BIDDING_MARKERS = ("bid", "exchange", "limit_values", "rtm_", "dam_", "gdam", "hpdam")


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Mongo hands back naive datetimes; ``created_at`` is UTC, so label it."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def job_group(job_type: str, service: Optional[str]) -> str:
    """The area of the system a job belongs to, for grouping on the page."""
    job_type = (job_type or "").lower()
    if any(marker in job_type for marker in _HOUSEKEEPING_MARKERS):
        return GROUP_HOUSEKEEPING
    if service == "ingestion":
        return GROUP_DATA_COLLECTION
    if any(marker in job_type for marker in _SETTLEMENT_MARKERS):
        return GROUP_SETTLEMENT
    if any(marker in job_type for marker in _BIDDING_MARKERS):
        return GROUP_BIDDING
    return GROUP_DEMAND_SUPPLY


def last_due_time(schedule: str, tz_name: str, now: datetime) -> Optional[datetime]:
    """
    The latest slot the job should have started in by now, allowing for
    ``MISSED_GRACE``. None when the schedule can't be read or never fires.
    """
    if not schedule:
        return None
    try:
        tz = pytz.timezone(tz_name or "Asia/Kolkata")
        base = (now - MISSED_GRACE).astimezone(tz)
        return croniter(schedule, base).get_prev(datetime).astimezone(timezone.utc)
    except Exception:
        return None


def classify_job(
    job: Dict[str, Any],
    last_started_at: Optional[datetime],
    last_finished: Optional[Dict[str, Any]],
    now: datetime,
) -> Dict[str, Any]:
    """
    Decide a job's state.

    ``job`` is its ``job_data`` document, ``last_started_at`` the UTC
    ``created_at`` of its newest run row of any status, and ``last_finished``
    its newest finished row (``status``, ``created_at``, ``error_message``).

    Precedence: paused, then missed (it isn't running at all, so whatever the
    last result was is stale), then the last result.
    """
    if not job.get("enabled", False):
        return {"state": STATE_PAUSED, "due_at": None}

    last_started_at = as_utc(last_started_at)
    due_at = last_due_time(job.get("schedule", ""), job.get("timezone"), now)
    created_at = as_utc(job.get("created_at"))
    was_due = due_at is not None and (created_at is None or due_at >= created_at)
    if was_due and (
        last_started_at is None or last_started_at < due_at - EARLY_START_TOLERANCE
    ):
        return {"state": STATE_MISSED, "due_at": due_at}

    status = (last_finished or {}).get("status")
    status = status.lower() if isinstance(status, str) else None
    if status in FAILED_STATUSES:
        state = STATE_FAILED
    elif status in PARTIAL_STATUSES:
        state = STATE_PARTIAL
    else:
        state = STATE_OK
    return {"state": state, "due_at": due_at}


def activity_window(due_at: datetime) -> Tuple[datetime, datetime]:
    """The span checked for any run at all around a missed slot."""
    return due_at - ACTIVITY_BEFORE, due_at + ACTIVITY_AFTER


def missed_reason(service_was_running: bool) -> str:
    return MISSED_NOT_STARTED if service_was_running else MISSED_RUNNER_DOWN


def provider_failures(result_data: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    The data sources a run failed on, as recorded by the ingestion processors
    (``failed_provider_details``). Reasons are re-masked here in case a row was
    written before masking existed.
    """
    details = (result_data or {}).get("failed_provider_details") or []
    failures = []
    for item in details:
        if not isinstance(item, dict):
            continue
        name = item.get("provider_name") or item.get("provider_code")
        if not name:
            continue
        failures.append({
            "name": str(name),
            "code": str(item.get("provider_code") or name),
            "reason": safe_reason(item.get("error")) or "Unknown error",
        })
    return failures


# "No data" is not a failure on the page. The ingestion API processor counts a
# source that returned nothing as failed, while the scrape processor counts
# the same thing as a successful run; PTC trades on IEX only, so the PXIL DSM
# and HPX trade feeds return nothing on most runs by design. A source that
# errors (can't connect, timed out, bad data) is still a failure.
_NO_DATA = re.compile(r"\bno data (fetched|to store)\b|\b0 records\b", re.IGNORECASE)


def is_no_data(reason: Optional[str]) -> bool:
    return bool(reason) and bool(_NO_DATA.search(reason))


def split_no_data(failures: List[Dict[str, str]]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """(real failures, sources that simply had nothing to return)."""
    real = [failure for failure in failures if not is_no_data(failure["reason"])]
    empty = [failure for failure in failures if is_no_data(failure["reason"])]
    return real, empty


# ---- one run, several rows --------------------------------------------------
#
# A run can write more than one final row under the same execution_id: the job
# records its own result (often "error") and the scheduler's wrapper records
# "success" because the job function returned without raising. Judging a job
# by whichever row is newest made failing jobs blink between red and green.
# A run is as bad as its worst row, and only a run with no failed row counts
# as having worked.

def _status(row: Optional[Dict[str, Any]]) -> str:
    status = (row or {}).get("status")
    return status.lower() if isinstance(status, str) else ""


def _severity(row: Dict[str, Any]) -> int:
    status = _status(row)
    if status in FAILED_STATUSES:
        return 3
    if status in PARTIAL_STATUSES:
        return 2
    if status in SUCCESS_STATUSES:
        return 1
    return 0


def worst_row(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The finished row that best describes a run: its worst, newest first."""
    finished = [row for row in rows if _status(row) in FINISHED_STATUSES]
    if not finished:
        return None
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    return max(finished, key=lambda row: (_severity(row), as_utc(row.get("created_at")) or epoch))


def last_clean_run(rows: List[Dict[str, Any]], complete: bool = True) -> Optional[Dict[str, Any]]:
    """
    The newest run in ``rows`` (newest first) in which nothing failed: every
    row of it is a success. A wrapper "success" beside the job's own "error"
    does not count.

    ``complete`` is False when ``rows`` was cut off at a limit: the oldest run
    in it may then be missing its other rows (a wrapper "success" kept, the
    job's "error" just past the limit), so that run is not trusted.
    """
    runs: Dict[Any, List[Dict[str, Any]]] = {}
    order: List[Any] = []
    for index, row in enumerate(rows):
        key = row.get("execution_id") or ("row", index)
        if key not in runs:
            runs[key] = []
            order.append(key)
        runs[key].append(row)
    if not complete and order:
        order = order[:-1]
    for key in order:
        run = runs[key]
        if all(_status(row) in SUCCESS_STATUSES for row in run):
            return run[0]
    return None


def run_problem(row: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    What went wrong in a run, in one line. The job's error message when it
    wrote one; otherwise its failed steps (pipelines record those in
    ``result_data.steps`` and leave the message empty), or its own summary.
    """
    if not row:
        return None
    if row.get("error_message"):
        return str(row["error_message"])
    result = row.get("result_data") or {}
    problems = []
    for step in result.get("steps") or []:
        if not isinstance(step, dict):
            continue
        status = str(step.get("status") or "").lower()
        if status in SUCCESS_STATUSES:
            continue
        name = step.get("name") or step.get("step") or "A step"
        text = f"{name}: {step.get('message') or status or 'failed'}"
        outcome = step.get("result") if isinstance(step.get("result"), dict) else {}
        failed, total = outcome.get("failed_count"), outcome.get("blocks_total")
        if isinstance(failed, int) and isinstance(total, int) and total:
            text += f" ({failed} of {total} blocks failed)"
        problems.append(text)
    if problems:
        return "; ".join(problems)
    return str(result["message"]) if result.get("message") else None


# ---- run history ------------------------------------------------------------
#
# One entry per run, however many rows it wrote. A run with no finished row is
# still running, unless its start marker is old: then the process stopped
# mid-run (a restart, a crash) and the run never finished.

RUN_OK = "ok"
RUN_PARTIAL = "partial"
RUN_FAILED = "failed"
RUN_RUNNING = "running"
RUN_UNFINISHED = "unfinished"
RUN_PROBLEMS = frozenset({RUN_PARTIAL, RUN_FAILED, RUN_UNFINISHED})

RUNNING_STALE = timedelta(hours=1)
# However quick a job normally is, give a run at least this long before
# calling it unfinished; slower jobs get a few times their usual duration.
RUNNING_STALE_MIN = timedelta(minutes=10)
RUNNING_STALE_FACTOR = 3
# A run that starts within this long after one of its schedule's slots was
# started by the clock (they start within a second); anything else was
# triggered (new data, a retry), even a few seconds later.
SCHEDULED_WINDOW = timedelta(seconds=10)


def running_stale_after(durations: List[Optional[float]]) -> timedelta:
    """How long a run may go without finishing before it counts as unfinished."""
    finished = [d for d in durations if isinstance(d, (int, float)) and d >= 0]
    if not finished:
        return RUNNING_STALE
    return max(RUNNING_STALE_MIN, timedelta(seconds=max(finished) * RUNNING_STALE_FACTOR))


def run_status(
    statuses: List[Optional[str]],
    last_seen: Optional[datetime],
    now: datetime,
    stale_after: timedelta = RUNNING_STALE,
) -> str:
    """A run's status from every row it wrote."""
    seen = {str(status).lower() for status in statuses if status}
    if seen & FAILED_STATUSES:
        return RUN_FAILED
    if seen & PARTIAL_STATUSES:
        return RUN_PARTIAL
    if seen & SUCCESS_STATUSES:
        return RUN_OK
    last_seen = as_utc(last_seen)
    if last_seen is not None and now - last_seen < stale_after:
        return RUN_RUNNING
    return RUN_UNFINISHED


def run_trigger(started_at: Optional[datetime], schedule: Optional[str], tz_name: Optional[str]) -> Optional[str]:
    """"scheduled" when a run started on one of its schedule's slots, else "triggered"."""
    started_at = as_utc(started_at)
    if started_at is None or not schedule:
        return None
    try:
        tz = pytz.timezone(tz_name or "Asia/Kolkata")
        slot = croniter(schedule, (started_at + timedelta(seconds=1)).astimezone(tz)).get_prev(datetime)
    except Exception:
        return None
    return "scheduled" if started_at - slot.astimezone(timezone.utc) <= SCHEDULED_WINDOW else "triggered"


def run_detail(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    What a problem run's worst row says: the cause, the data sources that
    failed, and the ones that only had nothing to return. ``clean`` is True
    when every failure was an empty source, which the page treats as fine.
    """
    failures = provider_failures((row or {}).get("result_data"))
    real, empty = split_no_data(failures)
    clean = bool(failures) and not real
    return {
        "clean": clean,
        "message": None if clean else run_problem(row),
        "failures": real,
        "no_data_sources": sorted({failure["name"] for failure in empty}),
    }


# ---- what the schedulers actually registered --------------------------------
#
# job_data is not what runs. The scheduler registers several jobs from a
# hard-coded fallback list in its cron_manager, with schedules and on/off
# switches that differ from job_data (peak_demand_compute: job_data */15,
# registered 0 */6; data_migration: enabled in job_data, never registered).
# Each service's GET /cron/status lists what it registered; these helpers turn
# that into the schedule a job really runs on.

_TRIGGER_FIELD = re.compile(r"(\w+)='([^']*)'")


def cron_from_trigger(trigger: Optional[str]) -> Optional[str]:
    """
    APScheduler's ``str(CronTrigger)`` as a five-field cron expression:
    ``cron[month='*', day='*', day_of_week='*', hour='*/6', minute='0']`` ->
    ``0 */6 * * *``. None for anything that isn't a cron trigger.
    """
    if not trigger or not trigger.startswith("cron["):
        return None
    fields = dict(_TRIGGER_FIELD.findall(trigger))
    return " ".join((
        fields.get("minute", "0"),
        fields.get("hour", "*"),
        fields.get("day", "*"),
        fields.get("month", "*"),
        fields.get("day_of_week", "*"),
    ))


def registered_jobs(cron_status: Optional[Mapping[str, Any]]) -> Optional[Dict[str, str]]:
    """
    ``{job_type: cron}`` from one service's /cron/status body, or None when the
    body isn't usable (service down, or a build without the endpoint).
    """
    if not isinstance(cron_status, Mapping):
        return None
    listing = cron_status.get("jobs")
    if not isinstance(listing, Mapping) or not isinstance(listing.get("jobs"), list):
        return None
    jobs = {}
    for job in listing["jobs"]:
        if isinstance(job, Mapping) and job.get("job_id"):
            jobs[str(job["job_id"])] = cron_from_trigger(job.get("trigger"))
    return jobs


def live_schedule(
    job: Mapping[str, Any], registered: Mapping[str, Optional[Dict[str, str]]]
) -> Dict[str, Any]:
    """
    How a job is really scheduled, given ``{service: registered_jobs(...)}``.
    ``scheduled`` is None when that can't be told (a listing is missing), and
    the caller falls back to job_data.
    """
    job_type = job.get("job_type")
    own = job.get("service")
    order = [own] + [service for service in registered if service != own]
    for service in order:
        jobs = registered.get(service)
        if jobs and job_type in jobs:
            return {"scheduled": True, "schedule": jobs[job_type], "service": service}
    if registered and all(jobs is not None for jobs in registered.values()):
        return {"scheduled": False, "schedule": None, "service": None}
    return {"scheduled": None, "schedule": None, "service": None}
