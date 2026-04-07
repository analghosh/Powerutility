import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Union,Tuple
from collections import defaultdict
import pandas as pd
import numpy as np


from fastapi import HTTPException, Request
from ..repository.serve_table_repository import SupplyTableRepository

logger = logging.getLogger(__name__)


class SupplyServices:
    def __init__(self, collection_name: str = "supply_table_api"):
        self.supply_repo = SupplyTableRepository()
        self.demand_repo = SupplyTableRepository(collection_name="demand_table_api")
        self.serve_availability_repo = SupplyTableRepository(collection_name="serve_availability_table")
        self.price_group_repo = SupplyTableRepository(collection_name="price_group")


    def supply_by_category(self, schedule_date) -> Dict[str, Any]:

        if not schedule_date:
            schedule_date = "2025-04-02"

        pipeline = [
    {
        "$match": {
            "schedule_date": schedule_date,
            "Data_cat_type": "category"
        }
    },
    {
        "$project": {
            "_id": 0,
            "schedule_date":1,
            "timeBlock":1,
            "start_time":1,
            "end_time":1,
            "filterData":1,
            "demand": "$Demand_actual_vs_forecast",  # alias created here
            "rostering": "0"

        }
    },
    {
        "$sort": {"timeBlock": 1}  # ascending order
    }
]

        result = self.serve_availability_repo.aggregate(pipeline)
        return {"result": result}




    def supply_by_technology(self, schedule_date):
        if not schedule_date:
            schedule_date = "2025-04-02"

        pipeline = [
    {
        "$match": {
            "schedule_date": schedule_date,
            "Data_cat_type": "technology"
        }
    },
    {
        "$project": {
            "_id": 0,
            "schedule_date":1,
            "timeBlock":1,
            "start_time":1,
            "end_time":1,
            "filterData":1,
            "demand": "$Demand_actual_vs_forecast",  # alias created here
            "rostering": "0"
        }
    },
    {
        "$sort": {"timeBlock": 1}  # ascending order
    }
]

        result = self.serve_availability_repo.aggregate(pipeline)
        return {"result": result}


    def price_range(self) -> Dict[str, Any]:
       

        pipeline = [
    
    {
        "$project": {
            "_id": 0,
            "inserted_date":0
           
        }
    },
   
]

        result = self.price_group_repo.aggregate(pipeline)
        return {"result": result}
    
    def supply_by_price_group(self, schedule_date) -> Dict[str, Any]:
        if not schedule_date:
            schedule_date = "2025-04-02"

        pipeline = [
    {
        "$match": {
            "schedule_date": schedule_date,
            "Data_cat_type": "price_group"
        }
    },
    {
        "$project": {
            "_id": 0,
            "schedule_date":1,
            "timeBlock":1,
            "start_time":1,
            "end_time":1,
            "filterData":1,
            "demand": "$Demand_actual_vs_forecast",  # alias created here
            "rostering": "0"
        }
    },
    {
        "$sort": {"timeBlock": 1}  # ascending order
    }
]

        result = self.serve_availability_repo.aggregate(pipeline)
        return {"result": result}
    
    # def supply_by_plant(self):
    #     start_date_str = "2025-08-02"
    #     end_date_str = "2025-08-02"

    #     pipeline = [
    #         # 1️⃣ Filter by date
    #         {
    #             "$match": {
    #                 "schedule_date": {
    #                     "$gte": start_date_str,
    #                     "$lte": end_date_str
    #                 }
    #             }
    #         },
    #         # 2️⃣ Join with plant_list
    #         {
    #             "$lookup": {
    #                 "from": "plant_list",
    #                 "localField": "key",
    #                 "foreignField": "key",
    #                 "as": "plant_info"
    #             }
    #         },
    #         {"$unwind": "$plant_info"},

    #         # 3️⃣ Project needed fields including category & display_category
    #         {
    #             "$project": {
    #                 "schedule_date": 1,
    #                 "time_block": 1,
    #                 "key": 1,
    #                 "vc_price": 1,
    #                 "qnt_aft_loss": 1,
    #                 "plant_name": "$plant_info.plant_name",
    #                 "technology": "$plant_info.technology",
    #                 "category": "$plant_info.category",
    #                 "display_category": "$plant_info.display_category"
    #             }
    #         },

    #         # 4️⃣ Sort by VC_price for merit order
    #         {
    #             "$sort": {
    #                 "schedule_date": 1,
    #                 "time_block": 1,
    #                 "vc_price": 1
    #             }
    #         },

    #         # 5️⃣ Group by plant per time_block
    #         {
    #             "$group": {
    #                 "_id": {
    #                     "schedule_date": "$schedule_date",
    #                     "time_block": "$time_block",
    #                     "key": "$key",
    #                     "plant_name": "$plant_name",
    #                     "technology": "$technology",
    #                     "category": "$category",
    #                     "display_category": "$display_category",
    #                     "vc_price": "$vc_price"
    #                 },
    #                 "total_qnt_aft_loss": {"$sum": "$qnt_aft_loss"}
    #             }
    #         },

    #         # 6️⃣ Group by time_block
    #         {
    #             "$group": {
    #                 "_id": {
    #                     "schedule_date": "$_id.schedule_date",
    #                     "time_block": "$_id.time_block"
    #                 },
    #                 "plants": {
    #                     "$push": {
    #                         "key": "$_id.key",
    #                         "plant_name": "$_id.plant_name",
    #                         "technology": "$_id.technology",
    #                         "category": "$_id.category",
    #                         "display_category": "$_id.display_category",
    #                         "vc_price": "$_id.vc_price",
    #                         "qnt_aft_loss": "$total_qnt_aft_loss"
    #                     }
    #                 }
    #             }
    #         },

    #         # 7️⃣ Group by schedule_date
    #         {
    #             "$group": {
    #                 "_id": "$_id.schedule_date",
    #                 "time_blocks": {
    #                     "$push": {
    #                         "time_block": "$_id.time_block",
    #                         "plants": "$plants"
    #                     }
    #                 }
    #             }
    #         },

    #         # 8️⃣ Final projection
    #         {
    #             "$project": {
    #                 "_id": 0,
    #                 "schedule_date": "$_id",
    #                 "time_blocks": 1
    #             }
    #         },

    #         # 9️⃣ Sort by schedule_date
    #         {"$sort": {"schedule_date": 1}}
    #     ]

    #     supply_data = self.supply_repo.aggregate(pipeline)
    #     return {"data": supply_data}

    # # schedule_date -> time_block-> vc_price >,quant_after loss ->key -> plant name ->> category --> display category






# Global Instances
supply_pg_services = SupplyServices()
