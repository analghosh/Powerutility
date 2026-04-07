# # services/serve_demand_services.py

# import logging
# from typing import List, Dict, Any, Optional, Tuple
# from ..repository.serve_demand_table_repository import DashboardRepository as serveDemandTableRepository

# logger = logging.getLogger(__name__)

# class serveDemandServices:
#     """
#     Mirrors serveSupplyServices.supply_raw but for the 'serve_demand_table'.
#     """
#     def __init__(self, collection_name: str = "serve_demand_table"):
#         self.repo = serveDemandTableRepository(collection_name=collection_name)

#     def demand_raw(
#         self,
#         # ranges (optional)
#         date_from: Optional[str] = None,
#         date_to: Optional[str] = None,
#         from_block: Optional[int] = None,
#         to_block: Optional[int] = None,

#         # generic field filters — each value can be a single value or a list (for $in)
#         filters: Optional[Dict[str, Any]] = None,

#         # projection (optional)
#         include_fields: Optional[List[str]] = None,
#         exclude_fields: Optional[List[str]] = None,

#         # sorting + pagination
#         sort: Optional[List[Tuple[str, int]]] = None,
#         skip: int = 0,
#         limit: Optional[int] = None,   # None => no $limit
#     ) -> Dict[str, Any]:
#         """
#         Generic reader for serve_demand_table, identical behavior to supply_raw:
#         - optional date/time_block ranges
#         - flexible field filters (single value, list -> $in, or operator dict)
#         - projection include/exclude (always drops _id and updated_at)
#         - sort, skip, limit
#         """
#         match_and: List[Dict[str, Any]] = []

#         # ---- Date range ----
#         if date_from or date_to:
#             dr: Dict[str, Any] = {}
#             if date_from:
#                 dr["$gte"] = date_from
#             if date_to:
#                 dr["$lte"] = date_to
#             match_and.append({"schedule_date": dr})

#         # ---- Time block range ----
#         if from_block is not None or to_block is not None:
#             tb: Dict[str, Any] = {}
#             if from_block is not None:
#                 tb["$gte"] = int(from_block)
#             if to_block is not None:
#                 tb["$lte"] = int(to_block)
#             match_and.append({"time_block": tb})

#         # ---- Field filters ----
#         if filters:
#             for k, v in filters.items():
#                 if v is None:
#                     continue
#                 if isinstance(v, dict):
#                     match_and.append({k: v})
#                 elif isinstance(v, (list, tuple, set)):
#                     match_and.append({k: {"$in": list(v)}})
#                 else:
#                     match_and.append({k: v})

#         pipeline: List[Dict[str, Any]] = []
#         if match_and:
#             pipeline.append({"$match": {"$and": match_and}})

#         # ---- Sort ----
#         if sort:
#             sort_stage = {field: (1 if int(direction) >= 0 else -1) for field, direction in sort}
#             pipeline.append({"$sort": sort_stage})

#         # ---- Projection ----
#         # Always drop _id and updated_at (align with supply_raw)
#         if include_fields:
#             proj = {f: 1 for f in include_fields}
#             proj["_id"] = 0
#             proj["updated_at"] = 0
#             pipeline.append({"$project": proj})
#         else:
#             proj = {"_id": 0, "updated_at": 0}
#             if exclude_fields:
#                 for f in exclude_fields:
#                     proj[f] = 0
#             pipeline.append({"$project": proj})

#         # ---- Pagination ----
#         if skip:
#             pipeline.append({"$skip": int(skip)})
#         if isinstance(limit, int) and limit > 0:
#             pipeline.append({"$limit": int(limit)})

#         docs = self.repo.aggregate(pipeline)
#         return {
#             "result": docs,
#             "meta": {
#                 "skip": skip,
#                 "limit": limit,
#                 "returned": len(docs),
#             },
#         }

# # Global instance
# serve_demand_services = serveDemandServices()

# services/serve_demand_services.py

import logging
from typing import List, Dict, Any, Optional, Tuple
from ..repository.serve_demand_table_repository import DashboardRepository as serveDemandTableRepository

