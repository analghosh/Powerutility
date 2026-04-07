import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Union, Tuple, Optional
from collections import defaultdict
import pandas as pd
import numpy as np
import re


from fastapi import HTTPException, Request
from ..repository.serve_supply_table_repository import serveSupplyTableRepository

logger = logging.getLogger(__name__)


class serveSupplyServices:
    def __init__(self, collection_name: str = "serve_supply_table"):
        self.supply_repo = serveSupplyTableRepository()

     # --- Helpers (IST "today" + current block) --------------------------------
    IST = timezone(timedelta(hours=5, minutes=30))

    def get_max_updated_at(self) -> Optional[str]:
        """
        Get the maximum updated_at timestamp from serve_supply_table.
        Returns ISO formatted datetime string or None if no data.
        """
        try:
            pipeline = [
                {"$group": {"_id": None, "max_updated": {"$max": "$updated_at"}}},
                {"$project": {"_id": 0, "max_updated": 1}}
            ]
            result = self.supply_repo.aggregate(pipeline)
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

    @classmethod
    def _today_ist_str(cls) -> str:
        return datetime.now(cls.IST).date().isoformat()

    # @classmethod
    # def _current_time_block_ist(cls) -> int:
    #     """
    #     'Previous-end' 15-min blocks:
    #     00:00–00:15 -> 0
    #     16:45–17:00 -> 67
    #     17:00–17:15 -> 68
    #     Use // (floor), not ceil.
    #     """
    #     now = datetime.now(cls.IST)
    #     total_min = now.hour * 60 + now.minute
    #     block = total_min // 15          # 0..95
    #     return block
    
    @classmethod
    def _current_time_block_ist(cls) -> int:
        """
        15-min blocks mapped to 1..96:
        00:00–00:14 -> 1
        00:15–00:29 -> 2
        ...
        23:45–23:59 -> 96
        """
        now = datetime.now(cls.IST)
        total_min = now.hour * 60 + now.minute
        block = total_min // 15 + 1  # 1..96
        return block
    
    @staticmethod
    def _attach_demand_by_block(schedule_date: str, demand_coll: str = "serve_demand_table"):
        return [
            {
                "$lookup": {
                    "from": demand_coll,
                    # IMPORTANT: our current doc's _id must be the time_block number,
                    # or we should read _id.time_block if _id is a dict.
                    "let": {"blk": "$_id", "sd": schedule_date},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        # {"$eq": ["$time_block", "$$blk"]},
                                        {"$eq": [{"$toInt": "$time_block"}, {"$toInt": "$$blk"}]},
                                        {"$eq": ["$schedule_date", "$$sd"]},
                                    ]
                                }
                            }
                        },
                        {
                            "$project": {
                                "_id": 0,
                                "comp_act_demand": {"$round": ["$comp_act_demand", 2]},
                                "comp_udm": {"$round": ["$comp_udm", 2]},
                                # "rostering": 1, Not required  as suggested by client. Rostering value will be calculated on frontend by UDM - demand
                            }
                        }
                    ],
                    "as": "demand"
                }
            },
            {"$set": {"demand": {"$first": "$demand"}}}
        ]



    def supply_by_category(self, schedule_date) -> Dict[str, Any]:
        if not schedule_date:
            schedule_date = self._today_ist_str()
        R = lambda x: {"$round": [x, 2]}
        pipeline = [
            # 1) Filter the day & blocks
            {"$match": {
                "schedule_date": schedule_date,
                "time_block": {"$gte": 1, "$lte": 96}
            }},

            # 2) Normalize dc_sg; default missing/blank to "DC"
            {"$addFields": {
                "_dcsg": {
                    "$toUpper": {
                        "$ifNull": [
                            {"$cond": [
                                {"$eq": [{"$type": "$dc_sg"}, "missing"]},
                                None,
                                "$dc_sg"
                            ]},
                            "DC"
                        ]
                    }
                }
            }},

            # 3) Sum at (block, c_s, category, dc_sg)
            {"$group": {
                "_id": {
                    "time_block": "$time_block",
                    "c_s": "$c_s",
                    "category": "$category",
                    "dc_sg": "$_dcsg"
                },
                "vol": {"$sum": {"$ifNull": ["$volume", 0]}}
            }},

            # 4) Collapse dc_sg within each (block, c_s, category)
            {"$group": {
                "_id": {
                    "time_block": "$_id.time_block",
                    "c_s": "$_id.c_s",
                    "category": "$_id.category"
                },
                "volSG": {"$sum": {"$cond": [{"$eq": ["$_id.dc_sg", "SG"]}, "$vol", 0]}},
                "volDC": {"$sum": {"$cond": [{"$eq": ["$_id.dc_sg", "DC"]}, "$vol", 0]}}
            }},

            # 4.5) Compute chosen volume per (block, c_s, category)
            {"$project": {
                "_id": 0,
                "time_block": "$_id.time_block",
                "c_s": "$_id.c_s",
                "category": "$_id.category",
                "volume": R({"$cond": [{"$gt": ["$volSG", 0]}, "$volSG", "$volDC"]})
            }},

            # 5) Order (central before state, categories alphabetical)
            {"$addFields": {"c_s_order": {"$cond": [{"$eq": ["$c_s", "central"]}, 0, 1]}}},
            {"$sort": {"time_block": 1, "c_s_order": 1, "category": 1}},

            # 6) Build array {category, volume} per c_s in each block
            {"$group": {
                "_id": {"time_block": "$time_block", "c_s": "$c_s"},
                "categories": {"$push": {"category": "$category", "volume": "$volume"}}
            }},

            # 7) Put central then state under each block
            {"$addFields": {"c_s_order": {"$cond": [{"$eq": ["$_id.c_s", "central"]}, 0, 1]}}},
            {"$sort": {"_id.time_block": 1, "c_s_order": 1}},

            # 8) Final shape per block -> NOW _id == time_block
            {"$group": {
                "_id": "$_id.time_block",
                # "byCategory": {
                #     "$push": {"c_s": "$_id.c_s", "category": "$categories"}
                # }
                "byCategory": {
                    "$push": {
                        "c_s": {
                            "$cond": [
                                {"$eq": ["$_id.c_s", "central"]}, "Central", "State"
                            ]
                        },
                        "category": "$categories"
                    }
                }
            }},

            # Attached demand AFTER you have one doc per block
            *serveSupplyServices._attach_demand_by_block(schedule_date),

            # Keep demand in the final projection
            {"$project": {
                "_id": 0,
                "block": "$_id",
                "byCategory": 1,
                # "demand": 1,  # or project individual fields if you prefer
                "comp_act_demand": R("$demand.comp_act_demand"),
                "comp_udm": R("$demand.comp_udm"),
               
            }},

            {"$sort": {"block": 1}},
        ]

        result = self.supply_repo.aggregate(pipeline)
        return {"result": result, "last_updated_at": self.get_max_updated_at()}


    def supply_by_category_block(
        self,
        schedule_date: Union[str, None] = None,
        time_block: Union[int, None] = None,
    ) -> Dict[str, Any]:
        """
        Single-block supply grouped by category with central/state split and totals.
        SG-priority at plant×block×category: use SG/SH if present, else DC.
        """
        date_str = schedule_date or self._today_ist_str()
        block = time_block if time_block is not None else self._current_time_block_ist()
        if not (1 <= block <= 96):
            raise HTTPException(status_code=422, detail="time_block must be in 1..96")
        R = lambda x: {"$round": [x, 2]}
        pipeline = [
            {"$match": {
                "schedule_date": date_str,
                "time_block": block
            }},
            {
                "$project": {
                    "time_block": 1,
                    "category": 1,
                    "c_s": 1,
                    "volume": {"$ifNull": ["$volume", 0]},
                    # Normalize SG/SH -> SG, else DC
                    "_dcsg": {
                        "$let": {
                            "vars": {
                                "x": {
                                    "$toUpper": {
                                        "$trim": {"input": {"$ifNull": ["$dc_sg", "DC"]}}
                                    }
                                }
                            },
                            "in": {"$cond": [{"$in": ["$$x", ["SG", "SH"]]}, "SG", "DC"]}
                        }
                    }
                }
            },
            # Collapse per-plant rows inside a category by SG/DC, then choose SG if available
            {
                "$group": {
                    "_id": {
                        "time_block": "$time_block",
                        "category": "$category",
                        "c_s": "$c_s"
                    },
                    "volSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, "$volume", 0]}},
                    "volDC": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "DC"]}, "$volume", 0]}},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "time_block": "$_id.time_block",
                    "category": {"$ifNull": ["$_id.category", "Uncategorized"]},
                    "c_s": "$_id.c_s",
                    "chosenVol": R({"$cond": [{"$gt": ["$volSG", 0]}, "$volSG", "$volDC"]})
                }
            },
            # Sum to central, state, total for each category
            {
                "$group": {
                    "_id": {"time_block": "$time_block", "category": "$category"},
                    "central": {"$sum": {"$cond": [{"$eq": ["$c_s", "central"]}, "$chosenVol", 0]}},
                    "state":   {"$sum": {"$cond": [{"$eq": ["$c_s", "state"]}, "$chosenVol", 0]}},
                    "total":   {"$sum": "$chosenVol"}
                }
            },
            {"$sort": {"_id.category": 1}},
            {
                "$group": {
                    "_id": "$_id.time_block",
                    "category_breakdown": {
                        "$push": {
                            "category": "$_id.category",
                            "Central": R("$central"),
                            "State": R("$state"),
                            "total": R("$total")
                        }
                    }
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "time_block": "$_id",
                    "category_breakdown": 1,
                }
            },
            {"$sort": {"time_block": 1}},
        ]

        result = self.supply_repo.aggregate(pipeline)
        last_updated_at = self.get_max_updated_at()
        if not result:
            return {"result": [{"time_block": block, "category_breakdown": []}], "last_updated_at": last_updated_at}
        return {"result": result, "last_updated_at": last_updated_at}


    def supply_by_technology(self, schedule_date: Union[str, None] = None, time_block: Union[int, None] = None) -> Dict[str, Any]:
        date_str = schedule_date or self._today_ist_str()
        block = time_block if time_block is not None else self._current_time_block_ist()
        if not (1 <= block <= 96):
            raise HTTPException(status_code=422, detail="time_block must be in 1..96")
        R = lambda x: {"$round": [x, 2]}
        pipeline = [
            # Only the chosen date AND chosen block
            {"$match": {
                "schedule_date": date_str,
                "time_block": block
            }},

            # carry fields + normalize dc_sg (same as before) ...
            {"$project": {
                "time_block": 1, "plant_name": 1, "technology": 1, "c_s": 1,
                "volume": {"$ifNull": ["$volume", 0]},
                "_dcsg": {
                    "$cond": [
                        {"$eq": [{"$toUpper": {"$trim": {"input": {"$ifNull": ["$dc_sg", "DC"]}}}}, "SG"]},
                        "SG", "DC"
                    ]
                }
            }},
            {"$group": {
                "_id": {"time_block": "$time_block", "plant_name": "$plant_name", "technology": "$technology", "c_s": "$c_s"},
                "volSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, "$volume", 0]}},
                "volDC": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "DC"]}, "$volume", 0]}}
            }},
            {"$project": {
                "_id": 0,
                "time_block": "$_id.time_block",
                "technology": "$_id.technology",
                "c_s": "$_id.c_s",
                "chosenVol": R({"$cond": [{"$gt": ["$volSG", 0]}, "$volSG", "$volDC"]})
            }},
            {"$group": {
                "_id": {"time_block": "$time_block", "technology": "$technology"},
                "central": {"$sum": {"$cond": [{"$eq": ["$c_s", "central"]}, "$chosenVol", 0]}},
                "state":   {"$sum": {"$cond": [{"$eq": ["$c_s", "state"]}, "$chosenVol", 0]}},
                "total":   {"$sum": "$chosenVol"}
            }},
            {"$group": {
                "_id": "$_id.time_block",
                "technology_breakdown": {
                    "$push": {
                        "technology": "$_id.technology", 
                        "Central": R("$central"), 
                        "State": R("$state"), 
                        "total": R("$total")
                        }
                }
            }},
            
            {"$project": {
                "_id": 0,
                "time_block": "$_id",
                "technology_breakdown": 1,
            }},
            {"$sort": {"time_block": 1}},
        ]

        result = self.supply_repo.aggregate(pipeline)
        last_updated_at = self.get_max_updated_at()
        # Guarantee a single-block shape even if no rows
        if not result:
            return {"result": [{"time_block": block, "technology_breakdown": []}], "last_updated_at": last_updated_at}
        return {"result": result, "last_updated_at": last_updated_at}

