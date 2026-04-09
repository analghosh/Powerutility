"""
Dashboard service layer using repository pattern.
Handles business logic for dashboard-related data operations.
"""

import logging
from pydoc import text
from typing import List, Optional, Dict, Any
from datetime import date, datetime, timedelta
import time
from math import isnan
from collections import OrderedDict

from bson import ObjectId

from ..repository.serve_supply_table_repository import serveSupplyTableRepository
from ..repository.rtm_single_bid_data_for_approval_repository import RTMSingleBidDataForApprovalRepository
from ..repository.limit_value_repository import limitVallueRepository
from ..repository.plants_master_repository import PlantsMasterRepository 
from ..repository.rtm_product_hardcoded_data_repository import RTMProductHardCodedDataRepository
from ..repository.serve_demand_table_repository import  DashboardRepository
from ..repository.scheduling_requisition_calculation import  SchedulingRequisitionCalculationRepository
from ..repository.serve_exchange_table_repository import serveExchangeTableRepository
from ..repository.rtm_current_bid_data_for_approval_repository import RTMCurrentBidDataForApprovalRepository


from ..core.helpers import dsm_calculation

logger = logging.getLogger(__name__)

def bidPriceNOC():
    """Equivalent of PHP's bidPriceNOC() constant."""
    return 10000

