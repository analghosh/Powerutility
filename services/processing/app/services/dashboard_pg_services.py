import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Union,Tuple
import pandas as pd
import numpy as np


from fastapi import HTTPException, Request
from ..repository.dashboard_repository import DashboardRepository
from ..repository.serve_data_historical_repository import HistoricalRepository

logger = logging.getLogger(__name__)

# ------------------- Utility Functions ------------------- #

# Current IST timezone
def ist_time():
    # Current UTC time
    utc_now = datetime.now(timezone.utc)

    # Convert to IST
    ist_offset = timedelta(hours=5, minutes=30)
    current_time = utc_now + ist_offset
    return current_time



def get_fifteen_time_block(time_str: str) -> int:
    hour, minute = map(int, time_str.split(":"))
    return hour * 4 + (minute // 15) + 1




def get_fifteen_full_time_block_id(block_id: int) -> str:
    if block_id < 1 or block_id > 96:
        return "Invalid block"

    start_time = (datetime.strptime("00:00", "%H:%M") + timedelta(minutes=(block_id - 1) * 15)).time()
    end_time = (datetime.strptime("00:00", "%H:%M") + timedelta(minutes=block_id * 15)).time()
    
    # Handle midnight wrap-around
    end_time_str = "00:00" if block_id == 96 else end_time.strftime("%H:%M")

    return f"{start_time.strftime('%H:%M')}-{end_time_str}"


def get_current_block_number() -> int:
    now = ist_time()        # Current Time
    total_minutes = now.hour * 60 + now.minute
    return min((total_minutes // 15) + 1, 96)

def get_last_24_blocks( current_block_id: int) -> List[Dict[str, Union[int, str]]]:
    result = []
    for i in range(23, -1, -1):  # 23 before, then current
        block_id = ((current_block_id - i - 1) % 96) + 1
        result.append({
            "block_id": block_id,
            "full_time": get_fifteen_full_time_block_id(block_id)
        })
    return result

def get_surrounding_block_ranges(current_block_id: int, window: int = 12) -> List[Dict[str, Union[int, str]]]:
    start = max(current_block_id - window, 1)
    end = min(current_block_id + window - 1, 96)

    result = []
    for block_id in range(start, end + 1):
        result.append({
            "block_id": block_id,
            "full_time": get_fifteen_full_time_block_id(block_id)
        })
    return result




class DashboardPGService:
    def __init__(self, collection_name: str = "dashboard_PG"):
        self.dashboard_repo = DashboardRepository(collection_name=collection_name)
        self.demand_PG_repo = DashboardRepository(collection_name="demand_PG")
        self.historical_repo = HistoricalRepository()
        self.master_plant =  DashboardRepository(collection_name="master_plant_list_api")
        self.iex_acp_repo = DashboardRepository(collection_name="iex_areaprice_acp")

    

                                                   
    async def get_dashboard_demand(self, schedule_date: str, current_block_id: int, block_ranges, block_ids) -> Dict[str, Any]:
        current_doc = await self.dashboard_repo.get_current_block(schedule_date, current_block_id)
        if not current_doc:
            raise HTTPException(status_code=404, detail="Current demand block not found")

        data = await self.dashboard_repo.get_blocks_by_ids(schedule_date, block_ids)

        time_block_map = {doc["time_block"]: doc for doc in data}
        chart_data = []

        for block in block_ranges:
            bid = block["block_id"]
            full_time = block["full_time"]
            val = float(time_block_map.get(bid, {}).get("MP_Demand", 0)) if bid else 0
            chart_data.append({"time_block": full_time, "demand": val})

        return {
            "data": {
                "demand": {
                    "current_block": get_fifteen_full_time_block_id(current_block_id),
                    "current_demand": float(current_doc.get("MP_Demand", 0)),
                    "data": chart_data
                }
            }
        }

    async def get_dashboard_frequency(self, schedule_date: str, current_block_id: int) -> Dict[str, Any]:
        # Current block data
        current_doc = await self.dashboard_repo.find_one({
            "schedule_date": schedule_date,
            "time_block": current_block_id,
            "data_type": "demand"
        })

        # rounding Off Frequency value
        current_frequency = round(float(current_doc.get("Raw_Frequency", 0)), 2) if current_doc else 0

        # Ge Previous 23 + current block = total 24
        block_ranges = get_last_24_blocks(current_block_id)
        block_ids = [b["block_id"] for b in block_ranges if isinstance(b["block_id"], int)]

        data = await self.dashboard_repo.get_blocks_by_ids(schedule_date, block_ids)

        time_block_map = {doc["time_block"]: doc for doc in data}
        chart_data = []

        # Previous 24 block including current block
        for block in block_ranges:
            bid = block["block_id"]
            full_time = block["full_time"]
            val = float(time_block_map.get(bid, {}).get("Raw_Frequency", 0)) if bid else 0
            chart_data.append({"time_block": full_time, "frequency": val})

        return {
            "data": {
                "frequency": {
                    "current_block": get_fifteen_full_time_block_id(current_block_id),
                    "current_frequency": current_frequency,
                    "data": chart_data
                }
            }
        }

    async def get_dashboard_ScheduledDrawal(self, schedule_date: str, current_block_id: int, block_ranges, block_ids) -> Dict[str, Any]:
        full_time_block = get_fifteen_full_time_block_id(current_block_id)

        current_doc = await self.dashboard_repo.find_one({
            "schedule_date": schedule_date,
            "dc_sg_type": "actual",
            "data_type": "demand",
            "full_time": full_time_block
        })

        data = await self.dashboard_repo.get_blocks_by_ids(schedule_date, block_ids, dc_sg_type="actual")

        time_block_map = {doc["time_block"]: doc for doc in data}
        chart_data = []

        for block in block_ranges:
            bid = block["block_id"]
            full_time = block["full_time"]
            val = float(time_block_map.get(bid, {}).get("MP_Schedule", 0)) if bid else 0
            chart_data.append({"time_block": full_time, "schedule_drawal": val})

        return {
            "data": {
                "schedule_drawal": {
                    "current_block": get_fifteen_full_time_block_id(current_block_id),
                    "current_demand": round(current_doc.get("MP_Schedule", 0)) if current_doc else None,
                    "data": chart_data
                }
            }
        }

    
    async def get_dashboard_ODUD(self, schedule_date: str, current_block_id: int) -> Dict[str, Any]:
        full_time_block = get_fifteen_full_time_block_id(current_block_id)

        # Fetch current block OD-UD record
        current_doc = await self.dashboard_repo.find_one({
            "schedule_date": schedule_date,
            "dc_sg_type": "actual",
            "data_type": "demand",
            "full_time": full_time_block
        })
        
        current_od_ud = None
        if current_doc:
            current_od_ud = round(current_doc.get("MP_Drawal", 0) - current_doc.get("MP_Schedule", 0))


         # Ge Previous 23 + current block = total 24
        block_ranges = get_last_24_blocks(current_block_id)
        block_ids = [b["block_id"] for b in block_ranges if isinstance(b["block_id"], int)]

        # Fetch data for block range
        data = await self.dashboard_repo.get_blocks_by_ids(schedule_date, block_ids, dc_sg_type="actual")

        time_block_map = {doc["time_block"]: doc for doc in data}
        chart_data = []

        for block in block_ranges:
            bid = block["block_id"]
            full_time = block["full_time"]
            drawal = float(time_block_map.get(bid, {}).get("MP_Drawal", 0)) if bid else 0
            schedule = float(time_block_map.get(bid, {}).get("MP_Schedule", 0)) if bid else 0
            od_ud = round(drawal - schedule)
            chart_data.append({"time_block": full_time, "od_ud": od_ud})

        return {
            "current_block": full_time_block,
            "current_od_ud": current_od_ud,
            "data": chart_data
        }


    async def get_dashboard_MCP(self, schedule_date: str, current_block_id: int, block_ranges: List[Dict[str, Any]], block_ids: List[int]) -> Dict[str, Any]:
        try:
            # full_time_blocks = [get_fifteen_full_time_block_id(bid) for bid in block_ids]
            rtm_block_ids = [i for i in range(max(1, current_block_id - 11), current_block_id + 1)]
            rtm_pipeline = [
                {"$match": {"inserted_date": schedule_date, "blockNo": {"$in": rtm_block_ids}}},
                {"$sort": {"blockNo": 1}},
                {"$project": {
                    "time_block": "$blockNo",
                    "full_time": "$timeBlock",
                    "mcp": {
                        "$cond": {
                            "if": {"$isNumber": "$MCPRsMWh"},
                            "then": "$MCPRsMWh",
                            "else": {
                                "$convert": {
                                    "input": "$MCPRsMWh",
                                    "to": "double",
                                    "onError": None,
                                    "onNull": None
                                }
                            }
                        }
                    }
                }}
            ]

            dam_pipeline = [
                {"$match": {"inserted_date": schedule_date, "blockNo": {"$in": block_ids}}},
                {"$sort": {"blockNo": 1}},
                {"$project": {
                    "time_block": "$blockNo",
                    "full_time": "$timeBlock",
                    "mcp": {
                        "$cond": {
                            "if": {"$isNumber": "$MCPRsMWh"},
                            "then": "$MCPRsMWh",
                            "else": {
                                "$convert": {
                                    "input": "$MCPRsMWh",
                                    "to": "double",
                                    "onError": None,
                                    "onNull": None
                                }
                            }
                        }
                    }
                }}
            ]

            rtm_result = await self.dashboard_repo.aggregate_on("iex_rtm_market_snapshot", rtm_pipeline)
            dam_result = await self.dashboard_repo.aggregate_on("iex_market_snapshot", dam_pipeline)

            def process_mcp_data(result: List[Dict[str, Any]]) -> Tuple[float, List[List[Union[str, float]]]]:
                data = []
                current_value = 0.0
                for row in result:
                    time_label = row.get("full_time", "")
                    mcp_value = row.get("mcp", 0)
                    # data.append([time_label, mcp_value])
                    data.append({"time_block": time_label, "data": mcp_value})
                    if row.get("time_block") == current_block_id:
                        current_value = mcp_value
                return current_value, data

            rtm_current_value, rtm_data = process_mcp_data(rtm_result)
            dam_current_value, dam_data = process_mcp_data(dam_result)

            current_block_range = get_fifteen_full_time_block_id(current_block_id)

            return {
                "mcp": {
                    "current_block": current_block_range,
                    "rtm": rtm_current_value,
                    "dam": dam_current_value,
                    "rtm_data": rtm_data,
                    "dam_data": dam_data
                }
            }

        except Exception:
            logger.exception("Error fetching MCP data")
            return {"mcp": {}}





    def get_dashboard_peak_demand(self, schedule_date: str, current_block_id: int) -> Dict[str, Any]:
        """
        Calculates the peak demand over a 31-day period.
        It finds both the overall peak demand and the peak demand for a specific time block.
        """
        try:
            assert isinstance(current_block_id, int), "Invalid block ID"

            # Step 1: Prepare date range (for the last 31 days)
            end_date = datetime.strptime(schedule_date, "%Y-%m-%d")
            start_date = end_date - timedelta(days=30)
            start_date = start_date.strftime("%Y-%m-%d")
            end_date = end_date.strftime("%Y-%m-%d")
            # logger.warning(f'Dateds====================================================={start_date}--------------------{end_date}')
            
            # Step 2: Fetch all records for the 31-day period from the repository
            query = {
                "schedule_date": {"$gte": start_date, "$lte": end_date},
               "data_type": "demand", "dc_sg_type": "actual"
            }

            all_results = self.historical_repo.find_many(filter=query)
            # logger.warning(f'Dateds====================================================={all_results}')

            # Step 3: Group by date and find the peak for each day
            daily_peaks = {}
            current_block_demands = []
            
            for row in all_results:
                date = row.get("schedule_date")
                demand = row.get("MP_Demand", 0)
                time_block = row.get("time_block")
                
                if date and isinstance(demand, (int, float)):
                    if date not in daily_peaks or demand > daily_peaks[date]["demand"]:
                        daily_peaks[date] = {
                            "demand":round(demand, 2),
                            "time_block": time_block
                        }
                    
                    if time_block == current_block_id:
                        current_block_demands.append({"date": date, "demand": demand})

            # Step 4: Find the overall peak demand from the daily peaks
            max_demand_31_days = 0
            max_date_31_days = ""
            max_block_31_days = 0
            
            demand_data_list = []
            sorted_dates = sorted(daily_peaks.keys())

            
            for date in sorted_dates:
                peak_data = daily_peaks[date]
                demand_data_list.append({"date": date, "peak_demand": peak_data["demand"]})
                if peak_data["demand"] > max_demand_31_days:
                    max_demand_31_days = round(peak_data["demand"], 2)                          # ===============> RoundUp 2 digit figure
                    max_date_31_days = date
                    max_block_31_days = round(peak_data["time_block"], 2)                       # ===============> RoundUp 2 digit figure        

            # Step 5: Find the highest demand for the specified current_block_id
            current_block_max_demand = 0
            current_block_date = ""
            
            if current_block_demands:
                current_block_max_demand_row = max(current_block_demands, key=lambda item: item["demand"])
                current_block_max_demand = round(current_block_max_demand_row["demand"], 2)     # ===============> RoundUp 2 digit figure
                max_date_31_days = date
                current_block_date = current_block_max_demand_row["date"]
            # Step 6: Return the structured output
            return {
                "overall_peak_demand_31_days": {
                    "max_date": max_date_31_days,
                    "max_value": demand_data_list,
                    "block_id": max_block_31_days
                },
                "current_block_peak_demand_31_days": {
                    "max_date": current_block_date,
                    "max_value": current_block_max_demand,
                    "block_id": current_block_id
                },
                "daily_peak_data": demand_data_list
            }

        except Exception:
            logging.exception("Error fetching peak demand data")
            return {"data": {"peak_demand": {}}}





    def get_cronjob_time_from_block(self, block_id: int) -> str:
        total_minutes = (block_id - 1) * 15
        hour = total_minutes // 60
        minute = total_minutes % 60
        return f"{hour:02d}:{minute:02d}"



    async def get_dashboard_acp(self, schedule_date: str, current_block_id: int, block_ranges, block_ids) -> Dict[str, Any]:
        cron_time = self.get_cronjob_time_from_block(current_block_id)

        current_doc = await self.iex_acp_repo.find_one({
            "schedule_date": schedule_date,
            "cronJob_time": cron_time
        })
        if not current_doc:
            raise HTTPException(status_code=404, detail="Current acp block not found")

        # Updated line here
        data = await self.iex_acp_repo.find_many({
            "schedule_date": schedule_date,
            "cronJob_time": {
                "$in":[self.get_cronjob_time_from_block(b["block_id"]) for b in block_ranges if isinstance(b.get("block_id"), int)]
            }
        })

        # Preprocess mapping: "HH:MM" => doc
        time_block_map = {doc["cronJob_time"]: doc for doc in data}

        chart_data = []
       
        for block in block_ranges:
            bid = block["block_id"]

            full_time = block["full_time"]
            cron_time = self.get_cronjob_time_from_block(bid)

            acp_str = time_block_map.get(cron_time, {}).get("acp", "0")
            val = float(acp_str) if acp_str else 0

            chart_data.append({"time_block": full_time, "acp": val})

        return {
                    "current_block": get_fifteen_full_time_block_id(current_block_id),
                    "current_acp": float(current_doc.get("acp", 0)),
                    "data": chart_data
        }


    async def get_dashboard_DSM(self, schedule_date: str, current_block_id: int, block_ids: List[int]) -> Dict[str, Any]:
        try:
            # Define the MongoDB aggregation pipelines for RTM and DAM
            common_pipeline = lambda collection_name: [
                {"$match": {"inserted_date": schedule_date, "blockNo": {"$in": block_ids}}},
                {"$sort": {"blockNo": 1}},
                {"$project": {
                    "time_block": "$blockNo",
                    "full_time": "$timeBlock",
                    "mcp": {
                        "$cond": {
                            "if": {"$isNumber": "$MCPRsMWh"},
                            "then": "$MCPRsMWh",
                            "else": {
                                "$convert": {
                                    "input": "$MCPRsMWh",
                                    "to": "double",
                                    "onError": None,
                                    "onNull": None
                                }
                            }
                        }
                    }
                }}
            ]

            # Run aggregations
            rtm_result = await self.dashboard_repo.aggregate_on("iex_rtm_market_snapshot", common_pipeline("iex_rtm_market_snapshot"))
            dam_result = await self.dashboard_repo.aggregate_on("iex_market_snapshot", common_pipeline("iex_market_snapshot"))

            # Convert results into block-wise dicts for easy lookup
            rtm_map = {row["time_block"]: row.get("mcp", 0.0) for row in rtm_result}
            dam_map = {row["time_block"]: row.get("mcp", 0.0) for row in dam_result}

            dsm_data = []
            dsm_current_value = 0.0

            for block_id in block_ids:
                full_time = get_fifteen_full_time_block_id(block_id)

                rtm_val = rtm_map.get(block_id, 0.0)
                dam_val = dam_map.get(block_id, 0.0)
                dsm_val = round(dam_val - rtm_val, 2)

                dsm_data.append({"time_block": full_time, "dsm": dsm_val})

                if block_id == current_block_id:
                    dsm_current_value = dsm_val

            return {
                    "current_block": get_fifteen_full_time_block_id(current_block_id),
                    "current_value": dsm_current_value,
                    "data": dsm_data
            }

        except Exception:
            logger.exception("Error fetching DSM data")
            return {"dsm": {}}

    async def get_dashboard_capacity_chart(self) -> Dict[str, Any]:
        """
        Returns hierarchical sunburst data with colors:
        Total Capacity -> display_category -> category -> plant_name (with ppa_mw)
        """
        try:
            pipeline = [
                {"$addFields": {"ppa_mw_num": {"$toDouble": "$ppa_mw"}}},

                {"$group": {
                    "_id": {
                        "display_category": "$display_category",
                        "category": "$category",
                        "plant_name": "$plant_name"
                    },
                    "ppa_mw": {"$sum": "$ppa_mw_num"}
                }},

                {"$group": {
                    "_id": {
                        "display_category": "$_id.display_category",
                        "category": "$_id.category"
                    },
                    "plants": {"$push": {
                        "plant_name": "$_id.plant_name",
                        "ppa_mw": "$ppa_mw"
                    }},
                    "category_capacity": {"$sum": "$ppa_mw"}
                }},

                {"$group": {
                    "_id": "$_id.display_category",
                    "categories": {"$push": {
                        "category": "$_id.category",
                        "capacity_mw": "$category_capacity",
                        "children": "$plants"
                    }},
                    "display_category_capacity": {"$sum": "$category_capacity"}
                }},

                {"$group": {
                    "_id": None,
                    "total_capacity": {"$sum": "$display_category_capacity"},
                    "display_categories": {"$push": {
                        "display_category": "$_id",
                        "capacity_mw": "$display_category_capacity",
                        "children": "$categories"
                    }}
                }},

                {"$project": {
                    "_id": 0,
                    "name": {"$literal": "Total Capacity"},
                    "capacity_mw": "$total_capacity",
                    "children": "$display_categories"
                }}
            ]

            result = await self.master_plant.aggregate(pipeline)
            if not result:
                raise HTTPException(status_code=404, detail="No capacity data found")

            root = result[0]

            # Rename display categories
            rename_map = {"ISGS": "Central", "InSGS": "State", "In-SGS": "State"}
            for disp in root['children']:
                disp_name = disp['display_category']
                if disp_name in rename_map:
                    disp['display_category'] = rename_map[disp_name]

            # Assign colors
            display_colors = {
                "Central": "#34A853",
                "State": "#FF9900"
            }

            def lighten_color(base_hex: str, factor: float) -> str:
                base_hex = base_hex.lstrip('#')
                r, g, b = int(base_hex[:2], 16), int(base_hex[2:4], 16), int(base_hex[4:], 16)
                r = min(int(r + (255 - r) * factor), 255)
                g = min(int(g + (255 - g) * factor), 255)
                b = min(int(b + (255 - b) * factor), 255)
                return f"#{r:02X}{g:02X}{b:02X}"

            for disp in root['children']:
                disp_name = disp['display_category']
                disp['color'] = display_colors.get(disp_name, "#CCCCCC")

                num_categories = len(disp['children'])
                for c_idx, cat in enumerate(disp['children']):
                    factor = 0.2 + 0.6 * c_idx / max(1, num_categories - 1)
                    cat['color'] = lighten_color(disp['color'], factor)

                    num_plants = len(cat['children'])
                    for p_idx, plant in enumerate(cat['children']):
                        factor_p = 0.3 + 0.4 * p_idx / max(1, num_plants - 1)
                        plant['color'] = lighten_color(cat['color'], factor_p)

            return root

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def get_dashboard(self, request: Request) -> Dict[str, Any]:
        try:
            now = ist_time()    # called for IST timezone
            current_block_id = get_fifteen_time_block((now + timedelta(minutes=0, seconds=1)).strftime("%H:%M"))
            block_ranges = get_surrounding_block_ranges(current_block_id)
            block_ids = [b["block_id"] for b in block_ranges if isinstance(b["block_id"], int)]

            schedule_date_now = now.strftime("%Y-%m-%d")                                              # Dynamic schedule_date for all
            schedule_date = "2025-07-27"  # we can replace this with dynamic logic
            schedule_date_old = "2022-07-27"  # we can replace this with dynamic logic

            demand = await self.get_dashboard_demand(schedule_date, current_block_id, block_ranges, block_ids)
            frequency = await self.get_dashboard_frequency(schedule_date, current_block_id)
            schedule_drawal = await self.get_dashboard_ScheduledDrawal(schedule_date, current_block_id, block_ranges, block_ids)
            od_ud = await self.get_dashboard_ODUD(schedule_date, current_block_id)
            mcp = await self.get_dashboard_MCP(schedule_date_old, current_block_id, block_ranges, block_ids)        # As date not matched so used "schedule_date_old"
            peak_demand = self.get_dashboard_peak_demand(schedule_date_now, current_block_id)
            acp = await self.get_dashboard_acp(schedule_date_old, current_block_id, block_ranges, block_ids)
            dsm = await self.get_dashboard_DSM(schedule_date_old, current_block_id, block_ids)




            return {
                "demand": demand["data"]["demand"],
                "frequency": frequency["data"]["frequency"],
                "schedule_drawal": schedule_drawal["data"]["schedule_drawal"],
                "od_ud": od_ud,
                "mcp": mcp,
                "peak_demand": peak_demand,
                "acp": acp,
                "dsm": dsm
            }

        except Exception:
            logger.exception("Error combining dashboard data")
            raise HTTPException(status_code=500, detail="Failed to get dashboard data")

    async def get_daily_peak_demand(self) -> Dict[str, Any]:
        try:
            from_timestamp = 1709231400
            to_timestamp = 1711909799
            dc_sg_type = "actual"
            pipeline = [
                    {
                        "$match": {
                            "schedule_date_timestamp": {
                                "$gte": 1709231400,
                                "$lte": 1711909799
                            },
                            "dc_sg_type": "actual",
                            "data_type": "demand"
                        }
                    },
                    {
                        "$addFields": {
                            "schedule_date": {
                                "$toDate": { "$multiply": ["$schedule_date_timestamp", 1000] }
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": {
                                "$dateToString": { "format": "%Y-%m-%d", "date": "$schedule_date" }
                            },
                            "maxDemand": { "$max": "$MP_Demand" }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "date": "$_id",
                            "maxDemand": 1
                        }
                    },
                    {
                        "$sort": { "date": 1 }
                    }
                ]

            cursor = await self.demand_PG_repo.aggregate(pipeline)
            max_item = max(cursor, key=lambda x: x["maxDemand"])
            logger.warning(f' combining dashboard data {cursor[0]["maxDemand"]}')
            return {"data":cursor, "month_peak_demand":max_item}

        except Exception:
            logger.exception("Error combining dashboard data")
            raise HTTPException(status_code=500, detail="Failed to get dashboard data")

# Global instance
dashboard_service = DashboardPGService()











