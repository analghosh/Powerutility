
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta, date

try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Fallback for Python < 3.9
    from backports.zoneinfo import ZoneInfo

from .base_repository import GenericRepository
from ...core.config import settings

logger = logging.getLogger(__name__)

class SupplyRepository:
    """
    Repository for fetching supply data.
    """

    def __init__(self):
        self.repository = GenericRepository()
        self.supply_serve = settings.MONGODB_SUPPLY_SERVE_COLLECTION
        self.plant_master = settings.MONGODB_PLANT_MASTER_COLLECTION
        self.demand_serve = settings.MONGODB_DEMAND_SERVE_COLLECTION
        self.price_group = settings.MONGODB_PRICE_GROUP_COLLECTION

    @staticmethod
    def _attach_demand_by_block(schedule_date: str, demand_coll: str):
        """
        Attach demand data by block with type condition:
        - If schedule_date is tomorrow: match type="DAF"
        - Otherwise: match type="IDF"
        """
        # Calculate tomorrow's date in IST timezone
        ist = ZoneInfo("Asia/Kolkata")
        today_ist = datetime.now(ist).date()
        tomorrow_ist = today_ist + timedelta(days=1)
        
        # Parse schedule_date to compare
        try:
            schedule_date_obj = datetime.strptime(schedule_date, "%Y-%m-%d").date()
            is_tomorrow = schedule_date_obj == tomorrow_ist
        except (ValueError, TypeError):
            # If date parsing fails, default to IDF
            is_tomorrow = False
        
        # Determine type based on whether schedule_date is tomorrow
        demand_type = "DAF" if is_tomorrow else "IDF"
        
        return [
            {
                "$lookup": {
                    "from": demand_coll,
                    "let": {"blk": "$_id", "sd": schedule_date},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": [{"$toInt": "$time_block"}, {"$toInt": "$$blk"}]},
                                        {
                                            "$eq": [
                                                "$scheduled_at",
                                                {
                                                    "$dateFromString": {
                                                        "dateString": {"$concat": ["$$sd", "T00:00:00.000Z"]},
                                                        "timezone": "UTC"
                                                    }
                                                }
                                            ]
                                        },
                                        {"$eq": ["$type", demand_type]}
                                    ]
                                }
                            }
                        },
                        {
                            "$project": {
                                "_id": 0,
                                "comp_act_demand": {"$round": ["$comp_act_demand", 2]},
                                "comp_udm": {"$round": ["$comp_udm", 2]},
                            }
                        }
                    ],
                    "as": "demand"
                }
            },
            {"$set": {"demand": {"$first": "$demand"}}}
        ]

    async def get_max_updated_at_from_supply(self, schedule_date_str: str) -> Optional[datetime]:
        """Get the maximum updated_at timestamp for a given schedule date."""
        try:
            start_date = datetime.fromisoformat(schedule_date_str + "T00:00:00+00:00")
            end_date = start_date + timedelta(days=1)
            
            pipeline = [
                {"$match": {"dc_sg": "SG", "scheduled_at": {"$gte": start_date, "$lt": end_date}}},
                {"$group": {"_id": None, "max_updated": {"$max": "$updated_at"}}},
                {"$project": {"_id": 0, "max_updated": 1}}
            ]
            
            result = await self.repository.aggregate_async(self.supply_serve, pipeline)
            if result and len(result) > 0 and "max_updated" in result[0]:
                return result[0]["max_updated"]
            return None
        except Exception as e:
            logger.error(f"[SupplyRepository] Error getting max updated_at: {str(e)}")
            return None

    async def get_price_group_definitions(self) -> Dict[str, Any]:
        pipeline = [
            {
                "$project": {
                    "_id": 0,
                    "inserted_date": 0
                }
            },
        ]
        result = await self.repository.aggregate_async(self.price_group, pipeline)
        return {"result": result}

    async def get_supply_raw(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
        include_fields: Optional[List[str]] = None,
        exclude_fields: Optional[List[str]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        skip: int = 0,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generic reader for raw supply data with joins and dynamic filtering."""
        pipeline: List[Dict[str, Any]] = []

        # Start with the lookup and initial projection
        pipeline.extend([
            {
                "$lookup": {
                    "from": self.plant_master,
                    "localField": "plant_id",
                    "foreignField": "plant_id",
                    "as": "plant_details"
                }
            },
            {"$unwind": "$plant_details"},
            {
                "$project": {
                    "_id": 0,
                    "schedule_date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$scheduled_at"}},
                    "time_block": "$time_block",
                    "plant_id": "$plant_id",
                    "plant_name": "$plant_details.plant_name",
                    "c_s": "$plant_details.c_s",
                    "category": "$plant_details.category",
                    "technology": "$plant_details.technology",
                    "dc_sg": "$dc_sg",
                    "VC_price": "$plant_details.vc_price",
                    "volume": "$volume",
                    "price_group": "$plant_details.price_group",
                    "updated_at": "$updated_at"
                }
            }
        ])

        match_and: List[Dict[str, Any]] = []
        if date_from or date_to:
            dr: Dict[str, Any] = {}
            if date_from: dr["$gte"] = date_from
            if date_to: dr["$lte"] = date_to
            match_and.append({"schedule_date": dr})

        if from_block is not None or to_block is not None:
            tb: Dict[str, Any] = {}
            if from_block is not None: tb["$gte"] = int(from_block)
            if to_block is not None: tb["$lte"] = int(to_block)
            match_and.append({"time_block": tb})

        if filters:
            for k, v in filters.items():
                if v is None: continue
                if isinstance(v, dict):
                    match_and.append({k: v})
                elif isinstance(v, (list, tuple, set)):
                    match_and.append({k: {"$in": list(v)}})
                else:
                    match_and.append({k: v})

        if match_and:
            pipeline.append({"$match": {"$and": match_and}})

        if sort:
            sort_stage = {field: (1 if int(direction) >= 0 else -1) for field, direction in sort}
            pipeline.append({"$sort": sort_stage})

        if include_fields or exclude_fields:
            proj = {}
            if include_fields:
                proj = {f: 1 for f in include_fields}
            if exclude_fields:
                proj.update({f: 0 for f in exclude_fields})
            proj["_id"] = 0
            pipeline.append({"$project": proj})

        if skip > 0:
            pipeline.append({"$skip": int(skip)})
        if isinstance(limit, int) and limit > 0:
            pipeline.append({"$limit": int(limit)})

        docs = await self.repository.aggregate_async(self.supply_serve, pipeline)
        return {
            "result": docs,
            "meta": {"skip": skip, "limit": limit, "returned": len(docs)},
        }

    async def get_supply_by_category_graph(self, schedule_date_str: str) -> Dict[str, Any]:
        R = lambda x: {"$round": [x, 2]}
        start_date = datetime.fromisoformat(schedule_date_str + "T00:00:00+00:00")
        end_date = start_date + timedelta(days=1)

        pipeline = [
            {"$match": {"dc_sg": "SG", "scheduled_at": {"$gte": start_date, "$lt": end_date}, "time_block": {"$gte": 1, "$lte": 96}}},
            {
                "$lookup": {
                    "from": self.plant_master,
                    "localField": "plant_id",
                    "foreignField": "plant_id",
                    "as": "plant_details"
                }
            },
            {"$unwind": "$plant_details"},
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
            {"$group": {
                "_id": {
                    "time_block": "$time_block",
                    "c_s": "$plant_details.c_s",
                    "category": "$plant_details.category",
                    "dc_sg": "$_dcsg"
                },
                "vol": {"$sum": {"$ifNull": ["$volume", 0]}}
            }},
            {"$group": {
                "_id": {
                    "time_block": "$_id.time_block",
                    "c_s": "$_id.c_s",
                    "category": "$_id.category"
                },
                "volSG": {"$sum": {"$cond": [{"$eq": ["$_id.dc_sg", "SG"]}, "$vol", 0]}},
                "volDC": {"$sum": {"$cond": [{"$eq": ["$_id.dc_sg", "DC"]}, "$vol", 0]}},
                "countSG": {"$sum": {"$cond": [{"$eq": ["$_id.dc_sg", "SG"]}, 1, 0]}}
            }},
            {"$project": {
                "_id": 0,
                "time_block": "$_id.time_block",
                "c_s": "$_id.c_s",
                "category": "$_id.category",
                "volume": R({"$cond": [{"$gt": ["$countSG", 0]}, "$volSG", "$volDC"]})
            }},
            {"$addFields": {"c_s_order": {"$cond": [{"$eq": ["$c_s", "central"]}, 0, 1]}}},
            {"$sort": {"time_block": 1, "c_s_order": 1, "category": 1}},
            {"$group": {
                "_id": {"time_block": "$time_block", "c_s": "$c_s"},
                "categories": {"$push": {"category": "$category", "volume": "$volume"}}
            }},
            {"$addFields": {"c_s_order": {"$cond": [{"$eq": ["$_id.c_s", "central"]}, 0, 1]}}},
            {"$sort": {"_id.time_block": 1, "c_s_order": 1}},
            {"$group": {
                "_id": "$_id.time_block",
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
            *self._attach_demand_by_block(schedule_date_str, self.demand_serve),
            {"$project": {
                "_id": 0,
                "block": "$_id",
                "byCategory": 1,
                "comp_act_demand": R("$demand.comp_act_demand"),
                "comp_udm": R("$demand.comp_udm"),
            }},
            {"$sort": {"block": 1}},
        ]
        result = await self.repository.aggregate_async(self.supply_serve, pipeline)
        return {"result": result}

    async def get_supply_by_technology_graph(self, schedule_date_str: str) -> Dict[str, Any]:
        R = lambda x: {"$round": [x, 2]}
        start_date = datetime.fromisoformat(schedule_date_str + "T00:00:00+00:00")
        end_date = start_date + timedelta(days=1)

        pipeline = [
            {"$match": { "dc_sg": "SG", "scheduled_at": {"$gte": start_date, "$lt": end_date}, "time_block": {"$gte": 1, "$lte": 96}}},
            {
                "$lookup": {
                    "from": self.plant_master,
                    "localField": "plant_id",
                    "foreignField": "plant_id",
                    "as": "plant_details"
                }
            },
            {"$unwind": "$plant_details"},
            {"$project": {
                "time_block": 1,
                "plant_name": "$plant_details.plant_name",
                "technology": "$plant_details.technology",
                "c_s": "$plant_details.c_s",
                "volume": {"$ifNull": ["$volume", 0]},
                "_dcsg": {
                    "$cond": [
                        {"$eq": [{"$toUpper": {"$trim": {"input": {"$ifNull": ["$dc_sg", "DC"]}}}}, "SG"]},
                        "SG",
                        "DC"
                    ]
                }
            }},
            {"$group": {
                "_id": {
                    "time_block": "$time_block",
                    "plant_name": "$plant_name",
                    "technology": "$technology",
                    "c_s": "$c_s"
                },
                "volSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, "$volume", 0]}},
                "volDC": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "DC"]}, "$volume", 0]}},
                "countSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, 1, 0]}}
            }},
            {"$project": {
                "_id": 0,
                "time_block": "$_id.time_block",
                "technology": "$_id.technology",
                "c_s": "$_id.c_s",
                "chosenVol": {"$cond": [{"$gt": ["$countSG", 0]}, "$volSG", "$volDC"]}
            }},
            {"$group": {
                "_id": {"time_block": "$time_block", "technology": "$technology"},
                "volume": {"$sum": "$chosenVol"}
            }},
            {"$group": {
                "_id": "$_id.time_block",
                "technology_breakdown": {
                    "$push": {
                        "technology": "$_id.technology",
                        "volume": R("$volume")
                    }
                }
            }},
            *self._attach_demand_by_block(schedule_date_str, self.demand_serve),
            {"$project": {"_id": 0, "time_block": "$_id", "technology_breakdown": 1, "demand": 1}},
            {"$sort": {"time_block": 1}}
        ]
        result = await self.repository.aggregate_async(self.supply_serve, pipeline)
        return {"result": result}

    async def get_supply_by_price_group_graph(self, schedule_date_str: str) -> Dict[str, Any]:
        R = lambda x: {"$round": [x, 2]}
        start_date = datetime.fromisoformat(schedule_date_str + "T00:00:00+00:00")
        end_date = start_date + timedelta(days=1)

        pipeline = [
            {"$match": {"dc_sg": "SG", "scheduled_at": {"$gte": start_date, "$lt": end_date}, "time_block": {"$gte": 1, "$lte": 96}}},
            {
                "$lookup": {
                    "from": self.plant_master,
                    "localField": "plant_id",
                    "foreignField": "plant_id",
                    "as": "plant_details"
                }
            },
            {"$unwind": "$plant_details"},
            {"$project": {
                "time_block": 1,
                "plant_name": "$plant_details.plant_name",
                "price_group": "$plant_details.price_group",
                "c_s": "$plant_details.c_s",
                "volume": {"$ifNull": ["$volume", 0]},
                "_dcsg": {
                    "$let": {
                        "vars": {"x": {"$toUpper": {"$trim": {"input": {"$ifNull": ["$dc_sg", "DC"]}}}}},
                        "in": {"$cond": [{"$in": ["$$x", ["SG", "SH"]]}, "SG", "DC"]}
                    }
                }
            }},
            {"$group": {
                "_id": {
                    "time_block": "$time_block",
                    "plant_name": "$plant_name",
                    "price_group": "$price_group",
                    "c_s": "$c_s"
                },
                "volSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, "$volume", 0]}},
                "volDC": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "DC"]}, "$volume", 0]}},
                "countSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, 1, 0]}}
            }},
            {"$project": {
                "_id": 0,
                "time_block": "$_id.time_block",
                "price_group": "$_id.price_group",
                "c_s": "$_id.c_s",
                "chosenVol": {"$cond": [{"$gt": ["$countSG", 0]}, "$volSG", "$volDC"]}
            }},
            {"$group": {
                "_id": {"time_block": "$time_block", "price_group": "$price_group"},
                "volume": {"$sum": "$chosenVol"}
            }},
            {"$sort": {"_id.time_block": 1, "_id.price_group": 1}},
            {"$group": {
                "_id": "$_id.time_block",
                "price_group_breakdown": {
                    "$push": {
                        "price_group": "$_id.price_group",
                        "volume": R("$volume")
                    }
                }
            }},
            *self._attach_demand_by_block(schedule_date_str, self.demand_serve),
            {"$project": {"_id": 0, "time_block": "$_id", "price_group_breakdown": 1, "comp_act_demand": R("$demand.comp_act_demand"), "comp_udm": R("$demand.comp_udm")}},
            {"$sort": {"time_block": 1}}
        ]
        result = await self.repository.aggregate_async(self.supply_serve, pipeline)
        return {"result": result}

    async def get_supply_graph_bundle(self, schedule_date_str: str) -> Dict[str, Any]:
        # This method will be orchestrated by the service layer
        return {}

    async def get_supply_by_category_block(self, schedule_date_str: str, time_block: int) -> Dict[str, Any]:
        R = lambda x: {"$round": [x, 2]}
        start_date = datetime.fromisoformat(schedule_date_str + "T00:00:00+00:00")
        end_date = start_date + timedelta(days=1)

        pipeline = [
            {"$match": {"dc_sg": "SG", "scheduled_at": {"$gte": start_date, "$lt": end_date}, "time_block": time_block}},
            {
                "$lookup": {
                    "from": self.plant_master,
                    "localField": "plant_id",
                    "foreignField": "plant_id",
                    "as": "plant_details"
                }
            },
            {"$unwind": "$plant_details"},
            {
                "$project": {
                    "time_block": 1,
                    "category": "$plant_details.category",
                    "c_s": "$plant_details.c_s",
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
            {
                "$group": {
                    "_id": {
                        "time_block": "$time_block",
                        "category": "$category",
                        "c_s": "$c_s"
                    },
                    "volSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, "$volume", 0]}},
                    "volDC": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "DC"]}, "$volume", 0]}},
                    "countSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, 1, 0]}},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "time_block": "$_id.time_block",
                    "category": {"$ifNull": ["$_id.category", "Uncategorized"]},
                    "c_s": "$_id.c_s",
                    "chosenVol": R({"$cond": [{"$gt": ["$countSG", 0]}, "$volSG", "$volDC"]})
                }
            },
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

        result = await self.repository.aggregate_async(self.supply_serve, pipeline)
        if not result:
            return {"result": [{"time_block": time_block, "category_breakdown": []}]}
        return {"result": result}

    async def get_supply_by_technology_block(self, schedule_date_str: str, time_block: int) -> Dict[str, Any]:
        R = lambda x: {"$round": [x, 2]}
        start_date = datetime.fromisoformat(schedule_date_str + "T00:00:00+00:00")
        end_date = start_date + timedelta(days=1)

        pipeline = [
            {"$match": {"dc_sg": "SG", "scheduled_at": {"$gte": start_date, "$lt": end_date}, "time_block": time_block}},
            {
                "$lookup": {
                    "from": self.plant_master,
                    "localField": "plant_id",
                    "foreignField": "plant_id",
                    "as": "plant_details"
                }
            },
            {"$unwind": "$plant_details"},
            {"$project": {
                "time_block": 1, "plant_name": "$plant_details.plant_name", "technology": "$plant_details.technology", "c_s": "$plant_details.c_s",
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
                "volDC": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "DC"]}, "$volume", 0]}},
                "countSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, 1, 0]}}
            }},
            {"$project": {
                "_id": 0,
                "time_block": "$_id.time_block",
                "technology": "$_id.technology",
                "c_s": "$_id.c_s",
                "chosenVol": R({"$cond": [{"$gt": ["$countSG", 0]}, "$volSG", "$volDC"]})
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

        result = await self.repository.aggregate_async(self.supply_serve, pipeline)
        if not result:
            return {"result": [{"time_block": time_block, "technology_breakdown": []}]}
        return {"result": result}

    async def get_supply_by_price_group_block(self, schedule_date_str: str, time_block: int) -> Dict[str, Any]:
        R = lambda x: {"$round": [x, 2]}
        start_date = datetime.fromisoformat(schedule_date_str + "T00:00:00+00:00")
        end_date = start_date + timedelta(days=1)

        pipeline = [
            {"$match": {"dc_sg": "SG", "scheduled_at": {"$gte": start_date, "$lt": end_date}, "time_block": time_block}},
            {
                "$lookup": {
                    "from": self.plant_master,
                    "localField": "plant_id",
                    "foreignField": "plant_id",
                    "as": "plant_details"
                }
            },
            {"$unwind": "$plant_details"},
            {"$project": {
                "time_block": 1, "plant_name": "$plant_details.plant_name", "price_group": "$plant_details.price_group", "c_s": "$plant_details.c_s",
                "volume": {"$ifNull": ["$volume", 0]},
                "_dcsg": {
                    "$let": {
                        "vars": {"x": {"$toUpper": {"$trim": {"input": {"$ifNull": ["$dc_sg", "DC"]}}}}},
                        "in": {"$cond": [{"$in": ["$$x", ["SG", "SH"]]}, "SG", "DC"]}
                    }
                }
            }},
            {"$group": {
                "_id": {
                    "time_block": "$time_block",
                    "plant_name": "$plant_name",
                    "price_group": "$price_group",
                    "c_s": "$c_s"
                },
                "volSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, "$volume", 0]}},
                "volDC": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "DC"]}, "$volume", 0]}},
                "countSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, 1, 0]}}
            }},
            {"$project": {
                "_id": 0,
                "time_block": "$_id.time_block",
                "price_group": "$_id.price_group",
                "c_s": "$_id.c_s",
                "chosenVol": R({"$cond": [{"$gt": ["$countSG", 0]}, "$volSG", "$volDC"]})
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
            *self._attach_demand_by_block(schedule_date_str, self.demand_serve),
            {"$project": {
                "_id": 0,
                "time_block": "$_id",
                "price_group_breakdown": 1,
            }},
            {"$sort": {"time_block": 1}},
        ]

        result = await self.repository.aggregate_async(self.supply_serve, pipeline)
        if not result:
            return {"result": [{"time_block": time_block, "price_group_breakdown": []}]}
        return {"result": result}

    async def get_plants_list_block(self, schedule_date_str: str, time_block: int) -> Dict[str, Any]:
        R = lambda x: {"$round": [x, 2]}
        start_date = datetime.fromisoformat(schedule_date_str + "T00:00:00+00:00")
        end_date = start_date + timedelta(days=1)

        pipeline = [
            {"$match": {"dc_sg": "SG", "scheduled_at": {"$gte": start_date, "$lt": end_date}, "time_block": time_block}},
            {
                "$lookup": {
                    "from": self.plant_master,
                    "localField": "plant_id",
                    "foreignField": "plant_id",
                    "as": "plant_details"
                }
            },
            {"$unwind": "$plant_details"},
            {
                "$project": {
                    "time_block": 1,
                    "plant_id": "$plant_details.plant_id",
                    "plant_name": "$plant_details.plant_name",
                    "c_s": "$plant_details.c_s",
                    "technology": "$plant_details.technology",
                    "VC_price": "$plant_details.vc_price",
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
            {
                "$group": {
                    "_id": {
                        "time_block": "$time_block",
                        "plant_id": "$plant_id",
                        "plant_name": "$plant_name",
                        "c_s": "$c_s",
                        "technology": "$technology",
                    },
                    "volSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, "$volume", 0]}},
                    "volDC": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "DC"]}, "$volume", 0]}},
                    "countSG": {"$sum": {"$cond": [{"$eq": ["$_dcsg", "SG"]}, 1, 0]}},
                    "vc": {"$first": "$VC_price"},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "time_block": "$_id.time_block",
                    "plant_id": "$_id.plant_id",
                    "plant_name": "$_id.plant_name",
                    "c_s": "$_id.c_s",
                    "technology": "$_id.technology",
                    "vc": "$vc",
                    "volume": R({"$cond": [{"$gt": ["$countSG", 0]}, "$volSG", "$volDC"]}),
                    "vc_sortKey": {"$ifNull": ["$vc", 9e15]},
                    "vol_sortKey": {
                        "$cond": [{"$gt": ["$countSG", 0]}, "$volSG", "$volDC"]
                    },
                }
            },
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
            {"$sort": {"vc_sortKey": 1, "vol_sortKey": 1, "plant_name": 1}},
            {
                "$group": {
                    "_id": "$time_block",
                    "plant_breakdown": {
                        "$push": {
                            "plant_id": "$plant_id",
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
            *self._attach_demand_by_block(schedule_date_str, self.demand_serve),
            {"$project": {"_id": 0, "time_block": "$_id", "plant_breakdown": 1, "comp_act_demand": R("$demand.comp_act_demand"), "comp_udm": R("$demand.comp_udm"),}},
        ]

        docs = await self.repository.aggregate_async(self.supply_serve, pipeline)
        if not docs:
            return {"result": [{"time_block": time_block, "plant_breakdown": []}]}
        return {"result": docs}

    async def get_supply_block_bundle(self, schedule_date_str: str, time_block: int) -> Dict[str, Any]:
        # This method will be orchestrated by the service layer
        return {}

# Global instance
supply_repository = SupplyRepository()
