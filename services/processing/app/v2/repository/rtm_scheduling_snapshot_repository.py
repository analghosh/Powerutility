"""
RTM Scheduling Snapshot Repository (V2)
Standalone repository for RTM scheduling snapshot data/excel exports.
"""

import logging
from typing import Dict, Any, List

from .base_repository import GenericRepository
from ...core.config import settings

logger = logging.getLogger(__name__)


class RTMSchedulingSnapshotRepository:
    """Repository focused only on RTM snapshot collection reads."""

    def __init__(self):
        self.repository = GenericRepository()
        self.rtm_snapshot_collection = settings.MONGODB_RTM_OPTIMIZATION_RAW_INPUTS_SNAPSHOT_COLLECTION

    async def get_rtm_snapshot_rows(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Run aggregate pipeline on RTM snapshot collection."""
        return await self.repository.aggregate_async(self.rtm_snapshot_collection, pipeline)


rtm_scheduling_snapshot_repository = RTMSchedulingSnapshotRepository()
