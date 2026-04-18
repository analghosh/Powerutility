# repositories/serve_dam_scheduling_repository.py
from typing import List, Dict, Any, Optional
import logging

from .base_repository import BaseRepository

logger = logging.getLogger(__name__)


class serveDamSchedulingRepository(BaseRepository):
    """
    Thin repository for the 'serve_dam_scheduling' collection.
    Keeps the same interface style as your other repos (e.g., serveExchangeTableRepository).
    """

    def __init__(self, collection_name: str = "serve_dam_scheduling"):
        super().__init__(collection_name=collection_name)
        logger.info(f"serveDamSchedulingRepository initialized with collection: {collection_name}")

    # ---- Generic passthrough used by the service layer ----
    def aggregate(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Run an aggregation pipeline and return the list of documents.
        """
        logger.debug("serveDamSchedulingRepository.aggregate | pipeline=%s", pipeline)
        cursor = self.collection.aggregate(pipeline)
        try:
            return list(cursor)
        finally:
            cursor.close()

    # ---- Optional helpers (useful for debugging / quick checks) ----
    def find_by_date(self, schedule_date: str) -> List[Dict[str, Any]]:
        """
        Return raw rows for a given schedule_date (no shaping).
        """
        logger.debug("serveDamSchedulingRepository.find_by_date | schedule_date=%s", schedule_date)
        cursor = self.collection.find({"schedule_date": schedule_date})
        try:
            return list(cursor)
        finally:
            cursor.close()

    def get_distinct_plants(self, schedule_date: str) -> List[str]:
        """
        Distinct plant_ids available for the date.
        """
        logger.debug("serveDamSchedulingRepository.get_distinct_plants | schedule_date=%s", schedule_date)
        return self.collection.distinct("plant_id", {"schedule_date": schedule_date})