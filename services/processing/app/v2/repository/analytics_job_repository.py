"""Export job rows for the analytics dashboard (`epms.analytics_export_jobs`).

The job row outlives the file it produced. The generated file is deleted after
`ANALYTICS_EXPORT_RETENTION_HOURS` (72h); the row is kept for
`ANALYTICS_JOB_RETENTION_DAYS` (90 days) so the Regenerate button can rebuild the
export on demand. That only works if the row is self-contained, which is why
`create()` refuses a job whose `filters` are not a complete description of the
query: there is no other record of what the user picked, and the failure would
otherwise surface days later, at Regenerate time, in front of the person who
needed it.

Document shape is the design spec's §8 schema, verbatim — no extra bookkeeping
fields. Liveness is tracked on `heartbeat_at`, which the reaper indexes.

Indexes live in `migrations/analytics_indexes_2026_09_17.py`:
    (user_id, created_at desc) · (status, heartbeat_at) · (user_id, request_hash, status)
"""

import copy
import logging
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pymongo import ReturnDocument

from ...core.config import settings
from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_ASSEMBLING = "assembling"
STATUS_READY = "ready"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"

#: Statuses that still hold a place in the queue. Dedupe attaches a repeat request
#: to one of these; a finished job must never block a fresh one.
ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING, STATUS_ASSEMBLING)

STATUSES = ACTIVE_STATUSES + (
    STATUS_READY, STATUS_FAILED, STATUS_EXPIRED, STATUS_CANCELLED,
)

#: Without every one of these the job cannot be re-run from the row alone.
REQUIRED_FILTER_KEYS = (
    "datasets",
    "from_date",
    "to_date",
    "from_block",
    "to_block",
    "date_grouping",
    "block_grouping",
    "plants",
    "format",
)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_LIST_LIMIT = 50
DEFAULT_REAP_LIMIT = 25
DEFAULT_EXPIRE_LIMIT = 200

_NO_ID = {"_id": 0}


class IncompleteFilters(ValueError):
    """A job was submitted without enough filters to reconstruct it later."""

    def __init__(self, missing):
        self.missing = list(missing)
        super().__init__(
            "Export job filters are incomplete — the job would not be "
            f"reproducible by Regenerate. Missing: {', '.join(self.missing)}"
        )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _jsonable(value: Any) -> Any:
    """Make a filter value safe for both BSON and a JSON round trip.

    `bson` cannot encode `datetime.date` at all — it raises InvalidDocument — and a
    Pydantic request model hands us exactly that for the date range. Dates become
    ISO strings, which is also what the frontend sends back on Regenerate.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def validate_filters(filters: Any) -> Dict[str, Any]:
    """Return a stored-ready copy of `filters`, or raise `IncompleteFilters`."""
    if not isinstance(filters, dict):
        raise IncompleteFilters(REQUIRED_FILTER_KEYS)

    missing = [k for k in REQUIRED_FILTER_KEYS if k not in filters]

    # CUSTOM block grouping is defined by its ranges; without them the grouping
    # is only plausible, not reproducible.
    if str(filters.get("block_grouping") or "").upper() == "CUSTOM":
        if not filters.get("custom_blocks"):
            missing.append("custom_blocks")

    if missing:
        raise IncompleteFilters(missing)

    return _jsonable(copy.deepcopy(filters))


def build_job_document(job: Dict[str, Any], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Compose the §8 job document from a caller's request dict."""
    now = now or _utcnow()

    user_id = job.get("user_id")
    if not user_id:
        raise ValueError("Export job requires a user_id — the row is the ownership record")

    request_hash = job.get("request_hash")
    if not request_hash:
        raise ValueError("Export job requires a request_hash for dedupe")

    return {
        "job_id": job.get("job_id") or uuid.uuid4().hex,
        "user_id": user_id,
        "request_hash": request_hash,
        "filters": validate_filters(job.get("filters")),
        "label": job.get("label") or "",
        "status": STATUS_QUEUED,
        "cells_estimate": int(job.get("cells_estimate") or 0),
        "chunks_total": int(job.get("chunks_total") or 0),
        "chunks_done": 0,
        "parts": [],
        "result": None,
        "download_token": None,
        "token_expires_at": None,
        "error": None,
        "attempts": 0,
        "max_attempts": int(job.get("max_attempts") or DEFAULT_MAX_ATTEMPTS),
        "worker_id": None,
        "heartbeat_at": None,
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "expires_at": None,
    }