class RealtimemarketService:
    """
    Service class responsible for dashboard business logic.
    """

    def __init__(self, collection_name: str = "serve_supply_table"):
        self.supply_repo = serveSupplyTableRepository()
        self.single_bid_data_for_approval_repo = RTMSingleBidDataForApprovalRepository(collection_name="rtm_single_bid_data_for_approval")
        self.rtm_product_hardCoded_data_repo = RTMProductHardCodedDataRepository(collection_name="rtm_product_hardCoded_data")
        self.plants_list_repo = PlantsMasterRepository(collection_name="plant_master")
        self.limit_values_repo = limitVallueRepository(collection_name="limit_values")
        self.serve_demand_repo = DashboardRepository(collection_name="serve_demand_table")
        self.serve_exchange_repo = serveExchangeTableRepository(collection_name="serve_exchange_table")
        self.scheduling_requisition_repo = SchedulingRequisitionCalculationRepository(collection_name="scheduling_requisition_calculation_table")
        self.rtm_currect_selected_forecast_log_repo = RTMCurrentBidDataForApprovalRepository(collection_name="RTM_currect_selected_forecast_log")

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
            result = self.supply_repo.repository.aggregate(self.supply_repo.supply_serve, pipeline)
            return {"result": result}
        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise


    
    async def rtm_single_bid_table(
        self, 
        schedule_date: Optional[str], 
        from_block: int, 
        to_block: int
    ) -> Dict[str, Any]:
        """
        Fetch DSM data and compute total availability with all limits and splits.
        """

        try:
            # Use hardcoded date if not provided
            if not schedule_date:
                schedule_date = '2025-08-04'

            # Fetch plant data
            pipeline = [
                {"$match": {
                    "schedule_date": schedule_date,
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
            cursor = self.supply_repo.repository.aggregate(self.supply_repo.supply_serve, pipeline)
            try:
                getplantsData = await cursor.to_list(None)
            finally:
                cursor.close()
           
            if not getplantsData:
                return {}

            mustplantPrice = 20
            checkTMvalue = "TR"

            # Hardcoded session data
            product_hardCoded_pipeline = [
                {"$sort": {"Product_Code": 1}},
                {"$project": {"_id": 0}}
            ]
            getsessionNo = await self.rtm_product_hardCoded_data_repo.get_Data()
            
            plants_list_pipeline = [{"$project": {"_id": 0}}]
            cursor = self.plants_list_repo.repository.aggregate('plant_master', plants_list_pipeline)
            try:
                plantData = await cursor.to_list(None)
            finally:
                cursor.close()
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
                        'schedule_date': availability_value['schedule_date'],
                        'time_block': availability_value['time_block'],
                        'plants': {}
                    }

                plant_key = availability_value['plant_id']
                plant_info = plant_dict.get(plant_key, {})
                
                dispatch_type = plant_info.get('dispatch_type')
                if(plant_key == "NTPCS"):
                    logger.debug(f"Plant info: {plant_info}, key: {plant_key}, dispatch_type: {dispatch_type}")
                availability_data_array[int(array_key)]['plants'][plant_key] = {
                    'plant_name': availability_value['plant_name'],
                    'plant_key': plant_key,
                    'plant_volume': availability_value['volume'],
                    'dispatch_type': dispatch_type,
                    'PX_Price': plant_info.get('px_price'),
                    'category': availability_value['category'],
                    'display_category': availability_value.get('display_category', 'Merit')
                }
            # return availability_data_array
            # Fetch forecast, limits, and exchange data
            cursor = self.serve_demand_repo.aggregate([
                {"$match": {
                    "schedule_date": schedule_date,
                    "type": "IDF",
                    "time_block": {"$gte": from_block, "$lte": to_block}
                }},
                {"$project": {"_id": 0}}
            ])
            try:
                getForecastedValue = await cursor.to_list(None)
            finally:
                cursor.close()

            TotalAvailability = self.total_availability_new(availability_data_array, getForecastedValue, from_block, to_block)
            
            cursor = self.limit_values_repo.aggregate([
                    {"$match": {
                        "schedule_date": schedule_date,
                        "ID": "RTM_forecasted_price",
                        "market": "RTM",
                        "block": {"$gte": from_block, "$lte": to_block}
                    }},
                    {"$project": {"_id": 0}}
                ])
            try:
                forecastedPrice = await cursor.to_list(None)
            finally:
                cursor.close()
            limit_schedule_date = schedule_date
            
            cursor = self.limit_values_repo.aggregate([
                    {"$match": {
                        "schedule_date": limit_schedule_date,
                        "ID": "Buffer",
                        "market": "RTM",
                        "block": {"$gte": from_block, "$lte": to_block}
                    }},
                    {"$project": {"_id": 0}}
                ])
            try:
                bufferPrice = await cursor.to_list(None)
            finally:
                cursor.close()
            
            cursor = self.limit_values_repo.aggregate([
                {"$match": {
                    "schedule_date": limit_schedule_date,
                    "ID": "Ceiling_Price",
                    "market": "RTM",
                    "category": "Limits",
                    "block": {"$gte": from_block, "$lte": to_block}
                }},
                {"$project": {"_id": 0}}
            ])
            try:
                ceilingPrice = await cursor.to_list(None)
            finally:
                cursor.close()
            

            cursor = self.limit_values_repo.aggregate([
                {"$match": {
                    "schedule_date": limit_schedule_date,
                    "ID": "n_1",
                    "market": "RTM",
                    "category": "Limits",
                    "block": {"$gte": from_block, "$lte": to_block}
                }},
                {"$project": {"_id": 0}}
            ])
            try:
                n_1Limits = await cursor.to_list(None)
            finally:
                cursor.close()

            cursor = self.limit_values_repo.aggregate([
                {"$match": {
                    "schedule_date": limit_schedule_date,
                    "ID": "noc_buy",
                    "market": "RTM",
                    "category": "Limits",
                    "block": {"$gte": from_block, "$lte": to_block}
                }},
                {"$project": {"_id": 0}}
            ])
            try:
                noc_buyLimits = await cursor.to_list(None)
            finally:
                cursor.close()

            cursor = self.limit_values_repo.aggregate([
                {"$match": {
                    "schedule_date": limit_schedule_date,
                    "ID": "noc_sell",
                    "market": "RTM",
                    "category": "Limits",
                    "block": {"$gte": from_block, "$lte": to_block}
                }},
                {"$project": {"_id": 0}}
            ])
            try:
                noc_sellLimits = await cursor.to_list(None)
            finally:
                cursor.close()

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
                        'Date': schedule_date,
                        # 'start_time': availability_data_array_b_wise['start_time'],
                        # 'end_tme': availability_data_array_b_wise['end_time'],
                        # 'full_time': availability_data_array_b_wise['full_time'],
                        'time_block': TotalblkwiseData['BlockNo'],
                        'session_number': getsessionNo[int(TotalblkwiseData['BlockNo'])-1]["Session_Id"],
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
                        'Date': schedule_date,
                        # 'start_time': availability_data_array_b_wise['start_time'],
                        # 'end_tme': availability_data_array_b_wise['end_time'],
                        # 'full_time': availability_data_array_b_wise['full_time'],
                        'time_block': TotalblkwiseData['BlockNo'],
                        'session_number': getsessionNo[int(TotalblkwiseData['BlockNo'])-1]["Session_Id"],
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
                        logger.debug(f"Plant: {plant_name}, dispatch: {dispatch_type}, surplus_deficit: {TotalblkwiseData['surplus_deficit']}")
                        if TotalblkwiseData["surplus_deficit"] > 0:
                            if checkTMvalue == "TM":
                                # TMvalueofPl = float(allTMValues.get(f"{availability_data_array_b_wise['schedule_date']}_{plant_name}_{availability_data_array_b_wise['time_block']}", 0))
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
                                    'Date': schedule_date,
                                    # 'start_time': availability_data_array_b_wise['start_time'],
                                    # 'end_tme': availability_data_array_b_wise['end_time'],
                                    # 'full_time': availability_data_array_b_wise['full_time'],
                                    'time_block': TotalblkwiseData['BlockNo'],
                                    'session_number': getsessionNo[int(TotalblkwiseData['BlockNo'])-1]["Session_Id"],
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
                            'Date': schedule_date,
                            # 'start_time': availability_data_array_b_wise['start_time'],
                            # 'end_tme': availability_data_array_b_wise['end_time'],
                            # 'full_time': availability_data_array_b_wise['full_time'],
                            'time_block': TotalblkwiseData['BlockNo'],
                            'session_number': getsessionNo[int(TotalblkwiseData['BlockNo'])-1]["Session_Id"],
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
        
            pipeline = [
    # --- Match schedule_date, dc_sg, and time_block ---
    {
        "$match": {
            "schedule_date": schedule_date,
            "dc_sg": "SG",
            "time_block": {"$gte": from_block, "$lte": to_block}
        }
    },

    # --- Group initial plant blocks ---
    {
        "$group": {
            "_id": {
                "plant": "$plant",
                "plant_name": "$plant_name",
                "plant_id": "$plant_id"
            },
            "c_s": {"$first": "$c_s"},
            "TM": {"$first": "$TM"},
            "multiplier": {"$first": "$multiplier"},
            "schedule_date": {"$first": "$schedule_date"},
            "blocks": {
                "$push": {
                    "time_block": "$time_block",
                    "volume": "$volume",
                    "full_time": "$full_time"
                }
            }
        }
    },

    # --- Unwind blocks ---
    {"$unwind": "$blocks"},

    # --- Lookup demand data ---
    {
        "$lookup": {
            "from": "serve_demand_table",
            "let": {"t_block": "$blocks.time_block", "sched": "$schedule_date"},
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                {"$eq": ["$time_block", "$$t_block"]},
                                {"$eq": ["$schedule_date", "$$sched"]},
                                {"$eq": ["$type", "IDF"]}
                            ]
                        }
                    }
                },
                {"$group": {"_id": None, "forecast": {"$sum": "$forecast"}}}
            ],
            "as": "demand"
        }
    },
    {"$unwind": {"path": "$demand", "preserveNullAndEmptyArrays": True}},

    # --- Lookup Plant Master (Fetch PX_price) ---
    {
        "$lookup": {
            "from": "plant_master",
            "let": {"pid": "$_id.plant_id"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$plant_id", "$$pid"]}}},
                {"$project": {"_id": 0, "px_price": 1}}
            ],
            "as": "plant_master"
        }
    },

    # --- Unwind plant_master and add top-level PX_price ---
    {"$unwind": {"path": "$plant_master", "preserveNullAndEmptyArrays": False}},
    {"$addFields": {"PX_price": "$plant_master.px_price"}},

    # --- Lookup exchange MCP ---
    {
        "$lookup": {
            "from": "serve_exchange_table",
            "let": {"sched": "$schedule_date", "tb": "$blocks.time_block"},
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                {"$eq": ["$market", "RTM"]},
                                {"$eq": ["$exchange", "IEX"]},
                                {"$eq": ["$schedule_date", "$$sched"]},
                                {"$eq": ["$time_block", "$$tb"]}
                            ]
                        }
                    }
                },
                {"$project": {"_id": 0, "mcp": 1}}
            ],
            "as": "exchange_data"
        }
    },
    {"$unwind": {"path": "$exchange_data", "preserveNullAndEmptyArrays": True}},

    # --- Lookup RTM product data ---
    {
        "$lookup": {
            "from": "rtm_product_hardCoded_data",
            "let": {"tb": "$blocks.time_block"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$From_Period_Code", "$$tb"]}}},
                {"$project": {"_id": 0, "Session_Id": 1}}
            ],
            "as": "rtm_data"
        }
    },
    {"$unwind": {"path": "$rtm_data", "preserveNullAndEmptyArrays": True}},

    # --- Lookup all limit values ---
    *self._limit_values_lookups(),

    # --- Group back by plant ---
    {
        "$group": {
            "_id": {
                "plant": "$_id.plant",
                "plant_name": "$_id.plant_name",
                "plant_id": "$_id.plant_id",
                "c_s": "$c_s",
                "TM": "$TM",
                "multiplier": "$multiplier"
            },
            "PX_price": {"$first": "$PX_price"},  # top-level PX_price
            "blocks": {
                "$push": {
                    "time_block": "$blocks.time_block",
                    "volume": "$blocks.volume",
                    "full_time": "$blocks.full_time",
                    "Session_Id": "$rtm_data.Session_Id",
                    "forecast_value": "$demand.forecast",
                    "forecasted_price": "$exchange_data.mcp",
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

    # --- Sort by PX_price ascending ---
    {"$sort": {"PX_price": 1}},  # use -1 for descending

    # --- Final projection ---
    {
        "$project": {
            "_id": 0,
            "plant": "$_id.plant",
            "plant_name": "$_id.plant_name",
            "plant_id": "$_id.plant_id",
            "PX_price": 1,
            "c_s": "$_id.c_s",
            "TM": "$_id.TM",
            "multiplier": "$_id.multiplier",
            "blocks": 1
        }
    }
]




            result = self.supply_repo.repository.aggregate(self.supply_repo.supply_serve, pipeline)
           
            # --- Prepare data for TotalAvailability ---
            get_plants_data = []
            get_forecasted_value = []

            for plant in result:
                for blk in plant.get("blocks", []):
                    px_price = plant.get("PX_price") or 0 
                    get_plants_data.append({
                        "time_block": blk["time_block"],
                        "full_time": blk.get("full_time", ""),
                        "plants": [
                            {
                                "plant_name": plant["plant_name"],
                                "dispatch_type": "Merit" if px_price > 0 else "Must",
                                "plant_id": plant["plant_id"],
                                "plant_volume": blk["volume"],
                                "c_s": plant["c_s"],
                                "c_s": plant["c_s"],
                                "PX_price": px_price,
                            }
                        ],
                        "forecasted_price": blk.get("forecasted_price"),
                        "buffer_limit": blk.get("buffer_limit"),
                        "ceiling_price": blk.get("ceiling_price"),
                        "n1_limit": blk.get("n1_limit"),
                        "noc_buy_limit": blk.get("noc_buy_limit"),
                        "noc_sell_limit": blk.get("noc_sell_limit"),
                        "iex_split": blk.get("iex_split"),
                        "pxil_split": blk.get("pxil_split"),
                        "Session_Id": blk.get("Session_Id")
                    })
                    get_forecasted_value.append({
                        "totalvol": blk.get("forecast_value", 0)
                    })

            # --- Call TotalAvailability ---
            availability_result = await self.get_total_availability(
                get_plants_data,
                get_forecasted_value,
                
            )

            return availability_result

        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise




    def total_availability_new(self, raw_data, getForecastedValue, from_block, to_block):
        """
        Compute total availability per time block.
        """
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
            # print(i)
            # print("Index:", i, "Data:", getplantsDataaa['plants'])
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
                # 'full_time': getplantsDataaa.get("full_time"),
                'BlockNo': getplantsData[i]["time_block"],
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

        return data_ar

    async def rtm_single_bid_table_old(
        self, 
        schedule_date: Optional[str], 
        from_block: int, 
        to_block: int
    ) -> List[Dict[str, Any]]:
        """
        Fetch DSM data and compute total availability with all limits and splits.
        """

        try:
            # Use hardcoded date if not provided
            if not schedule_date:
                schedule_date = '2025-08-04'

            pipeline = [
    # --- Match schedule_date, dc_sg, and time_block ---
    {
        "$match": {
            "schedule_date": schedule_date,
            "dc_sg": "SG",
            "time_block": {"$gte": from_block, "$lte": to_block}
        }
    },

    # --- Group initial plant blocks ---
    {
        "$group": {
            "_id": {
                "plant": "$plant",
                "plant_name": "$plant_name",
                "plant_id": "$plant_id"
            },
            "c_s": {"$first": "$c_s"},
            "TM": {"$first": "$TM"},
            "multiplier": {"$first": "$multiplier"},
            "schedule_date": {"$first": "$schedule_date"},
            "blocks": {
                "$push": {
                    "time_block": "$time_block",
                    "volume": "$volume",
                    "full_time": "$full_time"
                }
            }
        }
    },

    # --- Unwind blocks ---
    {"$unwind": "$blocks"},

    # --- Lookup demand data ---
    {
        "$lookup": {
            "from": "serve_demand_table",
            "let": {"t_block": "$blocks.time_block", "sched": "$schedule_date"},
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                {"$eq": ["$time_block", "$$t_block"]},
                                {"$eq": ["$schedule_date", "$$sched"]},
                                {"$eq": ["$type", "IDF"]}
                            ]
                        }
                    }
                },
                {"$group": {"_id": None, "forecast": {"$sum": "$forecast"}}}
            ],
            "as": "demand"
        }
    },
    {"$unwind": {"path": "$demand", "preserveNullAndEmptyArrays": True}},

    # --- Lookup Plant Master (Fetch PX_price) ---
    {
        "$lookup": {
            "from": "plant_master",
            "let": {"pid": "$_id.plant_id"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$plant_id", "$$pid"]}}},
                {"$project": {"_id": 0, "px_price": 1}}
            ],
            "as": "plant_master"
        }
    },

    # --- Unwind plant_master and add top-level PX_price ---
    {"$unwind": {"path": "$plant_master", "preserveNullAndEmptyArrays": False}},
    {"$addFields": {"PX_price": "$plant_master.px_price"}},

    # --- Lookup exchange MCP ---
    {
        "$lookup": {
            "from": "serve_exchange_table",
            "let": {"sched": "$schedule_date", "tb": "$blocks.time_block"},
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                {"$eq": ["$market", "RTM"]},
                                {"$eq": ["$exchange", "IEX"]},
                                {"$eq": ["$schedule_date", "$$sched"]},
                                {"$eq": ["$time_block", "$$tb"]}
                            ]
                        }
                    }
                },
                {"$project": {"_id": 0, "mcp": 1}}
            ],
            "as": "exchange_data"
        }
    },
    {"$unwind": {"path": "$exchange_data", "preserveNullAndEmptyArrays": True}},

    # --- Lookup RTM product data ---
    {
        "$lookup": {
            "from": "rtm_product_hardCoded_data",
            "let": {"tb": "$blocks.time_block"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$From_Period_Code", "$$tb"]}}},
                {"$project": {"_id": 0, "Session_Id": 1}}
            ],
            "as": "rtm_data"
        }
    },
    {"$unwind": {"path": "$rtm_data", "preserveNullAndEmptyArrays": True}},

    # --- Lookup all limit values ---
    *self._limit_values_lookups(),

    # --- Group back by plant ---
    {
        "$group": {
            "_id": {
                "plant": "$_id.plant",
                "plant_name": "$_id.plant_name",
                "plant_id": "$_id.plant_id",
                "c_s": "$c_s",
                "TM": "$TM",
                "multiplier": "$multiplier"
            },
            "PX_price": {"$first": "$PX_price"},  # top-level PX_price
            "blocks": {
                "$push": {
                    "time_block": "$blocks.time_block",
                    "volume": "$blocks.volume",
                    "full_time": "$blocks.full_time",
                    "Session_Id": "$rtm_data.Session_Id",
                    "forecast_value": "$demand.forecast",
                    "forecasted_price": "$exchange_data.mcp",
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

    # --- Sort by PX_price ascending ---
    {"$sort": {"PX_price": 1}},  # use -1 for descending

    # --- Final projection ---
    {
        "$project": {
            "_id": 0,
            "plant": "$_id.plant",
            "plant_name": "$_id.plant_name",
            "plant_id": "$_id.plant_id",
            "PX_price": 1,
            "c_s": "$_id.c_s",
            "TM": "$_id.TM",
            "multiplier": "$_id.multiplier",
            "blocks": 1
        }
    }
]




            result = self.supply_repo.repository.aggregate(self.supply_repo.supply_serve, pipeline)
           
            # --- Prepare data for TotalAvailability ---
            get_plants_data = []
            get_forecasted_value = []

            for plant in result:
                for blk in plant.get("blocks", []):
                    px_price = plant.get("PX_price") or 0 
                    get_plants_data.append({
                        "time_block": blk["time_block"],
                        "full_time": blk.get("full_time", ""),
                        "plants": [
                            {
                                "plant_name": plant["plant_name"],
                                "dispatch_type": "Merit" if px_price > 0 else "Must",
                                "plant_id": plant["plant_id"],
                                "plant_volume": blk["volume"],
                                "c_s": plant["c_s"],
                                "c_s": plant["c_s"],
                                "PX_price": px_price,
                            }
                        ],
                        "forecasted_price": blk.get("forecasted_price"),
                        "buffer_limit": blk.get("buffer_limit"),
                        "ceiling_price": blk.get("ceiling_price"),
                        "n1_limit": blk.get("n1_limit"),
                        "noc_buy_limit": blk.get("noc_buy_limit"),
                        "noc_sell_limit": blk.get("noc_sell_limit"),
                        "iex_split": blk.get("iex_split"),
                        "pxil_split": blk.get("pxil_split"),
                        "Session_Id": blk.get("Session_Id")
                    })
                    get_forecasted_value.append({
                        "totalvol": blk.get("forecast_value", 0)
                    })

            # --- Call TotalAvailability ---
            availability_result = await self.get_total_availability(
                get_plants_data,
                get_forecasted_value,
                
            )

            return availability_result

        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise

    async def get_total_availability( 
        self,
        get_plants_data: List[Dict[str, Any]],
        get_forecasted_value: List[Dict[str, Any]],
        
    ) -> List[Dict[str, Any]]:
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
                "BlockNo": block_data.get("time_block"),
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
                "px_price":bl_plants.get("PX_price"),
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

        return data_ar


    def _limit_values_lookups(self):
        """
        Helper function to return all $lookup stages for limit_values.
        """
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
                    "from": "limit_values",
                    "let": {"sched": "$schedule_date", "tb": "$blocks.time_block"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$ID", ID]},
                                        {"$eq": ["$area", area]},
                                        {"$eq": ["$schedule_date", "$$sched"]},
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
            result = self.supply_repo.repository.aggregate(self.supply_repo.supply_serve, pipeline)
            return {"result": result}
        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise

    async def submit_for_approval_post_data(self, request_data: dict):
        try:
            dbarray = {}
            
            # Helper function to get block date - extract from time_block_range if time_block_dates missing
            def get_block_date_data(index):
                if "time_block_dates" in request_data and index < len(request_data["time_block_dates"]):
                    return request_data["time_block_dates"][index].split("_")
                elif index < len(request_data["time_block_range"]):
                    # Extract date from time_block_range format: "23:30-23:45_95_2025-10-27"
                    time_range_parts = request_data["time_block_range"][index].split("_")
                    if len(time_range_parts) >= 3:
                        return [time_range_parts[2]]  # Return as list to match expected format
                return [""]
            
            # Iterate through each time block range
            for time_key, time_block_value in enumerate(request_data["time_block_range"]):
                keys = time_key
                blno = time_block_value.split("_")
                bltimefull = blno[0].split("-")
                dates = get_block_date_data(time_key)

                # Get revision
                
                getRevision = await self.single_bid_data_for_approval_repo.getPlantDataForApprovalbytime(
                    datetime.now().strftime("%Y-%m-%d"), bltimefull[0]
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
                
                # await self.rtm_currect_selected_forecast_log_repo.insert_many([loginsertarray])
                # return loginsertarray
                # Fetch product code
                get_product_code = await self.rtm_product_hardCoded_data_repo.get_period(
                    bltimefull[0]
                )

                session_block = get_product_code["Session_Id"] if get_product_code else 0
                
                # Build dbarray for this block
                date_obj = datetime.strptime(dates[0], "%d-%m-%Y")  # parse string to datetime
                formatted_date = date_obj.strftime("%Y-%m-%d")
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
                    "member_id": "N2UP0UPC0000",
                    "user_id": "UPC01",
                    "portfolio_code": "N2UP0UPC0001",
                    "sheet_market": "INDIA-RTM",
                    "requested_by": request_data["current_user"],
                    "requested_time": current_time,
                    "approved_by": "",
                    "approved_time": "",
                    "created_by": "",
                    "created_time": "",
                    "Ex_split_string": request_data[f"Exsplitstringblk_{blno[1]}"],
                    "remark": "",
                    "buy_sell_bid": request_data[f"buy_sell_bid_type_{blno[1]}"]
                }
                
                # Handle bid status
                if request_data[f"buy_sell_bid_type_{blno[1]}"] == "withdrawBid":
                    dbarray[keys]["bid_status"] = "pending_withdrawal"
                    notificationData = {
                        "bid_status": "submitForWithdrawal",
                        "module_name": "Single Bid",
                        "title": f"RTM Single Bid Submitted for Withdrawal by {request_data['current_user']}",
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
                        "title": f"RTM Single Bid Submitted for Approval Requested by {request_data['current_user']}",
                        "description": "RTM single bid submitted for approval of the selected time blocks",
                        "time_block": request_data["time_block_range"],
                        "submitted_time": datetime.now().strftime("%d-%m-%Y %H:%M"),
                        "action": "#",
                        "notification_type": "bid_for_Approvel"
                    }

                plcounter = 0
                buy_sell_type = request_data[f"buy_sell_bid_type_{blno[1]}"]
                
                if buy_sell_type != "buy":
                    dbarray[keys]["Plants"] = {}
                    
                    for plkey, plvalue in enumerate(request_data.get("bid_plant_name", [])):
                        logger.debug(f"Processing plant index: {plkey}")

                        # Safe retrieval of category
                        bid_categories = request_data.get("bid_category", [])
                        cat = bid_categories[plkey] if plkey < len(bid_categories) else "Unknown"

                        # Ensure blno has at least 2 elements
                        if len(blno) < 2:
                            logger.warning(f"Skipping plant {plvalue}: blno has insufficient elements")
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
                        elif plvalue == bidPriceNOC():  # assuming bidPriceNOC() returns a string
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
                        # print("end---")
                        plcounter += 1
                    logger.debug("end-if---")
                    
                    # print(dbarray)
                else:
                    # Buy side handling
                    if request_data["selected_action_type"] == "with_cut_off":
                        buyinsert = await self.submit_for_approval_post_data_buy(request_data, blno[1])
                    else:
                        
                        buyinsert = await self.submit_for_approval_post_data_buy_no_cutoff(request_data, blno[1])
                        return buyinsert
                    dbarray[keys]["Plants"] = buyinsert

            
            # Insert final data into model
            
            response = await self.single_bid_data_for_approval_repo.insert_many(dbarray) # type: ignore
            
            if response:
                return {"status": "success", "code": 200, "message": "Successfully Inserted"}
            else:
                return {"status": "error", "code": 404, "message": "Error: data not found!"}

        except Exception as e:
            return {"status": "error", "code": 500, "message": f"Internal Server Error: {str(e)}"}

    async def submit_for_approval_post_data_buy(self, request: dict, blockno: str):
        """
        Convert PHP submitForApprovalPostDataBuy to Python async version.
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
        
        # Step 1: Build buybiddataarray for the given blockno
        for plkey, plvalue in enumerate(request["time_block_range"]):
            blno_parts = plvalue.split("_")
            blno_index = blno_parts[1]
            blockdate = get_block_date(plkey)

            if blockno == blno_index:
                buyary = {}
                for idx, bid_price in enumerate(request[f"bid_price_{blno_index}", []]):
                    bid_volume = request[f"bid_volume_{blno_index}", []][idx]
                    if bid_price not in [bidPriceNOC(), "0", ""] and bid_volume != "0":
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
                if price_str not in ["0", bidPriceNOC()]:
                    buypricekeys.append(price)
                    incresingprbuy2.append(price + 1)

        finalpricearraybuy = sorted(set(buypricekeys + incresingprbuy2))
        finalpricearray = [0] + sorted(list(set(finalpricearraybuy + [bidPriceNOC()])))

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
                            pRarrayf = {
                                "bid_plant_name": f"Buy_{counterkey}",
                                "bid_price": pricevalue,
                                "bid_default_price": pricevalue,
                                "bid_volume": "",
                                "bid_default_volume": "",
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

    async def calSchedulingTableData(self, schedule_date): 
        try:
            getsessionNo = await self.rtm_product_hardCoded_data_repo.get_Data()
            
            # Fetch all documents from plants_list
            getPlantList = await self.plants_list_repo.get_Data()
            
            mustplantPrice = "20"

            yesterday = date.today() - timedelta(days=1)
            tomorrow  = date.today() + timedelta(days=1)


            
            allTMValues = await self.limit_values_repo.get_data( yesterday, tomorrow, "RTM", "TM", "NA")
            allSEZValues = await self.limit_values_repo.get_data( yesterday, tomorrow, "DAM", "SEZ", "OB")
            allMLTPValues = await self.limit_values_repo.get_data( yesterday, tomorrow, "RTM", "Multiplier", "OB")
            
            
            getplantsData = await self.get_all_plant_data_for_scheduling(schedule_date, "SG")
            # return getplantsData
            getplantsDataDC =  await self.get_all_plant_data_for_scheduling(schedule_date, "DC")
            
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

        cursor = self.supply_repo.repository.aggregate(self.supply_repo.supply_serve, pipeline) # type: ignore
        try:
            posted_jobs = await cursor.to_list(None)
        finally:
            cursor.close()
        
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
            pipeline = [
        {
            "$match": {
                "inserted_date": schedule_date,
                "block_no": {"$gte": from_block, "$lte": to_block}
            }
        },
        {
            "$sort": {"block_no": 1, "revision_no": -1}  # block_no asc, revision_no desc
        },
        {
            "$group": {
                "_id": "$time_block",  # unique per block
                "doc": {"$first": "$$ROOT"}  # take first document after sort
            }
        },
        {
            "$replaceRoot": {"newRoot": "$doc"}  # flatten back to original structure
        },
        
    ]
            data_cursor  = self.single_bid_data_for_approval_repo.repository.aggregate(
                self.single_bid_data_for_approval_repo.collection_name, 
                pipeline
            )
            data_list = list(data_cursor)
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
                            if 0 < len(plant_keys):
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

                            block['bid_plant_name'] = data[j]['Plants'][i]['bid_plant_name']
                            block['bid_plant_category'] = data[j]['Plants'][i]['bid_plant_category']
                            block['bid_volume'] = float(data[j]['Plants'][i]['bid_volume'])
                            block['bid_price'] = float(data[j]['Plants'][i]['bid_price'])
                            block['bid_default_price'] = float(data[j]['Plants'][i]['bid_default_price'])
                            block['bid_default_volume'] = float(data[j]['Plants'][i]['bid_default_volume'])
                            block['rtm_forecast_Price'] = data[j]['Plants'][i]['rtm_forecast_Price']

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
                    
                    if plants: # type: ignore
                        # print(data[j]['Plants'],i)
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
            if data_array:  # checks if list/dict is non-empty
                datapl = {}
                datapl["plant_data"] = data_array
                datapl["plant_name"] = all_plants_array
                datapl["selected_block"] = list(set(alltimeblock))  # array_unique equivalent
                datapl["block_wise_data"] = data_arrayblwise

            return {"result": datapl}
        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
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
    

    async def BidCreationpost(self, request):
        try:
            # ---------- INITIALIZATION ----------
            allpricearray = [0.0]
            allpricearray2 = []
            buybiddataarray = {}
            finalbuyarraysorted = {}

            biding_date = datetime.strptime(request["bidingDate"], "%d-%m-%Y")

            # ---------- STEP 1: COLLECT PRICES ----------
            for plkey, plvalue in enumerate(request["time_block_range"]):
                blno = plvalue.split("_")  # ["23:30-23:45", "95", "2025-10-27"]
                if len(blno) < 3:
                    continue

                block_date = datetime.strptime(blno[2], "%Y-%m-%d")

                if biding_date.date() == block_date.date():
                    bid_type = request["sell_buy_bid_type"][plkey].lower()

                    # ---- SELL ----
                    if bid_type == "sell":
                        price_key = f"bid_price_{blno[1]}"
                        if price_key not in request:
                            continue

                        for plvalue22 in request[price_key]:
                            val = float(plvalue22)
                            allpricearray.append(val)
                            if int(val) != 0:
                                allpricearray2.append(float(val + 1))
                            else:
                                allpricearray2.append(float(val))

                    # ---- BUY ----
                    elif bid_type == "buy":
                        price_key = f"bid_price_{blno[1]}"
                        vol_key = f"bid_volume_{blno[1]}"
                        if price_key not in request or vol_key not in request:
                            continue

                        buyary = {}
                        for idx, price_str in enumerate(request[price_key]):
                            if price_str in ["", None]:
                                continue
                            price_f = float(price_str)
                            if price_f == bidPriceNOC():
                                continue

                            volume = float(request[vol_key][idx])
                            buyary[price_f] = volume
                            finalbuyarraysorted[price_f] = volume

                        buybiddataarray[blno[1]] = dict(sorted(buyary.items(), reverse=True))

            # ---------- STEP 2: PREPARE BUY PRICE LIST ----------
            buypricekeys = []
            if buybiddataarray:
                for plkey, plvalue in enumerate(request["time_block_range"]):
                    blno = plvalue.split("_")
                    if len(blno) < 3:
                        continue

                    block_date = datetime.strptime(blno[2], "%Y-%m-%d")
                    if biding_date.date() != block_date.date():
                        continue

                    if blno[1] in buybiddataarray:
                        for pricekey in buybiddataarray[blno[1]].keys():
                            buypricekeys.append(float(pricekey))

            finalarrayforbidbuy = sorted(set(buypricekeys))
            finalpricearraysell = sorted(set(allpricearray + allpricearray2))
            finalpricearray = sorted(set(finalpricearraysell + finalarrayforbidbuy))
            finalpricearray.append(bidPriceNOC())
            finalpricearray = sorted(set(finalpricearray))

            # ---------- STEP 3: BUILD BIDBLOCKARRAY ----------
            bidblockarray = {}
            pDetailarray = {"reference_number": {}, "bid_status": {}, "approve_by": {}}

            for timeblock_key, timeblock_value in enumerate(request["time_block_range"]):
                blno = timeblock_value.split("_")
                if len(blno) < 3:
                    continue

                block_date = datetime.strptime(blno[2], "%Y-%m-%d")
                if biding_date.date() != block_date.date():
                    continue

                fsellvolume = 0
                blk_key = f"{blno[0]}_{blno[1]}"
                bid_type = request["sell_buy_bid_type"][timeblock_key].lower()

                # ---- SELL ----
                if bid_type == "sell":
                    price_key = f"bid_price_{blno[1]}"
                    vol_key = f"bid_volume_{blno[1]}"
                    if price_key not in request or vol_key not in request:
                        continue

                    for i, pricevalue in enumerate(finalpricearray):
                        # return i
                        pRarrayf = {}
                        for idx, price in enumerate(request[price_key]):
                            price_f = float(price)
                            vol_f = float(request[vol_key][idx])

                            if pricevalue == price_f:
                                fsellvolume += 0
                            elif pricevalue == price_f + 1:
                                fsellvolume += vol_f

                            pRarrayf = {
                                "RTMPrice": pricevalue,
                                "Volume": fsellvolume,
                                "sell_buy_bid_type": bid_type,
                                "biding_date": biding_date.strftime("%Y-%m-%d"),
                            }
                            bidblockarray.setdefault(blk_key, {})[i] = pRarrayf

                # ---- BUY ----
                elif bid_type == "buy":
                    if blno[1] not in buybiddataarray:
                        continue

                    buypriceary = list(buybiddataarray[blno[1]].values())

                    for pricevalue in finalpricearray:
                        pRarrayf = {}
                        matched = False
                        for plkey_price, plval_vol in buybiddataarray[blno[1]].items():
                            if pricevalue == 0:
                                vol = min(buybiddataarray[blno[1]].values()) if buybiddataarray[blno[1]] else 0
                                matched = True
                            elif pricevalue == plkey_price:
                                vol = plval_vol
                                buypriceary = [v for v in buypriceary if v != plval_vol]
                                matched = True
                            elif pricevalue == plkey_price + 1:
                                vol = min(buypriceary) if buypriceary else 0
                                matched = True
                            else:
                                continue

                            pRarrayf = {
                                "RTMPrice": pricevalue,
                                "Volume": vol if matched else "",
                                "sell_buy_bid_type": bid_type,
                                "biding_date": biding_date.strftime("%Y-%m-%d"),
                            }
                            bidblockarray.setdefault(blk_key, {})[pricevalue] = pRarrayf
                            if matched:
                                break
                        if not matched:
                            bidblockarray.setdefault(blk_key, {})[pricevalue] = {
                                "RTMPrice": pricevalue,
                                "Volume": "",
                                "sell_buy_bid_type": bid_type,
                                "biding_date": biding_date.strftime("%Y-%m-%d"),
                            }

                # ---- Detail Arrays ----
                pDetailarray["reference_number"][timeblock_key] = request["reference_number"][timeblock_key]
                pDetailarray["bid_status"][timeblock_key] = request["bid_status"][timeblock_key]
                pDetailarray["approve_by"][timeblock_key] = request["approve_by"][timeblock_key]

            # ---------- STEP 4: SPLITTING EXCHANGE WISE ----------
            bidblockarrayExWise = {}
            for Tblk, bidblockarray1 in bidblockarray.items():
                blkno = Tblk.split("_")
                if len(blkno) < 2:
                    continue

                ex_key = f"Ex_split_string_{blkno[1]}"
                if ex_key not in request:
                    continue

                ex_splits = request[ex_key].split("_")
                for ExNo, ex_split in enumerate(ex_splits):
                    ex_parts = ex_split.split("-")
                    if len(ex_parts) >= 2:
                        ex_percent = float(ex_parts[1])
                    else:
                        ex_percent = 0

                    for plkey, bidblockarrayPriceWise in bidblockarray1.items():
                        ExbidArray = {
                            "RTMPrice": bidblockarrayPriceWise["RTMPrice"],
                            "Volume": int(float(bidblockarrayPriceWise["Volume"]) * ex_percent / 100),
                            "AC_Volume": bidblockarrayPriceWise["Volume"],
                            "sell_buy_bid_type": bidblockarrayPriceWise["sell_buy_bid_type"],
                            "biding_date": bidblockarrayPriceWise["biding_date"],
                        }

                        bidblockarrayExWise.setdefault(ExNo, {}).setdefault(blkno[0], {})[plkey] = ExbidArray

            # ---------- RETURN ----------
            return {
                "status": "success",
                "bidblockarray": bidblockarray,
                "bidblockarrayExWise": bidblockarrayExWise,
                "finalpricearray": finalpricearray,
                "pDetailarray": pDetailarray,
            }

        except Exception as e:
            import traceback
            logger.error(f"Error in BidCreationpost: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    async def submitForPreExchangeData(self, data):
        try:
            data_array = []  # list to hold each reference dictionary

            for reference in data:
                ref_dict = {
                    "reference_number": reference.reference_number,
                    "bid_status": reference.bid_status,
                    "Remarks": reference.Remarks,
                    "action_by": reference.action_by,
                    "block_no": reference.block_no
                }
                self.updatePlantDataForApproval(ref_dict)
                data_array.append(ref_dict)  # add to the list

            return {"result": True, "data": data_array}

        except Exception as e:
            logger.exception(f"[DashboardService] Failed to fetch demand data: {str(e)}")
            raise
    
    async def updatePlantDataForApproval(self, data):
        
        try:
            filter_query = {
                "_id": data["reference_number"],
                "block_no": int(data["block_no"])
            }

            update_query = {
                "$set": {
                    "bid_status": data["bid_status"],
                    "remark": data["Remarks"],
                    "approved_by": data["action_by"],
                    "approved_time": datetime.now().strftime("%H:%M")
                }
            }

            result = self.single_bid_data_for_approval_repo.update_one(filter_query, update_query)
            return result.modified_count  # returns number of documents updated

        except Exception as e:
            logger.exception(f"[updatePlantDataForApproval] Error: {str(e)}")
            raise
# Global instance
realtimemarket_service = RealtimemarketService()