"""
Day-Ahead Market service layer using repository pattern.
Handles business logic for day-ahead market related data operations.
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import pytz
from math import isnan

from ..repository.base_repository import GenericRepository
from services.processing.app.core.config import settings

logger = logging.getLogger(__name__)

def bidPriceNOC():
    """Equivalent of PHP's bidPriceNOC() constant."""
    return 10000

class DayaheadmarketService:
    """
    Service class responsible for day-ahead market business logic.
    """

    def _convert_date_format(self, date_str: str) -> str:
        """
        Convert date from YYYY-MM-DD to DD-MM-YYYY format.
        If conversion fails, returns the original string.
        """
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            return date_obj.strftime("%d-%m-%Y")
        except ValueError:
            return date_str

    def __init__(self):
        self.repo = GenericRepository()
        self.supply_collection = settings.MONGODB_SUPPLY_SERVE_COLLECTION
        self.demand_collection = settings.MONGODB_DEMAND_SERVE_COLLECTION
        self.dam_product_hardcoded_collection = settings.MONGODB_DAM_PRODUCT_HARDCODED_DATA_COLLECTION
        self.plant_master_collection = settings.MONGODB_PLANT_MASTER_COLLECTION
        self.limit_values_collection = settings.MONGODB_LIMIT_VALUES_COLLECTION
        self.dam_single_bid_data_for_approval_collection = settings.MONGODB_DAM_SINGLE_BID_DATA_FOR_APPROVAL_COLLECTION

    async def _get_active_exchange(self) -> str:
        """Get active exchange for limit_values filtering (parameter, default IEX)."""
        try:
            from .parameter_service import parameter_service
            param_exchange = await parameter_service.get_active_exchange()
            if param_exchange:
                return param_exchange
        except Exception as e:
            logger.warning(f"Failed to get active exchange from parameter: {e}")
        return "IEX"

    async def _aggregate_limit_values_with_fallback(
        self,
        pipeline: List[Dict[str, Any]],
        active_exchange: str
    ) -> List[Dict[str, Any]]:
        """
        Run a limit_values aggregate first with the active exchange filter.
        If no results are found, fall back to documents without an exchange field
        (legacy/default IEX data).
        """
        import copy
        pipeline_with_exchange = copy.deepcopy(pipeline)
        if pipeline_with_exchange and "$match" in pipeline_with_exchange[0]:
            pipeline_with_exchange[0]["$match"]["exchange"] = active_exchange

        result = list(await self.repo.aggregate_async(self.limit_values_collection, pipeline_with_exchange))
        if result:
            return result

        logger.info(
            f"No limit_values found for active exchange '{active_exchange}'; "
            "falling back to legacy/default (no exchange) documents."
        )
        return list(await self.repo.aggregate_async(self.limit_values_collection, pipeline))

    def _apply_active_limit_prefix(
        self,
        data: Dict[str, Any],
        active_exchange: str
    ) -> Dict[str, Any]:
        """
        Rename limit-values fields in single-bid-table rows so response keys
        carry the exchange they were sourced from, following the pattern
        {exchange_lowercase}_{original_fieldname} (e.g. iex_dam_nminusone_quantum).
        """
        if not data or not isinstance(data, dict):
            return data

        prefix = f"{active_exchange.lower()}_"
        limit_field_map = {
            "dam_forecasted_price": f"{prefix}dam_forecasted_price",
            "ceiling_price": f"{prefix}ceiling_price",
            "dam_buffer_value": f"{prefix}dam_buffer_value",
            "dam_nminusone_quantum": f"{prefix}dam_nminusone_quantum",
            "dam_noc_buy_value": f"{prefix}dam_noc_buy_value",
            "dam_noc_sell_value": f"{prefix}dam_noc_sell_value",
        }

        table_data = data.get("table_data")
        if not table_data or not isinstance(table_data, dict):
            return data

        for plkey, block_rows in table_data.items():
            if not isinstance(block_rows, dict):
                continue
            for blk, row in block_rows.items():
                if not isinstance(row, dict):
                    continue
                for old_key, new_key in limit_field_map.items():
                    if old_key in row:
                        row[new_key] = row.pop(old_key)

        return data

    async def dayahead_single_bid_graph(
        self, 
        schedule_date: Optional[str], 
        from_block: int, 
        to_block: int,
    ) -> Dict[str, Any]:
        """
        Fetch Day-Ahead Market (DAF) data for a given schedule date.
        
        For day-ahead market, we show all 96 blocks and use tomorrow's date.
        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format.
            from_block (int): Starting time block (always 1 for day-ahead).
            to_block (int): Ending time block (always 96 for day-ahead).
        Returns:
            Optional[Dict[str, Any]]: A dictionary containing the DAF data,
                                    or None if no data is found.
        """
        

        try:
            if schedule_date:
                dt_obj = datetime.strptime(schedule_date, '%Y-%m-%d')
            else:
                # For day-ahead market, use tomorrow's date
                dt_obj = datetime.now() + timedelta(days=1)
            
            # Set the timezone to UTC and format the datetime object
            utc_tz = pytz.utc
            dt_obj = utc_tz.localize(dt_obj.replace(hour=0, minute=0, second=0, microsecond=0))
            
            pipeline = [
                    {
                        "$match": {
                            "scheduled_at": dt_obj,
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
                                                {"$eq": ["$scheduled_at", dt_obj]},
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
            result = await self.repo.aggregate_async(self.supply_collection, pipeline)
            return {"data": result}
        except Exception as e:
            logger.exception(f"[DayaheadmarketService] Failed to fetch day-ahead data: {str(e)}")
            raise

    async def dayahead_single_bid_table(
        self, 
        schedule_date: Optional[str], 
        from_block: int, 
        to_block: int,
    ) -> Dict[str, Any]:
        """
        Fetch Day-Ahead Market (DAF) table data for a given schedule date.
        
        For day-ahead market, we show all 96 blocks and use tomorrow's date.
        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format.
            from_block (int): Starting time block (always 1 for day-ahead).
            to_block (int): Ending time block (always 96 for day-ahead).
        Returns:
            Optional[Dict[str, Any]]: A dictionary containing the DAF data,
                                    or None if no data is found.
        """
        

        try:
            if schedule_date:
                dt_obj = datetime.strptime(schedule_date, '%Y-%m-%d')
            else:
                # For day-ahead market, use tomorrow's date
                dt_obj = datetime.now() + timedelta(days=1)
            
            # Set the timezone to UTC and format the datetime object
            utc_tz = pytz.utc
            dt_obj = utc_tz.localize(dt_obj.replace(hour=0, minute=0, second=0, microsecond=0))
            
            # Fetch plant data
            pipeline = [
                {"$match": {
                    "scheduled_at": dt_obj,
                    "dc_sg": "SG",
                    "time_block": {"$gte": from_block, "$lte": to_block}
                }},
                {
                    "$lookup": {
                        "from": self.plant_master_collection,
                        "localField": "plant_id",
                        "foreignField": "plant_id",
                        "as": "plant_details"
                    }
                },
                {
                    "$unwind": "$plant_details"
                },
                {
                    "$project": {
                        "_id": 0,
                        "schedule_date": "$scheduled_at",
                        "time_block": 1,
                        "plant_id": 1,
                        "volume": 1,
                        "plant_name": "$plant_details.plant_name",
                        "dispatch_type": "$plant_details.dispatch_type",
                        "px_price": "$plant_details.px_price",
                        "category": "$plant_details.category",
                        "display_category": "$plant_details.display_category"
                    }
                }
            ]
            get_plants_data = list(await self.repo.aggregate_async(self.supply_collection, pipeline))
            
            if not get_plants_data:
                return {
                    "table_data": {},
                    "total_availability": [],
                    "message": f"No data available for schedule_date: {schedule_date}"
                }

            must_plant_price = 20
            check_tm_value = "TR"

            product_hardcoded_pipeline = [
                {"$sort": {"Product_Code": 1}},
                {"$project": {"_id": 0}}
            ]
            get_session_no = list(await self.repo.aggregate_async(self.dam_product_hardcoded_collection, product_hardcoded_pipeline))
            
            fix_count = get_plants_data[0]['time_block'] if get_plants_data else 1
            availability_data_array = {}

            for i, availability_value in enumerate(get_plants_data):
                array_key = int(availability_value["time_block"]) - fix_count
                
                if array_key not in availability_data_array:
                    availability_data_array[int(array_key)] = {
                        'schedule_date': availability_value['schedule_date'],
                        'time_block': availability_value['time_block'],
                        'plants': {},
                        'must_plants_cumulative': {
                            'total_volume': 0,
                            'plant_count': 0,
                            'plants': []
                        }
                    }

                plant_key = availability_value['plant_id']
                dispatch_type = availability_value.get('dispatch_type', 'Merit')
                
                if dispatch_type == 'Merit':
                    availability_data_array[int(array_key)]['plants'][plant_key] = {
                        'plant_name': availability_value['plant_name'],
                        'plant_key': plant_key,
                        'plant_volume': availability_value['volume'],
                        'dispatch_type': dispatch_type,
                        'PX_Price': availability_value['px_price'],
                        'category': availability_value['category'],
                        'display_category': availability_value.get('display_category', 'Merit')
                    }
                else:
                    must_plant_data = {
                        'plant_name': availability_value['plant_name'],
                        'plant_key': plant_key,
                        'plant_volume': availability_value['volume'],
                        'dispatch_type': dispatch_type,
                        'PX_Price': availability_value['px_price'],
                        'category': availability_value['category']
                    }
                    availability_data_array[int(array_key)]['must_plants_cumulative']['plants'].append(must_plant_data)
                    availability_data_array[int(array_key)]['must_plants_cumulative']['total_volume'] += availability_value['volume']
                    availability_data_array[int(array_key)]['must_plants_cumulative']['plant_count'] += 1
            
            for time_block_key, time_block_data in availability_data_array.items():
                if 'plants' in time_block_data:
                    plants_list = list(time_block_data['plants'].items())
                    plants_list.sort(key=lambda x: x[1].get('PX_Price', 0))
                    time_block_data['plants'] = dict(plants_list)
            
            get_forecasted_value = list(await self.repo.aggregate_async(self.demand_collection, [
                {"$match": {
                    "scheduled_at": dt_obj,
                    "type": "DAF",
                    "time_block": {"$gte": from_block, "$lte": to_block}
                }},
                {"$project": {"_id": 0}}
            ]))

            if not get_forecasted_value:
                get_forecasted_value = [{"forecast": 0} for _ in range(to_block - from_block + 1)]

            latest_dam_dates = list(await self.repo.aggregate_async(self.limit_values_collection, [
                {"$match": {"market": "DAM"}},
                {"$group": {"_id": "$schedule_date"}},
                {"$sort": {"_id": -1}},
                {"$limit": 1}
            ]))
            if latest_dam_dates:
                latest_limit_date = latest_dam_dates[0]["_id"]
            else:
                latest_dates = list(await self.repo.aggregate_async(self.limit_values_collection, [
                    {"$group": {"_id": "$schedule_date"}},
                    {"$sort": {"_id": -1}},
                    {"$limit": 1}
                ]))
                if latest_dates:
                    latest_limit_date = latest_dates[0]["_id"]
                else:
                    latest_limit_date = schedule_date
            
            total_availability = self.total_availability_new(availability_data_array, get_forecasted_value)

            # Active exchange for limit_values filtering
            active_exchange = await self._get_active_exchange()

            forecasted_price = await self._aggregate_limit_values_with_fallback([
                    {"$match": {
                        "schedule_date": latest_limit_date,
                        "ID": "DAM_forecasted_price",
                        "market": "DAM",
                        "block": {"$gte": from_block, "$lte": to_block}
                    }},
                    {"$project": {"_id": 0}}
                ], active_exchange)

            buffer_price = await self._aggregate_limit_values_with_fallback([
                    {"$match": {
                        "schedule_date": latest_limit_date,
                        "ID": "Buffer",
                        "market": "DAM",
                        "block": {"$gte": from_block, "$lte": to_block}
                    }},
                    {"$project": {"_id": 0}}
                ], active_exchange)

            ceiling_price = await self._aggregate_limit_values_with_fallback([
                {"$match": {
                    "schedule_date": latest_limit_date,
                    "ID": "Ceiling_Price",
                    "market": "DAM",
                    "block": {"$gte": from_block, "$lte": to_block}
                }},
                {"$project": {"_id": 0}}
            ], active_exchange)


            n1_limits = await self._aggregate_limit_values_with_fallback([
                {"$match": {
                    "schedule_date": latest_limit_date,
                    "ID": "n_1",
                    "market": "DAM",
                    "block": {"$gte": from_block, "$lte": to_block}
                }},
                {"$project": {"_id": 0}}
            ], active_exchange)

            noc_buy_limits = await self._aggregate_limit_values_with_fallback([
                {"$match": {
                    "schedule_date": latest_limit_date,
                    "ID": "noc_buy",
                    "market": "DAM",
                    "block": {"$gte": from_block, "$lte": to_block}
                }},
                {"$project": {"_id": 0}}
            ], active_exchange)

            noc_sell_limits = await self._aggregate_limit_values_with_fallback([
                {"$match": {
                    "schedule_date": latest_limit_date,
                    "ID": "noc_sell",
                    "market": "DAM",
                    "block": {"$gte": from_block, "$lte": to_block}
                }},
                {"$project": {"_id": 0}}
            ], active_exchange)

            type_of_exchange_iex = "IEX-100"
            type_of_exchange_pxil = "PXIL-10"

            data = {}
            total_sell_volume = 0
            
            for blk, total_block_wise_data in enumerate(total_availability):
                
                forecasted_price_inner = round(forecasted_price[blk].get('values', 0)) if blk < len(forecasted_price) else 0

                if isnan(forecasted_price_inner):
                    forecasted_price_inner = 0

                ceiling_price_inner = 0
                if ceiling_price and blk < len(ceiling_price) and "values" in ceiling_price[blk]:
                    ceiling_price_inner = ceiling_price[blk]["values"]
                
                buffer_price_inner = 0
                if buffer_price and blk < len(buffer_price) and "values" in buffer_price[blk]:
                    buffer_price_inner = buffer_price[blk]["values"]

                n1_limits_inner = 0
                if n1_limits and blk < len(n1_limits) and "values" in n1_limits[blk]:
                    n1_limits_inner = n1_limits[blk]["values"]

                noc_buy_limits_inner = 0
                if noc_buy_limits and blk < len(noc_buy_limits) and "values" in noc_buy_limits[blk]:
                    noc_buy_limits_inner = noc_buy_limits[blk]["values"]
                
                noc_sell_limits_inner = 0
                if noc_sell_limits and blk < len(noc_sell_limits) and "values" in noc_sell_limits[blk]:
                    noc_sell_limits_inner = noc_sell_limits[blk]["values"]
                total_block_bid = 0
                type_of_exchange_iex_blk = type_of_exchange_iex
                type_of_exchange_pxil_blk = type_of_exchange_pxil

                total_diff = total_block_wise_data["diff_for_bid_cal"]
                
                block_no = int(total_block_wise_data['BlockNo'])
                if get_session_no and block_no < len(get_session_no):
                    session_number = get_session_no[block_no]["Session_Id"]
                else:
                    session_number = None
                
                plkey = 0

                if total_diff < 0:
                    remaining_volume = round(total_diff) * -1
                    row = {
                        'Date': schedule_date,
                        'time_block': total_block_wise_data['BlockNo'],
                        'session_number': session_number,
                        'forecast': round(total_block_wise_data['forecast_value']),
                        'supply_availablity': round(total_block_wise_data['bltotalvolume']),
                        'surplus_deficit': round(total_block_wise_data['surplus_deficit']),
                        'price': must_plant_price,
                        'dam_forecasted_price': forecasted_price_inner,
                        'ceiling_price': ceiling_price_inner,
                        'dam_buffer_value': buffer_price_inner,
                        'dam_nminusone_quantum': n1_limits_inner,
                        'dam_noc_buy_value': noc_buy_limits_inner,
                        'dam_noc_sell_value': noc_sell_limits_inner,
                        'totalBlockBid': total_block_bid,
                        'remaining_volume': remaining_volume,
                        'sell_volume': remaining_volume,
                        'actual_volume': remaining_volume,
                        'category': "Must_Plants_cat",
                        'plant_name': "Must_Plants",
                        'display_category': "All",
                        'dispatch_type': "must",
                        'total_sell_volume': remaining_volume,
                        'Ex_split_string': f"IEX-{type_of_exchange_iex_blk}_PXIL-{type_of_exchange_pxil_blk}"
                    }
                    data.setdefault('table_data', {}).setdefault(plkey, {})[blk] = row
                    total_diff = 0
                    plkey += 1
                else:
                    row = {
                        'Date': schedule_date,
                        'time_block': total_block_wise_data['BlockNo'],
                        'session_number': session_number,
                        'forecast': round(total_block_wise_data['forecast_value']),
                        'supply_availablity': round(total_block_wise_data['bltotalvolume']),
                        'surplus_deficit': round(total_block_wise_data['surplus_deficit']),
                        'price': must_plant_price,
                        'dam_forecasted_price': forecasted_price_inner,
                        'ceiling_price': ceiling_price_inner,
                        'dam_buffer_value': buffer_price_inner,
                        'dam_nminusone_quantum': n1_limits_inner,
                        'dam_noc_buy_value': noc_buy_limits_inner,
                        'dam_noc_sell_value': noc_sell_limits_inner,
                        'totalBlockBid': total_block_bid,
                        'remaining_volume': 0,
                        'sell_volume': 0,
                        'actual_volume': 0,
                        'category': "Must_Plants_cat",
                        'plant_name': "Must_Plants",
                        'display_category': "All",
                        'dispatch_type': "must",
                        'total_sell_volume': 0,
                        'Ex_split_string': f"IEX-{type_of_exchange_iex_blk}_PXIL-{type_of_exchange_pxil_blk}"
                    }
                    data.setdefault('table_data', {}).setdefault(plkey, {})[blk] = row
                    plkey += 1

                # Calculate the correct key for availability_data_array
                availability_key = block_no - fix_count
                
                # Check if the block exists in availability_data_array
                if availability_key not in availability_data_array:
                    logger.warning(f"Block {block_no} (key: {availability_key}) not found in availability_data_array. Available keys: {list(availability_data_array.keys())}")
                    continue
                    
                for pl_name_key, plant in availability_data_array[availability_key]["plants"].items():
                    px_price = round(plant.get('PX_Price')) if plant.get('PX_Price') is not None else 0
                    dispatch_type = plant.get("dispatch_type", "")
                    plant_volume = float(plant.get("plant_volume"))
                    category = plant.get("category", "")
                    display_category = plant.get("display_category", "")
                    plant_name = plant.get("plant_name", "")
                    if dispatch_type == "Merit":
                        if total_block_wise_data["surplus_deficit"] > 0:
                            if check_tm_value == "TM":
                                tm_value_of_pl = 0
                                plnt_fourty_five_vol = plant_volume - tm_value_of_pl
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

                            if round(sell_volume) >= 0.01 and plnt_100_volume != 0:
                                row = {
                                    'Date': schedule_date,
                                    'time_block': total_block_wise_data['BlockNo'],
                                    'session_number': session_number,
                                    'forecast': round(total_block_wise_data['forecast_value']),
                                    'supply_availablity': round(total_block_wise_data['bltotalvolume']),
                                    'surplus_deficit': round(total_block_wise_data['surplus_deficit']),
                                    'price': px_price,
                                    'dam_forecasted_price': forecasted_price_inner,
                                    'ceiling_price': ceiling_price_inner,
                                    'dam_buffer_value': buffer_price_inner,
                                    'dam_nminusone_quantum': n1_limits_inner,
                                    'dam_noc_buy_value': noc_buy_limits_inner,
                                    'dam_noc_sell_value': noc_sell_limits_inner,
                                    'totalBlockBid': total_block_bid,
                                    'remaining_volume': float(total_diff),
                                    'sell_volume': round(sell_volume),
                                    'actual_volume': float(round(plnt_100_volume)),
                                    'category': category,
                                    'plant_name': plant_name,
                                    'display_category': display_category,
                                    'dispatch_type': dispatch_type,
                                    'total_sell_volume': total_sell_volume + sell_volume,
                                    'Ex_split_string': f"IEX-{type_of_exchange_iex_blk}_PXIL-{type_of_exchange_pxil_blk}"
                                }
                                data.setdefault('table_data', {}).setdefault(plkey, {})[blk] = row
                                plkey += 1

                    else:
                        row = {
                            'Date': schedule_date,
                            'time_block': total_block_wise_data['BlockNo'],
                            'session_number': session_number,
                            'forecast': round(total_block_wise_data['forecast_value']),
                            'supply_availablity': round(total_block_wise_data['bltotalvolume']),
                            'surplus_deficit': round(total_block_wise_data['surplus_deficit']),
                            'price': px_price,
                            'dam_forecasted_price': forecasted_price_inner,
                            'ceiling_price': ceiling_price_inner,
                            'dam_buffer_value': buffer_price_inner,
                            'dam_nminusone_quantum': n1_limits_inner,
                            'dam_noc_buy_value': noc_buy_limits_inner,
                            'dam_noc_sell_value': noc_sell_limits_inner,
                            'totalBlockBid': total_block_bid,
                            'remaining_volume': float(total_diff),
                            'sell_volume': 0,
                            'actual_volume': float(round(plant_volume)),
                            'category': category,
                            'plant_name': plant_name,
                            'display_category': display_category,
                            'dispatch_type': dispatch_type,
                            'total_sell_volume': total_sell_volume,
                            'Ex_split_string': f"IEX-{type_of_exchange_iex_blk}_PXIL-{type_of_exchange_pxil_blk}"
                        }
                        data.setdefault('table_data', {}).setdefault(plkey, {})[blk] = row
                        plkey += 1

            self._apply_active_limit_prefix(data, active_exchange)
            return data
        except Exception as e:
            logger.exception(f"[DayaheadmarketService] Failed to fetch day-ahead data: {str(e)}")
            raise

    @staticmethod
    def total_availability_new(
        raw_data: Dict[str, Any], 
        get_forecasted_value: List[Dict[str, Any]], 
    ) -> List[Dict[str, Any]]:
        """
        Compute total availability per time block.
        """
        data_ar = []
        get_plants_data = [v for k, v in raw_data.items()]
        
        if not get_plants_data:
            return []
            
        for i, get_plants_data_aa in enumerate(get_plants_data):
            if not isinstance(get_plants_data_aa, dict):
                continue
            
            total_volume_merit_bl = 0
            must_merit_fifty_five_volume_bl_sum = 0
            bl_total_volume = 0
            volume_for_bid_submission = 0
            blk_volume_sum_fifty_five = 0
            blk_volume_sum_must = 0
            
            get_plants_data_aa1 = [v for k, v in get_plants_data_aa['plants'].items()]
            for j, bl_plants in enumerate(get_plants_data_aa1):
                plnt_volume_fifty_five = 0
                total_volume_merit = 0
                plnt_volume_must = 0
                
                pl_volume = float(bl_plants.get('plant_volume'))

                if bl_plants.get('dispatch_type') == "Merit":
                    plnt_volume_fifty_five = pl_volume * 0.55
                    total_volume_merit = pl_volume
                else:
                    plnt_volume_must = pl_volume

                bl_total_volume += pl_volume
                volume_for_bid_submission += pl_volume
                total_volume_merit_bl += total_volume_merit
                blk_volume_sum_fifty_five += plnt_volume_fifty_five
                blk_volume_sum_must += plnt_volume_must
                must_merit_fifty_five_volume_bl_sum = blk_volume_sum_fifty_five + blk_volume_sum_must
            if i < len(get_forecasted_value) and get_forecasted_value[i]:
                forecast_val = get_forecasted_value[i].get('forecast', 0)
            else:
                forecast_val = 0
            
            
            data_ar.append({
                'BlockNo': get_plants_data_aa['time_block'],
                'forecast_value': forecast_val,
                'bltotalvolume': bl_total_volume,
                'volume_for_bid_submision': volume_for_bid_submission,
                'total_volume_mritbl': total_volume_merit_bl,
                'total_volume_mritbl_45': total_volume_merit_bl * 0.45,
                'blk_volume_sum_fiftyfive': blk_volume_sum_fifty_five,
                'blk_volume_sum_must': blk_volume_sum_must,
                'must_merit_fiftyfive_volume_bl_sum': must_merit_fifty_five_volume_bl_sum,
                'diff_for_bid_cal': forecast_val - must_merit_fifty_five_volume_bl_sum,
                'surplus_deficit': bl_total_volume - forecast_val
            })

        return data_ar

    async def approval_request_list(
        self, 
        schedule_date: Optional[str], 
        from_block: int, 
        to_block: int,
    ) -> Dict[str, Any]:
        """
        Fetch Day-Ahead Market approval request list for a given schedule date.
        
        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format.
            from_block (int): Starting time block.
            to_block (int): Ending time block.
        Returns:
            Dict[str, Any]: A dictionary containing the approval request list data.
        """
        try:
            if not schedule_date:
                # For dayahead market, use tomorrow's date
                tomorrow = datetime.now() + timedelta(days=1)
                schedule_date = tomorrow.strftime('%Y-%m-%d')
            
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
                {
                    "$project": {"_id": 0}  # exclude MongoDB _id
                }
            ]
            
            data_cursor = await self.repo.aggregate_async(self.dam_single_bid_data_for_approval_collection, pipeline)
            data_list = list(data_cursor)
            
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
            data_array = {}
            data_arrayblwise = {}

            # --- Build plant mappings ---
            for i, allplantsarrayval in all_plants_array.items():
                j = ""
                bl = 0
                bidvlolumetotal = 0
                alltimeblock = []
                
                for blockcount2 in range(from_block - 1, to_block):
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
                            bidvlolumetotal += float(data[j]['Plants'][i]['bid_volume'])

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

                            block['inserted_date'] = data[j]['inserted_date']
                            block['biding_date'] = data[j]['bid_date']
                            block['inserted_time'] = data[j]['inserted_time']
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

                            # Increment block index
                            bl += 1
                        else:
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

                    plants = data.get(j, {}).get('Plants', [])
                    
                    if plants:
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
                            block['inserted_date'] = data[j]['inserted_date']
                            block['biding_date'] = data[j]['bid_date']
                            block['inserted_time'] = data[j]['inserted_time']
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

            data_arrayblwise = dict(sorted(data_arrayblwise.items()))
            if data_array:  # checks if list/dict is non-empty
                datapl = {}
                datapl["plant_data"] = data_array
                datapl["plant_name"] = all_plants_array
                datapl["selected_block"] = list(set(alltimeblock))  # array_unique equivalent
                datapl["block_wise_data"] = data_arrayblwise

            return {"result": datapl}
        except Exception as e:
            logger.exception(f"[DayaheadmarketService] Failed to fetch approval request list: {str(e)}")
            raise

    def getFifteenTimeBlockID(self, id: int) -> str:
        """
        Get time block string for given block ID.
        
        Args:
            id (int): Block ID (0-95)
        Returns:
            str: Time block string (e.g., "00:00")
        """
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
            "22:00","22:15","22:30","22:45","23:00","23:15","23:30","23:45"
        ]
        
        if 0 <= id < len(time_block):
            return time_block[id]
        return "00:00"  # Default fallback

    async def BidCreationpost(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create bid data for Day-Ahead Market.
        
        Args:
            request (Dict[str, Any]): Bid creation request data
        Returns:
            Dict[str, Any]: Processed bid block data
        """
        try:
            # ---- INITIALIZATION ----
            allpricearray = [0.0]
            allpricearray2 = []
            buybiddataarray = {}
            finalbuyarraysorted = {}

            # Convert input bidding date: "15-10-2025"
            biding_date = datetime.strptime(request["bidingDate"], "%d-%m-%Y")

            # ---- STEP 1: PREPARE SELL & BUY PRICE ARRAYS ----
            for plkey, plvalue in enumerate(request["time_block_range"]):
                blno = plvalue.split("_")  # e.g. ["04:00-04:15", "17", "2025-10-15"]
                date_obj = datetime.strptime(blno[2], "%Y-%m-%d")

                if biding_date.date() == date_obj.date():
                    bid_type = request.get("sell_buy_bid_type", [""])[0] if request.get("sell_buy_bid_type") else ""

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
                bid_type = request.get("sell_buy_bid_type", [""])[0] if request.get("sell_buy_bid_type") else ""

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

                            # Ensure pricevalue is not a list
                            if isinstance(pricevalue, list):
                                pricevalue = pricevalue[0] if pricevalue else 0
                            bidblockarray.setdefault(blk_key, {})[pricevalue] = {
                                "RTMPrice": pricevalue,
                                "Volume": fsellvolume,
                                "sell_buy_bid_type": bid_type,
                                "biding_date": biding_date.strftime("%Y-%m-%d"),
                            }

                # ---- BUY ----
                elif bid_type == "buy":
                    if blno[1] not in buybiddataarray:
                        continue
                    buypriceary = list(buybiddataarray[blno[1]].values())

                    for pricevalue in finalpricearray:
                        for plkey_price, plval_vol in buybiddataarray[blno[1]].items():
                            if pricevalue == 0:
                                vol = min(buybiddataarray[blno[1]].values()) if buybiddataarray[blno[1]] else 0
                            elif pricevalue == plkey_price:
                                vol = plval_vol
                                buypriceary = [v for v in buypriceary if v != plval_vol]
                                break
                            elif pricevalue == plkey_price + 1:
                                vol = min(buypriceary) if buypriceary else 0
                                break
                            else:
                                vol = ""

                            # Ensure pricevalue is not a list
                            if isinstance(pricevalue, list):
                                pricevalue = pricevalue[0] if pricevalue else 0
                            bidblockarray.setdefault(blk_key, {})[pricevalue] = {
                                "RTMPrice": pricevalue,
                                "Volume": vol,
                                "sell_buy_bid_type": bid_type,
                                "biding_date": biding_date.strftime("%Y-%m-%d"),
                            }

                # ---- DETAIL ARRAYS ----
                pDetailarray["reference_number"][timeblock_key] = request.get("reference_number", [""])[0] if request.get("reference_number") else ""
                pDetailarray["bid_status"][timeblock_key] = request.get("bid_status", [""])[0] if request.get("bid_status") else ""
                pDetailarray["approve_by"][timeblock_key] = request.get("approve_by", [""])[0] if request.get("approve_by") else ""

            # ---- STEP 5: SPLIT EXCHANGE-WISE VOLUMES ----
            bidblockarrayExWise = {}
            for Tblk, bidblockarray1 in bidblockarray.items():
                blkno = Tblk.split("_")
                ex_split_key = f"Ex_split_string_{blkno[1]}"

                if ex_split_key not in request:
                    continue

                ex_splits = request[ex_split_key].split("_")
                for ExNo, ex_str in enumerate(ex_splits):
                    ex_parts = ex_str.split("-")
                    try:
                        # Take the last part as percentage (handles formats like IEX-IEX-100 or IEX-100)
                        ex_percent = float(ex_parts[-1]) if len(ex_parts) > 1 else 0
                    except (ValueError, IndexError):
                        # Handle cases where exchange string doesn't contain percentage or is malformed
                        ex_percent = 0

                    for plkey, bidPriceWise in bidblockarray1.items():
                        ex_entry = {
                            "RTMPrice": bidPriceWise["RTMPrice"],
                            "Volume": int(float(bidPriceWise["Volume"]) * ex_percent / 100),
                            "AC_Volume": bidPriceWise["Volume"],
                            "sell_buy_bid_type": bidPriceWise["sell_buy_bid_type"],
                            "biding_date": bidPriceWise["biding_date"],
                        }
                        # Ensure plkey is not a list
                        if isinstance(plkey, list):
                            plkey = plkey[0] if plkey else "0"
                        bidblockarrayExWise.setdefault(ExNo, {}).setdefault(blkno[0], {})[plkey] = ex_entry

            # ---- FINAL RESULT ----
            return {
                "status": "success",
                "bidblockarray": bidblockarray,
                "bidblockarrayExWise": bidblockarrayExWise,
                "finalpricearray": finalpricearray,
                "pDetailarray": pDetailarray
            }

        except Exception as e:
            logger.exception(f"[DayaheadmarketService] Failed to process bid creation: {str(e)}")
            raise

    async def submit_for_approval_post_data(self, request_data: Dict[str, Any], current_user: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit dayahead market bid data for approval.
        
        Args:
            request_data (Dict[str, Any]): Approval request data
        Returns:
            Dict[str, Any]: Success/error status with message
        """
        try:
            # Add default values for missing fields
            if "current_selected_demandforecast" not in request_data:
                request_data["current_selected_demandforecast"] = "default"
            if "current_user" not in request_data:
                request_data["current_user"] = "system"
            if "selected_action_type" not in request_data:
                request_data["selected_action_type"] = "with_cut_off"
            if "rtm_forecast_range" not in request_data:
                request_data["rtm_forecast_range"] = [0] * 100  # Default array of zeros
            
            current_user_id = current_user.get("user_name", current_user.get("user_id", "system"))
            dbarray = {}
            
            # Helper function to get block date - extract from time_block_range if time_block_dates missing
            def get_block_date_data(index):
                if "time_block_dates" in request_data and index < len(request_data["time_block_dates"]):
                    return request_data["time_block_dates"][index].split("_")
                elif index < len(request_data["time_block_range"]):
                    # Extract date from time_block_range format: "23:30-23:45_95_2025-10-27"
                    time_range_parts = request_data["time_block_range"][index].split("_")
                    if len(time_range_parts) >= 3:
                        # Convert from YYYY-MM-DD to DD-MM-YYYY format
                        date_str = time_range_parts[2]  # "2025-10-27"
                        formatted_date = self._convert_date_format(date_str)
                        return [formatted_date]
                return [""]
            
            # Iterate through each time block range
            for time_key, time_block_value in enumerate(request_data["time_block_range"]):
                keys = time_key
                blno = time_block_value.split("_")
                bltimefull = blno[0].split("-")
                dates = get_block_date_data(time_key)

                # Get revision - using generic repository
                revision_pipeline = [
                    {"$match": {"inserted_date": datetime.now().strftime("%Y-%m-%d"), "start_time": bltimefull[0]}},
                    {"$sort": {"revision_no": -1}},
                    {"$limit": 1},
                    {"$project": {"revision_no": 1}}
                ]
                revision_result = await self.repo.aggregate_async(self.dam_single_bid_data_for_approval_collection, revision_pipeline)
                revision_list = list(revision_result)
                getRevision = revision_list[0]["revision_no"] if revision_list else 0
                
                # Fetch product code
                product_pipeline = [
                    {"$match": {"start_time": bltimefull[0]}},
                    {"$project": {"Session_Id": 1}}
                ]
                product_result = await self.repo.aggregate_async(self.dam_product_hardcoded_collection, product_pipeline)
                product_list = list(product_result)
                session_block = product_list[0]["Session_Id"] if product_list else 0
                
                # Build dbarray for this block
                date_obj = datetime.strptime(dates[0], "%d-%m-%Y")  # parse string to datetime
                formatted_date = date_obj.strftime("%Y-%m-%d")
                current_time = datetime.now().strftime("%H:%M")
                dbarray[keys] = {
                    "bid_date": formatted_date,
                    "inserted_date": formatted_date,
                    "schedule_date": formatted_date,
                    "inserted_time": current_time,
                    "time_block": blno[0],
                    "start_time": bltimefull[0],
                    "end_time": bltimefull[1],
                    "block_no": int(blno[1]),
                    "revision_no": int(getRevision) + 1 if getRevision else 1,
                    "bid_type": "single",
                    "session_block": int(session_block),
                    "member_id": "N2UP0UPC0000",
                    "user_id": current_user_id,
                    "portfolio_code": "N2UP0UPC0001",
                    "sheet_market": "INDIA-RTM",
                    "requested_by": request_data.get("current_user", "system"),
                    "requested_time": current_time,
                    "approved_by": "",
                    "approved_time": "",
                    "created_by": "",
                    "created_time": "",
                    "Ex_split_string": request_data.get(f"Ex_split_string_{blno[1]}", ""),
                    "remark": "",
                    "buy_sell_bid": request_data.get("sell_buy_bid_type", [""])[0] if request_data.get("sell_buy_bid_type") else ""
                }
                
                # Handle bid status
                buy_sell_type = request_data.get("sell_buy_bid_type", [""])[0] if request_data.get("sell_buy_bid_type") else ""
                if buy_sell_type == "withdrawBid":
                    dbarray[keys]["bid_status"] = "pending_withdrawal"
                else:
                    dbarray[keys]["bid_status"] = "pending"

                plcounter = 0
                buy_sell_type = request_data.get("sell_buy_bid_type", [""])[0] if request_data.get("sell_buy_bid_type") else ""
                
                if buy_sell_type != "buy":
                    dbarray[keys]["Plants"] = {}
                    
                    for plkey, plvalue in enumerate(request_data.get("bid_plant_name", [])):
                        # Safe retrieval of category
                        bid_categories = request_data.get("bid_category", [])
                        cat = bid_categories[plkey] if plkey < len(bid_categories) else "Unknown"

                        # Ensure blno has at least 2 elements
                        if len(blno) < 2:
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
                        elif plvalue == str(bidPriceNOC()):
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

                        plcounter += 1
                else:
                    # Buy side handling - simplified version for v2
                    # For now, we'll create a basic structure for buy bids
                    dbarray[keys]["Plants"] = {}
                    
                    # Basic buy bid processing
                    for plkey, plvalue in enumerate(request_data.get("bid_plant_name", [])):
                        if len(blno) < 2:
                            continue
                            
                        vol_key = f"bid_volume_{blno[1]}"
                        price_key = f"bid_price_{blno[1]}"
                        
                        bid_vols = request_data.get(vol_key, [])
                        bid_prices = request_data.get(price_key, [])
                        
                        bid_volume = bid_vols[plkey] if plkey < len(bid_vols) else 0
                        bid_price = bid_prices[plkey] if plkey < len(bid_prices) else 0
                        
                        plant_key = f"buy_plant_{plkey}"
                        dbarray[keys]["Plants"][plant_key] = {
                            "bid_plant_name": plvalue,
                            "bid_plant_category": "Buy",
                            "bid_default_volume": 0,
                            "bid_volume": bid_volume,
                            "bid_price": bid_price,
                            "bid_default_price": 0,
                            "rtm_forecast_Price": 0
                        }
            
            # Insert final data into collection using generic repository
            documents_to_insert = []
            for key, data in dbarray.items():
                documents_to_insert.append(data)
            
            if documents_to_insert:
                result = await self.repo.insert_many_async(self.dam_single_bid_data_for_approval_collection, documents_to_insert)
                if result and result.inserted_ids:
                    return {"status": "success", "code": 200, "message": "Successfully Inserted"}
                else:
                    return {"status": "error", "code": 404, "message": "Error: data not found!"}
            else:
                return {"status": "error", "code": 400, "message": "No data to insert"}

        except Exception as e:
            logger.exception(f"[DayaheadmarketService] Failed to submit for approval: {str(e)}")
            return {"status": "error", "code": 500, "message": f"Internal Server Error: {str(e)}"}

    async def calSchedulingTableData(self, schedule_date: Optional[str]) -> Dict[str, Any]:
        """
        Calculate scheduling table data for Day-Ahead Market.
        
        Args:
            schedule_date (Optional[str]): The schedule date in 'YYYY-MM-DD' format.
        Returns:
            Dict[str, Any]: Scheduling calculation data
        """
        try:
            if not schedule_date:
                # For dayahead market, use tomorrow's date
                tomorrow = datetime.now() + timedelta(days=1)
                schedule_date = tomorrow.strftime('%Y-%m-%d')
            
            # Convert schedule_date to datetime object for MongoDB queries
            dt_obj = datetime.strptime(schedule_date, '%Y-%m-%d')
            utc_tz = pytz.utc
            dt_obj = utc_tz.localize(dt_obj.replace(hour=0, minute=0, second=0, microsecond=0))
            
            # Fetch hardcoded session data
            session_pipeline = [
                {"$sort": {"Product_Code": 1}},
                {"$project": {"_id": 0}}
            ]
            session_data = list(await self.repo.aggregate_async(self.dam_product_hardcoded_collection, session_pipeline))
            
            # Fetch plant data for scheduling (SG - Supply Generation)
            supply_pipeline = [
                {"$match": {
                    "scheduled_at": dt_obj,
                    "dc_sg": "SG",
                    "time_block": {"$gte": 1, "$lte": 96}
                }},
                {
                    "$lookup": {
                        "from": self.plant_master_collection,
                        "localField": "plant_id",
                        "foreignField": "plant_id",
                        "as": "plant_details"
                    }
                },
                {
                    "$unwind": "$plant_details"
                },
                {
                    "$project": {
                        "_id": 0,
                        "schedule_date": "$scheduled_at",
                        "time_block": 1,
                        "plant_id": 1,
                        "volume": 1,
                        "plant_name": "$plant_details.plant_name",
                        "dispatch_type": "$plant_details.dispatch_type",
                        "px_price": "$plant_details.px_price",
                        "category": "$plant_details.category"
                    }
                },
                {"$sort": {"time_block": 1, "px_price": 1}}
            ]
            plants_data = list(await self.repo.aggregate_async(self.supply_collection, supply_pipeline))
            
            # Fetch demand forecast data
            demand_pipeline = [
                {"$match": {
                    "scheduled_at": dt_obj,
                    "type": "DAF",
                    "time_block": {"$gte": 1, "$lte": 96}
                }},
                {"$project": {"_id": 0, "time_block": 1, "forecast": 1}},
                {"$sort": {"time_block": 1}}
            ]
            demand_data = list(await self.repo.aggregate_async(self.demand_collection, demand_pipeline))
            
            # Fetch limit values for forecasted price
            latest_limit_dates = list(await self.repo.aggregate_async(self.limit_values_collection, [
                {"$match": {"market": "DAM"}},
                {"$group": {"_id": "$schedule_date"}},
                {"$sort": {"_id": -1}},
                {"$limit": 1}
            ]))
            
            latest_limit_date = latest_limit_dates[0]["_id"] if latest_limit_dates else schedule_date
            
            forecasted_price_pipeline = [
                {"$match": {
                    "schedule_date": latest_limit_date,
                    "ID": "DAM_forecasted_price",
                    "market": "DAM",
                    "block": {"$gte": 1, "$lte": 96}
                }},
                {"$project": {"_id": 0, "block": 1, "values": 1}},
                {"$sort": {"block": 1}}
            ]
            forecasted_prices = list(await self.repo.aggregate_async(self.limit_values_collection, forecasted_price_pipeline))
            
            # Process scheduling calculations
            scheduling_data = []
            
            # Group plants by time block
            plants_by_block = {}
            for plant in plants_data:
                block = plant["time_block"]
                if block not in plants_by_block:
                    plants_by_block[block] = []
                plants_by_block[block].append(plant)
            
            # Group demand by time block
            demand_by_block = {d["time_block"]: d["forecast"] for d in demand_data}
            
            # Group forecasted prices by block
            price_by_block = {p["block"]: p["values"] for p in forecasted_prices}
            
            # Calculate scheduling for each time block
            for block in range(1, 97):  # 96 blocks
                block_plants = plants_by_block.get(block, [])
                block_demand = demand_by_block.get(block, 0)
                block_price = price_by_block.get(block, 0)
                
                # Calculate total supply for this block
                total_supply = sum(plant.get("volume", 0) for plant in block_plants)
                
                # Calculate surplus/deficit
                surplus_deficit = total_supply - block_demand
                
                # Basic scheduling calculation
                scheduled_plants = []
                remaining_demand = block_demand
                
                # Sort plants by merit order (price ascending for merit plants)
                merit_plants = [p for p in block_plants if p.get("dispatch_type") == "Merit"]
                must_plants = [p for p in block_plants if p.get("dispatch_type") != "Merit"]
                
                merit_plants.sort(key=lambda x: x.get("px_price", 0))
                
                # Schedule must run plants first
                for plant in must_plants:
                    scheduled_volume = min(plant.get("volume", 0), remaining_demand)
                    scheduled_plants.append({
                        "plant_id": plant["plant_id"],
                        "plant_name": plant["plant_name"],
                        "scheduled_volume": scheduled_volume,
                        "available_volume": plant.get("volume", 0),
                        "px_price": plant.get("px_price", 0),
                        "dispatch_type": plant.get("dispatch_type", "Must"),
                        "category": plant.get("category", "")
                    })
                    remaining_demand = max(0, remaining_demand - scheduled_volume)
                
                # Schedule merit plants based on remaining demand
                for plant in merit_plants:
                    if remaining_demand <= 0:
                        scheduled_volume = 0
                    else:
                        scheduled_volume = min(plant.get("volume", 0), remaining_demand)
                    
                    scheduled_plants.append({
                        "plant_id": plant["plant_id"],
                        "plant_name": plant["plant_name"],
                        "scheduled_volume": scheduled_volume,
                        "available_volume": plant.get("volume", 0),
                        "px_price": plant.get("px_price", 0),
                        "dispatch_type": plant.get("dispatch_type", "Merit"),
                        "category": plant.get("category", "")
                    })
                    remaining_demand = max(0, remaining_demand - scheduled_volume)
                
                # Get session info for this block
                session_info = session_data[block - 1] if block - 1 < len(session_data) else {}
                
                block_data = {
                    "time_block": block,
                    "session_id": session_info.get("Session_Id", block),
                    "start_time": self.getFifteenTimeBlockID(block - 1),
                    "demand_forecast": block_demand,
                    "total_supply": total_supply,
                    "surplus_deficit": surplus_deficit,
                    "forecasted_price": block_price,
                    "scheduled_plants": scheduled_plants,
                    "total_scheduled": sum(p["scheduled_volume"] for p in scheduled_plants),
                    "unmet_demand": remaining_demand
                }
                
                scheduling_data.append(block_data)
            
            return {
                "status": "success",
                "schedule_date": schedule_date,
                "total_blocks": 96,
                "scheduling_data": scheduling_data,
                "summary": {
                    "total_demand": sum(demand_by_block.values()),
                    "total_supply": sum(sum(plant.get("volume", 0) for plant in plants_by_block.get(block, [])) for block in range(1, 97)),
                    "total_surplus_deficit": sum(block_data["surplus_deficit"] for block_data in scheduling_data)
                }
            }
            
        except Exception as e:
            logger.exception(f"[DayaheadmarketService] Failed to calculate scheduling table data: {str(e)}")
            return {"status": "error", "message": f"Internal Server Error: {str(e)}"}

    async def submitForPreExchangeData(self, data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Submit dayahead pre-exchange data for processing.
        
        Args:
            data (List[Dict[str, Any]]): List of reference data for pre-exchange processing
        Returns:
            Dict[str, Any]: Processing result with status and data
        """
        try:
            data_array = []  # list to hold each reference dictionary

            for reference in data:
                # Handle both dict and object types
                if hasattr(reference, 'reference_number'):
                    # Pydantic model object
                    ref_dict = {
                        "reference_number": reference.reference_number,
                        "bid_status": reference.bid_status,
                        "Remarks": reference.Remarks,
                        "action_by": reference.action_by,
                        "block_no": reference.block_no
                    }
                else:
                    # Dictionary object
                    ref_dict = {
                        "reference_number": reference.get("reference_number", ""),
                        "bid_status": reference.get("bid_status", ""),
                        "Remarks": reference.get("Remarks", ""),
                        "action_by": reference.get("action_by", ""),
                        "block_no": reference.get("block_no", 0)
                    }
                data_array.append(ref_dict)  # add to the list

            return {"result": True, "data": data_array}

        except Exception as e:
            logger.exception(f"[DayaheadmarketService] Failed to process pre-exchange data: {str(e)}")
            raise

# Global instance
dayaheadmarket_service = DayaheadmarketService()