#     def price_range(self) -> Dict[str, Any]:
       

#         pipeline = [
    
#     {
#         "$project": {
#             "_id": 0,
           
#         }
#     },
   
# ]

        # result = self.supply_repo.aggregate(pipeline)
        # return {"result": result}
    
    def supply_by_price_group(self, schedule_date: Union[str, None] = None, time_block: Union[int, None] = None) -> Dict[str, Any]:
        date_str = schedule_date or self._today_ist_str()
        block = time_block if time_block is not None else self._current_time_block_ist()
        if not (1 <= block <= 96):
            raise HTTPException(status_code=422, detail="time_block must be in 1..96")
        R = lambda x: {"$round": [x, 2]}
        pipeline = [
            {"$match": {
                "schedule_date": date_str,
                "time_block": block
            }},
            {"$project": {
                "time_block": 1, "plant_name": 1, "price_group": 1, "c_s": 1,
                "volume": {"$ifNull": ["$volume", 0]},
                "_dcsg": {
                    "$let": {
                        "vars": {"x": {"$toUpper": {"$trim": {"input": {"$ifNull": ["$dc_sg", "DC"]}}}}},
                        "in": {"$cond": [{"$in": ["$$x", ["SG", "SH"]]}, "SG", "DC"]}
                    }
                }
            }},
            {"$group": {
                "_id": {"time_block": "$time_block", "plant_name": "$plant_name", "price_group": "$price_group", "c_s": "$c_s"},
                "volSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, "$volume", 0]}},
                "volDC": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "DC"]}, "$volume", 0]}}
            }},
            {"$project": {
                "_id": 0,
                "time_block": "$_id.time_block",
                "price_group": "$_id.price_group",
                "c_s": "$_id.c_s",
                "chosenVol": R({"$cond": [{"$gt": ["$volSG", 0]}, "$volSG", "$volDC"]})
            }},
            {"$group": {
                "_id": {"time_block": "$time_block", "price_group": "$price_group"},
                "central": {"$sum": {"$cond": [{"$eq": ["$c_s", "central"]}, "$chosenVol", 0]}},
                "state":   {"$sum": {"$cond": [{"$eq": ["$c_s", "state"]}, "$chosenVol", 0]}},
                "total":   {"$sum": "$chosenVol"}
            }},
            {"$sort": {"_id.price_group": 1}},
            {"$group": {
                "_id": "$_id.time_block",
                "price_group_breakdown": {
                    "$push": {"price_group": "$_id.price_group", "Central": R("$central"), "State": R("$state"), "total": R("$total")}
                }
            }},
            *serveSupplyServices._attach_demand_by_block(date_str),
            {"$project": {
                "_id": 0,
                "time_block": "$_id",
                "price_group_breakdown": 1,
            }},
            {"$sort": {"time_block": 1}},
        ]

        result = self.supply_repo.aggregate(pipeline)
        last_updated_at = self.get_max_updated_at()
        if not result:
            return {"result": [{"time_block": block, "price_group_breakdown": []}], "last_updated_at": last_updated_at}
        return {"result": result, "last_updated_at": last_updated_at}
   

