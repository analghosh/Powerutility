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
             "type": "IDF",
           
        }
        return self.collection.find_one(query)


    def get_blocks_by_ids(self, schedule_date: str, block_ids: List[int]):
        query = {
            "schedule_date": schedule_date,
            "time_block": {"$in": [b for b in block_ids if b is not None]},
             "type": "IDF",
       
        }
        

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
