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
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from bson import ObjectId

from ...core.database import get_audit_database, get_auth_database

logger = logging.getLogger(__name__)

RUN_LOG_COLLECTIONS = ("job_detail", "job_details")

# Which run log each service writes, for "was it running at the time?".
RUN_LOG_BY_SERVICE = {"scheduler": "job_detail", "ingestion": "job_details"}

PAGE_MENU_PATH = "/admin/system-health"

_ROW_FIELDS = {
    "_id": 0,
    "execution_id": 1,
    "status": 1,
    "created_at": 1,
    "error_message": 1,
    "result_data.message": 1,
    "result_data.steps": 1,
    "result_data.failed_provider_details": 1,
}


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
        self.auth_db = get_auth_database()

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

    async def run_rows(self, job_id: str, execution_id: str) -> List[Dict[str, Any]]:
        """
        Every row one run wrote. A run can write several final rows under one
        execution_id (the job's own result and the scheduler wrapper's), and
        they don't always agree.
        """
        query = {"job_id": _job_id_filter(job_id), "execution_id": execution_id}
        batches = await asyncio.gather(
            *(
                self.audit_db[name].find(query, _ROW_FIELDS).to_list(length=50)
                for name in RUN_LOG_COLLECTIONS
            )
        )
        return [row for batch in batches for row in batch]

    async def recent_runs(
        self, job_id: str, statuses: Iterable[str], limit: int
    ) -> List[Dict[str, Any]]:
        """The job's newest rows with these statuses, newest first, from both logs."""
        query = {"job_id": _job_id_filter(job_id), "status": {"$in": list(statuses)}}
        batches = await asyncio.gather(
            *(
                self.audit_db[name]
                .find(query, _ROW_FIELDS)
                .sort("execution_start", -1)
                .limit(limit)
                .to_list(length=limit)
                for name in RUN_LOG_COLLECTIONS
            )
        )
        rows = [row for batch in batches for row in batch if row.get("created_at")]
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return rows[:limit]

    async def any_run_between(self, collection: str, start: datetime, end: datetime) -> bool:
        """
        Whether the service writing ``collection`` logged any run at all in the
        window. Every-minute jobs make a running service log constantly, so an
        empty window means the service itself was not running.
        """
        row = await self.audit_db[collection].find_one(
            {"created_at": {"$gte": start, "$lte": end}}, {"_id": 1}
        )
        return row is not None

    async def roles_have_page(self, role_names: Iterable[str]) -> bool:
        """Whether any of these roles is granted the System Health menu item."""
        names = [name for name in role_names if name]
        if not names:
            return False
        menu = await self.auth_db["menus"].find_one({"path": PAGE_MENU_PATH}, {"_id": 1})
        if not menu:
            return False
        roles = await self.auth_db["roles"].find({"name": {"$in": names}}, {"_id": 1}).to_list(length=100)
        role_ids = [str(role["_id"]) for role in roles]
        if not role_ids:
            return False
        grant = await self.auth_db["menu_roles"].find_one(
            {
                "menu_id": str(menu["_id"]),
                "role_id": {"$in": role_ids},
                "is_active": {"$ne": False},
            },
            {"_id": 1},
        )
        return grant is not None


system_health_repository = SystemHealthRepository()
