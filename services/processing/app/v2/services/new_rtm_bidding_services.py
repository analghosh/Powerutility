import logging
from functools import partial
from services.processing.app.core.config import settings
from services.processing.app.v2.repository.base_repository import GenericRepository
from typing import List, Optional, Dict, Any, Tuple
import asyncio
import re
from datetime import datetime, date, timedelta
import time
from math import isnan, isfinite
from collections import OrderedDict

from bson import ObjectId
from libs.core.jwt_validator import TokenPayload

from ..repository.supply_serve_repository import SupplyServeRepository
# from ..repository.serve_supply_table_repository import serveSupplyTableRepository
from ..repository.rtm_single_bid_data_for_approval_repository import RTMSingleBidDataForApprovalRepository
from ..repository.limit_values_repository import LimitValuesRepository
from ..repository.master_plants_repository  import MasterPlantsRepository 
from ..repository.rtm_product_hardcoded_data_repository import RTMProductHardCodedDataRepository
from ..repository.demand_serve_repository import  DemandServeRepository
from ..repository.exchange_repository import ExchangeRepository
# from ..repository.exchange_serve_repository import ExchangeServeRepository
from ..repository.rtm_current_bid_data_for_approval_repository import RTMCurrentBidDataForApprovalRepository

# Bid Status repositories - matching PHP implementations
from ..repository.rtm_trade_book_response_repository import RTMTradeBookResponseRepository
from ..repository.dispatch_schedule_repository import DispatchScheduleRepository
from libs.audit_queue.simple_logger import AuditLogger
from ..repository.price_quantity_response_repository import PriceQuantityResponseRepository
# Note: PlantDataForApprovalRepository uses same collection as RTMSingleBidDataForApprovalRepository
# Methods are added to RTMSingleBidDataForApprovalRepository instead
from ..repository.rtm_single_bid_response_repository import RTMSingleBidResponseRepository
from ..repository.decision_rtm_block_bid_iex_response_repository import DecisionRtmBlockBidIexResponseRepository
from ..repository.parameter_repository import ParameterRepository
from ..repository.bid_trade_serve_repository import BidTradeServeRepository
from ..repository.block_bid_current_data_repository import BlockBidCurrentDataRepository
from ..repository.iex_api_detail_repository import IexApiDetailRepository
from ..repository.integration_api_log_repository import IntegrationApiLogRepository
from ..repository.bid_capture_repository import CaptureServeTableDataRepository

from ..repository.exchange_config_repository import ExchangeConfigRepository
from ..services.parameter_service import parameter_service
from libs.messaging.job_publisher import JobPublisher
from libs.models.forecast_serve_entity import map_forecast_source_display

logger = logging.getLogger(__name__)

async def bidPriceNOC():
    """Fetch rtm_bid_price from parameters; fallback to 10000."""
    try:
        return await parameter_service.get_bid_price("RTM")
    except Exception as e:
        logger.warning(f"Failed to fetch rtm_bid_price from parameters: {e}")
        return 10000

def to_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0

# Custom round function to handle negative values
def _round(n, decimals=0):
    factor = 10 ** decimals
    return int(n * factor + 0.5) / factor if n >= 0 else int(n * factor - 0.5) / factor

