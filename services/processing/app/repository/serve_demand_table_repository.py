# repositories/dashboard_repository.py

from typing import List, Dict, Optional, Any
from .base_repository import BaseRepository
import logging

logger = logging.getLogger(__name__)


class DashboardRepository(BaseRepository):
    def __init__(self, collection_name: str = "serve_demand_table"):
        super().__init__(collection_name=collection_name)
        logger.info(f"DashboardRepository initialized with collection: {collection_name}")


    def get_current_block(self, schedule_date: str, block_id: int) -> Optional[Dict[str, Any]]:
        query = {
            "schedule_date": schedule_date,
            "time_block": block_id,
            "data_type": "demand"
        }
        return self.collection.find_one(query)


    def get_blocks_by_ids(self, schedule_date: str, block_ids: List[int], data_type="demand", dc_sg_type=None):
        query = {
            "schedule_date": schedule_date,
            "time_block": {"$in": [b for b in block_ids if b is not None]},
            "data_type": data_type
        }
        if dc_sg_type:
            query["dc_sg_type"] = dc_sg_type

        return self.collection.find(query).sort("time_block", 1)
    
    def get_acp_data_by_time_blocks(self, schedule_date: str, full_time_list: List[str]) -> List[Dict[str, Any]]:
        query = {
            "schedule_date": schedule_date,
            "cronJob_time": {"$in": full_time_list}
        }
        projection = {
            "_id": 0,
            "cronJob_time": 1,
            "acp": 1
        }
        cursor = self.collection.find(query, projection)
        try:
            return list(cursor)
        finally:
            cursor.close()


    def get_demand(self, schedule_date: str):
        pipeline = [
            {"$match": {"schedule_date": schedule_date, "type": "IDF"}},
            {"$project": {
                "_id": 0,
                "updated_at":0,
                "updated_flag":0
    
            }},
            {"$sort": {"time_block": 1}} 
        ]
        cursor = self.collection.aggregate(pipeline)
        try:
            return list(cursor)
        finally:
            cursor.close()
    
    
    def aggregate(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        try:
            cursor = self.collection.aggregate(pipeline, allowDiskUse=True)
            return list(cursor)
        except Exception:
            logger.exception("aggregate failed on %s", self.collection_name)
            return []

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
            raise

