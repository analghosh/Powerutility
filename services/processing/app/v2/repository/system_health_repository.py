"""
Run-log reads for the System Health page.

Scheduled jobs are defined in ``audit_db.job_data`` and log their runs in two
collections: the scheduler writes ``job_detail`` and the ingestion service
writes ``job_details``. Some jobs run under both, so every lookup checks both
and keeps the newer row.

``job_id`` is stored as a string in both run logs, so it is matched as a
string (and as an ObjectId, in case an older writer stored it that way).
Rows are ordered by ``execution_start``, which has a ``(job_id,
execution_start)`` index in both collections; it is only ever compared within
one job, where the writer's clock is consistent. Times reported back are the
UTC ``created_at``.
"""

import asyncio
import logging
from typing import Any, Dict, Iterable, List, Optional

from bson import ObjectId

from ...core.database import get_audit_database

logger = logging.getLogger(__name__)

RUN_LOG_COLLECTIONS = ("job_detail", "job_details")

_ROW_FIELDS = {"_id": 0, "status": 1, "created_at": 1, "error_message": 1}


def _job_id_filter(job_id: str) -> Dict[str, Any]:
    ids: List[Any] = [job_id]
    if ObjectId.is_valid(job_id):
        ids.append(ObjectId(job_id))
    return {"$in": ids}


def _newest(rows: Iterable[Optional[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    present = [row for row in rows if row and row.get("created_at")]
    return max(present, key=lambda row: row["created_at"], default=None)


class SystemHealthRepository:
    """Reads job definitions and each job's latest run rows."""

    def __init__(self):
        self.audit_db = get_audit_database()

    async def ping(self) -> None:
        """Round-trip to MongoDB; raises if the database can't be reached."""
        await self.audit_db.command("ping")

    async def get_all_jobs(self) -> List[Dict[str, Any]]:
        cursor = self.audit_db["job_data"].find({})
        return await cursor.to_list(length=1000)

    async def latest_run(
        self, job_id: str, statuses: Optional[Iterable[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        The job's newest run row across both run logs, optionally limited to
        some statuses. ``None`` when it has never logged one.
        """
        query: Dict[str, Any] = {"job_id": _job_id_filter(job_id)}
        if statuses is not None:
            query["status"] = {"$in": list(statuses)}

        rows = await asyncio.gather(
            *(
                self.audit_db[name].find_one(
                    query, _ROW_FIELDS, sort=[("execution_start", -1)]
                )
                for name in RUN_LOG_COLLECTIONS
            )
        )
        return _newest(rows)


system_health_repository = SystemHealthRepository()