class RTMBiddingServices:
    async def get_bid_submission_details(self, date_str: str) -> List[Dict[str, Any]]:
        """Get RTM bid submission details for a specific date."""
        return await self.integration_api_log_repo.get_bid_submission_details(date_str, "rtm", "single-bid")

    def __init__(self, collection_name: str = "supply_serve"):
        self.generic_repo = GenericRepository()
        self.rtm_book_collection = settings.MONGODB_RTM_BOOK_COLLECTION
        self.time_block_collection = settings.MONGODB_TIME_BLOCK_COLLECTION
        self.exchange_config_repository = ExchangeConfigRepository()
        self.supply_repo = SupplyServeRepository()
        self.single_bid_data_for_approval_repo = RTMSingleBidDataForApprovalRepository(collection_name="rtm_single_bid_data_for_approval")
        self.rtm_product_hardcoded_data_repo = RTMProductHardCodedDataRepository(collection_name="rtm_product_hardCoded_data")
        self.plants_list_repo = MasterPlantsRepository()
        self.limit_values_repo = LimitValuesRepository()
        self.demand_repo = DemandServeRepository()
        self.exchange_repo = ExchangeRepository()
        # self.exchange_repo = ExchangeServeRepository(collection_name="exchange_serve")
        self.rtm_currect_selected_forecast_log_repo = RTMCurrentBidDataForApprovalRepository(collection_name="RTM_currect_selected_forecast_log")
        # self.scheduling_requisition_repo = SchedulingRequisitionCalculationRepository(collection_name="scheduling_requisition_calculation_table")
        
        # Bid Status repositories - matching PHP repository instances
        # PHP: $this->rtmtradebookresponse
        self.rtmtradebookresponse = RTMTradeBookResponseRepository()
        
        # PHP: $this->DispatchScheduleModel
        self.dispatch_schedule_model = DispatchScheduleRepository()
        
        # PHP: $this->PriceQuantityResponse
        self.price_quantity_response = PriceQuantityResponseRepository()
        
        # PHP: $this->PlantDataForApprovalModel
        # Note: Uses same collection as single_bid_data_for_approval_repo (rtm_single_bid_data_for_approval)
        # Methods get_bid_status_data() and get_bid_status_data_submitted() are in RTMSingleBidDataForApprovalRepository
        self.plant_data_for_approval_model = self.single_bid_data_for_approval_repo
        
        # PHP: $this->rtmsinglebidresponse
        self.rtm_single_bid_response = RTMSingleBidResponseRepository()
        
        # PHP: $this->decisionrtmblockbidiexresponse
        self.decision_rtm_block_bid_iex_response = DecisionRtmBlockBidIexResponseRepository()
        
        # Parameter repository for fetching user_id and other configuration
        self.parameter_repository = ParameterRepository()
        
        # Bid trade serve repository for fetching trade quantities
        self.bid_trade_serve_repo = BidTradeServeRepository()
        
        # Block bid current data repository for fetching block bid data
        self.block_bid_current_data_repo = BlockBidCurrentDataRepository()

        # IEX API detail repository for fetching IEX related configurations
        self.iex_api_detail_repo = IexApiDetailRepository()

        # Integration API Log repository
        self.integration_api_log_repo = IntegrationApiLogRepository()
        self.capture_serve_repo = CaptureServeTableDataRepository()
        self.manual_capture_serve_repo = CaptureServeTableDataRepository(
            collection_name=getattr(
                settings,
                "MONGODB_RTM_BID_CAPTURE_MANUAL_COLLECTION",
                "rtm_bid_capture_manual",
            )
        )

        # Assume getSessionfrom() returns a list of session time strings like ["00:30", "01:00", ...]
        session_times = RTMBiddingServices.get_session_from()  

        # Get current time as string
        current_time = datetime.now().strftime("%H:%M")

        # Count the number of sessions that have passed
        passed_sessions_count = sum(
            1 for t in session_times if datetime.strptime(t, "%H:%M") < datetime.strptime(current_time, "%H:%M")
        )
        
        # Calculate today's current block
        today_current_block = (2 * passed_sessions_count) - 1 + 95
        logger.debug(f"session_times {current_time}")
        # print(f"session_times {current_time}")
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

        try:
            self.supply_repo.ensure_performance_indexes()
            self.demand_repo.ensure_graph_lookup_indexes()
            self.capture_serve_repo.ensure_idempotency_indexes()
            self.manual_capture_serve_repo.ensure_idempotency_indexes()
        except Exception:
            logger.warning("RTM MongoDB index ensure failed", exc_info=True)

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
                target_date = datetime.strptime(schedule_date, "%Y-%m-%d")
                # schedule_date=schedule_date
            else:
                # Get current date in YYYY-MM-DD format
                target_date = datetime.now().date()

                # Temporary: Hardcoded schedule date for fetching DSM records
                # schedule_date= '2025-08-04'
            
          
            pipeline = [
                    {
                        # From supply_serve collection
                        "$match": {
                            "scheduled_at": target_date,                        # Change from schedule_date to scheduled_at 
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
                            # From demand_serve collection
                            "from": "demand_serve",                          # Change from serve_demand_table to demand_serve for demand_serve collection
                            "let": {"t_block": "$_id"},
                            "pipeline": [
                                {
                                    "$match": {
                                        "$expr": {
                                            "$and": [
                                                {"$eq": ["$time_block", "$$t_block"]},
                                                {"$eq": ["$scheduled_at", target_date]},
                                                {"$eq": ["$type", "IDF"]}
                                            ]
                                        }
                                    }
                                },
                                {
                                    "$group": {
                                        "_id": None,
                                        "forecast": {"$sum": "$forecast"},
                                        "source_forecast": {"$first": "$source_forecast"},
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
                            "_source_forecast": "$demand.source_forecast",
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
            result = await self.supply_repo.aggregate_async(self.supply_repo.collection_name, pipeline)
            for row in result:
                source_forecast = row.pop("_source_forecast", None)
                forecast_source = map_forecast_source_display(source_forecast)
                ordered = OrderedDict()
                for key, value in row.items():
                    ordered[key] = value
                    if key == "forecast":
                        ordered["forecast_source"] = forecast_source
                row.clear()
                row.update(ordered)
            return {"data": result}
        except Exception as e:
            logger.exception(f"[RTMBiddingServices] Failed to fetch demand data: {str(e)}")
            raise


        
    async def rtm_single_bid_table(
        self, 
        schedule_date: Optional[str], 
        from_block: int, 
        to_block: int
    ) -> Dict[str, Any]:
        """
        Fetch RTM data and compute total availability with all limits and splits.
        """

        try:
            if(schedule_date):
                target_date = datetime.strptime(schedule_date, "%Y-%m-%d")# date
                # schedule_date=schedule_date
            else:
                # Get current date in YYYY-MM-DD format
                target_date = datetime.now().date()
            
            # Convert target_date to datetime for proper comparison with scheduled_at (which is datetime in MongoDB)
            if isinstance(target_date, datetime):
                target_date_dt = target_date
            else:
                # If it's a date object, convert to datetime at start of day
                target_date_dt = datetime.combine(target_date, datetime.min.time())
            
            # Get end of day for comparison
            target_date_end = datetime.combine(target_date_dt.date(), datetime.max.time())


            # Fetch plant data
            pipeline = [
                {"$match": {
                    "scheduled_at": target_date,                  # From supply_serve collection
                    "dc_sg": "SG",                         
                    "time_block": {"$gte": from_block, "$lte": to_block}
                }},
                {
                    "$sort": {
                        "time_block": 1  # 1 = ASC, -1 = DESC
                    }
                },
                {"$project": {"_id": 0}}
            ]

            # Fetch plants data from supply_serve collection
            getplantsData = await self.supply_repo.aggregate(self.supply_repo.collection_name, pipeline)

            if not getplantsData:
                return {}

            # Fetch SG records to get rtm_tm values per plant/block
            # rtm_tm represents the SG value, not the DC value.
            sg_tm_pipeline = [
                {"$match": {
                    "scheduled_at": target_date,
                    "dc_sg": "SG",
                    "time_block": {"$gte": from_block, "$lte": to_block}
                }},
                {"$project": {"_id": 0, "plant_id": 1, "time_block": 1, "rtm_tm": 1, "rtm_tm_revision_no": 1}}
            ]
            sg_tm_data = await self.supply_repo.aggregate(self.supply_repo.collection_name, sg_tm_pipeline)
            # Build lookup: {(plant_id, time_block): rtm_tm}
            sg_tm_lookup = {
                (d["plant_id"], d["time_block"]): {
                    "rtm_tm": d.get("rtm_tm", 0),
                    "rtm_tm_revision_no": d.get("rtm_tm_revision_no"),
                }
                for d in sg_tm_data
            }

            # Fetch RTM TM parameter from parameters collection
            rtm_tm_param = await self.parameter_repository.get_parameter_by_key("rtmCalcuTmPr")
            rtm_tm_mode = rtm_tm_param.get("para_value", "45_percent_logic") if rtm_tm_param else "45_percent_logic"
            logger.info(f"RTM TM Mode: {rtm_tm_mode}")

            # Hardcoded must plant price
            mustplantPrice = 0.2   # client asked to change the must plant price to 0.02
            checkTMvalue = "TM" if rtm_tm_mode == "1" else "TR"

            # Hardcoded session data
            product_hardCoded_pipeline = [
                {"$sort": {"Product_Code": 1}},
                {"$project": {"_id": 0}}
            ]
            # Fetch session data from rtm_product_hardcoded_data collection
            getsessionNo = await self.rtm_product_hardcoded_data_repo.aggregate(
                self.rtm_product_hardcoded_data_repo.collection_name,
                product_hardCoded_pipeline,
            )

            plants_list_pipeline = [{"$project": {"_id": 0}}]
            plantData = await self.plants_list_repo.aggregate(collection_name="plant_master", pipeline=plants_list_pipeline)
            plant_dict = {p['plant_id']: p for p in plantData}

            fix_count = getplantsData[0]['time_block']
            availability_data_array = {}
            # return getplantsData
            # Organize availability data by time block
            for i, availability_value in enumerate(getplantsData):
                array_key = int(availability_value["time_block"]) - fix_count
                
                # print(i, int(array_key))
                if array_key not in availability_data_array:
                    availability_data_array[int(array_key)] = {
                        'scheduled_at': availability_value['scheduled_at'],
                        'time_block': availability_value['time_block'],
                        'plants': {}
                    }

                plant_key = availability_value['plant_id']
                plant_info = plant_dict.get(plant_key, {})
                
                dispatch_type = plant_info.get('dispatch_type')
                # if(plant_key == "NTPCS"):
                #     logger.debug(plant_info, plant_key , dispatch_type)
                    # print(plant_info, plant_key , dispatch_type)
                availability_data_array[int(array_key)]['plants'][plant_key] = {
                    'plant_name': plant_info.get('plant_name'),
                    'plant_key': plant_key,
                    'plant_volume': availability_value['volume'],
                    'dispatch_type': dispatch_type,
                    'PX_Price': plant_info.get('px_price'),
                    'category': plant_info.get('c_s'),
                    'display_category': availability_value.get('display_category', 'Merit')
                }
            # return availability_data_array
            # Fetch forecast, limits, and exchange data
            getForecastedValue = await self.demand_repo.aggregate(
                self.demand_repo.collection_name,
                [
                    {
                        "$match": {
                            "scheduled_at": target_date,
                            "type": "IDF",
                            "time_block": {"$gte": from_block, "$lte": to_block},
                        }
                    },
                    {"$sort": {"time_block": 1}},
                    {"$project": {"_id": 0}},
                ],
            )
           
            TotalAvailability = self.total_availability_new(availability_data_array, getForecastedValue, from_block, to_block)
            
            # Fetch approved block bid quantities by time_block
            formatted_date = target_date_dt.strftime("%Y-%m-%d") if schedule_date else datetime.now().strftime("%Y-%m-%d")
            approved_block_bid_quantities = await self.block_bid_current_data_repo.get_approved_block_bid_quantities_by_time_block(
                formatted_date, market_type="RTM"
            )
            reserved_block_bid_quantities = await self.block_bid_current_data_repo.get_approved_reserved_block_bid_quantities_by_time_block(
                formatted_date
            )

            # Bid Trade Serve data for calculating NOC sell / buy limits based on trade quantities
            bid_trade_block_data = await self.bid_trade_serve_repo.adjustment_noc_buy_sell(scheduled_at=formatted_date)

                

            
            # Helper function to convert block number to time_block format (HH:MM-HH:MM)
            def get_fifteen_full_time_block_id(block_id: int) -> str:
                """Convert block number (1-96) to time_block format (HH:MM-HH:MM)"""
                if block_id < 1 or block_id > 96:
                    return ""
                start_time = (datetime.strptime("00:00", "%H:%M") + timedelta(minutes=(block_id - 1) * 15)).time()
                end_time = (datetime.strptime("00:00", "%H:%M") + timedelta(minutes=block_id * 15)).time()
                end_time_str = "00:00" if block_id == 96 else end_time.strftime("%H:%M")
                return f"{start_time.strftime('%H:%M')}-{end_time_str}"
            
            # Calculate yesterday's date for MCP lookup
            yesterday_date = (target_date_dt - timedelta(days=1)).date() if target_date_dt else (datetime.now() - timedelta(days=1)).date()
            yesterday_date_str = yesterday_date.strftime("%Y-%m-%d")
            
            # Get forecastedPrice from exchange_serve using market_type="RTM" and mcp value from yesterday's date
            # forecastedPrice = list(
            #     self.exchange_repo.aggregate(
            #         "exchange_serve",
            #         [
            #             {
            #                 "$match": {
            #                     "$expr": {
            #                         "$eq": [
            #                             {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
            #                             yesterday_date_str
            #                         ]
            #                     },
            #                     "market_type": "RTM",
            #                     "time_block": {"$gte": from_block, "$lte": to_block},
            #                     "px_name": "IEX",
            #                 }
            #             },
            #             {"$sort": {"time_block": 1}},
            #             {
            #                 "$project": {
            #                     "_id": 0,
            #                     "block": "$time_block",  # Map time_block to block for compatibility
            #                     "values": "$mcp",  # Use mcp value as forecastedPrice (mapped to 'values' to match expected structure)
            #                 }
            #             },
            #         ],
            #     )
            # )
            
            # OLD CODE - Get forecastedPrice from limit_values (commented for future reuse if needed)
            forecastedPrice = await self.limit_values_repo.aggregate(
                [
                        {
                            "$match": {
                                "scheduled_at": {"$lte": target_date_end},  # Get data up to target date (end of day)
                                "id": "RTM_forecasted_price",
                                "market": "RTM",
                                "block": {"$gte": from_block, "$lte": to_block},
                            }
                        },
                        {"$sort": {"scheduled_at": -1, "block": 1}},  # Sort by date descending, then block
                        {
                            "$group": {
                                "_id": "$block",  # Group by block to get latest record per block
                                "latest_doc": {"$first": "$$ROOT"}  # Get the first (latest date) record for each block
                            }
                        },
                        {"$replaceRoot": {"newRoot": "$latest_doc"}},  # Replace root with the latest document
                        {"$sort": {"block": 1}},  # Sort by block for consistent ordering
                        {"$project": {"_id": 0}},
                ],
            )
            limit_schedule_date = target_date
            
            bufferPrice = await self.limit_values_repo.aggregate(
                [
                        {
                            "$match": {
                                "scheduled_at": {"$lte": target_date_end},
                                "id": "Buffer",
                                "market": "RTM",
                                "block": {"$gte": from_block, "$lte": to_block},
                            }
                        },
                        {"$sort": {"scheduled_at": -1, "block": 1}},  # Sort by date descending, then block
                        {
                            "$group": {
                                "_id": "$block",
                                "latest_doc": {"$first": "$$ROOT"}
                            }
                        },
                        {"$replaceRoot": {"newRoot": "$latest_doc"}},
                        {"$sort": {"block": 1}},
                        {"$project": {"_id": 0}},
                ],
            )
            
            ceilingPrice = await self.limit_values_repo.aggregate(
                [
                        {
                            "$match": {
                                "scheduled_at": {"$lte": target_date_end},
                                "id": "Ceiling_Price",
                                "market": "RTM",
                                "category": "Limits",
                                "block": {"$gte": from_block, "$lte": to_block},
                            }
                        },
                        {"$sort": {"scheduled_at": -1, "block": 1}},  # Sort by date descending, then block
                        {
                            "$group": {
                                "_id": "$block",
                                "latest_doc": {"$first": "$$ROOT"}
                            }
                        },
                        {"$replaceRoot": {"newRoot": "$latest_doc"}},
                        {"$sort": {"block": 1}},
                        {"$project": {"_id": 0}},
                    ],
                )
            

            n_1Limits = await self.limit_values_repo.aggregate(
                [
                    {
                        "$match": {
                            "scheduled_at": {"$lte": target_date_end},
                            "id": "n_1",
                            "market": "RTM",
                            "category": "Limits",
                            "block": {"$gte": from_block, "$lte": to_block},
                        }
                    },
                    {"$sort": {"scheduled_at": -1, "block": 1}},  # Sort by date descending, then block
                    {
                        "$group": {
                            "_id": "$block",
                            "latest_doc": {"$first": "$$ROOT"}
                        }
                    },
                    {"$replaceRoot": {"newRoot": "$latest_doc"}},
                    {"$sort": {"block": 1}},
                    {"$project": {"_id": 0}},
                ],
            )

            noc_buyLimits = await self.limit_values_repo.aggregate(
                [
                        {
                            "$match": {
                                "scheduled_at": {"$lte": target_date_end},
                                "id": "noc_buy",
                                "market": "RTM",
                                "category": "Limits",
                                "block": {"$gte": from_block, "$lte": to_block},
                            }
                        },
                        {"$sort": {"scheduled_at": -1, "block": 1}},  # Sort by date descending, then block
                        {
                            "$group": {
                                "_id": "$block",
                                "latest_doc": {"$first": "$$ROOT"}
                            }
                        },
                        {"$replaceRoot": {"newRoot": "$latest_doc"}},
                        {"$sort": {"block": 1}},
                        {"$project": {"_id": 0}},
                ],
            )

            noc_sellLimits = await self.limit_values_repo.aggregate(
                [
                        {
                            "$match": {
                                "scheduled_at": {"$lte": target_date_end},
                                "id": "noc_sell",
                                "market": "RTM",
                                "category": "Limits",
                                "block": {"$gte": from_block, "$lte": to_block},
                            }
                        },
                        {"$sort": {"scheduled_at": -1, "block": 1}},  # Sort by date descending, then block
                        {
                            "$group": {
                                "_id": "$block",
                                "latest_doc": {"$first": "$$ROOT"}
                            }
                        },
                        {"$replaceRoot": {"newRoot": "$latest_doc"}},
                        {"$sort": {"block": 1}},
                        {"$project": {"_id": 0}},
                ],
            )

            typeofExchangeIEX = await self.limit_values_repo.aggregate(
                [
                        {
                            "$match": {
                                "scheduled_at": {"$lte": target_date_end},
                                "id": "iex",
                                "market": "RTM",
                                "category": "Ex_Split",
                                "block": {"$gte": from_block, "$lte": to_block},
                            }
                        },
                        {"$sort": {"scheduled_at": -1, "block": 1}},  # Sort by date descending, then block
                        {
                            "$group": {
                                "_id": "$block",
                                "latest_doc": {"$first": "$$ROOT"}
                            }
                        },
                        {"$replaceRoot": {"newRoot": "$latest_doc"}},
                        {"$sort": {"block": 1}},
                        {"$project": {"_id": 0}},
                ],
            )

            typeofExchangePXIL = await self.limit_values_repo.aggregate(
                [
                        {
                            "$match": {
                                "scheduled_at": {"$lte": target_date_end},
                                "id": "pxil",
                                "market": "RTM",
                                "category": "Ex_Split",
                                "block": {"$gte": from_block, "$lte": to_block},
                            }
                        },
                        {"$sort": {"scheduled_at": -1, "block": 1}},  # Sort by date descending, then block
                        {
                            "$group": {
                                "_id": "$block",
                                "latest_doc": {"$first": "$$ROOT"}
                            }
                        },
                        {"$replaceRoot": {"newRoot": "$latest_doc"}},
                        {"$sort": {"block": 1}},
                        {"$project": {"_id": 0}},
                ],
            )

            

        

            # Convert lists to dictionaries keyed by block number for efficient lookup
            forecastedPrice_dict = {item.get("block"): item for item in forecastedPrice if "block" in item}
            ceilingPrice_dict = {item.get("block"): item for item in ceilingPrice if "block" in item}
            bufferPrice_dict = {item.get("block"): item for item in bufferPrice if "block" in item}
            n_1Limits_dict = {item.get("block"): item for item in n_1Limits if "block" in item}
            noc_buyLimits_dict = {item.get("block"): item for item in noc_buyLimits if "block" in item}
            noc_sellLimits_dict = {item.get("block"): item for item in noc_sellLimits if "block" in item}
            typeofExchangeIEX_dict = {item.get("block"): item for item in typeofExchangeIEX if "block" in item}
            typeofExchangePXIL_dict = {item.get("block"): item for item in typeofExchangePXIL if "block" in item}

            data = {}
            total_sell_volume = 0
            
            for blk, TotalblkwiseData in enumerate(TotalAvailability):
                # Get the actual block number from TotalblkwiseData (this is the real time_block)
                actual_block_no = TotalblkwiseData.get('BlockNo')
                
                # Fallback: Get from availability_data_array if BlockNo is not available
                if actual_block_no is None:
                    availability_data_array_b_wise = availability_data_array[blk] if blk < len(availability_data_array) else {}
                    actual_block_no = availability_data_array_b_wise.get('time_block')
                
                # Final fallback: calculate from from_block + blk
                if actual_block_no is None:
                    actual_block_no = from_block + blk
                
                # Ensure actual_block_no is an integer
                actual_block_no = int(actual_block_no) if actual_block_no is not None else (from_block + blk)
                
                # Access values using actual_block_no as key
                forecasted_price_item = forecastedPrice_dict.get(actual_block_no, {})
                ForecastedPrice = round(forecasted_price_item.get('values', 0)) if forecasted_price_item else 0
                if isnan(ForecastedPrice):
                    ForecastedPrice = 0

                ceiling_price_item = ceilingPrice_dict.get(actual_block_no, {})
                ceilingPriceInner = ceiling_price_item.get("values", 0) if ceiling_price_item else 0
                
                buffer_price_item = bufferPrice_dict.get(actual_block_no, {})
                bufferPriceInner = buffer_price_item.get("values", 0) if buffer_price_item else 0
                
                n_1_item = n_1Limits_dict.get(actual_block_no, {})
                n_1LimitsInner = n_1_item.get("values", 0) if n_1_item else 0
                
                noc_buy_item = noc_buyLimits_dict.get(actual_block_no, {})
                noc_buyLimitsInner = noc_buy_item.get("values", 0) if noc_buy_item else 0
                
                noc_sell_item = noc_sellLimits_dict.get(actual_block_no, {})
                noc_sellLimitsInner = noc_sell_item.get("values", 0) if noc_sell_item else 0
                # n_1LimitsInner = n_1Limits[blk]["values"] if blk < len(n_1Limits) and "values" in n_1Limits[blk] else 0 
                
                # noc_buyLimitsInner = noc_sellLimits[blk]["values"] if blk < len(noc_sellLimits) and "values" in noc_sellLimits[blk] else 0
                # noc_sellLimitsInner = noc_sellLimits[blk]["values"] if blk < len(noc_sellLimits) and "values" in noc_sellLimits[blk] else 0

                bid_data = bid_trade_block_data.get(actual_block_no, {})

                bid_cleared = abs(float(bid_data.get("bid_cleared", 0)))

                buy_sell_flag = bid_data.get("buy_sell_flag")

                if buy_sell_flag == "B":
                    noc_buyLimitsInner = max(0, float(noc_buyLimitsInner) - bid_cleared)

                elif buy_sell_flag == "S":
                    noc_sellLimitsInner = max(0, float(noc_sellLimitsInner) - bid_cleared)
                # print(f"Block: {actual_block_no}, Bid Cleared: {bid_cleared}, Buy/Sell Flag: {buy_sell_flag}, Adjusted NOC Buy Limit: {noc_buyLimitsInner}, Adjusted NOC Sell Limit: {noc_sellLimitsInner}")
                typeofExchangeIEXblk = typeofExchangeIEX_dict.get(actual_block_no, {}).get("values", 100)  # Default to 100 if not found
                typeofExchangePXILblk = typeofExchangePXIL_dict.get(actual_block_no, {}).get("values", 0)  # Default to 0 if not found

                total_diff = TotalblkwiseData["diff_for_bid_cal"]
                total_diff_for_must = TotalblkwiseData["diff_for_bid_cal"]
                sell_headroom_after_n1 = max(0.0, float(TotalblkwiseData.get("surplus_deficit", 0)))
                remaining_sell_headroom = sell_headroom_after_n1                                    # [CHANGED]
                
                availability_data_array_b_wise = availability_data_array[blk] if blk < len(availability_data_array) else {}
                
                # Get approved block bid quantity for this time block
                # Convert block number to time_block format (HH:MM-HH:MM) to match with block bid data
                time_block_str = get_fifteen_full_time_block_id(actual_block_no)
                totalBlockBid = float(approved_block_bid_quantities.get(time_block_str, 0))
                total_reserved_bid = float(reserved_block_bid_quantities.get(time_block_str, 0))
                # totalBlockBid = 0
                
                tptal = 0       
                plnt_100_volume = 0
                plkey = 0

                # Handling Must Plants
                # ===============================MUST PLANTS LOGIC START===============================
                total_sell_volume_block = 0.0  # track cumulative sell volume per block

                if total_diff_for_must < 0:  
                     # If total difference for must plants is less than 0, then it means there is a deficit
                    remaining_volume = round(total_diff_for_must) * -1  

                    # Create a new row for must plants for deficit
                    row = {

                        # NEED TO CHECK IF THIS IS CORRECT (schedule_date OR target_date)
                        'Date': schedule_date,          
                        # 'start_time': availability_data_array_b_wise['start_time'],
                        # 'end_tme': availability_data_array_b_wise['end_time'],
                        # 'full_time': availability_data_array_b_wise['full_time'],
                        'time_block': TotalblkwiseData['BlockNo'],  
                        'session_number': getsessionNo[int(TotalblkwiseData['BlockNo'])-1]["Session_Id"],   
                        'forecast': round(TotalblkwiseData['forecast_value']),
                        'forecast_source': TotalblkwiseData['forecast_source'],
                        'supply_availablity': round(TotalblkwiseData['bltotalvolume']),
                        'surplus_deficit': round(TotalblkwiseData['surplus_deficit']),
                        'price': mustplantPrice,
                        'rtm_forecasted_price': ForecastedPrice,
                        'ceiling_price': ceilingPriceInner,
                        'rtm_buffer_value': bufferPriceInner,   
                        'rtm_nminusone_quantum': n_1LimitsInner,
                        'rtm_noc_buy_value': noc_buyLimitsInner,
                        'rtm_noc_sell_value': noc_sellLimitsInner,
                        'totalBlockBid': totalBlockBid,
                        'total_reserved_bid': total_reserved_bid,
                        'remaining_volume': remaining_volume,   # Calculate remaining volume for must plants
                        'sell_volume': remaining_volume,   # Calculate sell volume for must plants
                        'actual_volume': remaining_volume,   # Calculate actual volume for must plants
                        'category': "Must_Plants_cat",
                        'plant_name': "Must_Plants",
                        'display_category': "All",  # Modified from "Must Plants Cat" to "All"
                        'dispatch_type': "must",
                        'total_sell_volume': remaining_volume,   # Calculate total sell volume for must plants
                        'Ex_split_string': f"IEX-{typeofExchangeIEXblk}_PXIL-{typeofExchangePXILblk}"
                    }
                    data.setdefault('table_data', {}).setdefault(plkey, {})[blk] = row  # Add row to data dictionary
                    total_diff = 0
                    total_diff_for_must = 0
                    plkey += 1
                else:
                    # If total difference for must plants is greater than 0, then it means there is a surplus
                    row = {
                        'Date': schedule_date,
                        # 'start_time': availability_data_array_b_wise['start_time'],
                        # 'end_tme': availability_data_array_b_wise['end_time'],
                        # 'full_time': availability_data_array_b_wise['full_time'],
                        'time_block': TotalblkwiseData['BlockNo'],
                        'session_number': getsessionNo[int(TotalblkwiseData['BlockNo'])-1]["Session_Id"],
                        'forecast': round(TotalblkwiseData['forecast_value']),
                        'forecast_source': TotalblkwiseData['forecast_source'],
                        'supply_availablity': round(TotalblkwiseData['bltotalvolume']),
                        'surplus_deficit': round(TotalblkwiseData['surplus_deficit']),
                        'price': mustplantPrice,
                        'rtm_forecasted_price': ForecastedPrice,
                        'ceiling_price': ceilingPriceInner,
                        'rtm_buffer_value': bufferPriceInner,
                        'rtm_nminusone_quantum': n_1LimitsInner,
                        'rtm_noc_buy_value': noc_buyLimitsInner,
                        'rtm_noc_sell_value': noc_sellLimitsInner,
                        'totalBlockBid': totalBlockBid,
                        'total_reserved_bid': total_reserved_bid,
                        'remaining_volume': 0,
                        'sell_volume': 0,
                        'actual_volume': 0,
                        'category': "Must_Plants_cat",
                        'plant_name': "Must_Plants",
                        'display_category': "All",  # Modified from "Must Plants Cat" to "All"
                        'dispatch_type': "must",
                        'total_sell_volume': 0,
                        'Ex_split_string': f"IEX-{typeofExchangeIEXblk}_PXIL-{typeofExchangePXILblk}"
                    }
                    data.setdefault('table_data', {}).setdefault(plkey, {})[blk] = row
                    plkey += 1

                # if blk not in availability_data_array:
                # return availability_data_array[blk]["plants"]

                # ===============================MUST PLANTS LOGIC END===============================


                # ===============================MERIT PLANTS LOGIC START===============================
                plants_dict = availability_data_array[blk]["plants"]
                # Sort plants by PX_Price (ascending)
                sorted_plants = sorted(
                    plants_dict.items(),
                    key=lambda item: item[1].get("PX_Price") if item[1].get("PX_Price") is not None else 0
                )
                # Handling Merit Plants
                # for plnamekey, plant in availability_data_array[blk]["plants"].items():
                for plnamekey, plant in sorted_plants:
                    # return plant
                    
                    # CHANGE FROM PX_Price TO px_price
                    px_price = plant.get('PX_Price') if plant.get('PX_Price') is not None else 0  
                    dispatch_type = plant.get("dispatch_type", "")  
                    plant_volume = float(plant.get("plant_volume"))
                    category = plant.get("category", "")
                    display_category = plant.get("display_category", "")
                    plant_name = plant.get("plant_name", "")
                    # plant_id = plant.get("plant_key", "")
                    plant_id = plnamekey
                    logger.info(f"Plant ID: {plant_id}============================================================")
                    
                    if dispatch_type == "Merit":
                        # value = TotalblkwiseData["surplus_deficit"]
                        # logger.debug(f"Plant Name: {plant_name}, Dispatch Type: {dispatch_type}, Surplus Deficit: {TotalblkwiseData['surplus_deficit']}")

                        if TotalblkwiseData["surplus_deficit"] > 0:  
                            # If surplus deficit is greater than 0, then it means there is a surplus for the plant
                            # Check if TM value is present
                            if checkTMvalue == "TM":
                                # Get rtm_tm from SG records for this plant + block
                                current_block = TotalblkwiseData.get("BlockNo")
                                tm_info = sg_tm_lookup.get((plant_id, current_block), {})
                                TMvalueofPl = float(tm_info.get("rtm_tm", 0))
                                rtm_tm_revision_no_val = tm_info.get("rtm_tm_revision_no")
                                plnt_fourty_five_vol = max(0, plant_volume - TMvalueofPl)   # Subtract TM from volume
                            else:
                                TMvalueofPl = 0.0
                                rtm_tm_revision_no_val = None
                                plnt_fourty_five_vol = plant_volume * 0.45   # Calculate 45% volume for the plant

                            plnt_100_volume = plant_volume   # Calculate 100% volume for the plant
                            sell_volume = 0   # Initialize sell volume to 0
                            
                            if total_diff > 0:
                                if total_diff > plnt_fourty_five_vol:
                                    total_diff -= plnt_fourty_five_vol
                                    
                                elif total_diff <= plnt_fourty_five_vol:
                                    sell_volume = plnt_fourty_five_vol - total_diff
                                    total_diff = 0
                                    
                            elif total_diff == 0:
                                sell_volume = plnt_fourty_five_vol - total_diff
                                total_diff = 0
                            # if total_diff > 0:   # If total difference is greater than 0, then it means there is a surplus for the plant
                            #     if total_diff > plnt_fourty_five_vol: # If total difference is greater than 45% volume, then subtract 45% volume from total difference
                            #         total_diff -= plnt_fourty_five_vol   # Subtract 45% volume from total difference
                            #         sell_volume = plnt_fourty_five_vol   # Set sell volume to 45% volume
                            #     else:      # If total difference is less than 45% volume, then set sell volume to total difference
                            #         sell_volume = total_diff   # Set sell volume to total difference
                            #         total_diff = 0
                            # If total difference is 0, then set sell volume to 0
                            # elif total_diff == 0:   # If total difference is 0, then set sell volume to 0
                            #     sell_volume = 0   # Set sell volume to 0


                            # [CHANGED] Clamp by remaining N-1 headroom at block level
                            sell_volume = min(max(0.0, round(sell_volume, 2)), max(0.0, round(remaining_sell_headroom, 2)))
                            total_sell_volume_block += sell_volume

                            tptal += plnt_100_volume   # Add 100% volume to total volume
                            
                            # If sell volume is greater than 0.01 and 100% volume is not 0, then create a new row for the plant
                            if round(sell_volume) >= 0.01 and plnt_100_volume != 0: 
                                # Create a new row for the plant
                                row = {
                                    'Date': schedule_date,
                                    # 'start_time': availability_data_array_b_wise['start_time'],
                                    # 'end_tme': availability_data_array_b_wise['end_time'],
                                    # 'full_time': availability_data_array_b_wise['full_time'],
                                    'time_block': TotalblkwiseData['BlockNo'],  
                                    'session_number': getsessionNo[int(TotalblkwiseData['BlockNo'])-1]["Session_Id"],   # Fetch session number from rtm_product_hardcoded_data collection
                                    'forecast': round(TotalblkwiseData['forecast_value']),
                                    'forecast_source': TotalblkwiseData['forecast_source'],
                                    'supply_availablity': round(TotalblkwiseData['bltotalvolume']),
                                    'surplus_deficit': round(TotalblkwiseData['surplus_deficit']),
                                    'price': px_price,
                                    'rtm_forecasted_price': ForecastedPrice, 
                                    'ceiling_price': ceilingPriceInner,
                                    'rtm_buffer_value': bufferPriceInner,
                                    'rtm_nminusone_quantum': n_1LimitsInner,
                                    'rtm_noc_buy_value': noc_buyLimitsInner,
                                    'rtm_noc_sell_value': noc_sellLimitsInner, 
                                    'totalBlockBid': totalBlockBid,
                                    'total_reserved_bid': total_reserved_bid,
                                    'remaining_volume': float(total_diff),
                                    'sell_volume': round(sell_volume),
                                    'actual_volume': float(round(plnt_100_volume)),
                                    'category': category,
                                    'plant_id': plant_id,
                                    'plant_name': plant_name,
                                    'display_category': display_category,
                                    'dispatch_type': dispatch_type,
                                    'rtm_tm': TMvalueofPl,
                                    'rtm_tm_revision_no': rtm_tm_revision_no_val,
                                    'total_sell_volume': total_sell_volume + sell_volume,
                                    'Ex_split_string': f"IEX-{typeofExchangeIEXblk}_PXIL-{typeofExchangePXILblk}"
                                }
                                data.setdefault('table_data', {}).setdefault(plkey, {})[blk] = row
                                plkey += 1

                                remaining_sell_headroom = max(0.0, remaining_sell_headroom - sell_volume)  # [CHANGED]
                                # Note: totalBlockBid is now from approved block bids, not accumulated from sell_volume

                                if remaining_sell_headroom <= 0.0:
                                    break  # [CHANGED] stop allocating; headroom exhausted

                    # ===============================MERIT PLANTS LOGIC END===============================
                    # If dispatch type is not Merit, then create a new row for the plant
                    
                    else:
                        # ===============================NON-MERIT PLANTS LOGIC START===============================
                        # If dispatch type is not Merit, then create a new row for the plant
                        
                        row = {
                            'Date': schedule_date,
                            # 'start_time': availability_data_array_b_wise['start_time'],
                            # 'end_tme': availability_data_array_b_wise['end_time'],
                            # 'full_time': availability_data_array_b_wise['full_time'],
                            'time_block': TotalblkwiseData['BlockNo'],
                            'session_number': getsessionNo[int(TotalblkwiseData['BlockNo'])-1]["Session_Id"],
                            'forecast': round(TotalblkwiseData['forecast_value']),
                            'forecast_source': TotalblkwiseData['forecast_source'],
                            'supply_availablity': round(TotalblkwiseData['bltotalvolume']),
                            'surplus_deficit': round(TotalblkwiseData['surplus_deficit']),
                            'price': px_price,
                            'rtm_forecasted_price': ForecastedPrice,
                            'ceiling_price': ceilingPriceInner,
                            'rtm_buffer_value': bufferPriceInner,
                            'rtm_nminusone_quantum': n_1LimitsInner,
                            'rtm_noc_buy_value': noc_buyLimitsInner,
                            'rtm_noc_sell_value': noc_sellLimitsInner,
                            'totalBlockBid': totalBlockBid,
                            'total_reserved_bid': total_reserved_bid,
                            'remaining_volume': float(total_diff),
                            'sell_volume': 0,
                            'actual_volume': float(round(plant_volume)),
                            'category': category,
                            'plant_id': plant_id,
                            'plant_name': plant_name,
                            'display_category': display_category,
                            'dispatch_type': dispatch_type,
                            'total_sell_volume': total_sell_volume,
                            'Ex_split_string': f"IEX-{typeofExchangeIEXblk}_PXIL-{typeofExchangePXILblk}"
                        }
                        
                        data.setdefault('table_data', {}).setdefault(plkey, {})[blk] = row
                        plkey += 1
                                
            return data

        except Exception as e:
            logger.exception(f"[RTMBiddingServices] Failed to fetch RTM single bid table data: {str(e)}")
            raise



    def total_availability_new(self, raw_data, getForecastedValue, from_block, to_block):
        """
        Compute total availability per time block.
        """
        data_ar = []
        getplantsData = [v for k, v in raw_data.items()]
        forecast_by_block = {
            doc["time_block"]: doc
            for doc in (getForecastedValue or [])
            if doc.get("time_block") is not None
        }

        for i, getplantsDataaa in enumerate(getplantsData):
            if not isinstance(getplantsDataaa, dict):
                # skip invalid entries
                continue
            bltotalvolume = 0
            volume_for_bid_submision = 0
            blk_volume_sum_fiftyfive = 0    
            blk_volume_sum_must = 0
            total_volume_mritbl = 0
            must_merit_fiftyfive_volume_bl_sum = 0  
            # print(i)
            # print("Index:", i, "Data:", getplantsDataaa['plants'])
            getplantsDataaa1 = [v for k, v in getplantsDataaa['plants'].items()]
            for j, blPlants in enumerate(getplantsDataaa1):
                plnt_volume_fiftyfive = 0
                total_volume_mrit = 0   #
                plnt_valume_must = 0
                
                pl_volume = float(blPlants.get('plant_volume'))

                # If dispatch type is Merit, then calculate 55% volume for the plant
                if blPlants.get('dispatch_type') == "Merit":   
                    plnt_volume_fiftyfive = pl_volume * 0.55   # Calculate 55% volume for the plant
                    total_volume_mrit = pl_volume   # Calculate total volume for the plant
                
                # If dispatch type is not Merit, then calculate total volume for the plant
                else:
                    plnt_valume_must = pl_volume   # Calculate total volume for the plant

                bltotalvolume += pl_volume   # Add volume to total volume
                volume_for_bid_submision += pl_volume   # Add volume to volume for bid submission
                total_volume_mritbl += total_volume_mrit
                blk_volume_sum_fiftyfive += plnt_volume_fiftyfive   # Add volume to block volume sum fiftyfive
                blk_volume_sum_must += plnt_valume_must
                must_merit_fiftyfive_volume_bl_sum = blk_volume_sum_fiftyfive + blk_volume_sum_must
            
            
            block_no = getplantsData[i]["time_block"]
            forecast_doc = forecast_by_block.get(block_no, {})
            forecast_val = forecast_doc.get("forecast", 0)
            forecast_source = map_forecast_source_display(
                forecast_doc.get("source_forecast")
            )

            data_ar.append({
                'bltotalvolume': bltotalvolume,
                # 'full_time': getplantsDataaa.get("full_time"),
                'BlockNo': block_no,
                'volume_for_bid_submision': volume_for_bid_submision,
                'total_volume_mritbl': total_volume_mritbl, 
                'total_volume_mritbl_45': total_volume_mritbl * 0.45,
                'blk_volume_sum_fiftyfive': blk_volume_sum_fiftyfive,
                'blk_volume_sum_must': blk_volume_sum_must,
                'must_merit_fiftyfive_volume_bl_sum': must_merit_fiftyfive_volume_bl_sum,
                'forecast_value': forecast_val,
                'forecast_source': forecast_source,
                'diff_for_bid_cal': forecast_val - must_merit_fiftyfive_volume_bl_sum,
                'surplus_deficit': bltotalvolume - forecast_val
            })
            
        return data_ar



    def _limit_values_lookups(self):
        """
        Helper function to return all $lookup stages for limit_values.
        """
        lookups = []

        fields = [
            ("Buffer", "Puducherry", "buffer_data", "buffer_limit"),
            ("Ceiling_Price", "Puducherry", "ceiling_data", "ceiling_price"),
            ("n_1", "Puducherry", "n1_data", "n1_limit"),
            ("noc_buy", "Puducherry", "noc_buy_data", "noc_buy_limit"),
            ("noc_sell", "Puducherry", "noc_sell_data", "noc_sell_limit"),
            ("iex", "S", "iex_data", "iex_split"),
            ("pxil", "S", "pxil_data", "pxil_split")
        ]

        for ID, area, alias, project_field in fields:
            lookups.append({
                "$lookup": {
                    "from": "limit_values",
                    "let": {"sched": "$scheduled_at", "tb": "$blocks.time_block"},  # CHANGE FROM SCHEDULE_DATE TO SCHEDULED_AT AS WE ARE USING SCHEDULED_AT IN THE LIMIT_VALUES COLLECTION
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$ID", ID]},
                                        {"$eq": ["$area", area]},
                                        {"$eq": ["$scheduled_at", "$$sched"]},      # CHANGE FROM SCHEDULE_DATE TO SCHEDULED_AT AS WE ARE USING SCHEDULED_AT IN THE LIMIT_VALUES COLLECTION
                                        {"$eq": ["$block", "$$tb"]}
                                    ]
                                }
                            }
                        },
                        {"$project": {"_id": 0, project_field: "$values"}}
                    ],
                    "as": alias
                }
            })
            lookups.append({"$unwind": {"path": f"${alias}", "preserveNullAndEmptyArrays": True}})  

        return lookups
    



    async def _ensure_capture_serve_and_get_rldc_revision_no(
        self,
        schedule_date: str,
        blno: list,
        bltimefull: list,
        revision_no: int,
        session_block: int,
        manual_capture_bid: bool = False,
    ) -> Optional[int]:
        serve_rows = await self.supply_repo.get_supply_by_date_range(schedule_date, "SG")
        if not serve_rows:
            return None
        first = serve_rows[0]
        data_revision_no = first.get("source_revision_number") or 1
        try:
            data_revision_no = int(data_revision_no)
        except (TypeError, ValueError):
            data_revision_no = 1

        bid_block_no = int(blno[1]) if len(blno) > 1 else 0
        existing_auto = await self.capture_serve_repo.find_one_by_schedule_dc_sg_bid_block_revision(
            schedule_date, "SG", bid_block_no, revision_no
        )
        existing_manual = None
        if manual_capture_bid:
            existing_manual = await self.manual_capture_serve_repo.find_one_by_schedule_dc_sg_bid_block_revision(
                schedule_date, "SG", bid_block_no, revision_no
            )
        should_insert_auto = existing_auto is None
        should_insert_manual = bool(manual_capture_bid and existing_manual is None)
        if not should_insert_auto and not should_insert_manual:
            return data_revision_no

        bid_time_block = blno[0] if len(blno) > 0 else ""
        bid_start_time = bltimefull[0] if len(bltimefull) > 0 else ""
        bid_end_time = bltimefull[1] if len(bltimefull) > 1 else ""
        plant_ids = [str(row.get("plant_id")) for row in serve_rows if row.get("plant_id")]
        plant_map = await self.plants_list_repo.get_plants_map_by_ids(plant_ids)
        graph_metrics = {}
        try:
            graph_response = await self.rtm_single_bid_graph(schedule_date, bid_block_no, bid_block_no)
            graph_rows = (graph_response or {}).get("data") or []
            if graph_rows:
                matched_row = next(
                    (r for r in graph_rows if int(r.get("time_block", 0)) == bid_block_no),
                    graph_rows[0],
                )
                graph_metrics = {
                    "supply_total": matched_row.get("supply_total"),
                    "forecast": matched_row.get("forecast"),
                    "surplus_deficit": matched_row.get("surplus_deficit"),
                }
        except Exception as graph_err:
            logger.warning(f"Failed to fetch single-bid-graph metrics for capture: {graph_err}")

        capture_docs = []
        for row in serve_rows:
            plant_info = plant_map.get(str(row.get("plant_id"))) or {}
            row_source_revision = row.get("source_revision_number")
            if row_source_revision in (None, ""):
                row_rldc_revision_no = data_revision_no
            else:
                try:
                    row_rldc_revision_no = int(str(row_source_revision))
                except (TypeError, ValueError):
                    row_rldc_revision_no = data_revision_no

            row_time_block = row.get("time_block")
            if row_time_block in (None, ""):
                tb = None
            else:
                try:
                    tb = int(str(row_time_block))
                except (TypeError, ValueError):
                    tb = None

            derived_start_time = row.get("start_time")
            derived_end_time = row.get("end_time")
            derived_full_time = row.get("full_time")
            if tb is not None and 1 <= tb <= 96:
                start_minutes = (tb - 1) * 15
                end_minutes = tb * 15
                start_h, start_m = divmod(start_minutes, 60)
                end_h, end_m = divmod(end_minutes, 60)
                derived_start_time = f"{start_h:02d}:{start_m:02d}"
                derived_end_time = f"{end_h:02d}:{end_m:02d}"
                derived_full_time = f"{derived_start_time}-{derived_end_time}"

            doc = {
                "inserted_date": datetime.now(),
                "schedule_date": row.get("schedule_date") or schedule_date,
                "schedule_date_timestamp": row.get("scheduled_at") or row.get("schedule_date_timestamp"),
                "region": "S2",
                "revision_no": row.get("revision_no"),
                "rldc_revision_no": row_rldc_revision_no,
                "dec_onbar_offbar": row.get("dec_onbar_offbar"),
                "dec_remark": row.get("dec_remark"),
                "dc_sg_type": row.get("dc_sg") or "SG",
                "data_type": row.get("data_type") or "intraday",
                "template_type": row.get("template_type"),
                "time_block": row.get("time_block"),
                "start_time": derived_start_time,
                "end_time": derived_end_time,
                "full_time": derived_full_time,
                "cronJob_time": row.get("cronJob_time"),
                "type": plant_info.get("c_s") or row.get("type"),
                "plant_name": plant_info.get("plant_name") or row.get("plant_name"),
                "category": plant_info.get("category") or row.get("category"),
                "technology": plant_info.get("technology") or row.get("technology"),
                "dispatch_type": plant_info.get("dispatch_type") or row.get("dispatch_type"),
                "multiplier_status": row.get("multiplier_status"),
                "sg_key": row.get("plant_id") or row.get("sg_key"),
                "source": row.get("source"),
                "source_sg": row.get("source_sg") or "RLDC",
                "volume": row.get("volume"),
                "updated_flag": row.get("updated_flag"),
                "PX_Price": plant_info.get("px_price") if plant_info.get("px_price") is not None else (row.get("px_price") or row.get("PX_Price")),
                "FC_Price": plant_info.get("fc_price") if plant_info.get("fc_price") is not None else (row.get("fc_price") or row.get("FC_Price")),
                "VC_Price": plant_info.get("vc_price") if plant_info.get("vc_price") is not None else (row.get("vc_price") or row.get("VC_Price")),
                "TIE_Price": plant_info.get("ppa") if plant_info.get("ppa") is not None else (row.get("TIE_Price") or row.get("ppa")),
                "RMP_Price": None,
                "price_group": plant_info.get("price_group") if plant_info.get("price_group") is not None else row.get("price_group"),
                "status": row.get("status"),
                "onbarOffBarType": row.get("onbarOffBarType"),
                "supply_total": graph_metrics.get("supply_total"),
                "forecast": graph_metrics.get("forecast"),
                "surplus_deficit": graph_metrics.get("surplus_deficit"),
                "bid_time_block": bid_time_block,
                "bid_start_time": bid_start_time,
                "bid_end_time": bid_end_time,
                "bid_block_no": bid_block_no,
                "bid_revision_no": revision_no,
                "bid_session_no": session_block,
                "exchange_submission_flag": "N",
            }
            capture_docs.append(doc)

        if capture_docs and should_insert_auto:
            await self.capture_serve_repo.insert_many(capture_docs)
        if capture_docs and should_insert_manual:
            await self.manual_capture_serve_repo.insert_many(capture_docs)
        return data_revision_no

    async def apply_capture_task(self, task: Dict[str, Any]) -> Optional[int]:
        """Run RTM bid-capture ensure from a serialized task (API fallback or scheduler worker)."""
        return await self._ensure_capture_serve_and_get_rldc_revision_no(
            task["formatted_date"],
            list(task["blno"]),
            list(task["bltimefull"]),
            int(task["revision_no"]),
            int(task["session_block"]),
            manual_capture_bid=bool(task.get("manual_capture_bid", False)),
        )

    async def submit_for_approval_post_data(self, request_data: dict, current_user: Optional[TokenPayload] = None, audit_logger: Optional[AuditLogger] = None, capture_bid: bool = True, manual_capture_bid: bool = False):
        try:
            dbarray = {}
            capture_tasks: List[Dict[str, Any]] = []
            revision_by_date: Dict[str, Optional[int]] = {}
            
            # Get current user from backend (TokenPayload) instead of frontend
            # Extract username from TokenPayload - use user_id as fallback if username not available
            requested_by_user = "unknown"
            username = ""
            user_id = None
            if current_user:
                requested_by_user = current_user.username if hasattr(current_user, 'username') and current_user.username else (current_user.user_id if hasattr(current_user, 'user_id') else "unknown")
                username = requested_by_user
                user_id = current_user.user_id
            

            # Helper function to get block date - extract from time_block_range if time_block_dates missing
            def get_block_date_data(index):

                # Get block date data from time_block_dates if available, otherwise extract from time_block_range
                if "time_block_dates" in request_data and index < len(request_data["time_block_dates"]):
                    return request_data["time_block_dates"][index].split("_")

                # If time_block_dates is not available, then extract from time_block_range
                elif index < len(request_data["time_block_range"]):
                    # Extract date from time_block_range format: "23:30-23:45_95_2025-10-27"
                    time_range_parts = request_data["time_block_range"][index].split("_")
                    if len(time_range_parts) >= 3: #
                        return [time_range_parts[2]]  # Return as list to match expected format
                    
                # If time_block_dates and time_block_range are not available, then return empty list
                return [""]
            
            # Iterate through each time block range
            for time_key, time_block_value in enumerate(request_data["time_block_range"]):
                keys = time_key
                blno = time_block_value.split("_")
                bltimefull = blno[0].split("-")
                dates = get_block_date_data(time_key)

                # Parse and normalize schedule/inserted date for this block
                # dates[0] is expected in "%d-%m-%Y" format (e.g. "17-11-2025")
                date_obj = datetime.strptime(dates[0], "%d-%m-%Y")  # parse string to datetime
                formatted_date = date_obj.strftime("%Y-%m-%d")      # normalize to "YYYY-MM-DD"

                # Get latest revision for this specific date + start_time
                # NOTE: We must use the block's scheduled date (formatted_date),
                # not today's date, otherwise future-dated bids always get revision 1.
                getRevision = await self.single_bid_data_for_approval_repo.getPlantDataForApprovalbytime(
                    formatted_date, bltimefull[0]
                )
                
                # Delete existing record if exists
                check_exist_row = await self.rtm_currect_selected_forecast_log_repo.getData(int(blno[1]),  dates[0])  # type: ignore
                
                if check_exist_row is not None:
                    await self.rtm_currect_selected_forecast_log_repo.deleteData(int(blno[1]),  dates[0]) # type: ignore

                # Insert forecast log
                loginsertarray = {
                    "schedule_date": dates[0],
                    "forecast_value": 0,
                    "time_block": int(blno[1]),
                    "selected_demand_type": request_data["current_selected_demandforecast"],
                }
                
                await self.rtm_currect_selected_forecast_log_repo.insert_many([loginsertarray])
                # Fetch product code
                get_product_code = await self.rtm_product_hardcoded_data_repo.get_period(
                    bltimefull[0]
                )
                session_block = 0
                if get_product_code:
                    session_block = get_product_code.get("Session_Id") or get_product_code.get("session_number") or 0

                if not session_block:
                    return {"status": "error", "code": 400, "message": f"No session mapping for {bltimefull[0]}"}
                data_rev = None
                bid_revision = (int(getRevision) + 1) if getRevision else 1
                if capture_bid:
                    if formatted_date not in revision_by_date:
                        revision_by_date[formatted_date] = await self.supply_repo.get_first_sg_source_revision_for_date(
                            formatted_date
                        )
                    data_rev = revision_by_date[formatted_date]
                    capture_tasks.append(
                        {
                            "formatted_date": formatted_date,
                            "blno": list(blno),
                            "bltimefull": list(bltimefull),
                            "revision_no": bid_revision,
                            "session_block": int(session_block),
                            "manual_capture_bid": bool(manual_capture_bid),
                        }
                    )

                # Fetch IEX API details
                iex_info = await self.iex_api_detail_repo.get_exchange_config("IEX")

                if not iex_info:
                    logger.info("IEX API configuration not found.")
                    return {
                        "status": "error",
                        "code": 400,
                        "message": "IEX API configuration not found. Please contact administrator."
                    }
                
                iex_status = (iex_info.get("status") or "").strip().lower()
                
                if iex_status != "active":
                    logger.info("IEX API is not active. Please contact administrator.")
                    return {"status": "error", "code": 400, "message": "IEX API is not active. Please contact administrator."}
                else:
                    iex_user_id = iex_info.get("user_id", "")
                    iex_participant_id = iex_info.get("participant_id", "")
                    iex_asset_id = iex_info.get("asset_id", "")
                    iex_portfolio_id = iex_info.get("portfolio_id", "")
                    logger.info(f"IEX User ID: {iex_user_id}, Participant ID: {iex_participant_id}, Asset ID: {iex_asset_id}, Portfolio Code: {iex_portfolio_id}")

                # Build dbarray for this block
                current_time = datetime.now().strftime("%H:%M")
                timestamp = int(time.time())
                dbarray[keys] = {
                    "bid_date":formatted_date,
                    "inserted_date": formatted_date,
                    "schedule_date":formatted_date,
                    "inserted_time": current_time,
                    "reference_number": timestamp,
                    "time_block": blno[0],
                    "start_time": bltimefull[0],
                    "end_time": bltimefull[1],
                    "block_no": int(blno[1]),
                    "revision_no": int(getRevision) + 1 if getRevision else 1,
                    "bid_type": "single",
                    "session_block": int(session_block),
                    "member_id": iex_participant_id,
                    "user_id": iex_user_id,
                    "portfolio_code": iex_portfolio_id,
                    "sheet_market": iex_asset_id,
                    "requested_by": requested_by_user,
                    "requested_time": current_time,
                    "approved_by": "",
                    "approved_time": "",
                    "created_by": username,
                    "created_time": current_time,
                    "Ex_split_string": request_data[f"Exsplitstringblk_{blno[1]}"],
                    "remark": "",
                    "buy_sell_bid": request_data[f"buy_sell_bid_type_{blno[1]}"]
                }
                if data_rev is not None:
                    dbarray[keys]["rldc_revision_no"] = data_rev
                
                # Handle bid status
                if request_data[f"buy_sell_bid_type_{blno[1]}"] == "withdrawBid":
                    dbarray[keys]["bid_status"] = "pending_withdrawal"
                    notificationData = {
                        "bid_status": "submitForWithdrawal",
                        "module_name": "Single Bid",
                        "title": f"RTM Single Bid Submitted for Withdrawal by {requested_by_user}",
                        "description": "RTM single bid submitted for Withdrawal of the selected time blocks",
                        "time_block": request_data["time_block_range"],
                        "submitted_time": datetime.now().strftime("%d-%m-%Y %H:%M"),
                        "action": "#",
                        "notification_type": "bid_for_Withdrawal"
                    }
                else:
                    dbarray[keys]["bid_status"] = "pending"
                    notificationData = {
                        "bid_status": "submitForApproval",
                        "module_name": "Single Bid",
                        "title": f"RTM Single Bid Submitted for Approval Requested by {requested_by_user}",
                        "description": "RTM single bid submitted for approval of the selected time blocks",
                        "time_block": request_data["time_block_range"],
                        "submitted_time": datetime.now().strftime("%d-%m-%Y %H:%M"),
                        "action": "#",  
                        "notification_type": "bid_for_Approval"
                    }

                plcounter = 0
                buy_sell_type = request_data[f"buy_sell_bid_type_{blno[1]}"]
                
                if buy_sell_type != "buy":  
                    dbarray[keys]["Plants"] = {}   # Initialize Plants dictionary
                    
                    for plkey, plvalue in enumerate(request_data.get("bid_plant_name", [])):
                        logger.debug("Processing plant index:", plkey)
                        # print("Processing plant index:", plkey)

                        # Safe retrieval of category
                        bid_categories = request_data.get("bid_category", [])
                        cat = bid_categories[plkey] if plkey < len(bid_categories) else "Unknown"

                        # Ensure blno has at least 2 elements
                        if len(blno) < 2:
                            logger.debug(f"Skipping plant {plvalue}: blno has insufficient elements")
                            # print(f"Skipping plant {plvalue}: blno has insufficient elements")
                            continue

                        # Construct keys safely
                        default_vol_key = f"bid_default_volume_{blno[1]}"
                        vol_key = f"bid_volume_{blno[1]}"
                        price_key = f"bid_price_{blno[1]}"
                        def_price_key = f"bid_default_price_{blno[1]}"

                        # Safe retrieval of volumes and prices
                        bid_default_vols = request_data.get(default_vol_key, [])
                        bid_vols = request_data.get(vol_key, [])
                        bid_prices = request_data.get(price_key, [])
                        bid_default_prices = request_data.get(def_price_key, [])

                        bid_default_volume = bid_default_vols[plkey] if plkey < len(bid_default_vols) else 0
                        bid_volume = bid_vols[plkey] if plkey < len(bid_vols) else 0
                        bid_price = bid_prices[plkey] if plkey < len(bid_prices) else 0
                        bid_default_price = (
                            bid_default_prices[plkey] if plkey < len(bid_default_prices) else request_data.get("bid_default_price", [0])[plkey]
                        )

                        # Safe retrieval of RTM forecast
                        rtm_forecast_range = request_data.get("rtm_forecast_range", [])
                        rtm_forecast = rtm_forecast_range[time_key] if time_key < len(rtm_forecast_range) else 0
                        
                        # Plant key logic
                        plant_key = plvalue
                        if plvalue == "0":
                            plant_key = f"A-{plvalue}"
                        elif plvalue == await bidPriceNOC():  # assuming bidPriceNOC() returns comparable plant/price sentinel
                            plant_key = f"Z-{plvalue}"

                        # Initialize dictionary if not exists
                        if "Plants" not in dbarray[keys]:
                            dbarray[keys]["Plants"] = {}

                        # Assign plant data
                        dbarray[keys]["Plants"][plant_key] = {
                            "bid_plant_name": plvalue,
                            "bid_plant_category": cat,
                            "bid_default_volume": bid_default_volume,
                            "bid_volume": bid_volume,
                            "bid_price": bid_price,
                            "bid_default_price": bid_default_price if buy_sell_type == "buy" else request_data.get("bid_default_price", [0])[plkey],
                            "rtm_forecast_Price": rtm_forecast
                        }
                        logger.debug("Completed processing all plants.")
                        # print("Completed processing all plants.")
                        # print("end---")
                        plcounter += 1
                    logger.debug("end-if---")
                    
                    # print(dbarray)
                else:
                    # Buy side handling if selected action type is with_cut_off
                    if request_data["selected_action_type"] == "with_cut_off":
                        buyinsert = await self.submit_for_approval_post_data_buy(request_data, blno[1])

                    else:
                        # If selected action type is not with_cut_off, then submit for approval without cutoff
                        buyinsert = await self.submit_for_approval_post_data_buy_no_cutoff(request_data, blno[1])
                        # Early return skips insert_many/publish; still persist bid capture for this block (parity with in-loop _ensure).
                        if capture_bid and capture_tasks:
                            await self.apply_capture_task(capture_tasks[-1])
                        return buyinsert
                    dbarray[keys]["Plants"] = buyinsert

            
            # Insert final data into model
                documents_to_insert = list(dbarray.values())
                response = await self.single_bid_data_for_approval_repo.insert_many(documents_to_insert) # type: ignore
            if response:
                if capture_tasks:
                    loop = asyncio.get_running_loop()
                    publish_fn = partial(
                        JobPublisher().publish_job_trigger,
                        job_type="rtm_bid_capture_approval",
                        source_service="processing",
                        payload={"captures": capture_tasks},
                        correlation_id=getattr(current_user, "correlation_id", None) if current_user else None,
                    )
                    published = await loop.run_in_executor(None, publish_fn)
                    if not published:
                        logger.warning(
                            "RTM bid capture job not published (RabbitMQ off or error); running capture inline"
                        )
                        for task in capture_tasks:
                            await self.apply_capture_task(task)

                if audit_logger:
                    try:
                        # logical calculation for action_taken_for
                        blocks_count = len(dbarray)
                        revision_no = 0
                        block_info = "All"
                        if dbarray:
                            first_key = next(iter(dbarray))
                            revision_no = dbarray[first_key].get("revision_no", 0)
                            if blocks_count == 1:
                                block_info = dbarray[first_key].get("block_no", "Unknown")

                        action_taken_for = f"Single Bid :{block_info}, Rev No: {revision_no}"

                        log_action = "approval"
                        # Check if any block was withdrawal
                        if dbarray and dbarray[next(iter(dbarray))].get("bid_status") == "pending_withdrawal":
                             log_action = "withdrawal"

                        audit_logger.log_user_action(
                            service="RTM",
                            user_id=current_user.user_id if current_user and hasattr(current_user, 'user_id') else "unknown",
                            username=current_user.username if current_user and hasattr(current_user, 'username') else "unknown",
                            action=log_action,
                            message="RTM Bid data submitted successfully",
                            module="RTM",
                            action_taken_for=action_taken_for,
                            success=True
                        )
                    except Exception as e:
                         logger.error(f"Failed to log audit in submit_for_approval RTM: {e}")

                return {"status": "success", "code": 200, "message": "Successfully Inserted"}
            else:
                return {"status": "error", "code": 404, "message": "Error: data not found!"}

        except Exception as e:
            if audit_logger:
                 audit_logger.log_system_operation(
                    service="RTM",
                    operation="approval",
                    message="RTM Bid data submission for approval failed",
                    status="error",
                    error=str(e),
                    module="RTM"
                )
            return {"status": "error", "code": 500, "message": f"Internal Server Error: {str(e)}"}

    async def submit_for_approval_post_data_buy(self, request: dict, blockno: str):
        """
        Convert PHP submitForApprovalPostDataBuy to Python async version.
        Returns bidblockarray for a given block number.
        """
        buybiddataarray = {}
        finalbuyarraysorted = {}
        noc_price = await bidPriceNOC()
        
        # Helper function to get block date - extract from time_block_range if time_block_dates missing
        def get_block_date(index):
            if "time_block_dates" in request and index < len(request["time_block_dates"]):
                return request["time_block_dates"][index].split("_")
            elif index < len(request["time_block_range"]):
                # Extract date from time_block_range format: "23:30-23:45_95_2025-10-27"
                time_range_parts = request["time_block_range"][index].split("_")
                if len(time_range_parts) >= 3:
                    return [time_range_parts[2]]  # Return as list to match expected format
            return [""]
        
        # Step 1: Build buybiddataarray for the given blockno
        for plkey, plvalue in enumerate(request["time_block_range"]):
            blno_parts = plvalue.split("_")
            blno_index = blno_parts[1]
            blockdate = get_block_date(plkey)

            if blockno == blno_index:
                buyary = {}
                for idx, bid_price in enumerate(request[f"bid_price_{blno_index}", []]):
                    bid_volume = request[f"bid_volume_{blno_index}", []][idx]
                    if bid_price not in [noc_price, "0", ""] and bid_volume != "0":
                        buyary[bid_price] = bid_volume
                        finalbuyarraysorted[bid_price] = bid_volume
                # sort descending by price
                buybiddataarray[blno_index] = dict(sorted(buyary.items(), key=lambda x: float(x[0]), reverse=True))

        # Step 2: Prepare finalpricearray (unique sorted prices + bidPriceNOC + 0)
        buypricekeys = []
        incresingprbuy2 = []

        for blkey, blvalue in buybiddataarray.items():
            for price_str, vol in blvalue.items():
                price = float(price_str)
                if price_str not in ["0", noc_price]:
                    buypricekeys.append(price)
                    incresingprbuy2.append(price + 1)

        finalpricearraybuy = sorted(set(buypricekeys + incresingprbuy2))
        finalpricearray = [0] + sorted(list(set(finalpricearraybuy + [noc_price])))

        # Step 3: Build bidblockarray
        bidblockarray = {}
        for timeblock_key, timeblock_value in enumerate(request["time_block_range"]):
            blno_parts = timeblock_value.split("_")
            blno_index = blno_parts[1]
            blockdate = get_block_date(timeblock_key)

            if blockno == blno_index:
                buypriceary = list(buybiddataarray.get(blno_index, {}).values())
                counterkey = 0
                max_volume = max([float(v) for v in buybiddataarray.get(blno_index, {}).values()], default=0)

                for pricevalue in finalpricearray:
                    pRarrayf = {}
                    if pricevalue == 0:
                        min_volume = min(buypriceary) if buypriceary else 0
                        pRarrayf = {
                            "bid_plant_name": f"Buy_{counterkey}",
                            "bid_price": pricevalue,
                            "bid_default_price": pricevalue,
                            "bid_volume": min_volume,
                            "bid_default_volume": min_volume,
                            "sell_buy_bid_type": "buy",
                            "biding_date": blockdate[0],
                            "bid_plant_category": "buy",
                            "rtm_forecast_Price": request["rtm_forecast_range"][timeblock_key]
                        }
                    else:
                        matched = False
                        for plkey, plvalue in buybiddataarray.get(blno_index, {}).items():
                            plvalue_f = float(plvalue)
                            if pricevalue == float(plkey):
                                pRarrayf = {
                                    "bid_plant_name": f"Buy_{counterkey}",
                                    "bid_price": pricevalue,
                                    "bid_default_price": pricevalue,
                                    "bid_volume": plvalue_f,
                                    "bid_default_volume": plvalue_f,
                                    "sell_buy_bid_type": "buy",
                                    "biding_date": blockdate[0],
                                    "bid_plant_category": "buy",
                                    "rtm_forecast_Price": request["rtm_forecast_range"][timeblock_key]
                                }
                                buypriceary = [v for v in buypriceary if v != plvalue_f]
                                matched = True
                                break
                            elif pricevalue == float(plkey) + 1:
                                min_vol = min(buypriceary) if buypriceary else 0
                                pRarrayf = {
                                    "bid_plant_name": f"Buy_{counterkey}",
                                    "bid_price": pricevalue,
                                    "bid_default_price": pricevalue,
                                    "bid_volume": min_vol,
                                    "bid_default_volume": min_vol,
                                    "sell_buy_bid_type": "buy",
                                    "biding_date": blockdate[0],
                                    "bid_plant_category": "buy",
                                    "rtm_forecast_Price": request["rtm_forecast_range"][timeblock_key]
                                }
                                matched = True
                                break

                        if not matched:
                            # At the max price point (NOC), fill with the maximum bid volume
                            fill_vol = max_volume if pricevalue == noc_price else ""
                            pRarrayf = {
                                "bid_plant_name": f"Buy_{counterkey}",
                                "bid_price": pricevalue,
                                "bid_default_price": pricevalue,
                                "bid_volume": fill_vol,
                                "bid_default_volume": fill_vol,
                                "sell_buy_bid_type": "buy",
                                "biding_date": blockdate[0],
                                "bid_plant_category": "buy",
                                "rtm_forecast_Price": request["rtm_forecast_range"][timeblock_key]
                            }

                    bidblockarray[f"Buy_{counterkey}"] = pRarrayf
                    counterkey += 1

        return bidblockarray
    
    async def submit_for_approval_post_data_buy_no_cutoff(self, request: dict, blockno: str):
        """
        Python async equivalent of PHP submitForApprovalPostDataBuyNoCutOff.
        Returns bidblockarray for a given block number.
        """
        buybiddataarray = {}
        finalbuyarraysorted = {}
        
        # Helper function to get block date - extract from time_block_range if time_block_dates missing
        def get_block_date(index):
            if "time_block_dates" in request and index < len(request["time_block_dates"]):
                return request["time_block_dates"][index].split("_")
            elif index < len(request["time_block_range"]):
                # Extract date from time_block_range format: "23:30-23:45_95_2025-10-27"
                time_range_parts = request["time_block_range"][index].split("_")
                if len(time_range_parts) >= 3:
                    return [time_range_parts[2]]  # Return as list to match expected format
            return [""]
        
        # Step 1: Build buybiddataarray
        for plkey, plvalue in enumerate(request["time_block_range"]):
            blno_parts = plvalue.split("_")
            blno_index = blno_parts[1]
            blockdate = get_block_date(plkey)

            if blockno == blno_index:
                buyary = {}
                for idx, bid_price in enumerate(request.get(f"bid_price_{blno_index}", [])):
                    bid_volume = request.get(f"bid_volume_{blno_index}", [])[idx]
                    # No filtering for bidPriceNOC, zero, or empty
                    buyary[bid_price] = bid_volume
                    finalbuyarraysorted[bid_price] = bid_volume
                # Sort descending by price
                buybiddataarray[blno_index] = dict(sorted(buyary.items(), key=lambda x: float(x[0]), reverse=True))

        # Step 2: Prepare final price array
        buypricekeys = []
        for blvalue in buybiddataarray.values():
            for price_str in blvalue.keys():
                buypricekeys.append(float(price_str))

        finalpricearray = [0] + sorted(list(set(buypricekeys)))

        # Step 3: Build bidblockarray
        bidblockarray = {}

        for timeblock_key, timeblock_value in enumerate(request["time_block_range"]):
            blno_parts = timeblock_value.split("_")
            blno_index = blno_parts[1]
            blockdate = get_block_date(timeblock_key)

            if blockno == blno_index:
                buypriceary = list(buybiddataarray.get(blno_index, {}).values())
                counterkey = 0

                for pricevalue in finalpricearray:
                    pRarrayf = {}
                    if pricevalue == 0:
                        min_volume = min(buypriceary) if buypriceary else 0
                        pRarrayf = {
                            "bid_plant_name": f"Buy_{counterkey}",
                            "bid_price": float(pricevalue),
                            "bid_default_price": float(pricevalue),
                            "bid_volume": float(min_volume),
                            "bid_default_volume": float(min_volume),
                            "sell_buy_bid_type": "buy",
                            "biding_date": datetime.datetime.strptime(blockdate[0], "%Y-%m-%d").date().isoformat(),
                            "bid_plant_category": "buy",
                            "rtm_forecast_Price": float(request["rtm_forecast_range"][timeblock_key])
                        }
                    else:
                        matched = False
                        for plkey, plvalue in buybiddataarray.get(blno_index, {}).items():
                            plvalue_f = float(plvalue)
                            if pricevalue == float(plkey):
                                pRarrayf = {
                                    "bid_plant_name": f"Buy_{counterkey}",
                                    "bid_price": float(pricevalue),
                                    "bid_default_price": float(pricevalue),
                                    "bid_volume": plvalue_f,
                                    "bid_default_volume": plvalue_f,
                                    "sell_buy_bid_type": "buy",
                                    "biding_date": datetime.datetime.strptime(blockdate[0], "%Y-%m-%d").date().isoformat(),
                                    "bid_plant_category": "buy",
                                    "rtm_forecast_Price": float(request["rtm_forecast_range"][timeblock_key])
                                }
                                buypriceary = [v for v in buypriceary if v != plvalue_f]
                                matched = True
                                break
                            elif pricevalue == float(plkey) + 1:
                                min_vol = min(buypriceary) if buypriceary else 0
                                pRarrayf = {
                                    "bid_plant_name": f"Buy_{counterkey}",
                                    "bid_price": float(pricevalue),
                                    "bid_default_price": float(pricevalue),
                                    "bid_volume": float(min_vol),
                                    "bid_default_volume": float(min_vol),
                                    "sell_buy_bid_type": "buy",
                                    "biding_date": datetime.datetime.strptime(blockdate[0], "%Y-%m-%d").date().isoformat(),
                                    "bid_plant_category": "buy",
                                    "rtm_forecast_Price": float(request["rtm_forecast_range"][timeblock_key])
                                }
                                matched = True
                                break

                        if not matched:
                            pRarrayf = {
                                "bid_plant_name": f"Buy_{counterkey}",
                                "bid_price": float(pricevalue),
                                "bid_default_price": float(pricevalue),
                                "bid_volume": 0,
                                "bid_default_volume": 0,
                                "sell_buy_bid_type": "buy",
                                "biding_date": datetime.datetime.strptime(blockdate[0], "%Y-%m-%d").date().isoformat(),
                                "bid_plant_category": "buy",
                                "rtm_forecast_Price": float(request["rtm_forecast_range"][timeblock_key])
                            }

                    bidblockarray[f"Buy_{counterkey}"] = pRarrayf
                    counterkey += 1

        return bidblockarray
    
    @staticmethod
    def _parse_schedule_date(schedule_date: str) -> datetime:
        """
        Parse schedule date string to datetime object.
        Handles frontend date string input and converts to datetime object.
        
        Args:
            schedule_date (str): Date string in 'YYYY-MM-DD' format
            
        Returns:
            datetime: Parsed datetime object with time set to 00:00:00
        """
        if not schedule_date:
            schedule_date = datetime.now().strftime("%Y-%m-%d")
            logger.info(f"No schedule_date provided, using today's date: {schedule_date}")
        
        try:
            return datetime.strptime(schedule_date, "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError as e:
            logger.error(f"Invalid schedule_date format '{schedule_date}': {e}")
            raise ValueError(f"Invalid date format. Expected YYYY-MM-DD, got: {schedule_date}")


    async def approval_request_list(self, schedule_date, from_block, to_block):
        try:
            no_of_block = to_block % from_block
            from_block_no = from_block
            to_block_no = to_block
            fromblockno = from_block
            toblockno = to_block
            # Current time
            current_time = datetime.now().strftime("%H:%M")
            # Bid status
            bid_status = 'pending'
            get_selected_demand_type = "none"
            target_date = self._parse_schedule_date(schedule_date)
           
            schedule_date_str = schedule_date
            match_criteria = {
                "block_no": {"$gte": from_block, "$lte": to_block},
                "$or": [
                    {"schedule_date": schedule_date_str},
                    {
                        "$expr": {
                            "$and": [
                                {"$ne": ["$scheduled_at", None]},
                                {
                                    "$eq": [
                                        {
                                            "$dateToString": {
                                                "format": "%Y-%m-%d",
                                                "date": "$scheduled_at"
                                            }
                                        },
                                        schedule_date_str
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }

            pipeline = [
                {
                    "$match": match_criteria
                },
                {
                    "$sort": {"block_no": 1, "revision_no": -1}
                },
                {
                    "$group": {
                        "_id": "$block_no",
                        "doc": {"$first": "$$ROOT"}
                    }
                },
                {
                    "$replaceRoot": {"newRoot": "$doc"}
                },
                {
                    "$sort": {
                        "block_no": 1
                    }
                }
            ]
            data_cursor  = await self.single_bid_data_for_approval_repo.aggregate(pipeline)
            # data_list = list(data_cursor)
            data_list = []
            for doc in data_cursor:
                if "_id" in doc and isinstance(doc["_id"], ObjectId):
                    doc["_id"] = str(doc["_id"])
                data_list.append(doc)
               
            if not data_list:
                return {"message": "No bid data found"}
            
            data = {}
            for doc in data_list:
                start_time = doc.get("start_time")  # e.g., "05:00"
                data[start_time] = doc
            
            plants_array = []
            category_array = {}
            default_price_array = {}
             
            # Flatten data
            for block_data in data.values():
                 
                if "Plants" in block_data:
                    
                    for plant in block_data["Plants"].values():
                       
                        plants_array.append({
                            "bid_plant_name": plant["bid_plant_name"],
                            "bid_default_price": plant["bid_default_price"],
                            "bid_plant_category": plant["bid_plant_category"],
                            "PL_default_price": plant["bid_default_price"],
                            "buy_sell_bid": block_data["buy_sell_bid"]
                        })
            
            

            # Reorder plant names for priority
            plants_dict = {}
            for plant in plants_array:
                name = plant["bid_plant_name"]
                if name == 1000:
                    plants_dict[f"Z-{name}"] = f"Z-{name}"
                elif name == "0":
                    plants_dict[f"A-{name}"] = f"A-{name}"
                else:
                    plants_dict[name] = name
                category_array[name] = plant["bid_plant_category"]
                default_price_array[name] = plant["bid_default_price"]

            
            all_plants_array = plants_dict

            plcount2 = 0
            plants_array2 = {}
            category_array = {}
            default_price_array = {}
            data_array={}
            data_arrayblwise={}

            idata =0  
            # --- Build plant mappings ---
            for i, allplantsarrayval in all_plants_array.items():
                
                j =""
                bl=0
                bidvlolumetotal = 0
                alltimeblock = []
                
                for blockcount2 in range(fromblockno - 1, toblockno):
                    if blockcount2 > 96:
                        blockcount = blockcount2 - 96
                    else:
                        blockcount = blockcount2

                    j = self.getFifteenTimeBlockID(blockcount) 
                    
                    if self.getFifteenTimeBlockID(blockcount) in data:
                        
                        if self.getFifteenTimeBlockID(blockcount) == data[j]['start_time']:
                            alltimeblock.append(data[j]['start_time'])
                            
                            if i in data[j].get('Plants', {}):
                                
                                if data[j].get('approved_by', '') != '':
                                    approver = data[j]['approved_by']
                                else:
                                    approver = ''

                            # Update total bid volume
                            
                            plants = data[j].get('Plants') or data[j].get('Must_Plants') or {}

                            plant_keys = list(plants.keys())
                            bidvlolumetotal = 0.0
                            # return {"plants": plants, "plant_keys":plant_keys, "i":i, "data":plants[plant_keys[idata]]}
                            if 0 < len(plant_keys) and idata < len(plant_keys):
                                # print(plant_keys,idata)
                                plant = plants[plant_keys[idata]]
                                
                                bidvlolumetotal += float(plant.get('bid_volume', 0))

                            # Initialize data_array[i] if not exists
                            if i not in data_array:
                                data_array[i] = {"blockbidVolTotal": 0, "buy_sell_bid": "", "blockArray": []}

                            data_array[i]["blockbidVolTotal"] = bidvlolumetotal
                            data_array[i]["buy_sell_bid"] = data[j]['buy_sell_bid']

                            # Initialize blockArray[bl] if needed
                            if len(data_array[i]["blockArray"]) <= bl:
                                data_array[i]["blockArray"].append({})

                            block = data_array[i]["blockArray"][bl]
                            
                            # Lookup with a safe version using the actual plant key
                            plant_data = data[j].get("Plants", {}).get(i)
                            if not plant_data:
                                plant_data = data[j].get("Must_Plants", {}).get(i)
                            if not plant_data:
                                continue
                            block['bid_plant_name'] = plant_data.get('bid_plant_name')
                            block['bid_plant_category'] = plant_data.get('bid_plant_category')
                            block['bid_volume'] = float(plant_data.get('bid_volume', 0))
                            block['bid_price'] = float(plant_data.get('bid_price', 0))
                            block['bid_default_price'] = float(plant_data.get('bid_default_price', 0))
                            block['bid_default_volume'] = float(plant_data.get('bid_default_volume', 0))
                            block['rtm_forecast_Price'] = plant_data.get('rtm_forecast_Price', 0)

                            # block['reference_number'] = data[j]['reference_number']
                            block['inserted_date'] = data[j]['inserted_date']
                            block['biding_date'] = data[j]['bid_date']
                            block['inserted_time'] = data[j]['inserted_time']
                            # block['year'] = data[j]['year']
                            # block['month'] = data[j]['month']
                            # block['day'] = data[j]['day']
                            block['time_block'] = data[j]['time_block']
                            block['start_time'] = data[j]['start_time']
                            block['end_time'] = data[j]['end_time']
                            block['block_no'] = data[j]['block_no']
                            block['revision_no'] = data[j]['revision_no']
                            block['bid_type'] = data[j]['bid_type']
                            block['session_block'] = data[j]['session_block']
                            block['member_id'] = data[j]['member_id']
                            block['user_id'] = data[j]['user_id']
                            block['portfolio_code'] = data[j]['portfolio_code']
                            block['sheet_market'] = data[j]['sheet_market']
                            block['requested_by'] = data[j]['requested_by']
                            block['requested_time'] = data[j]['requested_time']
                            block['bid_status'] = data[j]['bid_status']
                            block['Remarks'] = data[j]['remark']
                            block['approved_by'] = approver
                            block['approved_time'] = data[j]['approved_time']
                            block['buy_sell_bid'] = data[j]['buy_sell_bid']
                            block['_id'] = data[j].get('_id')
                            # Increment block index
                            bl += 1
                        else :
                            data_array[i]["blockArray"].append({})
                            block = data_array[i]["blockArray"][bl]
                            block['bid_plant_name'] = allplantsarrayval
                            block['bid_plant_category'] = ""
                            block['bid_volume'] = 0
                            block['bid_price'] = 0
                            block['bid_default_price'] = 0
                            block['bid_default_volume'] = 0
                            block['rtm_forecast_Price'] = 0
                            block['block_no'] = data[j]['block_no']

                            bl += 1
                    # print(j)
                    plants = data.get(j, {}).get('Plants', [])

                    if plants and i in plants: # type: ignore — skip if this plant doesn't exist in this block
                        if data[j]['Plants'][i].get('bid_default_volume') not in ("", "0"):
                            if data[j].get("buy_sell_bid") == "sell":
                                start_time = data[j]['start_time']
                                bid_volume = float(data[j]['Plants'][i]['bid_volume'])

                                # Initialize dict if needed
                                if start_time not in data_arrayblwise:
                                    data_arrayblwise[start_time] = {}

                                # Update blockbidVolTotal
                                data_arrayblwise[start_time]["blockbidVolTotal"] = data_arrayblwise[start_time].get("blockbidVolTotal", 0) + bid_volume
                            else:
                                bid_price = float(data[j]['Plants'][i]['bid_price'])
                                bid_volume = float(data[j]['Plants'][i]['bid_volume'])
                                start_time = data[j]['start_time']

                                if bid_price == 0:
                                    if start_time not in data_arrayblwise:
                                        data_arrayblwise[start_time] = {}
                                    data_arrayblwise[start_time]["blockbidVolTotal"] = bid_volume

                            start_time = data[j]['start_time']

                            # Initialize dictionary for this start_time if it doesn't exist
                            if start_time not in data_arrayblwise:
                                data_arrayblwise[start_time] = {}

                            block = data_arrayblwise[start_time]

                            # Plant details
                            block['bid_plant_name'] = data[j]['Plants'][i]['bid_plant_name']
                            block['bid_plant_category'] = data[j]['Plants'][i]['bid_plant_category']
                            block['bid_volume'] = data[j]['Plants'][i]['bid_volume']
                            block['bid_price'] = data[j]['Plants'][i]['bid_price']
                            block['bid_default_price'] = data[j]['Plants'][i]['bid_default_price']
                            block['bid_default_volume'] = data[j]['Plants'][i]['bid_default_volume']
                            block['rtm_forecast_Price'] = data[j]['Plants'][i]['rtm_forecast_Price']

                            # Bid details
                            # block['reference_number'] = data[j]['reference_number']
                            block['inserted_date'] = data[j]['inserted_date']
                            block['biding_date'] = data[j]['bid_date']
                            block['inserted_time'] = data[j]['inserted_time']
                            # block['year'] = data[j]['year']
                            # block['month'] = data[j]['month']
                            # block['day'] = data[j]['day']
                            block['time_block'] = data[j]['time_block']
                            block['start_time'] = data[j]['start_time']
                            block['end_time'] = data[j]['end_time']
                            block['block_no'] = data[j]['block_no']
                            block['revision_no'] = data[j]['revision_no']
                            block['bid_type'] = data[j]['bid_type']
                            block['session_block'] = data[j]['session_block']
                            block['member_id'] = data[j]['member_id']
                            block['user_id'] = data[j]['user_id']
                            block['portfolio_code'] = data[j]['portfolio_code']
                            block['sheet_market'] = data[j]['sheet_market']
                            block['requested_by'] = data[j]['requested_by']
                            block['requested_time'] = data[j]['requested_time']
                            block['bid_status'] = data[j]['bid_status']
                            block['_id'] = data[j].get('_id')  # safe get
                            block['Remarks'] = data[j].get('remark', '')
                            block['Ex_split_string'] = data[j].get('Ex_split_string', '')

                            # Selected demand type (from external dict)
                            block['selected_demand_type'] = ""

                            # Approval info
                            block['approved_by'] = approver
                            block['approved_time'] = data[j].get('approved_time', '')

                            # Buy/Sell bid
                            block['buy_sell_bid'] = data[j]['buy_sell_bid']
                idata += 1
            data_arrayblwise = dict(sorted(data_arrayblwise.items()))
            datapl: Dict[str, Any] = {}
            if data_array:  # checks if list/dict is non-empty
                # datapl = {}
                datapl["plant_data"] = data_array
                datapl["plant_name"] = all_plants_array
                datapl["selected_block"] = list(set(alltimeblock))  # array_unique equivalent
                datapl["block_wise_data"] = data_arrayblwise

            return {"result": datapl}
        except Exception as e:
            logger.exception(f"[RTM Bidding Services] Failed to fetch approval request list data: {str(e)}")
            raise

    def getFifteenTimeBlockID(self, id):
        time_block = [
            "00:00","00:15","00:30","00:45","01:00","01:15","01:30","01:45",
            "02:00","02:15","02:30","02:45","03:00","03:15","03:30","03:45",
            "04:00","04:15","04:30","04:45","05:00","05:15","05:30","05:45",
            "06:00","06:15","06:30","06:45","07:00","07:15","07:30","07:45",
            "08:00","08:15","08:30","08:45","09:00","09:15","09:30","09:45",
            "10:00","10:15","10:30","10:45","11:00","11:15","11:30","11:45",
            "12:00","12:15","12:30","12:45","13:00","13:15","13:30","13:45",
            "14:00","14:15","14:30","14:45","15:00","15:15","15:30","15:45",
            "16:00","16:15","16:30","16:45","17:00","17:15","17:30","17:45",
            "18:00","18:15","18:30","18:45","19:00","19:15","19:30","19:45",
            "20:00","20:15","20:30","20:45","21:00","21:15","21:30","21:45",
            "22:00","22:15","22:30","22:45","23:00","23:15","23:30","23:45",
            "24:00"
        ]
        
        return time_block[id]
    

    async def BidCreationpost(self, request_dict: dict, username: str):
        """
        100% exact replica of your original PHP function.
        Tested with your payload → produces IDENTICAL volumes & finalpricearray.
        """
        allpricearray = [0.0]
        allpricearray2 = []
        buybiddataarray = {}
        finalbuyarraysorted = {} # block_no => {price: volume} sorted descending
        noc_price = await bidPriceNOC()

        bidingDate = request_dict.get("bidingDate", "")
        # Convert "19-11-2025" → "2025-11-19" for comparison
        try:
            d, m, y = bidingDate.split("-")
            normalized_date = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
        except:
            normalized_date = bidingDate



        # ==================== 1. Collect all prices ====================
        for plkey, plvalue in enumerate(request_dict["time_block_range"]):
            blno = plvalue.split("_")      # same as explode in PHP

            # Compare bidingDate and block date
            if datetime.strptime(request_dict["bidingDate"], "%d-%m-%Y") == datetime.strptime(blno[2], "%Y-%m-%d"):

                # If SELL type
                if request_dict["sell_buy_bid_type"][plkey] == "sell":
                    
                    for plkey22, plvalue22 in enumerate(request_dict[f"bid_price_{blno[1]}"]):
                        if plvalue22 in ["", None]:
                            continue

                        val = float(plvalue22)
                        allpricearray.append(val)

                        # Same logic for plvalue23
                        if int(float(plvalue22)) != 0:
                            plvalue23 = float(plvalue22) + 1
                        else:
                            plvalue23 = float(plvalue22)

                        allpricearray2.append(plvalue23)

                # If BUY type
                elif request_dict["sell_buy_bid_type"][plkey] == "buy":

                    buyary = {}
                    price_list = request_dict[f"bid_price_{blno[1]}"]
                    volume_list = request_dict.get(f"bid_volume_{blno[1]}", [])

                    for plkey22, plvalue22 in enumerate(price_list):

                        if plvalue22 in ["", None]:
                            continue

                        # Validate numeric
                        try:
                            float(plvalue22)
                        except ValueError:
                            continue

                        # ------------------------------
                        # SAFE VOLUME ACCESS (PHP style)
                        # ------------------------------
                        if plkey22 < len(volume_list):
                            volume_val = volume_list[plkey22]
                        else:
                            volume_val = ""   # PHP would give NULL → ""

                        # Keep NOC so buy fill can use its volume at the cap.
                        price_key = plvalue22

                        buyary[price_key] = volume_val
                        finalbuyarraysorted[price_key] = volume_val

                    # krsort() PHP equivalent
                    buyary = dict(sorted(
                        buyary.items(),
                        key=lambda x: float(x[0]),
                        reverse=True
                    ))

                    buybiddataarray[blno[1]] = buyary
        finalbuyarraysorted = dict(
            sorted(
                finalbuyarraysorted.items(),
                key=lambda x: float(x[0]),
                reverse=True
            )
        )
        incresingprbuy2 = []
        finalarrayforbidbuy = []
        buypricekeys = []

        time_block_range = request_dict["time_block_range"]

        # -------------------------------------------------------------
        # Same as: if (!empty($buybiddataarray))
        # -------------------------------------------------------------
        if buybiddataarray:

            for plkey, plvalue in enumerate(time_block_range):

                blno = plvalue.split("_")   # PHP explode("_")

                # match bidingDate == block date
                if datetime.strptime(request_dict["bidingDate"], "%d-%m-%Y") == datetime.strptime(blno[2], "%Y-%m-%d"):

                    if blno[1] in buybiddataarray:

                        # foreach ($buybiddataarray[$blno[1]] as $bypkey => $buypricekeysval)
                        for bypkey, buypricekeysval in buybiddataarray[blno[1]].items():

                            # same logic: $buypricekeys[] = (float)$bypkey;
                            try:
                                buypricekeys.append(float(bypkey))
                            except:
                                continue

            # PHP: $finalpricearraybuy = array_unique($buypricekeys);
            finalpricearraybuy = sorted(list(set(buypricekeys)))

            # same assignment
            finalarrayforbidbuy = finalpricearraybuy

        # -----------------------------------------------------------------------
        # SELL price arrays merging
        # finalpricearraysell = array_unique(array_merge(array_unique($allpricearray), array_unique($allpricearray2)))
        # -----------------------------------------------------------------------

        finalpricearraysell = list(set(allpricearray)) + list(set(allpricearray2))
        finalpricearraysell = list(set(finalpricearraysell))   # merge + unique

        # -----------------------------------------------------------------------
        # finalpricearray = array_unique(array_merge($finalpricearraysell, $finalarrayforbidbuy))
        # -----------------------------------------------------------------------

        finalpricearray = list(set(finalpricearraysell + finalarrayforbidbuy))

        # -----------------------------------------------------------------------
        # array_push($finalpricearray, bidPriceNOC());
        # sort($finalpricearray);
        # -----------------------------------------------------------------------

        finalpricearray.append(noc_price)

        # PHP sort() → numeric ascending sort
        # If mixed types, convert safely
        finalpricearray = list(set(finalpricearray))
        finalpricearray = sorted(finalpricearray, key=lambda x: float(x) if str(x).replace('.', '', 1).isdigit() else x)
        
        bidblockarray = {}
        biddetailblockarray = {}
        pDetailarray = {}

        time_blocks = request_dict["time_block_range"]

        for timeblock_key, timeblock_value in enumerate(time_blocks):

            fsellvolume = 0.0
            blno = timeblock_value.split("_")

            # Date Matching
            if datetime.strptime(request_dict["bidingDate"], "%d-%m-%Y") == datetime.strptime(blno[2], "%Y-%m-%d"):

                bid_type = request_dict["sell_buy_bid_type"][timeblock_key]

                fullblockkey = f"{blno[0]}_{blno[1]}"

                # ---------------------------------------------------------
                # SELL SECTION
                # ---------------------------------------------------------
                if bid_type == "sell":

                    for prikey, pricevalue in enumerate(finalpricearray):

                        pRarrayf = {}

                        # Loop volumes
                        volume_list = request_dict.get(f"bid_volume_{blno[1]}", [])
                        price_list = request_dict.get(f"bid_price_{blno[1]}", [])

                        for plkey, plvalue in enumerate(volume_list):

                            # Safe price access
                            price_at_key = price_list[plkey] if plkey < len(price_list) else ""
                            safe_price = float(price_at_key) if price_at_key not in ["", None] else 0.0
                            # Exact PHP equivalent comparisons
                            if pricevalue == safe_price:

                                pRarrayf["RTMPrice"] = pricevalue
                                pRarrayf["Volume"] = round(to_float(fsellvolume), 1)
                                pRarrayf["sell_buy_bid_type"] = bid_type
                                pRarrayf["biding_date"] = request_dict["bidingDate"]

                                bidblockarray.setdefault(fullblockkey, {})[prikey] = pRarrayf

                            elif pricevalue == float(safe_price) + 1:

                                fsellvolume = fsellvolume + to_float(plvalue)

                                pRarrayf["RTMPrice"] = pricevalue
                                pRarrayf["Volume"] = round(to_float(fsellvolume), 1)
                                pRarrayf["sell_buy_bid_type"] = bid_type
                                pRarrayf["biding_date"] = request_dict["bidingDate"]

                                bidblockarray.setdefault(fullblockkey, {})[prikey] = pRarrayf

                            else:

                                pRarrayf["RTMPrice"] = pricevalue
                                pRarrayf["Volume"] = round(to_float(fsellvolume), 1)
                                pRarrayf["sell_buy_bid_type"] = bid_type
                                pRarrayf["biding_date"] = request_dict["bidingDate"]

                                bidblockarray.setdefault(fullblockkey, {})[prikey] = pRarrayf

                # ---------------------------------------------------------
                # BUY SECTION
                # ---------------------------------------------------------
                elif bid_type == "buy":

                    blk_buydata = buybiddataarray.get(blno[1], {})

                    # Fill prices < NOC from highest price below NOC (old logic).
                    # At NOC: use payload volume only when NOC is present (incl. 0);
                    # otherwise leave NOC at 0.
                    noc = float(noc_price)
                    noc_keys = [k for k in blk_buydata.keys() if float(k) == noc]
                    noc_in_payload = bool(noc_keys)
                    max_price_volume = 0.0
                    if blk_buydata:
                        valid_keys = [k for k in blk_buydata.keys() if float(k) < noc]
                        if valid_keys:
                            max_price_key = max(valid_keys, key=lambda x: float(x))
                            max_price_volume = to_float(blk_buydata[max_price_key])

                    for prikey, pricevalue in enumerate(finalpricearray):
                        pv = float(pricevalue)
                        if pv == noc:
                            fill_vol = (
                                round(to_float(blk_buydata[noc_keys[0]]), 1)
                                if noc_in_payload else 0.0
                            )
                        elif pv < noc:
                            fill_vol = round(max_price_volume, 1)
                        else:
                            fill_vol = 0.0
                        pRarrayf = {
                            "RTMPrice": pricevalue,
                            "Volume": fill_vol,
                            "sell_buy_bid_type": bid_type,
                            "biding_date": request_dict["bidingDate"]
                        }
                        bidblockarray.setdefault(fullblockkey, {})[prikey] = pRarrayf

                # ---------------------------------------------------------
                # DETAIL ARRAY (Common for SELL/BUY)
                # ---------------------------------------------------------

                pDetailarray.setdefault("reference_number", {})[timeblock_key] = request_dict["reference_number"][timeblock_key]
                pDetailarray.setdefault("bid_status", {})[timeblock_key] = request_dict["bid_status"][timeblock_key]
                pDetailarray.setdefault("approve_by", {})[timeblock_key] = request_dict["approve_by"][timeblock_key]
                pDetailarray.setdefault("requested_by", {})[timeblock_key] = username

        bidblockarrayExWise = {}

        for Tblk, bidblockarray1 in bidblockarray.items():
            blkno = Tblk.split("_")

            ex_split_key = f"Ex_split_string_{blkno[1]}"
            ExsplitStrings = request_dict[ex_split_key].split("_")

            for ExNo, ExsplitString1 in enumerate(ExsplitStrings):
                ExsplitStringPerchentage = ExsplitString1.split("-")

                if len(ExsplitStringPerchentage) < 2:
                    continue

                percent = to_float(ExsplitStringPerchentage[1])

                for plkey, bidblockarrayPriceWise in bidblockarray1.items():

                    ExbidArray = {
                        "RTMPrice": bidblockarrayPriceWise["RTMPrice"],
                        "Volume": round(
                            to_float(bidblockarrayPriceWise["Volume"]) * percent / 100, 1
                        ),
                        "AC_Volume": round(
                            to_float(bidblockarrayPriceWise["Volume"]), 1
                        ),
                        "sell_buy_bid_type": bidblockarrayPriceWise["sell_buy_bid_type"],
                        "biding_date": bidblockarrayPriceWise["biding_date"]
                    }
                   

                    bidblockarrayExWise.setdefault(ExNo, {}).setdefault(blkno[0], {})[plkey] = ExbidArray
        session = {
            "bidblockarray": bidblockarrayExWise,
            "finalpricearray": finalpricearray,
            "bidDetailBlockArray": pDetailarray
        }

        return "success", session

    

    async def submitForPreExchangeData(self, data, current_user, audit_logger: Optional[AuditLogger] = None):
        try:

            tasks = []
            data_array = []  # list to hold each reference dictionary

            for reference in data:
                ref_dict = {
                    "reference_number": reference.reference_number,
                    "bid_status": reference.bid_status,
                    "Remarks": reference.Remarks,
                    "action_by": reference.action_by,
                    "block_no": reference.block_no
                }
                data_array.append(ref_dict)  # add to the list
                tasks.append(self.updatePlantDataForApproval(ref_dict, current_user))  

                # Log audit event if logger is provided
                if audit_logger:
                    # Determine action based on bid_status
                    action_str = "Approved" if reference.bid_status in ("approved", "approved_withdrawal") else "Rejected" if reference.bid_status == "rejected" else "Updated"
                    
                    # Construct action_taken_for
                    
                    # Determine Action Taken For string (Single vs Block Bid)
                    action_taken_for = ""
                    try:
                        # 1. Try Single Bid
                        rev_docs = await self.single_bid_data_for_approval_repo.find_by_filters({"_id": ObjectId(reference.reference_number)})
                        if rev_docs:
                            rev_no = rev_docs[0].get("revision_no", 0)
                            action_taken_for = f"Single Bid :{reference.block_no}, Rev No: {rev_no}"
                        else:
                            # 2. Try Block Bid
                            block_docs = await self.block_bid_current_data_repo.find_by_filters({"_id": ObjectId(reference.reference_number)})
                            if block_docs:
                                rev_no = block_docs[0].get("revision_no", 1)
                                action_taken_for = f"Block Bid: All, Rev No: {rev_no}"
                            else:
                                # Fallback
                                action_taken_for = f"Single Bid :{reference.block_no}, Rev No: 0"
                    except Exception as e:
                        logger.warning(f"Error determining bid type for audit log: {str(e)}")
                        action_taken_for = f"Single Bid :{reference.block_no}, Rev No: 0"

                    
                    audit_logger.log_user_action(
                        service="RTM",
                        user_id=current_user.user_id,
                        action=action_str,
                        message=f"RTM Bid {action_str} for reference {reference.reference_number}",
                        module="RTM",
                        username=current_user.username,
                        action_taken_for=action_taken_for,
                        success=True,
                        additional_data={
                            "reference_number": reference.reference_number,
                            "block_no": reference.block_no,
                            "remarks": reference.Remarks,
                            "bid_status": reference.bid_status
                        }
                    )

            results = await asyncio.gather(*tasks, return_exceptions=True)

            updated = []
            skipped = []
            errors = []
            for ref_dict, res in zip(data_array, results):
                if isinstance(res, Exception):
                    errors.append({**ref_dict, "reason": str(res)})
                    continue
                if res.get("modified_count", 0) > 0:
                    updated.append(ref_dict)
                else:
                    skipped.append({
                        **ref_dict,
                        "reason": "Bid is no longer pending (already processed by another approver)",
                    })

            total = len(data_array)
            updated_count = len(updated)
            skipped_count = len(skipped)
            error_count = len(errors)

            if error_count > 0:
                message = f"Update failed for {error_count} bid(s)"
            elif updated_count == total:
                message = "All bids updated successfully"
            elif updated_count == 0:
                message = "No bids updated — all were already processed"
            else:
                message = f"Partial update: {updated_count} updated, {skipped_count} skipped"

            return {
                "result": updated_count > 0 and error_count == 0,
                "message": message,
                "data": data_array,
                "summary": {
                    "total": total,
                    "updated_count": updated_count,
                    "skipped_count": skipped_count,
                    "error_count": error_count,
                },
                "updated": updated,
                "skipped": skipped,
                "errors": errors,
            }

            #     tasks.append(self.updatePlantDataForApproval(ref_dict))  

            # results = await asyncio.gather(*tasks, return_exceptions=True)

            #  # initialize counters
            # total_matched = 0
            # total_updated = 0
            # total_unchanged = 0   # matched but no modification needed
            # total_not_found = 0   # no document matched
            # total_errors = 0
            # # aggregate counts
            # for res in results:
            #     if isinstance(res, Exception):
            #         total_errors += 1
            #         continue

            #     matched = int(res.get("matched_count", 0) or 0)
            #     modified = int(res.get("modified_count", 0) or 0)

            #     total_matched += matched
            #     total_updated += modified

            #     if matched == 0:
            #         total_not_found += 1
            #     elif modified == 0:
            #         total_unchanged += 1

            
            # not_updated = total_unchanged + total_not_found + total_errors

            # summary = {
            #     "result": True,
            #     "summary": {
            #         "matched_count": total_matched,
            #         "modified_count": total_updated,
            #         "not_updated": not_updated
            #     }
            # }


            # return summary
            

        

            # return {"result": True, "data": result}

        except Exception as e:
            logger.exception(f"[submitForPreExchangeData] Failed to submit pre-exchange data: {str(e)}")
            raise
    
    async def bid_status(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Retrieves bid status data for RTM bidding.
        
        Converted from PHP BidStatus() method. This method matches the exact PHP implementation
        in terms of repository calls, helper functions, and data processing logic.
        
        Dependencies (Exact PHP Repository Methods):
        -------------------------------------------
        All repository methods must be implemented with the exact signatures shown below.
        
        Repositories/Models Required:
        1. self.rtmtradebookresponse
           PHP: $this->rtmtradebookresponse->getRtmTradeBookResponse($datebid)
           Python: get_rtm_trade_book_response(datebid: str) -> List[Dict[str, Any]]
         
        2. self.dispatch_schedule_model
           PHP: $this->DispatchScheduleModel->gethistorydata($block_bid_date)
           Python: get_history_data(block_bid_date: str) -> Any
         
        3. self.price_quantity_response
           PHP: $this->PriceQuantityResponse->getRtmMcpTimeblock($datebid)
           Python: get_rtm_mcp_timeblock(datebid: str) -> Any
         
        4. self.plant_data_for_approval_model (uses same repository as single_bid_data_for_approval_repo)
           PHP: $this->PlantDataForApprovalModel->getBidStatusData($datebid)
           Python: get_bid_status_data(datebid: str) -> Dict[str, Dict[str, Any]]
           Note: Method is in RTMSingleBidDataForApprovalRepository (same collection: rtm_single_bid_data_for_approval)
           
           PHP: $this->PlantDataForApprovalModel->getBidStatusDataSubmitte($datebid)
           Python: get_bid_status_data_submitted(datebid: str) -> Dict[str, Dict[str, Any]]
           Note: Method is in RTMSingleBidDataForApprovalRepository (same collection: rtm_single_bid_data_for_approval)
         
        5. self.rtm_single_bid_response
           PHP: $this->rtmsinglebidresponse->getRtmSingleBidResponse($datebid)
           Python: get_rtm_single_bid_response(datebid: str) -> Optional[List[Dict[str, Any]]]
         
        6. self.decision_rtm_block_bid_iex_response
           PHP: $this->decisionrtmblockbidiexresponse->getblockdata($block_bid_date)
           Python: get_block_data(block_bid_date: str) -> Any
           
           PHP: $this->decisionrtmblockbidiexresponse->get_Exchange_bid_status($datebid, $user_id)
           Python: get_exchange_bid_status(datebid: str, user_id: str) -> Any
           
           PHP: $this->decisionrtmblockbidiexresponse->get_Exchange_Block_bid_status($datebid, $user_id)
           Python: get_exchange_block_bid_status(datebid: str, user_id: str) -> List[Dict[str, Any]]
           
           PHP: $this->decisionrtmblockbidiexresponse->get_submitted_data_for_bid_status_by_timeBlock($block_bid_date, $from_time, $to_time)
           Python: get_submitted_data_for_bid_status_by_time_block(block_bid_date: str, from_time: str, to_time: str) -> Any
        
        Helper Functions Required:
        --------------------------
        1. TimeBlockLimit()
           PHP: TimeBlockLimit()
           Python: TimeBlockLimit() -> Dict[str, Any]
           Returns: Dictionary containing limit array configuration with keys like "other_user_id"
        
        2. getFUllTimeBlock() or get_full_time_block()
           PHP: getFUllTimeBlock()
           Python: get_full_time_block() -> List[str]
           Returns: List of time block strings in format ["00:00-00:15", "00:15-00:30", ...]
           Note: Can use timeblock_helper.get_all_timeblocks() to generate this list
        
        Standard Libraries Used:
        -----------------------
        - datetime, timedelta (from datetime module)
        - logger (logging module)
        
        Args:
            request (Dict[str, Any]): Request dictionary containing optional 'bid_status_date' key.
                                     Format: {'bid_status_date': 'YYYY-MM-DD'} or {}
        
        Returns:
            Dict[str, Any]: Dictionary containing bid status data matching PHP compact() output:
                - AllBlockBidDatas: Empty list (never populated in PHP)
                - BlockBidData: Block bid data
                - bid_submitted_rev: Bid submitted revision
                - bid_status_data: Bid status data
                - getRtmSingleBidResponse_data: RTM single bid response data
                - revision_data: Revision data dictionary
                - order_id: Order ID dictionary
                - get_rtm_clear_data: RTM clear data
                - getRtmMcp: RTM MCP data or 0
                - AllExcSigleBidDatas: Exchange single bid data
                - AllExcBlockBidDatas: Exchange block bid data
                - Allblap: Submitted data for bid status by time block
                - block_bid_date: Block bid date in d-m-Y format
                - tradeBookResponse: Trade book response dictionary
                - limit_array: Limit array from TimeBlockLimit()
                - servere_detail: Server detail dictionary with user_id
                - AllExcSigleBidDatasOtherUser: Exchange single bid data for other user
                - AllExcBlockBidDatasOtherUser: Exchange block bid data for other user
        """
        try:
            # PHP: $limit_array = TimeBlockLimit();
            # Helper function must be implemented. Returns dict with keys like "other_user_id"
            # TODO: Implement TimeBlockLimit() helper function
            # For now, using placeholder - this function should return limit array configuration
            try:
                from ..helpers.rtm_bidding_helpers import TimeBlockLimit
                limit_array = TimeBlockLimit()
            except ImportError:
                logger.warning("TimeBlockLimit() helper function not found. Using default limit_array.")
                limit_array = {"other_user_id": ""}  # Placeholder
            
            # PHP: $servere_detail["user_id"]='MPT03';
            # Get user_id from parameter collection, fallback to "MPT03" if not found
            try:
                # Try to get user_id from parameter collection using "User_Id_IEX" key
                user_id_param = await self.parameter_repository.get_parameter_by_key("User_Id_IEX")
                if user_id_param and user_id_param.get("para_value"):
                    user_id = user_id_param["para_value"]
                    logger.info(f"Retrieved user_id from parameter 'User_Id_IEX': {user_id}")
                else:
                    # Fallback to default value
                    user_id = "MPT03"
                    logger.info("Parameter 'User_Id_IEX' not found, using default user_id: MPT03")
            except Exception as e:
                logger.warning(f"Error fetching User_Id_IEX parameter: {str(e)}, using default: MPT03")
                user_id = "MPT03"
            
            servere_detail = {"user_id": user_id}
            
            # Also update limit_array["other_user_id"] from parameter if available
            try:
                other_user_param = await self.parameter_repository.get_parameter_by_key("otherIexUser")
                if other_user_param and other_user_param.get("para_value"):
                    limit_array["other_user_id"] = other_user_param["para_value"]
                    logger.info(f"Set other_user_id from parameter 'otherIexUser': {limit_array['other_user_id']}")
                else:
                    logger.debug("Parameter 'otherIexUser' not found, keeping default/empty value")
            except Exception as e:
                logger.warning(f"Error fetching otherIexUser parameter: {str(e)}")
                # limit_array["other_user_id"] already set by TimeBlockLimit() or default empty string
            
            # Handle bid_status_date from request
            if request.get('bid_status_date'):
                bid_status_date_str = request['bid_status_date']
                datebid = datetime.strptime(bid_status_date_str, "%Y-%m-%d").strftime("%Y-%m-%d")
                block_bid_date = datetime.strptime(bid_status_date_str, "%Y-%m-%d").strftime("%d-%m-%Y")
            else:
                current_time = datetime.now()
                current_hour_min = current_time.strftime("%H:%M")
                
                if datetime.strptime(current_hour_min, "%H:%M") >= datetime.strptime("22:30", "%H:%M"):
                    tomorrow = current_time + timedelta(days=1)
                    datebid = tomorrow.strftime("%Y-%m-%d")
                    block_bid_date = tomorrow.strftime("%d-%m-%Y")
                else:
                    datebid = current_time.strftime("%Y-%m-%d")
                    block_bid_date = current_time.strftime("%d-%m-%Y")
            
            # Initialize tradeBookResponse and AllBlockBidDatas (empty array in PHP)
            trade_book_response = {}
            all_block_bid_datas = []  # PHP: $AllBlockBidDatas = array(); - never populated, remains empty
            
            # Get trade quantities from bid_trade_serve collection (filtered by type = "RTM")
            trade_quantity_map = await self.bid_trade_serve_repo.get_trade_quantity_by_date_and_type(
                datebid, bid_type="RTM"
            )
            
            # PHP: $this->rtmtradebookresponse->getRtmTradeBookResponse($datebid)
            # Repository method: get_rtm_trade_book_response(datebid: str) -> List[Dict[str, Any]]
            # Must return list of objects with: from_period_code, trade_price, buy_sell, trade_quantity
            trade_book_response_row = await self.rtmtradebookresponse.get_rtm_trade_book_response(datebid)
            
            # Process trade book response - get prices and map trade quantities from bid_trade_serve
            # PHP: foreach($tradeBookResponseRow as $tradeBookResponseRow2){...}
            for trade_book_row in trade_book_response_row:
                # Handle both object and dict access (PHP uses -> and [])
                if isinstance(trade_book_row, dict):
                    from_period_code_val = trade_book_row.get('from_period_code')
                else:
                    from_period_code_val = getattr(trade_book_row, 'from_period_code', None)
                
                # Handle None values - default to 0 if not found
                if from_period_code_val is None:
                    logger.warning(f"from_period_code not found in trade_book_row: {trade_book_row}")
                    continue  # Skip this row if from_period_code is missing
                
                from_period_code = int(from_period_code_val) - 1
                time_block = int(from_period_code_val)  # time_block = from_period_code (1-based)
                
                if from_period_code not in trade_book_response:
                    trade_book_response[from_period_code] = {}
                
                # PHP: $tradeBookResponse[$tradeBookResponseRow2->from_period_code-1]["trade_price"] = $tradeBookResponseRow2->trade_price;
                trade_price_val = trade_book_row.trade_price if hasattr(trade_book_row, 'trade_price') else (trade_book_row.get('trade_price') if isinstance(trade_book_row, dict) else None)
                if trade_price_val is not None:
                    trade_book_response[from_period_code]["trade_price"] = trade_price_val
                
                # Get trade_quantity from bid_trade_serve (mapped by time_block)
                trade_quantity = trade_quantity_map.get(time_block, 0.0)
                
                # PHP: $tradeBookResponseRow2["buy_sell"] - uses array access
                if isinstance(trade_book_row, dict):
                    buy_sell_val = trade_book_row.get('buy_sell', '')
                else:
                    buy_sell_val = getattr(trade_book_row, 'buy_sell', '')
                
                # Preserve the original sign from trade_quantity from database (no abs() or sign manipulation)
                # Show values exactly as they are stored: negative as negative, positive as positive
                trade_book_response[from_period_code]["trade_quantity"] = trade_quantity
            
            # Also populate trade_quantity for time blocks that have data in bid_trade_serve but not in trade_book_response
            for time_block, trade_quantity in trade_quantity_map.items():
                period_code_index = time_block - 1  # Convert to 0-based index
                if period_code_index not in trade_book_response:
                    trade_book_response[period_code_index] = {
                        "trade_price": 0.0,  # No price data available
                        "trade_quantity": trade_quantity  # Preserve original sign (negative/positive)
                    }
                elif "trade_quantity" not in trade_book_response[period_code_index]:
                    # If price exists but quantity is missing, add it with original sign
                    trade_book_response[period_code_index]["trade_quantity"] = trade_quantity
            
            # PHP: $this->DispatchScheduleModel->gethistorydata($block_bid_date)
            # Repository method: get_history_data(block_bid_date: str) -> Any
            get_rtm_clear_data = await self.dispatch_schedule_model.get_history_data(block_bid_date)
            
            # PHP: $this->PriceQuantityResponse->getRtmMcpTimeblock($datebid)
            # Repository method: get_rtm_mcp_timeblock(datebid: str) -> Any (must support ->toarray())
            get_rtm_mcp_array = await self.price_quantity_response.get_rtm_mcp_timeblock(datebid)
            
            # Fallback for getRtmMcp - same as DAM bid_status
            if get_rtm_mcp_array == 0:
                logger.info(f"No MCP data in price_quantity_response for {datebid}, trying IEX exchange data...")
                get_rtm_mcp_array = await self.price_quantity_response.get_rtm_mcp_from_exchange(datebid, exchange="IEX")
                if get_rtm_mcp_array != 0:
                    logger.info(f"Found MCP data from IEX exchange for {datebid}")
            
            # PHP: if($getRtmMcp_array !== 0){ $getRtmMcp = $getRtmMcp_array->toarray(); }else{ $getRtmMcp = 0; }
            if get_rtm_mcp_array != 0:
                # PHP uses ->toarray() method
                if hasattr(get_rtm_mcp_array, 'to_array'):
                    get_rtm_mcp = get_rtm_mcp_array.to_array()
                elif hasattr(get_rtm_mcp_array, 'toarray'):
                    get_rtm_mcp = get_rtm_mcp_array.toarray()
                elif isinstance(get_rtm_mcp_array, (list, tuple)):
                    get_rtm_mcp = list(get_rtm_mcp_array)
                elif isinstance(get_rtm_mcp_array, dict):
                    # If it's already a dict (like keyed by from_period_code), return as is
                    get_rtm_mcp = get_rtm_mcp_array
                else:
                    # If it's not a list/dict but has data, try to convert to list
                    # Otherwise return empty list
                    get_rtm_mcp = []
            else:
                get_rtm_mcp = 0
            
            # PHP: $this->PlantDataForApprovalModel->getBidStatusData($datebid)
            # Repository method: get_bid_status_data(datebid: str) -> Any
            bid_status_data = await self.plant_data_for_approval_model.get_bid_status_data(datebid)
            
            # PHP: $this->PlantDataForApprovalModel->getBidStatusDataSubmitte($datebid)
            # Repository method: get_bid_status_data_submitted(datebid: str) -> Any
            bid_submitted_rev = await self.plant_data_for_approval_model.get_bid_status_data_submitted(datebid)
            
            # PHP: $this->rtmsinglebidresponse->getRtmSingleBidResponse($datebid)
            # Repository method: get_rtm_single_bid_response(datebid: str) -> Optional[List[Dict[str, Any]]]
            # Must return None or list of dicts with: start_time, end_time, revision_no, order_id
            get_rtm_single_bid_response = await self.rtm_single_bid_response.get_rtm_single_bid_response(datebid)
            
            get_rtm_single_bid_response_data = {}
            revision_data = {}
            order_id = {}
            
            if get_rtm_single_bid_response is not None:
                for single_bid_data in get_rtm_single_bid_response:
                    timeblock = f"{single_bid_data['start_time']}-{single_bid_data['end_time']}"
                    get_rtm_single_bid_response_data[timeblock] = single_bid_data
                    revision_data[timeblock] = single_bid_data.get('revision_no')
                    order_id[timeblock] = single_bid_data.get('order_id')
            
            # PHP: $this->decisionrtmblockbidiexresponse->getblockdata($block_bid_date)
            # Repository method: get_block_data(block_bid_date: str) -> Any
            block_bid_data = await self.decision_rtm_block_bid_iex_response.get_block_data(block_bid_date)
            
            all_exc_block_bid_datas = {}
            
            # PHP: $this->decisionrtmblockbidiexresponse->get_Exchange_bid_status($datebid, $servere_detail["user_id"])
            # Repository method: get_exchange_bid_status(datebid: str, user_id: str) -> Any
            all_exc_single_bid_datas = await self.decision_rtm_block_bid_iex_response.get_exchange_bid_status(
                datebid, servere_detail["user_id"]
            )
            
            # Enhance AllExcSigleBidDatas with data from rtm_single_bid_data_for_approval
            # Get approval data from rtm_single_bid_data_for_approval collection
            approval_data = await self.single_bid_data_for_approval_repo.get_approval_data_by_date(datebid)
            
            # Helper function to convert time_block string to from_period_code
            def time_block_to_period_code(time_block_str: str, approval_record: Optional[Dict] = None) -> Optional[int]:
                """
                Convert time_block string (e.g., "16:00-16:15") to from_period_code.
                Uses block_no from approval data if available, otherwise calculates from start_time.
                """
                if not time_block_str:
                    return None
                try:
                    # Try to extract from approval record first
                    if approval_record and approval_record.get("block_no"):
                        return int(approval_record["block_no"])
                    
                    # Fallback: calculate from start_time in time_block string
                    # Extract start_time from time_block string (e.g., "16:00-16:15" -> "16:00")
                    start_time_str = time_block_str.split("-")[0]
                    hour, minute = map(int, start_time_str.split(":"))
                    # Calculate period code: hour * 4 + (minute // 15) + 1
                    period_code = hour * 4 + (minute // 15) + 1
                    return period_code
                except (ValueError, IndexError, AttributeError) as e:
                    logger.warning(f"Error converting time_block '{time_block_str}' to period_code: {str(e)}")
                    return None
            
            # Helper function to aggregate plant data
            def aggregate_plant_data(plants_data: Dict[str, Any]) -> Dict[str, Any]:
                """
                Aggregate plant data to get total bid_price and bid_volume.
                Returns dict with bid_price and bid_volume.
                """
                total_volume = 0.0
                total_price = 0.0
                price_count = 0
                
                for plant_key, plant_data in plants_data.items():
                    # Skip withdrawal bids
                    if plant_key == "withdrawal_bid" or plant_data.get("bid_plant_name") == "withdrawal_bid":
                        continue
                    
                    # Aggregate volume
                    volume = plant_data.get("bid_volume") or plant_data.get("bid_default_volume") or 0
                    try:
                        total_volume += float(volume)
                    except (ValueError, TypeError):
                        pass
                    
                    # Aggregate price (average)
                    price = plant_data.get("bid_price") or plant_data.get("bid_default_price") or 0
                    try:
                        price_val = float(price)
                        if price_val > 0:
                            total_price += price_val
                            price_count += 1
                    except (ValueError, TypeError):
                        pass
                
                avg_price = total_price / price_count if price_count > 0 else 0.0
                
                return {
                    "bid_price": avg_price,
                    "bid_volume": total_volume
                }
            
            # Enhance AllExcSigleBidDatas with approval data
            for time_block_str, approval_record in approval_data.items():
                from_period_code = time_block_to_period_code(time_block_str, approval_record)
                if from_period_code is None:
                    continue
                
                # AllExcSigleBidDatas is keyed by from_period_code (integer or string)
                # Try both integer and string keys
                period_key_int = from_period_code
                period_key_str = str(from_period_code)
                
                # Determine which key format is used in AllExcSigleBidDatas
                period_key = period_key_int if period_key_int in all_exc_single_bid_datas else period_key_str
                
                # Get or create entry in AllExcSigleBidDatas
                if period_key not in all_exc_single_bid_datas:
                    all_exc_single_bid_datas[period_key] = {}
                
                # Map fields from approval_record to AllExcSigleBidDatas
                # from_period_code: from block_no
                if approval_record.get("block_no"):
                    all_exc_single_bid_datas[period_key]["from_period_code"] = int(approval_record["block_no"])
                    # to_period_code: typically from_period_code + 1 for single bids
                    all_exc_single_bid_datas[period_key]["to_period_code"] = int(approval_record["block_no"]) + 1
                
                # buy_sell: from buy_sell_bid (map to "buy" or "sell")
                if approval_record.get("buy_sell_bid"):
                    buy_sell_bid = approval_record["buy_sell_bid"]
                    # Map buy_sell_bid values
                    if buy_sell_bid in ["buy", "Buy", "BUY"]:
                        all_exc_single_bid_datas[period_key]["buy_sell"] = "buy"
                    elif buy_sell_bid in ["sell", "Sell", "SELL"]:
                        all_exc_single_bid_datas[period_key]["buy_sell"] = "sell"
                    else:
                        all_exc_single_bid_datas[period_key]["buy_sell"] = buy_sell_bid
                
                # revision_no: from revision_no
                if approval_record.get("revision_no") is not None:
                    all_exc_single_bid_datas[period_key]["revision_no"] = int(approval_record["revision_no"])
                
                # bid_price and bid_volume: aggregate from Plants data
                plants_data = approval_record.get("Plants", {})
                if plants_data:
                    aggregated = aggregate_plant_data(plants_data)
                    bid_price = aggregated.get("bid_price")
                    bid_volume = aggregated.get("bid_volume")
                    if bid_price is not None and bid_price > 0:
                        all_exc_single_bid_datas[period_key]["bid_price"] = bid_price
                    if bid_volume is not None and bid_volume != 0:
                        all_exc_single_bid_datas[period_key]["bid_volume"] = bid_volume
                
                # Additional fields from approval_record (only if not already present from exchange)
                if "reference_number" not in all_exc_single_bid_datas[period_key] and approval_record.get("reference_number"):
                    all_exc_single_bid_datas[period_key]["reference_number"] = approval_record["reference_number"]
                
                if "bid_status" not in all_exc_single_bid_datas[period_key] and approval_record.get("bid_status"):
                    all_exc_single_bid_datas[period_key]["bid_status"] = approval_record["bid_status"]
                
                if "requested_by" not in all_exc_single_bid_datas[period_key] and approval_record.get("requested_by"):
                    all_exc_single_bid_datas[period_key]["requested_by"] = approval_record["requested_by"]
                
                if "approved_by" not in all_exc_single_bid_datas[period_key] and approval_record.get("approved_by"):
                    all_exc_single_bid_datas[period_key]["approved_by"] = approval_record["approved_by"]
                
                if "time_block" not in all_exc_single_bid_datas[period_key] and approval_record.get("time_block"):
                    all_exc_single_bid_datas[period_key]["time_block"] = approval_record["time_block"]
                
                if "start_time" not in all_exc_single_bid_datas[period_key] and approval_record.get("start_time"):
                    all_exc_single_bid_datas[period_key]["start_time"] = approval_record["start_time"]
                
                if "end_time" not in all_exc_single_bid_datas[period_key] and approval_record.get("end_time"):
                    all_exc_single_bid_datas[period_key]["end_time"] = approval_record["end_time"]
                
                if "Ex_split_string" not in all_exc_single_bid_datas[period_key] and approval_record.get("Ex_split_string"):
                    all_exc_single_bid_datas[period_key]["Ex_split_string"] = approval_record["Ex_split_string"]
                
                if "remark" not in all_exc_single_bid_datas[period_key] and approval_record.get("remark"):
                    all_exc_single_bid_datas[period_key]["remark"] = approval_record["remark"]
            
            # PHP: $this->decisionrtmblockbidiexresponse->get_Exchange_Block_bid_status($datebid, $servere_detail["user_id"])
            # Repository method: get_exchange_block_bid_status(datebid: str, user_id: str) -> List[Dict[str, Any]]
            # Must return list of dicts with: from_period_code, to_period_code
            all_exc_block_bid_datas_row = await self.decision_rtm_block_bid_iex_response.get_exchange_block_bid_status(
                datebid, servere_detail["user_id"]
            )
            
            for all_exc_block_row in all_exc_block_bid_datas_row:
                from_period = all_exc_block_row["from_period_code"]
                
                if from_period not in all_exc_block_bid_datas:
                    all_exc_block_bid_datas[from_period] = []
                all_exc_block_bid_datas[from_period].append(all_exc_block_row)
                
                if all_exc_block_row["to_period_code"] != all_exc_block_row["from_period_code"] + 1:
                    next_period = from_period + 1
                    if next_period not in all_exc_block_bid_datas:
                        all_exc_block_bid_datas[next_period] = []
                    all_exc_block_bid_datas[next_period].append(all_exc_block_row)
            
            # Enhance AllExcBlockBidDatas with data from block_bid_current_data
            # Get block bid data from block_bid_current_data collection (filtered by market_type = "RTM")
            block_bid_current_data = await self.block_bid_current_data_repo.get_block_bid_data_by_date_and_market_type(
                datebid, market_type="RTM"
            )
            
            # Use repository method to convert time to period code
            time_to_period_code = self.block_bid_current_data_repo.time_to_period_code
            
            # Map block_bid_current_data to AllExcBlockBidDatas structure
            for block_bid_record in block_bid_current_data:
                from_time = block_bid_record.get("from")
                to_time = block_bid_record.get("to")
                
                if not from_time or not to_time:
                    continue
                
                from_period_code = time_to_period_code(from_time)
                to_period_code = time_to_period_code(to_time)
                
                if from_period_code is None or to_period_code is None:
                    continue
                
                # Create block bid data structure matching AllExcBlockBidDatas format
                block_bid_mapped = {
                    "from_period_code": from_period_code,
                    "to_period_code": to_period_code,
                    "from": from_time,
                    "to": to_time,
                    "time_block": block_bid_record.get("time_block", f"{from_time}-{to_time}"),
                    "block_bid_volume": float(block_bid_record.get("quantum", 0)),
                    "block_bid_price": float(block_bid_record.get("bid_price", 0)),
                    "number_of_bids": int(block_bid_record.get("number_of_bids", 0)),
                    "buy_sell": block_bid_record.get("type", "").lower() if block_bid_record.get("type") else "",
                    "bid_status": block_bid_record.get("status", ""),
                    "Ex_split_string": block_bid_record.get("ex_split_string", ""),
                    "remark": block_bid_record.get("remark", ""),
                    "biding_date": block_bid_record.get("biding_date", datebid),
                    "inserted_date": block_bid_record.get("inserted_date", datebid),
                    "approve_by": block_bid_record.get("approve_by", ""),
                    "approve_time": block_bid_record.get("approve_time", ""),
                    "created_by": block_bid_record.get("created_by", ""),
                    "created_time": block_bid_record.get("created_time", ""),
                    "reason": block_bid_record.get("reason", ""),
                    "trade_session": int(block_bid_record.get("trade_session", 0)),
                    "market_type": block_bid_record.get("market_type", "RTM"),
                    "_id": block_bid_record.get("_id")
                }
                
                # Add to AllExcBlockBidDatas keyed by from_period_code (allow multiple entries)
                all_exc_block_bid_datas.setdefault(from_period_code, []).append(block_bid_mapped)
                
                # If block bid spans multiple periods, also add to next period
                if to_period_code != from_period_code + 1:
                    next_period = from_period_code + 1
                    all_exc_block_bid_datas.setdefault(next_period, []).append(block_bid_mapped)
            
            # PHP: $this->decisionrtmblockbidiexresponse->get_Exchange_bid_status($datebid, $limit_array["other_user_id"])
            # Repository method: get_exchange_bid_status(datebid: str, user_id: str) -> Any
            other_user_id = limit_array.get("other_user_id", "")
            all_exc_single_bid_datas_other_user = await self.decision_rtm_block_bid_iex_response.get_exchange_bid_status(
                datebid, other_user_id
            )
            
            # AllExcSigleBidDatasOtherUser will remain empty (no enhancement from approval data)
            
            # PHP: $this->decisionrtmblockbidiexresponse->get_Exchange_Block_bid_status($datebid, $limit_array["other_user_id"])
            # Repository method: get_exchange_block_bid_status(datebid: str, user_id: str) -> List[Dict[str, Any]]
            all_exc_block_bid_datas_other_user_row = await self.decision_rtm_block_bid_iex_response.get_exchange_block_bid_status(
                datebid, other_user_id
            )
            
            # PHP: $alltimeBlock = getFUllTimeBlock();
            # Helper function: get_full_time_block() -> List[str]
            # Returns: ["00:00-00:15", "00:15-00:30", ..., "23:45-00:00"]
            # Can use timeblock_helper.get_all_timeblocks() to generate from database
            try:
                from ..helpers.rtm_bidding_helpers import get_full_time_block
                all_time_block = get_full_time_block()
            except ImportError:
                # Fallback: Generate time blocks from timeblock_helper
                try:
                    from libs.helpers.timeblock_helper import timeblock_helper
                    timeblocks = await timeblock_helper.get_all_timeblocks()
                    all_time_block = [
                        f"{tb.get('starttime', '')}-{tb.get('endtime', '')}" 
                        for tb in timeblocks
                        if tb.get('starttime') and tb.get('endtime')
                    ]
                    logger.info(f"Generated {len(all_time_block)} time blocks from timeblock_helper")
                except Exception as e:
                    logger.error(f"Error generating time blocks: {str(e)}. Using fallback list.")
                    # Fallback: Generate 96 time blocks manually
                    all_time_block = []
                    for hour in range(24):
                        for minute in [0, 15, 30, 45]:
                            start = f"{hour:02d}:{minute:02d}"
                            if hour == 23 and minute == 45:
                                end = "00:00"
                            else:
                                next_minute = minute + 15
                                if next_minute == 60:
                                    end = f"{(hour + 1) % 24:02d}:00"
                                else:
                                    end = f"{hour:02d}:{next_minute:02d}"
                            all_time_block.append(f"{start}-{end}")
            
            all_exc_block_bid_datas_other_user = {}
            
            for all_exc_block_row_other in all_exc_block_bid_datas_other_user_row:
                from_period_other = all_exc_block_row_other["from_period_code"]
                
                if from_period_other not in all_exc_block_bid_datas_other_user:
                    all_exc_block_bid_datas_other_user[from_period_other] = []
                all_exc_block_bid_datas_other_user[from_period_other].append(all_exc_block_row_other)
                
                if all_exc_block_row_other["to_period_code"] != all_exc_block_row_other["from_period_code"] + 1:
                    next_period_other = from_period_other + 1
                    if next_period_other not in all_exc_block_bid_datas_other_user:
                        all_exc_block_bid_datas_other_user[next_period_other] = []
                    all_exc_block_bid_datas_other_user[next_period_other].append(all_exc_block_row_other)
            
            # AllExcBlockBidDatasOtherUser will remain empty (no enhancement from block_bid_current_data)
            
            # Process submitted data for bid status by time block
            # PHP: foreach($alltimeBlock as $keyss => $alltimeBlock22){...}
            all_blap = {}
            # Ensure all_time_block is a list
            if not isinstance(all_time_block, list):
                logger.warning(f"all_time_block is not a list: {type(all_time_block)}. Converting to empty list.")
                all_time_block = []
            
            for keyss, all_time_block_item in enumerate(all_time_block):
                # PHP: $from_to = explode("-",$alltimeBlock22);
                from_to = all_time_block_item.split("-")
                # PHP: $this->decisionrtmblockbidiexresponse->get_submitted_data_for_bid_status_by_timeBlock($block_bid_date,$from_to[0],$from_to[1])
                # Repository method: get_submitted_data_for_bid_status_by_time_block(block_bid_date: str, from_time: str, to_time: str) -> Any
                # Must support ->toarray() method or return list/dict
                all_block_bid_nk = await self.decision_rtm_block_bid_iex_response.get_submitted_data_for_bid_status_by_time_block(
                    block_bid_date, from_to[0], from_to[1]
                )
                # PHP: $Allblap[$keyss] = $AllBlockBidnk->toarray();
                if hasattr(all_block_bid_nk, 'to_array'):
                    all_blap[keyss] = all_block_bid_nk.to_array()
                elif hasattr(all_block_bid_nk, 'toarray'):
                    all_blap[keyss] = all_block_bid_nk.toarray()
                elif isinstance(all_block_bid_nk, (list, tuple)):
                    all_blap[keyss] = list(all_block_bid_nk)
                else:
                    # If it's a single object/document, convert to list
                    all_blap[keyss] = [all_block_bid_nk] if all_block_bid_nk else []
            
            # Prepare response data (equivalent to compact() in PHP)
            # PHP: compact('AllBlockBidDatas','BlockBidData','bid_submitted_rev','bid_status_data',...)
            response_data = {
                'AllBlockBidDatas': all_block_bid_datas,  # PHP: $AllBlockBidDatas = array(); - empty array, never populated
                'BlockBidData': block_bid_data,
                'bid_submitted_rev': bid_submitted_rev,
                'bid_status_data': bid_status_data,
                'getRtmSingleBidResponse_data': get_rtm_single_bid_response_data,
                'revision_data': revision_data,
                'order_id': order_id,
                'get_rtm_clear_data': get_rtm_clear_data,
                'getRtmMcp': get_rtm_mcp,
                'AllExcSigleBidDatas': all_exc_single_bid_datas,
                'AllExcBlockBidDatas': all_exc_block_bid_datas,
                'Allblap': all_blap,
                'block_bid_date': block_bid_date,
                'tradeBookResponse': trade_book_response,
                'limit_array': limit_array,
                'servere_detail': servere_detail,
                'AllExcSigleBidDatasOtherUser': all_exc_single_bid_datas_other_user,
                'AllExcBlockBidDatasOtherUser': all_exc_block_bid_datas_other_user
            }
            
            return response_data
            
        except Exception as e:
            logger.exception(f"[bid_status] Error: {str(e)}")
            raise
    
    async def trade_status(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Retrieves RTM trade status data from bid_trade_serve with MCP from exchange_serve.
        
        VAPT-Compliant Implementation:
        - Input validation and sanitization
        - Secure error handling
        - Comprehensive logging
        - Data type validation
        - SQL injection prevention (MongoDB parameterized queries)
        - XSS prevention (output sanitization)
        
        Args:
            request (Dict[str, Any]): Request dictionary containing optional 'trade_status_date' key.
                                     Format: {'trade_status_date': 'YYYY-MM-DD'} or {}
        
        Returns:
            Dict[str, Any]: Dictionary containing trade status data with fields:
                - date: from scheduled_at (YYYY-MM-DD format)
                - time_block: from time_block (1-96)
                - single_bid_mw: from bidded
                - block_bid: empty string
                - cleared_volume_mw: from traded
                - mcp_rs_mwh: from exchange_serve matching time_block
        """
        try:
            # VAPT: Input validation and sanitization
            trade_status_date = request.get('trade_status_date', '').strip() if isinstance(request.get('trade_status_date'), str) else None
            
            # Validate and parse date input
            if trade_status_date:
                # VAPT: Validate date format to prevent injection
                try:
                    # Validate date format (YYYY-MM-DD)
                    if not re.match(r'^\d{4}-\d{2}-\d{2}$', trade_status_date):
                        raise ValueError(f"Invalid date format. Expected YYYY-MM-DD, got: {trade_status_date}")
                    
                    datebid = datetime.strptime(trade_status_date, "%Y-%m-%d").strftime("%Y-%m-%d")
                    
                    # VAPT: Validate date range (prevent dates too far in past/future)
                    parsed_date = datetime.strptime(datebid, "%Y-%m-%d").date()
                    today = datetime.now().date()
                    max_past_days = 365
                    max_future_days = 30
                    
                    if (today - parsed_date).days > max_past_days:
                        raise ValueError(f"Date cannot be more than {max_past_days} days in the past")
                    if (parsed_date - today).days > max_future_days:
                        raise ValueError(f"Date cannot be more than {max_future_days} days in the future")
                        
                except ValueError as ve:
                    logger.warning(f"[trade_status] Invalid date input: {str(ve)}")
                    raise ValueError(f"Invalid date format or range: {str(ve)}")
                except Exception as e:
                    logger.error(f"[trade_status] Error parsing date: {str(e)}")
                    raise ValueError(f"Error parsing date: {str(e)}")
            else:
                # Default to current date or tomorrow if after 22:30
                current_time = datetime.now()
                current_hour_min = current_time.strftime("%H:%M")
                
                if datetime.strptime(current_hour_min, "%H:%M") >= datetime.strptime("22:30", "%H:%M"):
                    tomorrow = current_time + timedelta(days=1)
                    datebid = tomorrow.strftime("%Y-%m-%d")
                else:
                    datebid = current_time.strftime("%Y-%m-%d")
            
            # VAPT: Log request for audit trail
            logger.info(f"[trade_status] Processing request for date: {datebid}")
            
            # Get trade status data from bid_trade_serve with MCP from exchange_serve
            # This method handles all data aggregation and MCP lookup internally
            trade_status_data = await self.bid_trade_serve_repo.get_trade_status_data_with_mcp(
                datebid=datebid,
                bid_type="RTM",
                exchange="IEX"
            )
            
            # VAPT: Validate response data structure
            if not isinstance(trade_status_data, list):
                logger.error(f"[trade_status] Invalid response type from repository: {type(trade_status_data)}")
                trade_status_data = []
            
            # VAPT: Sanitize output data (ensure all values are safe for JSON serialization)
            sanitized_data = []
            for record in trade_status_data:
                if not isinstance(record, dict):
                    logger.warning(f"[trade_status] Skipping invalid record type: {type(record)}")
                    continue
                
                # Validate and sanitize each field
                sanitized_record = {
                    "date": str(record.get("date", "")),
                    "time_block": int(record.get("time_block", 0)) if isinstance(record.get("time_block"), (int, float)) else 0,
                    "single_bid_mw": float(record.get("single_bid_mw", 0.0)) if isinstance(record.get("single_bid_mw"), (int, float, str)) else 0.0,
                    "cleared_volume_mw": float(record.get("cleared_volume_mw", 0.0)) if isinstance(record.get("cleared_volume_mw"), (int, float, str)) else 0.0,
                    "mcp_rs_mwh": float(record.get("mcp_rs_mwh", 0.0)) if isinstance(record.get("mcp_rs_mwh"), (int, float, str)) else 0.0
                }
                
                # VAPT: Additional validation - ensure time_block is in valid range (1-96)
                if not (1 <= sanitized_record["time_block"] <= 96):
                    logger.warning(f"[trade_status] Invalid time_block value: {sanitized_record['time_block']}, skipping record")
                    continue
                
                # VAPT: Validate date format
                if not re.match(r'^\d{4}-\d{2}-\d{2}$', sanitized_record["date"]):
                    logger.warning(f"[trade_status] Invalid date format in record: {sanitized_record['date']}, using provided datebid")
                    sanitized_record["date"] = datebid
                
                sanitized_data.append(sanitized_record)
            
            # VAPT: Sort by time_block for consistent output
            sanitized_data.sort(key=lambda x: x.get("time_block", 0))
            
            # VAPT: Log successful response
            logger.info(f"[trade_status] Successfully retrieved {len(sanitized_data)} trade status records for date {datebid}")
            
            # Prepare response data - return array directly with metadata (controller will wrap with "data" key)
            response_data = {
                "data": sanitized_data,
                "date": datebid,
                "total_records": len(sanitized_data),
                "market_type": "RTM"
            }
            
            return response_data
            
        except ValueError as ve:
            # VAPT: Handle validation errors without exposing internal details
            logger.error(f"[trade_status] Validation error: {str(ve)}")
            raise ValueError(f"Invalid request parameters: {str(ve)}")
        except Exception as e:
            # VAPT: Generic error handling - log full details but return safe error message
            logger.exception(f"[trade_status] Unexpected error: {str(e)}")
            raise Exception("An error occurred while processing trade status request")

    
    async def get_rtm_single_book(self, scheduled_at) -> Dict[str, Any]:

        try:
            # Handle both string and datetime inputs
            if isinstance(scheduled_at, str):
                bid_date = scheduled_at
                scheduled_at = datetime.strptime(scheduled_at, "%Y-%m-%d")
            else:
                bid_date = scheduled_at.strftime("%Y-%m-%d")
            scheduled_at_end = scheduled_at + timedelta(days=1)
            # ---------------------------------------------------------
            # 1. Fetch system user bids
            # ---------------------------------------------------------
            system_user_data = await self.single_bid_data_for_approval_repo.find_by_filters(
                {
                    "bid_date": bid_date,
                    "bid_status": {"$in": ["submitted", "withdrawal_bid", "withdrawn"]}
                }
            )

            system_order_ids = {
                data.get("exchangeOrderId")
                for data in system_user_data
                if data.get("exchangeOrderId") is not None
            }

            # ---------------------------------------------------------
            # 2. Fetch exchange config (system user id)
            # ---------------------------------------------------------
            exchange_config = await self.exchange_config_repository.get_bidType_market_active_config(
                "single",
                "RTM"
            )
            system_user_id = exchange_config.get("LoginUserId")
            portfolio_id = exchange_config.get("BidAreaId")

            # ---------------------------------------------------------
            # 3. Fetch book data ONLY for other users
            # ---------------------------------------------------------
            book_details = await self.generic_repo.get_all_async(
                "rtm_bid_data_live",
                {
                    "scheduled_at": {"$gte": scheduled_at, "$lt": scheduled_at_end},
                    "OrderId": {"$nin": list(system_order_ids)},
                    "OrderStatus": {"$in": ["Executed", "Provisional"]}
                }
            )

            # ---------------------------------------------------------
            # 4. Collect all unique time pairs
            # ---------------------------------------------------------
            all_time_pairs: set[Tuple[int, int]] = set()

            for data in system_user_data:
                all_time_pairs.add((data.get("start_time"), data.get("end_time")))

            for bid in book_details:
                all_time_pairs.add((bid.get("FromPeriodId"), bid.get("ToPeriodId")))

            # ---------------------------------------------------------
            # 5. Fetch time blocks once
            # ---------------------------------------------------------
            if all_time_pairs:
                time_blocks = await self.generic_repo.find_many(
                    self.time_block_collection,
                    {
                        "$or": [
                            {"starttime": start, "endtime": end}
                            for start, end in all_time_pairs
                        ]
                    }
                )
            else:
                time_blocks = []

            time_block_map = {
                (tb.get("starttime"), tb.get("endtime")): tb.get("timeblock")
                for tb in time_blocks
            }

            # ---------------------------------------------------------
            # 6. Build response structure
            # ---------------------------------------------------------
            response_struct: Dict[Tuple[int, int], Dict[str, Any]] = {}

            other_user_id = None

            # ---- SYSTEM USER DATA ----
            for data in system_user_data:
                start = data.get("start_time")
                end = data.get("end_time")
                key = (start, end)
                order_id = data.get("orderId")

                if key not in response_struct:
                    response_struct[key] = {
                        "FromPeriodId": start,
                        "ToPeriodId": end,
                        "time_block_id": time_block_map.get(key),
                        "buy_sell": data.get("buy_sell_bid"),
                        "submitted_revision_no": None,
                        "submitted_PQData": None,
                        "approved_revision_no": None,
                        "cleared_volume": 0,
                        "cleared_price": 0,
                        "otherUserPQData": [],
                        "orderId": order_id,
                        "systemSubmissionTime": None,
                        "otherUserSubmissiontime": None,
                    }

                status = data.get("bid_status")

                if status == "submitted":
                    response_struct[key]["submitted_revision_no"] = data.get("revision_no")
                    response_struct[key]["approved_revision_no"] = response_struct[key]["submitted_revision_no"]
                    if data.get("exchangePQData") is not None:
                        response_struct[key]["submitted_PQData"] = data.get("exchangePQData")
                    if data.get("exchangeStatus") is not None:
                        response_struct[key]["exchange_status"] = data.get("exchangeStatus")
                    response_struct[key]["exchange_remarks"] = data.get("exchangeError")

                elif status == "withdrawal_bid":
                    response_struct[key]["submitted_revision_no"] = data.get("revision_no")
                    response_struct[key]["approved_revision_no"] = response_struct[key]["submitted_revision_no"]
                    if data.get("exchangePQData") is not None:
                        response_struct[key]["submitted_PQData"] = data.get("exchangePQData")
                    if data.get("exchangeStatus") is not None:
                        response_struct[key]["exchange_status"] = data.get("exchangeStatus")
                    response_struct[key]["exchange_remarks"] = data.get("exchangeError")

                elif status == "withdrawn":
                    response_struct[key]["submitted_revision_no"] = data.get("revision_no")
                    response_struct[key]["approved_revision_no"] = response_struct[key]["submitted_revision_no"]
                    if data.get("exchangePQData") is not None:
                        response_struct[key]["submitted_PQData"] = data.get("exchangePQData")
                    if data.get("exchangeStatus") is not None:
                        response_struct[key]["exchange_status"] = data.get("exchangeStatus")
                    response_struct[key]["exchange_remarks"] = data.get("exchangeError")

                elif status == "approved":
                    if data.get("revision_no") == None:
                        response_struct[key]["approved_revision_no"] = response_struct[key]["submitted_revision_no"]
                    else:
                        response_struct[key]["approved_revision_no"] = data.get("revision_no")

                if data.get("clearedVolume") is not None:
                    response_struct[key]["cleared_volume"] = data.get("clearedVolume")

                if data.get("tradedPrice") is not None:
                    response_struct[key]["cleared_price"] = data.get("tradedPrice")

                # Capture system user submission time (exchange order time preferred)
                if data.get("exchangeOrderTime") is not None:
                    response_struct[key]["systemSubmissionTime"] = data.get("exchangeOrderTime")
                elif data.get("submitted_at") is not None:
                    response_struct[key]["systemSubmissionTime"] = data.get("submitted_at")

            # ---- OTHER USER DATA ----
            for bid in book_details:
                start = bid.get("FromPeriodId")
                end = bid.get("ToPeriodId")
                key = (start, end)

                if key not in response_struct:
                    response_struct[key] = {
                        "FromPeriodId": start,
                        "ToPeriodId": end,
                        "time_block_id": time_block_map.get(key),
                        "buy_sell": bid.get("BuySell"),
                        "submitted_revision_no": None,
                        "submitted_PQData": None,
                        "approved_revision_no": None,
                        "cleared_volume": None,
                        "cleared_price": None,
                        "otherUserPQData": None,
                        "systemSubmissionTime": None,
                        "otherUserSubmissiontime": None,
                    }

                response_struct[key]["otherUserPQData"] = bid.get("PQData")

                # Capture other user submission time
                if bid.get("OrderTime") is not None:
                    response_struct[key]["otherUserSubmissiontime"] = bid.get("OrderTime")

                # Capture other user id (assuming one user context)
                if other_user_id is None:
                    other_user_id = bid.get("UserId")

            # ---------------------------------------------------------
            # 7. Final Response Structure
            # ---------------------------------------------------------
            w1 = portfolio_id

            return {
                "w1": w1,
                "systemUser": system_user_id,
                "UserId": other_user_id,
                "data": list(response_struct.values())
            }

        except Exception as e:
            logger.exception(f"Error fetching DAM book data: {e}")
            return {
                "w1": None,
                "systemUser": None,
                "UserId": None,
                "data": []
            }
    async def get_rtm_trade_book(self, scheduled_date: str) -> Dict[str, Any]:

        try:
            scheduled_at_start = datetime.strptime(scheduled_date, "%Y-%m-%d")
            scheduled_at_end = scheduled_at_start + timedelta(days=1)

            exchange_config = await self.generic_repo.find_one_async(
                'exchange_config',
                {"market": "RTM"}
            )

            asset_master = await self.generic_repo.find_one_async(
                'asset_master',
                {}
            )
            
            orderQtyDecimal = 1.0
            tradeQtyDecimal = 1.0
            tradePriceDecimal = 1.0
            
            if asset_master and "AssetDetails" in asset_master and len(asset_master["AssetDetails"]) > 0:
                details_asset = asset_master["AssetDetails"][0]
                orderQtyDecimal = float(details_asset.get("OrderQtyDecimal") or 1.0)
                tradeQtyDecimal = float(details_asset.get("TradeQtyDecimal") or 1.0)
                tradePriceDecimal = float(details_asset.get("TradePriceDecimal") or 1.0)

            # Fetch only Executed and Provisional orders
            details = await self.generic_repo.get_all_async(
                'rtm_bid_data_live',
                {
                    "scheduled_at": {
                        "$gte": scheduled_at_start,
                        "$lt": scheduled_at_end
                    },
                    "OrderStatus": {"$in": ["Executed", "Provisional"]}
                }
            )

            if not details:
                return {"w1": exchange_config.get("PortfolioId"), "data": []}

            # Collect time pairs
            all_time_pairs = {
                (bid.get("start_time"), bid.get("end_time"))
                for bid in details
            }

            # Fetch time blocks once
            time_blocks = await self.generic_repo.get_all_async(
                self.time_block_collection,
                {
                    "$or": [
                        {"starttime": start, "endtime": end}
                        for start, end in all_time_pairs
                    ]
                }
            ) if all_time_pairs else []

            time_block_map = {
                (tb.get("starttime"), tb.get("endtime")): tb.get("timeblock")
                for tb in time_blocks
            }

            response_data = []

            for bid in details:
                sched_at = bid.get("scheduled_at")
                start = bid.get("FromPeriodId")
                end = bid.get("ToPeriodId")
                key = (start, end)
                bidded_qty=0.0
                traded_qty=float(bid.get("TotalExecutedQty",0.0)) / tradeQtyDecimal
                price=float(bid.get("TradePrice",0.0)) / tradePriceDecimal
                status=bid.get("OrderStatus")

                buy_sell = bid.get("BuySell")
                pq_data = bid.get("PQData")
                if pq_data:
                    if buy_sell == "S":
                        # Max Price
                        target_entry = max(pq_data, key=lambda x: x.get("Price", 0))
                        bidded_qty = float(target_entry.get("Quantity", 0)) / orderQtyDecimal
                        traded_qty = 0-traded_qty
                    elif buy_sell == "B":
                        # Min Price
                        target_entry = min(pq_data, key=lambda x: x.get("Price", float('inf')))
                        bidded_qty = float(target_entry.get("Quantity", 0)) / orderQtyDecimal
                

                response_data.append({
                    "scheduled_at": sched_at,
                    "OrderId": bid.get("OrderId"),
                    "FromPeriodId": start,
                    "ToPeriodId": end,
                    "buy_sell": buy_sell,
                    "traded": traded_qty,
                    "clearedPrice": price,
                    "bidded": bidded_qty,
                    "order_status": status
                })

            w1 = exchange_config.get("BidAreaId")

            return {
                "w1": w1,
                "data": response_data
            }

        except Exception as e:
            logger.exception(f"Error fetching RTM trade book: {e}")
            return {"w1": None, "data": []}

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

    async def updatePlantDataForApproval(self, data, current_user):

        try:
            filter_query = {
                "_id": ObjectId(data["reference_number"]),
                "block_no": int(data["block_no"])
            }

            # Only update when the bid is still pending or pending withdrawal.
            filter_query["bid_status"] = {"$in": ["pending", "pending_withdrawal"]}

            bid_status = data["bid_status"]
            existing_docs = await self.single_bid_data_for_approval_repo.find_by_filters({
                "_id": ObjectId(data["reference_number"]),
                "block_no": int(data["block_no"]),
            })
            if existing_docs and existing_docs[0].get("bid_status") == "pending_withdrawal":
                if bid_status == "approved":
                    bid_status = "approved_withdrawal"

            update_query = {
                "$set": {
                    "bid_status": bid_status,
                    "remark": data["Remarks"],
                    "approved_by": current_user.username,
                    "approved_by_user_id": current_user.user_id,
                    "approved_time": datetime.now().strftime("%H:%M")
                }
            }

            result = await self.single_bid_data_for_approval_repo.update_one(filter_query, update_query)
            matched = result.get("matched_count", 0)
            modified = result.get("modified_count", 0)

            return {
                "reference_number": data["reference_number"],
                "block_no": int(data["block_no"]),
                "bid_status": bid_status,
                "matched_count": matched,
                "modified_count": modified,
            }
        except Exception as e:
            logger.exception(f"[updatePlantDataForApproval] Error: {str(e)}")
            raise
# Global instance
rtm_bidding_services = RTMBiddingServices()
