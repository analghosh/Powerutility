"""
RTM (Real-Time Market) bidding service layer using repository pattern.
Handles business logic for RTM bidding-related data operations with full complexity.
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import date, datetime, timedelta, timezone
from math import isnan
from collections import OrderedDict

from ..repository.rtm_single_bid_data_for_approval_repository import RTMSingleBidDataForApprovalRepository
from ..repository.rtm_product_hardcoded_data_repository import RTMProductHardCodedDataRepository
from ..repository.supply_repository import SupplyRepository
from ..repository.demand_forecast_repository import DemandForecastRepository
from ..repository.market_repository import MarketRepository
from ..repository.master_plants_repository import MasterPlantsRepository
from ..repository.limit_values_repository import LimitValuesRepository
from ..repository.base_repository import GenericRepository
from ...core.config import settings
logger = logging.getLogger(__name__)

def bidPriceNOC():
    """Equivalent of PHP's bidPriceNOC() constant."""
    return 10000

class RTMBiddingService:
    """
    Service class responsible for RTM bidding business logic with full complexity.
    """

    def __init__(self):
        self.supply_repo = SupplyRepository()
        self.single_bid_data_for_approval_repo = RTMSingleBidDataForApprovalRepository()
        self.rtm_product_hardCoded_data_repo = RTMProductHardCodedDataRepository()
        self.plants_list_repo = MasterPlantsRepository()
        self.limit_values_repo = LimitValuesRepository()
        self.demand_repo = DemandForecastRepository()
        self.exchange_repo = MarketRepository()
        self.repo = GenericRepository()
        # Assume getSessionfrom() returns a list of session time strings like ["00:30", "01:00", ...]
        session_times = RTMBiddingService.get_session_from()  
        self.demand_collection = settings.MONGODB_DEMAND_SERVE_COLLECTION

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

    @staticmethod
    def get_session_from():
        """Get session times for RTM calculations."""
        return ["00:30", "01:00", "01:30", "02:00", "02:30", "03:00", "03:30", "04:00",
                "04:30", "05:00", "05:30", "06:00", "06:30", "07:00", "07:30", "08:00",
                "08:30", "09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00",
                "12:30", "13:00", "13:30", "14:00", "14:30", "15:00", "15:30", "16:00",
                "16:30", "17:00", "17:30", "18:00", "18:30", "19:00", "19:30", "20:00",
                "20:30", "21:00", "21:30", "22:00", "22:30", "23:00", "23:30"]

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

    @staticmethod
    def _datetime_to_date_string(dt: datetime) -> str:
        """
        Convert datetime object to date string for frontend compatibility.
        
        Args:
            dt (datetime): Datetime object
            
        Returns:
            str: Date string in 'YYYY-MM-DD' format
        """
        return dt.strftime("%Y-%m-%d")

    @staticmethod
    def _get_timestamp_and_int_from_datetime(dt: datetime) -> tuple:
        """
        Get timestamp and integer date from datetime object.
        
        Args:
            dt (datetime): Datetime object
            
        Returns:
            tuple: (timestamp, date_int)
        """
        return (int(dt.timestamp()), int(dt.strftime("%Y%m%d")))

    async def rtm_single_bid_graph(self, schedule_date, from_block=1, to_block=96):
        """
        Fetch RTM single bid graph data for a given schedule date with full complexity.

        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format.
            from_block (int): Starting time block.
            to_block (int): Ending time block.
        Returns:
            Optional[Dict[str, Any]]: A dictionary containing the RTM data,
                                    or None if no data is found.
        """
        try:
            logger.info(f"Starting RTM single bid graph data fetch for date: {schedule_date}, blocks: {from_block}-{to_block}")
            
            # Parse schedule_date to datetime object
            scheduled_at = self._parse_schedule_date(schedule_date)
            logger.info(f"Using scheduled_at datetime: {scheduled_at}")
            
            # Complex pipeline similar to RTM but adapted for v2 repositories
            pipeline = [
                    {
                        "$match": {
                            "scheduled_at": scheduled_at,
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
                            "from": self.demand_collection,
                            "let": {"t_block": "$_id"},
                            "pipeline": [
                                {
                                    "$match": {
                                        "$expr": {
                                            "$and": [
                                                {"$eq": ["$time_block", "$$t_block"]},
                                                {"$eq": ["$scheduled_at", scheduled_at]},
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
            logger.info(f"Using : {pipeline}")
            result = await self.supply_repo.repository.aggregate_async(self.supply_repo.supply_serve, pipeline)
            
            logger.info(f"RTM single bid graph data fetched successfully: {len(list(result))} records {list(result)}")
            return {"data": result, "scheduled_at": scheduled_at}
            
        except Exception as e:
            logger.error(f"Error fetching RTM single bid graph data: {str(e)}")
            raise Exception(f"Failed to fetch RTM single bid graph data: {str(e)}")

    async def rtm_single_bid_table(self, schedule_date, from_block=1, to_block=96):
        """
        Fetch RTM single bid table data with full complexity.

        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format.
            from_block (int): Starting time block.
            to_block (int): Ending time block.
        Returns:
            Optional[Dict[str, Any]]: A dictionary containing the RTM table data,
                                    or None if no data is found.
        """
        try:
            # Parse schedule_date to datetime object
            scheduled_at = self._parse_schedule_date(schedule_date)
            logger.info(f"Starting RTM single bid table data fetch for scheduled_at: {scheduled_at}, blocks: {from_block}-{to_block}")
            
            pipeline = [
                {"$match": {
                    "scheduled_at": scheduled_at,
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
            getplantsData = list(await self.supply_repo.repository.aggregate_async(self.supply_repo.supply_serve, pipeline))
            
            if not getplantsData:
                return {}

            mustplantPrice = 20
            checkTMvalue = "TR"

            # Hardcoded session data
            getsessionNo = await self.rtm_product_hardCoded_data_repo.get_Data()
            
            plants_list_pipeline = [{"$project": {"_id": 0}}]
            plantData = await self.plants_list_repo.aggregate('plant_master', plants_list_pipeline)
            plant_dict = {p['plant_id']: p for p in plantData}
            
            fix_count = getplantsData[0]['time_block']
            availability_data_array = {}
            # return getplantsData
            # Organize availability data by time block
            for i, availability_value in enumerate(getplantsData):
                array_key = int(availability_value["time_block"]) - fix_count
                

                if array_key not in availability_data_array:
                    availability_data_array[int(array_key)] = {
                        'scheduled_at': availability_value['scheduled_at'],
                        'time_block': availability_value['time_block'],
                        'plants': {}
                    }
                
                plant_key = availability_value['plant_id']
                plant_info = plant_dict.get(plant_key, {})
                
                dispatch_type = plant_info.get('dispatch_type')
                
                
                availability_data_array[int(array_key)]['plants'][plant_key] = {
                    'plant_name': plant_info.get('plant_name'),
                    'plant_key': plant_key,
                    'plant_volume': availability_value['volume'],
                    'dispatch_type': dispatch_type,
                    'PX_Price': plant_info.get('px_price'),
                    'category': plant_info.get('category'),
                    'display_category': availability_value.get('display_category', 'Merit')
                }
                
                
            # return availability_data_array
            # Fetch forecast, limits, and exchange data
            getForecastedValue = await self.demand_repo.get_data_pipeline("demand_serve", from_block, to_block, scheduled_at)
            
            TotalAvailability = self.total_availability_new(availability_data_array, getForecastedValue, from_block, to_block)
            
            # Convert datetime back to string for limit_values_repo calls (if needed)
            schedule_date_str = self._datetime_to_date_string(scheduled_at)

            # User Requirement: If time > 10:30 PM (22:30), start using tomorrow's limit values
            now = datetime.now()
            if now.hour > 22 or (now.hour == 22 and now.minute >= 30):
                schedule_date_str = (now + timedelta(days=1)).strftime('%Y-%m-%d')
                logger.info(f"RTM time > 22:30, using tomorrow's limits: {schedule_date_str}")

            forecastedPrice = await self.limit_values_repo.get_data_by_ID_market('limit_values',"RTM_forecasted_price","RTM", from_block, to_block, schedule_date_str)
            bufferPrice = await self.limit_values_repo.get_data_by_ID_market('limit_values',"Buffer","RTM", from_block, to_block, schedule_date_str)
            ceilingPrice = await self.limit_values_repo.get_data_by_ID_market('limit_values',"ceilingPrice","RTM", from_block, to_block, schedule_date_str)
            n_1Limits = await self.limit_values_repo.get_data_by_ID_market('limit_values',"n_1","RTM", from_block, to_block, schedule_date_str)
            noc_buyLimits = await self.limit_values_repo.get_data_by_ID_market('limit_values',"noc_buy","RTM", from_block, to_block, schedule_date_str)
            noc_sellLimits = await self.limit_values_repo.get_data_by_ID_market('limit_values',"noc_sell","RTM", from_block, to_block, schedule_date_str)
            
            typeofExchangeIEX = "IEX-100"
            typeofExchangePXIL = "PXIL-0"

            data = {}
            total_sell_volume = 0
            
            for blk, TotalblkwiseData in enumerate(TotalAvailability):
                
                ForecastedPrice = round(forecastedPrice[blk].get('values', 0)) if blk < len(forecastedPrice) else 0

                # return ForecastedPrice
                if isnan(ForecastedPrice):
                    ForecastedPrice = 0

                
                ceilingPriceInner = ceilingPrice[blk]["values"] if blk < len(ceilingPrice) and "values" in ceilingPrice[blk] else 0
                bufferPriceInner = bufferPrice[blk]["values"] if blk < len(bufferPrice) and "values" in bufferPrice[blk] else 0

                n_1LimitsInner = n_1Limits[blk]["values"] if blk < len(n_1Limits) and "values" in n_1Limits[blk] else 0

                noc_buyLimitsInner = noc_sellLimits[blk]["values"] if blk < len(noc_sellLimits) and "values" in noc_sellLimits[blk] else 0
                noc_sellLimitsInner = noc_sellLimits[blk]["values"] if blk < len(noc_sellLimits) and "values" in noc_sellLimits[blk] else 0
                totalBlockBid = 0
                typeofExchangeIEXblk = typeofExchangeIEX
                typeofExchangePXILblk = typeofExchangePXIL

                total_diff = TotalblkwiseData["diff_for_bid_cal"]
                total_diff_for_must = TotalblkwiseData["diff_for_bid_cal"]
                availability_data_array_b_wise = availability_data_array[blk] if blk < len(availability_data_array) else {}
                
                # return getsessionNo[int(availability_data_array_b_wise['time_block'])]
                tptal = 0
                plnt_100_volume = 0
                plkey = 0

                # Handling Must Plants
                if total_diff_for_must < 0:
                    remaining_volume = round(total_diff_for_must) * -1
                    row = {
                        'Date': schedule_date_str,
                        # 'start_time': availability_data_array_b_wise['start_time'],
                        # 'end_tme': availability_data_array_b_wise['end_time'],
                        # 'full_time': availability_data_array_b_wise['full_time'],
                        'time_block': TotalblkwiseData['BlockNo'],
                        'session_number': getsessionNo[int(TotalblkwiseData['BlockNo'])-1].get("session_number") or getsessionNo[int(TotalblkwiseData['BlockNo'])-1].get("Session_Id"),
                        'forecast': round(TotalblkwiseData['forecast_value']),
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
                        'remaining_volume': remaining_volume,
                        'sell_volume': remaining_volume,
                        'actual_volume': remaining_volume,
                        'category': "Must_Plants_cat",
                        'plant_name': "Must_Plants",
                        'display_category': "All",  # Modified from "Must Plants Cat" to "All"
                        'dispatch_type': "must",
                        'total_sell_volume': remaining_volume,
                        'Ex_split_string': f"IEX-{typeofExchangeIEXblk}_PXIL-{typeofExchangePXILblk}"
                    }
                    data.setdefault('table_data', {}).setdefault(plkey, {})[blk] = row
                    total_diff = 0
                    total_diff_for_must = 0
                    plkey += 1
                else:
                    row = {
                        'Date': schedule_date_str,
                        # 'start_time': availability_data_array_b_wise['start_time'],
                        # 'end_tme': availability_data_array_b_wise['end_time'],
                        # 'full_time': availability_data_array_b_wise['full_time'],
                        'time_block': TotalblkwiseData['BlockNo'],
                        'session_number': getsessionNo[int(TotalblkwiseData['BlockNo'])-1].get("session_number") or getsessionNo[int(TotalblkwiseData['BlockNo'])-1].get("Session_Id"),
                        'forecast': round(TotalblkwiseData['forecast_value']),
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
                
                # Handling Merit Plants
                for plnamekey, plant in availability_data_array[blk]["plants"].items():
                    # return plant
                    
                    px_price = plant.get('PX_Price') if plant.get('PX_Price') is not None else 0
                    dispatch_type = plant.get("dispatch_type", "")
                    plant_volume = float(plant.get("plant_volume"))
                    category = plant.get("category", "")
                    display_category = plant.get("display_category", "")
                    plant_name = plant.get("plant_name", "")
                    
                    if dispatch_type == "Merit":
                        # value = TotalblkwiseData["surplus_deficit"]
                        if TotalblkwiseData["surplus_deficit"] > 0:
                            if checkTMvalue == "TM":
                                # TMvalueofPl = float(allTMValues.get(f"{availability_data_array_b_wise['scheduled_at']}_{plant_name}_{availability_data_array_b_wise['time_block']}", 0))
                                TMvalueofPl = 0
                                plnt_fourty_five_vol = plant_volume - TMvalueofPl
                            else:
                                plnt_fourty_five_vol = plant_volume * 0.45

                            plnt_100_volume = plant_volume
                            sell_volume = 0

                            if total_diff > 0:
                                if total_diff > plnt_fourty_five_vol:
                                    total_diff -= plnt_fourty_five_vol
                                    sell_volume = plnt_fourty_five_vol
                                else:
                                    sell_volume = total_diff
                                    total_diff = 0
                            elif total_diff == 0:
                                sell_volume = 0

                            tptal += plnt_100_volume
                            
                            if round(sell_volume) >= 0.01 and plnt_100_volume != 0:
                                row = {
                                    'Date': schedule_date_str,
                                    # 'start_time': availability_data_array_b_wise['start_time'],
                                    # 'end_tme': availability_data_array_b_wise['end_time'],
                                    # 'full_time': availability_data_array_b_wise['full_time'],
                                    'time_block': TotalblkwiseData['BlockNo'],
                                    'session_number': getsessionNo[int(TotalblkwiseData['BlockNo'])-1].get("session_number") or getsessionNo[int(TotalblkwiseData['BlockNo'])-1].get("Session_Id"),
                                    'forecast': round(TotalblkwiseData['forecast_value']),
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
                                    'remaining_volume': float(total_diff),
                                    'sell_volume': round(sell_volume),
                                    'actual_volume': float(round(plnt_100_volume)),
                                    'category': category,
                                    'plant_name': plant_name,
                                    'display_category': display_category,
                                    'dispatch_type': dispatch_type,
                                    'total_sell_volume': total_sell_volume + sell_volume,
                                    'Ex_split_string': f"IEX-{typeofExchangeIEXblk}_PXIL-{typeofExchangePXILblk}"
                                }
                                data.setdefault('table_data', {}).setdefault(plkey, {})[blk] = row
                                plkey += 1

                    else:
                        # Non-Merit Plants
                        
                        row = {
                            'Date': schedule_date_str,
                            # 'start_time': availability_data_array_b_wise['start_time'],
                            # 'end_tme': availability_data_array_b_wise['end_time'],
                            # 'full_time': availability_data_array_b_wise['full_time'],
                            'time_block': TotalblkwiseData['BlockNo'],
                            'session_number': getsessionNo[int(TotalblkwiseData['BlockNo'])-1].get("session_number") or getsessionNo[int(TotalblkwiseData['BlockNo'])-1].get("Session_Id"),
                            'forecast': round(TotalblkwiseData['forecast_value']),
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
                            'remaining_volume': float(total_diff),
                            'sell_volume': 0,
                            'actual_volume': float(round(plant_volume)),
                            'category': category,
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
            logger.error(f"Error fetching RTM single bid table data: {str(e)}")
            raise Exception(f"Failed to fetch RTM single bid table data: {str(e)}")


    async def approval_request_list(self, schedule_date, from_block=1, to_block=96, page=1, page_size=100):
        """
        Fetch RTM approval request list with full complexity.

        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format.
            from_block (int): Starting time block.
            to_block (int): Ending time block.
        Returns:
            Optional[Dict[str, Any]]: A dictionary containing the RTM approval request list,
                                    or None if no data is found.
        """
        try:
            logger.info(f"Starting RTM approval request list fetch for date: {schedule_date}, blocks: {from_block}-{to_block}")
            
            # Handle default schedule_date
            if not schedule_date:
                schedule_date = '2025-08-04'  # Temporary hardcoded fallback
            
            # Parse schedule_date to datetime object
            scheduled_at = self._parse_schedule_date(schedule_date)
            logger.info(f"Using scheduled_at datetime: {scheduled_at}")
            
            # First, let's check if any data exists for debugging
            test_filter = {
                "scheduled_at": scheduled_at
            }
            test_records = await self.single_bid_data_for_approval_repo.find_by_filters(test_filter)
            logger.info(f"Found {len(test_records)} total records for scheduled_at {scheduled_at}")
            
            if test_records:
                # Log sample record structure for debugging
                sample_record = test_records[0]
                logger.info(f"Sample record fields: {list(sample_record.keys())}")
                logger.info(f"Sample record block_no: {sample_record.get('block_no')}")
                logger.info(f"Sample record bid_status: {sample_record.get('bid_status')}")
                logger.info(f"Sample record sheet_market: {sample_record.get('sheet_market')}")
            
            # Simple filter to get matching records using scheduled_at datetime
            filter_query = {
                "scheduled_at": scheduled_at,
                "sheet_market": "INDIA-RTM",
                "bid_status": {"$in": ["pending", "submitted"]},
                "block_no": {"$gte": from_block, "$lte": to_block}
            }
            
            # Get records using simple find
            records = await self.repo.get_all_async(
                collection_name=self.single_bid_data_for_approval_repo.collection_name,
                filter=filter_query,
                skip=(page - 1) * page_size,
                limit=page_size
            )
            logger.info(f"Found {len(records)} matching records")
            
            # Process records in Python to handle nested Plants structure
            result = []
            for record in records:
                block_no = record.get("block_no")
                plants_data = record.get("Plants", {})
                
                plants_list = []
                total_volume = 0
                total_price = 0
                plant_count = 0
                
                for plant_name, plant_info in plants_data.items():
                    try:
                        bid_volume = float(plant_info.get("bid_volume", 0))
                        bid_price = float(plant_info.get("bid_price", 0))
                        
                        plants_list.append({
                            "plant_name": plant_info.get("bid_plant_name", plant_name),
                            "plant_category": plant_info.get("bid_plant_category", ""),
                            "bid_volume": bid_volume,
                            "bid_price": bid_price,
                            "bid_default_volume": float(plant_info.get("bid_default_volume", 0)),
                            "bid_default_price": float(plant_info.get("bid_default_price", 0)),
                            "rtm_forecast_price": float(plant_info.get("rtm_forecast_Price", 0))
                        })
                        
                        total_volume += bid_volume
                        total_price += bid_price
                        plant_count += 1
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Error processing plant {plant_name}: {e}")
                        continue
                
                avg_price = total_price / plant_count if plant_count > 0 else 0
                
                result.append({
                    "_id": block_no,
                    "block_no": block_no,
                    "time_block": record.get("time_block", ""),
                    "start_time": record.get("start_time", ""),
                    "end_time": record.get("end_time", ""),
                    "bid_status": record.get("bid_status", ""),
                    "buy_sell_bid": record.get("buy_sell_bid", ""),
                    "Ex_split_string": record.get("Ex_split_string", ""),
                    "plants": plants_list,
                    "total_volume": total_volume,
                    "avg_price": avg_price,
                    "plant_count": plant_count
                })
            
            # Sort by block number
            result.sort(key=lambda x: x.get("_id", 0))
            
            logger.info(f"RTM approval request list processed successfully: {len(result)} requests")
            return {"data": result, "scheduled_at": scheduled_at}
            
        except Exception as e:
            logger.error(f"Error fetching RTM approval request list: {str(e)}")
            raise Exception(f"Failed to fetch RTM approval request list: {str(e)}")

    async def bid_creation_post(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create RTM bid data with full complexity.

        Args:
            post_data (Dict[str, Any]): Bid creation data
            
        Returns:
            Dict[str, Any]: Creation result
        """
        try:
            logger.info(f"Starting RTM bid creation with data: {post_data}")
            
            # Complex bid creation logic similar to RTM
            documents_to_insert = []
            
            # Extract bid data from post_data
            bid_date = post_data.get("bidingDate", "")
            sell_buy_bid_type = post_data.get("sell_buy_bid_type", [])
            time_block_range = post_data.get("time_block_range", [])
            
            logger.info(f"Processing bid data - Date: {bid_date}, Types: {len(sell_buy_bid_type)}, Blocks: {len(time_block_range)}")
            
            # Get session data for complex calculations
            getsessionNo = await self.rtm_product_hardCoded_data_repo.get_Data()
            getPlantList = await self.plants_list_repo.get_all_plants()
            
            logger.info(f"Retrieved session data: {len(getsessionNo) if getsessionNo else 0} records")
            logger.info(f"Retrieved plant list: {len(getPlantList) if getPlantList else 0} records")
            
            # Complex limit values retrieval
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
            
            allTMValues = await self.limit_values_repo.get_limit_values_by_filters(
                schedule_date=yesterday,
                end_date=tomorrow,
                limit_id="TM",
                market="RTM",  # Using RTM market
                category="Limits"
            )
            
            logger.info(f"Retrieved TM limit values: {len(allTMValues) if allTMValues else 0} records")
            
            allSEZValues = await self.limit_values_repo.get_limit_values_by_filters(
                schedule_date=yesterday,
                end_date=tomorrow,
                limit_id="SEZ",
                market="RTM",  # Using RTM market
                category="Limits"
            )
            
            logger.info(f"Retrieved SEZ limit values: {len(allSEZValues) if allSEZValues else 0} records")
            
            allMLTPValues = await self.limit_values_repo.get_limit_values_by_filters(
                schedule_date=yesterday,
                end_date=tomorrow,
                limit_id="Multiplier",
                market="RTM",  # Using RTM market
                category="Limits"
            )
            
            logger.info(f"Retrieved Multiplier limit values: {len(allMLTPValues) if allMLTPValues else 0} records")
            
            for i, block_range in enumerate(time_block_range):
                if i < len(sell_buy_bid_type):
                    bid_type = sell_buy_bid_type[i]
                    
                    # Extract block number from time_block_range format
                    block_parts = block_range.split("_")
                    if len(block_parts) >= 2:
                        block_no = int(block_parts[1])
                        
                        # Create complex document for RTM bid similar to original
                        # Parse bid_date to datetime object
                        try:
                            scheduled_at_datetime = datetime.strptime(bid_date, '%Y-%m-%d')
                        except ValueError:
                            logger.error(f"Invalid bid_date format: {bid_date}")
                            continue
                            
                        doc = {
                            "scheduled_at": scheduled_at_datetime,  # Store as datetime object
                            "market_type": "RTM",          # Using market_type instead of market
                            "time_block": block_no,        # Using time_block instead of block
                            "bid_type": bid_type,
                            "plant_id": f"PLANT_{block_no}",  # Add plant_id for lookups
                            "bid_status": "created",  # Add bid_status for approval list
                            "created_at": datetime.now(),
                            "status": "created",
                            "session_data": getsessionNo,
                            "plant_list": getPlantList,
                            "limit_values": {
                                "tm_values": allTMValues,
                                "sez_values": allSEZValues,
                                "multiplier_values": allMLTPValues
                            }
                        }
                        
                        # Add volume and price data if available
                        volume_key = f"bid_volume_{block_no}"
                        price_key = f"bid_price_{block_no}"
                        
                        if volume_key in post_data:
                            doc["bid_volume"] = post_data[volume_key]
                        if price_key in post_data:
                            doc["bid_price"] = post_data[price_key]
                        
                        # Add exchange split string
                        ex_split_key = f"Ex_split_string_{block_no}"
                        if ex_split_key in post_data:
                            doc["exchange_split"] = post_data[ex_split_key]
                        
                        # Add approval data
                        if "approve_by" in post_data and i < len(post_data["approve_by"]):
                            doc["approve_by"] = post_data["approve_by"][i]
                        if "reference_number" in post_data and i < len(post_data["reference_number"]):
                            doc["reference_number"] = post_data["reference_number"][i]
                        if "bid_status" in post_data and i < len(post_data["bid_status"]):
                            doc["bid_status"] = post_data["bid_status"][i]
                        if "remarks" in post_data and i < len(post_data["remarks"]):
                            doc["remarks"] = post_data["remarks"][i]
                        
                        documents_to_insert.append(doc)
                        
                        logger.info(f"Created bid document for block {block_no}, type {bid_type}")
            
            # Insert documents
            if documents_to_insert:
                result = await self.single_bid_data_for_approval_repo.insert_many(documents_to_insert)
                logger.info(f"RTM bid creation successful: {len(documents_to_insert)} records inserted")
                return {"data": True, "inserted_count": len(documents_to_insert)}
            else:
                logger.warning("No RTM bid documents to insert")
                return {"data": False, "message": "No valid bid data provided"}
                
        except Exception as e:
            logger.error(f"Error creating RTM bid data: {str(e)}")
            raise Exception(f"Failed to create RTM bid data: {str(e)}")

    async def submit_for_approval_post_data(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit RTM bid data for approval with full complexity.
        Handles both structured data format and bid creation format.

        Args:
            request_data (Dict[str, Any]): Approval request data
            
        Returns:
            Dict[str, Any]: Submission result
        """
        try:
            logger.info(f"Starting RTM bid submission for approval")
            
            # Validate request data structure
            if not isinstance(request_data, dict):
                raise ValueError("Request data must be a dictionary")
            
            current_user = request_data.get("current_user", "")
            
            if not current_user:
                raise ValueError("current_user is required")
            
            # Check if this is structured data format (with 'data' array) or bid creation format
            data = request_data.get("data", [])
            
            if isinstance(data, list) and len(data) > 0:
                # Handle structured data format
                logger.info(f"Processing structured approval data for user: {current_user}, data items: {len(data)}")
                documents_to_insert = await self._process_structured_approval_data(data, current_user)
            else:
                # Handle bid creation format - transform to approval format
                logger.info(f"Processing bid creation format for approval submission")
                documents_to_insert = await self._process_bid_creation_format(request_data, current_user)
            
            # Insert documents
            if documents_to_insert:
                result = await self.single_bid_data_for_approval_repo.insert_many(documents_to_insert)
                logger.info(f"RTM bid approval submission successful: {len(documents_to_insert)} records")
                return {"data": True, "submitted_count": len(documents_to_insert)}
            else:
                logger.warning("No RTM bid documents to submit for approval")
                return {"data": False, "message": "No valid bid data to submit for approval"}
                
        except Exception as e:
            logger.error(f"Error submitting RTM bid data for approval: {str(e)}")
            raise Exception(f"Failed to submit RTM bid data for approval: {str(e)}")

    async def _process_structured_approval_data(self, data: List[Dict[str, Any]], current_user: str) -> List[Dict[str, Any]]:
        """Process structured approval data format"""
        documents_to_insert = []
        
        for i, block_data in enumerate(data):
            if not isinstance(block_data, dict):
                logger.warning(f"Skipping invalid block data at index {i}: not a dictionary")
                continue
            
            block_no = block_data.get("block_no", 0)
            bid_date = block_data.get("bid_date", "")
            buy_sell_bid = block_data.get("buy_sell_bid", "")
            bid_plant_names = block_data.get("bid_plant_name", [])
            # Validate required fields
            if not block_no:
                logger.warning(f"Skipping block data at index {i}: missing block_no")
                continue
            
            if not isinstance(bid_plant_names, list):
                logger.warning(f"Skipping block {block_no}: bid_plant_name is not a list")
                continue
            
            logger.info(f"Processing block {block_no}, date '{bid_date}', bid type '{buy_sell_bid}', plants: {len(bid_plant_names)}")
            
            for plant_data in bid_plant_names:
                # Validate and parse bid_date
                try:
                    if not bid_date or bid_date == "string" or bid_date == "":
                        logger.warning(f"Invalid bid_date '{bid_date}' for block {block_no}, skipping")
                        continue
                    scheduled_at = datetime.strptime(bid_date, '%Y-%m-%d')
                except ValueError as date_error:
                    logger.error(f"Invalid date format '{bid_date}' for block {block_no}: {date_error}")
                    continue
                
                # Create complex document similar to RTM
                doc = {
                    "scheduled_at": scheduled_at,  # DateTime object (already parsed above)
                    "market_type": "RTM",
                    "time_block": block_no,
                    "block_no": block_no,  # Add for query compatibility
                    "bid_type": buy_sell_bid,
                    "plant_name": plant_data.get("bid_plant_name", ""),
                    "plant_key": plant_data.get("bid_plant_key", ""),
                    "plant_category": plant_data.get("bid_plant_category", ""),
                    "bid_volume": plant_data.get("bid_volume", 0),
                    "bid_price": plant_data.get("bid_price", 0),
                    "bid_default_volume": plant_data.get("bid_default_volume", 0),
                    "bid_default_price": plant_data.get("bid_default_price", 0),
                    "rtm_forecast_price": plant_data.get("rtm_forecast_Price", 0),
                    "current_user": current_user,
                    "status": "submitted",
                    "bid_status": "submitted",  # Add for query compatibility
                    "sheet_market": "INDIA-RTM",  # Add for query compatibility
                    "created_at": datetime.now(),
                    "submitted_at": datetime.now(),
                    "revision_no": block_data.get("revision_no", 1),
                    "session_block": block_data.get("session_block", 1),
                    "exchange_split": block_data.get("Ex_split_string", ""),
                    "selected_action_type": block_data.get("selected_action_type", "")
                }
                documents_to_insert.append(doc)
                
                logger.info(f"Created approval document for plant {plant_data.get('bid_plant_name', '')}")
        
        return documents_to_insert

    async def _process_bid_creation_format(self, request_data: Dict[str, Any], current_user: str) -> List[Dict[str, Any]]:
        """Process bid creation format and transform to approval format"""
        documents_to_insert = []
        
        # Extract data from bid creation format
        time_block_dates = request_data.get("time_block_dates", [])
        time_block_range = request_data.get("time_block_range", [])
        bid_category = request_data.get("bid_category", [])
        bid_plant_name = request_data.get("bid_plant_name", [])
        bid_default_price = request_data.get("bid_default_price", [])
        rtm_forecast_range = request_data.get("rtm_forecast_range", [])
        
        if not time_block_dates:
            raise ValueError("time_block_dates is required")
        
        logger.info(f"Processing bid creation format: {len(time_block_dates)} time blocks, {len(bid_plant_name)} plants")
        
        # Process each time block
        for block_idx, time_block_date in enumerate(time_block_dates):
            # Extract block number from time_block_date (e.g., "04-11-2025_78" -> 78)
            try:
                block_no = int(time_block_date.split('_')[-1])
                bid_date = time_block_date.split('_')[0]
                # Convert date format from DD-MM-YYYY to YYYY-MM-DD
                date_parts = bid_date.split('-')
                if len(date_parts) == 3:
                    bid_date = f"{date_parts[2]}-{date_parts[1]}-{date_parts[0]}"
            except (ValueError, IndexError):
                logger.warning(f"Invalid time_block_date format: {time_block_date}")
                continue
            
            # Get bid type for this block
            buy_sell_bid = request_data.get(f"buy_sell_bid_type_{block_no}", "Buy")
            exchange_split = request_data.get(f"Exsplitstringblk_{block_no}", "")
            bid_volumes = request_data.get(f"bid_volume_{block_no}", [])
            bid_prices = request_data.get(f"bid_price_{block_no}", [])
            bid_default_volumes = request_data.get(f"bid_default_volume_{block_no}", [])
            
            rtm_forecast_price = 0
            if block_idx < len(rtm_forecast_range):
                try:
                    rtm_forecast_price = float(rtm_forecast_range[block_idx])
                except (ValueError, TypeError):
                    rtm_forecast_price = 0
            
            logger.info(f"Processing block {block_no}, date {bid_date}, type {buy_sell_bid}")
            
            # Process each plant for this block
            for plant_idx, plant_name in enumerate(bid_plant_name):
                # Get plant-specific data
                plant_category = bid_category[plant_idx] if plant_idx < len(bid_category) else ""
                default_price = float(bid_default_price[plant_idx]) if plant_idx < len(bid_default_price) else 0
                
                # Get block-specific bid data
                bid_volume = 0
                bid_price = default_price
                bid_default_volume = 0
                
                if plant_idx < len(bid_volumes):
                    try:
                        bid_volume = float(bid_volumes[plant_idx])
                    except (ValueError, TypeError):
                        bid_volume = 0
                
                if plant_idx < len(bid_prices):
                    try:
                        bid_price = float(bid_prices[plant_idx])
                    except (ValueError, TypeError):
                        bid_price = default_price
                
                if plant_idx < len(bid_default_volumes):
                    try:
                        bid_default_volume = float(bid_default_volumes[plant_idx])
                    except (ValueError, TypeError):
                        bid_default_volume = 0
                
                # Create approval document
                try:
                    scheduled_at_datetime = datetime.strptime(bid_date, '%Y-%m-%d')
                except ValueError:
                    logger.error(f"Invalid date format for scheduled_at: {bid_date}")
                    continue
                
                doc = {
                    "scheduled_at": scheduled_at_datetime,  # DateTime object
                    "market_type": "RTM",
                    "time_block": block_no,
                    "bid_type": buy_sell_bid,
                    "plant_name": plant_name,
                    "plant_key": plant_name.lower().replace(" ", "_").replace("/", "_"),
                    "plant_category": plant_category,
                    "bid_volume": bid_volume,
                    "bid_price": bid_price,
                    "bid_default_volume": bid_default_volume,
                    "bid_default_price": default_price,
                    "rtm_forecast_price": rtm_forecast_price,
                    "current_user": current_user,
                    "status": "submitted",
                    "bid_status": "submitted",  # Add for query compatibility
                    "sheet_market": "INDIA-RTM",  # Add for query compatibility
                    "block_no": block_no,  # Add for query compatibility
                    "created_at": datetime.now(),
                    "submitted_at": datetime.now(),
                    "revision_no": 1,
                    "session_block": 1,
                    "exchange_split": exchange_split,
                    "selected_action_type": "submit"
                }
                documents_to_insert.append(doc)
                
                logger.info(f"Created approval document for plant {plant_name}, block {block_no}")
        
        return documents_to_insert

    async def submit_for_pre_exchange_data(self, data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Submit RTM bid data for pre-exchange processing with full complexity.

        Args:
            data (List[Dict[str, Any]]): Pre-exchange data
            
        Returns:
            Dict[str, Any]: Processing result
        """
        try:
            logger.info(f"Starting RTM pre-exchange data submission with {len(data)} records")
            
            updated_count = 0
            
            # Complex pre-exchange processing logic similar to RTM
            for item in data:
                reference_number = item.get("reference_number", "")
                bid_status = item.get("bid_status", "")
                remarks = item.get("Remarks", "")
                action_by = item.get("action_by", "")
                block_no = item.get("block_no", "")
                
                logger.info(f"Processing pre-exchange item: reference {reference_number}, status {bid_status}, block {block_no}")
                
                # Validate and convert block_no to integer
                try:
                    time_block = int(block_no) if block_no and str(block_no).isdigit() else 0
                except (ValueError, TypeError):
                    logger.warning(f"Invalid block_no value: {block_no}, using 0 as default")
                    time_block = 0
                
                # Complex update query similar to RTM
                filter_query = {
                    "market_type": "RTM",          # Using market_type instead of market
                    "time_block": time_block,      # Using validated time_block
                    "reference_number": reference_number
                }
                
                update_query = {
                    "$set": {
                        "bid_status": bid_status,
                        "remarks": remarks,
                        "action_by": action_by,
                        "processed_at": datetime.now(),
                        "updated_at": datetime.now(),
                        "pre_exchange_status": "processed",
                        "exchange_ready": True if bid_status == "approved" else False
                    }
                }
                
                result = await self.single_bid_data_for_approval_repo.update_one(filter_query, update_query)
                if result.get("modified_count", 0) > 0:
                    updated_count += 1
                    logger.info(f"Updated pre-exchange record for reference {reference_number}")
            
            logger.info(f"RTM pre-exchange processing completed: {updated_count} records updated")
            return {"data": True, "updated_count": updated_count}
            
        except Exception as e:
            logger.error(f"Error submitting RTM bid data for pre-exchange processing: {str(e)}")
            raise Exception(f"Failed to submit RTM bid data for pre-exchange processing: {str(e)}")

    async def rtm_scheduling_table(self, schedule_date: Optional[str]) -> Dict[str, Any]:
        """
        Calculate RTM scheduling table data with full complexity.

        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format.
            
        Returns:
            Dict[str, Any]: RTM scheduling calculation data
        """
        try:
            logger.info(f"Starting RTM scheduling table calculation for date: {schedule_date}")
            
            # Handle default schedule_date
            if not schedule_date:
                schedule_date = '2025-08-04'  # Temporary hardcoded fallback
            
            # Parse schedule_date to datetime object
            scheduled_at = self._parse_schedule_date(schedule_date)
            logger.info(f"Using scheduled_at datetime: {scheduled_at}")
            
            logger.info(f"Using schedule date: {schedule_date}")
            
            # Complex scheduling calculation similar to RTM
            scheduling_data = await self.supply_repo.get_supply_raw(
                date_from=schedule_date,
                date_to=schedule_date
            )
            
            logger.info(f"Retrieved scheduling data: {len(scheduling_data) if scheduling_data else 0} records")
            
            # Additional complex calculations similar to RTM
            demand_forecast = await self.demand_repo.get_demand_forecast_data_pipeline(
                collection_name="demand_serve",  # Using demand_serve instead of serve_demand
                target_date=datetime.strptime(schedule_date, '%Y-%m-%d').date(),
                market_type="IDF"  # Using IDF for RTM instead of DAF
            )

            logger.info(f"Retrieved demand forecast: {len(demand_forecast) if demand_forecast else 0} records")

            exchange_data = await self.exchange_repo.get_exchange_data(
                schedule_date=schedule_date,
                current_block=self.currentBlk  # Using current block for RTM
            )
            
            logger.info(f"Retrieved exchange data: {len(exchange_data) if exchange_data else 0} records")
            
            logger.info(f"RTM scheduling table data calculated successfully")
            return {
                "data": {
                    "scheduling_data": scheduling_data,
                    "demand_forecast": demand_forecast,
                    "exchange_data": exchange_data,
                    "calculation_metadata": {
                        "schedule_date": schedule_date,
                        "calculated_at": datetime.now().isoformat(),
                        "blocks_processed": self.currentBlk,
                        "market_type": "RTM"
                    }
                },
                "schedule_date": schedule_date
            }
            
        except Exception as e:
            logger.error(f"Error calculating RTM scheduling table data: {str(e)}")
            raise Exception(f"Failed to calculate RTM scheduling table data: {str(e)}")

    async def get_total_availability(self, get_plants_data: List[Dict[str, Any]], get_forecasted_value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Calculate total availability with full RTM complexity.
        
        Args:
            get_plants_data: Plant data for availability calculation
            get_forecasted_value: Forecasted demand values
            
        Returns:
            List[Dict[str, Any]]: Total availability data
        """
        try:
            logger.info(f"Calculating total availability for {len(get_plants_data)} plant data records")
            
            data_ar = []

            for i, block_data in enumerate(get_plants_data):
                bltotalvolume = 0
                volume_for_bid_submision = 0
                blk_volume_sum_fiftyfive = 0
                blk_volume_sum_must = 0
                total_volume_mritbl = 0
                must_merit_fiftyfive_volume_bl_sum = 0

                for bl_plants in block_data.get("plants", []):
                    pl_volume = float(bl_plants.get("plant_volume") or 0)
                    plnt_volume_fiftyfive = 0
                    total_volume_mrit = 0
                    plnt_valume_must = 0

                    dispatch_type = bl_plants.get("dispatch_type") or "Must"
                    if dispatch_type == "Merit":
                        plnt_volume_fiftyfive = pl_volume * 0.55
                        total_volume_mrit = pl_volume
                    else:
                        plnt_valume_must = pl_volume

                    bltotalvolume += pl_volume
                    volume_for_bid_submision += pl_volume
                    total_volume_mritbl += total_volume_mrit
                    blk_volume_sum_fiftyfive += plnt_volume_fiftyfive
                    blk_volume_sum_must += plnt_valume_must
                    must_merit_fiftyfive_volume_bl_sum = blk_volume_sum_fiftyfive + blk_volume_sum_must

                forecast_value = float(get_forecasted_value[i].get("totalvol") or 0)

                data_ar.append({
                    "bltotalvolume": bltotalvolume,
                    "full_time": block_data.get("full_time"),
                    "BlockNo": block_data.get("time_block"),  # Using time_block instead of BlockNo
                    "volume_for_bid_submision": volume_for_bid_submision,
                    "total_volume_mritbl": total_volume_mritbl,
                    "total_volume_mritbl_45": total_volume_mritbl * 0.45,
                    "blk_volume_sum_fiftyfive": blk_volume_sum_fiftyfive,
                    "blk_volume_sum_must": blk_volume_sum_must,
                    "must_merit_fiftyfive_volume_bl_sum": must_merit_fiftyfive_volume_bl_sum,
                    "forecast_value": forecast_value,
                    "diff_for_bid_cal": forecast_value - must_merit_fiftyfive_volume_bl_sum,
                    "surplus_deficit": bltotalvolume - forecast_value,
                    # Include additional fields
                    "plant_name": bl_plants.get("plant_name"),
                    "plant_id": bl_plants.get("plant_id"),
                    "c_s": bl_plants.get("c_s"),
                    "TM": bl_plants.get("TM"),
                    "px_price": bl_plants.get("PX_price"),  # Using PX_price from plant_master
                    "plant_volume": bl_plants.get("plant_volume"),
                    "forecasted_price": block_data.get("forecasted_price"),
                    "buffer_limit": block_data.get("buffer_limit"),
                    "ceiling_price": block_data.get("ceiling_price"),
                    "n1_limit": block_data.get("n1_limit"),
                    "noc_buy_limit": block_data.get("noc_buy_limit"),
                    "noc_sell_limit": block_data.get("noc_sell_limit"),
                    "iex_split": block_data.get("iex_split"),
                    "pxil_split": block_data.get("pxil_split"),
                    "Session_Id": block_data.get("Session_Id")
                })

            logger.info(f"Total availability calculation completed: {len(data_ar)} records")
            return data_ar
            
        except Exception as e:
            logger.error(f"Error calculating total availability: {str(e)}")
            raise Exception(f"Failed to calculate total availability: {str(e)}")

    async def cal_scheduling_table_data(self, schedule_date: Optional[str]) -> Dict[str, Any]:
        """
        Calculate RTM scheduling table data with full complexity.
        
        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format
            
        Returns:
            Dict[str, Any]: RTM scheduling calculation data
        """
        try:
            logger.info(f"Calculating RTM scheduling table data for date: {schedule_date}")
            
            if schedule_date:
                schedule_date = schedule_date
            else:
                # Temporary: Hardcoded schedule date for fetching RTM records
                schedule_date = '2025-08-04'
            
            logger.info(f"Using schedule date: {schedule_date}")
            
            # Get session data for complex calculations
            getsessionNo = await self.rtm_product_hardCoded_data_repo.get_Data()
            
            # Fetch all documents from plants_list
            getPlantList = await self.plants_repo.get_all_plants()
            
            logger.info(f"Retrieved session data: {len(getsessionNo) if getsessionNo else 0} records")
            logger.info(f"Retrieved plant list: {len(getPlantList) if getPlantList else 0} records")
            
            mustplantPrice = "20"

            yesterday = "2025-09-26"
            tomorrow = "2025-09-28"
            
            # Get limit values with complex logic
            allTMValues = await self.limit_values_repo.get_limit_values_by_filters(
                schedule_date=yesterday,
                end_date=tomorrow,
                limit_id="TM",
                market="RTM",  # Using RTM market
                category="Limits"
            )
            
            logger.info(f"Retrieved TM limit values: {len(allTMValues) if allTMValues else 0} records")
            
            allSEZValues = await self.limit_values_repo.get_limit_values_by_filters(
                schedule_date=yesterday,
                end_date=tomorrow,
                limit_id="SEZ",
                market="DAM",  # Using DAM market for SEZ (as per original)
                category="Limits"
            )
            
            logger.info(f"Retrieved SEZ limit values: {len(allSEZValues) if allSEZValues else 0} records")
            
            allMLTPValues = await self.limit_values_repo.get_limit_values_by_filters(
                schedule_date=yesterday,
                end_date=tomorrow,
                limit_id="Multiplier",
                market="RTM",  # Using RTM market
                category="Limits"
            )
            
            logger.info(f"Retrieved Multiplier limit values: {len(allMLTPValues) if allMLTPValues else 0} records")
            
            # Get plant data for scheduling (using v2 repository methods)
            supply_result = await self.supply_repo.get_supply_raw(
                date_from=schedule_date,
                date_to=schedule_date
            )
            getplantsData = supply_result.get("result", []) if supply_result else []
            
            logger.info(f"Retrieved plant data for scheduling: {len(getplantsData) if getplantsData else 0} records")
            
            # Get demand forecast data
            get_forecasted_value = await self.demand_repo.get_demand_forecast_data_pipeline(
                collection_name="demand_serve",  # Using demand_serve instead of serve_demand
                target_date=datetime.strptime(schedule_date, '%Y-%m-%d').date(),
                market_type="IDF"  # Using IDF for RTM
            )
            
            logger.info(f"Retrieved forecasted demand data: {len(get_forecasted_value) if get_forecasted_value else 0} records")
            
            # Calculate total availability
            totalAvailability = await self.get_total_availability(getplantsData, get_forecasted_value)
            
            logger.info(f"Calculated total availability: {len(totalAvailability) if totalAvailability else 0} records")
            
            # Get RTM forecasted price
            rtm_forecasted_price = await self.limit_values_repo.get_limit_values_by_filters(
                schedule_date=schedule_date,
                limit_id="RTM_forecasted_price",
                market="RTM",  # Using RTM market
                category="Limits"
            )
            
            logger.info(f"Retrieved RTM forecasted price: {len(rtm_forecasted_price) if rtm_forecasted_price else 0} records")
            
            # Complex scheduling calculation similar to RTM
            data = self.scheduling_calculator(
                total_availability=totalAvailability,
                rtm_forecasted_price=rtm_forecasted_price,
                get_plants_data=getplantsData,
                get_plants_data_dc=getplantsData,
                all_tm_values=allTMValues,
                all_sez_values=allSEZValues,
                all_mltp_values=allMLTPValues,
                get_plant_list=getPlantList,
                get_session_no=getsessionNo
            )
            
            logger.info(f"RTM scheduling table data calculation completed successfully")
            return data
            
        except Exception as e:
            logger.error(f"Error calculating RTM scheduling table data: {str(e)}")
            return {"status": "error", "message": f"Internal Server Error: {str(e)}"}

    def scheduling_calculator(self, total_availability, rtm_forecasted_price, get_plants_data, get_plants_data_dc, all_tm_values, all_sez_values, all_mltp_values, get_plant_list, get_session_no):
        """
        Complex scheduling calculator with full RTM logic (1,149 lines of complex business logic).
        
        Args:
            total_availability: Total availability data
            rtm_forecasted_price: RTM forecasted price data
            get_plants_data: Plant data for SG
            get_plants_data_dc: Plant data for DC
            all_tm_values: TM limit values
            all_sez_values: SEZ limit values
            all_mltp_values: Multiplier limit values
            get_plant_list: Plant list data
            get_session_no: Session data
            
        Returns:
            Dict[str, Any]: Complex scheduling calculation results
        """
        try:
            logger.info("Starting complex scheduling calculator with full RTM logic")
            
            data = []
            total_sell_volume = 0
            
            blk22 = 0 
            graphblkcount = 0
            plkey = 0  # Initialize plant counter
            reviseDataArray = {} 
            reviseDataTotalsupply = {}
            
            for blk, TotalblkwiseData in enumerate(total_availability):
                logger.info(f"Processing block {blk} for complex scheduling calculation")
                
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
                
                logger.info(f"key={blk} atart-{self.BlkNoForRunSchedule} BlkNoToRunSchedule-{self.BlkNoToRunSchedule} BlkNoFromRunReq- {self.BlkNoFromRunReq} BlkNoToRunReq- {self.BlkNoToRunReq}")

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
                            px_price = round(availability_plnt_arrayyy.get("PX_Price", 0))  # Using PX_Price from plant_master
                            planttm_key = (
                                f"{availability_plnt_arrayyy['scheduled_at']}_"  # Using scheduled_at instead of schedule_date
                                f"{availability_plnt_arrayyy['key']}_"
                                f"{availability_data_array_b_wise['time_block']}"  # Using time_block instead of block
                            )

                            planttmvalue = all_tm_values.get(planttm_key, 0)
                            plantSEZ_key = (
                                f"{availability_plnt_arrayyy['scheduled_at']}_"  # Using scheduled_at instead of schedule_date
                                f"{availability_plnt_arrayyy['key']}_"
                                f"{availability_data_array_b_wise['time_block']}"  # Using time_block instead of block
                            )

                            plantSEZvalue = all_sez_values.get(plantSEZ_key, 0)

                            plantMLTP_key = (
                                f"{availability_plnt_arrayyy['scheduled_at']}_"  # Using scheduled_at instead of schedule_date
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
                                            "scheduled_at": availability_data_array_b_wise["scheduled_at"],  # Using scheduled_at instead of schedule_date
                                            "schedule_date_timestamp": int(availability_data_array_b_wise["scheduled_at"].timestamp()),  # Using scheduled_at
                                            "schedule_date_Int": int(availability_data_array_b_wise["scheduled_at"].strftime("%Y%m%d")),  # Using scheduled_at
                                            "start_time": availability_data_array_b_wise["start_time"],
                                            "end_time": availability_data_array_b_wise["end_time"],
                                            "full_time": availability_data_array_b_wise["full_time"],
                                            "time_block": availability_data_array_b_wise["time_block"],  # Using time_block instead of block
                                            "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                            "session_number": get_session_no[availability_data_array_b_wise["time_block"]].get("session_number") or get_session_no[availability_data_array_b_wise["time_block"]].get("Session_Id"),  # Using time_block
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
                                            "scheduling_requisition_type": "Scheduling",
                                            "market_type": "RTM",  # Using market_type instead of market
                                            "created_at": datetime.now(),
                                            "updated_at": datetime.now()
                                        }
                                        logger.info(f"Complex scheduling calculation - Plant {availability_plnt_arrayyy['key']} scheduling data prepared")

                                        plkey += 1

                                    else:
                                        reverse_array_next = list(reversed(get_plants_data[blk + 1]['result']))
                                        nextblkSG = reverse_array_next[plnamekey]['volume'] if plnamekey < len(reverse_array_next) else 0

                                        reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[
                                            availability_plnt_arrayyy["key"]
                                        ] = min(plnt_100_volume_genrated, nextblkSG)

                                        genratedTotalsupply += min(plnt_100_volume_genrated, nextblkSG)

                                        insertData = {
                                            "scheduled_at": availability_data_array_b_wise["scheduled_at"],  # Using scheduled_at instead of schedule_date
                                            "schedule_date_timestamp": int(availability_data_array_b_wise["scheduled_at"].timestamp()),  # Using scheduled_at
                                            "schedule_date_Int": int(availability_data_array_b_wise["scheduled_at"].strftime("%Y%m%d")),  # Using scheduled_at
                                            "start_time": availability_data_array_b_wise["start_time"],
                                            "end_time": availability_data_array_b_wise["end_time"],
                                            "full_time": availability_data_array_b_wise["full_time"],
                                            "time_block": availability_data_array_b_wise["time_block"],  # Using time_block instead of block
                                            "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                            "session_number": get_session_no[availability_data_array_b_wise["time_block"]].get("session_number") or get_session_no[availability_data_array_b_wise["time_block"]].get("Session_Id"),  # Using time_block
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
                                            "scheduling_requisition_type": "NA",
                                            "market_type": "RTM",  # Using market_type instead of market
                                            "created_at": datetime.now(),
                                            "updated_at": datetime.now()
                                        }
                                        logger.info(f"Complex scheduling calculation - Plant {availability_plnt_arrayyy['key']} NA type data prepared")
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
                                        "scheduled_at": availability_data_array_b_wise["scheduled_at"],  # Using scheduled_at instead of schedule_date
                                        "schedule_date_timestamp": int(availability_data_array_b_wise["scheduled_at"].timestamp()),  # Using scheduled_at
                                        "schedule_date_Int": int(availability_data_array_b_wise["scheduled_at"].strftime("%Y%m%d")),  # Using scheduled_at
                                        "start_time": availability_data_array_b_wise["start_time"],
                                        "end_time": availability_data_array_b_wise["end_time"],
                                        "full_time": availability_data_array_b_wise["full_time"],
                                        "time_block": availability_data_array_b_wise["time_block"],  # Using time_block instead of block
                                        "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                        "session_number": get_session_no[availability_data_array_b_wise["time_block"]].get("session_number") or get_session_no[availability_data_array_b_wise["time_block"]].get("Session_Id"),  # Using time_block
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
                                        "scheduling_requisition_type": "NA",
                                        "market_type": "RTM",  # Using market_type instead of market
                                        "created_at": datetime.now(),
                                        "updated_at": datetime.now()
                                    }
                                    logger.info(f"Complex scheduling calculation - Plant {availability_plnt_arrayyy['key']} else branch data prepared")

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
                                    "scheduled_at": availability_data_array_b_wise["scheduled_at"],  # Using scheduled_at instead of schedule_date
                                    "schedule_date_timestamp": int(availability_data_array_b_wise["scheduled_at"].timestamp()),  # scheduled_at is already datetime
                                    "schedule_date_Int": int(availability_data_array_b_wise["scheduled_at"].strftime("%Y%m%d")),  # scheduled_at is already datetime
                                    "start_time": availability_data_array_b_wise["start_time"],
                                    "end_time": availability_data_array_b_wise["end_time"],
                                    "full_time": availability_data_array_b_wise["full_time"],
                                    "time_block": availability_data_array_b_wise["time_block"],  # Using time_block instead of block
                                    "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                    "session_number": get_session_no[availability_data_array_b_wise["time_block"]].get("session_number") or get_session_no[availability_data_array_b_wise["time_block"]].get("Session_Id"),  # Using time_block
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
                                    "scheduling_requisition_type": "NA",
                                    "market_type": "RTM",  # Using market_type instead of market
                                    "created_at": datetime.now(),
                                    "updated_at": datetime.now()
                                }

                                plkey += 1
                    else:
                        reverse_array = list(reversed(get_plants_data[blk].result))
                        for plnamekey, availability_plnt_arrayyyObj in enumerate(reverse_array):
                            # Cast object to dict (similar to (array) in PHP)
                            availability_plnt_arrayyy = dict(availability_plnt_arrayyyObj)

                            # PX_Price
                            px_price = round(
                                availability_plnt_arrayyy.get("PX_Price", 0)  # Using PX_Price from plant_master
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
                                f"{availability_plnt_arrayyy.get('scheduled_at')}_"  # Using scheduled_at instead of schedule_date
                                f"{availability_plnt_arrayyy.get('key')}_"
                                f"{availability_data_array_b_wise.get('time_block')}"  # Using time_block instead of block
                            )
                            planttmvalue = all_tm_values.get(planttm_key, 0)

                            # Plant SEZ value
                            plantsez_key = (
                                f"{availability_plnt_arrayyy.get('scheduled_at')}_"  # Using scheduled_at instead of schedule_date
                                f"{availability_plnt_arrayyy.get('key')}_"
                                f"{availability_data_array_b_wise.get('time_block')}"  # Using time_block instead of block
                            )
                            plantSEZvalue = all_sez_values.get(plantsez_key, 0)

                            # Plant MLTP value
                            plantmltp_key = (
                                f"{availability_plnt_arrayyy.get('scheduled_at')}_"  # Using scheduled_at instead of schedule_date
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
                                            "scheduled_at": availability_data_array_b_wise["scheduled_at"],  # Using scheduled_at instead of schedule_date
                                            "schedule_date_timestamp": int(datetime.strptime(
                                                availability_data_array_b_wise["scheduled_at"], "%Y-%m-%d"  # Using scheduled_at
                                            ).timestamp()),
                                            "schedule_date_Int": int(datetime.strptime(
                                                availability_data_array_b_wise["scheduled_at"], "%Y-%m-%d"  # Using scheduled_at
                                            ).strftime("%Y%m%d")),
                                            "start_time": availability_data_array_b_wise["start_time"],
                                            "end_time": availability_data_array_b_wise["end_time"],
                                            "full_time": availability_data_array_b_wise["full_time"],
                                            "time_block": availability_data_array_b_wise["time_block"],  # Using time_block instead of block
                                            "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                            "session_number": get_session_no[availability_data_array_b_wise["time_block"]].get("session_number") or get_session_no[availability_data_array_b_wise["time_block"]].get("Session_Id"),  # Using time_block
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
                                            "market_type": "RTM",  # Using market_type instead of market
                                            "created_at": datetime.now(),
                                            "updated_at": datetime.now()
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
                                            "scheduling_requisition_type": "NA",
                                            "market_type": "RTM",  # Using market_type instead of market
                                            "created_at": datetime.now(),
                                            "updated_at": datetime.now()
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
                                        "scheduling_requisition_type": "NA",
                                        "market_type": "RTM",  # Using market_type instead of market
                                        "created_at": datetime.now(),
                                        "updated_at": datetime.now()
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
                                scheduled_at_datetime = availability_data_array_b_wise["scheduled_at"]  # Already a datetime object
                                insertData = {
                                    "scheduled_at": scheduled_at_datetime,  # DateTime object
                                    "schedule_date_timestamp": int(scheduled_at_datetime.timestamp()),
                                    "schedule_date_Int": int(scheduled_at_datetime.strftime("%Y%m%d")),
                                    "start_time": availability_data_array_b_wise["start_time"],
                                    "end_time": availability_data_array_b_wise["end_time"],
                                    "full_time": availability_data_array_b_wise["full_time"],
                                    "time_block": availability_data_array_b_wise["time_block"],  # Using time_block instead of block
                                    "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                    "session_number": get_session_no[availability_data_array_b_wise["time_block"]].get("session_number") or get_session_no[availability_data_array_b_wise["time_block"]].get("Session_Id"),  # Using time_block
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
                                    "scheduling_requisition_type": "NA",
                                    "market_type": "RTM",  # Using market_type instead of market
                                    "created_at": datetime.now(),
                                    "updated_at": datetime.now()
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

                            px_price = round(availability_plnt_arrayyy.get("PX_Price", 0))  # Using PX_Price from plant_master

                            tm_key = f"{availability_plnt_arrayyy['scheduled_at']}_{availability_plnt_arrayyy['key']}_{availability_data_array_b_wise['time_block']}"  # Using scheduled_at and time_block
                            planttmvalue = all_tm_values.get(tm_key, 0)
                            plantSEZvalue = all_sez_values.get(tm_key, 0)

                            mltp_key = f"{availability_plnt_arrayyy['scheduled_at']}_{availability_plnt_arrayyy['key']}"  # Using scheduled_at
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
                                            "scheduled_at": availability_data_array_b_wise["scheduled_at"],  # Using scheduled_at instead of schedule_date
                                            "schedule_date_timestamp": int(availability_data_array_b_wise["scheduled_at"].timestamp()),  # Using scheduled_at
                                            "schedule_date_Int": int(availability_data_array_b_wise["scheduled_at"].strftime("%Y%m%d")),  # Using scheduled_at
                                            "start_time": availability_data_array_b_wise["start_time"],
                                            "end_time": availability_data_array_b_wise["end_time"],
                                            "full_time": availability_data_array_b_wise["full_time"],
                                            "time_block": availability_data_array_b_wise["time_block"],  # Using time_block instead of block
                                            "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                            "session_number": get_session_no[availability_data_array_b_wise["time_block"]].get("session_number") or get_session_no[availability_data_array_b_wise["time_block"]].get("Session_Id"),  # Using time_block
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
                                            "market_type": "RTM",  # Using market_type instead of market
                                            "created_at": datetime.now(),
                                            "updated_at": datetime.now()
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
                                    "scheduled_at": availability_data_array_b_wise["scheduled_at"],  # Using scheduled_at instead of schedule_date
                                    "schedule_date_timestamp": int(availability_data_array_b_wise["scheduled_at"].timestamp()),  # Using scheduled_at
                                    "schedule_date_Int": int(availability_data_array_b_wise["scheduled_at"].strftime("%Y%m%d")),  # Using scheduled_at
                                    "start_time": availability_data_array_b_wise["start_time"],
                                    "end_time": availability_data_array_b_wise["end_time"],
                                    "full_time": availability_data_array_b_wise["full_time"],
                                    "time_block": availability_data_array_b_wise["time_block"],  # Using time_block instead of block
                                    "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                    "session_number": get_session_no[availability_data_array_b_wise["time_block"]].get("session_number") or get_session_no[availability_data_array_b_wise["time_block"]].get("Session_Id"),  # Using time_block
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
                                    "market_type": "RTM",  # Using market_type instead of market
                                    "created_at": datetime.now(),
                                    "updated_at": datetime.now()
                                }
                                # DB insertion here
                                # db.insert(insertData)
                                plkey += 1
                    else:
                        # Reverse the plant data for current block
                        reverse_array = list(reversed(get_plants_data[blk]["result"]))

                        for plnamekey, availability_plnt_arrayyyObj in enumerate(reverse_array):
                            availability_plnt_arrayyy = dict(availability_plnt_arrayyyObj)  # Convert object to dict

                            px_price = round(availability_plnt_arrayyy.get("PX_Price", 0))  # Using PX_Price from plant_master

                            SGofLastplant = reviseDataArray.get(blk, {}).get("genrated_sg_volume", {}).get(
                                availability_plnt_arrayyy["key"], 0
                            )

                            plnt_100_volume = float(availability_plnt_arrayyy.get("volume", 0))
                            vlueofChangesandactual = SGofLastplant
                            plnt_100_volume_genrated = float(vlueofChangesandactual)

                            tm_key = f"{availability_plnt_arrayyy['scheduled_at']}_{availability_plnt_arrayyy['key']}_{availability_data_array_b_wise['time_block']}"  # Using scheduled_at and time_block
                            planttmvalue = all_tm_values.get(tm_key, 0)
                            plantSEZvalue = all_sez_values.get(tm_key, 0)
                            mltp_key = f"{availability_plnt_arrayyy['scheduled_at']}_{availability_plnt_arrayyy['key']}"  # Using scheduled_at
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
                                        "scheduled_at": availability_data_array_b_wise["scheduled_at"],  # Using scheduled_at instead of schedule_date
                                        "schedule_date_timestamp": int(availability_data_array_b_wise["scheduled_at"].timestamp()),  # Using scheduled_at
                                        "schedule_date_Int": int(availability_data_array_b_wise["scheduled_at"].strftime("%Y%m%d")),  # Using scheduled_at
                                        "start_time": availability_data_array_b_wise["start_time"],
                                        "end_time": availability_data_array_b_wise["end_time"],
                                        "full_time": availability_data_array_b_wise["full_time"],
                                        "time_block": availability_data_array_b_wise["time_block"],  # Using time_block instead of block
                                        "dc_sg_type": availability_data_array_b_wise["dc_sg_type"],
                                        "session_number": get_session_no[availability_data_array_b_wise["time_block"]].get("session_number") or get_session_no[availability_data_array_b_wise["time_block"]].get("Session_Id"),  # Using time_block
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
                                        "market_type": "RTM",  # Using market_type instead of market
                                        "created_at": datetime.now(),
                                        "updated_at": datetime.now()
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
                            availability_plnt_arrayyy.get("PX_Price", 0)  # Using PX_Price from plant_master
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
                        composite_key_tm = f"{availability_plnt_arrayyy['scheduled_at']}_{availability_plnt_arrayyy['key']}_{availability_data_array_b_wise['time_block']}"  # Using scheduled_at and time_block
                        
                        composite_key_sez = f"{availability_plnt_arrayyy['scheduled_at']}_{availability_plnt_arrayyy['key']}_{availability_data_array_b_wise['time_block']}"  # Using scheduled_at and time_block
                        
                        composite_key_mltp = f"{availability_plnt_arrayyy['scheduled_at']}_{availability_plnt_arrayyy['key']}"  # Using scheduled_at
                        
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
                            
                            logger.info(f"Complex scheduling calculation - Plant {availability_plnt_arrayyy['key']} SR-NA processing")
                            time_block = int(availability_data_array_b_wise["time_block"])  # convert float to int
                            session_number = get_session_no[time_block].get("session_number", get_session_no[time_block].get("Session_Id"))  # now safe
                            # return availability_data_array_b_wise
                            insertData = {
                                "scheduled_at": availability_data_array_b_wise["scheduled_at"],  # Using scheduled_at instead of schedule_date
                                "schedule_date_timestamp": int(
                                    datetime.strptime(
                                        availability_data_array_b_wise["scheduled_at"], "%Y-%m-%d"  # Using scheduled_at
                                    ).timestamp()
                                ),
                                "schedule_date_Int": int(
                                    datetime.strptime(
                                        availability_data_array_b_wise["scheduled_at"], "%Y-%m-%d"  # Using scheduled_at
                                    ).strftime("%Y%m%d")
                                ),
                                "time_block": availability_data_array_b_wise["time_block"],  # Using time_block instead of block
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
                                "market_type": "RTM",  # Using market_type instead of market
                                "created_at": datetime.now(),
                                "updated_at": datetime.now()
                            }
                            # return insertData
                            # self.scheduling_requisition_repo.create(insertData)  # Commented out for now
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
                            session_number = get_session_no[time_block].get("session_number", get_session_no[time_block].get("Session_Id"))  # now safe
                            
                            insertData = {
                                "scheduled_at": availability_data_array_b_wise["scheduled_at"],  # Using scheduled_at instead of schedule_date
                                "schedule_date_timestamp": int(
                                    datetime.strptime(
                                        availability_data_array_b_wise["scheduled_at"], "%Y-%m-%d"  # Using scheduled_at
                                    ).timestamp()
                                ),
                                "schedule_date_Int": int(
                                    datetime.strptime(
                                        availability_data_array_b_wise["scheduled_at"], "%Y-%m-%d"  # Using scheduled_at
                                    ).strftime("%Y%m%d")
                                ),
                                "time_block": availability_data_array_b_wise["time_block"],  # Using time_block instead of block
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
                                "market_type": "RTM",  # Using market_type instead of market
                                "created_at": datetime.now(),
                                "updated_at": datetime.now()
                            }
                           
                            # return insertData
                            # self.scheduling_requisition_repo.create(insertData)  # Commented out for now
                            
                            plkey += 1
                            
                    blk22 += 1
                
                # Ensure nested dict exists and update totals for this block
                reviseDataTotalsupply.setdefault(blk + 1, {})
                
                reviseDataTotalsupply[blk + 1]["total_supply_updated"] = genratedTotalsupply
                reviseDataTotalsupply[blk + 1]["surplus_deficit_updated"] = (
                    genratedTotalsupply
                    - TotalblkwiseData.get("forecast_value", 0)
                )

            logger.info(f"Complex scheduling calculator completed: {blk22} blocks processed, {plkey} plants processed")
            return reviseDataTotalsupply
            
        except Exception as e:
            logger.error(f"Error in complex scheduling calculator: {str(e)}")
            raise Exception(f"Failed to calculate complex scheduling: {str(e)}")

    async def bid_creation_post_old(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create RTM bid data with full complexity (original method).
        
        Args:
            request (Dict[str, Any]): Bid creation request data
            
        Returns:
            Dict[str, Any]: Creation result
        """
        try:
            logger.info(f"Starting RTM bid creation (old method) with request data")
            
            # ---- INITIALIZATION ----
            allpricearray = [0.0]
            allpricearray2 = []
            buybiddataarray = {}
            finalbuyarraysorted = {}

            # Convert input bidding date: "15-10-2025"
            biding_date = datetime.strptime(request["bidingDate"], "%d-%m-%Y")

            logger.info(f"Processing bid creation for date: {biding_date}")

            # ---- STEP 1: PREPARE SELL & BUY PRICE ARRAYS ----
            for plkey, plvalue in enumerate(request["time_block_range"]):
                blno = plvalue.split("_")  # e.g. ["04:00-04:15", "17", "2025-10-15"]
                date_obj = datetime.strptime(blno[2], "%Y-%m-%d")

                if biding_date.date() == date_obj.date():
                    bid_type = request["sell_buy_bid_type"][plkey]

                    # ---- SELL BIDS ----
                    if bid_type == "sell":
                        bid_price_key = f"bid_price_{blno[1]}"
                        if bid_price_key not in request:
                            continue
                        for price in request[bid_price_key]:
                            plvalue22 = float(price)
                            allpricearray.append(plvalue22)
                            allpricearray2.append(plvalue22 + 1 if int(plvalue22) != 0 else plvalue22)

                    # ---- BUY BIDS ----
                    elif bid_type == "buy":
                        bid_price_key = f"bid_price_{blno[1]}"
                        bid_volume_key = f"bid_volume_{blno[1]}"

                        if bid_price_key not in request or bid_volume_key not in request:
                            continue

                        buyary = {}
                        for i, plvalue22 in enumerate(request[bid_price_key]):
                            if plvalue22 in [bidPriceNOC(), "", None]:
                                continue
                            buyary[float(plvalue22)] = float(request[bid_volume_key][i])
                            finalbuyarraysorted[float(plvalue22)] = float(request[bid_volume_key][i])

                        # Sort descending
                        buybiddataarray[blno[1]] = dict(sorted(buyary.items(), reverse=True))

            logger.info(f"Processed price arrays: {len(allpricearray)} sell prices, {len(buybiddataarray)} buy blocks")

            # ---- STEP 2: PREPARE BUY PRICE COMBINED ARRAY ----
            finalarrayforbidbuy = []
            buypricekeys = []

            if buybiddataarray:
                for plkey, plvalue in enumerate(request["time_block_range"]):
                    blno = plvalue.split("_")
                    date_obj = datetime.strptime(blno[2], "%Y-%m-%d")

                    if biding_date.date() == date_obj.date() and blno[1] in buybiddataarray:
                        for bypkey in buybiddataarray[blno[1]].keys():
                            buypricekeys.append(float(bypkey))

                finalarrayforbidbuy = sorted(set(buypricekeys))

            # ---- STEP 3: FINAL PRICE ARRAY ----
            finalpricearraysell = sorted(set(allpricearray + allpricearray2))
            finalpricearray = sorted(set(finalpricearraysell + finalarrayforbidbuy))
            finalpricearray.append(bidPriceNOC())
            finalpricearray = sorted(set(finalpricearray))

            logger.info(f"Final price array created with {len(finalpricearray)} prices")

            # ---- STEP 4: BUILD BID BLOCK ARRAY ----
            bidblockarray = {}
            pDetailarray = {"reference_number": {}, "bid_status": {}, "approve_by": {}}

            for timeblock_key, timeblock_value in enumerate(request["time_block_range"]):
                fsellvolume = 0
                blno = timeblock_value.split("_")
                date_obj = datetime.strptime(blno[2], "%Y-%m-%d")

                if biding_date.date() != date_obj.date():
                    continue

                blk_key = f"{blno[0]}_{blno[1]}"
                bid_type = request["sell_buy_bid_type"][timeblock_key]

                # ---- SELL ----
                if bid_type == "sell":
                    bid_price_key = f"bid_price_{blno[1]}"
                    bid_volume_key = f"bid_volume_{blno[1]}"

                    if bid_price_key not in request or bid_volume_key not in request:
                        continue

                    for pricevalue in finalpricearray:
                        for i, price in enumerate(request[bid_price_key]):
                            price_f = float(price)
                            vol_f = float(request[bid_volume_key][i])

                            if pricevalue == price_f:
                                fsellvolume += 0
                            elif pricevalue == price_f + 1:
                                fsellvolume += vol_f
                            else:
                                fsellvolume += vol_f

                        # Create bid document with v2 field mappings
                        doc = {
                            "scheduled_at": datetime.strptime(blno[2], '%Y-%m-%d'),  # Parse string to datetime object
                            "market_type": "RTM",          # Using market_type instead of market
                            "time_block": int(blno[1]),    # Using time_block instead of block
                            "bid_type": "sell",
                            "bid_price": pricevalue,
                            "bid_volume": fsellvolume,
                            "created_at": datetime.now(),
                            "status": "created"
                        }
                        
                        # Add to bid block array
                        if blk_key not in bidblockarray:
                            bidblockarray[blk_key] = []
                        bidblockarray[blk_key].append(doc)

            logger.info(f"RTM bid creation (old method) completed: {len(bidblockarray)} blocks processed")
            return {"data": True, "bid_blocks": len(bidblockarray), "total_prices": len(finalpricearray)}
            
        except Exception as e:
            logger.error(f"Error in RTM bid creation (old method): {str(e)}")
            raise Exception(f"Failed to create RTM bid (old method): {str(e)}")

    async def update_plant_data_for_approval(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update plant data for approval with full RTM complexity.
        
        Args:
            data (Dict[str, Any]): Plant approval data
            
        Returns:
            Dict[str, Any]: Update result
        """
        try:
            logger.info(f"Updating plant data for approval: {data.get('reference_number', 'unknown')}")
            
            # Complex update query similar to RTM
            filter_query = {
                "_id": data["reference_number"],
                "time_block": int(data["block_no"])  # Using time_block instead of block_no
            }

            update_query = {
                "$set": {
                    "bid_status": data["bid_status"],
                    "remark": data["Remarks"],  # Using remark instead of remarks
                    "approved_by": data["action_by"],
                    "approved_time": datetime.now(),
                    "updated_at": datetime.now()
                }
            }

            result = await self.single_bid_data_for_approval_repo.update_one(filter_query, update_query)
            
            logger.info(f"Plant data update completed: {result.get('modified_count', 0)} records modified")
            return result
            
        except Exception as e:
            logger.error(f"Error updating plant data for approval: {str(e)}")
            raise Exception(f"Failed to update plant data for approval: {str(e)}")


    def total_availability_new(self, raw_data, getForecastedValue, from_block, to_block):
        """
        Compute total availability per time block with full RTM complexity.
        
        Args:
            raw_data: Raw plant data
            getForecastedValue: Forecasted demand values
            from_block: Starting time block
            to_block: Ending time block
            
        Returns:
            List[Dict[str, Any]]: Total availability data
        """
        try:
            logger.info(f"Calculating total availability (new method) for blocks {from_block}-{to_block}")
            
            data_ar = []
            getplantsData = [v for k, v in raw_data.items()]
            
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
                
                logger.info(f"Processing plant data block {i}")
                
                getplantsDataaa1 = [v for k, v in getplantsDataaa['plants'].items()]
                for j, blPlants in enumerate(getplantsDataaa1):
                    plnt_volume_fiftyfive = 0
                    total_volume_mrit = 0
                    plnt_valume_must = 0
                    
                    pl_volume = float(blPlants.get('plant_volume'))

                    if blPlants.get('dispatch_type') == "Merit":
                        plnt_volume_fiftyfive = pl_volume * 0.55
                        total_volume_mrit = pl_volume
                    else:
                        plnt_valume_must = pl_volume

                    bltotalvolume += pl_volume
                    volume_for_bid_submision += pl_volume
                    total_volume_mritbl += total_volume_mrit
                    blk_volume_sum_fiftyfive += plnt_volume_fiftyfive
                    blk_volume_sum_must += plnt_valume_must
                    must_merit_fiftyfive_volume_bl_sum = blk_volume_sum_fiftyfive + blk_volume_sum_must
                    
                if i < len(getForecastedValue):
                    forecast_val = getForecastedValue[i].get('forecast', 0)
                else:
                    forecast_val = 0
                
                data_ar.append({
                    'bltotalvolume': bltotalvolume,
                    'BlockNo': getplantsData[i]["time_block"],  # Using time_block instead of BlockNo
                    'volume_for_bid_submision': volume_for_bid_submision,
                    'total_volume_mritbl': total_volume_mritbl,
                    'total_volume_mritbl_45': total_volume_mritbl * 0.45,
                    'blk_volume_sum_fiftyfive': blk_volume_sum_fiftyfive,
                    'blk_volume_sum_must': blk_volume_sum_must,
                    'must_merit_fiftyfive_volume_bl_sum': must_merit_fiftyfive_volume_bl_sum,
                    'forecast_value': forecast_val,
                    'diff_for_bid_cal': forecast_val - must_merit_fiftyfive_volume_bl_sum,
                    'surplus_deficit': bltotalvolume - forecast_val
                })

            logger.info(f"Total availability (new method) calculation completed: {len(data_ar)} blocks")
            return data_ar
            
        except Exception as e:
            logger.error(f"Error calculating total availability (new method): {str(e)}")
            raise Exception(f"Failed to calculate total availability (new method): {str(e)}")

    def _limit_values_lookups(self):
        """
        Helper function to return all $lookup stages for limit_values with full RTM complexity.
        """
        try:
            logger.info("Building limit values lookup stages")
            
            lookups = []

            fields = [
                ("Buffer", "MP", "buffer_data", "buffer_limit"),
                ("Ceiling_Price", "MP", "ceiling_data", "ceiling_price"),
                ("n_1", "MP", "n1_data", "n1_limit"),
                ("noc_buy", "MP", "noc_buy_data", "noc_buy_limit"),
                ("noc_sell", "MP", "noc_sell_data", "noc_sell_limit"),
                ("iex", "S", "iex_data", "iex_split"),
                ("pxil", "S", "pxil_data", "pxil_split")
            ]

            for ID, area, alias, project_field in fields:
                lookups.append({
                    "$lookup": {
                        "from": "limit_values",  # Using limit_values collection
                        "let": {"sched": "$scheduled_at", "tb": "$blocks.time_block"},  # Using scheduled_at and time_block
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": ["$ID", ID]},
                                            {"$eq": ["$area", area]},
                                            {"$eq": ["$scheduled_at", "$$sched"]},  # Using scheduled_at instead of schedule_date
                                            {"$eq": ["$time_block", "$$tb"]}  # Using time_block instead of block
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

            logger.info(f"Limit values lookup stages built: {len(lookups)} stages")
            return lookups
            
        except Exception as e:
            logger.error(f"Error building limit values lookup stages: {str(e)}")
            raise Exception(f"Failed to build limit values lookup stages: {str(e)}")

    async def get_all_plant_data_for_scheduling(self, schedule_date, dc_sg):
        """
        Get all plant data for scheduling with full RTM complexity.

        Args:
            schedule_date: Schedule date for data retrieval
            dc_sg: DC/SG filter for plant data

        Returns:
            List[Dict[str, Any]]: Plant data for scheduling
        """
        try:
            logger.info(f"Getting plant data for scheduling: date={schedule_date}, dc_sg={dc_sg}")
            
            # scheduledatetimestamp = yesterday midnight (unix timestamp)
            yesterday = datetime.now() - timedelta(days=1)

            # Format as YYYY-MM-DD
            yesterday_date = yesterday.strftime("%Y-%m-%d")

            # check current time
            now_time = datetime.now().time()
            cutoff_time = datetime.strptime("22:30", "%H:%M").time()

            if now_time > cutoff_time:
                metch = {
                    "scheduled_at": {"$gte": yesterday_date},  # Using scheduled_at instead of schedule_date
                    "dc_sg": dc_sg
                }
            else:
                metch = {
                    "scheduled_at": {"$gte": yesterday_date},  # Using scheduled_at instead of schedule_date
                    "dc_sg": dc_sg
                }

            pipeline = [
                {"$match": metch},
                {
                    "$group": {
                        "_id": {
                            "schedule_date": "$scheduled_at",  # Using scheduled_at instead of schedule_date
                            "time_block": "$time_block"  # Using time_block instead of block
                        },
                        "result": {
                            "$push": {
                                "plant_name": "$plant_name",
                                "key": "$plant_id",
                                "volume": "$volume",
                                "schedule_date": "$scheduled_at",  # Using scheduled_at instead of schedule_date
                                "category": "$category",
                                "VC_Price": "$VC_Price",
                            }
                        },
                        "time_block": {"$first": "$time_block"},  # Using time_block instead of block
                        "dc_sg": {"$first": "$dc_sg"},
                        "schedule_date": {"$first": "$scheduled_at"},  # Using scheduled_at instead of schedule_date
                        "counter": {"$first": "0.0"},
                        "totalAvailability": {"$sum": "$volume"}
                    }
                },
                {
                    "$project": {
                        "result": 1,
                        "time_block": 1,  # Using time_block instead of block
                        "dc_sg": 1,
                        "schedule_date": 1,
                        "totalAvailability": 1
                    }
                },
                {"$sort": {"schedule_date": 1.0, "time_block": 1.0}}
            ]

            posted_jobs = await self.supply_repo.repository.aggregate(self.supply_repo.supply_serve, pipeline)
            
            logger.info(f"Plant data for scheduling retrieved: {len(posted_jobs)} records")
            return posted_jobs
            
        except Exception as e:
            logger.error(f"Error getting plant data for scheduling: {str(e)}")
            raise Exception(f"Failed to get plant data for scheduling: {str(e)}")

    def get_last_record_of_sg(self, plants_data, no_of_block=0):
        """
        Get the last record of SG plants for a given number of blocks with full RTM complexity.
        
        Args:
            plants_data (list): List of plant data per time block.
            no_of_block (int): Number of blocks to consider.

        Returns:
            dict: Dictionary containing last counted block data.
        """
        try:
            logger.info(f"Getting last record of SG plants for {no_of_block} blocks")
            
            getplants_data = plants_data
            count_block_volume = {}

            for nobl in range(no_of_block + 1):
                block_index = self.BlkNoForRunSchedule - nobl - 1  # adjust for 0-based index
                
                # Safety check: avoid going negative
                if block_index < 0:
                    continue

                if block_index >= len(getplants_data):
                    continue  # avoid list index out of range

                for allplants in getplants_data[block_index].get('result', []):
                    key = allplants['key']
                    volume = allplants['volume']
                    
                    if key not in count_block_volume:
                        count_block_volume[key] = {}
                    
                    count_block_volume[key][block_index] = volume

            logger.info(f"Last record of SG plants retrieved: {len(count_block_volume)} plants")
            return {"lastcountedBlockData": count_block_volume}
            
        except Exception as e:
            logger.error(f"Error getting last record of SG plants: {str(e)}")
            raise Exception(f"Failed to get last record of SG plants: {str(e)}")

    def total_availability(self, getplantsData, getForecastedValue):
        """
        Calculate total availability, surplus/deficit, and forecasted values per block with full RTM complexity.
        
        Args:
            getplantsData: Plant data for availability calculation
            getForecastedValue: Forecasted demand values
            
        Returns:
            List[Dict[str, Any]]: Total availability data
        """
        try:
            logger.info(f"Calculating total availability for {len(getplantsData)} plant data blocks")
            
            data_ar = []
            
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
                    "BlockNo": block_data.get("_id", {}).get("time_block"),  # Using time_block instead of BlockNo
                    "dc_sg": block_data.get("dc_sg"),
                    "forecast_value": demand_forecasted_value,
                    "surplus_deficit": bltotalvolume - demand_forecasted_value,
                    "totalAvailability": bltotalvolume,
                    "plants": block_data.get("result", []),
                }

                data_ar.append(data_block)

            logger.info(f"Total availability calculation completed: {len(data_ar)} blocks")
            return data_ar
            
        except Exception as e:
            logger.error(f"Error calculating total availability: {str(e)}")
            raise Exception(f"Failed to calculate total availability: {str(e)}")

    def check_forecast_price_apply_for_surplus(self, rtmforecastedpriceblk, px_price, S_D_type):
        """
        Check if the forecast price condition applies for surplus/deficit with full RTM complexity.

        Args:
            rtmforecastedpriceblk (float): RTM forecasted price for the block.
            px_price (float): PX price of the plant.
            S_D_type (str): "surplus" or "deficit".

        Returns:
            bool: True if condition is met, False otherwise.
        """
        try:
            logger.info(f"Checking forecast price condition: rtm_price={rtmforecastedpriceblk}, px_price={px_price}, type={S_D_type}")
            
            if S_D_type == "surplus":
                result = rtmforecastedpriceblk < px_price
            else:  # deficit
                result = rtmforecastedpriceblk > px_price
                
            logger.info(f"Forecast price condition result: {result}")
            return result
            
        except Exception as e:
            logger.error(f"Error checking forecast price condition: {str(e)}")
            raise Exception(f"Failed to check forecast price condition: {str(e)}")

    def get_fifteen_time_block_id(self, id):
        """
        Get fifteen-minute time block ID with full RTM complexity.
        
        Args:
            id: Time block index
            
        Returns:
            str: Time block string
        """
        try:
            logger.info(f"Getting fifteen-minute time block ID for index: {id}")
            
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
            
            result = time_block[id]
            logger.info(f"Fifteen-minute time block ID retrieved: {result}")
            return result
            
        except Exception as e:
            logger.error(f"Error getting fifteen-minute time block ID: {str(e)}")
            raise Exception(f"Failed to get fifteen-minute time block ID: {str(e)}")


# Global service instance
rtm_bidding_service = RTMBiddingService()
