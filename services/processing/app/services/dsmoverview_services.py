"""
Dashboard service layer using repository pattern.
Handles business logic for dashboard-related data operations.
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from ..repository.dsmoverview_repository import DsmoverviewRepository
from ..repository.area_price_dam_repository import AreaPriceDamRepository
from ..repository.area_price_rtm_repository import AreaPriceRtmRepository
from ..core.helpers import dsm_calculation

logger = logging.getLogger(__name__)


class DsmoverviewService:
    """
    Service class responsible for dashboard business logic.
    """

    def __init__(self, collection_name: str = "serve_demand_table"):
        self.dsmoverview_repo = DsmoverviewRepository(collection_name=collection_name)
        self.dsmoverview_historical_repo = DsmoverviewRepository(collection_name='serve_demand_table_historical')
        self.areapricertm_repo = AreaPriceRtmRepository(collection_name='area_price_rtm')
        self.areapricedam_repo = AreaPriceDamRepository(collection_name='area_price_dam')

    


    async def dsm(self, schedule_date):
        """
        Fetch DSM (Deviation Settlement Mechanism) data for a given schedule date.

        Currently, the schedule_date is hardcoded to '8/4/2025'. This should ideally be
        passed as a parameter or derived dynamically.

        Returns:
            Optional[Dict[str, Any]]: A dictionary containing the DSM data,
                                    or None if no data is found.
        """
        

        try:
            if(schedule_date):
                schedule_date=schedule_date
            else:
                # Temporary: Hardcoded schedule date for fetching DSM records
                schedule_date= '2025-08-04'
            
            acp = 250.172
            cursor = await self.dsmoverview_repo.get_dsm(schedule_date)
           
            logger.warning(f"[DashboardService] =========++++++++.: {cursor}")
            return cursor
        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise


    async def dsmHistorical(self, from_date, to_date):
        """
        Fetch DSM (Deviation Settlement Mechanism) data for a given schedule date.

        Currently, the schedule_date is hardcoded to '8/4/2025'. This should ideally be
        passed as a parameter or derived dynamically.

        Returns:
            Optional[Dict[str, Any]]: A dictionary containing the DSM data,
                                    or None if no data is found.
        """
        

        try:
           #  Default range if not provided
            if not from_date or not to_date:
                from_date = datetime.strptime("2025-08-01", "%Y-%m-%d")
                to_date = datetime.strptime("2025-08-10", "%Y-%m-%d")
            
                
            pipeline = [
    {
        "$match": {
            "schedule_date": {
                "$gte": from_date,
                "$lte": to_date,
            },
            "type": "IDF",
        }
    },
    {
        "$group": {
            "_id": "$schedule_date",
            "positive_sum": {
                "$sum": {
                    "$cond": [
                        { "$gt": ["$net_dsm_amount", 0] },
                        "$net_dsm_amount",
                        0
                    ]
                }
            },
            "negative_sum": {
                "$sum": {
                    "$cond": [
                        { "$lt": ["$net_dsm_amount", 0] },
                        "$net_dsm_amount",
                        0
                    ]
                }
            },
            "total_dsm_rate": { "$sum": "$dsm_rate" }
        }
    },
    {
        "$project": {
            "_id": 0,
            "schedule_date": "$_id",
            "net_dsm_amount": {
                "positive_sum": { "$round": ["$positive_sum", 2] },
                "negative_sum": { "$round": ["$negative_sum", 2] },
                "total": {
                    "$round": [
                        { "$add": ["$positive_sum", "$negative_sum"] },
                        2
                    ]
                }
            },
        }
    },
    { "$sort": { "schedule_date": 1 } },

    # 👇 add a final stage to calculate overall totals
    {
        "$group": {
            "_id": None,
            "dates": { "$push": "$$ROOT" },   # keep per-date results
            "grand_total": { "$sum": "$net_dsm_amount.total" },
        }
    },
    {
        "$project": {
            "_id": 0,
            "dates": 1,   # keep all schedule_date results
            "grand_totals": {
                "total": { "$round": ["$grand_total", 2] },
            }
        }
    }
]





            cursor = self.dsmoverview_historical_repo.aggregate(pipeline)
            return await cursor.to_list(length=10000)  # ✅ return after loop finishes
        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise
    
    async def dsmHistoricalTotal(self, from_date, to_date, time_block):
        """
        Fetch DSM (Deviation Settlement Mechanism) data for a given schedule date.

        Currently, the schedule_date is hardcoded to '8/4/2025'. This should ideally be
        passed as a parameter or derived dynamically.

        Returns:
            Optional[Dict[str, Any]]: A dictionary containing the DSM data,
                                    or None if no data is found.
        """
        

        try:
           #  Default range if not provided
           
            logger.warning(f"No dashboard data found: {from_date,to_date, time_block}")        
            pipeline = [
    {
        "$match": {
            "schedule_date": {
                "$gte": from_date,
                "$lte": to_date,
            },
            "type": "IDF",
        }
    },
    # 👇 calculate odud before grouping
    {
        "$addFields": {
            "odud": { "$round": [{ "$subtract": ["$Schedule", "$comp_act_demand"] }, 2] }
        }
    },
    {
        "$group": {
            "_id": "$schedule_date",
            "positive_sum": {
                "$sum": {
                    "$cond": [
                        { "$gt": ["$net_dsm_amount", 0] },
                        "$net_dsm_amount",
                        0
                    ]
                }
            },
            "negative_sum": {
                "$sum": {
                    "$cond": [
                        { "$lt": ["$net_dsm_amount", 0] },
                        "$net_dsm_amount",
                        0
                    ]
                }
            },
            "total_dsm_rate": { "$sum": "$dsm_rate" },
            "total_Raw_Frequency": { "$sum": "$Raw_Frequency" },
            "total_odud": { "$sum": "$odud" }
        }
    },
    {
        "$project": {
            "_id": 0,
            "schedule_date": "$_id",
            "net_dsm_amount": {
                "positive_sum": { "$round": ["$positive_sum", 2] },
                "negative_sum": { "$round": ["$negative_sum", 2] },
                "total": {
                    "$round": [
                        { "$add": ["$positive_sum", "$negative_sum"] },
                        2
                    ]
                }
            },
            "total_dsm_rate": { "$round": ["$total_dsm_rate", 2] },
            "total_Raw_Frequency": { "$round": ["$total_Raw_Frequency", 2] },
            "total_odud": { "$round": ["$total_odud", 2] }
        }
    },
    { "$sort": { "schedule_date": 1 } }
]




            cursor = self.dsmoverview_historical_repo.aggregate(pipeline)
            logger.warning(f"No dashboard data found: {pipeline}")
            return await cursor.to_list(length=10000)  # ✅ return after loop finishes
        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise

# Global instance
dsmoverview_service = DsmoverviewService()
