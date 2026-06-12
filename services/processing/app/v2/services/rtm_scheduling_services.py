import logging
from datetime import datetime, timedelta, date
from typing import Dict, Any, List, Optional
from fastapi import HTTPException
from math import isnan, isfinite

from ..repository.scheduling_requissition_calculation_repository import SchedulingRequisitionCalculationRepository
from ..repository.supply_serve_repository import SupplyServeRepository
from ..repository.rtm_product_hardcoded_data_repository import RTMProductHardCodedDataRepository
from ..repository.master_plants_repository import MasterPlantsRepository
from ..repository.limit_values_repository import LimitValuesRepository
from ..repository.demand_serve_repository import DemandServeRepository
from ..repository.exchange_repository import ExchangeRepository
from ..helpers.rtm_scheduling_helpers import get_time_block_start_end


logger = logging.getLogger(__name__)


def round_numeric_value(value):
    """
    Round numeric values: 2 decimals if decimal, otherwise integer.
    """
    if value is None:
        return 0
    try:
        val = float(value)
        # Check if value is effectively an integer (no significant decimal part)
        if abs(val - round(val)) < 0.001:
            return int(round(val))
        else:
            return round(val, 2)
    except (ValueError, TypeError):
        return 0


class RTMSchedulingServices:
    def __init__(self, collection_name: str = "scheduling_requisition_calculation_table"):
        # Initialize all repositories (v2 pattern - using new serve tables)
        self.supply_repo = SupplyServeRepository()
        self.rtm_product_hardCoded_data_repo = RTMProductHardCodedDataRepository()
        self.plants_list_repo = MasterPlantsRepository()
        self.limit_values_repo = LimitValuesRepository()
        self.serve_demand_repo = DemandServeRepository()
        self.serve_exchange_repo = ExchangeRepository()
        self.scheduling_repo = SchedulingRequisitionCalculationRepository(collection_name=collection_name)
        
        # Calculate block ranges based on current time
        session_times = RTMSchedulingServices.get_session_from()
        current_time = datetime.now().strftime("%H:%M")
        
        # Count the number of sessions that have passed
        passed_sessions_count = sum(
            1 for t in session_times if datetime.strptime(t, "%H:%M") < datetime.strptime(current_time, "%H:%M")
        )
        
        # Calculate today's current block
        today_current_block = (2 * passed_sessions_count) - 1 + 95
        logger.debug(f"session_times {current_time}")
        
        # Set class attributes
        self.currentBlk = today_current_block
        self.shiftingBlockNo = 2
        self.shiftingBlockNoRuningTo = 5
        self.shiftingBlockNoREQFrom = 5
        self.shiftingBlockNoREQTo = 18
        
        self.BlkNoForRunSchedule = today_current_block + self.shiftingBlockNo
        self.BlkNoToRunSchedule = today_current_block + self.shiftingBlockNoRuningTo
        self.BlkNoFromRunReq = today_current_block + self.shiftingBlockNoREQFrom
        self.BlkNoToRunReq = today_current_block + self.shiftingBlockNoREQTo
        
        # Initialize custom demand
        self.customdemand = 0
        

    async def getAllPlantDataForScheduling(self) -> List[Dict[str, Any]]:
        """
        Get all plant data for RTM scheduling.
        Returns data in the exact same format as v1 API for compatibility.
        Multiplies px_price by 1000, but does NOT round other numeric fields.
        
        Returns:
            List[Dict[str, Any]]: List of scheduling records with v1 field names
        """
        try:
            logger.info("[RTMSchedulingServices] Calling scheduling_repo.getRTMAllPlantDataForScheduling()")
            data = await self.scheduling_repo.getRTMAllPlantDataForScheduling()
            logger.info(f"[RTMSchedulingServices] Retrieved {len(data) if isinstance(data, list) else 0} records from repository")
            
            # Processor function
            def process_fields(record):
                """
                Recursively traverse fields to handle px_price adjustment.
                """
                if isinstance(record, dict):
                    processed = {}
                    for key, value in record.items():
                        # --- SPECIAL CASE: Handle px_price ---
                        if key == "px_price" and isinstance(value, (int, float)):
                            # Multiply by 1000
                            processed[key] = value * 1000
                            
                        # --- RECURSION: Handle nested dicts ---
                        elif isinstance(value, dict):
                            processed[key] = process_fields(value)
                            
                        # --- RECURSION: Handle lists (generic or 'result') ---
                        elif isinstance(value, list):
                            processed[key] = [process_fields(item) for item in value]
                            
                        # --- GENERIC: Return value as-is (No Rounding) ---
                        else:
                            processed[key] = value
                    return processed
                
                elif isinstance(record, list):
                    return [process_fields(item) for item in record]
                    
                else:
                    return record
            
            # Apply the processing
            if isinstance(data, list):
                result = [process_fields(item) for item in data if item is not None]
                return result
            elif isinstance(data, dict):
                return [process_fields(data)]
            else:
                logger.warning("[RTMSchedulingServices] No data to return")
                return []
        except Exception as e:
            logger.exception(f"[RTMSchedulingServices] Error in getAllPlantDataForScheduling: {str(e)}")
            return []
            
    async def rtm_scheduling_table(self, schedule_date, dc_sg="DC_SG"):
        """
        Fetch DSM (Deviation Settlement Mechanism) data for a given schedule date.
        """
        
        try:
            if(schedule_date):
                schedule_date=schedule_date
            else:
                # Temporary: Hardcoded schedule date for fetching DSM records
                schedule_date= '2025-08-04'
            
            # Convert date string to datetime for scheduled_at matching
            target_date = datetime.strptime(schedule_date, "%Y-%m-%d")
            end_date = target_date + timedelta(days=1)
            
            pipeline = [
                # Match on supply_serve with scheduled_at
                {
                    "$match": {
                        "scheduled_at": {"$gte": target_date, "$lt": end_date},
                        "dc_sg": "SG"
                    }
                },
                # Lookup plant_master for plant_name, c_s, TM, multiplier, PX_price
                {
                    "$lookup": {
                        "from": "plant_master",
                        "localField": "plant_id",
                        "foreignField": "plant_id",
                        "as": "plant_details"
                    }
                },
                {"$unwind": {"path": "$plant_details", "preserveNullAndEmptyArrays": True}},
                # Group by plant
                {
                    "$group": {
                        "_id": {
                            "plant": "$plant_details.plant_name",
                            "plant_name": "$plant_details.plant_name",
                            "plant_id": "$plant_id"
                        },
                        "PX_price": {
                            "$first": {
                                "$cond": {
                                    "if": {"$and": [{"$ne": ["$px_price", None]}, {"$gt": ["$px_price", 0]}]},
                                    "then": "$px_price",
                                    "else": {
                                        "$cond": {
                                            "if": {"$and": [{"$ne": ["$plant_details.px_price", None]}, {"$gt": ["$plant_details.px_price", 0]}]},
                                            "then": "$plant_details.px_price",
                                            "else": {"$ifNull": ["$plant_details.vc_price", 0]}
                                        }
                                    }
                                }
                            }
                        }, 
                        "c_s": {"$first": "$plant_details.c_s"},
                        "TM": {"$first": "$rtm_tm"}, 
                        "multiplier": {"$first": "$multiplier"}, 
                        "schedule_date": {"$first": schedule_date}, 
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
                            {  
                                "$match": { 
                                    "$expr": { 
                                        "$or": [
                                            { "$eq": ["$from_period_code", "$$tb"] },
                                            { "$eq": ["$From_Period_Code", "$$tb"] }
                                        ]
                                    } 
                                }  
                            },
                            {  
                                "$project": {  
                                    "_id": 0,  
                                    "session_number": {"$ifNull": ["$session_number", "$Session_Id"]}
                                }  
                            }
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
                                            { "$eq": ["$market", "RTM"] }, 
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
                            "schedule_date": {
                                "$dateToString": {
                                    "format": "%Y-%m-%d",
                                    "date": "$scheduled_at"
                                }
                            },
                            "time_block": "$time_block"
                        },
                        "result": {
                            "$push": {
                                "plant_name": "$plant_details.plant_name",  # From plant_master
                                "key": "$plant_id",
                                "volume": "$volume",
                                "schedule_date": {
                                    "$dateToString": {
                                        "format": "%Y-%m-%d",
                                        "date": "$scheduled_at"
                                    }
                                },
                                "category": "$plant_details.category",  # From plant_master
                                "c_s": "$plant_details.c_s",  # From plant_master (for display_category)
                                "dispatch_type": "$plant_details.dispatch_type",  # From plant_master
                                "VC_Price": "$vc_price",  # Map vc_price to VC_Price
                                "ceiling_price": {"$ifNull": ["$ceiling_data.ceiling_price", 0]},  # Ceiling price from limit_values
                                "rtm_tm": {"$ifNull": ["$rtm_tm", 0]},
                                "rtm_tm_revision_no": {"$ifNull": ["$rtm_tm_revision_no", None]},
                                "rtm_max": {"$ifNull": ["$rtm_max", None]},
                                "rtm_max_revision_no": {"$ifNull": ["$rtm_max_revision_no", None]},
                                # Calculated fields from scheduling_requisition_calculation_table
                                "remaining_volume": {"$ifNull": ["$calc_data.remaining_volume", 0]},
                                "price": {"$ifNull": ["$calc_data.price", 0]},
                                "RMP": {"$ifNull": ["$calc_data.plant_RMP_value", 0]},
                                "plant_TM_value": {"$ifNull": ["$calc_data.plant_TM_value", 0]},
                                "Final_plant_RMP_volume": {"$ifNull": ["$calc_data.Final_plant_RMP_volume", 0]},
                                "actual_plant_volume": {"$ifNull": ["$calc_data.actual_plant_volume", 0]},
                                "genrated_sg_volume": {"$ifNull": ["$calc_data.genrated_sg_volume", 0]},
                                "surplus_deficit_updated": {"$ifNull": ["$calc_data.surplus_deficit_updated", 0]},
                                "scheduling_requisition_type": {"$ifNull": ["$calc_data.scheduling_requisition_type", ""]}
                            }
                        },
                        "time_block": {"$first": "$time_block"},
                        "dc_sg": {"$first": "$dc_sg"},
                        "schedule_date": {
                            "$first": {
                                "$dateToString": {
                                    "format": "%Y-%m-%d",
                                    "date": "$scheduled_at"
                                }
                            }
                        },
                        "counter": {"$first": "0.0"},
                        "totalAvailability": {"$sum": "$volume"}
                    }
                },
                # Lookup block-level aggregated fields from scheduling_requisition_calculation_table
                {
                    "$lookup": {
                        "from": "scheduling_requisition_calculation_table",
                        "let": {
                            "time_block_var": "$time_block",
                            "sched_date": "$schedule_date"
                        },
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": ["$time_block", "$$time_block_var"]},
                                            {
                                                "$eq": [
                                                    {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                                                    "$$sched_date"
                                                ]
                                            }
                                        ]
                                    }
                                }
                            },
                            # Filter by dc_sg_type (handle DC_SG as DC or SG)
                            *([{"$match": {"dc_sg_type": {"$in": ["DC", "SG"]}}}] if dc_sg == "DC_SG" else [{"$match": {"dc_sg_type": dc_sg}}]),
                            {
                                "$group": {
                                    "_id": "$time_block",
                                    "total_availablity_updated": {"$first": "$updated_total_supply_mw"},
                                    "surplus_deficit_updated": {"$first": "$updated_surplus_deficit_mw"},
                                    "rtm_forecasted_price": {"$first": "$rtm_forecasted_price"}
                                }
                            }
                        ],
                        "as": "block_calc_data"
                    }
                },
                {"$unwind": {"path": "$block_calc_data", "preserveNullAndEmptyArrays": True}},
                # Lookup RTM forecasted price from limit_values
                {
                    "$lookup": {
                        "from": "limit_values",
                        "let": {
                            "tb": "$time_block"
                        },
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": ["$ID", "RTM_forecasted_price"]},
                                            {"$eq": ["$market", "RTM"]},
                                            {"$eq": ["$block", "$$tb"]}
                                        ]
                                    }
                                }
                            },
                            {"$sort": {"schedule_date": -1}},
                            {"$limit": 1},
                            {"$project": {"_id": 0, "rtm_forecasted_price": "$values"}}
                        ],
                        "as": "rtm_price_data"
                    }
                },
                {"$unwind": {"path": "$rtm_price_data", "preserveNullAndEmptyArrays": True}},
                {
                    "$project": {
                        "result": 1,
                        "time_block": 1,
                        "dc_sg": 1,
                        "schedule_date": 1,
                        "totalAvailability": 1,
                        "total_availablity_updated": {"$ifNull": ["$block_calc_data.total_availablity_updated", 0]},
                        "surplus_deficit_updated": {"$ifNull": ["$block_calc_data.surplus_deficit_updated", 0]},
                        "rtm_forecasted_price": {"$ifNull": ["$rtm_price_data.rtm_forecasted_price", {"$ifNull": ["$block_calc_data.rtm_forecasted_price", 0]}]}
                    }
                },
                {"$sort": {"schedule_date": 1.0, "time_block": 1.0}}
            ]

            # Add timeout to prevent hanging (180 seconds = 3 minutes)
            try:
                posted_jobs = await self.supply_repo.aggregate("supply_serve", pipeline)
            except Exception as e:
                # Error handling remains unchanged
                posted_jobs = []
            
            # ... (Post-processing and final return remain unchanged) ...
        
            return {"result": posted_jobs}
        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise


    async def calSchedulingTableData(self, schedule_date, audit_logger=None, user_context: Optional[Dict[str, Any]] = None):
        try:
            getsessionNoRaw = await self.rtm_product_hardCoded_data_repo.get_Data()
            
            # Transform session data to dictionary keyed by from_period_code (time_block)
            getsessionNo = {}
            for session_item in getsessionNoRaw:
                time_block = session_item.get("from_period_code") or session_item.get("From_Period_Code")
                session_number = session_item.get("session_number") or session_item.get("Session_Id")
                if time_block is not None:
                    getsessionNo[time_block] = {
                        "session_number": session_number,
                        "Session_Id": session_number 
                    }
            
            # Fetch all documents from plants_list
            logger.info("[RTMSchedulingServices] Fetching all plant data from master plants repository")
            getPlantListRaw = await self.plants_list_repo.get_all_plants()
            logger.info(f"[RTMSchedulingServices] Retrieved {len(getPlantListRaw)} plant records")
            
            # Transform plant list - ensure each plant has a "key" field set to plant_id
            getPlantList = []
            getPlantListDict = {}
            for plant in getPlantListRaw:
                plant_id = plant.get("plant_id")
                if plant_id:
                    plant["key"] = plant_id
                    getPlantList.append(plant)
                    getPlantListDict[plant_id] = plant
            
            mustplantPrice = "20"

            yesterday = date.today() - timedelta(days=1)
            tomorrow  = date.today() + timedelta(days=1)
            
            # Convert to strings (dates are ignored anyway - uses latest date logic)
            yesterday_str = yesterday.strftime("%Y-%m-%d")
            tomorrow_str = tomorrow.strftime("%Y-%m-%d")
            
            logger.info("[RTMSchedulingServices] Fetching limit values - TM, SEZ, MLTP")
            allTMValuesRaw = await self.limit_values_repo.get_data(yesterday_str, tomorrow_str, "RTM", "TM", "NA")
            logger.info(f"[RTMSchedulingServices] Retrieved {len(allTMValuesRaw)} TM limit values")
            allSEZValuesRaw = await self.limit_values_repo.get_data(yesterday_str, tomorrow_str, "DAM", "SEZ", "OB")
            logger.info(f"[RTMSchedulingServices] Retrieved {len(allSEZValuesRaw)} SEZ limit values")
            allMLTPValuesRaw = await self.limit_values_repo.get_data(yesterday_str, tomorrow_str, "RTM", "Multiplier", "OB")
            logger.info(f"[RTMSchedulingServices] Retrieved {len(allMLTPValuesRaw)} MLTP limit values")
            
            # Build dictionaries with composite keys (Helper function assumed to be defined elsewhere)
            def build_limit_value_dict(limit_values_list, include_time_block=True):
                result = {}
                for item in limit_values_list:
                    schedule_date = item.get("schedule_date", "")
                    limit_id = item.get("ID", "")
                    time_block = item.get("block", "")
                    value = item.get("values", item.get("value", 0))
                    
                    if include_time_block:
                        key = f"{schedule_date}_{limit_id}_{time_block}"
                    else:
                        key = f"{schedule_date}_{limit_id}"
                    result[key] = value
                return result
            
            # Build dictionaries with composite keys
            allTMValues = build_limit_value_dict(allTMValuesRaw, include_time_block=True)
            allSEZValues = build_limit_value_dict(allSEZValuesRaw, include_time_block=True)
            allMLTPValues = build_limit_value_dict(allMLTPValuesRaw, include_time_block=False)
            logger.info(f"[RTMSchedulingServices] Converted limit values to dictionaries - TM: {len(allTMValues)}, SEZ: {len(allSEZValues)}, MLTP: {len(allMLTPValues)}")
            
            
            # Apply 22:30 PM cutoff logic
            now = datetime.now()
            now_time = now.time()
            cutoff_time = datetime.strptime("22:30", "%H:%M").time()
            is_after_cutoff = now_time > cutoff_time
            
            logger.info(f"[RTMSchedulingServices] 22:30 cutoff check: current_time={now_time}, cutoff_time={cutoff_time}, is_after_cutoff={is_after_cutoff}")
            
            logger.info(f"[RTMSchedulingServices] Fetching plant data for SG (Schedule Date: {schedule_date})")
            getplantsData = await self.get_all_plant_data_for_scheduling(schedule_date, "SG")
            
            # Filter out Central plants from SG data if any slipped through (User Request: RTM should not combine SG and DC)
            # Assuming 'c_s' field distinguishes Central vs State
            if getplantsData:
                filtered_sg_data = []
                for block_data in getplantsData:
                    if "result" in block_data:
                        # Filter the result list
                        filtered_result = [
                            p for p in block_data["result"] 
                            if p.get("c_s", "").lower() != "central"
                        ]
                        # Update totalAvailability for the block based on filtered result
                        new_total_availability = sum(float(p.get("volume", 0)) for p in filtered_result)
                        
                        # Create new block data with filtered result
                        new_block_data = block_data.copy()
                        new_block_data["result"] = filtered_result
                        new_block_data["totalAvailability"] = new_total_availability
                        filtered_sg_data.append(new_block_data)
                    else:
                        filtered_sg_data.append(block_data)
                getplantsData = filtered_sg_data
                
            logger.info(f"[RTMSchedulingServices] Retrieved {len(getplantsData)} SG plant data records (after filtering Central plants)")
            
            # Only fetch DC data if before 22:30 (matches PHP behavior)
            if is_after_cutoff:
                logger.info(f"[RTMSchedulingServices] After 22:30 cutoff - Skipping DC data fetch (only SG data as per PHP logic)")
                getplantsDataDC = []
            else:
                logger.info(f"[RTMSchedulingServices] Before 22:30 cutoff - Fetching DC data (Schedule Date: {schedule_date})")
                getplantsDataDC = await self.get_all_plant_data_for_scheduling(schedule_date, "DC")
                logger.info(f"[RTMSchedulingServices] Retrieved {len(getplantsDataDC)} DC plant data records")
            
            # Extract schedule_date for deletion
            schedule_date_for_deletion = getplantsData[0].get("schedule_date") if getplantsData else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            
            # Delete all existing records (Logic remains the same)
            
            # Fetch data from MongoDB for Demand Forecast
            yesterday_dt = datetime.now() - timedelta(days=1)
            demand_schedule_date = yesterday_dt.strftime("%Y-%m-%d")
            get_forecasted_value = await self.serve_demand_repo.get_demand(demand_schedule_date)
            
            # Call total_availability and limit fetching (Logic remains the same)
            totalAvailability = self.total_availability(getplantsData, get_forecasted_value)
            rtm_forecasted_price_list = await self.limit_values_repo.get_data_byID_with_latest_date("RTM_forecasted_price", "RTM", "", "")
            rtm_forecasted_price = {}
            for item in rtm_forecasted_price_list:
                block = item.get("block")
                if block is not None:
                    rtm_forecasted_price[int(block)] = item
            
            blk_no_for_run_schedule = 1
            blk_no_to_run_schedule = 96
            
            logger.info(f"[RTMSchedulingServices] Starting SchedulingCalculator - Block range: {blk_no_for_run_schedule} to {blk_no_to_run_schedule}")
            reviseDataTotalsupply = await self.SchedulingCalculator(
                total_availability=totalAvailability,
                rtm_forecasted_price=rtm_forecasted_price,
                get_plants_data=getplantsData,
                get_plants_data_dc=getplantsDataDC,
                all_tm_values=allTMValues,
                all_sez_values=allSEZValues,
                all_mltp_values=allMLTPValues,
                get_plant_list=getPlantListDict,
                get_session_no=getsessionNo,
                is_after_cutoff=is_after_cutoff
            )
            logger.info(f"[RTMSchedulingServices] SchedulingCalculator completed - Generated scheduling records")
            logger.info(f"[RTMSchedulingServices] Completed calSchedulingTableData successfully - Schedule Date: {schedule_date}")
            return reviseDataTotalsupply
        except Exception as e:
            # Error handling remains unchanged
            raise HTTPException(
                status_code=500,
                detail=f"Failed to calculate RTM scheduling table data: {str(e)}"
            )
    
    # LINE 681: START OF MODIFIED SCHEDULING CALCULATOR METHOD
    async def SchedulingCalculator(self,
            total_availability,
            rtm_forecasted_price,
            get_plants_data,
            get_plants_data_dc,
            all_tm_values,
            all_sez_values,
            all_mltp_values,
            get_plant_list,
            get_session_no,
            is_after_cutoff=False):
        
        reviseDataArray = {}
        reviseDataTotalsupply = {}

        # Helper function to get plant RMP value (Used for reference, not constraint)
        def _get_plant_rmp_value(plant_data, plant_list_dict):
            plant_key = plant_data.get("key")
            if plant_key and plant_key in plant_list_dict:
                return float(plant_list_dict[plant_key].get("ramping", 0))
            return 0.0

        # Helper function to get DC data by key (since lists are sorted)
        def get_dc_volume_by_key(current_blk_idx, p_key):
            if current_blk_idx < len(get_plants_data_dc):
                dc_result = get_plants_data_dc[current_blk_idx].get("result", [])
                if isinstance(dc_result, list):
                    for item in dc_result:
                        item_key = item.get("key") if isinstance(item, dict) else getattr(item, "key", None)
                        if item_key == p_key:
                            return float(item.get("volume", 0))
            return 0.0

        # Helper function to get next block SG volume by key
        def get_next_block_volume_by_key(current_blk_idx, p_key):
            next_blk_idx = current_blk_idx + 1
            if next_blk_idx < len(get_plants_data):
                next_res = get_plants_data[next_blk_idx].get("result", [])
                if isinstance(next_res, list):
                    for item in next_res:
                        item_key = item.get("key") if isinstance(item, dict) else getattr(item, "key", None)
                        if item_key == p_key:
                            return float(item.get("volume", 0))
            return 0.0

        # --- MAIN LOOP START ---
        for blk, TotalblkwiseData in enumerate(total_availability):
            
            total_diff = TotalblkwiseData.get("surplus_deficit", 0.0) 
            
            surplus_deficit_updatedfornextBlock = total_diff
            TotalSupplyfornextBlock = TotalblkwiseData.get("totalAvailability", 0.0)
            genratedTotalsupply = 0.0
            
            availability_data_array_b_wise = dict(get_plants_data[blk])
            
            # Fetch RTM forecasted price for current block (used for price check)
            rtmforecastedpriceblk = float(rtm_forecasted_price.get(int(TotalblkwiseData.get("BlockNo", 0)), {}).get("values", 0.0))
            
            
            if self.BlkNoForRunSchedule <= blk <= self.BlkNoToRunSchedule:
                
                # Get initial plant data to process
                plant_results = availability_data_array_b_wise.get("result", [])
                
                # Sort plants descending by price (most expensive first) for curtailment preference
                plants_to_process = sorted(
                    plant_results,
                    key=lambda p: float(p.get("PX_Price", 0)),
                    reverse=True 
                )

                remaining_system_surplus = total_diff
                
                # --- 55%/45% CAPACITY ALLOCATION LOGIC (ONLY APPLIED IN SURPLUS) ---
                if total_diff > 0:
                    
                    for plnamekey, availability_plnt_arrayyyObj in enumerate(plants_to_process):
                        availability_plnt_arrayyy = dict(availability_plnt_arrayyyObj)
                        plant_key = availability_plnt_arrayyy.get("key")
                        plant_volume = float(availability_plnt_arrayyy.get("volume", 0))
                        
                        dispatch_type = get_plant_list.get(plant_key, {}).get("dispatch_type", "")
                        
                        # Fetch initial SG volume (used as current capacity to adjust)
                        plnt_initial_schedule = float(reviseDataArray.get(blk, {}).get("genrated_sg_volume", {}).get(plant_key, plant_volume))

                        # Fetch relevant limits (for database storage/reference)
                        plantRMPvalue = self._get_plant_rmp_value(availability_plnt_arrayyy, get_plant_list)
                        planttmvalue = get_plant_list.get(plant_key, {}).get("tm", 0)
                        
                        sell_volume_adjustment = 0.0
                        
                        if dispatch_type == "Merit":
                            
                            # 1. Calculate the Volume Pool available for adjustment/sale (45% pool)
                            volume_45_percent_pool = plnt_initial_schedule * 0.45 
                            
                            # 2. Determine sell_volume_adjustment: Min(Remaining Surplus, 45% Pool)
                            sell_volume_adjustment = min(
                                remaining_system_surplus,
                                volume_45_percent_pool
                            )
                            
                            # 3. Final Scheduled Generation = Initial Capacity - Adjustment
                            final_schedule = plnt_initial_schedule - sell_volume_adjustment
                            
                            # 4. Update Tracking
                            remaining_system_surplus -= sell_volume_adjustment
                            
                        else:
                            # Must-run plants (and others) maintain declared capacity (no adjustment)
                            final_schedule = plnt_initial_schedule 
                            sell_volume_adjustment = 0.0

                        # Accumulate total generation for the block
                        genratedTotalsupply += final_schedule
                        
                        # Update next block SG (propagate the capacity change)
                        nextblkSG = self.get_next_block_volume_by_key(blk, plant_key, get_plants_data)
                        propagate_schedule = min(final_schedule, nextblkSG) if nextblkSG > 0 else final_schedule
                        reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[plant_key] = propagate_schedule

                        # Fetch DC volume and Revision for insert
                        plnt_100_volumeDC = self.get_dc_volume_by_key(blk, plant_key)
                        revision_no_DC = float(get_plants_data_dc[blk].get("result", [{}])[plnamekey].get("revision_no", 0)) # Crude lookup due to list sort issues

                        # Prepare Insert Data (Mapping to standard fields)
                        base_insert_data = {
                            "schedule_date": availability_data_array_b_wise.get("schedule_date"),
                            "time_block": availability_data_array_b_wise.get("time_block"),
                            "price": availability_plnt_arrayyy.get("PX_Price", 0),
                            
                            # --- CALCULATED OUTPUTS (New Logic) ---
                            "up_down_RMP_volume": round(sell_volume_adjustment, 3), 
                            "Final_plant_RMP_volume": round(final_schedule, 3), 
                            "genrated_sg_volume": round(final_schedule, 3),      
                            
                            # --- System Totals ---
                            "surplus_deficit": TotalblkwiseData.get("surplus_deficit", 0), 
                            "surplus_deficit_updated": round(remaining_system_surplus, 3), 
                            "total_availablity_updated": TotalSupplyfornextBlock,
                            
                            # --- Reference/Fixed Fields ---
                            "actual_plant_volume": round(plant_volume, 3),            
                            "plant_RMP_value": plantRMPvalue,               
                            "plant_TM_value": planttmvalue,
                            "actual_plant_volume_DC": plnt_100_volumeDC,
                            "rtm_forecasted_price": rtmforecastedpriceblk,
                            
                            "key": plant_key,
                            "plant_name": availability_plnt_arrayyy.get("plant_name"),
                            "category": availability_plnt_arrayyy.get("category"),
                            "dispatch_type": dispatch_type,
                            "scheduling_requisition_type": "Scheduling" if sell_volume_adjustment > 0 else "NA",
                            "scheduling_req_type": "Scheduling" if sell_volume_adjustment > 0 else "NA",
                            "revision_no_SG": availability_plnt_arrayyy.get("revision_no", 0),
                            "revision_no_DC": revision_no_DC,
                        }
                        
                        insertData = self.create_standardized_record(base_insert_data)
                        await self.scheduling_repo.create(insertData)
                        
                    # After iterating all plants: Update system totals for the NEXT block
                    time_block_num = TotalblkwiseData.get("BlockNo") or TotalblkwiseData.get("time_block")
                    if time_block_num is not None:
                        time_block_num = int(time_block_num)
                        reviseDataTotalsupply.setdefault(time_block_num, {})
                        
                        reviseDataTotalsupply[time_block_num]["total_supply_updated"] = genratedTotalsupply
                        reviseDataTotalsupply[time_block_num]["surplus_deficit_updated"] = remaining_system_surplus

                # --- DEFICIT LOGIC & NON-RUN BLOCKS (NA / Pass-through Logic) ---
                else:
                    # Logic remains simple NA/Pass-through for Deficit or blocks outside run range
                    time_block_num = TotalblkwiseData.get("BlockNo") or TotalblkwiseData.get("time_block")
                    if time_block_num is not None:
                        time_block_num = int(time_block_num)
                        reviseDataTotalsupply.setdefault(time_block_num, {})
                        
                        reviseDataTotalsupply[time_block_num]["total_supply_updated"] = TotalSupplyfornextBlock
                        reviseDataTotalsupply[time_block_num]["surplus_deficit_updated"] = surplus_deficit_updatedfornextBlock

            else:
                # --- BLOCKS OUTSIDE THE RUN SCHEDULE RANGE ---
                time_block_num = TotalblkwiseData.get("BlockNo") or TotalblkwiseData.get("time_block")
                if time_block_num is not None:
                    time_block_num = int(time_block_num)
                    reviseDataTotalsupply.setdefault(time_block_num, {})
                    
                    reviseDataTotalsupply[time_block_num]["total_supply_updated"] = TotalSupplyfornextBlock
                    reviseDataTotalsupply[time_block_num]["surplus_deficit_updated"] = surplus_deficit_updatedfornextBlock

        return reviseDataTotalsupply

    async def get_all_plant_data_for_scheduling(self, schedule_date, dc_sg):
        """
        Get all plant data for scheduling filtered by DC/SG type.
        Uses supply_serve collection with proper field mappings.
        
        Note: Matches V1 behavior - always uses yesterday's date regardless of schedule_date parameter.
        V1's implementation completely ignores the schedule_date parameter.
        
        Args:
            schedule_date (str): Schedule date parameter (ignored - always uses yesterday to match V1 behavior)
            dc_sg (str): "DC" or "SG" to filter plant data
            
        Returns:
            list: List of plant data grouped by time block
        """
        # V1 behavior: Always use yesterday regardless of schedule_date parameter
        # This matches V1's get_all_plant_data_for_scheduling which ignores schedule_date
        yesterday = datetime.now() - timedelta(days=1)
        schedule_date_used = yesterday.strftime("%Y-%m-%d")
        
        logger.info(f"[RTMSchedulingServices] get_all_plant_data_for_scheduling: Using date {schedule_date_used} (yesterday) for schedule_date parameter {schedule_date}, dc_sg={dc_sg}")
        
        # Convert to datetime for scheduled_at matching
        target_date = datetime.strptime(schedule_date_used, "%Y-%m-%d")
        end_date = target_date + timedelta(days=1)

        # Match on scheduled_at for supply_serve (always uses yesterday to match V1 behavior)
        # Handle "DC_SG" as "DC or SG" - the database stores them separately
        if dc_sg == "DC_SG":
            # Query for both DC and SG records
            metch = {
                "dc_sg": {"$in": ["DC", "SG"]},
                "scheduled_at": {"$gte": target_date, "$lt": end_date}
            }
        else:
            # Use the exact dc_sg value
            metch = {
                "scheduled_at": {"$gte": target_date, "$lt": end_date},
                "dc_sg": dc_sg
            }
        
        logger.debug(f"[RTMSchedulingServices] MongoDB match query: {metch}")

        pipeline = [
            # Match on supply_serve with scheduled_at
            {"$match": metch},
            # Lookup plant_master for plant_name and category
            {
                "$lookup": {
                    "from": "plant_master",
                    "localField": "plant_id",
                    "foreignField": "plant_id",
                    "as": "plant_details"
                }
            },
            {"$unwind": {"path": "$plant_details", "preserveNullAndEmptyArrays": True}},
            # Compute effective_volume: For DC records, use max_mw_dc if available; for SG records, use volume
            {
                "$addFields": {
                    "effective_volume": {
                        "$cond": {
                            "if": {"$eq": ["$dc_sg", "DC"]},
                            "then": {"$ifNull": ["$eightThirtyValue", "$volume"]},
                            "else": "$volume"
                        }
                    }
                }
            },
            # Lookup ceiling_price from limit_values
            {
                "$lookup": {
                    "from": "limit_values",
                    "let": {
                        "sched": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$scheduled_at"
                            }
                        },
                        "tb": "$time_block"
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$ID", "Ceiling_Price"]},
                                        {"$eq": ["$area", "MP"]},
                                        {"$eq": ["$category", "Limits"]},
                                        {"$eq": ["$schedule_date", "$$sched"]},
                                        {"$eq": ["$block", "$$tb"]}
                                    ]
                                }
                            }
                        },
                        {"$project": {"_id": 0, "ceiling_price": "$values"}}
                    ],
                    "as": "ceiling_data"
                }
            },
            {"$unwind": {"path": "$ceiling_data", "preserveNullAndEmptyArrays": True}},
            # Lookup calculated fields from scheduling_requisition_calculation_table
            {
                "$lookup": {
                    "from": "scheduling_requisition_calculation_table",
                    "let": {
                        "plant_id_var": "$plant_id",
                        "time_block_var": "$time_block",
                        "sched_at": "$scheduled_at"
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$plant_id", "$$plant_id_var"]},
                                        {"$eq": ["$time_block", "$$time_block_var"]},
                                        {
                                            "$eq": [
                                                {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                                                {"$dateToString": {"format": "%Y-%m-%d", "date": "$$sched_at"}}
                                            ]
                                        }
                                    ]
                                }
                            }
                        },
                        # Filter by dc_sg_type (handle DC_SG as DC or SG)
                        *([{"$match": {"dc_sg_type": {"$in": ["DC", "SG"]}}}] if dc_sg == "DC_SG" else [{"$match": {"dc_sg_type": dc_sg}}]),
                        {"$limit": 1},
                        {
                            "$project": {
                                "_id": 0,
                                # Map from standardized field names to old field names
                                "remaining_volume": "$remaining_volume_mw",
                                "price": "$price",
                                "plant_RMP_value": "$plant_rmp_value",
                                "plant_TM_value": "$plant_tm_value",
                                "Final_plant_RMP_volume": "$final_rmp_volume_mw",
                                "actual_plant_volume": "$actual_plant_volume_mw",
                                "genrated_sg_volume": "$generated_sg_volume_mw",
                                "surplus_deficit_updated": "$updated_surplus_deficit_mw",
                                "scheduling_requisition_type": "$scheduling_req_type"
                            }
                        }
                    ],
                    "as": "calc_data"
                }
            },
            {"$unwind": {"path": "$calc_data", "preserveNullAndEmptyArrays": True}},
            {
                "$group": {
                    "_id": {
                        "schedule_date": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$scheduled_at"
                            }
                        },
                        "time_block": "$time_block"
                    },
                    "result": {
                        "$push": {
                            "plant_name": "$plant_details.plant_name",  # From plant_master
                            "key": "$plant_id",
                            "volume": "$effective_volume",
                            "schedule_date": {
                                "$dateToString": {
                                    "format": "%Y-%m-%d",
                                    "date": "$scheduled_at"
                                }
                            },
                            "category": "$plant_details.category",  # From plant_master
                            "c_s": "$plant_details.c_s",  # From plant_master (for display_category)
                            "dispatch_type": "$plant_details.dispatch_type",  # From plant_master
                            "VC_Price": "$vc_price",  # Map vc_price to VC_Price
                            "ceiling_price": {"$ifNull": ["$ceiling_data.ceiling_price", 0]},  # Ceiling price from limit_values
                            # Calculated fields from scheduling_requisition_calculation_table
                            "remaining_volume": {"$ifNull": ["$calc_data.remaining_volume", 0]},
                            "price": {"$ifNull": ["$calc_data.price", 0]},
                            "RMP": {"$ifNull": ["$calc_data.plant_RMP_value", 0]},
                            "plant_TM_value": {"$ifNull": ["$calc_data.plant_TM_value", 0]},
                            "Final_plant_RMP_volume": {"$ifNull": ["$calc_data.Final_plant_RMP_volume", 0]},
                            "actual_plant_volume": {"$ifNull": ["$calc_data.actual_plant_volume", 0]},
                            "genrated_sg_volume": {"$ifNull": ["$calc_data.genrated_sg_volume", 0]},
                            "surplus_deficit_updated": {"$ifNull": ["$calc_data.surplus_deficit_updated", 0]},
                            "scheduling_requisition_type": {"$ifNull": ["$calc_data.scheduling_requisition_type", ""]}
                        }
                    },
                    "time_block": {"$first": "$time_block"},
                    "dc_sg": {"$first": "$dc_sg"},
                    "schedule_date": {
                        "$first": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$scheduled_at"
                            }
                        }
                    },
                    "counter": {"$first": "0.0"},
                    "totalAvailability": {"$sum": "$effective_volume"}
                }
            },
            # Lookup block-level aggregated fields from scheduling_requisition_calculation_table
            {
                "$lookup": {
                    "from": "scheduling_requisition_calculation_table",
                    "let": {
                        "time_block_var": "$time_block",
                        "sched_date": "$schedule_date"
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$time_block", "$$time_block_var"]},
                                        {
                                            "$eq": [
                                                {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                                                "$$sched_date"
                                            ]
                                        }
                                    ]
                                }
                            }
                        },
                        # Filter by dc_sg_type (handle DC_SG as DC or SG)
                        *([{"$match": {"dc_sg_type": {"$in": ["DC", "SG"]}}}] if dc_sg == "DC_SG" else [{"$match": {"dc_sg_type": dc_sg}}]),
                        {
                            "$group": {
                                "_id": "$time_block",
                                "total_availablity_updated": {"$first": "$updated_total_supply_mw"},
                                "surplus_deficit_updated": {"$first": "$updated_surplus_deficit_mw"},
                                "rtm_forecasted_price": {"$first": "$rtm_forecasted_price"}
                            }
                        }
                    ],
                    "as": "block_calc_data"
                }
            },
            {"$unwind": {"path": "$block_calc_data", "preserveNullAndEmptyArrays": True}},
            # Lookup RTM forecasted price from limit_values
            {
                "$lookup": {
                    "from": "limit_values",
                    "let": {
                        "tb": "$time_block"
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$ID", "RTM_forecasted_price"]},
                                        {"$eq": ["$market", "RTM"]},
                                        {"$eq": ["$block", "$$tb"]}
                                    ]
                                }
                            }
                        },
                        {"$sort": {"schedule_date": -1}},
                        {"$limit": 1},
                        {"$project": {"_id": 0, "rtm_forecasted_price": "$values"}}
                    ],
                    "as": "rtm_price_data"
                }
            },
            {"$unwind": {"path": "$rtm_price_data", "preserveNullAndEmptyArrays": True}},
            {
                "$project": {
                    "result": 1,
                    "time_block": 1,
                    "dc_sg": 1,
                    "schedule_date": 1,
                    "totalAvailability": 1,
                    "total_availablity_updated": {"$ifNull": ["$block_calc_data.total_availablity_updated", 0]},
                    "surplus_deficit_updated": {"$ifNull": ["$block_calc_data.surplus_deficit_updated", 0]},
                    "rtm_forecasted_price": {"$ifNull": ["$rtm_price_data.rtm_forecasted_price", {"$ifNull": ["$block_calc_data.rtm_forecasted_price", 0]}]}
                }
            },
            {"$sort": {"schedule_date": 1.0, "time_block": 1.0}}
        ]

        # Add timeout to prevent hanging (180 seconds = 3 minutes)
        try:
            posted_jobs = await self.supply_repo.aggregate(self.supply_repo.collection_name, pipeline)
        except Exception as e:
            if "MaxTimeMSExpired" in str(e) or "time limit" in str(e).lower():
                logger.error(f"[RTMSchedulingServices] get_all_plant_data_for_scheduling: Aggregation timed out for date {schedule_date_used}, dc_sg={dc_sg}. "
                            f"This may indicate missing indexes or a very large dataset.")
                posted_jobs = []
            else:
                logger.exception(f"[RTMSchedulingServices] get_all_plant_data_for_scheduling: Error during aggregation: {str(e)}")
                raise
        
        logger.info(f"[RTMSchedulingServices] get_all_plant_data_for_scheduling: Aggregation returned {len(posted_jobs)} documents for date {schedule_date_used}, dc_sg={dc_sg}")
        if len(posted_jobs) == 0:
            logger.warning(f"[RTMSchedulingServices] No data found in supply_serve for date {schedule_date_used} with dc_sg={dc_sg}. This might indicate:")
            logger.warning("  1. No data exists for this date")
            logger.warning("  2. Date format mismatch (check if scheduled_at uses different format)")
            logger.warning("  3. dc_sg filter is too restrictive")
            # Construct dc_sg_value outside f-string to avoid backslash issue
            dc_sg_value = dc_sg if dc_sg != 'DC_SG' else '{"$in": ["DC", "SG"]}'
            logger.warning(f"  4. Check if data exists with query: {{'scheduled_at': {{'$gte': {target_date}, '$lt': {end_date}}}, 'dc_sg': {dc_sg_value}}}")
        
        # Add missing fields to match original structure (start_time, end_time, full_time, dc_sg_type)
        
        for block_data in posted_jobs:
            time_block = block_data.get('time_block')
            if time_block is not None:
                try:
                    time_block_int = int(time_block)
                    time_info = get_time_block_start_end(time_block_int)
                    block_data['start_time'] = time_info['start_time']
                    block_data['end_time'] = time_info['end_time']
                    block_data['full_time'] = time_info['full_time']
                except (ValueError, TypeError):
                    block_data['start_time'] = ''
                    block_data['end_time'] = ''
                    block_data['full_time'] = ''
            else:
                block_data['start_time'] = ''
                block_data['end_time'] = ''
                block_data['full_time'] = ''
            
            # Map dc_sg to dc_sg_type to match original
            if 'dc_sg' in block_data:
                block_data['dc_sg_type'] = block_data['dc_sg']
        
        return posted_jobs

    
    def total_availability(self, getplantsData, getForecastedValue):
        """
        Calculates total availability and surplus/deficit for each block
        
        Args:
            getplantsData: Plant data dictionary keyed by block (or list that will be converted)
            getForecastedValue: Forecast data list
        
        Returns:
            List of block-wise availability data
        """
        try:
            finalarray = []
            
            # Convert list to dict if needed (to match original structure)
            if isinstance(getplantsData, list):
                # Convert list to dict keyed by block number
                getplantsDataDict = {}
                for block_data in getplantsData:
                    time_block = block_data.get('time_block')
                    if time_block is not None:
                        try:
                            blk_key = int(time_block)
                            getplantsDataDict[blk_key] = block_data
                        except (ValueError, TypeError):
                            continue
                getplantsData = getplantsDataDict
            
            # Process each block
            for i, getplantsDataaa in enumerate(getplantsData.values()):
                # Get forecast value
                forecast_value = getForecastedValue[i].get('Demand_actual_vs_forecast', 0) + self.customdemand
                
                getplantsDataaa = dict(getplantsDataaa) if not isinstance(getplantsDataaa, dict) else getplantsDataaa
                
                # Get total availability
                bltotalvolume = getplantsDataaa.get('totalAvailability', 0)
                
                # Build data array
                data_ar = {
                    'full_time': getplantsDataaa.get('full_time', ''),
                    'BlockNo': getplantsDataaa.get('time_block', 0),
                    'dc_sg_type': getplantsDataaa.get('dc_sg_type', ''),
                    'forecast_value': forecast_value,
                    'surplus_deficit': bltotalvolume - forecast_value,
                    'totalAvailability': bltotalvolume
                }
                
                finalarray.append(data_ar)
            
            return finalarray
            
        except Exception as e:
            logger.error(f"Error in total_availability: {str(e)}")
            return []
    

    def get_last_record_of_sg(self, getplantsDataLastSGRecordes, noofblock=0):
        """
        Gets last N blocks of SG data for each plant
        
        Args:
            getplantsDataLastSGRecordes: Plant data dictionary keyed by block number (or list that will be converted)
            noofblock: Number of blocks to look back
        
        Returns:
            Dict with lastcountedBlockData
        """
        try:
            countblockVolume = {}
            
            # Convert list to dict if needed (to match original structure)
            if isinstance(getplantsDataLastSGRecordes, list):
                # Convert list to dict keyed by block number
                getplantsDataDict = {}
                for i, block_data in enumerate(getplantsDataLastSGRecordes):
                    time_block = block_data.get('time_block')
                    if time_block is not None:
                        try:
                            blk_key = int(time_block)
                            getplantsDataDict[blk_key] = block_data
                        except (ValueError, TypeError):
                            continue
                getplantsDataLastSGRecordes = getplantsDataDict
            
            # Loop through blocks
            for nobl in range(noofblock + 1):
                block_num = self.BlkNoForRunSchedule - nobl
                
                # Get plant data for this block
                if block_num in getplantsDataLastSGRecordes:
                    block_data = getplantsDataLastSGRecordes[block_num]
                    result = block_data.get('result', [])
                    
                    # Store volume for each plant
                    for plant in result:
                        plant_key = plant.get('key')
                        if plant_key:
                            if plant_key not in countblockVolume:
                                countblockVolume[plant_key] = {}
                            countblockVolume[plant_key][block_num] = plant.get('volume', 0)
            
            return {
                "lastcountedBlockData": countblockVolume
            }
            
        except Exception as e:
            logger.error(f"Error in get_last_record_of_sg: {str(e)}")
            return {"lastcountedBlockData": {}}

      
    def check_forecast_price_apply_for_surplus(self, rtmforecastedpriceblk: float, 
                                               px_price: float, s_d_type: str) -> bool:
        """
        Checks if forecast price condition is met for surplus/deficit
        
        Args:
            rtmforecastedpriceblk: RTM forecasted price for block
            px_price: Plant price
            s_d_type: "surplus" or "deficit"
        
        Returns:
            bool: True if condition is met, False otherwise
        """
        try:
            from ..helpers.rtm_scheduling_helpers import get_parameter_value
            
            # Get parameter value
            # Note: Need to pass a repo that has get_parameter_value method
            # For now, using a simplified version
            # The original has this commented out, so we'll use the direct comparison
            forecast_price_checkbox = get_parameter_value(
                "Scheduling_ForecastPrice_checkbox", 
                self.scheduling_repo  # Pass repo for parameter lookup
            )
            
            # Convert to boolean if needed
            if isinstance(forecast_price_checkbox, str):
                forecast_price_checkbox = forecast_price_checkbox.lower() in ('true', '1', 'yes')
            elif isinstance(forecast_price_checkbox, (int, float)):
                forecast_price_checkbox = bool(forecast_price_checkbox)
            else:
                forecast_price_checkbox = False
            
            if s_d_type == "surplus":
                if forecast_price_checkbox:
                    # For surplus: RTM price < plant price
                    return rtmforecastedpriceblk < px_price
                else:
                    return True
            else:  # deficit
                if forecast_price_checkbox:
                    # For deficit: RTM price > plant price
                    return rtmforecastedpriceblk > px_price
                else:
                    return True
                    
        except Exception as e:
            logger.error(f"Error in check_forecast_price_apply_for_surplus: {str(e)}")
            # Fallback to direct comparison if parameter check fails
            if s_d_type == "surplus":
                return rtmforecastedpriceblk < px_price
            else:
                return rtmforecastedpriceblk > px_price
      
    @staticmethod
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

    async def get_revision_no(self, schedule_date: str) -> Dict[str, Any]:
        """
        Gets latest revision numbers for ISP and SLDC categories
        
        Args:
            schedule_date: Schedule date in YYYY-MM-DD format
        
        Returns:
            Dict with revision numbers: srldc_revision_DC, srldc_revision_SG, sldc_revision_DC, sldc_revision_SG
        """
        try:
            from ..helpers.rtm_scheduling_helpers import format_date_for_display
            
            # Query SR-LDC revision (ISP category) from scheduling_requisition_calculation_table
            collection = self.scheduling_repo.db[self.scheduling_repo.collection_name]
            
            getsrldcrevision = await collection.find_one(
                {
                    "schedule_date": schedule_date,
                    "category": "ISP"
                },
                sort=[("revision_no_SG", -1)]
            )
            if getsrldcrevision:
                getsrldcrevision = {k: v for k, v in getsrldcrevision.items() if k in ["revision_no_DC", "revision_no_SG"]}
            
            # Query SLDC revision from supply_serve using dc_sg field
            # Convert schedule_date to datetime for scheduled_at field
            schedule_date_dt = datetime.strptime(schedule_date, "%Y-%m-%d")
            schedule_date_start = datetime.combine(schedule_date_dt.date(), datetime.min.time())
            schedule_date_end = schedule_date_start + timedelta(days=1)
            
            # Get max revision number for DC from supply_serve (prioritize source_revision_number, then system_revision_number)
            supply_collection = self.supply_repo.db[self.supply_repo.collection_name]
            
            # For DC: Sort by source_revision_number first, then system_revision_number
            sldc_revision_dc_doc = await supply_collection.find_one(
                {
                    "scheduled_at": {
                        "$gte": schedule_date_start,
                        "$lt": schedule_date_end
                    },
                    "dc_sg": "DC"
                },
                sort=[("source_revision_number", -1), ("system_revision_number", -1)]
            )
            
            # For SG: Sort by source_revision_number first, then system_revision_number
            sldc_revision_sg_doc = await supply_collection.find_one(
                {
                    "scheduled_at": {
                        "$gte": schedule_date_start,
                        "$lt": schedule_date_end
                    },
                    "dc_sg": "SG"
                },
                sort=[("source_revision_number", -1), ("system_revision_number", -1)]
            )
            
            # Extract revision numbers (first priority: source_revision_number, secondary: system_revision_number)
            sldc_revision_DC = 0
            if sldc_revision_dc_doc:
                source_rev = sldc_revision_dc_doc.get('source_revision_number')
                system_rev = sldc_revision_dc_doc.get('system_revision_number')
                # Use source_revision_number first if available and not None/0, otherwise use system_revision_number
                sldc_revision_DC = source_rev if source_rev is not None and source_rev != 0 else (system_rev if system_rev is not None else 0)
            
            sldc_revision_SG = 0
            if sldc_revision_sg_doc:
                source_rev = sldc_revision_sg_doc.get('source_revision_number')
                system_rev = sldc_revision_sg_doc.get('system_revision_number')
                # Use source_revision_number first if available and not None/0, otherwise use system_revision_number
                sldc_revision_SG = source_rev if source_rev is not None and source_rev != 0 else (system_rev if system_rev is not None else 0)
            
            # Build response with only the 4 required fields
            getrevision = {
                'srldc_revision_DC': getsrldcrevision.get('revision_no_DC', 0) if getsrldcrevision else 0,
                'srldc_revision_SG': getsrldcrevision.get('revision_no_SG', 0) if getsrldcrevision else 0,
                'sldc_revision_DC': sldc_revision_DC,
                'sldc_revision_SG': sldc_revision_SG
            }
            
            logger.info(f"[RTMSchedulingServices] get_revision_no: schedule_date={schedule_date}, "
                       f"srldc_revision_DC={getrevision['srldc_revision_DC']}, "
                       f"srldc_revision_SG={getrevision['srldc_revision_SG']}, "
                       f"sldc_revision_DC={getrevision['sldc_revision_DC']}, "
                       f"sldc_revision_SG={getrevision['sldc_revision_SG']}")
            
            return getrevision
            
        except Exception as e:
            logger.error(f"Error in get_revision_no: {str(e)}")
            logger.exception(f"Exception details: {type(e).__name__}: {str(e)}")
            return {
                'srldc_revision_DC': 0,
                'srldc_revision_SG': 0,
                'sldc_revision_DC': 0,
                'sldc_revision_SG': 0
            }

    async def get_scheduling_graph_data(self, schedule_date: str, from_block: Optional[int] = None,
                                        to_block: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Provides aggregated data for graph visualization
        
        UPDATED: Now uses supply_serve + demand_serve instead of availability_PG
        
        Args:
            schedule_date: Schedule date in YYYY-MM-DD format
            from_block: Optional starting time block (ignored to match original - original doesn't have this)
            to_block: Optional ending time block (ignored to match original - original doesn't have this)
        
        Returns:
            List of time block data for graphs
        """
        try:
            from ..helpers.rtm_scheduling_helpers import get_time_new, sum_of_data_filter
            
            # Format schedule date
            schedule_date = datetime.strptime(schedule_date, "%Y-%m-%d").strftime("%Y-%m-%d")
            
            # Calculate time block range
            from_time_block = self.currentBlk - 95
            to_time_block = self.BlkNoToRunReq - 96
            
            # Get filterData from supply_serve (replaces availability_PG.filterData)
            # Filter by category
            filter_data_list = await self.supply_repo.get_filter_data_by_category(
                schedule_date=schedule_date,
                filter_type="category",
                dc_sg_type=None,  # Get all DC_SG types
                from_block=from_time_block,
                to_block=to_time_block
            )
            
            # Convert filter_data_list to dict keyed by timeBlock for easier lookup
            filter_data_dict = {}
            for item in filter_data_list:
                time_block = item.get('timeBlock')
                if time_block is not None:
                    filter_data_dict[int(time_block)] = item
            
            # Get demand forecast data from demand_serve (replaces availability_PG.Demand_actual_vs_forecast)
            demand_data = await self.serve_demand_repo.get_demand(schedule_date)
            
            # Convert demand_data to dict keyed by time_block for easier lookup
            demand_dict = {}
            for item in demand_data:
                time_block = item.get('time_block')
                if time_block is not None:
                    demand_dict[int(time_block)] = item
            
            finalarray = []
            time_blocks = get_time_new()
            
            # Process each time block in the range
            for time_block_num in range(from_time_block, to_time_block + 1):
                # Get filterData for this time block
                filter_data_item = filter_data_dict.get(time_block_num)
                if not filter_data_item:
                    # Skip if no data for this time block
                    continue
                
                filter_data = filter_data_item.get('filterData', [])
                
                # Extract totalvolcat values from filterData objects
                # filterData structure: [{"category": "ISP", "totalvolcat": 1000.0}, ...]
                # We need to extract the numeric values (totalvolcat) for summing
                filter_data_values = []
                if isinstance(filter_data, list):
                    for item in filter_data:
                        if isinstance(item, dict):
                            # Extract totalvolcat value from each object
                            totalvolcat = item.get('totalvolcat', 0)
                            if totalvolcat is not None:
                                try:
                                    filter_data_values.append(float(totalvolcat))
                                except (ValueError, TypeError):
                                    continue
                        elif isinstance(item, (int, float)):
                            # If it's already a number, use it directly
                            filter_data_values.append(float(item))
                elif isinstance(filter_data, (int, float)):
                    # If filterData is a single number
                    filter_data_values.append(float(filter_data))
                
                # Calculate total availability
                total_availability = sum_of_data_filter(filter_data_values)
                
                # Get demand forecast for this time block
                demand_item = demand_dict.get(time_block_num, {})
                demand_actual_vs_forecast = demand_item.get('Demand_actual_vs_forecast', 0)
                
                # Calculate demand forecast
                demand_forecast = demand_actual_vs_forecast + self.customdemand
                
                # Get time strings
                array_key = time_block_num
                start_time = time_blocks[array_key - 1] if array_key > 0 else "00:00"
                end_time = time_blocks[array_key] if array_key < len(time_blocks) else "24:00"
                full_time = f"{start_time}-{end_time}"
                
                # Build response
                finalarray.append({
                    "timeBlock": time_block_num,
                    "start_time": start_time,
                    "end_time": end_time,
                    "full_time": full_time,
                    "total_avialability": round_numeric_value(total_availability),
                    "demand_forecast": round_numeric_value(demand_forecast),
                    "surplus_deficit": round_numeric_value(total_availability - demand_forecast),
                    "block_bid_submitted_volume": 0,
                    "block_bid_approved_volume": 0,
                    "block_bid_cleared_volume": 0,
                    "single_bid_submitted_volume": 0,
                    "single_bid_approved_volume": 0,
                    "single_bid_cleared_volume": 0
                })
            
            logger.info(f"Generated scheduling graph data for {len(finalarray)} time blocks (schedule_date={schedule_date})")
            return finalarray
            
        except Exception as e:
            logger.error(f"Error in get_scheduling_graph_data: {str(e)}")
            return []

    async def get_realtime_scheduling_tb_data(self, schedule_date: str, from_block: Optional[int] = None, to_block: Optional[int] = None) -> Dict[str, Any]:
        """
        Retrieves real-time scheduling data for current and upcoming blocks
        
        Args:
            schedule_date: Schedule date in YYYY-MM-DD format
            from_block: Optional starting time block (1-96). If not provided, uses calculated current block
            to_block: Optional ending time block (1-96). If not provided, uses calculated end block
        
        Returns:
            Dict with table_data and Block_data
        """
        try:
            from ..helpers.rtm_scheduling_helpers import format_date_for_display
            
            # Get session numbers
            # For v2, we use the repository directly
            try:
                if hasattr(self.rtm_product_hardCoded_data_repo, 'get_Data'):
                    getsessionNoRaw = await self.rtm_product_hardCoded_data_repo.get_Data()
                elif hasattr(self.rtm_product_hardCoded_data_repo, 'get_rtm_product_hardcoded_data'):
                    getsessionNoRaw = await self.rtm_product_hardCoded_data_repo.get_rtm_product_hardcoded_data()
                else:
                    getsessionNoRaw = []
            except Exception as e:
                logger.warning(f"Could not get session numbers: {str(e)}")
                logger.exception(f"Exception details: {type(e).__name__}: {str(e)}")
                getsessionNoRaw = []
            
            logger.info(f"[RTMSchedulingServices] Retrieved {len(getsessionNoRaw) if isinstance(getsessionNoRaw, (list, dict)) else 0} session number records")
            
            # Convert to dict format
            getsessionNo = {}
            if isinstance(getsessionNoRaw, dict):
                # Already a dict keyed by time_block
                getsessionNo = getsessionNoRaw
            elif isinstance(getsessionNoRaw, list):
                # Convert list to dict
                for session_item in getsessionNoRaw:
                    time_block = session_item.get("from_period_code") or session_item.get("From_Period_Code")
                    session_number = session_item.get("session_number") or session_item.get("Session_Id")
                    if time_block is not None:
                        try:
                            # Convert time_block to int for dict key
                            time_block_int = int(time_block)
                            getsessionNo[time_block_int] = {
                                "session_number": session_number,
                                "Session_Id": session_number
                            }
                        except (ValueError, TypeError):
                            logger.warning(f"Invalid time_block in session data: {time_block}")
                            continue
            else:
                getsessionNo = {}
            
            logger.info(f"[RTMSchedulingServices] Converted session numbers to dict with {len(getsessionNo)} time blocks")
            if len(getsessionNo) > 0:
                sample_keys = list(getsessionNo.keys())[:5]
                logger.debug(f"[RTMSchedulingServices] Sample session number keys: {sample_keys}")
                for key in sample_keys:
                    logger.debug(f"[RTMSchedulingServices] Session data for block {key}: {getsessionNo[key]}")
            else:
                logger.warning(f"[RTMSchedulingServices] No session numbers found! This might indicate:")
                logger.warning(f"  1. rtm_product_hardcoded_data collection is empty")
                logger.warning(f"  2. Collection name mismatch")
                logger.warning(f"  3. Field names don't match (checking for from_period_code/From_Period_Code)")
            
            # Get plant data - returns a list, convert to dict keyed by block
            # Note: get_all_plant_data_for_scheduling always uses yesterday's date (matches V1 behavior)
            now = datetime.now()
            now_time = now.time()
            cutoff_time = datetime.strptime("22:30", "%H:%M").time()
            is_after_cutoff = now_time > cutoff_time

            # 2. Determine target Type: "SG" if after 22:30, otherwise "DC"
            # This prevents fetching duplicate blocks and overwriting data randomly
            target_dc_sg = "SG" if is_after_cutoff else "DC"

            logger.info(f"[RTMSchedulingServices] get_realtime_scheduling_tb_data: Current time {now_time}, Fetching {target_dc_sg} data")

            # 3. Fetch specific data type
            getplantsDataList = await self.get_all_plant_data_for_scheduling(schedule_date, target_dc_sg)
            logger.info(f"[RTMSchedulingServices] get_realtime_scheduling_tb_data: Retrieved {len(getplantsDataList)} plant data records for schedule_date={schedule_date}")
            
            if len(getplantsDataList) == 0:
                logger.warning(f"[RTMSchedulingServices] No plant data found for schedule_date={schedule_date}. This might indicate:")
                logger.warning(f"  1. No data exists in supply_serve for yesterday's date")
                logger.warning(f"  2. Data exists but doesn't match the query criteria")
                logger.warning(f"  3. Check if scheduling has been run for the date")
            
            # Get demand forecast data
            # V1 behavior: Always use yesterday for demand forecast to match plant data
            yesterday_dt = datetime.now() - timedelta(days=1)
            demand_schedule_date = yesterday_dt.strftime("%Y-%m-%d")
            demand_data = await self.serve_demand_repo.get_demand(demand_schedule_date)
            
            # Convert demand_data to dict keyed by time_block for easier lookup
            demand_dict = {}
            for item in demand_data:
                time_block = item.get('time_block')
                if time_block is not None:
                    try:
                        demand_dict[int(time_block)] = item
                    except (ValueError, TypeError):
                        continue
            
            logger.info(f"[RTMSchedulingServices] Retrieved {len(demand_data)} demand forecast records for date {demand_schedule_date}")
            
            # Convert list to dict keyed by time_block
            # Also merge demand forecast data into each block
            getplantsData = {}
            for block_data in getplantsDataList:
                time_block = block_data.get('time_block')
                if time_block is not None:
                    try:
                        blk_key = int(time_block)
                        # Merge demand forecast data into block_data
                        demand_item = demand_dict.get(blk_key, {})
                        demand_actual_vs_forecast = demand_item.get('Demand_actual_vs_forecast', 0)
                        
                        # Add demand forecast and calculate surplus_deficit
                        block_data['demand_forecast'] = demand_actual_vs_forecast + self.customdemand
                        block_data['surplus_deficit'] = block_data.get('totalAvailability', 0) - (demand_actual_vs_forecast + self.customdemand)
                        
                        getplantsData[blk_key] = block_data
                    except (ValueError, TypeError):
                        continue
            
            logger.info(f"[RTMSchedulingServices] Converted to dict with {len(getplantsData)} time blocks. Block range in data: {min(getplantsData.keys()) if getplantsData else 'N/A'} to {max(getplantsData.keys()) if getplantsData else 'N/A'}")
            
            # Use provided from_block/to_block if available, otherwise default to all blocks (1-96)
            # This ensures data is returned when parameters are not provided
            if from_block is not None and to_block is not None:
                effective_from_block = from_block
                effective_to_block = to_block
                logger.info(f"[RTMSchedulingServices] Using provided block range: {effective_from_block} to {effective_to_block}")
            elif from_block is not None:
                effective_from_block = from_block
                effective_to_block = to_block if to_block is not None else 96
                logger.info(f"[RTMSchedulingServices] Using provided from_block={effective_from_block}, defaulting to_block={effective_to_block}")
            elif to_block is not None:
                effective_from_block = from_block if from_block is not None else 1
                effective_to_block = to_block
                logger.info(f"[RTMSchedulingServices] Using provided to_block={effective_to_block}, defaulting from_block={effective_from_block}")
            else:
                # Both are None - default to all blocks (1-96) to ensure data is returned
                effective_from_block = 1
                effective_to_block = 96
                logger.info(f"[RTMSchedulingServices] No block parameters provided, defaulting to all blocks: {effective_from_block} to {effective_to_block}")
            
            # Ensure block range is valid (time_block values are 1-96)
            effective_from_block = max(1, min(96, effective_from_block))
            effective_to_block = max(1, min(96, effective_to_block))
            
            # If from_block > to_block, swap them or use all blocks
            if effective_from_block > effective_to_block:
                logger.warning(f"[RTMSchedulingServices] from_block ({effective_from_block}) > to_block ({effective_to_block}), using all blocks (1-96)")
                effective_from_block = 1
                effective_to_block = 96
            
            # from_block and to_block are time_block values - filter directly by time_block
            # No need for complex mapping - just filter the data by time_block range
            time_block_start = effective_from_block
            time_block_end = effective_to_block
            
            logger.info(f"[RTMSchedulingServices] Filtering by time_block range: {time_block_start} to {time_block_end}")
            
            blk22 = 0
            data = {
                'table_data': {},
                'Block_data': {}
            }
            
            blocks_matched = 0
            blocks_filtered_out = 0
            
            # Process each block
            for blk, availability_data_array_b_wise in getplantsData.items():
                # Filter by time_block range directly (from_block and to_block are time_block values)
                if blk >= time_block_start and blk <= time_block_end:
                    blocks_matched += 1
                    availability_data_array_b_wise = availability_data_array_b_wise
                    
                    remaningvolumearray = []
                    updatedsurpluse = []
                    
                    # Process each plant
                    for plkey, availability_plnt_arrayyy_single in enumerate(
                        availability_data_array_b_wise.get('result', [])
                    ):
                        availability_plnt_arrayyy = availability_plnt_arrayyy_single
                        
                        # Build table_data
                        schedule_date_obj = datetime.strptime(
                            availability_data_array_b_wise['schedule_date'], 
                            "%Y-%m-%d"
                        )
                        
                        data['table_data'][plkey] = data['table_data'].get(plkey, {})
                        data['table_data'][plkey][blk22] = {
                            'Date': format_date_for_display(availability_data_array_b_wise['schedule_date']),
                            'start_time': availability_data_array_b_wise['start_time'],
                            'end_time': availability_data_array_b_wise['end_time'],
                            'full_time': availability_data_array_b_wise['full_time'],
                            'time_block': availability_data_array_b_wise['time_block'],
                            'dc_sg_type': availability_data_array_b_wise['dc_sg_type'],
                            'session_number': (lambda tb: getsessionNo.get(tb, {}).get('Session_Id') or getsessionNo.get(tb, {}).get('session_number', ''))(
                                int(availability_data_array_b_wise.get('time_block', 0))
                            ),
                            'forecast': round_numeric_value(availability_data_array_b_wise['demand_forecast']),
                            'supply_availablity': round_numeric_value(availability_data_array_b_wise['totalAvailability']),
                            'surplus_deficit': round_numeric_value(availability_data_array_b_wise['surplus_deficit']),
                            'surplus_deficit_updated': round_numeric_value(availability_plnt_arrayyy.get('surplus_deficit_updated', availability_data_array_b_wise.get('surplus_deficit_updated', 0))),
                            'price': round_numeric_value(availability_plnt_arrayyy.get('price', 0)),  # From calculation table
                            'remaining_volume': round_numeric_value(availability_plnt_arrayyy.get('remaining_volume', 0)),  # From calculation table
                            'plant_RMP_value': round_numeric_value(availability_plnt_arrayyy.get('RMP', 0)),  # From calculation table
                            'plant_TM_value': round_numeric_value(availability_plnt_arrayyy.get('plant_TM_value', 0)),  # From calculation table
                            'sell_volume': round_numeric_value(availability_plnt_arrayyy.get('Final_plant_RMP_volume', 0)),  # From calculation table
                            'genrated_sg': round_numeric_value(availability_plnt_arrayyy.get('genrated_sg_volume', 0)),  # From calculation table
                            'actual_volume': round_numeric_value(availability_plnt_arrayyy.get('actual_plant_volume', 0)),  # From calculation table
                            'category': availability_plnt_arrayyy.get('category', ''),
                            'plant_name': availability_plnt_arrayyy.get('plant_name', ''),
                            'display_category': availability_plnt_arrayyy.get('c_s', availability_plnt_arrayyy.get('display_category', '')),  # Use c_s from plant_master
                            'dispatch_type': availability_plnt_arrayyy.get('dispatch_type', ''),  # From plant_master
                            'total_sell_volume': round_numeric_value(availability_plnt_arrayyy.get('remaining_volume', 0)),
                            'scheduling_requisition_type': availability_plnt_arrayyy.get('scheduling_requisition_type', '')
                        }
                        
                        # Append remaining_volume and surplus_deficit_updated from each plant
                        remaining_vol = float(availability_plnt_arrayyy.get('remaining_volume', 0))
                        surplus_def_updated = float(availability_plnt_arrayyy.get('surplus_deficit_updated', 0))
                        remaningvolumearray.append(remaining_vol)
                        updatedsurpluse.append(surplus_def_updated)
                    
                    # Log array sizes for debugging
                    logger.debug(f"[RTMSchedulingServices] Block {blk22}: remaningvolumearray has {len(remaningvolumearray)} values, updatedsurpluse has {len(updatedsurpluse)} values")
                    if len(remaningvolumearray) > 0:
                        logger.debug(f"[RTMSchedulingServices] Block {blk22}: remaining_volume range: min={min(remaningvolumearray)}, max={max(remaningvolumearray)}")
                    if len(updatedsurpluse) > 0:
                        logger.debug(f"[RTMSchedulingServices] Block {blk22}: surplus_deficit_updated range: min={min(updatedsurpluse)}, max={max(updatedsurpluse)}")
                    
                    # Build Block_data
                    data['Block_data'][blk22] = {
                        'Date': format_date_for_display(availability_data_array_b_wise['schedule_date']),
                        'start_time': availability_data_array_b_wise['start_time'],
                        'end_time': availability_data_array_b_wise['end_time'],
                        'full_time': availability_data_array_b_wise['full_time'],
                        'time_block': availability_data_array_b_wise['time_block'],
                        'dc_sg_type': availability_data_array_b_wise['dc_sg_type'],
                        'session_number': getsessionNo.get(
                            availability_data_array_b_wise['time_block'], 
                            {}
                        ).get('Session_Id', ''),
                        'forecast': round_numeric_value(availability_data_array_b_wise['demand_forecast']),
                        'supply_availablity': round_numeric_value(availability_data_array_b_wise['totalAvailability']),
                        'surplus_deficit': round_numeric_value(availability_data_array_b_wise['surplus_deficit']),
                        'total_availablity_updated': round_numeric_value(availability_data_array_b_wise.get('total_availablity_updated', 0)),
                        'rtm_forecasted_price': round_numeric_value(availability_data_array_b_wise.get('rtm_forecasted_price', 0))
                    }
                    
                    # Calculate remaining_volume and surplus_deficit_updated
                    # surplus_deficit_updated: Use stored value from database (matches  behavior)
                    # If not available, calculate as: total_availablity_updated - forecast
                    # remaining_volume uses min/max of plant-level values
                    
                    # Get surplus_deficit_updated from stored value (matches  - uses value from calculation table)
                    stored_surplus_deficit_updated = availability_data_array_b_wise.get('surplus_deficit_updated', 0)
                    if stored_surplus_deficit_updated == 0:
                        # Fallback: calculate from block-level values if stored value is 0 or missing
                        total_avail_updated = availability_data_array_b_wise.get('total_availablity_updated', 0)
                        demand_forecast = availability_data_array_b_wise.get('demand_forecast', 0)
                        stored_surplus_deficit_updated = total_avail_updated - demand_forecast
                    data['Block_data'][blk22]['surplus_deficit_updated'] = round_numeric_value(stored_surplus_deficit_updated)
                    
                    # Calculate remaining_volume using min/max of plant-level values
                    if len(remaningvolumearray) > 0:
                        # Use the stored surplus_deficit_updated to determine min/max
                        if stored_surplus_deficit_updated > 0:
                            data['Block_data'][blk22]['remaining_volume'] = round_numeric_value(min(remaningvolumearray))
                        elif stored_surplus_deficit_updated < 0:
                            data['Block_data'][blk22]['remaining_volume'] = round_numeric_value(max(remaningvolumearray))
                        else:
                            # If surplus_deficit_updated is 0, use min as default
                            data['Block_data'][blk22]['remaining_volume'] = round_numeric_value(min(remaningvolumearray))
                    else:
                        # If arrays are empty, set to 0
                        data['Block_data'][blk22]['remaining_volume'] = 0
                    
                    blk22 += 1
                else:
                    blocks_filtered_out += 1
            
            logger.info(f"[RTMSchedulingServices] Blocks matched filter: {blocks_matched}, Blocks filtered out: {blocks_filtered_out}, Total blocks processed: {blk22}")
            
            if blocks_matched == 0:
                logger.warning(f"[RTMSchedulingServices] No blocks matched the filter range. Data has time_blocks {min(getplantsData.keys()) if getplantsData else 'N/A'}-{max(getplantsData.keys()) if getplantsData else 'N/A'}, but filter requires time_blocks {time_block_start}-{time_block_end}.")
                logger.warning(f"[RTMSchedulingServices] This might indicate:")
                logger.warning(f"  1. No data exists for the requested time_block range")
                logger.warning(f"  2. Data exists but outside the requested time_block range")
                logger.warning(f"[RTMSchedulingServices] Available time_blocks in data: {sorted(list(getplantsData.keys()))[:20] if getplantsData else 'No data'}...")
                
                # If no blocks matched and no parameters were provided, try returning all blocks
                if from_block is None and to_block is None and getplantsData:
                    logger.info(f"[RTMSchedulingServices] No parameters provided and no blocks matched. Trying to return all available blocks (1-96) as fallback.")
                    time_block_start = 1
                    time_block_end = 96
                    # Re-process with all blocks
                    blk22 = 0
                    data = {
                        'table_data': {},
                        'Block_data': {}
                    }
                    blocks_matched = 0
                    for blk, availability_data_array_b_wise in getplantsData.items():
                        if blk >= time_block_start and blk <= time_block_end:
                            blocks_matched += 1
                            # ... (same processing logic as above)
                            # Copy the processing logic from the main loop
                            remaningvolumearray = []
                            updatedsurpluse = []
                            
                            for plkey, availability_plnt_arrayyy_single in enumerate(
                                availability_data_array_b_wise.get('result', [])
                            ):
                                availability_plnt_arrayyy = availability_plnt_arrayyy_single
                                
                                schedule_date_obj = datetime.strptime(
                                    availability_data_array_b_wise['schedule_date'], 
                                    "%Y-%m-%d"
                                )
                                
                                data['table_data'][plkey] = data['table_data'].get(plkey, {})
                                data['table_data'][plkey][blk22] = {
                                    'Date': format_date_for_display(availability_data_array_b_wise['schedule_date']),
                                    'start_time': availability_data_array_b_wise['start_time'],
                                    'end_time': availability_data_array_b_wise['end_time'],
                                    'full_time': availability_data_array_b_wise['full_time'],
                                    'time_block': availability_data_array_b_wise['time_block'],
                                    'dc_sg_type': availability_data_array_b_wise['dc_sg_type'],
                                    'session_number': (lambda tb: getsessionNo.get(tb, {}).get('Session_Id') or getsessionNo.get(tb, {}).get('session_number', ''))(
                                        int(availability_data_array_b_wise.get('time_block', 0))
                                    ),
                                    'forecast': round_numeric_value(availability_data_array_b_wise['demand_forecast']),
                                    'supply_availablity': round_numeric_value(availability_data_array_b_wise['totalAvailability']),
                                    'surplus_deficit': round_numeric_value(availability_data_array_b_wise['surplus_deficit']),
                                    'surplus_deficit_updated': round_numeric_value(availability_plnt_arrayyy.get('surplus_deficit_updated', availability_data_array_b_wise.get('surplus_deficit_updated', 0))),
                                    'price': round_numeric_value(availability_plnt_arrayyy.get('price', 0)),
                                    'remaining_volume': round_numeric_value(availability_plnt_arrayyy.get('remaining_volume', 0)),
                                    'plant_RMP_value': round_numeric_value(availability_plnt_arrayyy.get('RMP', 0)),
                                    'plant_TM_value': round_numeric_value(availability_plnt_arrayyy.get('plant_TM_value', 0)),
                                    'sell_volume': round_numeric_value(availability_plnt_arrayyy.get('Final_plant_RMP_volume', 0)),
                                    'genrated_sg': round_numeric_value(availability_plnt_arrayyy.get('genrated_sg_volume', 0)),
                                    'actual_volume': round_numeric_value(availability_plnt_arrayyy.get('actual_plant_volume', 0)),
                                    'category': availability_plnt_arrayyy.get('category', ''),
                                    'plant_name': availability_plnt_arrayyy.get('plant_name', ''),
                                    'display_category': availability_plnt_arrayyy.get('c_s', availability_plnt_arrayyy.get('display_category', '')),
                                    'dispatch_type': availability_plnt_arrayyy.get('dispatch_type', ''),
                                    'total_sell_volume': round_numeric_value(availability_plnt_arrayyy.get('remaining_volume', 0)),
                                    'scheduling_requisition_type': availability_plnt_arrayyy.get('scheduling_requisition_type', '')
                                }
                                
                                remaining_vol = float(availability_plnt_arrayyy.get('remaining_volume', 0))
                                surplus_def_updated = float(availability_plnt_arrayyy.get('surplus_deficit_updated', 0))
                                remaningvolumearray.append(remaining_vol)
                                updatedsurpluse.append(surplus_def_updated)
                            
                            data['Block_data'][blk22] = {
                                'Date': format_date_for_display(availability_data_array_b_wise['schedule_date']),
                                'start_time': availability_data_array_b_wise['start_time'],
                                'end_time': availability_data_array_b_wise['end_time'],
                                'full_time': availability_data_array_b_wise['full_time'],
                                'time_block': availability_data_array_b_wise['time_block'],
                                'dc_sg_type': availability_data_array_b_wise['dc_sg_type'],
                                'session_number': getsessionNo.get(
                                    availability_data_array_b_wise['time_block'], 
                                    {}
                                ).get('Session_Id', ''),
                                'forecast': round_numeric_value(availability_data_array_b_wise['demand_forecast']),
                                'supply_availablity': round_numeric_value(availability_data_array_b_wise['totalAvailability']),
                                'surplus_deficit': round_numeric_value(availability_data_array_b_wise['surplus_deficit']),
                                'total_availablity_updated': round_numeric_value(availability_data_array_b_wise.get('total_availablity_updated', 0)),
                                'rtm_forecasted_price': round_numeric_value(availability_data_array_b_wise.get('rtm_forecasted_price', 0))
                            }
                            
                            stored_surplus_deficit_updated = availability_data_array_b_wise.get('surplus_deficit_updated', 0)
                            if stored_surplus_deficit_updated == 0:
                                total_avail_updated = availability_data_array_b_wise.get('total_availablity_updated', 0)
                                demand_forecast = availability_data_array_b_wise.get('demand_forecast', 0)
                                stored_surplus_deficit_updated = total_avail_updated - demand_forecast
                            data['Block_data'][blk22]['surplus_deficit_updated'] = round_numeric_value(stored_surplus_deficit_updated)
                            
                            if len(remaningvolumearray) > 0:
                                if stored_surplus_deficit_updated > 0:
                                    data['Block_data'][blk22]['remaining_volume'] = round_numeric_value(min(remaningvolumearray))
                                elif stored_surplus_deficit_updated < 0:
                                    data['Block_data'][blk22]['remaining_volume'] = round_numeric_value(max(remaningvolumearray))
                                else:
                                    data['Block_data'][blk22]['remaining_volume'] = round_numeric_value(min(remaningvolumearray))
                            else:
                                data['Block_data'][blk22]['remaining_volume'] = 0
                            
                            blk22 += 1
                    
                    logger.info(f"[RTMSchedulingServices] Fallback: Processed {blocks_matched} blocks with all blocks (1-96) range")
            
            if len(data.get('table_data', {})) == 0 and len(data.get('Block_data', {})) == 0:
                logger.warning(f"[RTMSchedulingServices] Returning empty table_data and Block_data. This might indicate:")
                logger.warning(f"  1. No plant data found in get_all_plant_data_for_scheduling")
                logger.warning(f"  2. All blocks were filtered out by block range")
                logger.warning(f"  3. Plant data has no 'result' array")
            
            return data if data else {}
            
        except Exception as e:
            logger.error(f"Error in get_realtime_scheduling_tb_data: {str(e)}")
            return {}

    async def get_all_scheduling_data(self, exclude_date_type: str = "all") -> List[Dict[str, Any]]:
        """
        Retrieves historical scheduling data across multiple dates
        
        Args:
            exclude_date_type: "exclude_1" (today-tomorrow), "exclude_2" (yesterday-today), 
                             "all" (yesterday-tomorrow)
        
        Returns:
            List of scheduling records
        """
        try:
            # Apply 22:30 PM cutoff logic: if current time > 22:30, treat "today" as "tomorrow"
            now = datetime.now()
            now_time = now.time()
            cutoff_time = datetime.strptime("22:30", "%H:%M").time()
            
            # If after 22:30, shift the date calculation by 1 day forward
            if now_time > cutoff_time:
                # After 22:30, "today" is effectively "tomorrow" for scheduling purposes
                effective_today = (now + timedelta(days=1)).date()
                logger.info(f"[RTMSchedulingServices] get_all_scheduling_data: Current time {now_time} > 22:30, using effective_today={effective_today}")
            else:
                # Before 22:30, use actual today
                effective_today = now.date()
                logger.info(f"[RTMSchedulingServices] get_all_scheduling_data: Current time {now_time} <= 22:30, using effective_today={effective_today}")
            
            # Determine date range using effective_today
            if exclude_date_type == "exclude_1":
                # Today to Tomorrow
                from_date_dt = datetime.combine(effective_today, datetime.min.time())
                to_date_dt = from_date_dt + timedelta(days=1)
            elif exclude_date_type == "exclude_2":
                # Yesterday to Today
                from_date_dt = datetime.combine(effective_today, datetime.min.time()) - timedelta(days=1)
                to_date_dt = datetime.combine(effective_today, datetime.min.time())
            else:
                # Yesterday to Tomorrow (default)
                from_date_dt = datetime.combine(effective_today, datetime.min.time()) - timedelta(days=1)
                to_date_dt = datetime.combine(effective_today, datetime.min.time()) + timedelta(days=1)
            
            logger.info(f"[RTMSchedulingServices] get_all_scheduling_data: exclude_date_type={exclude_date_type}, "
                       f"from_date={from_date_dt.date()}, to_date={to_date_dt.date()}")
            
            # Get all scheduling data from repository
            # Query using scheduled_at (Date field) which is more reliable than timestamp
            collection = self.scheduling_repo.db[self.scheduling_repo.collection_name]
            
            results = await collection.find(
                {
                    'scheduled_at': {
                        '$gte': from_date_dt,
                        '$lt': to_date_dt
                    }
                }
            ).sort([('scheduled_at', 1), ('time_block', 1)]).to_list(length=10000)
            
            logger.info(f"[RTMSchedulingServices] get_all_scheduling_data: Found {len(results)} records in date range")
            
            if len(results) == 0:
                logger.warning(f"[RTMSchedulingServices] get_all_scheduling_data: No records found for exclude_date_type={exclude_date_type}, "
                             f"date range: {from_date_dt.date()} to {to_date_dt.date()}. "
                             f"This might indicate:")
                logger.warning(f"  1. No data exists in scheduling_requisition_calculation_table for this date range")
                logger.warning(f"  2. Data exists but scheduled_at field doesn't match the date range")
                logger.warning(f"  3. Try running /rtm/run-scheduling first to populate the table")
            
            # Get unique plant_ids to lookup plant_master data
            plant_ids = set()
            for value in results:
                plant_id = value.get('plant_id', value.get('key', ''))
                if plant_id:
                    plant_ids.add(plant_id)
            
            # Fetch plant_master data for display_category and dispatch_type
            # Use plant_id field to match (same as get_all_plant_data_for_scheduling)
            plant_master_data = {}
            if plant_ids:
                try:
                    plant_master_collection = self.plants_list_repo.db[self.plants_list_repo.collection_name]
                    plant_master_records = await plant_master_collection.find(
                        {"plant_id": {"$in": list(plant_ids)}}
                    ).to_list(length=10000)
                    for plant_record in plant_master_records:
                        plant_id_key = plant_record.get('plant_id', '')
                        if plant_id_key:
                            plant_master_data[plant_id_key] = {
                                'display_category': plant_record.get('c_s', ''),
                                'dispatch_type': plant_record.get('dispatch_type', '')
                            }
                    logger.info(f"[RTMSchedulingServices] get_all_scheduling_data: Fetched plant_master data for {len(plant_master_data)} plants")
                except Exception as e:
                    logger.warning(f"[RTMSchedulingServices] get_all_scheduling_data: Error fetching plant_master data: {str(e)}")
            
            data = []
            
            # Transform to response format
            for value in results:
                # Extract scheduled_at and convert to schedule_date string
                scheduled_at = value.get('scheduled_at')
                if isinstance(scheduled_at, datetime):
                    schedule_date_str = scheduled_at.strftime('%Y-%m-%d')
                    schedule_date_timestamp = int(scheduled_at.timestamp())
                    schedule_date_int = int(scheduled_at.strftime('%Y%m%d'))
                else:
                    # Fallback: try to get from schedule_date field or scheduled_at_timestamp
                    schedule_date_str = value.get('schedule_date', '')
                    if not schedule_date_str and scheduled_at:
                        try:
                            schedule_date_str = scheduled_at.strftime('%Y-%m-%d') if hasattr(scheduled_at, 'strftime') else str(scheduled_at)
                        except:
                            schedule_date_str = ''
                    schedule_date_timestamp = value.get('scheduled_at_timestamp', 0)
                    schedule_date_int = value.get('scheduled_at_int', 0)
                
                # Calculate time fields from time_block if not present
                time_block = value.get('time_block', 0)
                start_time = value.get('start_time', '')
                end_time = value.get('end_time', '')
                full_time = value.get('full_time', '')
                
                if not start_time or not end_time or not full_time:
                    if time_block:
                        try:
                            time_info = get_time_block_start_end(int(time_block))
                            start_time = time_info['start_time']
                            end_time = time_info['end_time']
                            full_time = time_info['full_time']
                        except (ValueError, TypeError) as e:
                            logger.warning(f"[RTMSchedulingServices] get_all_scheduling_data: Error calculating time from time_block {time_block}: {str(e)}")
                
                # Get plant_id for lookup
                plant_id = value.get('plant_id', value.get('key', ''))
                
                # Get display_category and dispatch_type from plant_master
                # Always prefer plant_master data (same as graph data API)
                display_category = ''
                dispatch_type = ''
                
                if plant_id and plant_id in plant_master_data:
                    display_category = plant_master_data[plant_id].get('display_category', '')
                    dispatch_type = plant_master_data[plant_id].get('dispatch_type', '')
                else:
                    # Fallback to stored values if plant_master lookup failed
                    display_category = value.get('display_category', '')
                    dispatch_type = value.get('dispatch_type', '')
                
                data.append({
                    'schedule_date': schedule_date_str,
                    'schedule_date_timestamp': schedule_date_timestamp,
                    'schedule_date_Int': schedule_date_int,
                    'start_time': start_time,
                    'end_time': end_time,
                    'full_time': full_time,
                    'time_block': time_block,
                    'dc_sg_type': value.get('dc_sg_type', ''),
                    'session_number': value.get('session_number', ''),
                    'forecast': round_numeric_value(value.get('demand_forecast_mw', value.get('demand_forecast', 0))),
                    'demand_forecast': round_numeric_value(value.get('demand_forecast_mw', value.get('demand_forecast', 0))),
                    'total_supply_availablity': round_numeric_value(value.get('total_supply_available_mw', value.get('total_supply_availablity', 0))),
                    'surplus_deficit': round_numeric_value(value.get('net_surplus_deficit_mw', value.get('surplus_deficit', 0))),
                    'surplus_deficit_updated': round_numeric_value(value.get('updated_surplus_deficit_mw', value.get('surplus_deficit_updated', 0))),
                    'total_availablity_updated': round_numeric_value(value.get('updated_total_supply_mw', value.get('total_availablity_updated', 0))),
                    'price': round_numeric_value(value.get('price', 0)),
                    'rtm_forecasted_price': round_numeric_value(value.get('rtm_forecasted_price', 0)),
                    'remaining_volume': round_numeric_value(value.get('remaining_volume_mw', value.get('remaining_volume', 0))),
                    'plant_RMP_value': round_numeric_value(value.get('plant_rmp_value', value.get('plant_RMP_value', 0))),
                    'plant_TM_value': round_numeric_value(value.get('plant_tm_value', value.get('plant_TM_value', 0))),
                    'up_down_RMP_volume': round_numeric_value(value.get('total_rmp_volume_mw', value.get('up_down_RMP_volume', 0))),
                    'sell_volume': round_numeric_value(value.get('final_rmp_volume_mw', value.get('Final_plant_RMP_volume', 0))),
                    'Final_plant_RMP_volume': round_numeric_value(value.get('final_rmp_volume_mw', value.get('Final_plant_RMP_volume', 0))),
                    'genrated_sg': round_numeric_value(value.get('generated_sg_volume_mw', value.get('genrated_sg_volume', 0))),
                    'actual_plant_volume': round_numeric_value(value.get('actual_plant_volume_mw', value.get('actual_plant_volume', 0))),
                    'actual_plant_volume_DC': round_numeric_value(value.get('actual_dc_volume_mw', value.get('actual_plant_volume_DC', 0))),
                    'plant_name': value.get('plant_name', ''),
                    'category': value.get('category', ''),
                    'display_category': display_category,
                    'dispatch_type': dispatch_type,
                    'total_RMP_volume': round_numeric_value(value.get('total_rmp_volume_mw', value.get('total_RMP_volume', 0))),
                    'scheduling_requisition_type': value.get('scheduling_req_type', value.get('scheduling_requisition_type', '')),
                    'plant_id': plant_id
                })
            
            return data
            
        except Exception as e:
            logger.error(f"Error in get_all_scheduling_data: {str(e)}")
            return []

        
    
# # Global Instances
rtm_scheduling_services = RTMSchedulingServices()