class AnalyticsJobRepository(GenericRepository):
    """Async repository for the analytics export job queue."""

    def __init__(self, collection_name: Optional[str] = None):
        super().__init__()
        self.collection_name = collection_name or settings.MONGODB_ANALYTICS_JOBS_COLLECTION
        self._collection_override = None
        logger.info(
            "AnalyticsJobRepository initialized with collection: %s", self.collection_name
        )

    @property
    def collection(self):
        # Settable so unit tests can hand in an AsyncMock, the house convention.
        override = getattr(self, "_collection_override", None)
        if override is not None:
            return override
        return self.db[self.collection_name]

    @collection.setter
    def collection(self, value):
        self._collection_override = value

    # ------------------------------------------------------------ create ---

    async def create(self, job: Dict[str, Any]) -> str:
        """Insert a queued job and return its `job_id`.

        Raises `IncompleteFilters` before touching Mongo if the filter echo would
        not support Regenerate.
        """
        doc = build_job_document(job, now=_utcnow())
        await self.collection.insert_one(doc)
        logger.info(
            "Queued analytics export %s for user %s (%s cells, %s chunks)",
            doc["job_id"], doc["user_id"], doc["cells_estimate"], doc["chunks_total"],
        )
        return doc["job_id"]

    # ------------------------------------------------------------- reads ---

    async def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        return await self.collection.find_one({"job_id": job_id}, _NO_ID)

    async def list_for_user(
        self, user_id: str, limit: int = DEFAULT_LIST_LIMIT
    ) -> List[Dict[str, Any]]:
        """This user's jobs, newest first. Scoped by user_id — the ownership boundary."""
        cursor = (
            self.collection.find({"user_id": user_id}, _NO_ID)
            .sort([("created_at", -1)])
            .limit(int(limit))
        )
        return await cursor.to_list(length=int(limit))

    async def find_active_by_hash(
        self, user_id: str, request_hash: str
    ) -> Optional[Dict[str, Any]]:
        """An identical in-flight request by the same user, if one exists."""
        return await self.collection.find_one(
            {
                "user_id": user_id,
                "request_hash": request_hash,
                "status": {"$in": list(ACTIVE_STATUSES)},
            },
            _NO_ID,
        )

    # ------------------------------------------------------------ worker ---

    async def mark(self, job_id: str, status: str, **fields) -> bool:
        """Move a job to `status`, deriving the timestamps that status implies.

        Explicit keyword fields always win over the derived ones.
        """
        if status not in STATUSES:
            raise ValueError(f"Unknown export job status: {status!r}")

        now = _utcnow()
        derived: Dict[str, Any] = {"status": status}

        if status == STATUS_RUNNING:
            derived["started_at"] = now
            derived["heartbeat_at"] = now
        elif status == STATUS_READY:
            expires_at = now + timedelta(hours=settings.ANALYTICS_EXPORT_RETENTION_HOURS)
            derived["finished_at"] = now
            derived["expires_at"] = expires_at
            # The token is only useful while the file exists, so it dies with it.
            derived["download_token"] = secrets.token_urlsafe(32)
            derived["token_expires_at"] = expires_at
        elif status in (STATUS_FAILED, STATUS_CANCELLED, STATUS_EXPIRED):
            derived["finished_at"] = now

        derived.update(fields)
        # A caller-supplied expiry moves the token with it unless it says otherwise.
        if status == STATUS_READY and "expires_at" in fields and "token_expires_at" not in fields:
            derived["token_expires_at"] = fields["expires_at"]

        result = await self.collection.update_one({"job_id": job_id}, {"$set": derived})
        return bool(getattr(result, "matched_count", 0))

    async def heartbeat(self, job_id: str, worker_id: str) -> bool:
        """Refresh liveness. False means this worker no longer owns the job.

        The claim is part of the filter on purpose: once the reaper has requeued a
        stale job and another worker has taken it, the original worker's heartbeat
        must not be able to win it back.
        """
        result = await self.collection.update_one(
            {"job_id": job_id, "worker_id": worker_id},
            {"$set": {"heartbeat_at": _utcnow()}},
        )
        return bool(getattr(result, "matched_count", 0))

    async def record_part(self, job_id: str, part: Dict[str, Any]) -> bool:
        """Append one chunk receipt. Appends — a `$set` here would destroy a resume.

        Idempotent on `part["idx"]`, so a retry that re-runs a chunk cannot
        double-count it.
        """
        query: Dict[str, Any] = {"job_id": job_id}
        if "idx" in part:
            query["parts.idx"] = {"$ne": part["idx"]}

        result = await self.collection.update_one(
            query,
            {
                "$push": {"parts": part},
                "$inc": {"chunks_done": 1},
                "$set": {"heartbeat_at": _utcnow()},
            },
        )
        return bool(getattr(result, "matched_count", 0))

    # ------------------------------------------------------------ reaper ---

    async def claim_orphans(
        self, stale_before: datetime, limit: int = DEFAULT_REAP_LIMIT
    ) -> List[Dict[str, Any]]:
        """Requeue running jobs whose worker stopped heartbeating.

        Claims one at a time with `find_one_and_update` so two scheduler instances
        cannot requeue the same job twice. Jobs that have used up `max_attempts`
        end at `failed` rather than cycling forever.
        """
        now = _utcnow()
        requeued: List[Dict[str, Any]] = []

        for _ in range(int(limit)):
            doc = await self.collection.find_one_and_update(
                {
                    "status": STATUS_RUNNING,
                    "heartbeat_at": {"$lt": stale_before},
                    # attempts is compared against a sibling field, so $expr.
                    "$expr": {"$lt": ["$attempts", "$max_attempts"]},
                },
                {
                    "$set": {
                        "status": STATUS_QUEUED,
                        "worker_id": None,
                        "heartbeat_at": None,
                        "started_at": None,
                    },
                    "$inc": {"attempts": 1},
                },
                projection=_NO_ID,
                sort=[("heartbeat_at", 1)],
                return_document=ReturnDocument.AFTER,
            )
            if not doc:
                break
            requeued.append(doc)

        exhausted = await self.collection.update_many(
            {
                "status": STATUS_RUNNING,
                "heartbeat_at": {"$lt": stale_before},
                "$expr": {"$gte": ["$attempts", "$max_attempts"]},
            },
            {
                "$set": {
                    "status": STATUS_FAILED,
                    "finished_at": now,
                    "error": {
                        "code": "max_attempts_exceeded",
                        "message": "Export abandoned after repeated worker failures",
                        "dataset": None,
                    },
                }
            },
        )

        if requeued or getattr(exhausted, "modified_count", 0):
            logger.warning(
                "Analytics export reaper: requeued %s, failed %s",
                len(requeued), getattr(exhausted, "modified_count", 0),
            )
        return requeued

    # ------------------------------------------------------------ expiry ---

    async def expire_before(
        self, cutoff: datetime, limit: int = DEFAULT_EXPIRE_LIMIT
    ) -> List[Dict[str, Any]]:
        """Mark ready jobs past `cutoff` expired and hand back their file paths.

        The ROW IS NOT DELETED. It carries the filter set that powers Regenerate
        for the remaining 90-day retention; only the file on disk goes, and the
        caller is the one that unlinks it.
        """
        expired: List[Dict[str, Any]] = []

        for _ in range(int(limit)):
            doc = await self.collection.find_one_and_update(
                {"status": STATUS_READY, "expires_at": {"$lte": cutoff}},
                {
                    "$set": {
                        "status": STATUS_EXPIRED,
                        "download_token": None,
                        "token_expires_at": None,
                    }
                },
                projection=_NO_ID,
                sort=[("expires_at", 1)],
                return_document=ReturnDocument.AFTER,
            )
            if not doc:
                break
            expired.append(doc)

        if expired:
            logger.info("Analytics exports expired: %s", len(expired))
        return expired
