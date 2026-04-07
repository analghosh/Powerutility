# repositories/dsmoverview_repository.py

from typing import List, Dict, Optional, Any
from .base_repository import BaseRepository
import logging

logger = logging.getLogger(__name__)


class DsmoverviewRepository(BaseRepository):
    def __init__(self, collection_name: str = "serve_demand_table"):
        super().__init__(collection_name=collection_name)
        logger.info(f"DashboardRepository initialized with collection: {collection_name}")


    def get_dsm(self, schedule_date: str):
        pipeline = [
        {"$match": {"schedule_date": schedule_date, "type":"IDF"}},
        {"$project": {
            "_id": 0,
            "Raw_Frequency": 1,
            "dsm_rate":1,
            "time_block": 1,
            "schedule_date":1,
            "net_dsm_amount":1,
            "odud": {
                "$round": [
                    {"$subtract": ["$Schedule", "$comp_act_demand"]},
                    2  # number of decimal places
                ]
            }
        }},
        {"$sort": {"time_block": 1}} 
    ]
        cursor = self.collection.aggregate(pipeline)
        try:
            return list(cursor)
        finally:
            cursor.close()
    
    def get_IDF_demand_forecast(self, schedule_date: str):
        pipeline = [
        {"$match": {"schedule_date": schedule_date, "type":"IDF"}},
        {"$project": {
            "_id": 0,
            "Actual": 1,
            "forecast": 1,
            "time_block":1,
            "schedule_date":1,
            "MAPE":1,
            "MPE":1,
            "rostering":1,
            "cost":"0",
            "comp_udm":1,
            "comp_act_demand":1

            
        }},
        {"$sort": {"time_block": 1}}  # ascending order
    ]
        cursor = self.collection.aggregate(pipeline)
        try:
            return list(cursor)
        finally:
            cursor.close()
    
    def get_DAF_demand_forecast(self, schedule_date: str):
        pipeline = [
        {"$match": {"schedule_date": schedule_date, "type":"DAF"}},
        {"$project": {
            "_id": 0,
            "forecast": 1,
            "time_block":1,
            "schedule_date":1,
            "MAPE":1,
            "MPE":1,
            "rostering":1,
            "cost":"0",
            "comp_udm":1,
            "comp_act_demand":1
        }},
        {"$sort": {"time_block": 1}}  # ascending order
    ]
        cursor = self.collection.aggregate(pipeline)
        try:
            return list(cursor)
        finally:
            cursor.close()

