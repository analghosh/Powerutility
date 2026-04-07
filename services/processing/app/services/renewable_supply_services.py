import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from zoneinfo import ZoneInfo  
from fastapi import HTTPException


from ..repository.serve_table_repository import SupplyTableRepository

logger = logging.getLogger(__name__)



class RenewableSupplyServices:
    def __init__(self, collection_name: str = "supply_table_api"):
        # your existing repos
        self.supply_repo = SupplyTableRepository(collection_name=collection_name)
        self.demand_repo = SupplyTableRepository(collection_name="serve_demand_table")
        # NEW: availability repo (your source table)
        self.availability_repo = SupplyTableRepository(collection_name="serve_availability_table")
        self.schedule_repo = SupplyTableRepository(collection_name="serve_renewable_table")
        # For getting max updated_at from serve_supply_table
        self.serve_supply_repo = SupplyTableRepository(collection_name="serve_supply_table")


    def get_max_updated_at_from_supply(self) -> Optional[str]:
        """
        Get the maximum updated_at timestamp from serve_supply_table.
        Returns ISO formatted datetime string or None if no data.
        """
        try:
            pipeline = [
                {"$group": {"_id": None, "max_updated": {"$max": "$updated_at"}}},
                {"$project": {"_id": 0, "max_updated": 1}}
            ]
            result = self.serve_supply_repo.aggregate(pipeline)
            if result and len(result) > 0 and result[0].get("max_updated"):
                max_updated = result[0]["max_updated"]
                # Convert to ISO string if it's a datetime object
                if isinstance(max_updated, datetime):
                    return max_updated.isoformat()
                return str(max_updated)
            return None
        except Exception as e:
            logger.exception("Failed to get max updated_at from serve_supply_table")
            return None

    def _four_day_window(self, anchor_date: Optional[str]) -> List[str]:
        """
        Returns the 4 dates (YYYY-MM-DD) for: D-2, D-1, D, D+1
        Anchor defaults to *today* in Asia/Kolkata if None.
        """
        tz = ZoneInfo("Asia/Kolkata")
        if anchor_date:
            if len(anchor_date) != 10:
                raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
            anchor = datetime.strptime(anchor_date, "%Y-%m-%d").date()
        else:
            anchor = datetime.now(tz).date()
        start = anchor - timedelta(days=2)
        return [(start + timedelta(days=i)).isoformat() for i in range(4)]
    
    def renewable_availability_by_day(self, date_str: str) -> Dict[str, Any]:
        """
        One day, 96 blocks. SG priority, DC fallback. Gap = SG - DC.
        Assumes schedule_date stored as 'YYYY-MM-DD' (string).
        """
        if not isinstance(date_str, str) or len(date_str) != 10:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

        # quick existence probe so callers can 404 cleanly
        probe = [
            {"$match": {
                "schedule_date": date_str,
                "Data_cat_type": "technology",
                "dc_sg_type": {"$in": ["SG", "DC"]},
                "timeBlock": {"$gte": 1, "$lte": 96},
            }},
            {"$limit": 1},
            {"$project": {"_id": 1}}
        ]
        if not list(self.availability_repo.aggregate(probe)):
            raise HTTPException(status_code=404, detail=f"No data for {date_str}.")

        pipeline = [
            {"$match": {
                "schedule_date": date_str,
                "Data_cat_type": "technology",
                "dc_sg_type": {"$in": ["SG", "DC"]},
                "timeBlock": {"$gte": 1, "$lte": 96},
            }},
            {"$group": {
                "_id": "$timeBlock",
                "sg_state_solar": {"$max": {"$cond": [{"$eq": ["$dc_sg_type","SG"]}, "$filterData.state_solar", None]}},
                "sg_state_wind":  {"$max": {"$cond": [{"$eq": ["$dc_sg_type","SG"]}, "$filterData.state_wind",  None]}},
                "sg_solar":       {"$max": {"$cond": [{"$eq": ["$dc_sg_type","SG"]}, "$filterData.Solar",       None]}},
                "sg_wind":        {"$max": {"$cond": [{"$eq": ["$dc_sg_type","SG"]}, "$filterData.Wind",        None]}},
                "dc_state_solar": {"$max": {"$cond": [{"$eq": ["$dc_sg_type","DC"]}, "$filterData.state_solar", None]}},
                "dc_state_wind":  {"$max": {"$cond": [{"$eq": ["$dc_sg_type","DC"]}, "$filterData.state_wind",  None]}},
                "dc_solar":       {"$max": {"$cond": [{"$eq": ["$dc_sg_type","DC"]}, "$filterData.Solar",       None]}},
                "dc_wind":        {"$max": {"$cond": [{"$eq": ["$dc_sg_type","DC"]}, "$filterData.Wind",        None]}},
            }},
            {"$project": {
                "_id": 0,
                "time_block": "$_id",
                "state_solar": {"$ifNull": ["$sg_state_solar", "$dc_state_solar"]},
                "state_wind":  {"$ifNull": ["$sg_state_wind",  "$dc_state_wind"]},
                "Solar":       {"$ifNull": ["$sg_solar",       "$dc_solar"]},
                "Wind":        {"$ifNull": ["$sg_wind",        "$dc_wind"]},
                "gap_state_solar": {"$cond": [
                    {"$and":[{"$ne":["$sg_state_solar",None]},{"$ne":["$dc_state_solar",None]}]},
                    {"$subtract":["$sg_state_solar","$dc_state_solar"]}, None]},
                "gap_state_wind": {"$cond": [
                    {"$and":[{"$ne":["$sg_state_wind",None]},{"$ne":["$dc_state_wind",None]}]},
                    {"$subtract":["$sg_state_wind","$dc_state_wind"]}, None]},
                "gap_solar": {"$cond": [
                    {"$and":[{"$ne":["$sg_solar",None]},{"$ne":["$dc_solar",None]}]},
                    {"$subtract":["$sg_solar","$dc_solar"]}, None]},
                "gap_wind": {"$cond": [
                    {"$and":[{"$ne":["$sg_wind",None]},{"$ne":["$dc_wind",None]}]},
                    {"$subtract":["$sg_wind","$dc_wind"]}, None]},
                "source": {
                    "$cond": [
                        {"$or":[
                            {"$ne":["$sg_state_solar",None]},
                            {"$ne":["$sg_state_wind",None]},
                            {"$ne":["$sg_solar",None]},
                            {"$ne":["$sg_wind",None]},
                        ]},
                        "SG",
                        {"$cond":[
                            {"$or":[
                                {"$ne":["$dc_state_solar",None]},
                                {"$ne":["$dc_state_wind",None]},
                                {"$ne":["$dc_solar",None]},
                                {"$ne":["$dc_wind",None]},
                            ]},
                            "DC","NONE"
                        ]}
                    ]
                }
            }},
            {"$sort": {"time_block": 1}}
        ]

        rows: List[Dict[str, Any]] = list(self.availability_repo.aggregate(pipeline))

        # normalize to 96 blocks
        by_tb = {r["time_block"]: r for r in rows}
        blocks: List[Dict[str, Any]] = []
        for tb in range(1, 97):
            blocks.append(by_tb.get(tb, {
                "time_block": tb,
                "state_solar": None, "state_wind": None,
                "Solar": None, "Wind": None,
                "gap_state_solar": None, "gap_state_wind": None, "gap_solar": None, "gap_wind": None,
                "source": "NONE",
            }))
        return {
            "data": {"schedule_date": date_str, "blocks": blocks},
            "last_updated_at": self.get_max_updated_at_from_supply()
        }
    

    # def renewable_schedule_by_day(self, date_str: str):
    #     """
    #     Returns 4 documents for a given date (YYYY-MM-DD):
    #       (solar, IDF), (solar, DAF), (wind, IDF), (wind, DAF)
    #     Each doc has `data` with 96 {time_block, scheduled_volume} entries.
    #     schedule_volume values are SUMmed across matching rows.
    #     """
    #     if not isinstance(date_str, str) or len(date_str) != 10:
    #         raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

    #     tech_list = ["solar", "wind"]
    #     type_list = ["IDF", "DAF"]

    #     # quick probe so callers can 404 cleanly
    #     probe = [
    #         {"$match": {
    #             "schedule_date": date_str,
    #             "technology": {"$in": tech_list},
    #             "type": {"$in": type_list},
    #             "time_block": {"$gte": 1, "$lte": 96}
    #         }},
    #         {"$limit": 1},
    #         {"$project": {"_id": 1}}
    #     ]
    #     if not list(self.schedule_repo.aggregate(probe)):
    #         raise HTTPException(status_code=404, detail=f"No schedule data for {date_str}.")

    #     # pipeline: sum by (date, tech, type, time_block) and expand to 96 slots
    #     pipeline = [
    #         {"$match": {
    #             "schedule_date": date_str,
    #             "technology": {"$in": tech_list},
    #             "type": {"$in": type_list},
    #             "time_block": {"$gte": 1, "$lte": 96}
    #         }},
    #         {"$group": {
    #             "_id": {
    #                 "schedule_date": "$schedule_date",
    #                 "technology": "$technology",
    #                 "type": "$type",
    #                 "time_block": "$time_block"
    #             },
    #             "scheduled_volume": {"$sum": "$schedule_volume"}
    #         }},
    #         {"$group": {
    #             "_id": {
    #                 "schedule_date": "$_id.schedule_date",
    #                 "technology": "$_id.technology",
    #                 "type": "$_id.type"
    #             },
    #             "data": {
    #                 "$push": {
    #                     "time_block": "$_id.time_block",
    #                     "scheduled_volume": "$scheduled_volume"
    #                 }
    #             }
    #         }},
    #         # Expand to exactly 96 blocks; default missing to 0 (no dynamic $getField)
    #         {"$project": {
    #             "_id": 0,
    #             "schedule_date": "$_id.schedule_date",
    #             "technology": "$_id.technology",
    #             "type": "$_id.type",
    #             "data": {
    #                 "$map": {
    #                     "input": {"$range": [1, 97]},
    #                     "as": "tb",
    #                     "in": {
    #                         "time_block": "$$tb",
    #                         "scheduled_volume": {
    #                             "$round": [       # 👈 round to 2 decimals
    #                                 {
    #                                     "$let": {
    #                                         "vars": {
    #                                             "match": {
    #                                                 "$first": {
    #                                                     "$filter": {
    #                                                         "input": "$data",
    #                                                         "cond": {"$eq": ["$$this.time_block", "$$tb"]}
    #                                                     }
    #                                                 }
    #                                             }
    #                                         },
    #                                         "in": {"$ifNull": ["$$match.scheduled_volume", 0]}
    #                                     }
    #                                 },
    #                                 2
    #                             ]
    #                         }
    #                     }
    #                 }
    #             }
    #         }},
    #         # stable ordering: solar before wind, IDF before DAF
    #         {"$addFields": {
    #             "tech_order": {"$indexOfArray": [tech_list, "$technology"]},
    #             "type_order": {"$indexOfArray": [type_list, "$type"]}
    #         }},
    #         {"$sort": {"tech_order": 1, "type_order": 1}},
    #         {"$project": {"tech_order": 0, "type_order": 0}}
    #     ]

    #     docs = list(self.schedule_repo.aggregate(pipeline))
    #     return {"data": docs}

    def renewable_schedule_by_day(self, date_str: str):
        """
        Source: serve_renewable_table
        Returns separate series for both 'central' and 'state'.
        For each c_s × technology × type, you get exactly 96 blocks.
        """
        if not isinstance(date_str, str) or len(date_str) != 10:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")

        tech_list = ["solar", "wind"]
        type_list = ["IDF", "DAF"]
        cs_list   = ["central", "state"]

        base_match = {
            "schedule_date": date_str,
            "technology": {"$in": tech_list},
            "type": {"$in": type_list},
            "time_block": {"$gte": 1, "$lte": 96},
            "c_s": {"$in": cs_list},
        }

        # probe for existence
        probe = [{"$match": base_match}, {"$limit": 1}, {"$project": {"_id": 1}}]
        if not list(self.schedule_repo.aggregate(probe)):
            raise HTTPException(status_code=404, detail=f"No schedule data for {date_str}.")

        pipeline = [
            {"$match": base_match},
            {"$group": {
                "_id": {
                    "c_s": "$c_s",
                    "technology": "$technology",
                    "type": "$type",
                    "tb": "$time_block"
                },
                "scheduled_volume": {"$sum": "$schedule_volume"},
            }},
            {"$group": {
                "_id": {
                    "c_s": "$_id.c_s",
                    "technology": "$_id.technology",
                    "type": "$_id.type"
                },
                "data": {
                    "$push": {"time_block": "$_id.tb", "scheduled_volume": "$scheduled_volume"}
                }
            }},
            {"$project": {
                "_id": 0,
                "schedule_date": date_str,
                "c_s": "$_id.c_s",
                "technology": "$_id.technology",
                "type": "$_id.type",
                "data": {
                    "$map": {
                        "input": {"$range": [1, 97]},
                        "as": "tb",
                        "in": {
                            "time_block": "$$tb",
                            "scheduled_volume": {
                                "$round": [
                                    {"$let": {
                                        "vars": {"m": {"$first": {
                                            "$filter": {
                                                "input": "$data",
                                                "cond": {"$eq": ["$$this.time_block", "$$tb"]}
                                            }
                                        }}},
                                        "in": {"$ifNull": ["$$m.scheduled_volume", 0]}
                                    }}, 2]
                            }
                        }
                    }
                }
            }},
            {"$addFields": {
                "cs_order": {"$indexOfArray": [cs_list, "$c_s"]},
                "tech_order": {"$indexOfArray": [tech_list, "$technology"]},
                "type_order": {"$indexOfArray": [type_list, "$type"]},
            }},
            {"$sort": {"cs_order": 1, "tech_order": 1, "type_order": 1}},
            {"$project": {"cs_order": 0, "tech_order": 0, "type_order": 0}},
        ]

        docs = list(self.schedule_repo.aggregate(pipeline))
        return {"data": docs, "last_updated_at": self.get_max_updated_at_from_supply()}

    


    # def renewable_availability_4day(self, anchor_date: Optional[str] = None) -> Dict[str, Any]:
    #     """
    #     Fixed 4-day window: day-before-yesterday, yesterday, today, tomorrow (Asia/Kolkata).
    #     Returns a list of 4 day objects; each day has 96 blocks (filled with NONE if missing).
    #     Assumes schedule_date is stored as 'YYYY-MM-DD' string.
    #     """
    #     days = self._four_day_window(anchor_date)
    #     start_date, end_date = days[0], days[-1]

    #     # Quick existence probe for the window
    #     probe = [
    #         {"$match": {
    #             "schedule_date": {"$gte": start_date, "$lte": end_date},
    #             "Data_cat_type": "technology",
    #             "dc_sg_type": {"$in": ["SG", "DC"]},
    #             "timeBlock": {"$gte": 1, "$lte": 96},
    #         }},
    #         {"$limit": 1},
    #         {"$project": {"_id": 1}}
    #     ]
    #     if not list(self.availability_repo.aggregate(probe)):
    #         raise HTTPException(
    #             status_code=404,
    #             detail=f"No data in 4-day window {start_date}..{end_date}."
    #         )

    #     # Aggregate SG/DC for the window
    #     pipeline = [
    #         {"$match": {
    #             "schedule_date": {"$gte": start_date, "$lte": end_date},
    #             "Data_cat_type": "technology",
    #             "dc_sg_type": {"$in": ["SG", "DC"]},
    #             "timeBlock": {"$gte": 1, "$lte": 96},
    #         }},
    #         {"$group": {
    #             "_id": {"schedule_date": "$schedule_date", "timeBlock": "$timeBlock"},
    #             "sg_state_solar": {"$max": {"$cond": [{"$eq": ["$dc_sg_type", "SG"]}, "$filterData.state_solar", None]}},
    #             "sg_state_wind":  {"$max": {"$cond": [{"$eq": ["$dc_sg_type", "SG"]}, "$filterData.state_wind",  None]}},
    #             "sg_solar":       {"$max": {"$cond": [{"$eq": ["$dc_sg_type", "SG"]}, "$filterData.Solar",       None]}},
    #             "sg_wind":        {"$max": {"$cond": [{"$eq": ["$dc_sg_type", "SG"]}, "$filterData.Wind",        None]}},
    #             "dc_state_solar": {"$max": {"$cond": [{"$eq": ["$dc_sg_type", "DC"]}, "$filterData.state_solar", None]}},
    #             "dc_state_wind":  {"$max": {"$cond": [{"$eq": ["$dc_sg_type", "DC"]}, "$filterData.state_wind",  None]}},
    #             "dc_solar":       {"$max": {"$cond": [{"$eq": ["$dc_sg_type", "DC"]}, "$filterData.Solar",       None]}},
    #             "dc_wind":        {"$max": {"$cond": [{"$eq": ["$dc_sg_type", "DC"]}, "$filterData.Wind",        None]}},
    #         }},
    #         {"$project": {
    #             "_id": 0,
    #             "schedule_date": "$_id.schedule_date",
    #             "time_block": "$_id.timeBlock",

    #             "state_solar": {"$ifNull": ["$sg_state_solar", "$dc_state_solar"]},
    #             "state_wind":  {"$ifNull": ["$sg_state_wind",  "$dc_state_wind"]},
    #             "Solar":       {"$ifNull": ["$sg_solar",       "$dc_solar"]},
    #             "Wind":        {"$ifNull": ["$sg_wind",        "$dc_wind"]},

    #             "gap_state_solar": {"$cond": [
    #                 {"$and": [{"$ne": ["$sg_state_solar", None]}, {"$ne": ["$dc_state_solar", None]}]},
    #                 {"$subtract": ["$sg_state_solar", "$dc_state_solar"]}, None]},
    #             "gap_state_wind": {"$cond": [
    #                 {"$and": [{"$ne": ["$sg_state_wind", None]}, {"$ne": ["$dc_state_wind", None]}]},
    #                 {"$subtract": ["$sg_state_wind", "$dc_state_wind"]}, None]},
    #             "gap_solar": {"$cond": [
    #                 {"$and": [{"$ne": ["$sg_solar", None]}, {"$ne": ["$dc_solar", None]}]},
    #                 {"$subtract": ["$sg_solar", "$dc_solar"]}, None]},
    #             "gap_wind": {"$cond": [
    #                 {"$and": [{"$ne": ["$sg_wind", None]}, {"$ne": ["$dc_wind", None]}]},
    #                 {"$subtract": ["$sg_wind", "$dc_wind"]}, None]},

    #             "source": {
    #                 "$cond": [
    #                     {"$or": [
    #                         {"$ne": ["$sg_state_solar", None]},
    #                         {"$ne": ["$sg_state_wind",  None]},
    #                         {"$ne": ["$sg_solar",       None]},
    #                         {"$ne": ["$sg_wind",        None]},
    #                     ]},
    #                     "SG",
    #                     {"$cond": [
    #                         {"$or": [
    #                             {"$ne": ["$dc_state_solar", None]},
    #                             {"$ne": ["$dc_state_wind",  None]},
    #                             {"$ne": ["$dc_solar",       None]},
    #                             {"$ne": ["$dc_wind",        None]},
    #                         ]},
    #                         "DC",
    #                         "NONE"
    #                     ]}
    #                 ]
    #             }
    #         }},
    #         {"$sort": {"schedule_date": 1, "time_block": 1}}
    #     ]

    #     rows: List[Dict[str, Any]] = list(self.availability_repo.aggregate(pipeline))

    #     # Bucket by day and fill exactly 96 blocks per requested day
    #     by_day: Dict[str, Dict[int, Dict[str, Any]]] = {d: {} for d in days}
    #     for r in rows:
    #         by_day.setdefault(r["schedule_date"], {})
    #         by_day[r["schedule_date"]][r["time_block"]] = r

    #     out: List[Dict[str, Any]] = []
    #     for day in days:  # keep the fixed window order
    #         blocks: List[Dict[str, Any]] = []
    #         tb_map = by_day.get(day, {})
    #         for tb in range(1, 97):
    #             blocks.append(tb_map.get(tb, {
    #                 "time_block": tb,
    #                 "state_solar": None, "state_wind": None,
    #                 "Solar": None, "Wind": None,
    #                 "gap_state_solar": None, "gap_state_wind": None, "gap_solar": None, "gap_wind": None,
    #                 "source": "NONE",
    #             }))
    #         out.append({"schedule_date": day, "blocks": blocks})

    #     return {"data": out}

# # Global Instances
renewable_supply_services = RenewableSupplyServices()
