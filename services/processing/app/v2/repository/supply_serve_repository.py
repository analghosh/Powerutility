"""
Supply Serve Repository for v2 RTM Scheduling
Uses supply_serve collection with proper field mappings
"""

from typing import List, Dict, Optional, Any
from .base_repository import GenericRepository
from ...core.config import settings
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)



class SupplyServeRepository(GenericRepository):
    """
    Repository for supply_serve collection using v2 pattern.
    Field mappings:
    - schedule_date (old) → scheduled_at.$date (new)
    - VC_price (old) → vc_price (new)
    - plant_name, category, technology, c_s → from plant_master lookup
    - dc_sg → dc_sg (same)
    - volume → volume (same)
    """
    
    def __init__(self):
        super().__init__()
        self.collection_name = settings.MONGODB_SUPPLY_SERVE_COLLECTION if hasattr(settings, 'MONGODB_SUPPLY_SERVE_COLLECTION') else "supply_serve"
        logger.info(f"SupplyServeRepository initialized with collection: {self.collection_name}")

    
    @property
    def collection(self):
        """Get the collection object for direct access"""
        return self.db[self.collection_name]

    async def get_supply_by_date_range(
        self,
        schedule_date: str,
        dc_sg: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get supply data for a date range with field mappings.
        
        Args:
            schedule_date: Date in YYYY-MM-DD format (will match scheduled_at.$date)
            dc_sg: Optional filter for DC/SG type
            
        Returns:
            List of supply records with mapped fields
        """
        try:
            # Convert date string to datetime for scheduled_at matching
            target_date = datetime.strptime(schedule_date, "%Y-%m-%d")
            end_date = target_date + timedelta(days=1)
            
            pipeline = [
                {
                    "$match": {
                        "scheduled_at": {"$gte": target_date, "$lt": end_date}
                    }
                }
            ]
            
            if dc_sg:
                pipeline[0]["$match"]["dc_sg"] = dc_sg
            
            # Lookup plant_master for plant_name, category, technology, c_s
            pipeline.extend([
                {
                    "$lookup": {
                        "from": "plant_master",
                        "localField": "plant_id",
                        "foreignField": "plant_id",
                        "as": "plant_details"
                    }
                },
                {"$unwind": {"path": "$plant_details", "preserveNullAndEmptyArrays": True}},
                {
                    "$project": {
                        "_id": 0,
                        "scheduled_at": 1,
                        # Map scheduled_at.$date to schedule_date for compatibility
                        "schedule_date": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$scheduled_at"
                            }
                        },
                        "time_block": 1,
                        "plant_id": 1,
                        "source_revision_number": "$source_revision_number",
                        "plant_name": "$plant_details.plant_name",  # From plant_master
                        "dc_sg": 1,
                        "volume": 1,
                        "VC_Price": "$vc_price",  # Map vc_price to VC_Price for compatibility
                        "vc_price": "$vc_price",  # Also keep standard field
                        "category": "$plant_details.category",  # From plant_master
                        "technology": "$plant_details.technology",  # From plant_master
                        "c_s": "$plant_details.c_s",  # From plant_master
                        "px_price": "$px_price",  # New field in supply_serve
                        "fc_price": "$fc_price",  # New field in supply_serve
                        "ppa": "$ppa",
                        "dam_tm": "$dam_tm",  # DAM TM value (55% of DC volume)
                        "rtm_tm": "$rtm_tm",  # RTM TM value (55% of DC volume)
                        "multiplier": "$multiplier",  # New field in supply_serve
                        "price_group": "$price_group",  # New field in supply_serve
                        "updated_flag": "$updated_flag",
                        "source_sg": "$source_sg",
                    }
                }
            ])
            
            return await super().aggregate(self.collection_name, pipeline)
        except Exception as e:
            logger.error(f"Failed to get supply by date range: {str(e)}")
            return []

    async def get_first_sg_source_revision_for_date(self, schedule_date: str) -> Optional[int]:
        """
        First SG row's source_revision_number for a calendar day (minimal IO, no plant_master lookup).
        Matches _ensure_capture_serve use of serve_rows[0] for RLDC revision hint.
        """
        try:
            target_date = datetime.strptime(schedule_date, "%Y-%m-%d")
            end_date = target_date + timedelta(days=1)
            pipeline = [
                {
                    "$match": {
                        "scheduled_at": {"$gte": target_date, "$lt": end_date},
                        "dc_sg": "SG",
                    }
                },
                {"$limit": 1},
                {"$project": {"_id": 0, "source_revision_number": 1}},
            ]
            rows = await self.aggregate_async(pipeline)
            if not rows:
                return None
            raw = rows[0].get("source_revision_number")
            if raw in (None, ""):
                return None
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None
        except Exception as e:
            logger.error("get_first_sg_source_revision_for_date failed: %s", e)
            return None

    def ensure_performance_indexes(self) -> None:
        """Compound index for day + dc_sg queries (idempotent, background)."""
        try:
            self.collection.create_index(
                [("dc_sg", 1), ("scheduled_at", 1)],
                name="dc_sg_scheduled_at_1",
                background=True,
            )
        except Exception as e:
            logger.warning("supply_serve index ensure skipped: %s", e)

    async def get_supply_power_optimization(self, schedule_date: str) -> List[Dict[str, Any]]:
        """
        Dedicated method for Power Optimization API with historical fallback.
        """
        try:
            target_date = datetime.strptime(schedule_date, "%Y-%m-%d")
            end_date = target_date + timedelta(days=1)
            # EXCLUDED_PLANTS = {"OA_PX", "OA_GNA", "OA_TGNA", "URS"}

            
            # Full pipeline with $lookup for main collection
            pipeline = [
                # Stage 1: Filter by date (should use index on scheduled_at)
                {
                    "$match": {
                        "scheduled_at": {"$gte": target_date, "$lt": end_date},
                        # "plant_id": {"$nin": list(EXCLUDED_PLANTS)}  # Filter excluded plants early
                        "dc_sg": "SG"  # Only SG for power optimization
                    }
                },
                # Stage 2: Conditional lookup - only if tm is missing or null
                {
                    "$lookup": {
                        "from": "plant_master",
                        "let": {"plant_id": "$plant_id"},
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {"$eq": ["$plant_id", "$$plant_id"]}
                                }
                            },
                            {
                                "$project": {
                                    "dam_tm": 1,  # Only fetch dam_tm field
                                    "rtm_tm": 1   # Only fetch rtm_tm field
                                }
                            }
                        ],
                        "as": "plant_tm"
                    }
                },
                # Stage 3: Add tm fields - use from supply_serve if exists, else from lookup
                {
                    "$addFields": {
                        "dam_tm": {
                            "$ifNull": ["$dam_tm", {"$arrayElemAt": ["$plant_tm.dam_tm", 0]}]
                        },
                        "rtm_tm": {
                            "$ifNull": ["$rtm_tm", {"$arrayElemAt": ["$plant_tm.rtm_tm", 0]}]
                        }
                    }
                },
                # Stage 4: Project only needed fields
                {
                    "$project": {
                        "_id": 0,
                        "time_block": 1,
                        "plant_id": 1,
                        "dc_sg": 1,
                        "volume": 1,
                        "ramp_up": 1,
                        "ramp_down": 1,
                        "dc_afterloss": 1,
                        "max_mw_dc": 1,  # Added for SG schedule logic
                        "dam_tm": {"$ifNull": ["$dam_tm", 0]},  # Default to 0 if still null
                        "rtm_tm": {"$ifNull": ["$rtm_tm", 0]}   # Default to 0 if still null
                    }
                }
            ]
            
            # Simplified pipeline for historical (no $lookup - too slow on time-series)
            historical_pipeline = [
                {
                    "$match": {
                        "scheduled_at": {"$gte": target_date, "$lt": end_date},
                        "dc_sg": "SG"  # Only SG for power optimization
                        # "plant_id": {"$nin": list(EXCLUDED_PLANTS)}
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "time_block": 1,
                        "plant_id": 1,
                        "dc_sg": 1,
                        "volume": 1,
                        "ramp_up": 1,
                        "ramp_down": 1,
                        "dc_afterloss": 1,
                        "dam_tm": {"$ifNull": ["$dam_tm", 0]},
                        "rtm_tm": {"$ifNull": ["$rtm_tm", 0]}
                    }
                }
            ]

            # Use optimized aggregation pipeline
            collection = self.db[self.collection_name]     
                  
            # Check if any data exists for this date (quick count check with timeout)
            try:
                count = await collection.count_documents({
                    "scheduled_at": {"$gte": target_date, "$lt": end_date},
                    # "plant_id": {"$nin": list(EXCLUDED_PLANTS)}
                }, limit=1)
            except Exception as count_err:
                logger.warning(f"PowerOpt: Count check failed for {schedule_date}: {count_err}")
                count = 0
            
            using_historical = False
            active_pipeline = pipeline
            
            if count == 0:
                # No data in main collection, try historical
                historical_collection = getattr(settings, 'MONGODB_SUPPLY_SERVE_HISTORICAL_COLLECTION', "supply_serve_historical")
                logger.info(f"PowerOpt: No supply data in {self.collection_name}, trying {historical_collection}")
                collection = self.db[historical_collection]
                using_historical = True
                active_pipeline = historical_pipeline  # Use simplified pipeline for historical
                
                # Quick check if historical has data (with short timeout)
                try:
                    hist_count = await collection.count_documents({
                        "scheduled_at": {"$gte": target_date, "$lt": end_date},
                        # "plant_id": {"$nin": list(EXCLUDED_PLANTS)}
                    }, limit=1)
                except Exception as hist_err:
                    logger.warning(f"PowerOpt: Historical count check failed: {hist_err}")
                    hist_count = 0
                    
                logger.info(f"PowerOpt: Historical collection {historical_collection} has {hist_count} documents for {schedule_date}")
                
                # If no data in historical either, return empty immediately
                if hist_count == 0:
                    logger.info(f"PowerOpt: No data in either collection for {schedule_date}, returning empty")
                    return []
            
            cursor = collection.aggregate(
                active_pipeline,
                allowDiskUse=True,
                maxTimeMS=30000,  # 30 seconds
                batchSize=1000
            )
                
            try:
                result = await cursor.to_list(length=10000)
                if using_historical:
                    logger.info(f"PowerOpt: Retrieved {len(result)} records from historical collection for {schedule_date}")
                    
                    # In-memory lookup for plant_master data (faster than $lookup on time-series)
                    if result:
                        plant_ids = list(set(r.get("plant_id") for r in result if r.get("plant_id")))
                        if plant_ids:
                            plant_cursor = self.db["plant_master"].find(
                                {"plant_id": {"$in": plant_ids}},
                                {"plant_id": 1, "dam_tm": 1, "rtm_tm": 1, "_id": 0}
                            )
                            plant_map = {p["plant_id"]: p for p in plant_cursor}
                            
                            # Merge plant data into results
                            for record in result:
                                pid = record.get("plant_id")
                                if pid and pid in plant_map:
                                    plant = plant_map[pid]
                                    # Only set if not already present in record
                                    if record.get("dam_tm") in (None, 0):
                                        record["dam_tm"] = plant.get("dam_tm", 0)
                                    if record.get("rtm_tm") in (None, 0):
                                        record["rtm_tm"] = plant.get("rtm_tm", 0)
                            
                            logger.info(f"PowerOpt: Merged plant data for {len(plant_map)} plants")
            finally:
                cursor.close()
                
            return result
        except Exception as e:
            logger.error(f"PowerOpt: Failed to get supply data: {type(e).__name__}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    def get_filter_data_by_category(
        self,
        schedule_date: str,
        filter_type: str = "category",
        dc_sg_type: Optional[str] = None,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Calculate filterData grouped by category/technology/price_group per time block.
        This replaces the filterData from availability_PG collection.
        
        Args:
            schedule_date: Date in YYYY-MM-DD format
            filter_type: "category", "technology", or "price_group" (default: "category")
            dc_sg_type: Optional filter for DC/SG type (default: None = all)
            from_block: Optional starting time block filter
            to_block: Optional ending time block filter
            
        Returns:
            List of time block data with filterData array, matching availability_PG structure:
            [
                {
                    "timeBlock": 1,
                    "schedule_date": "2024-01-01",
                    "dc_sg_type": "DC_SG",
                    "filterData": [
                        {"category": "ISP", "totalvolcat": 1000.0},
                        {"category": "STOA", "totalvolcat": 500.0},
                        ...
                    ]
                },
                ...
            ]
        """
        try:
            # Convert date string to datetime for scheduled_at matching
            target_date = datetime.strptime(schedule_date, "%Y-%m-%d")
            end_date = target_date + timedelta(days=1)
            
            # Build match stage
            match_stage = {
                "scheduled_at": {"$gte": target_date, "$lt": end_date}
            }
            
            # Add dc_sg filter if specified
            if dc_sg_type:
                match_stage["dc_sg"] = dc_sg_type
            
            # Add time_block range filter if specified
            if from_block is not None or to_block is not None:
                time_block_filter = {}
                if from_block is not None:
                    time_block_filter["$gte"] = from_block
                if to_block is not None:
                    time_block_filter["$lte"] = to_block
                if time_block_filter:
                    match_stage["time_block"] = time_block_filter
            
            # Note: onbarOffBarType filter - check if field exists in supply_serve
            # If field exists, filter: onbarOffBarType != "OffBarEnt"
            # If field doesn't exist, assume all records are valid (OnBar)
            
            # Build dynamic field path for plant_master lookup
            # For category, technology, and price_group: use plant_master
            # Since supply_serve doesn't have category/technology, and price_group may be null
            # We need to build the field path dynamically based on filter_type
            if filter_type == "category":
                filter_value_path = "$plant_details.category"
            elif filter_type == "technology":
                filter_value_path = "$plant_details.technology"
            elif filter_type == "price_group":
                filter_value_path = "$plant_details.price_group"
            else:
                # Default to category if unknown filter_type
                filter_value_path = "$plant_details.category"
                logger.warning(f"Unknown filter_type '{filter_type}', defaulting to category")
            
            pipeline = [
                {
                    "$match": match_stage
                },
                {
                    "$lookup": {
                        "from": "plant_master",
                        "localField": "plant_id",
                        "foreignField": "plant_id",
                        "as": "plant_details"
                    }
                },
                {"$unwind": {"path": "$plant_details", "preserveNullAndEmptyArrays": True}},
                {
                    "$group": {
                        "_id": {
                            "time_block": "$time_block",
                            # Use plant_master field (category, technology, or price_group)
                            "filter_value": filter_value_path  # e.g., "$plant_details.category"
                        },
                        "totalvol": {"$sum": "$volume"},
                        "dc_sg": {"$first": "$dc_sg"},
                        "schedule_date": {
                            "$first": {
                                "$dateToString": {
                                    "format": "%Y-%m-%d",
                                    "date": "$scheduled_at"
                                }
                            }
                        }
                    }
                },
                # Filter out records where filter_value is null (plants without category/technology/price_group in plant_master)
                {
                    "$match": {
                        "_id.filter_value": {"$ne": None}
                    }
                },
                {
                    "$group": {
                        "_id": "$_id.time_block",
                        "filterData": {
                            "$push": {
                                filter_type: "$_id.filter_value",  # Dynamic field name - built in Python before pipeline execution
                                "totalvolcat": "$totalvol"
                            }
                        },
                        "schedule_date": {"$first": "$schedule_date"},
                        "timeBlock": {"$first": "$_id.time_block"},
                        "dc_sg_type": {"$first": "$dc_sg"}
                    }
                },
                {"$sort": {"timeBlock": 1}},
                {
                    "$project": {
                        "_id": 0,
                        "timeBlock": 1,
                        "schedule_date": 1,
                        "dc_sg_type": 1,
                        "filterData": 1
                    }
                }
            ]
            
            result = self._aggregate_sync(pipeline)
            logger.info(f"Calculated filterData for {len(result)} time blocks (filter_type={filter_type}, schedule_date={schedule_date})")
            return result
            
        except Exception as e:
            logger.error(f"Failed to get filter data by category: {str(e)}")
            return []

    def aggregate_supply_schedule(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """
        Aggregates supply schedule grouped by scheduled_at and time_block.
        Returns separate totals for Central (c_s='central') and State (c_s='state') plants.
        """
        try:
            pipeline = [
                {
                    "$match": {
                        "scheduled_at": {"$gte": start_date, "$lt": end_date}
                    }
                },
                {
                    "$lookup": {
                        "from": "plant_master",
                        "localField": "plant_id",
                        "foreignField": "plant_id",
                        "as": "plant_details"
                    }
                },
                {"$unwind": {"path": "$plant_details", "preserveNullAndEmptyArrays": True}},
                {
                    "$group": {
                        "_id": {
                            "scheduled_at": "$scheduled_at",
                            "time_block": "$time_block"
                        },
                        "central_schedule": {
                            "$sum": {
                                "$cond": [
                                    {"$eq": [{"$toLower": "$plant_details.c_s"}, "central"]},
                                    "$volume",
                                    0
                                ]
                            }
                        },
                        "state_schedule": {
                            "$sum": {
                                "$cond": [
                                    {"$eq": [{"$toLower": "$plant_details.c_s"}, "state"]},
                                    "$volume",
                                    0
                                ]
                            }
                        },
                        "total_schedule": {"$sum": "$volume"}
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "scheduled_at": "$_id.scheduled_at",
                        "time_block": "$_id.time_block",
                        "central_schedule": 1,
                        "state_schedule": 1,
                        "total_schedule": 1
                    }
                }
            ]
            
            return self._aggregate_sync(pipeline)
        except Exception as e:
            logger.error(f"Failed to aggregate supply schedule: {str(e)}")
            return []