# ==========================================================================================================================================#
#                       THIS IS ONLY FOR GRAPH (SUPPLY BY TECHNOLOGY, PRICE GROUP )                                                         #
# ==========================================================================================================================================#
    def supply_by_technology_graph(self, schedule_date) -> Dict[str, Any]:
        if not schedule_date:
            schedule_date = self._today_ist_str()
        R = lambda x: {"$round": [x, 2]}
        pipeline = [
            # 1) Filter the day & blocks
            {
                "$match": {
                    "schedule_date": schedule_date,
                    "time_block": {"$gte": 1, "$lte": 96}
                }
            },

            # 2) Carry only needed fields + normalize dc_sg (SG priority; else DC)
            {
                "$project": {
                    "time_block": 1,
                    "plant_name": 1,   # or plant_id if you have it
                    "technology": 1,
                    "c_s": 1,
                    "volume": {"$ifNull": ["$volume", 0]},
                    "_dcsg": {
                        "$cond": [
                            {
                                "$eq": [
                                    {
                                        "$toUpper": {
                                            "$trim": {
                                                "input": {"$ifNull": ["$dc_sg", "DC"]}
                                            }
                                        }
                                    },
                                    "SG"
                                ]
                            },
                            "SG",
                            "DC"
                        ]
                    }
                }
            },

            # 3) Aggregate per plant × block (collect SG and DC separately)
            {
                "$group": {
                    "_id": {
                        "time_block": "$time_block",
                        "plant_name": "$plant_name",   # or plant_id
                        "technology": "$technology",
                        "c_s": "$c_s"
                    },
                    "volSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, "$volume", 0]}},
                    "volDC": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "DC"]}, "$volume", 0]}}
                }
            },

            # 4) Choose per-plant volume: SG if present, else DC
            {
                "$project": {
                    "_id": 0,
                    "time_block": "$_id.time_block",
                    "technology": "$_id.technology",
                    "c_s": "$_id.c_s",
                    "chosenVol": {
                        "$cond": [
                            {"$gt": ["$volSG", 0]},
                            "$volSG",
                            "$volDC"
                        ]
                    }
                }
            },

            # 5) Sum chosenVol per (block, technology) — we only keep TOTAL now
            {
                "$group": {
                    "_id": {"time_block": "$time_block", "technology": "$technology"},
                    "volume": {"$sum": ("$chosenVol")}
                }
            },

            # 6) Reshape to per-block array of { technology, volume }
            {
                "$group": {
                    "_id": "$_id.time_block",
                    "technology_breakdown": {
                        "$push": {
                            "technology": "$_id.technology",
                            "volume": R("$volume")
                        }
                    }
                }
            },

            # (optional) sort technologies inside each block by name or volume
            # If you want alphabetic ordering, insert a $unwind + $sort + $group here.
            # Keeping it simple as you didn't require a specific order.
            # Attached demand AFTER you have one doc per block
            *serveSupplyServices._attach_demand_by_block(schedule_date),
            {"$project": {"_id": 0, "time_block": "$_id", "technology_breakdown": 1, "demand": 1}},
            {"$sort": {"time_block": 1}}
        ]

        result = self.supply_repo.aggregate(pipeline)
        return {"result": result, "last_updated_at": self.get_max_updated_at()}



    def supply_by_price_group_graph(self, schedule_date) -> Dict[str, Any]:
        if not schedule_date:
            schedule_date = self._today_ist_str()
        R = lambda x: {"$round": [x, 2]}
        pipeline = [
            # 1) Filter to the day & valid blocks
            {
                "$match": {
                    "schedule_date": schedule_date,
                    "time_block": {"$gte": 1, "$lte": 96}
                }
            },

            # 2) Keep only needed fields + normalize dc_sg (treat SG/SH as SG; else DC)
            {
                "$project": {
                    "time_block": 1,
                    "plant_name": 1,   # swap to plant_id if available
                    "price_group": 1,
                    "c_s": 1,
                    "volume": {"$ifNull": ["$volume", 0]},
                    "_dcsg": {
                        "$let": {
                            "vars": {
                                "x": {
                                    "$toUpper": {
                                        "$trim": {"input": {"$ifNull": ["$dc_sg", "DC"]}}
                                    }
                                }
                            },
                            "in": {"$cond": [{"$in": ["$$x", ["SG", "SH"]]}, "SG", "DC"]}
                        }
                    }
                }
            },

            # 3) Aggregate per plant × block × price_group × c_s, split SG/DC
            {
                "$group": {
                    "_id": {
                        "time_block": "$time_block",
                        "plant_name": "$plant_name",  # or plant_id
                        "price_group": "$price_group",
                        "c_s": "$c_s"
                    },
                    "volSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, "$volume", 0]}},
                    "volDC": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "DC"]}, "$volume", 0]}}
                }
            },

            # 4) Choose per-plant volume: SG if present, else DC
            {
                "$project": {
                    "_id": 0,
                    "time_block": "$_id.time_block",
                    "price_group": "$_id.price_group",
                    "c_s": "$_id.c_s",
                    "chosenVol": {
                        "$cond": [
                            {"$gt": ["$volSG", 0]},
                            "$volSG",
                            "$volDC"
                        ]
                    }
                }
            },

            # 5) Sum chosen volumes to (block, price_group) TOTAL (central+state together)
            {
                "$group": {
                    "_id": {"time_block": "$time_block", "price_group": "$price_group"},
                    "volume": {"$sum": "$chosenVol"}
                }
            },

            # 6) Stable ordering (optional): by block, then price_group
            {"$sort": {"_id.time_block": 1, "_id.price_group": 1}},

            # 7) Reshape to one doc per block
            {
                "$group": {
                    "_id": "$_id.time_block",
                    "price_group_breakdown": {
                        "$push": {
                            "price_group": "$_id.price_group",
                            "volume": R("$volume")
                        }
                    }
                }
            },

            # (Optional) ensure all blocks 1..96 appear even if empty (MongoDB 5.1+):
            # {"$densify": {"field": "_id", "range": {"step": 1, "bounds": [1, 96]}}},
            # {"$addFields": {"price_group_breakdown": {"$ifNull": ["$price_group_breakdown", []]}}},

            # 8) Final shape & order
            # Attached demand AFTER you have one doc per block
            *serveSupplyServices._attach_demand_by_block(schedule_date),
            {"$project": {"_id": 0, "time_block": "$_id", "price_group_breakdown": 1, "comp_act_demand": R("$demand.comp_act_demand"), "comp_udm": R("$demand.comp_udm")}},
            {"$sort": {"time_block": 1}}
        ]

        result = self.supply_repo.aggregate(pipeline)
        return {"result": result, "last_updated_at": self.get_max_updated_at()}


    # -------------------------------------------------------------------------
    # Single-block plant list sorted by VC asc, with within-block cumulative
    # -------------------------------------------------------------------------
    def plants_list(
        self,
        schedule_date: Union[str, None] = None,
        time_block: Union[int, None] = None,
    ) -> Dict[str, Any]:
        """
        Return one block's plant list sorted by VC ascending, with chosen per-plant
        volume (SG priority: if SG/SH exists use SG, else DC) and a within-block
        cumulative sum in that order.

        Args:
            schedule_date: "YYYY-MM-DD" (IST). Defaults to today's IST date.
            time_block: 1..96. Defaults to current IST time block.

        Returns:
            {
              "result": [
                {
                  "time_block": <int>,
                  "plant_breakdown": [
                    {
                      "plant_name": <str>,
                      "c_s": "central" | "state",
                      "technology": <str>,
                      "vc": <float | null>,
                      "volume": <float>,               # chosen SG/DC for this plant in this block
                      "cumulative_volume": <float>     # running sum within block in VC asc order
                    }, ...
                  ]
                }
              ]
            }
        """
        # Defaults (IST)
        date_str = schedule_date or self._today_ist_str()
        block = time_block if time_block is not None else self._current_time_block_ist()

        # Validate inputs a bit
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=422, detail="schedule_date must be YYYY-MM-DD")

        if not (1 <= block <= 96):
            raise HTTPException(status_code=422, detail="time_block must be in the range 1..96")
        R = lambda x: {"$round": [x, 2]}
        pipeline = [
            # 1) Only the requested date + block
            {"$match": {"schedule_date": date_str, "time_block": block}},

            # 2) Keep fields + normalize dc_sg (treat SG/SH as SG; else DC)
            {
                "$project": {
                    "schedule_date": 1,
                    "time_block": 1,
                    "plant_name": 1,
                    "c_s": 1,
                    "technology": 1,
                    "VC_price": 1,
                    "volume": {"$ifNull": ["$volume", 0]},
                    "_dcsg": {
                        "$let": {
                            "vars": {
                                "x": {
                                    "$toUpper": {
                                        "$trim": {"input": {"$ifNull": ["$dc_sg", "DC"]}}
                                    }
                                }
                            },
                            "in": {"$cond": [{"$in": ["$$x", ["SG", "SH"]]}, "SG", "DC"]},
                        }
                    },
                }
            },

            # 3) Collapse duplicates per plant×block; collect SG/DC separately; choose VC policy
            {
                "$group": {
                    "_id": {
                        "time_block": "$time_block",
                        "plant_name": "$plant_name",
                        "c_s": "$c_s",
                        "technology": "$technology",
                    },
                    "volSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, "$volume", 0]}},
                    "volDC": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "DC"]}, "$volume", 0]}},
                    "vc": {"$max": "$VC_price"},  # or $first/$avg if you prefer
                }
            },

            # 4) Choose per-plant volume (SG if present else DC); add sort keys
            {
                "$project": {
                    "_id": 0,
                    "time_block": "$_id.time_block",
                    "plant_name": "$_id.plant_name",
                    "c_s": "$_id.c_s",
                    "technology": "$_id.technology",
                    "vc": "$vc",
                    "volume": R({"$cond": [{"$gt": ["$volSG", 0]}, "$volSG", "$volDC"]}),

                    # Sort keys: null VC last; tie-break by chosen volume, then name
                    "vc_sortKey": {"$ifNull": ["$vc", 9e15]},
                    "vol_sortKey": {
                        "$cond": [{"$gt": ["$volSG", 0]}, "$volSG", "$volDC"]
                    },
                }
            },

            # 5) Within-block cumulative by VC asc, then volume asc, then plant_name
            {
                "$setWindowFields": {
                    "partitionBy": "$time_block",
                    "sortBy": {"vc_sortKey": 1, "vol_sortKey": 1, "plant_name": 1},
                    "output": {
                        "cumulative_volume": {
                            "$sum": "$volume",
                            "window": {"documents": ["unbounded", "current"]},
                        }
                    },
                }
            },

            # 6) Final sort for display
            {"$sort": {"vc_sortKey": 1, "vol_sortKey": 1, "plant_name": 1}},

            # 7) Package the single-block result
            {
                "$group": {
                    "_id": "$time_block",
                    "plant_breakdown": {
                        "$push": {
                            "plant_name": "$plant_name",
                            "c_s": "$c_s",
                            "technology": "$technology",
                            "vc": R("$vc"),
                            "volume": R("$volume"),
                            "cumulative_volume":R( "$cumulative_volume"),
                        }
                    },
                }
            },
            *serveSupplyServices._attach_demand_by_block(date_str),
            {"$project": {"_id": 0, "time_block": "$_id", "plant_breakdown": 1, "comp_act_demand": R("$demand.comp_act_demand"), "comp_udm": R("$demand.comp_udm"),}},
        ]

        docs = self.supply_repo.aggregate(pipeline)
        last_updated_at = self.get_max_updated_at()
        # ensure stable shape even if nothing matched
        if not docs:
            return {"result": [{"time_block": block, "plant_breakdown": []}], "last_updated_at": last_updated_at}
        return {"result": docs, "last_updated_at": last_updated_at}

    # -------------------------------------------------------------------------
    # Generic reader for serve_supply_table with filtering, projection, sorting, pagination
    # ------------------------------------------------------------------------- 
    
    def supply_raw(
        self,
        # ranges (optional)
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,

        # generic field filters — each value can be a single value or a list (for $in)
        filters: Optional[Dict[str, Any]] = None,

        # projection (optional)
        include_fields: Optional[List[str]] = None,  # whitelist
        exclude_fields: Optional[List[str]] = None,  # blacklist

        # sorting + pagination
        sort: Optional[List[Tuple[str, int]]] = None,  # e.g. [("schedule_date", 1), ("time_block", -1)]
        skip: int = 0,
        limit: Optional[int] = None,  # None => no $limit (be careful for very large results)
    ) -> Dict[str, Any]:
        """
        Generic reader for serve_supply_table.

        Defaults:
        - no filters (returns everything),
        - projection: excludes `_id` and `updated_at` by default,
        - no sort (Mongo natural order),
        - pagination: `skip` applied if >0; `limit=None` means **no limit**.

        NOTE: Returning millions of rows as a list will be memory heavy.
            Prefer a streaming endpoint for huge exports.
        """
        match_and: List[Dict[str, Any]] = []

        # ---- Date range (schedule_date stored as 'YYYY-MM-DD' string) ----
        if date_from or date_to:
            dr: Dict[str, Any] = {}
            if date_from:
                dr["$gte"] = date_from
            if date_to:
                dr["$lte"] = date_to
            match_and.append({"schedule_date": dr})

        # ---- Time block range ----
        if from_block is not None or to_block is not None:
            tb: Dict[str, Any] = {}
            if from_block is not None:
                tb["$gte"] = int(from_block)
            if to_block is not None:
                tb["$lte"] = int(to_block)
            match_and.append({"time_block": tb})

        # ---- Field filters ----
        # Example:
        #   {
        #     "plant_id": ["KUDGI", "XYZ"],
        #     "c_s": "central",
        #     "technology": {"$in": ["Thermal", "Hydro"]},
        #     "VC_price": {"$gte": 1.0, "$lte": 4.5},
        #     "updated_at": {"$gte": "2025-09-22T00:00:00Z", "$lte": "2025-09-23T23:59:59Z"}
        #   }
        if filters:
            for k, v in filters.items():
                if v is None:
                    continue
                if isinstance(v, dict):
                    match_and.append({k: v})
                elif isinstance(v, (list, tuple, set)):
                    match_and.append({k: {"$in": list(v)}})
                else:
                    match_and.append({k: v})

        pipeline: List[Dict[str, Any]] = []

        if match_and:
            pipeline.append({"$match": {"$and": match_and}})

        # ---- Sort ----
        if sort:
            sort_stage = {field: (1 if int(direction) >= 0 else -1) for field, direction in sort}
            pipeline.append({"$sort": sort_stage})

        # ---- Projection ----
        # Always drop _id and updated_at to avoid BSON serialization issues and to match requirements
        if include_fields:
            proj = {f: 1 for f in include_fields}
            proj["_id"] = 0
            proj["updated_at"] = 0
            pipeline.append({"$project": proj})
        else:
            proj = {"_id": 0, "updated_at": 0}
            if exclude_fields:
                for f in exclude_fields:
                    proj[f] = 0
            pipeline.append({"$project": proj})

        # ---- Pagination ----
        if skip:
            pipeline.append({"$skip": int(skip)})
        # Only add $limit when explicitly provided and > 0
        if isinstance(limit, int) and limit > 0:
            pipeline.append({"$limit": int(limit)})

        docs = self.supply_repo.aggregate(pipeline)
        return {
            "result": docs,
            "meta": {
                "skip": skip,
                "limit": limit,            # None if no limit applied
                "returned": len(docs),     # number of docs materialized in this response
            },
        }


# Global Instances
serve_supply_services = serveSupplyServices()