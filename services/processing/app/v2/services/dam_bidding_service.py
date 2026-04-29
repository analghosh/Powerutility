"""
DAM (Day Ahead Market) bidding service layer using repository pattern.
Handles business logic for DAM bidding-related data operations with full RTM complexity.
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from math import isnan
from collections import OrderedDict

from ..repository.dam_single_bid_data_for_approval_repository import DAMSingleBidDataForApprovalRepository
from ..repository.dam_product_hardcoded_data_repository import DAMProductHardCodedDataRepository
from ..repository.supply_repository import SupplyRepository
from ..repository.demand_forecast_repository import DemandForecastRepository
from ..repository.market_repository import MarketRepository
from ..repository.master_plants_repository import MasterPlantsRepository
from ..repository.limit_values_repository import LimitValuesRepository

logger = logging.getLogger(__name__)

def bidPriceNOC():
    """Equivalent of PHP's bidPriceNOC() constant."""
    return 10000

class DAMBiddingService:
    """
    Service class responsible for DAM bidding business logic with full RTM complexity.
    """

    def __init__(self):
        self.supply_repo = SupplyRepository()
        self.single_bid_data_for_approval_repo = DAMSingleBidDataForApprovalRepository()
        self.dam_product_hardCoded_data_repo = DAMProductHardCodedDataRepository()
        self.plants_repo = MasterPlantsRepository()
        self.limit_values_repo = LimitValuesRepository()
        self.demand_repo = DemandForecastRepository()
        self.exchange_repo = MarketRepository()

        # For DAM, we use all 96 time blocks but keep the complex RTM logic structure
        # Assume getSessionfrom() returns a list of session time strings like ["00:00", "00:15", ...]
        session_times = DAMBiddingService.get_session_from()  

        # Get current time as string
        current_time = datetime.now().strftime("%H:%M")

        # Count the number of sessions that have passed
        passed_sessions_count = sum(
            1 for t in session_times if datetime.strptime(t, "%H:%M") < datetime.strptime(current_time, "%H:%M")
        )

    def _safe_int_conversion(self, value: Any, default: int = 0) -> int:
        """
        Safely convert a value to integer with fallback to default.
        
        Args:
            value: The value to convert
            default: Default value if conversion fails
            
        Returns:
            int: Converted integer or default value
        """
        if value is None:
            return default
        
        if isinstance(value, int):
            return value
            
        if isinstance(value, str):
            if value.strip() == "" or value.lower() == "string":
                return default
            try:
                return int(value)
            except ValueError:
                logger.warning(f"Cannot convert '{value}' to int, using default {default}")
                return default
                
        try:
            return int(value)
        except (ValueError, TypeError):
            logger.warning(f"Cannot convert '{value}' to int, using default {default}")
            return default
        
        # For DAM, we show all 96 blocks, but keep the complex calculation logic
        # Calculate today's current block (but use all blocks for DAM)
        today_current_block = (2 * passed_sessions_count) - 1 + 95
        print(f"session_times {current_time}")
        
        # Set class attributes (keeping RTM complexity but adapting for DAM)
        self.currentBlk = 96  # Show all blocks for DAM (instead of today_current_block)
        self.actualCurrentBlk = today_current_block  # Keep the actual calculation for reference

        self.shiftingBlockNo = 2
        self.shiftingBlockNoRuningTo = 5
        self.shiftingBlockNoREQFrom = 5
        self.shiftingBlockNoREQTo = 18

        # For DAM, these calculations are kept but we use all blocks
        self.BlkNoForRunSchedule = self.currentBlk + self.shiftingBlockNo
        self.BlkNoToRunSchedule = self.currentBlk + self.shiftingBlockNoRuningTo

        self.BlkNoFromRunReq = self.currentBlk + self.shiftingBlockNoREQFrom
        self.BlkNoToRunReq = self.currentBlk + self.shiftingBlockNoREQTo

    @staticmethod
    def get_session_from():
        """Get session times for DAM calculations - all 96 blocks."""
        return ["00:00", "00:15", "00:30", "00:45", "01:00", "01:15", "01:30", "01:45",
                "02:00", "02:15", "02:30", "02:45", "03:00", "03:15", "03:30", "03:45",
                "04:00", "04:15", "04:30", "04:45", "05:00", "05:15", "05:30", "05:45",
                "06:00", "06:15", "06:30", "06:45", "07:00", "07:15", "07:30", "07:45",
                "08:00", "08:15", "08:30", "08:45", "09:00", "09:15", "09:30", "09:45",
                "10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30", "11:45",
                "12:00", "12:15", "12:30", "12:45", "13:00", "13:15", "13:30", "13:45",
                "14:00", "14:15", "14:30", "14:45", "15:00", "15:15", "15:30", "15:45",
                "16:00", "16:15", "16:30", "16:45", "17:00", "17:15", "17:30", "17:45",
                "18:00", "18:15", "18:30", "18:45", "19:00", "19:15", "19:30", "19:45",
                "20:00", "20:15", "20:30", "20:45", "21:00", "21:15", "21:30", "21:45",
                "22:00", "22:15", "22:30", "22:45", "23:00", "23:15", "23:30", "23:45"]

    async def dam_single_bid_graph(self, schedule_date, from_block, to_block):
        """
        Fetch DAM single bid graph data for a given schedule date with full RTM complexity.

        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format. Defaults to tomorrow's date.
            from_block (int): Starting time block.
            to_block (int): Ending time block.
        Returns:
            Optional[Dict[str, Any]]: A dictionary containing the DAM data,
                                    or None if no data is found.
        """
        try:
            if schedule_date:
                schedule_date = schedule_date
            else:
                # For DAM, default to tomorrow's date
                tomorrow = datetime.now() + timedelta(days=1)
                schedule_date = tomorrow.strftime('%Y-%m-%d')
            
            # Complex pipeline similar to RTM but adapted for DAM
            pipeline = [
                {
                    "$match": {
                        "scheduled_at": schedule_date,
                        "market_type": "DAM",
                        "time_block": {"$gte": from_block, "$lte": to_block}
                    }
                },
                {
                    "$lookup": {
                        "from": "supply_serve",
                        "localField": "plant_id",
                        "foreignField": "plant_id",
                        "as": "supply_data"
                    }
                },
                {
                    "$lookup": {
                        "from": "exchange_serve",
                        "let": {"sched": schedule_date, "tb": "$time_block"},
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": ["$scheduled_at", "$$sched"]},
                                            {"$eq": ["$time_block", "$$tb"]},
                                            {"$eq": ["$market_type", "DAM"]},
                                            {"$eq": ["$px_name", "IEX"]}
                                        ]
                                    }
                                }
                            },
                            {"$project": {"_id": 0, "mcp": 1, "acp": 1}}
                        ],
                        "as": "exchange_data"
                    }
                },
                {
                    "$lookup": {
                        "from": "demand_serve",
                        "let": {"sched": schedule_date, "tb": "$time_block"},
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": ["$scheduled_at", "$$sched"]},
                                            {"$eq": ["$time_block", "$$tb"]},
                                            {"$eq": ["$type", "DAF"]}
                                        ]
                                    }
                                }
                            },
                            {"$project": {"_id": 0, "forecast": 1, "actual": 1}}
                        ],
                        "as": "demand_data"
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "time_block": "$time_block",
                            "plant_id": "$plant_id"
                        },
                        "bid_data": {"$first": "$$ROOT"},
                        "supply_volume": {"$first": {"$arrayElemAt": ["$supply_data.volume", 0]}},
                        "exchange_mcp": {"$first": {"$arrayElemAt": ["$exchange_data.mcp", 0]}},
                        "demand_forecast": {"$first": {"$arrayElemAt": ["$demand_data.forecast", 0]}},
                        "bid_volume": {"$sum": "$bid_volume"},
                        "bid_price": {"$avg": "$bid_price"},
                        "plant_count": {"$sum": 1}
                    }
                },
                {
                    "$group": {
                        "_id": "$_id.time_block",
                        "plants": {
                            "$push": {
                                "plant_id": "$_id.plant_id",
                                "bid_volume": "$bid_volume",
                                "bid_price": "$bid_price",
                                "supply_volume": "$supply_volume"
                            }
                        },
                        "total_volume": {"$sum": "$bid_volume"},
                        "avg_price": {"$avg": "$bid_price"},
                        "plant_count": {"$sum": "$plant_count"},
                        "exchange_mcp": {"$first": "$exchange_mcp"},
                        "demand_forecast": {"$first": "$demand_forecast"}
                    }
                },
                {
                    "$sort": {"_id": 1}
                },
                {
                    "$project": {
                        "_id": 0,
                        "time_block": "$_id",
                        "total_volume": 1,
                        "avg_price": 1,
                        "plant_count": 1,
                        "plants": 1,
                        "exchange_mcp": 1,
                        "demand_forecast": 1
                    }
                }
            ]
            
            result = await self.single_bid_data_for_approval_repo.aggregate_async(
                self.single_bid_data_for_approval_repo.collection_name, 
                pipeline
            )
            
            logger.info(f"DAM single bid graph data fetched successfully: {len(result)} records")
            return {"data": result, "schedule_date": schedule_date}
            
        except Exception as e:
            logger.error(f"Error fetching DAM single bid graph data: {str(e)}")
            raise Exception(f"Failed to fetch DAM single bid graph data: {str(e)}")

    async def dam_single_bid_table(self, schedule_date, from_block, to_block):
        """
        Fetch DAM single bid table data with full RTM complexity.

        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format. Defaults to tomorrow's date.
            from_block (int): Starting time block.
            to_block (int): Ending time block.
        Returns:
            Optional[Dict[str, Any]]: A dictionary containing the DAM table data,
                                    or None if no data is found.
        """
        try:
            if schedule_date:
                schedule_date = schedule_date
            else:
                # For DAM, default to tomorrow's date
                tomorrow = datetime.now() + timedelta(days=1)
                schedule_date = tomorrow.strftime('%Y-%m-%d')
            
            logger.info(f"Fetching DAM single bid table data for date: {schedule_date}, blocks: {from_block}-{to_block}")
            
            # Complex demand forecast data retrieval similar to RTM
            from datetime import date
            target_date = datetime.strptime(schedule_date, '%Y-%m-%d').date()
            demand_forecast = await self.demand_repo.get_demand_forecast_data_pipeline(
                collection_name="demand_serve",
                target_date=target_date,
                market_type="DAF"
            )
            
            # Complex exchange data retrieval with full RTM logic - placeholder for now
            exchange_data = {
                "data": [],
                "message": "DAM exchange data placeholder",
                "schedule_date": schedule_date,
                "market_type": "DAM",
                "blocks": 96
            }
            
            # Complex limit values retrieval similar to RTM
            # User Requirement: DAM whole time use tomorrow date for limit values
            limit_schedule_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
            
            # Get various limit values with complex logic
            buffer_price = await self.limit_values_repo.get_limit_values_by_filters(
                schedule_date=limit_schedule_date,
                limit_id="Buffer_Price",
                market="DAM",
                category="Limits",
                from_block=from_block,
                to_block=to_block
            )
            
            ceiling_price = await self.limit_values_repo.get_limit_values_by_filters(
                schedule_date=limit_schedule_date,
                limit_id="Ceiling_Price",
                market="DAM",
                category="Limits",
                from_block=from_block,
                to_block=to_block
            )
            
            # Complex plants data retrieval
            plants_data = await self.plants_repo.get_all_plants()
            
            # Complex DAM single bid approval data with full RTM pipeline logic
            bid_data = await self.single_bid_data_for_approval_repo.aggregate([
                {
                    "$match": {
                        "scheduled_at": schedule_date,
                        "market_type": "DAM",
                        "time_block": {"$gte": from_block, "$lte": to_block}
                    }
                },
                {
                    "$lookup": {
                        "from": "supply_serve",
                        "localField": "plant_id",
                        "foreignField": "plant_id",
                        "as": "supply_info"
                    }
                },
                {
                    "$lookup": {
                        "from": "plant_master",
                        "localField": "plant_id",
                        "foreignField": "plant_id",
                        "as": "plant_info"
                    }
                },
                {
                    "$addFields": {
                        "supply_volume": {"$arrayElemAt": ["$supply_info.volume", 0]},
                        "plant_name": {"$arrayElemAt": ["$plant_info.plant_name", 0]},
                        "plant_category": {"$arrayElemAt": ["$plant_info.category", 0]},
                        "px_price": {"$arrayElemAt": ["$plant_info.px_price", 0]},
                        "vc_price": {"$arrayElemAt": ["$plant_info.vc_price", 0]},
                        "multiplier": {"$arrayElemAt": ["$plant_info.multiplier", 0]},
                        "technology": {"$arrayElemAt": ["$plant_info.technology", 0]},
                        "dispatch_type": {"$arrayElemAt": ["$plant_info.dispatch_type", 0]},
                        "price_group": {"$arrayElemAt": ["$plant_info.price_group", 0]},
                        "ppa": {"$arrayElemAt": ["$plant_info.ppa", 0]}
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "time_block": 1,
                        "plant_id": 1,
                        "plant_name": 1,
                        "plant_category": 1,
                        "bid_volume": 1,
                        "bid_price": 1,
                        "supply_volume": 1,
                        "px_price": 1,
                        "vc_price": 1,
                        "multiplier": 1,
                        "technology": 1,
                        "dispatch_type": 1,
                        "price_group": 1,
                        "ppa": 1,
                        "bid_status": 1,
                        "created_at": 1,
                        "updated_at": 1
                    }
                },
                {
                    "$sort": {"time_block": 1, "plant_id": 1}
                }
            ])
            
            logger.info(f"DAM single bid table data fetched successfully")
            return {
                "data": {
                    "demand_forecast": demand_forecast,
                    "exchange_data": exchange_data,
                    "buffer_price": buffer_price,
                    "ceiling_price": ceiling_price,
                    "plants_data": plants_data,
                    "bid_data": bid_data
                },
                "schedule_date": schedule_date,
                "from_block": from_block,
                "to_block": to_block
            }
            
        except Exception as e:
            logger.error(f"Error fetching DAM single bid table data: {str(e)}")
            raise Exception(f"Failed to fetch DAM single bid table data: {str(e)}")

    async def approval_request_list(self, schedule_date, from_block, to_block):
        """
        Fetch DAM approval request list with full RTM complexity.

        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format. Defaults to tomorrow's date.
            from_block (int): Starting time block.
            to_block (int): Ending time block.
        Returns:
            Optional[Dict[str, Any]]: A dictionary containing the DAM approval request list,
                                    or None if no data is found.
        """
        try:
            if schedule_date:
                schedule_date = schedule_date
            else:
                # For DAM, default to tomorrow's date
                tomorrow = datetime.now() + timedelta(days=1)
                schedule_date = tomorrow.strftime('%Y-%m-%d')
            
            logger.info(f"Fetching DAM approval request list for date: {schedule_date}, blocks: {from_block}-{to_block}")
            
            # Complex approval request pipeline similar to RTM
            pipeline = [
                {
                    "$match": {
                        "scheduled_at": schedule_date,
                        "market_type": "DAM",
                        "bid_status": {"$in": ["pending", "submitted"]},
                        "time_block": {"$gte": from_block, "$lte": to_block}
                    }
                },
                {
                    "$lookup": {
                        "from": "supply_serve",
                        "localField": "plant_id",
                        "foreignField": "plant_id",
                        "as": "supply_info"
                    }
                },
                {
                    "$lookup": {
                        "from": "plant_master",
                        "localField": "plant_id",
                        "foreignField": "plant_id",
                        "as": "plant_info"
                    }
                },
                {
                    "$addFields": {
                        "supply_volume": {"$arrayElemAt": ["$supply_info.volume", 0]},
                        "plant_name": {"$arrayElemAt": ["$plant_info.plant_name", 0]},
                        "plant_category": {"$arrayElemAt": ["$plant_info.category", 0]},
                        "px_price": {"$arrayElemAt": ["$plant_info.px_price", 0]},
                        "vc_price": {"$arrayElemAt": ["$plant_info.vc_price", 0]},
                        "multiplier": {"$arrayElemAt": ["$plant_info.multiplier", 0]},
                        "technology": {"$arrayElemAt": ["$plant_info.technology", 0]},
                        "dispatch_type": {"$arrayElemAt": ["$plant_info.dispatch_type", 0]},
                        "price_group": {"$arrayElemAt": ["$plant_info.price_group", 0]},
                        "ppa": {"$arrayElemAt": ["$plant_info.ppa", 0]}
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "time_block": "$time_block",
                            "plant_name": "$plant_name"
                        },
                        "bid_data": {"$first": "$$ROOT"},
                        "total_bid_volume": {"$sum": "$bid_volume"},
                        "avg_bid_price": {"$avg": "$bid_price"},
                        "supply_volume": {"$first": "$supply_volume"},
                        "px_price": {"$first": "$px_price"},
                        "vc_price": {"$first": "$vc_price"},
                        "multiplier": {"$first": "$multiplier"},
                        "technology": {"$first": "$technology"},
                        "dispatch_type": {"$first": "$dispatch_type"},
                        "price_group": {"$first": "$price_group"},
                        "ppa": {"$first": "$ppa"}
                    }
                },
                {
                    "$sort": {"_id.time_block": 1, "_id.plant_name": 1}
                },
                {
                    "$group": {
                        "_id": "$_id.time_block",
                        "plants": {
                            "$push": {
                                "plant_name": "$_id.plant_name",
                                "bid_volume": "$total_bid_volume",
                                "bid_price": "$avg_bid_price",
                                "supply_volume": "$supply_volume",
                                "px_price": "$px_price",
                                "vc_price": "$vc_price",
                                "multiplier": "$multiplier",
                                "technology": "$technology",
                                "dispatch_type": "$dispatch_type",
                                "price_group": "$price_group",
                                "ppa": "$ppa",
                                "bid_data": "$bid_data"
                            }
                        },
                        "total_volume": {"$sum": "$total_bid_volume"},
                        "avg_price": {"$avg": "$avg_bid_price"},
                        "plant_count": {"$sum": 1}
                    }
                },
                {
                    "$sort": {"_id": 1}
                }
            ]
            
            result = await self.single_bid_data_for_approval_repo.aggregate_async(
                self.single_bid_data_for_approval_repo.collection_name, 
                pipeline
            )
            
            logger.info(f"DAM approval request list fetched successfully: {len(result)} requests")
            return {"data": result, "schedule_date": schedule_date}
            
        except Exception as e:
            logger.error(f"Error fetching DAM approval request list: {str(e)}")
            raise Exception(f"Failed to fetch DAM approval request list: {str(e)}")

    async def bid_creation_post(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create DAM bid data with full RTM complexity.

        Args:
            post_data (Dict[str, Any]): Bid creation data
            
        Returns:
            Dict[str, Any]: Creation result
        """
        try:
            logger.info(f"Creating DAM bid data: {post_data}")
            
            # Complex bid creation logic similar to RTM
            documents_to_insert = []
            
            # Extract bid data from post_data
            bid_date = post_data.get("bidingDate", "")
            sell_buy_bid_type = post_data.get("sell_buy_bid_type", [])
            time_block_range = post_data.get("time_block_range", [])
            
            # Get session data for complex calculations
            getsessionNo = await self.dam_product_hardCoded_data_repo.get_data()
            getPlantList = await self.plants_repo.get_all_plants()
            
            # Complex limit values retrieval
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
            
            allTMValues = await self.limit_values_repo.get_limit_values_by_filters(
                schedule_date=yesterday,
                end_date=tomorrow,
                limit_id="TM",
                market="DAM",
                category="Limits"
            )
            
            allSEZValues = await self.limit_values_repo.get_limit_values_by_filters(
                schedule_date=yesterday,
                end_date=tomorrow,
                limit_id="SEZ",
                market="DAM",
                category="Limits"
            )
            
            allMLTPValues = await self.limit_values_repo.get_limit_values_by_filters(
                schedule_date=yesterday,
                end_date=tomorrow,
                limit_id="Multiplier",
                market="DAM",
                category="Limits"
            )
            
            for i, block_range in enumerate(time_block_range):
                if i < len(sell_buy_bid_type):
                    bid_type = sell_buy_bid_type[i]
                    
                    # Extract block number from time_block_range format
                    block_parts = block_range.split("_")
                    if len(block_parts) >= 2:
                        block_no = int(block_parts[1])
                        
                        # Create complex document for DAM bid similar to RTM
                        doc = {
                            "scheduled_at": datetime.strptime(bid_date, '%Y-%m-%d'),
                            "market_type": "DAM",
                            "time_block": block_no,
                            "bid_type": bid_type,
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
            
            # Insert documents
            if documents_to_insert:
                result = await self.single_bid_data_for_approval_repo.insert_many(documents_to_insert)
                logger.info(f"DAM bid creation successful: {len(documents_to_insert)} records inserted")
                return {"data": True, "inserted_count": len(documents_to_insert)}
            else:
                logger.warning("No DAM bid documents to insert")
                return {"data": False, "message": "No valid bid data provided"}
                
        except Exception as e:
            logger.error(f"Error creating DAM bid data: {str(e)}")
            raise Exception(f"Failed to create DAM bid data: {str(e)}")

    async def submit_for_approval_post_data(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit DAM bid data for approval with full RTM complexity.

        Args:
            request_data (Dict[str, Any]): Approval request data
            
        Returns:
            Dict[str, Any]: Submission result
        """
        try:
            logger.info(f"Submitting DAM bid data for approval: {request_data}")
            
            current_user = request_data.get("current_user", "")
            data = request_data.get("data", [])
            
            documents_to_insert = []
            
            # Complex approval submission logic similar to RTM
            for block_data in data:
                block_no = block_data.get("block_no", 0)
                bid_date = block_data.get("bid_date", "")
                buy_sell_bid = block_data.get("buy_sell_bid", "")
                bid_plant_names = block_data.get("bid_plant_name", [])
                
                for plant_data in bid_plant_names:
                    # Create complex document similar to RTM
                    # Handle date parsing with validation
                    try:
                        if bid_date and bid_date != "string" and bid_date.strip():
                            scheduled_at = datetime.strptime(bid_date, '%Y-%m-%d')
                        else:
                            scheduled_at = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                    except ValueError:
                        logger.warning(f"Invalid date format '{bid_date}', using current date")
                        scheduled_at = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                    
                    doc = {
                        "scheduled_at": scheduled_at,
                        "market_type": "DAM",
                        "time_block": self._safe_int_conversion(block_no, 0),
                        "bid_type": buy_sell_bid,
                        "plant_name": plant_data.get("bid_plant_name", ""),
                        "plant_key": plant_data.get("bid_plant_key", ""),
                        "plant_category": plant_data.get("bid_plant_category", ""),
                        "bid_volume": plant_data.get("bid_volume", 0),
                        "bid_price": plant_data.get("bid_price", 0),
                        "bid_default_volume": plant_data.get("bid_default_volume", 0),
                        "bid_default_price": plant_data.get("bid_default_price", 0),
                        "dam_forecast_price": plant_data.get("dam_forecast_price", 0),
                        "current_user": current_user,
                        "status": "submitted",
                        "created_at": datetime.now(),
                        "submitted_at": datetime.now(),
                        "revision_no": block_data.get("revision_no", 1),
                        "session_block": block_data.get("session_block", 1),
                        "exchange_split": block_data.get("Ex_split_string", ""),
                        "selected_action_type": block_data.get("selected_action_type", "")
                    }
                    documents_to_insert.append(doc)
            
            # Insert documents
            if documents_to_insert:
                result = await self.single_bid_data_for_approval_repo.insert_many(documents_to_insert)
                logger.info(f"DAM bid approval submission successful: {len(documents_to_insert)} records")
                return {"data": True, "submitted_count": len(documents_to_insert)}
            else:
                logger.warning("No DAM bid documents to submit for approval")
                return {"data": False, "message": "No valid bid data to submit"}
                
        except Exception as e:
            logger.error(f"Error submitting DAM bid data for approval: {str(e)}")
            raise Exception(f"Failed to submit DAM bid data for approval: {str(e)}")

    async def submit_for_pre_exchange_data(self, data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Submit DAM bid data for pre-exchange processing with full RTM complexity.

        Args:
            data (List[Dict[str, Any]]): Pre-exchange data
            
        Returns:
            Dict[str, Any]: Processing result
        """
        try:
            logger.info(f"Submitting DAM bid data for pre-exchange processing: {len(data)} records")
            
            updated_count = 0
            
            # Complex pre-exchange processing logic similar to RTM
            for item in data:
                reference_number = item.get("reference_number", "")
                bid_status = item.get("bid_status", "")
                remarks = item.get("Remarks", "")
                action_by = item.get("action_by", "")
                block_no = item.get("block_no", "")
                
                # Complex update query similar to RTM
                filter_query = {
                    "market_type": "DAM",
                    "time_block": self._safe_int_conversion(block_no, 0),
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
            
            logger.info(f"DAM pre-exchange processing completed: {updated_count} records updated")
            return {"data": True, "updated_count": updated_count}
            
        except Exception as e:
            logger.error(f"Error submitting DAM bid data for pre-exchange processing: {str(e)}")
            raise Exception(f"Failed to submit DAM bid data for pre-exchange processing: {str(e)}")

    async def cal_scheduling_table_data(self, schedule_date: Optional[str]) -> Dict[str, Any]:
        """
        Calculate DAM scheduling table data with full RTM complexity.

        Args:
            schedule_date (str): The schedule date in 'YYYY-MM-DD' format. Defaults to tomorrow's date.
            
        Returns:
            Dict[str, Any]: DAM scheduling calculation data
        """
        try:
            if schedule_date:
                schedule_date = schedule_date
            else:
                # For DAM, default to tomorrow's date
                tomorrow = datetime.now() + timedelta(days=1)
                schedule_date = tomorrow.strftime('%Y-%m-%d')
            
            logger.info(f"Calculating DAM scheduling table data for date: {schedule_date}")
            
            # Complex scheduling calculation similar to RTM
            # Use available supply repository methods
            scheduling_data = await self.supply_repo.get_supply_by_category_graph(schedule_date)
            
            # Additional complex calculations similar to RTM
            # Use available demand repository methods - placeholder for now
            demand_forecast = {
                "data": [],
                "message": "DAM demand forecast calculation placeholder",
                "schedule_date": schedule_date
            }
            
            # Use available exchange repository methods - placeholder for now
            exchange_data = {
                "data": [],
                "message": "DAM exchange data calculation placeholder", 
                "schedule_date": schedule_date,
                "market_type": "DAM"
            }
            
            logger.info(f"DAM scheduling table data calculated successfully")
            return {
                "data": {
                    "scheduling_data": scheduling_data,
                    "demand_forecast": demand_forecast,
                    "exchange_data": exchange_data,
                    "calculation_metadata": {
                        "schedule_date": schedule_date,
                        "calculated_at": datetime.now().isoformat(),
                        "blocks_processed": 96,
                        "market_type": "DAM"
                    }
                },
                "schedule_date": schedule_date
            }
            
        except Exception as e:
            logger.error(f"Error calculating DAM scheduling table data: {str(e)}")
            raise Exception(f"Failed to calculate DAM scheduling table data: {str(e)}")

    async def dam_scheduling_cal_table(self, schedule_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Calculate DAM scheduling table data - wrapper method for controller compatibility.
        
        Args:
            schedule_date (str, optional): The schedule date in 'YYYY-MM-DD' format
            
        Returns:
            Dict[str, Any]: Scheduling calculation results
        """
        try:
            logger.info(f"DAM scheduling calculation initiated for date: {schedule_date}")
            return await self.cal_scheduling_table_data(schedule_date)
        except Exception as e:
            logger.error(f"Error in DAM scheduling calculation: {str(e)}")
            raise Exception(f"Failed to calculate DAM scheduling data: {str(e)}")

# Global service instance
dam_bidding_service = DAMBiddingService()