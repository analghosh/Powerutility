"""
RTM Scheduling Snapshot Repository (V2)
Standalone repository for RTM scheduling snapshot data/excel exports.
"""

import asyncio
import logging
import threading
from typing import Dict, Any, List, Optional

from .base_repository import GenericRepository
from ...core.config import settings

logger = logging.getLogger(__name__)

_SNAPSHOT_TTL_SECONDS = 30 * 24 * 60 * 60


class RTMSchedulingSnapshotRepository:
    """Repository focused only on RTM snapshot collection reads."""

    _indexes_lock = threading.Lock()
    _indexes_ensured = False

    def __init__(self):
        self.repository = GenericRepository()
        self.rtm_snapshot_collection = settings.MONGODB_RTM_OPTIMIZATION_RAW_INPUTS_SNAPSHOT_COLLECTION

    def ensure_rtm_snapshot_indexes(self) -> None:
        """
        Idempotent index setup aligned with scheduler RTMSchedulingProcessor.create_indexes
        for rtm_optimization_raw_inputs_snapshot, plus a compound for date+source+session queries.
        """
        with RTMSchedulingSnapshotRepository._indexes_lock:
            if RTMSchedulingSnapshotRepository._indexes_ensured:
                return
            coll = self.repository.db[self.rtm_snapshot_collection]
            try:
                coll.create_index([("run_id", 1)], name="idx_rtm_snapshot_run_id", background=True)
                coll.create_index([("execution_id", 1)], name="idx_rtm_snapshot_execution_id", background=True)
                coll.create_index(
                    [("source_name", 1), ("schedule_date", 1)],
                    name="idx_rtm_snapshot_source_schedule_date",
                    background=True,
                )
                coll.create_index(
                    [("schedule_date", 1), ("time_block", 1), ("plant_id", 1)],
                    name="idx_rtm_snapshot_schedule_block_plant",
                    background=True,
                )
                try:
                    coll.create_index(
                        [("created_at", 1)],
                        name="idx_rtm_snapshot_created_at_ttl_30d",
                        expireAfterSeconds=_SNAPSHOT_TTL_SECONDS,
                        background=True,
                    )
                except Exception as ttl_err:
                    logger.debug("TTL index create skipped or exists: %s", ttl_err)
                coll.create_index(
                    [("session_id", 1), ("schedule_date", 1)],
                    name="idx_rtm_snapshot_session_schedule_date",
                    background=True,
                )
                coll.create_index(
                    [("session_id", 1), ("run_id", 1)],
                    name="idx_rtm_snapshot_session_run_id",
                    background=True,
                )
                coll.create_index(
                    [("source_name", 1), ("run_id", 1)],
                    name="idx_rtm_snapshot_source_run_id",
                    background=True,
                )
                coll.create_index(
                    [
                        ("schedule_date", 1),
                        ("source_name", 1),
                        ("session_id", 1),
                        ("created_at", -1),
                    ],
                    name="idx_rtm_snapshot_date_source_session_created",
                    background=True,
                )
                RTMSchedulingSnapshotRepository._indexes_ensured = True
                logger.info("RTM snapshot indexes ensured on %s", self.rtm_snapshot_collection)
            except Exception as e:
                logger.warning("RTM snapshot index ensure failed (non-fatal): %s", e)

    async def count_snapshot_documents(self, query_filter: Dict[str, Any]) -> int:
        """Count documents matching filter (uses same DB as aggregate)."""
        self.ensure_rtm_snapshot_indexes()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.repository.get_count(self.rtm_snapshot_collection, query_filter),
        )

    async def get_rtm_snapshot_rows(
        self,
        pipeline: List[Dict[str, Any]],
        max_time_ms: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Run aggregate pipeline on RTM snapshot collection."""
        self.ensure_rtm_snapshot_indexes()
        return await self.repository.aggregate_async(
            self.rtm_snapshot_collection,
            pipeline,
            max_time_ms=max_time_ms,
        )


rtm_scheduling_snapshot_repository = RTMSchedulingSnapshotRepository()
