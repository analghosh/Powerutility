# repositories/serve_supply_table_repository.py

from typing import List, Dict, Optional, Any
from .base_repository import BaseRepository
import logging

logger = logging.getLogger(__name__)

class serveSupplyTableRepository(BaseRepository):
    def __init__(self, collection_name: str = "serve_supply_table"):
        super().__init__(collection_name=collection_name)
        logger.info(f"serveSupplyTableRepository initialized with collection: {collection_name}")

    # (Optional) keep your usual materialized aggregate but ensure allowDiskUse=True
    def aggregate(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        try:
            cursor = self.collection.aggregate(pipeline, allowDiskUse=True)
            return list(cursor)
        except Exception:
            logger.exception("aggregate failed on %s", self.collection_name)
            return []

    # NEW: streaming aggregate without materializing (no BaseRepository changes needed)
    def aggregate_stream(
        self,
        pipeline: List[Dict[str, Any]],
        batch_size: int = 5000,
        allow_disk_use: bool = True,
        max_time_ms: int = 0,
    ):
        """
        Return a PyMongo cursor suitable for FastAPI StreamingResponse.
        Does NOT materialize to a list. Use for NDJSON/CSV streaming.
        """
        try:
            kwargs: Dict[str, Any] = {
                "allowDiskUse": allow_disk_use,
                "batchSize": batch_size,
            }
            if max_time_ms and max_time_ms > 0:
                kwargs["maxTimeMS"] = max_time_ms
            return self.collection.aggregate(pipeline, **kwargs)
        except Exception:
            logger.exception(
                "aggregate_stream failed on %s | batch_size=%s max_time_ms=%s",
                self.collection_name, batch_size, max_time_ms
            )
            # Re-raise so your service can return a 500
            raise
