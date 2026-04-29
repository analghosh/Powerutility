"""
Dashboard service layer using repository pattern.
Handles business logic for dashboard-related data operations.
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from ..repository.serve_supply_table_repository import serveSupplyTableRepository
from ..repository.rtm_single_bid_data_for_approval_repository import RTMSingleBidDataForApprovalRepository
from ..repository.limit_values_repository import LimitValuesRepository
from ..repository.master_plants_repository import MasterPlantsRepository
from ..repository.rtm_product_hardcoded_data_repository import RTMProductHardCodedDataRepository
from ..repository.dashboard_repository import DashboardRepository
from ..repository.scheduling_requissition_calculation_repository import SchedulingRequisitionCalculationRepository


from ..core.helpers import dsm_calculation

logger = logging.getLogger(__name__)


class RealtimemarketService:
    """
    Service class responsible for dashboard business logic.
    """

    def __init__(self, collection_name: str = "serve_supply_table"):
        self.supply_repo = serveSupplyTableRepository()
        self.single_bid_data_for_approval_repo = RTMSingleBidDataForApprovalRepository(collection_name="rtm_single_bid_data_for_approval")
        self.rtm_product_hardCoded_data_repo = RTMProductHardCodedDataRepository(collection_name="rtm_product_hardCoded_data")
        self.plants_list_repo = MasterPlantsRepository()
        self.limit_values_repo = LimitValuesRepository()
        self.serve_demand_repo = DashboardRepository()
        self.scheduling_requisition_repo = SchedulingRequisitionCalculationRepository(collection_name="scheduling_requisition_calculation_table")


        # Assume getSessionfrom() returns a list of session time strings like ["00:30", "01:00", ...]
        session_times = RealtimemarketService.get_session_from()  

        # Get current time as string
        current_time = datetime.now().strftime("%H:%M")

        # Count the number of sessions that have passed
        passed_sessions_count = sum(
            1 for t in session_times if datetime.strptime(t, "%H:%M") < datetime.strptime(current_time, "%H:%M")
        )
        
        # Calculate today's current block
        today_current_block = (2 * passed_sessions_count) - 1 + 95
        logger.debug(f"session_times {current_time}")
        # Set class attributes (or variables)
        self.currentBlk = today_current_block

        self.shiftingBlockNo = 2
        self.shiftingBlockNoRuningTo = 5
        self.shiftingBlockNoREQFrom = 5
        self.shiftingBlockNoREQTo = 18

        self.BlkNoForRunSchedule = today_current_block + self.shiftingBlockNo
        self.BlkNoToRunSchedule = today_current_block + self.shiftingBlockNoRuningTo

        self.BlkNoFromRunReq = today_current_block + self.shiftingBlockNoREQFrom
        self.BlkNoToRunReq = today_current_block + self.shiftingBlockNoREQTo

    


    async def rtm_single_bid_graph(self, schedule_date, from_block, to_block):
        """
        Fetch DSM (Deviation Settlement Mechanism) data for a given schedule date.

        Currently, the schedule_date is hardcoded to '8/4/2025'. This should ideally be
        passed as a parameter or derived dynamically.
        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format.
            from_block (int): Starting time block.
            to_block (int): Ending time block.
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
            
            pipeline = [
                    {
                        "$match": {
                            "schedule_date": schedule_date,
                            "dc_sg": "SG",
                            "time_block": {"$gte": from_block, "$lte": to_block}
                        }
                    },
                    {
                        "$group": {
                            "_id": "$time_block",
                            "supply_total": {"$sum": "$volume"}
                        }
                    },
                    {
                        "$lookup": {
                            "from": "serve_demand_table",
                            "let": {"t_block": "$_id"},
                            "pipeline": [
                                {
                                    "$match": {
                                        "$expr": {
                                            "$and": [
                                                {"$eq": ["$time_block", "$$t_block"]},
                                                {"$eq": ["$schedule_date", schedule_date]},
                                                {"$eq": ["$type", "IDF"]}
                                            ]
                                        }
                                    }
                                },
                                {
                                    "$group": {
                                        "_id": None,
                                        "forecast": {"$sum": "$forecast"}
                                    }
                                }
                            ],
                            "as": "demand"
                        }
                    },
                    {
                        "$unwind": {
                            "path": "$demand",
                            "preserveNullAndEmptyArrays": True
                        }
                    },
                    {
                        "$project": {
                            "time_block": "$_id",
                            "supply_total": 1,
                            "forecast": "$demand.forecast",
                            "block_bid_submitted_volume": {"$literal": "0"},
                            "block_bid_approved_volume": {"$literal": "0"},
                            "single_bid_submitted_volume": {"$literal": "0"},
                            "single_bid_approved_volume": {"$literal": "0"},
                            "bid_cleared_volume": {"$literal": "0"},
                            "reserved": {"$literal": "0"},
                            "surplus_deficit": {
                                "$subtract": ["$supply_total", "$demand.forecast"]
                            }
                        }
                    },
                    {"$sort": {"time_block": 1}}
                ]
            result = await self.supply_repo.repository.aggregate(self.supply_repo.supply_serve, pipeline)
            return {"result": result}
        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise
    
    async def dam_single_bid_graph(self, schedule_date, from_block=1, to_block=96):
        """
        Fetch DSM (Deviation Settlement Mechanism) data for a given schedule date.

        Currently, the schedule_date is hardcoded to '8/4/2025'. This should ideally be
        passed as a parameter or derived dynamically.
        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format.
            from_block (int): Starting time block.
            to_block (int): Ending time block.
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
            
            pipeline = [
                    {
                        "$match": {
                            "schedule_date": schedule_date,
                            "dc_sg": "SG",
                            "time_block": {"$gte": from_block, "$lte": to_block}
                        }
                    },
                    {
                        "$group": {
                            "_id": "$time_block",
                            "supply_total": {"$sum": "$volume"}
                        }
                    },
                    {
                        "$lookup": {
                            "from": "serve_demand_table",
                            "let": {"t_block": "$_id"},
                            "pipeline": [
                                {
                                    "$match": {
                                        "$expr": {
                                            "$and": [
                                                {"$eq": ["$time_block", "$$t_block"]},
                                                {"$eq": ["$schedule_date", schedule_date]},
                                                {"$eq": ["$type", "DAF"]}
                                            ]
                                        }
                                    }
                                },
                                {
                                    "$group": {
                                        "_id": None,
                                        "forecast": {"$sum": "$forecast"}
                                    }
                                }
                            ],
                            "as": "demand"
                        }
                    },
                    {
                        "$unwind": {
                            "path": "$demand",
                            "preserveNullAndEmptyArrays": True
                        }
                    },
                    {
                        "$project": {
                            "time_block": "$_id",
                            "supply_total": 1,
                            "forecast": "$demand.forecast",
                            "block_bid_submitted_volume": {"$literal": "0"},
                            "block_bid_approved_volume": {"$literal": "0"},
                            "single_bid_submitted_volume": {"$literal": "0"},
                            "single_bid_approved_volume": {"$literal": "0"},
                            "bid_cleared_volume": {"$literal": "0"},
                            "reserved": {"$literal": "0"},
                            "surplus_deficit": {
                                "$subtract": ["$supply_total", "$demand.forecast"]
                            }
                        }
                    },
                    {"$sort": {"time_block": 1}}
                ]
            result = await self.supply_repo.repository.aggregate(self.supply_repo.supply_serve, pipeline)
            return {"result": result}
        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise



    async def rtm_single_bid_table(self, schedule_date, from_block, to_block):
        """
        Fetch DSM (Deviation Settlement Mechanism) data for a given schedule date.

        Currently, the schedule_date is hardcoded to '8/4/2025'. This should ideally be
        passed as a parameter or derived dynamically.
        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format.
            from_block (int): Starting time block.
            to_block (int): Ending time block.
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
            
            pipeline = [
    {
        "$match": { 
            "schedule_date": schedule_date, 
            "dc_sg": "SG",
            "time_block": {"$gte": from_block, "$lte": to_block} 
        }
    },
    {
        "$group": {
            "_id": { "plant": "$plant", "plant_name": "$plant_name" , "plant_id":"$plant_id" },
            "PX_price": { "$first": "$PX_price" },
            "c_s": { "$first": "$c_s" },
            "TM": { "$first": "$TM" },
            "multiplier": { "$first": "$multiplier" },
            "schedule_date": { "$first": "$schedule_date" },
            "blocks": {
                "$push": {
                    "time_block": "$time_block",
                    "volume": "$volume"
                }
            }
        }
    },
    { "$unwind": "$blocks" },

    # --- RTM product data ---
    {
        "$lookup": {
            "from": "rtm_product_hardCoded_data",
            "let": { "tb": "$blocks.time_block" },
            "pipeline": [
                { "$match": { "$expr": { "$eq": ["$From_Period_Code", "$$tb"] } } },
                { "$project": { "_id": 0, "Session_Id": 1 } }
            ],
            "as": "rtm_data"
        }
    },
    { "$unwind": { "path": "$rtm_data", "preserveNullAndEmptyArrays": True } },

    # --- Forecasted price (PV) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "RTM_forecasted_price"] },
                                { "$eq": ["$area", "PV"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "forecasted_price": "$values" } }
            ],
            "as": "forecast_data"
        }
    },
    { "$unwind": { "path": "$forecast_data", "preserveNullAndEmptyArrays": True } },

    # --- Buffer (MP, Limits) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "Buffer"] },
                                { "$eq": ["$area", "MP"] },
                                { "$eq": ["$category", "Limits"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "buffer_limit": "$values" } }
            ],
            "as": "buffer_data"
        }
    },
    { "$unwind": { "path": "$buffer_data", "preserveNullAndEmptyArrays": True } },

    # # --- Ceiling_Price (MP, Limits) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "Ceiling_Price"] },
                                { "$eq": ["$area", "MP"] },
                                { "$eq": ["$category", "Limits"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "ceiling_price": "$values" } }
            ],
            "as": "ceiling_data"
        }
    },
    { "$unwind": { "path": "$ceiling_data", "preserveNullAndEmptyArrays": True } },

    # # --- n_1 (MP, Limits) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "n_1"] },
                                { "$eq": ["$area", "MP"] },
                                { "$eq": ["$category", "Limits"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "n1_limit": "$values" } }
            ],
            "as": "n1_data"
        }
    },
    { "$unwind": { "path": "$n1_data", "preserveNullAndEmptyArrays": True } },

    # # --- noc_buy (MP, Limits) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "noc_buy"] },
                                { "$eq": ["$area", "MP"] },
                                { "$eq": ["$category", "Limits"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "noc_buy_limit": "$values" } }
            ],
            "as": "noc_buy_data"
        }
    },
    { "$unwind": { "path": "$noc_buy_data", "preserveNullAndEmptyArrays": True } },

    # # --- noc_sell (MP, Limits) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "noc_sell"] },
                                { "$eq": ["$area", "MP"] },
                                { "$eq": ["$category", "Limits"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "noc_sell_limit": "$values" } }
            ],
            "as": "noc_sell_data"
        }
    },
    { "$unwind": { "path": "$noc_sell_data", "preserveNullAndEmptyArrays": True } },

    # # --- iex (S, Ex_Split) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "iex"] },
                                { "$eq": ["$area", "S"] },
                                { "$eq": ["$category", "Ex_Split"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "iex_split": "$value" } }
            ],
            "as": "iex_data"
        }
    },
    { "$unwind": { "path": "$iex_data", "preserveNullAndEmptyArrays": True } },

    # # --- pxil (S, Ex_Split) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "pxil"] },
                                { "$eq": ["$area", "S"] },
                                { "$eq": ["$category", "Ex_Split"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "pxil_split": "$value" } }
            ],
            "as": "pxil_data"
        }
    },
    { "$unwind": { "path": "$pxil_data", "preserveNullAndEmptyArrays": True } },

    # --- Group back by plant ---
    {
        "$group": {
            "_id": {
                "plant": "$_id.plant",
                "plant_name": "$_id.plant_name",
                "plant_id": "$_id.plant_id",
                "PX_price": "$PX_price",
                "c_s": "$c_s",
                "TM": "$TM",
                "multiplier": "$multiplier"
            },
            "blocks": {
                "$push": {
                    "time_block": "$blocks.time_block",
                    "volume": "$blocks.volume",
                    "Session_Id": "$rtm_data.Session_Id",
                    "forecasted_price": "$forecast_data.forecasted_price",
                    "buffer_limit": "$buffer_data.buffer_limit",
                    "ceiling_price": "$ceiling_data.ceiling_price",
                    "n1_limit": "$n1_data.n1_limit",
                    "noc_buy_limit": "$noc_buy_data.noc_buy_limit",
                    "noc_sell_limit": "$noc_sell_data.noc_sell_limit",
                    "iex_split": "$iex_data.iex_split",
                    "pxil_split": "$pxil_data.pxil_split"
                }
            }
        }
    },
    {
        "$project": {
            "_id": 0,
            "plant": "$_id.plant",
            "plant_name": "$_id.plant_name",
            "plant_id": "$_id.plant_id",
            "PX_price": "$_id.PX_price",
            "c_s": "$_id.c_s",
            "TM": "$_id.TM",
            "multiplier": "$_id.multiplier",
            "blocks": 1
        }
    }
]
            result = await self.supply_repo.repository.aggregate(self.supply_repo.supply_serve, pipeline)
            return {"result": result}
        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise

    async def dam_single_bid_table(self, schedule_date, from_block=1, to_block=96):
        """
        Fetch DSM (Deviation Settlement Mechanism) data for a given schedule date.

        Currently, the schedule_date is hardcoded to '8/4/2025'. This should ideally be
        passed as a parameter or derived dynamically.
        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format.
            from_block (int): Starting time block.
            to_block (int): Ending time block.
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
            
            pipeline = [
    {
        "$match": { 
            "schedule_date": schedule_date, 
            "dc_sg": "SG",
            "time_block": {"$gte": from_block, "$lte": to_block} 
        }
    },
    {
        "$group": {
            "_id": { "plant": "$plant", "plant_name": "$plant_name" , "plant_id":"$plant_id" },
            "PX_price": { "$first": "$PX_price" },
            "c_s": { "$first": "$c_s" },
            "TM": { "$first": "$TM" },
            "multiplier": { "$first": "$multiplier" },
            "schedule_date": { "$first": "$schedule_date" },
            "blocks": {
                "$push": {
                    "time_block": "$time_block",
                    "volume": "$volume"
                }
            }
        }
    },
    { "$unwind": "$blocks" },

    # --- RTM product data ---
    {
        "$lookup": {
            "from": "rtm_product_hardCoded_data",
            "let": { "tb": "$blocks.time_block" },
            "pipeline": [
                { "$match": { "$expr": { "$eq": ["$From_Period_Code", "$$tb"] } } },
                { "$project": { "_id": 0, "Session_Id": 1 } }
            ],
            "as": "rtm_data"
        }
    },
    { "$unwind": { "path": "$rtm_data", "preserveNullAndEmptyArrays": True } },

    # --- Forecasted price (PV) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "RTM_forecasted_price"] },
                                { "$eq": ["$area", "PV"] },
                                { "$eq": ["$market", "DAM"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "forecasted_price": "$values" } }
            ],
            "as": "forecast_data"
        }
    },
    { "$unwind": { "path": "$forecast_data", "preserveNullAndEmptyArrays": True } },

    # --- Buffer (MP, Limits) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "Buffer"] },
                                { "$eq": ["$area", "MP"] },
                                { "$eq": ["$market", "DAM"] },
                                { "$eq": ["$category", "Limits"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "buffer_limit": "$values" } }
            ],
            "as": "buffer_data"
        }
    },
    { "$unwind": { "path": "$buffer_data", "preserveNullAndEmptyArrays": True } },

    # # --- Ceiling_Price (MP, Limits) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "Ceiling_Price"] },
                                { "$eq": ["$area", "MP"] },
                                { "$eq": ["$category", "Limits"] },
                                { "$eq": ["$market", "DAM"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "ceiling_price": "$values" } }
            ],
            "as": "ceiling_data"
        }
    },
    { "$unwind": { "path": "$ceiling_data", "preserveNullAndEmptyArrays": True } },

    # # --- n_1 (MP, Limits) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "n_1"] },
                                { "$eq": ["$area", "MP"] },
                                { "$eq": ["$category", "Limits"] },
                                { "$eq": ["$market", "DAM"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "n1_limit": "$values" } }
            ],
            "as": "n1_data"
        }
    },
    { "$unwind": { "path": "$n1_data", "preserveNullAndEmptyArrays": True } },

    # # --- noc_buy (MP, Limits) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "noc_buy"] },
                                { "$eq": ["$area", "MP"] },
                                { "$eq": ["$category", "Limits"] },
                                { "$eq": ["$market", "DAM"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "noc_buy_limit": "$values" } }
            ],
            "as": "noc_buy_data"
        }
    },
    { "$unwind": { "path": "$noc_buy_data", "preserveNullAndEmptyArrays": True } },

    # # --- noc_sell (MP, Limits) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "noc_sell"] },
                                { "$eq": ["$area", "MP"] },
                                { "$eq": ["$category", "Limits"] },
                                { "$eq": ["$market", "DAM"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "noc_sell_limit": "$values" } }
            ],
            "as": "noc_sell_data"
        }
    },
    { "$unwind": { "path": "$noc_sell_data", "preserveNullAndEmptyArrays": True } },

    # # --- iex (S, Ex_Split) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "iex"] },
                                { "$eq": ["$area", "S"] },
                                { "$eq": ["$category", "Ex_Split"] },
                                { "$eq": ["$market", "DAM"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "iex_split": "$value" } }
            ],
            "as": "iex_data"
        }
    },
    { "$unwind": { "path": "$iex_data", "preserveNullAndEmptyArrays": True } },

    # # --- pxil (S, Ex_Split) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "pxil"] },
                                { "$eq": ["$area", "S"] },
                                { "$eq": ["$category", "Ex_Split"] },
                                { "$eq": ["$market", "DAM"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "pxil_split": "$value" } }
            ],
            "as": "pxil_data"
        }
    },
    { "$unwind": { "path": "$pxil_data", "preserveNullAndEmptyArrays": True } },

    # --- Group back by plant ---
    {
        "$group": {
            "_id": {
                "plant": "$_id.plant",
                "plant_name": "$_id.plant_name",
                "plant_id": "$_id.plant_id",
                "PX_price": "$PX_price",
                "c_s": "$c_s",
                "TM": "$TM",
                "multiplier": "$multiplier"
            },
            "blocks": {
                "$push": {
                    "time_block": "$blocks.time_block",
                    "volume": "$blocks.volume",
                    "Session_Id": "$rtm_data.Session_Id",
                    "forecasted_price": "$forecast_data.forecasted_price",
                    "buffer_limit": "$buffer_data.buffer_limit",
                    "ceiling_price": "$ceiling_data.ceiling_price",
                    "n1_limit": "$n1_data.n1_limit",
                    "noc_buy_limit": "$noc_buy_data.noc_buy_limit",
                    "noc_sell_limit": "$noc_sell_data.noc_sell_limit",
                    "iex_split": "$iex_data.iex_split",
                    "pxil_split": "$pxil_data.pxil_split"
                }
            }
        }
    },
    {
        "$project": {
            "_id": 0,
            "plant": "$_id.plant",
            "plant_name": "$_id.plant_name",
            "plant_id": "$_id.plant_id",
            "PX_price": "$_id.PX_price",
            "c_s": "$_id.c_s",
            "TM": "$_id.TM",
            "multiplier": "$_id.multiplier",
            "blocks": 1
        }
    }
]
            result = await self.supply_repo.repository.aggregate(self.supply_repo.supply_serve, pipeline)
            return {"result": result}
        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise
    

    async def rtm_scheduling_table(self, schedule_date):
        """
        Fetch DSM (Deviation Settlement Mechanism) data for a given schedule date.

        Currently, the schedule_date is hardcoded to '8/4/2025'. This should ideally be
        passed as a parameter or derived dynamically.
        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format.
            from_block (int): Starting time block.
            to_block (int): Ending time block.
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
            
            pipeline = [
    {
        "$match": { 
            "schedule_date": schedule_date, 
            "dc_sg": "SG",
        }
    },
    {
        "$group": {
            "_id": { "plant": "$plant", "plant_name": "$plant_name" , "plant_id":"$plant_id" },
            "PX_price": { "$first": "$PX_price" },
            "c_s": { "$first": "$c_s" },
            "TM": { "$first": "$TM" },
            "multiplier": { "$first": "$multiplier" },
            "schedule_date": { "$first": "$schedule_date" },
            "blocks": {
                "$push": {
                    "time_block": "$time_block",
                    "volume": "$volume"
                }
            }
        }
    },
    { "$unwind": "$blocks" },

    # --- RTM product data ---
    {
        "$lookup": {
            "from": "rtm_product_hardCoded_data",
            "let": { "tb": "$blocks.time_block" },
            "pipeline": [
                { "$match": { "$expr": { "$eq": ["$From_Period_Code", "$$tb"] } } },
                { "$project": { "_id": 0, "Session_Id": 1 } }
            ],
            "as": "rtm_data"
        }
    },
    { "$unwind": { "path": "$rtm_data", "preserveNullAndEmptyArrays": True } },

    # --- Forecasted price (PV) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "RTM_forecasted_price"] },
                                { "$eq": ["$area", "PV"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "forecasted_price": "$values" } }
            ],
            "as": "forecast_data"
        }
    },
    { "$unwind": { "path": "$forecast_data", "preserveNullAndEmptyArrays": True } },

    # --- Buffer (MP, Limits) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "Buffer"] },
                                { "$eq": ["$area", "MP"] },
                                { "$eq": ["$category", "Limits"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "buffer_limit": "$values" } }
            ],
            "as": "buffer_data"
        }
    },
    { "$unwind": { "path": "$buffer_data", "preserveNullAndEmptyArrays": True } },

    # # --- Ceiling_Price (MP, Limits) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "Ceiling_Price"] },
                                { "$eq": ["$area", "MP"] },
                                { "$eq": ["$category", "Limits"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "ceiling_price": "$values" } }
            ],
            "as": "ceiling_data"
        }
    },
    { "$unwind": { "path": "$ceiling_data", "preserveNullAndEmptyArrays": True } },

    # # --- n_1 (MP, Limits) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "n_1"] },
                                { "$eq": ["$area", "MP"] },
                                { "$eq": ["$category", "Limits"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "n1_limit": "$values" } }
            ],
            "as": "n1_data"
        }
    },
    { "$unwind": { "path": "$n1_data", "preserveNullAndEmptyArrays": True } },

    # # --- noc_buy (MP, Limits) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "noc_buy"] },
                                { "$eq": ["$area", "MP"] },
                                { "$eq": ["$category", "Limits"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "noc_buy_limit": "$values" } }
            ],
            "as": "noc_buy_data"
        }
    },
    { "$unwind": { "path": "$noc_buy_data", "preserveNullAndEmptyArrays": True } },

    # # --- noc_sell (MP, Limits) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "noc_sell"] },
                                { "$eq": ["$area", "MP"] },
                                { "$eq": ["$category", "Limits"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "noc_sell_limit": "$values" } }
            ],
            "as": "noc_sell_data"
        }
    },
    { "$unwind": { "path": "$noc_sell_data", "preserveNullAndEmptyArrays": True } },

    # # --- iex (S, Ex_Split) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "iex"] },
                                { "$eq": ["$area", "S"] },
                                { "$eq": ["$category", "Ex_Split"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "iex_split": "$value" } }
            ],
            "as": "iex_data"
        }
    },
    { "$unwind": { "path": "$iex_data", "preserveNullAndEmptyArrays": True } },

    # # --- pxil (S, Ex_Split) ---
    {
        "$lookup": {
            "from": "limit_values",
            "let": { "sched": "$schedule_date", "tb": "$blocks.time_block" },
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                { "$eq": ["$ID", "pxil"] },
                                { "$eq": ["$area", "S"] },
                                { "$eq": ["$category", "Ex_Split"] },
                                { "$eq": ["$schedule_date", "$$sched"] },
                                { "$eq": ["$block", "$$tb"] }
                            ]
                        }
                    }
                },
                { "$project": { "_id": 0, "pxil_split": "$value" } }
            ],
            "as": "pxil_data"
        }
    },
    { "$unwind": { "path": "$pxil_data", "preserveNullAndEmptyArrays": True } },

    # --- Group back by plant ---
    {
        "$group": {
            "_id": {
                "plant": "$_id.plant",
                "plant_name": "$_id.plant_name",
                "plant_id": "$_id.plant_id",
                "PX_price": "$PX_price",
                "c_s": "$c_s",
                "TM": "$TM",
                "multiplier": "$multiplier"
            },
            "blocks": {
                "$push": {
                    "time_block": "$blocks.time_block",
                    "volume": "$blocks.volume",
                    "Session_Id": "$rtm_data.Session_Id",
                    "forecasted_price": "$forecast_data.forecasted_price",
                    "buffer_limit": "$buffer_data.buffer_limit",
                    "ceiling_price": "$ceiling_data.ceiling_price",
                    "n1_limit": "$n1_data.n1_limit",
                    "noc_buy_limit": "$noc_buy_data.noc_buy_limit",
                    "noc_sell_limit": "$noc_sell_data.noc_sell_limit",
                    "iex_split": "$iex_data.iex_split",
                    "pxil_split": "$pxil_data.pxil_split"
                }
            }
        }
    },
    {
        "$project": {
            "_id": 0,
            "plant": "$_id.plant",
            "plant_name": "$_id.plant_name",
            "plant_id": "$_id.plant_id",
            "PX_price": "$_id.PX_price",
            "c_s": "$_id.c_s",
            "TM": "$_id.TM",
            "multiplier": "$_id.multiplier",
            "blocks": 1
        }
    }
]
            result = await self.supply_repo.repository.aggregate(self.supply_repo.supply_serve, pipeline)
            return {"result": result}
        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise

    async def submit_for_approval_post_data(self, data: dict):
        # return {"status": "success", "message": "Successfully Inserted", "data":data[0]['bid_date']}
        try:
            dbarray = {}

            # iterate over time block ranges
            for keys , data_value in enumerate(data):
                
                dbarray[keys] = {
                    "bid_date": datetime.strptime(data_value['bid_date'], "%Y-%m-%d").strftime("%Y-%m-%d"),
                    "reference_number": str(int(datetime.now().timestamp())),
                    "inserted_date": datetime.today().strftime("%Y-%m-%d"),
                    "inserted_time": datetime.today().strftime("%H:%M"),
                    "year": datetime.today().strftime("%Y"),
                    "month": datetime.today().strftime("%m"),
                    "day": datetime.today().strftime("%d"),
                    "time_block": data_value['time_block'],
                    "start_time": data_value["start_time"],
                    "end_time": data_value["end_time"],
                    "block_no": int(data_value["block_no"]),
                    "revision_no": int(data_value["revision_no"]) + 1,
                    "bid_type": "single",
                    "session_block": int(data_value["session_block"]),
                    "member_id": "N2UP0UPC0000",
                    "user_id": "UPC01",
                    "portfolio_code": "N2UP0UPC0001",
                    "sheet_market": "INDIA-RTM",
                    "requested_by": "deepak",
                    "requested_time": datetime.today().strftime("%H:%M"),
                    "approved_by": "",
                    "approved_time": "",
                    "created_by": "",
                    "created_time": "",
                    "Ex_split_string": data_value["Ex_split_string"],
                    "remark": "",
                    "buy_sell_bid": data_value["buy_sell_bid"],
                }

                # handle bid_status + notifications
                if data_value["buy_sell_bid"] == "withdrawBid":
                    dbarray[keys]["bid_status"] = "pending_withdrawal"
                    notificationData = {
                        "bid_status": "submitForWithdrawal",
                        "module_name": "Single Bid",
                        "title": "RTM Single Bid Submitted for Withdrawal by deepak",
                        "description": "RTM single bid submitted for Withdrawal of the selected time blocks",
                        "time_block": data_value["time_block"],
                        "submitted_time": datetime.today().strftime("%d-%m-%Y %H:%M"),
                        "action": "#",
                        "notification_type": "bid_for_Withdrawal"
                    }
                else:
                    dbarray[keys]["bid_status"] = "pending"
                    notificationData = {
                        "bid_status": "submitForApproval",
                        "module_name": "Single Bid",
                        "title": "RTM Single Bid Submitted for Approval Requested by deepak",
                        "description": "RTM single bid submitted for approval of the selected time blocks",
                        "time_block": data_value["time_block"],
                        "submitted_time": datetime.today().strftime("%d-%m-%Y %H:%M"),
                        "action": "#",
                        "notification_type": "bid_for_Approvel"
                    }

                # handle Plants logic
                dbarray[keys]["Plants"] = {}
                if data_value["buy_sell_bid_type"] != "buy":
                    for plkey, plvalue in enumerate(data_value["bid_plant_name"]):
                        dbarray[keys]["Plants"][plvalue["bid_plant_key"]] = {
                            "bid_plant_name": plvalue["bid_plant_name"],
                            "bid_plant_category": plvalue["bid_plant_category"],
                            "bid_default_volume": plvalue["bid_default_volume"],
                            "bid_volume": plvalue["bid_volume"],
                            "bid_price": plvalue["bid_price"],
                            "bid_default_price": plvalue["bid_default_price"]
                            if plvalue["buy_sell_bid_type"] == "buy"
                            else plvalue["bid_default_price"],
                            "rtm_forecast_Price": plvalue["rtm_forecast_Price"],
                        }
                
            
            # save data into plant approval table
            # response = createPlantDataForApproval(dbarray)
            if not dbarray or len(dbarray) == 0:
                raise ValueError("No documents to insert")
            
             # Convert each item to dict
            documents_to_insert = list(dbarray.values())  # now it's a list of dicts

            # If elements might be Pydantic models:
            documents_to_insert = [
                item.dict() if hasattr(item, "dict") else item
                for item in dbarray.values()
            ]

            # Insert into MongoDB
            result = await self.single_bid_data_for_approval_repo.insert_many(documents_to_insert)
            if result:  
                return {"status": "success", "message": "Successfully Inserted"}
            else:
                return {"status": "error", "message": "Error: data not found!"}

        except Exception as e:
            return {"status": "error", "message": f"Internal Server Error: {str(e)}"}



    async def calSchedulingTableData(self, schedule_date): 
        try:
            getsessionNo = await self.rtm_product_hardCoded_data_repo.get_Data()
            
            # Fetch all documents from plants_list
            getPlantList = await self.plants_list_repo.get_Data()
            
            mustplantPrice = "20"

            yesterday = "2025-09-26"
            tomorrow  = "2025-09-28"


            
            allTMValues = await self.limit_values_repo.get_data( yesterday, tomorrow, "RTM", "TM", "NA")
            allSEZValues = await self.limit_values_repo.get_data( yesterday, tomorrow, "DAM", "SEZ", "OB")
            allMLTPValues = await self.limit_values_repo.get_data( yesterday, tomorrow, "RTM", "Multiplier", "OB")
            
            
            getplantsData = await self.get_all_plant_data_for_scheduling(schedule_date, "SG")
            # return getplantsData
            getplantsDataDC = await self.get_all_plant_data_for_scheduling(schedule_date, "DC")
            
            # countblockSGData = self.get_last_record_of_sg(getplantsData,0)
            
            
            
            # Fetch data from MongoDB
            get_forecasted_value =  self.serve_demand_repo.get_demand(schedule_date)
            
            totalAvailability =   self.total_availability(getplantsData,get_forecasted_value)
            
            rtm_forecasted_price = await self.limit_values_repo.get_data_byID(schedule_date,"RTM_forecasted_price","RTM_Bid","RTM","PV" )
            
            blk_no_for_run_schedule = 1
            blk_no_to_run_schedule = 96 
            
            
            data = self.SchedulingCalculator(
            total_availability=totalAvailability,
            rtm_forecasted_price=rtm_forecasted_price,
            get_plants_data=getplantsData,
            get_plants_data_dc=getplantsDataDC,
            all_tm_values=allTMValues,
            all_sez_values=allSEZValues,
            all_mltp_values=allMLTPValues,
            get_plant_list=getPlantList,
            get_session_no=getsessionNo)
            return data
        except Exception as e:
            return {"status": "error", "message": f"Internal Server Error: {str(e)}"}
    
    def SchedulingCalculator(self,
            total_availability,
            rtm_forecasted_price,
            get_plants_data,
            get_plants_data_dc,
            all_tm_values,
            all_sez_values,
            all_mltp_values,
            get_plant_list,
            get_session_no):
        data = []
        total_sell_volume = 0
            
        blk22 = 0 
        graphblkcount = 0
        reviseDataArray = {} 
        reviseDataTotalsupply = []
        
        for blk, TotalblkwiseData in enumerate(total_availability):
            # print(TotalblkwiseData)
            if blk in rtm_forecasted_price and "values" in rtm_forecasted_price[blk]:
                try:
                    val = float(rtm_forecasted_price[blk]["values"])
                    rtmforecastedpriceblk = val if not (val != val) else 0.0  # check NaN
                except (ValueError, TypeError):
                    rtmforecastedpriceblk = 0.0
            else:
                rtmforecastedpriceblk = 0.0

            # genratedTotalsupply
            genratedTotalsupply = 0.0
            
            # total_diff
            if blk in reviseDataTotalsupply and "surplus_deficit_updated" in reviseDataTotalsupply[blk]:
                total_diff = reviseDataTotalsupply[blk]["surplus_deficit_updated"]
            else:
                total_diff = TotalblkwiseData.get("surplus_deficit", 0.0)
            
            logger.debug(f"key={blk} atart-{self.BlkNoForRunSchedule} BlkNoToRunSchedule-{self.BlkNoToRunSchedule} BlkNoFromRunReq- {self.BlkNoFromRunReq} BlkNoToRunReq- {self.BlkNoToRunReq}")

            if self.BlkNoForRunSchedule <= blk <= self.BlkNoToRunSchedule:
                
                total_diff = (
                        reviseDataTotalsupply.get(blk, {}).get("surplus_deficit_updated")
                        if reviseDataTotalsupply.get(blk, {}).get("surplus_deficit_updated") is not None
                        else TotalblkwiseData.get("surplus_deficit", 0)
                    )

                surplus_deficit_updatedfornextBlock = (
                        reviseDataTotalsupply.get(blk, {}).get("surplus_deficit_updated")
                        if reviseDataTotalsupply.get(blk, {}).get("surplus_deficit_updated") is not None
                        else TotalblkwiseData.get("surplus_deficit", 0)
                    )
                
                TotalSupplyfornextBlock = float(
                        reviseDataTotalsupply.get(blk, {}).get("total_supply_updated")
                        if reviseDataTotalsupply.get(blk, {}).get("total_supply_updated") is not None
                        else TotalblkwiseData.get("totalAvailability", 0)
                    )
                
                availability_data_array_b_wise = dict(get_plants_data[blk])
                tptal = 0
                plnt_100_volume = 0
                plkey = 0
                Remaning_value = 0
                genratedTotalsupply = 0
                if total_diff > 0:
                    for plnamekey, availability_plnt_arrayyyObj in enumerate(get_plants_data[blk]["result"]):
                        availability_plnt_arrayyy = dict(availability_plnt_arrayyyObj)  # cast to dict
                        px_price = round(availability_plnt_arrayyy.get("PX_Price", 0))
                        planttm_key = (
                            f"{availability_plnt_arrayyy['schedule_date']}_"
                            f"{availability_plnt_arrayyy['key']}_"
                            f"{availability_data_array_b_wise['time_block']}"
                        )

                        planttmvalue = all_tm_values.get(planttm_key, 0)
                        plantSEZ_key = (
                            f"{availability_plnt_arrayyy['schedule_date']}_"
                            f"{availability_plnt_arrayyy['key']}_"
                            f"{availability_data_array_b_wise['time_block']}"
                        )

                        plantSEZvalue = all_sez_values.get(plantSEZ_key, 0)

                        plantMLTP_key = (
                            f"{availability_plnt_arrayyy['schedule_date']}_"
                            f"{availability_plnt_arrayyy['key']}"
                        )

                        plantMLTPvalue = all_mltp_values.get(plantMLTP_key, 1)

                        SGofLastplant = (
                            reviseDataArray.get(blk, {})
                            .get("genrated_sg_volume", {})
                            .get(availability_plnt_arrayyy["key"], 0)
                        )
                        plnt_100_volume = float(availability_plnt_arrayyy.get("volume", 0))
                        # Assign SG value
                        vlueofChangesandactual = SGofLastplant
                        plnt_100_volume_genrated = float(vlueofChangesandactual)

                        # Reverse the DC plants list
                        reverseplantDC = list(reversed(get_plants_data[blk]["result"]))

                        # Get volume for the current plant in DC (plnamekey)
                        plnt_100_volumeDC = float(reverseplantDC[plnamekey]["volume"]) if plnamekey < len(reverseplantDC) else 0

                        if get_plant_list.get(availability_plnt_arrayyy["key"], {}).get("scheduling_req_category") == "InSGS":
                            plantcontblockMinValue = 0
                            SR_ForecastPriceCheck = self.check_forecast_price_apply_for_surplus(
                                rtmforecastedpriceblk,
                                px_price,
                                "surplus"
                             )
                            if plnt_100_volume_genrated > 0 and (plnt_100_volume_genrated - plnt_100_volume_genrated) <= 0:
                                plnt_min_run_vol = float(planttmvalue)

                                plnt_RMP_run_vol = max(
                                    plnt_100_volume_genrated - availability_plnt_arrayyy.get("RMP", 0),
                                    plnt_min_run_vol
                                )

                                total_minus_RPM_vol = max(
                                    plnt_100_volume_genrated - plnt_RMP_run_vol,
                                    0
                                )
                                sell_volume = 0
                                if total_diff > 0:
                                    if total_diff - total_minus_RPM_vol < 0:
                                        sell_volume = min(total_diff, total_minus_RPM_vol)
                                        total_minus_RPM_vol = sell_volume

                                        Remaning_value = 0
                                        total_diff = 0
                                        
                                    else:
                                        sell_volume = total_diff - total_minus_RPM_vol
                                        Remaning_value = sell_volume
                                        total_diff = sell_volume

                                tptal = tptal + plnt_100_volume
                                if round(sell_volume) >= 0.01:
                                    reverse_array_next = list(reversed(get_plants_data[blk + 1]['result']))
                                    nextblkSG = reverse_array_next[plnamekey]['volume'] if plnamekey < len(reverse_array_next) else 0

                                    reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[
                                        availability_plnt_arrayyy["key"]
                                    ] = min(plnt_100_volume_genrated - total_minus_RPM_vol, nextblkSG)

                                    genratedTotalsupply += min(plnt_100_volume_genrated - total_minus_RPM_vol, nextblkSG)

                                    insertData = {
                                        "schedule_date": availability_data_array_b_wise["schedule_date"],
                                        "schedule_date_timestamp": int(datetime.strptime(availability_data_array_b_wise["schedule_date"], "%Y-%m-%d").timestamp()),
                                        "schedule_date_Int": int(datetime.strptime(availability_data_array_b_wise["schedule_date"], "%Y-%m-%d").strftime("%Y%m%d")),
                                        "start_time": availability_data_array_b_wise["start_time"],
                                        "end_time": availability_data_array_b_wise["end_time"],
                                        "full_time": availability_data_array_b_wise["full_time"],
                                        "time_block": availability_data_array_b_wise["time_block"],
                                        "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                        "session_number": get_session_no[availability_data_array_b_wise["time_block"]]["Session_Id"],
                                        "demand_forecast": round(TotalblkwiseData["forecast_value"]),
                                        "total_supply_availablity": round(TotalblkwiseData["totalAvailability"]),
                                        "surplus_deficit": round(TotalblkwiseData["surplus_deficit"]),
                                        "surplus_deficit_updated": surplus_deficit_updatedfornextBlock,
                                        "total_availablity_updated": TotalSupplyfornextBlock,
                                        "price": px_price,
                                        "rtm_forecasted_price": float(rtmforecastedpriceblk),
                                        "remaining_volume": float(total_diff),
                                        "plant_RMP_value": float(availability_plnt_arrayyy.get("RMP", 0)),
                                        "plant_TM_value": float(planttmvalue),
                                        "plant_SEZ_value": float(plantSEZvalue),
                                        "plant_MLTP_value": float(plantMLTPvalue),
                                        "up_down_RMP_volume": float(total_minus_RPM_vol),
                                        "Final_plant_RMP_volume": round(plnt_100_volume_genrated - total_minus_RPM_vol, 3),
                                        "genrated_sg_volume": round(plnt_100_volume_genrated, 3),
                                        "actual_plant_volume": round(plnt_100_volume, 3),
                                        "actual_plant_volume_DC": round(plnt_100_volumeDC, 3),
                                        "plant_min_rum_volume": round(plnt_min_run_vol, 3),
                                        "category": availability_plnt_arrayyy["category"],
                                        "plant_name": availability_plnt_arrayyy["plant_name"],
                                        "key": availability_plnt_arrayyy["key"],
                                        "revision_no_SG": availability_plnt_arrayyy.get("revision_no", 0),
                                        "revision_no_DC": reverseplantDC[plnamekey].get("revision_no", 0) if plnamekey < len(reverseplantDC) else 0,
                                        "display_category": get_plant_list[availability_plnt_arrayyy["key"]]["scheduling_req_category"],
                                        "dispatch_type": availability_plnt_arrayyy.get("dispatch_type", ""),
                                        "total_RMP_volume": float(total_diff),
                                        "scheduling_requisition_type": "Scheduling"
                                    }
                                    print(insertData)

                                    plkey += 1

                                else:
                                    reverse_array_next = list(reversed(get_plants_data[blk + 1]['result']))
                                    nextblkSG = reverse_array_next[plnamekey]['volume'] if plnamekey < len(reverse_array_next) else 0

                                    reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[
                                        availability_plnt_arrayyy["key"]
                                    ] = min(plnt_100_volume_genrated, nextblkSG)

                                    genratedTotalsupply += min(plnt_100_volume_genrated, nextblkSG)

                                    insertData = {
                                        "schedule_date": availability_data_array_b_wise["schedule_date"],
                                        "schedule_date_timestamp": int(datetime.strptime(availability_data_array_b_wise["schedule_date"], "%Y-%m-%d").timestamp()),
                                        "schedule_date_Int": int(datetime.strptime(availability_data_array_b_wise["schedule_date"], "%Y-%m-%d").strftime("%Y%m%d")),
                                        "start_time": availability_data_array_b_wise["start_time"],
                                        "end_time": availability_data_array_b_wise["end_time"],
                                        "full_time": availability_data_array_b_wise["full_time"],
                                        "time_block": availability_data_array_b_wise["time_block"],
                                        "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                        "session_number": get_session_no[availability_data_array_b_wise["time_block"]]["Session_Id"],
                                        "demand_forecast": round(TotalblkwiseData["forecast_value"]),
                                        "total_supply_availablity": round(TotalblkwiseData["totalAvailability"]),
                                        "surplus_deficit": round(TotalblkwiseData["surplus_deficit"]),
                                        "surplus_deficit_updated": surplus_deficit_updatedfornextBlock,
                                        "total_availablity_updated": TotalSupplyfornextBlock,
                                        "price": px_price,
                                        "rtm_forecasted_price": float(rtmforecastedpriceblk),
                                        "remaining_volume": float(total_diff),
                                        "plant_RMP_value": float(availability_plnt_arrayyy.get("RMP", 0)),
                                        "plant_TM_value": float(planttmvalue),
                                        "plant_SEZ_value": float(plantSEZvalue),
                                        "plant_MLTP_value": float(plantMLTPvalue),
                                        "up_down_RMP_volume": 0.0,
                                        "Final_plant_RMP_volume": round(plnt_100_volume_genrated, 3),
                                        "genrated_sg_volume": round(plnt_100_volume_genrated, 3),
                                        "actual_plant_volume": round(plnt_100_volume, 3),
                                        "actual_plant_volume_DC": round(plnt_100_volumeDC, 3),
                                        "plant_min_rum_volume": round(plnt_min_run_vol, 3),
                                        "category": availability_plnt_arrayyy["category"],
                                        "plant_name": availability_plnt_arrayyy["plant_name"],
                                        "key": availability_plnt_arrayyy["key"],
                                        "revision_no_SG": availability_plnt_arrayyy.get("revision_no", 0),
                                        "revision_no_DC": reverseplantDC[plnamekey].get("revision_no", 0) if plnamekey < len(reverseplantDC) else 0,
                                        "display_category": get_plant_list[availability_plnt_arrayyy["key"]]["scheduling_req_category"],
                                        "dispatch_type": availability_plnt_arrayyy.get("dispatch_type", ""),
                                        "total_RMP_volume": float(total_diff),
                                        "scheduling_requisition_type": "NA"
                                    }
                                    print(insertData)
                                    # Insert into DB as above
                                    plkey += 1
                            else:
                                reverse_array_next = list(reversed(get_plants_data[blk + 1]['result']))
                                nextblkSG = reverse_array_next[plnamekey]['volume'] if plnamekey < len(reverse_array_next) else 0

                                reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[
                                    availability_plnt_arrayyy["key"]
                                ] = min(plnt_100_volume_genrated, nextblkSG)

                                genratedTotalsupply += min(plnt_100_volume_genrated, nextblkSG)

                                insertData = {
                                    "schedule_date": availability_data_array_b_wise["schedule_date"],
                                    "schedule_date_timestamp": int(datetime.strptime(availability_data_array_b_wise["schedule_date"], "%Y-%m-%d").timestamp()),
                                    "schedule_date_Int": int(datetime.strptime(availability_data_array_b_wise["schedule_date"], "%Y-%m-%d").strftime("%Y%m%d")),
                                    "start_time": availability_data_array_b_wise["start_time"],
                                    "end_time": availability_data_array_b_wise["end_time"],
                                    "full_time": availability_data_array_b_wise["full_time"],
                                    "time_block": availability_data_array_b_wise["time_block"],
                                    "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                    "session_number": get_session_no[availability_data_array_b_wise["time_block"]]["Session_Id"],
                                    "demand_forecast": round(TotalblkwiseData["forecast_value"]),
                                    "total_supply_availablity": round(TotalblkwiseData["totalAvailability"]),
                                    "surplus_deficit": round(TotalblkwiseData["surplus_deficit"]),
                                    "surplus_deficit_updated": surplus_deficit_updatedfornextBlock,
                                    "total_availablity_updated": TotalSupplyfornextBlock,
                                    "price": px_price,
                                    "rtm_forecasted_price": float(rtmforecastedpriceblk),
                                    "remaining_volume": float(total_diff),
                                    "plant_RMP_value": float(availability_plnt_arrayyy.get("RMP", 0)),
                                    "plant_TM_value": float(planttmvalue),
                                    "plant_SEZ_value": float(plantSEZvalue),
                                    "plant_MLTP_value": float(plantMLTPvalue),
                                    "up_down_RMP_volume": 0.0,
                                    "Final_plant_RMP_volume": round(plnt_100_volume_genrated, 3),
                                    "genrated_sg_volume": round(plnt_100_volume_genrated, 3),
                                    "actual_plant_volume": round(plnt_100_volume, 3),
                                    "actual_plant_volume_DC": round(plnt_100_volumeDC, 3),
                                    "category": availability_plnt_arrayyy["category"],
                                    "plant_name": availability_plnt_arrayyy["plant_name"],
                                    "key": availability_plnt_arrayyy["key"],
                                    "revision_no_SG": availability_plnt_arrayyy.get("revision_no", 0),
                                    "revision_no_DC": reverseplantDC[plnamekey].get("revision_no", 0) if plnamekey < len(reverseplantDC) else 0,
                                    "display_category": get_plant_list[availability_plnt_arrayyy["key"]]["scheduling_req_category"],
                                    "dispatch_type": availability_plnt_arrayyy.get("dispatch_type", ""),
                                    "total_RMP_volume": float(total_diff),
                                    "scheduling_requisition_type": "NA"
                                }
                                print(insertData)
                                plkey += 1
                        else:
                            reverse_array_next = get_plants_data[blk + 1]['result']
                            nextblkSG = reverse_array_next[plnamekey]['volume'] if plnamekey < len(reverse_array_next) else 0

                            if nextblkSG >= 0:
                                reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[
                                    availability_plnt_arrayyy["key"]
                                ] = min(nextblkSG, nextblkSG)
                                genratedTotalsupply += min(nextblkSG, nextblkSG)
                            else:
                                reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[
                                    availability_plnt_arrayyy["key"]
                                ] = max(nextblkSG, nextblkSG)
                                genratedTotalsupply += max(nextblkSG, nextblkSG)

                            insertData = {
                                "schedule_date": availability_data_array_b_wise["schedule_date"],
                                "schedule_date_timestamp": int(datetime.strptime(
                                    availability_data_array_b_wise["schedule_date"], "%Y-%m-%d").timestamp()
                                ),
                                "schedule_date_Int": int(datetime.strptime(
                                    availability_data_array_b_wise["schedule_date"], "%Y-%m-%d").strftime("%Y%m%d")
                                ),
                                "start_time": availability_data_array_b_wise["start_time"],
                                "end_time": availability_data_array_b_wise["end_time"],
                                "full_time": availability_data_array_b_wise["full_time"],
                                "time_block": availability_data_array_b_wise["time_block"],
                                "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                "session_number": get_session_no[availability_data_array_b_wise["time_block"]]["Session_Id"],
                                "demand_forecast": round(TotalblkwiseData["forecast_value"]),
                                "total_supply_availablity": round(TotalblkwiseData["totalAvailability"]),
                                "surplus_deficit": round(TotalblkwiseData["surplus_deficit"]),
                                "surplus_deficit_updated": surplus_deficit_updatedfornextBlock,
                                "total_availablity_updated": TotalSupplyfornextBlock,
                                "price": px_price,
                                "rtm_forecasted_price": float(rtmforecastedpriceblk),
                                "remaining_volume": float(total_diff),
                                "plant_RMP_value": float(availability_plnt_arrayyy.get("RMP", 0)),
                                "plant_TM_value": float(planttmvalue),
                                "plant_SEZ_value": float(plantSEZvalue),
                                "plant_MLTP_value": float(plantMLTPvalue),
                                "up_down_RMP_volume": 0.0,
                                "Final_plant_RMP_volume": round(plnt_100_volume_genrated, 3),
                                "genrated_sg_volume": round(plnt_100_volume_genrated, 3),
                                "actual_plant_volume": round(plnt_100_volume, 3),
                                "actual_plant_volume_DC": round(plnt_100_volumeDC, 3),
                                "category": availability_plnt_arrayyy["category"],
                                "plant_name": availability_plnt_arrayyy["plant_name"],
                                "key": availability_plnt_arrayyy["key"],
                                "revision_no_SG": availability_plnt_arrayyy.get("revision_no", 0),
                                "revision_no_DC": reverseplantDC[plnamekey].get("revision_no", 0) if plnamekey < len(reverseplantDC) else 0,
                                "display_category": get_plant_list[availability_plnt_arrayyy["key"]]["scheduling_req_category"],
                                "dispatch_type": availability_plnt_arrayyy.get("dispatch_type", ""),
                                "total_RMP_volume": float(total_diff),
                                "scheduling_requisition_type": "NA"
                            }

                            plkey += 1
                else:
                    reverse_array = list(reversed(get_plants_data[blk].result))
                    for plnamekey, availability_plnt_arrayyyObj in enumerate(reverse_array):
                        # Cast object to dict (similar to (array) in PHP)
                        availability_plnt_arrayyy = dict(availability_plnt_arrayyyObj)

                        # PX_Price
                        px_price = round(
                            availability_plnt_arrayyy.get("PX_Price", 0)
                        )

                        # Plant 100% volume
                        plnt_100_volume = float(
                            availability_plnt_arrayyy.get("volume", 0)
                        )

                        # SG of Last plant
                        SGofLastplant = (
                            reviseDataArray
                                .get(blk, {})
                                .get("genrated_sg_volume", {})
                                .get(availability_plnt_arrayyy.get("key"), 0)
                        )

                        vlueofChangesandactual = SGofLastplant
                        plnt_100_volume_genrated = float(vlueofChangesandactual)

                        # Plant TM value
                        planttm_key = (
                            f"{availability_plnt_arrayyy.get('schedule_date')}_"
                            f"{availability_plnt_arrayyy.get('key')}_"
                            f"{availability_data_array_b_wise.get('time_block')}"
                        )
                        planttmvalue = all_tm_values.get(planttm_key, 0)

                        # Plant SEZ value
                        plantsez_key = (
                            f"{availability_plnt_arrayyy.get('schedule_date')}_"
                            f"{availability_plnt_arrayyy.get('key')}_"
                            f"{availability_data_array_b_wise.get('time_block')}"
                        )
                        plantSEZvalue = all_sez_values.get(plantsez_key, 0)

                        # Plant MLTP value
                        plantmltp_key = (
                            f"{availability_plnt_arrayyy.get('schedule_date')}_"
                            f"{availability_plnt_arrayyy.get('key')}"
                        )
                        plantMLTPvalue = all_mltp_values.get(plantmltp_key, 1)

                        # Plant 100% volume DC
                        plnt_100_volumeDC = float(
                            getattr(
                                get_plants_data[blk].result[plnamekey], "volume", 0
                            )
                        )

                       

                        plant_key = availability_plnt_arrayyy["key"]

                        if get_plant_list[plant_key]["scheduling_req_category"] == "InSGS":
                            
                            plantcontblockMinValue = 0

                            SR_ForecastPriceCheck = self.check_forecast_price_apply_for_surplus(
                                rtmforecastedpriceblk, px_price, "deficit"
                            )

                            if plnt_100_volume_genrated >= 0 and (plnt_100_volume_genrated - plnt_100_volume_genrated) >= 0:

                                plnt_min_run_vol = float(availability_plnt_arrayyy.get("volume", 0)) * planttmvalue

                                plnt_RMP_run_vol = min(
                                    plnt_100_volume_genrated + availability_plnt_arrayyy.get("RMP", 0),
                                    plnt_100_volumeDC
                                )

                                total_minus_RPM_vol = max(plnt_RMP_run_vol - plnt_100_volume_genrated, 0)

                                if total_diff < 0:
                                    if total_diff + total_minus_RPM_vol > 0:
                                        sell_volume = min(total_diff, total_minus_RPM_vol)
                                        total_minus_RPM_vol = abs(sell_volume)
                                        Remaning_value = abs(sell_volume)
                                        total_diff = 0
                                    else:
                                        sell_volume = total_diff + total_minus_RPM_vol
                                        Remaning_value = abs(sell_volume)
                                        total_diff = total_diff + total_minus_RPM_vol

                                    reverseArraynext = get_plants_data[blk + 1].result
                                    nextblkSG = getattr(reverseArraynext[plnamekey], "volume", 0) if plnamekey in reverseArraynext else 0

                                    reviseDataArray[blk + 1]["genrated_sg_volume"][plant_key] = min(
                                        plnt_100_volume_genrated + total_minus_RPM_vol,
                                        nextblkSG
                                    )
                                    genratedTotalsupply += min(
                                        plnt_100_volume_genrated + total_minus_RPM_vol,
                                        nextblkSG
                                    )

                                    insertData = {
                                        "schedule_date": availability_data_array_b_wise["schedule_date"],
                                        "schedule_date_timestamp": int(datetime.strptime(
                                            availability_data_array_b_wise["schedule_date"], "%Y-%m-%d"
                                        ).timestamp()),
                                        "schedule_date_Int": int(datetime.strptime(
                                            availability_data_array_b_wise["schedule_date"], "%Y-%m-%d"
                                        ).strftime("%Y%m%d")),
                                        "start_time": availability_data_array_b_wise["start_time"],
                                        "end_time": availability_data_array_b_wise["end_time"],
                                        "full_time": availability_data_array_b_wise["full_time"],
                                        "time_block": availability_data_array_b_wise["time_block"],
                                        "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                        "session_number": get_session_no[availability_data_array_b_wise["time_block"]]["Session_Id"],
                                        "demand_forecast": round(TotalblkwiseData["forecast_value"]),
                                        "total_supply_availablity": round(TotalblkwiseData["totalAvailability"]),
                                        "surplus_deficit": round(TotalblkwiseData["surplus_deficit"]),
                                        "surplus_deficit_updated": surplus_deficit_updatedfornextBlock,
                                        "total_availablity_updated": TotalSupplyfornextBlock,
                                        "price": px_price,
                                        "rtm_forecasted_price": float(rtmforecastedpriceblk),
                                        "remaining_volume": float(total_diff),
                                        "plant_RMP_value": float(availability_plnt_arrayyy.get("RMP", 0)),
                                        "plant_TM_value": float(planttmvalue),
                                        "plant_SEZ_value": float(plantSEZvalue),
                                        "plant_MLTP_value": float(plantMLTPvalue),
                                        "up_down_RMP_volume": float(total_minus_RPM_vol),
                                        "Final_plant_RMP_volume": round(plnt_100_volume_genrated + total_minus_RPM_vol, 3),
                                        "genrated_sg_volume": round(plnt_100_volume_genrated, 3),
                                        "actual_plant_volume": float(round(plnt_100_volume, 3)),
                                        "actual_plant_volume_DC": float(round(plnt_100_volumeDC, 3)),
                                        "category": availability_plnt_arrayyy["category"],
                                        "plant_name": availability_plnt_arrayyy["plant_name"],
                                        "key": plant_key,
                                        "revision_no_SG": availability_plnt_arrayyy["revision_no"],
                                        "revision_no_DC": get_plants_data[blk].result[plnamekey].revision_no,
                                        "display_category": get_plant_list[plant_key]["scheduling_req_category"],
                                        "dispatch_type": availability_plnt_arrayyy["dispatch_type"],
                                        "total_RMP_volume": float(total_diff),
                                        "scheduling_requisition_type": "Scheduling",
                                    }

                                    # DB insertion (replace with your ORM or DB execution)
                                   
                                    plkey += 1

                                else:
                                    # ELSE branch when total_diff >= 0
                                    reverseArraynext = get_plants_data[blk + 1].result
                                    nextblkSG = getattr(reverseArraynext[plnamekey], "volume", 0) if plnamekey in reverseArraynext else 0

                                    reviseDataArray[blk + 1]["genrated_sg_volume"][plant_key] = min(
                                        plnt_100_volume_genrated, nextblkSG
                                    )
                                    genratedTotalsupply += min(plnt_100_volume_genrated, nextblkSG)

                                    insertData = {
                                        **insertData,  # reuse previous keys
                                        "up_down_RMP_volume": 0,
                                        "Final_plant_RMP_volume": round(plnt_100_volume_genrated, 3),
                                        "genrated_sg_volume": round(plnt_100_volume_genrated, 3),
                                        "scheduling_requisition_type": "NA"
                                    }

                                    # db.session.execute(
                                    #     scheduling_requisition_calculation_table.insert().values(insertData)
                                    # )
                                    plkey += 1

                            else:
                                # ELSE branch for non InSGS plants
                                reverseArraynext = get_plants_data[blk + 1].result
                                nextblkSG = getattr(reverseArraynext[plnamekey], "volume", 0) if plnamekey in reverseArraynext else 0

                                reviseDataArray[blk + 1]["genrated_sg_volume"][plant_key] = min(
                                    plnt_100_volume_genrated, nextblkSG
                                )
                                genratedTotalsupply += min(plnt_100_volume_genrated, nextblkSG)

                                insertData = {
                                    **insertData,
                                    "up_down_RMP_volume": 0,
                                    "Final_plant_RMP_volume": round(plnt_100_volume_genrated, 3),
                                    "genrated_sg_volume": round(plnt_100_volume_genrated, 3),
                                    "scheduling_requisition_type": "NA"
                                }

                                # db.session.execute(
                                #     scheduling_requisition_calculation_table.insert().values(insertData)
                                # )
                                plkey += 1
                        else:
                            # Reverse the next block's plant results
                            reverseArraynext = list(reversed(get_plants_data[blk + 1].result))

                            # Get the next block SG volume safely
                            nextblkSG = getattr(reverseArraynext[plnamekey], "volume", 0) if plnamekey < len(reverseArraynext) else 0

                            # Update revised SG volumes and generated total supply
                            if nextblkSG >= 0:
                                reviseDataArray[blk + 1]["genrated_sg_volume"][availability_plnt_arrayyy["key"]] = min(nextblkSG, nextblkSG)
                                genratedTotalsupply += min(nextblkSG, nextblkSG)
                            else:
                                reviseDataArray[blk + 1]["genrated_sg_volume"][availability_plnt_arrayyy["key"]] = max(nextblkSG, nextblkSG)
                                genratedTotalsupply += max(nextblkSG, nextblkSG)

                            # Prepare insert data dictionary
                            schedule_date_str = availability_data_array_b_wise["schedule_date"]
                            insertData = {
                                "schedule_date": schedule_date_str,
                                "schedule_date_timestamp": int(datetime.strptime(schedule_date_str, "%Y-%m-%d").timestamp()),
                                "schedule_date_Int": int(datetime.strptime(schedule_date_str, "%Y-%m-%d").strftime("%Y%m%d")),
                                "start_time": availability_data_array_b_wise["start_time"],
                                "end_time": availability_data_array_b_wise["end_time"],
                                "full_time": availability_data_array_b_wise["full_time"],
                                "time_block": availability_data_array_b_wise["time_block"],
                                "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                "session_number": get_session_no[availability_data_array_b_wise["time_block"]]["Session_Id"],
                                "demand_forecast": round(TotalblkwiseData["forecast_value"]),
                                "total_supply_availablity": round(TotalblkwiseData["totalAvailability"]),
                                "surplus_deficit": round(TotalblkwiseData["surplus_deficit"]),
                                "surplus_deficit_updated": surplus_deficit_updatedfornextBlock,
                                "total_availablity_updated": TotalSupplyfornextBlock,
                                "price": px_price,
                                "rtm_forecasted_price": float(rtmforecastedpriceblk),
                                "remaining_volume": float(total_diff),
                                "plant_RMP_value": float(availability_plnt_arrayyy.get("RMP", 0)),
                                "plant_TM_value": float(planttmvalue),
                                "plant_SEZ_value": float(plantSEZvalue),
                                "plant_MLTP_value": float(plantMLTPvalue),
                                "up_down_RMP_volume": 0.0,
                                "Final_plant_RMP_volume": round(plnt_100_volume_genrated, 3),
                                "genrated_sg_volume": round(plnt_100_volume_genrated, 3),
                                "actual_plant_volume": round(float(plnt_100_volume), 3),
                                "actual_plant_volume_DC": round(float(plnt_100_volumeDC), 3),
                                "category": availability_plnt_arrayyy["category"],
                                "plant_name": availability_plnt_arrayyy["plant_name"],
                                "key": availability_plnt_arrayyy["key"],
                                "revision_no_SG": availability_plnt_arrayyy["revision_no"],
                                "revision_no_DC": getattr(get_plants_data[blk].result[plnamekey], "revision_no", 0),
                                "display_category": get_plant_list[availability_plnt_arrayyy["key"]]["scheduling_req_category"],
                                "dispatch_type": availability_plnt_arrayyy["dispatch_type"],
                                "total_RMP_volume": float(total_diff),
                                "scheduling_requisition_type": "NA"
                            }

                            # Database insert (adapt this to your ORM/DB)
                            # db.session.execute(scheduling_requisition_calculation_table.insert().values(insertData))

                            plkey += 1
                blk22 += 1
            elif blk > self.BlkNoFromRunReq and blk < self.BlkNoToRunReq:
                total_diff = (
                    reviseDataTotalsupply[blk]["surplus_deficit_updated"]
                    if blk in reviseDataTotalsupply and "surplus_deficit_updated" in reviseDataTotalsupply[blk]
                    else TotalblkwiseData["surplus_deficit"]
                )

                surplus_deficit_updatedfornextBlock = (
                    reviseDataTotalsupply[blk]["surplus_deficit_updated"]
                    if blk in reviseDataTotalsupply and "surplus_deficit_updated" in reviseDataTotalsupply[blk]
                    else TotalblkwiseData["surplus_deficit"]
                )

                TotalSupplyfornextBlock = (
                    reviseDataTotalsupply[blk]["total_supply_updated"]
                    if blk in reviseDataTotalsupply and "total_supply_updated" in reviseDataTotalsupply[blk]
                    else TotalblkwiseData["totalAvailability"]
                )
                

                availability_data_array_b_wise = get_plants_data[blk]  # assuming getplantsData is a list of dicts

                tptal = 0
                plnt_100_volume = 0
                Remaning_value = 0
                if total_diff > 0:
                    for plnamekey, availability_plnt_arrayyyObj in enumerate(get_plants_data[blk]["result"]):
                        availability_plnt_arrayyy = dict(availability_plnt_arrayyyObj)  # convert object to dict if needed

                        px_price = round(availability_plnt_arrayyy.get("PX_Price", 0))

                        tm_key = f"{availability_plnt_arrayyy['schedule_date']}_{availability_plnt_arrayyy['key']}_{availability_data_array_b_wise['time_block']}"
                        planttmvalue = all_tm_values.get(tm_key, 0)
                        plantSEZvalue = all_sez_values.get(tm_key, 0)

                        mltp_key = f"{availability_plnt_arrayyy['schedule_date']}_{availability_plnt_arrayyy['key']}"
                        plantMLTPvalue = all_mltp_values.get(mltp_key, 1)

                        SGofLastplant = reviseDataArray.get(blk, {}).get("genrated_sg_volume", {}).get(
                            availability_plnt_arrayyy["key"], 0
                        )

                        plnt_100_volume = float(availability_plnt_arrayyy.get("volume", 0))

                        reverseplantDC = list(reversed(get_plants_data[blk]["result"]))
                        plnt_100_volume_genrated = float(SGofLastplant)
                        vlueofChangesandactual = SGofLastplant

                        if get_plant_list[availability_plnt_arrayyy["key"]]["scheduling_req_category"] != "InRE":
                            plantcontblockMinValue = 0
                            SR_ForecastPriceCheck = self.check_forecast_price_apply_for_surplus(rtmforecastedpriceblk, px_price, "surplus")

                            if plnt_100_volume_genrated > 0 and SR_ForecastPriceCheck:
                                plnt_min_run_vol = float(planttmvalue)
                                plnt_RMP_run_vol = max(SGofLastplant - availability_plnt_arrayyy["RMP"], plnt_min_run_vol)
                                total_minus_RPM_vol = max(plnt_100_volume_genrated - plnt_RMP_run_vol, 0)

                                sell_volume = 0
                                if total_diff > 0:
                                    if total_diff - total_minus_RPM_vol < 0:
                                        sell_volume = min(total_diff, total_minus_RPM_vol)
                                        total_minus_RPM_vol = abs(sell_volume)
                                        total_diff = 0
                                        Remaning_value = 0
                                    else:
                                        sell_volume = total_diff - total_minus_RPM_vol
                                        Remaning_value = sell_volume
                                        total_diff = sell_volume

                                tptal += plnt_100_volume

                                if round(sell_volume, 2) >= 0.01:
                                    reverseArraynext = list(reversed(get_plants_data[blk + 1]["result"]))
                                    nextblkSG = reverseArraynext[plnamekey]["volume"] if plnamekey < len(reverseArraynext) else 0
                                    reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[
                                        availability_plnt_arrayyy["key"]
                                    ] = min(plnt_100_volume_genrated - total_minus_RPM_vol, nextblkSG)
                                    genratedTotalsupply += min(plnt_100_volume_genrated - total_minus_RPM_vol, nextblkSG)

                                    insertData = {
                                        "schedule_date": availability_data_array_b_wise["schedule_date"],
                                        "schedule_date_timestamp": int(datetime.strptime(availability_data_array_b_wise["schedule_date"], "%Y-%m-%d").timestamp()),
                                        "schedule_date_Int": int(datetime.strptime(availability_data_array_b_wise["schedule_date"], "%Y-%m-%d").strftime("%Y%m%d")),
                                        "start_time": availability_data_array_b_wise["start_time"],
                                        "end_time": availability_data_array_b_wise["end_time"],
                                        "full_time": availability_data_array_b_wise["full_time"],
                                        "time_block": availability_data_array_b_wise["time_block"],
                                        "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                        "session_number": get_session_no[availability_data_array_b_wise["time_block"]]["Session_Id"],
                                        "demand_forecast": round(TotalblkwiseData["forecast_value"]),
                                        "total_supply_availablity": round(TotalblkwiseData["totalAvailability"]),
                                        "surplus_deficit": round(TotalblkwiseData["surplus_deficit"]),
                                        "surplus_deficit_updated": surplus_deficit_updatedfornextBlock,
                                        "total_availablity_updated": TotalSupplyfornextBlock,
                                        "price": px_price,
                                        "rtm_forecasted_price": float(rtmforecastedpriceblk),
                                        "remaining_volume": float(total_diff),
                                        "plant_RMP_value": float(availability_plnt_arrayyy["RMP"]),
                                        "plant_TM_value": float(planttmvalue),
                                        "plant_SEZ_value": float(plantSEZvalue),
                                        "plant_MLTP_value": float(plantMLTPvalue),
                                        "up_down_RMP_volume": float(total_minus_RPM_vol),
                                        "Final_plant_RMP_volume": round(plnt_100_volume_genrated - total_minus_RPM_vol, 3),
                                        "genrated_sg_volume": round(plnt_100_volume_genrated, 3),
                                        "actual_plant_volume": round(plnt_100_volume, 3),
                                        "actual_plant_volume_DC": round(plnt_100_volumeDC, 3),
                                        "category": availability_plnt_arrayyy["category"],
                                        "plant_name": availability_plnt_arrayyy["plant_name"],
                                        "key": availability_plnt_arrayyy["key"],
                                        "revision_no_SG": availability_plnt_arrayyy["revision_no"],
                                        "revision_no_DC": reverseplantDC[plnamekey]["revision_no"],
                                        "display_category": get_plant_list[availability_plnt_arrayyy["key"]]["scheduling_req_category"],
                                        "dispatch_type": availability_plnt_arrayyy["dispatch_type"],
                                        "total_RMP_volume": float(total_diff),
                                        "scheduling_requisition_type": "Requisition",
                                    }
                                    # DB insertion here
                                    # db.insert(insertData)
                                    plkey += 1
                                else:
                                    # Similar else block for when sell_volume < 0.01
                                    pass
                        else:
                            # InRE category logic
                            reverseArraynext = get_plants_data[blk + 1]["result"]
                            nextblkSG = reverseArraynext[plnamekey]["volume"] if plnamekey < len(reverseArraynext) else 0
                            if nextblkSG >= 0:
                                reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[
                                    availability_plnt_arrayyy["key"]
                                ] = min(nextblkSG, nextblkSG)
                                genratedTotalsupply += min(nextblkSG, nextblkSG)
                            else:
                                reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[
                                    availability_plnt_arrayyy["key"]
                                ] = max(nextblkSG, nextblkSG)
                                genratedTotalsupply += max(nextblkSG, nextblkSG)

                            insertData = {
                                "schedule_date": availability_data_array_b_wise["schedule_date"],
                                "schedule_date_timestamp": int(datetime.strptime(availability_data_array_b_wise["schedule_date"], "%Y-%m-%d").timestamp()),
                                "schedule_date_Int": int(datetime.strptime(availability_data_array_b_wise["schedule_date"], "%Y-%m-%d").strftime("%Y%m%d")),
                                "start_time": availability_data_array_b_wise["start_time"],
                                "end_time": availability_data_array_b_wise["end_time"],
                                "full_time": availability_data_array_b_wise["full_time"],
                                "time_block": availability_data_array_b_wise["time_block"],
                                "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                "session_number": get_session_no[availability_data_array_b_wise["time_block"]]["Session_Id"],
                                "demand_forecast": round(TotalblkwiseData["forecast_value"]),
                                "total_supply_availablity": round(TotalblkwiseData["totalAvailability"]),
                                "surplus_deficit": round(TotalblkwiseData["surplus_deficit"]),
                                "surplus_deficit_updated": surplus_deficit_updatedfornextBlock,
                                "total_availablity_updated": TotalSupplyfornextBlock,
                                "price": px_price,
                                "rtm_forecasted_price": float(rtmforecastedpriceblk),
                                "remaining_volume": float(total_diff),
                                "plant_RMP_value": float(availability_plnt_arrayyy["RMP"]),
                                "plant_TM_value": float(planttmvalue),
                                "plant_SEZ_value": float(plantSEZvalue),
                                "plant_MLTP_value": float(plantMLTPvalue),
                                "up_down_RMP_volume": 0.0,
                                "Final_plant_RMP_volume": round(plnt_100_volume_genrated, 3),
                                "genrated_sg_volume": round(plnt_100_volume_genrated, 3),
                                "actual_plant_volume": round(plnt_100_volume, 3),
                                "actual_plant_volume_DC": round(plnt_100_volumeDC, 3),
                                "category": availability_plnt_arrayyy["category"],
                                "plant_name": availability_plnt_arrayyy["plant_name"],
                                "key": availability_plnt_arrayyy["key"],
                                "revision_no_SG": availability_plnt_arrayyy["revision_no"],
                                "revision_no_DC": reverseplantDC[plnamekey]["revision_no"],
                                "display_category": get_plant_list[availability_plnt_arrayyy["key"]]["scheduling_req_category"],
                                "dispatch_type": availability_plnt_arrayyy["dispatch_type"],
                                "total_RMP_volume": float(total_diff),
                                "scheduling_requisition_type": "NA",
                            }
                            # DB insertion here
                            # db.insert(insertData)
                            plkey += 1
                else:
                    # Reverse the plant data for current block
                    reverse_array = list(reversed(get_plants_data[blk]["result"]))

                    for plnamekey, availability_plnt_arrayyyObj in enumerate(reverse_array):
                        availability_plnt_arrayyy = dict(availability_plnt_arrayyyObj)  # Convert object to dict

                        px_price = round(availability_plnt_arrayyy.get("PX_Price", 0))

                        SGofLastplant = reviseDataArray.get(blk, {}).get("genrated_sg_volume", {}).get(
                            availability_plnt_arrayyy["key"], 0
                        )

                        plnt_100_volume = float(availability_plnt_arrayyy.get("volume", 0))
                        vlueofChangesandactual = SGofLastplant
                        plnt_100_volume_genrated = float(vlueofChangesandactual)

                        tm_key = f"{availability_plnt_arrayyy['schedule_date']}_{availability_plnt_arrayyy['key']}_{availability_data_array_b_wise['time_block']}"
                        planttmvalue = all_tm_values.get(tm_key, 0)
                        plantSEZvalue = all_sez_values.get(tm_key, 0)
                        mltp_key = f"{availability_plnt_arrayyy['schedule_date']}_{availability_plnt_arrayyy['key']}"
                        plantMLTPvalue = all_mltp_values.get(mltp_key, 1)

                        plnt_100_volumeDC = float(
                            get_plants_data[blk]["result"][plnamekey]["volume"]
                            if plnamekey < len(get_plants_data[blk]["result"])
                            else 0
                        )

                        category = get_plant_list[availability_plnt_arrayyy["key"]]["scheduling_req_category"]

                        if category != "InRE":
                            plantcontblockMinValue = 0
                            SR_ForecastPriceCheck = self.check_forecast_price_apply_for_surplus(
                                rtmforecastedpriceblk, px_price, "deficit"
                            )

                            if plnt_100_volume_genrated >= 0 and SR_ForecastPriceCheck:
                                plnt_min_run_vol = plnt_100_volume * planttmvalue
                                plnt_RMP_run_vol = min(plnt_100_volume_genrated + availability_plnt_arrayyy.get("RMP", 0), plnt_100_volumeDC)
                                total_minus_RPM_vol = max(plnt_RMP_run_vol - plnt_100_volume_genrated, 0)

                                if total_diff < 0:
                                    if total_diff + total_minus_RPM_vol > 0:
                                        sell_volume = min(total_diff, total_minus_RPM_vol)
                                        total_minus_RPM_vol = abs(sell_volume)
                                        Remaning_value = abs(sell_volume)
                                        total_diff = 0
                                    else:
                                        sell_volume = total_diff + total_minus_RPM_vol
                                        Remaning_value = sell_volume
                                        total_diff = total_diff + total_minus_RPM_vol

                                reverseArraynext = get_plants_data[blk + 1]["result"]
                                nextblkSG = reverseArraynext[plnamekey]["volume"] if plnamekey < len(reverseArraynext) else 0
                                reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[availability_plnt_arrayyy["key"]] = min(
                                    plnt_100_volume_genrated + total_minus_RPM_vol, nextblkSG
                                )
                                genratedTotalsupply += min(plnt_100_volume_genrated + total_minus_RPM_vol, nextblkSG)

                                # Prepare insert data
                                insertData = {
                                    "schedule_date": availability_data_array_b_wise["schedule_date"],
                                    "schedule_date_timestamp": int(datetime.strptime(availability_data_array_b_wise["schedule_date"], "%Y-%m-%d").timestamp()),
                                    "schedule_date_Int": int(datetime.strptime(availability_data_array_b_wise["schedule_date"], "%Y-%m-%d").strftime("%Y%m%d")),
                                    "start_time": availability_data_array_b_wise["start_time"],
                                    "end_time": availability_data_array_b_wise["end_time"],
                                    "full_time": availability_data_array_b_wise["full_time"],
                                    "time_block": availability_data_array_b_wise["time_block"],
                                    "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                    "session_number": get_session_no[availability_data_array_b_wise["time_block"]]["Session_Id"],
                                    "demand_forecast": round(TotalblkwiseData["forecast_value"]),
                                    "total_supply_availablity": round(TotalblkwiseData["totalAvailability"]),
                                    "surplus_deficit": round(TotalblkwiseData["surplus_deficit"]),
                                    "surplus_deficit_updated": surplus_deficit_updatedfornextBlock,
                                    "total_availablity_updated": TotalSupplyfornextBlock,
                                    "price": px_price,
                                    "rtm_forecasted_price": float(rtmforecastedpriceblk),
                                    "remaining_volume": float(total_diff),
                                    "plant_RMP_value": float(availability_plnt_arrayyy.get("RMP", 0)),
                                    "plant_TM_value": float(planttmvalue),
                                    "plant_SEZ_value": float(plantSEZvalue),
                                    "plant_MLTP_value": float(plantMLTPvalue),
                                    "up_down_RMP_volume": float(total_minus_RPM_vol),
                                    "Final_plant_RMP_volume": round(plnt_100_volume_genrated + total_minus_RPM_vol, 3),
                                    "genrated_sg_volume": round(plnt_100_volume_genrated, 3),
                                    "actual_plant_volume": round(plnt_100_volume, 3),
                                    "actual_plant_volume_DC": round(plnt_100_volumeDC, 3),
                                    "category": availability_plnt_arrayyy["category"],
                                    "plant_name": availability_plnt_arrayyy["plant_name"],
                                    "key": availability_plnt_arrayyy["key"],
                                    "revision_no_SG": availability_plnt_arrayyy["revision_no"],
                                    "revision_no_DC": get_plants_data[blk]["result"][plnamekey]["revision_no"],
                                    "display_category": category,
                                    "dispatch_type": availability_plnt_arrayyy["dispatch_type"],
                                    "total_RMP_volume": float(total_diff),
                                    "scheduling_requisition_type": "Requisition",
                                }
                                # DB insertion placeholder
                                # db.insert(insertData)
                                plkey += 1

                            else:
                                # If forecast price check fails
                                reverseArraynext = get_plants_data[blk + 1]["result"]
                                nextblkSG = reverseArraynext[plnamekey]["volume"] if plnamekey < len(reverseArraynext) else 0
                                reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[availability_plnt_arrayyy["key"]] = min(plnt_100_volume_genrated, nextblkSG)
                                genratedTotalsupply += min(plnt_100_volume_genrated, nextblkSG)

                                # Insert with "NA" type
                                insertData["scheduling_requisition_type"] = "NA"
                                # db.insert(insertData)
                                plkey += 1

                        else:  # category == "InRE"
                            reverseArraynext = list(reversed(get_plants_data[blk + 1]["result"]))
                            nextblkSG = reverseArraynext[plnamekey]["volume"] if plnamekey < len(reverseArraynext) else 0
                            if nextblkSG >= 0:
                                reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[availability_plnt_arrayyy["key"]] = min(nextblkSG, nextblkSG)
                                genratedTotalsupply += min(nextblkSG, nextblkSG)
                            else:
                                reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[availability_plnt_arrayyy["key"]] = max(nextblkSG, nextblkSG)
                                genratedTotalsupply += max(nextblkSG, nextblkSG)

                            insertData["scheduling_requisition_type"] = "NA"
                            # db.insert(insertData)

                    blk22 += 1
            else:
                # Equivalent Python Conversion
                
                # Safely fetch values with `.get()` instead of PHP's isset()
                blk_data = reviseDataTotalsupply[blk] if blk < len(reviseDataTotalsupply) else {}
                total_diff = blk_data.get("surplus_deficit_updated", TotalblkwiseData.get("surplus_deficit", 0))
                
                surplus_deficit_updatedfornextBlock =  blk_data.get(
                        "surplus_deficit_updated", TotalblkwiseData.get("surplus_deficit", 0)
                    )

                TotalSupplyfornextBlock = blk_data.get(
                        "total_supply_updated", TotalblkwiseData.get("totalAvailability", 0)
                    )
                                
                availability_data_array_b_wise = dict(get_plants_data[blk])  # PHP (array) cast
                
                tptal = 0
                plnt_100_volume = 0
                plkey = 0
                Remaning_value = 0
                
                # reverseArray = getplantsData[blk]->result
                reverseArray = get_plants_data[blk]["result"]
                
                for plnamekey, availability_plnt_arrayyyObj in enumerate(reverseArray):
                    
                    availability_plnt_arrayyy = dict(availability_plnt_arrayyyObj)
                    
                    # PHP round with isset
                    px_price = round(
                        availability_plnt_arrayyy.get("PX_Price", 0)
                    )
                    
                    plnt_100_volume = float(
                        availability_plnt_arrayyy.get("volume", 0)
                    )
                    
                    reverseDCData = list(reversed(get_plants_data[blk]["result"]))
                    
                    
                    plnt_100_volumeDC = float(
                        reverseDCData[plnamekey]["volume"]  if plnamekey < len(reverseDCData) else 0
                    )
                    
                    plnt_100_volume_genrated = (
                            reviseDataArray.get(blk, {})
                                        .get("genrated_sg_volume", {})
                                        .get(availability_plnt_arrayyy["key"], plnt_100_volume)
                        )
                    
                    
                    
                    
                    # Construct composite keys for lookup
                    composite_key_tm = f"{availability_plnt_arrayyy['schedule_date']}_{availability_plnt_arrayyy['key']}_{availability_data_array_b_wise['time_block']}"
                    
                    composite_key_sez = f"{availability_plnt_arrayyy['schedule_date']}_{availability_plnt_arrayyy['key']}_{availability_data_array_b_wise['time_block']}"
                    
                    composite_key_mltp = f"{availability_plnt_arrayyy['schedule_date']}_{availability_plnt_arrayyy['key']}"
                    
                    planttmvalue = all_tm_values.get(composite_key_tm, 0) if isinstance(all_tm_values, dict) else 0

                    
                    plantSEZvalue = all_sez_values.get(composite_key_sez, 0) if isinstance(all_sez_values, dict) else 0

                    plantMLTPvalue = all_mltp_values.get(composite_key_mltp, 1) if isinstance(all_mltp_values, dict) else 1
                    
                    plant = next(
                        (p for p in get_plant_list if p.get("key") == availability_plnt_arrayyy["key"]),
                        None
                    )

                    # return "hello"
                    # Example condition
                    # if (
                    #     True
                    # ):
                    
                    if (
                        plant is not None
                        # and plant.get("scheduling_req_category") == "InSGS"
                        and blk >= (self.BlkNoForRunSchedule - 1)
                        and blk < (self.BlkNoForRunSchedule + 1)
                    ):
                          
                        
                        nextblkSG = (
                            get_plants_data[blk + 1]["result"][plnamekey]['volume']
                            if plnamekey in get_plants_data[blk + 1]["result"]
                            else 0
                        )
                       
                        reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[
                            availability_plnt_arrayyy["key"]
                        ] = min(plnt_100_volume_genrated, nextblkSG)

                        
                        
                        genratedTotalsupply += min(plnt_100_volume_genrated, nextblkSG)
                        
                        print(
                            f"SR-NA{availability_plnt_arrayyy['key']}=SG-{plnt_100_volume_genrated}---DC-{plnt_100_volume}--{availability_data_array_b_wise['time_block']}"
                        )
                        time_block = int(availability_data_array_b_wise["time_block"])  # convert float to int
                        session_number = get_session_no[time_block]["Session_Id"]  # now safe
                        # return availability_data_array_b_wise
                        insertData = {
                            "schedule_date": availability_data_array_b_wise["schedule_date"],
                            "schedule_date_timestamp": int(
                                datetime.strptime(
                                    availability_data_array_b_wise["schedule_date"], "%Y-%m-%d"
                                ).timestamp()
                            ),
                            "schedule_date_Int": int(
                                datetime.strptime(
                                    availability_data_array_b_wise["schedule_date"], "%Y-%m-%d"
                                ).strftime("%Y%m%d")
                            ),
                            "time_block": availability_data_array_b_wise["time_block"],
                            "dc_sg_type": availability_data_array_b_wise["dc_sg"],
                            "session_number": session_number,
                            "demand_forecast": round(TotalblkwiseData["forecast_value"]),
                            "total_supply_availablity": round(TotalblkwiseData["totalAvailability"]),
                            "surplus_deficit": round(TotalblkwiseData["surplus_deficit"]),
                            "surplus_deficit_updated": surplus_deficit_updatedfornextBlock,
                            "total_availablity_updated": TotalSupplyfornextBlock,
                            "price": px_price,
                            "rtm_forecasted_price": float(rtmforecastedpriceblk),
                            "remaining_volume": float(total_diff),
                            "plant_RMP_value": float(availability_plnt_arrayyy.get("RMP", 0)),
                            "plant_TM_value": float(planttmvalue),
                            "plant_SEZ_value": float(plantSEZvalue),
                            "plant_MLTP_value": float(plantMLTPvalue),
                            "up_down_RMP_volume": float(0),
                            "Final_plant_RMP_volume": round(plnt_100_volume_genrated - 0, 3),
                            "genrated_sg_volume": round(plnt_100_volume_genrated - 0, 3),
                            "actual_plant_volume": round(plnt_100_volume, 3),
                            "actual_plant_volume_DC": round(plnt_100_volumeDC, 3),
                            "category": availability_plnt_arrayyy["category"],
                            "plant_name": availability_plnt_arrayyy["plant_name"],
                            "key": availability_plnt_arrayyy["key"],
                            # # "revision_no_SG": availability_plnt_arrayyy["revision_no"],
                            # # "revision_no_DC": getattr(reverseDCData[plnamekey], "revision_no", 0),
                            # "display_category": get_plant_list[availability_plnt_arrayyy["key"]]["scheduling_req_category"],
                            # "dispatch_type": availability_plnt_arrayyy["dispatch_type"],
                            "total_RMP_volume": float(total_diff),
                            "scheduling_requisition_type": "NA",
                        }
                        # return insertData
                        self.scheduling_requisition_repo.create(insertData)
                        # Replace this with actual DB insert
                        # db.session.add(SchedulingRequisitionCalculation(**insertData))
                        # db.session.commit()
                        plkey += 1
                    else:
                        
                        nextblkSG = (
                            get_plants_data[blk + 1]["result"][plnamekey]['volume']
                            if plnamekey in get_plants_data[blk + 1]["result"]
                            else 0
                        )
                        
                        reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[
                            availability_plnt_arrayyy["key"]
                        ] = nextblkSG

                        
                        
                        # Update total generated supply
                        genratedTotalsupply += nextblkSG
                        time_block = int(availability_data_array_b_wise["time_block"])  # convert float to int
                        session_number = get_session_no[time_block]["Session_Id"]  # now safe
                        
                        insertData = {
                            "schedule_date": availability_data_array_b_wise["schedule_date"],
                            "schedule_date_timestamp": int(
                                datetime.strptime(
                                    availability_data_array_b_wise["schedule_date"], "%Y-%m-%d"
                                ).timestamp()
                            ),
                            "schedule_date_Int": int(
                                datetime.strptime(
                                    availability_data_array_b_wise["schedule_date"], "%Y-%m-%d"
                                ).strftime("%Y%m%d")
                            ),
                            "time_block": availability_data_array_b_wise["time_block"],
                            "dc_sg_type": availability_data_array_b_wise["dc_sg"],
                            "session_number": session_number,
                            "demand_forecast": round(TotalblkwiseData["forecast_value"]),
                            "total_supply_availablity": round(TotalblkwiseData["totalAvailability"]),
                            "surplus_deficit": round(TotalblkwiseData["surplus_deficit"]),
                            "surplus_deficit_updated": surplus_deficit_updatedfornextBlock,
                            "total_availablity_updated": TotalSupplyfornextBlock,
                            "price": px_price,
                            "rtm_forecasted_price": float(rtmforecastedpriceblk),
                            "remaining_volume": float(total_diff),
                            "plant_RMP_value": float(availability_plnt_arrayyy.get("RMP", 0)),
                            "plant_TM_value": float(planttmvalue),
                            "plant_SEZ_value": float(plantSEZvalue),
                            "plant_MLTP_value": float(plantMLTPvalue),
                            "up_down_RMP_volume": float(0),
                            "Final_plant_RMP_volume": round(plnt_100_volume_genrated - 0, 3),
                            "genrated_sg_volume": round(plnt_100_volume_genrated - 0, 3),
                            "actual_plant_volume": round(plnt_100_volume, 3),
                            "actual_plant_volume_DC": round(plnt_100_volumeDC, 3),
                            "category": availability_plnt_arrayyy["category"],
                            "plant_name": availability_plnt_arrayyy["plant_name"],
                            "key": availability_plnt_arrayyy["key"],
                            # # "revision_no_SG": availability_plnt_arrayyy["revision_no"],
                            # # "revision_no_DC": getattr(reverseDCData[plnamekey], "revision_no", 0),
                            # "display_category": get_plant_list[availability_plnt_arrayyy["key"]]["scheduling_req_category"],
                            # "dispatch_type": availability_plnt_arrayyy["dispatch_type"],
                            "total_RMP_volume": float(total_diff),
                            "scheduling_requisition_type": "NA",
                        }
                       
                        # return insertData
                        self.scheduling_requisition_repo.create(insertData)
                        
                        plkey += 1
                        
                blk22 += 1
            # Ensure nested dict exists
        
        reviseDataTotalsupply.setdefault(blk + 1, {})
        
        reviseDataTotalsupply[blk + 1]["total_supply_updated"] = genratedTotalsupply
        reviseDataTotalsupply[blk + 1]["surplus_deficit_updated"] = (
            genratedTotalsupply
            - TotalblkwiseData.get(blk + 1, {}).get("forecast_value", 0)
        )



        return reviseDataTotalsupply
        

    async def get_all_plant_data_for_scheduling(self,schedule_date, dc_sg):
        

        # scheduledatetimestamp = yesterday midnight (unix timestamp)
        yesterday = datetime.now() - timedelta(days=1)

        # Format as YYYY-MM-DD
        yesterday_date = yesterday.strftime("%Y-%m-%d")

        yesterday_date

        # check current time
        now_time = datetime.now().time()
        cutoff_time = datetime.strptime("22:30", "%H:%M").time()

        if now_time > cutoff_time:
            metch = {
                "schedule_date":{"$gte": yesterday_date},
                "dc_sg": dc_sg
            }
        else:
            metch = {
                "schedule_date":{"$gte": yesterday_date},
                "dc_sg": dc_sg
            }

        

        pipeline = [
            {"$match": metch},
            {
                "$group": {
                    "_id": {
                        "schedule_date": "$schedule_date",
                        "time_block": "$time_block"
                    },
                    "result": {
                        "$push": {
                            "plant_name": "$plant_name",
                            "key": "$plant_id",
                            "volume": "$volume",
                            "schedule_date": "$schedule_date",
                            "category": "$category",
                            "VC_Price": "$VC_Price",
                        }
                    },
                    "time_block": {"$first": "$time_block"},
                    
                    "dc_sg": {"$first": "$dc_sg"},
                    "schedule_date": {"$first": "$schedule_date"},
                    "counter": {"$first": "0.0"},
                    "totalAvailability": {"$sum": "$volume"}
                }
            },
            {
                "$project": {
                    "result": 1,
                    "time_block": 1,
                    "dc_sg": 1,
                    "schedule_date": 1,
                    "totalAvailability": 1
                }
            },
            {"$sort": {"schedule_date": 1.0, "time_block": 1.0}}
        ]

        posted_jobs = await self.supply_repo.repository.aggregate(self.supply_repo.supply_serve, pipeline)
        
        return posted_jobs


    def get_last_record_of_sg(self, plants_data, no_of_block=0):
        """
        Get the last record of SG plants for a given number of blocks.
        
        Args:
            plants_data (list): List of plant data per time block.
            no_of_block (int): Number of blocks to consider.

        Returns:
            dict: Dictionary containing last counted block data.
        """
        getplants_data = plants_data
        count_block_volume = {}

        for nobl in range(no_of_block + 1):
            block_index = self.BlkNoForRunSchedule - nobl - 1  # ✅ adjust for 0-based index
            # print(block_index)
            # Safety check: avoid going negative
            if block_index < 0:
                continue

            if block_index >= len(getplants_data):
                continue  # ✅ avoid list index out of range

            for allplants in getplants_data[block_index].get('result', []):
                key = allplants['key']
                volume = allplants['volume']
                
                if key not in count_block_volume:
                    count_block_volume[key] = {}
                
                count_block_volume[key][block_index] = volume

        return {"lastcountedBlockData": count_block_volume}

    
    def get_session_from():
        time_block = [
            "00:00", "00:30", "01:00", "01:30", "02:00", "02:30", "03:00", "03:30",
            "04:00", "04:30", "05:00", "05:30", "06:00", "06:30", "07:00", "07:30",
            "08:00", "08:30", "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
            "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00", "15:30",
            "16:00", "16:30", "17:00", "17:30", "18:00", "18:30", "19:00", "19:30",
            "20:00", "20:30", "21:00", "21:30", "22:00", "22:30", "23:00", "23:30"
        ]
        return time_block

    def total_availability(self, getplantsData, getForecastedValue):
        """
        Calculate total availability, surplus/deficit, and forecasted values per block.
        """
        data_ar = []
        # return getplantsData
        for i, block_data in enumerate(getplantsData):
            bltotalvolume = block_data.get("totalAvailability", 0)

            if i < len(getForecastedValue):
                forecast_block = getForecastedValue[i]
                demand_forecasted_value = (
                    forecast_block.get("forecasted_frequency", 0)
                    + forecast_block.get("comp_act_demand", 0)
                )
            else:
                demand_forecasted_value = 0  # default when forecast missing

            data_block = {
                "full_time": block_data.get("_id", {}).get("schedule_date"),
                "BlockNo": block_data.get("_id", {}).get("time_block"),
                "dc_sg": block_data.get("dc_sg"),
                "forecast_value": demand_forecasted_value,
                "surplus_deficit": bltotalvolume - demand_forecasted_value,
                "totalAvailability": bltotalvolume,
                "plants": block_data.get("result", []),
            }

            data_ar.append(data_block)


        return data_ar
    
    def check_forecast_price_apply_for_surplus(self, rtmforecastedpriceblk, px_price, S_D_type):
        """
        Check if the forecast price condition applies for surplus/deficit.

        Args:
            rtmforecastedpriceblk (float): RTM forecasted price for the block.
            px_price (float): PX price of the plant.
            S_D_type (str): "surplus" or "deficit".

        Returns:
            bool: True if condition is met, False otherwise.
        """
        if S_D_type == "surplus":
            # if get_parameter_value("Scheduling_ForecastPrice_checkbox"):
                return rtmforecastedpriceblk < px_price
            # else:
            #     return True
        else:  # deficit
            # if get_parameter_value("Scheduling_ForecastPrice_checkbox"):
                return rtmforecastedpriceblk > px_price
            # else:
            #     return True

# Global instance
realtimemarket_service = RealtimemarketService()
