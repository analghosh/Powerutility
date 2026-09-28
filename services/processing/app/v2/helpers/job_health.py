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

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import pytz
from croniter import croniter

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