logger = logging.getLogger(__name__)

# Map ALL public (lowercase) field names to actual DB field names
FIELD_MAP: Dict[str, str] = {
    # core
    "time_block": "time_block",
    "schedule_date": "schedule_date",
    "type": "type",
    "zone": "zone",
    "updated_at": "updated_at",
    # demand metrics (DB is mixed case)
    "actual": "Actual",
    "drawal": "Drawal",
    "mape": "MAPE",
    "mpe": "MPE",
    "raw_frequency": "Raw_Frequency",
    "schedule": "Schedule",
    "comp_act_demand": "comp_act_demand",
    "comp_udm": "comp_udm",
    "dsm_rate": "dsm_rate",
    "forecast": "forecast",
    "forecasted_frequency": "forecasted_frequency",
    "net_dsm_amount": "net_dsm_amount",
    "rostering": "rostering",
}

def to_db_field(name: str) -> str:
    """Translate public (lowercase) field -> actual DB field."""
    return FIELD_MAP.get(name.lower(), name)

def lower_keys(obj: Any) -> Any:
    """Recursively lowercase dict keys in results."""
    if isinstance(obj, dict):
        return { (k.lower() if isinstance(k, str) else k): lower_keys(v) for k, v in obj.items() }
    if isinstance(obj, list):
        return [lower_keys(x) for x in obj]
    return obj


class serveDemandServices:
    def __init__(self, collection_name: str = "serve_demand_table"):
        self.repo = serveDemandTableRepository(collection_name=collection_name)

    def demand_raw(
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
        match_and: List[Dict[str, Any]] = []

        # ---- Date range (public names are lowercase)
        if date_from or date_to:
            dr: Dict[str, Any] = {}
            if date_from: dr["$gte"] = date_from
            if date_to:   dr["$lte"] = date_to
            match_and.append({to_db_field("schedule_date"): dr})

        # ---- Time block range
        if from_block is not None or to_block is not None:
            tb: Dict[str, Any] = {}
            if from_block is not None: tb["$gte"] = int(from_block)
            if to_block   is not None: tb["$lte"] = int(to_block)
            match_and.append({to_db_field("time_block"): tb})

        # ---- Field filters (translate keys to DB fields)
        if filters:
            for k, v in filters.items():
                db_k = to_db_field(k)
                if v is None:
                    continue
                if isinstance(v, dict):
                    match_and.append({db_k: v})
                elif isinstance(v, (list, tuple, set)):
                    match_and.append({db_k: {"$in": list(v)}})
                else:
                    match_and.append({db_k: v})

        pipeline: List[Dict[str, Any]] = []
        if match_and:
            pipeline.append({"$match": {"$and": match_and}})

        # ---- Sort (translate field names)
        if sort:
            sort_stage = {to_db_field(field): (1 if int(direction) >= 0 else -1)
                          for field, direction in sort}
            pipeline.append({"$sort": sort_stage})

        # ---- Projection (translate field names)
        if include_fields:
            proj = {to_db_field(f): 1 for f in include_fields}
            proj["_id"] = 0
            proj["updated_at"] = 0   # always drop from output
            pipeline.append({"$project": proj})
        else:
            proj = {"_id": 0, "updated_at": 0}
            if exclude_fields:
                for f in exclude_fields:
                    proj[to_db_field(f)] = 0
            pipeline.append({"$project": proj})

        # ---- Pagination
        if skip:
            pipeline.append({"$skip": int(skip)})
        if isinstance(limit, int) and limit > 0:
            pipeline.append({"$limit": int(limit)})

        docs = self.repo.aggregate(pipeline)

        # 🔻 enforce lowercase keys for the entire response payload
        docs_lc = lower_keys(docs)

        return {
            "result": docs_lc,
            "meta": {
                "skip": skip,
                "limit": limit,
                "returned": len(docs_lc),
            },
        }

# Global instance
serve_demand_services = serveDemandServices()
