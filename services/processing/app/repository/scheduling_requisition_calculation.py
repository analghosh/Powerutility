from typing import List, Dict, Optional, Any
from .base_repository import BaseRepository
import logging 
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

class SchedulingRequisitionCalculationRepository(BaseRepository):
    def __init__(self, collection_name: str = "scheduling_requisition_calculation_table"):
        super().__init__(collection_name=collection_name)
        logger.info(f"DashboardRepository initialized with collection: {collection_name}")
    
    async def getRTMAllPlantDataForScheduling(self) -> List[Dict[str, Any]]:
        scheduledate = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        logger.info(f"DashboardRepository initialized with collection: {scheduledate}")
        try:
            pipeline = [
    {
        "$match": {
            "schedule_date": { "$gte": scheduledate }
        }
    },

    {
        "$lookup": {
            "from": "plant_master",
            "let": {
                "plantKey": "$key",
            },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$eq": ["$plant_id", "$$plantKey"]
                        }
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "px_price": 1,
                        "plant_id": 1
                    }
                }
            ],
            "as": "rtm_data"
        }
    },

   { "$unwind": { "path": "$rtm_data", "preserveNullAndEmptyArrays": True } },

    {
        "$sort": {
            "rtm_data.px_price": -1,
            "price": -1,
            "key": -1
        }
    },

    { "$addFields": { "counter": 0.0 } },

    {
        "$group": {
            "_id": {
                "schedule_date_timestamp": "$schedule_date_timestamp",
                "time_block": "$time_block"
            },
            "result": {
                "$push": {
                    "plant_name": "$plant_name",
                    "key": "$key",
                    "px_price": "$rtm_data.px_price", 
                    "volume": "$volume",
                    "schedule_date": "$schedule_date",
                    "schedule_date_timestamp": "$schedule_date_timestamp",
                    "full_time": "$full_time",
                    "start_time": "$start_time",
                    "end_time": "$end_time",
                    "display_category": "$display_category",
                    "category": "$category",
                    "dispatch_type": "$dispatch_type",
                    "remaining_volume": "$remaining_volume",
                    "surplus_deficit_updated": "$surplus_deficit_updated",
                    "genrated_sg_volume": "$genrated_sg_volume",
                    "Final_plant_RMP_volume": "$Final_plant_RMP_volume",
                    "actual_plant_volume": "$actual_plant_volume",
                    "scheduling_requisition_type": "$scheduling_requisition_type",
                    "price": "$price",
                    "RMP": "$plant_RMP_value",
                    "plant_TM_value": "$plant_TM_value"
                }
            },

            "time_block": { "$first": "$time_block" },
            "full_time": { "$first": "$full_time" },
            "start_time": { "$first": "$start_time" },
            "end_time": { "$first": "$end_time" },
            "dc_sg_type": { "$first": "$dc_sg_type" },
            "schedule_date": { "$first": "$schedule_date" },
            "counter": { "$first": "$counter" },
            "schedule_date_timestamp": { "$first": "$schedule_date_timestamp" },
            "totalAvailability": { "$first": "$total_supply_availablity" },
            "demand_forecast": { "$first": "$demand_forecast" },
            "surplus_deficit": { "$first": "$surplus_deficit" },
            "rtm_forecasted_price": { "$first": "$rtm_forecasted_price" },
            "surplus_deficit_updated": { "$first": "$surplus_deficit_updated" },
            "total_availablity_updated": { "$first": "$total_availablity_updated" },
            "genrated_sg_volume": { "$first": "$genrated_sg_volume" }
        }
    },

    {
        "$project": {
            "result": 1,
            "time_block": 1,
            "full_time": 1,
            "start_time": 1,
            "end_time": 1,
            "dc_sg_type": 1,
            "schedule_date": 1,
            "schedule_date_timestamp": 1,
            "totalAvailability": 1,
            "demand_forecast": 1,
            "surplus_deficit": 1,
            "rtm_forecasted_price": 1,
            "surplus_deficit_updated": 1,
            "genrated_sg_volume": 1,
            "total_availablity_updated": 1
        }
    },

    {
        "$sort": {
            "schedule_date": 1.0,
            "time_block": 1.0
        }
    }
]


        # return pipeline
            return await self.collection.aggregate(pipeline).to_list(None)
        except Exception as e:
            logger.error("Failed serve records: %s", str(e))
            return {"error": str(e)}