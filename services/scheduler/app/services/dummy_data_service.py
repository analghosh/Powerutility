"""
Demand Data Management Service
Handles cron job for managing data flow between collections

Time Series Collections:
- demand_serve: Active time series data (4-day window)
- demand_serve_historical: Archived time series data
- dummy: Source time series data

Time Series Data Structure:
- scheduled_date: Primary date field in dummy collection for filtering
- inserted_date: Insertion timestamp in demand_serve collection
- created_at: Creation timestamp in demand_serve collection
- timestamp: Time series indexing field
- processed_at: Processing timestamp
- archived_at: Archive timestamp in historical collection

ID Management:
- demand_serve: New _id generated for each record
- demand_serve_historical: Original _id preserved for tracking
"""

import logging
from datetime import datetime, timedelta
import random
from typing import List, Dict, Any, Optional
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from bson import ObjectId

from libs.models import (
    DemandServeEntity,
    DemandServeHistoricalEntity,
    SupplyServeEntity,
    SupplyServeHistoricalEntity
)
from ..core.config import settings
from ..repository.demand_serve_repository import demand_serve_repository  # type: ignore
from ..repository.supply_serve_repository import supply_serve_repository  # type: ignore
from libs.core.job_factory import create_job_detail_repository, create_job_data_repository

# Create repository instances
job_detail_repository = create_job_detail_repository(
    settings.AUDIT_DB_URI,
    settings.AUDIT_DB_NAME,
    settings.JOB_DETAIL_COLLECTION
)
job_data_repository = create_job_data_repository(
    settings.AUDIT_DB_URI,
    settings.AUDIT_DB_NAME,
    settings.JOB_DATA_COLLECTION
)

logger = logging.getLogger(__name__)


class DummyDataService:
    """Service for managing demand data between collections"""

    def __init__(self):
        """Initialize the service with MongoDB connection"""
        from ..core.database import get_database
        self.db: Database = get_database()
        self.dummy_collection: Collection = self.db[settings.MONGODB_DUMMY_COLLECTION]
        self.demand_serve_collection: Collection = self.db[
            settings.MONGODB_DEMAND_SERVE_COLLECTION
        ]
        self.demand_historical_collection: Collection = self.db[
            settings.MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION
        ]
        self.supply_serve_collection: Collection = self.db[
            settings.MONGODB_SUPPLY_SERVE_COLLECTION
        ]
        self.supply_historical_collection: Collection = self.db[
            settings.MONGODB_SUPPLY_SERVE_HISTORICAL_COLLECTION
        ]

    async def initialize(self):
        """Async initialization: create time series collections if they don't exist.
        Must be called once at application startup (inside the async lifespan)."""
        await self._ensure_time_series_collections()

    async def _ensure_time_series_collections(self):
        """Create time series collections if they don't exist"""
        await self._ensure_demand_serve_collection()
        await self._ensure_supply_serve_collection()

    async def _ensure_demand_serve_collection(self):
        try:
            existing = await self.db.list_collection_names()
            if settings.MONGODB_DEMAND_SERVE_COLLECTION not in existing:
                await self.db.create_collection(
                    settings.MONGODB_DEMAND_SERVE_COLLECTION,
                    timeseries={
                        "timeField": "timestamp",
                        "metaField": "schedule_date",
                        "granularity": "hours",
                    },
                )
                logger.info(
                    "Created time series collection: %s",
                    settings.MONGODB_DEMAND_SERVE_COLLECTION,
                )

            # Create demand_serve_historical as time series collection
            if settings.MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION not in existing:
                await self.db.create_collection(
                    settings.MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION,
                    timeseries={
                        "timeField": "archived_timestamp",
                        "metaField": "schedule_date",
                        "granularity": "hours",
                    },
                )
                logger.info(
                    "Created time series collection: %s",
                    settings.MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION,
                )

            # Get the collections after creation
            self.demand_serve_collection = self.db[
                settings.MONGODB_DEMAND_SERVE_COLLECTION
            ]
            self.demand_historical_collection = self.db[
                settings.MONGODB_DEMAND_SERVE_HISTORICAL_COLLECTION
            ]
        except (ConnectionError, TimeoutError) as e:
            logger.info("Could not create time series collections: %s", str(e))
            logger.info("Using regular collections with timestamp indexes")

    async def _ensure_supply_serve_collection(self):
        try:
            existing = await self.db.list_collection_names()
            if settings.MONGODB_SUPPLY_SERVE_COLLECTION not in existing:
                await self.db.create_collection(
                    settings.MONGODB_SUPPLY_SERVE_COLLECTION,
                    timeseries={
                        "timeField": "timestamp",
                        "metaField": "schedule_date",
                        "granularity": "hours",
                    },
                )
                logger.info(
                    "Created time series collection: %s",
                    settings.MONGODB_SUPPLY_SERVE_COLLECTION,
                )

            # Create supply_serve_historical as time series collection
            if settings.MONGODB_SUPPLY_SERVE_HISTORICAL_COLLECTION not in existing:
                await self.db.create_collection(
                    settings.MONGODB_SUPPLY_SERVE_HISTORICAL_COLLECTION,
                    timeseries={
                        "timeField": "archived_timestamp",
                        "metaField": "schedule_date",
                        "granularity": "hours",
                    },
                )
                logger.info(
                    "Created time series collection: %s",
                    settings.MONGODB_SUPPLY_SERVE_HISTORICAL_COLLECTION,
                )

            # Get the collections after creation
            self.supply_serve_collection = self.db[
                settings.MONGODB_SUPPLY_SERVE_COLLECTION
            ]
            self.supply_historical_collection = self.db[
                settings.MONGODB_SUPPLY_SERVE_HISTORICAL_COLLECTION
            ]
        except (ConnectionError, TimeoutError) as e:
            logger.info("Could not create time series collections: %s", str(e))
            logger.info("Using regular collections with timestamp indexes")

    def check_if_cron_has_run_before(self, job_id: Optional[str] = None) -> bool:
        """Check if cron jobs have run before by examining execution logs"""
        try:
            logger.info("Checking if cron jobs have run before...")
            
            cron_execution_status = self._check_recent_cron_executions(job_id)

            logger.info("Cron serve data reconciliation execution status: %s", cron_execution_status)
            return cron_execution_status
            
        except (ConnectionError, TimeoutError) as e:
            logger.error("Error checking if cron has run before: %s", str(e))
            return False
    
    def _check_recent_cron_executions(self, job_id: Optional[str] = None) -> bool:
        """Check for recent cron job execution logs in job_detail collection"""
        try:
            if not job_id:
                logger.warning("Job ID is required to check recent cron executions")
                return False
            
            execution_status = job_detail_repository.get_recent_executions(
                job_id=str(job_id)
            )
            
            return execution_status
            
        except (ConnectionError, TimeoutError) as e:
            logger.error("Error checking recent cron executions: %s", str(e))
            return False

    
    async def _process_startup_data_flow(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Process data flow on startup based on whether cron has run before

        Returns:
            Dict containing startup processing results
        """
        try:
            logger.info("Processing startup data flow...")
            logger.info("First time running - starting backfill from July 1st, 2025")
            await self._process_first_time_startup("demand")
            await self._process_first_time_startup("supply")

            return {
                "status": "success",
                "startup_type": "first_time",
                "message": "First-time startup processing completed",
            }

        except (ConnectionError, TimeoutError) as e:
            logger.error("Error in startup data flow: %s", str(e))
            return {
                "status": "error",
                "error": str(e),
                "message": "Startup data flow failed",
            }

    async def _process_first_time_startup(self, data_type: str) -> Dict[str, Any]:
        """Process first-time startup with backfill from July 1st, 2025"""
        try:
            logger.info("Processing first-time startup with backfill...")

            # Check if we have stale data that needs cleanup
            if data_type == "demand":
                total_count = demand_serve_repository.count_demand_serve()
            elif data_type == "supply":
                total_count = supply_serve_repository.count_supply_serve()
            else:
                raise ValueError(f"Invalid data type: {data_type}")

            if total_count > 0:
                logger.info(
                    "Found %d existing records - will move old data to historical during backfill",
                    total_count,
                )

            # Start backfill from July 1st, 2025
            start_date = "2025-07-01"
            backfill_result = await self.backfill_from_date(start_date, data_type)
            logger.info("Backfill result: %s", backfill_result)

            if backfill_result["status"] == "success":
                result = {
                    "status": "success",
                    "startup_type": "first_time",
                    "backfill_result": backfill_result,
                    "message": f"First-time startup completed with backfill from {start_date}",
                }

                logger.info("First-time startup completed successfully")
                return result
            else:
                return {
                    "status": "error",
                    "data_type": data_type,
                    "startup_type": "first_time",
                    "error": backfill_result.get("error", "Backfill failed"),
                    "message": "First-time startup failed during backfill",
                }

        except (ConnectionError, TimeoutError) as e:
            logger.error("Error in first-time startup: %s", str(e))
            return {
                "status": "error",
                "data_type": data_type,
                "startup_type": "first_time",
                "error": str(e),
                "message": "First-time startup failed",
            }

    async def _fetch_data_for_date(
        self, target_date: datetime, data_type: str
    ) -> List[Dict[str, Any]]:
        """Fetch data from dummy collection for a specific date with uniqueness applied at MongoDB level"""
        try:
            # Filter by scheduled_date in dummy collection
            start_timestamp = target_date
            end_timestamp = target_date + timedelta(days=1)

            # Build match stage for filtering
            match_stage: Dict[str, Any] = {
                "schedule_date": {"$gte": start_timestamp, "$lt": end_timestamp}
            }
            if data_type == "demand":
                match_stage["data_type"] = "demand"
                match_stage["dc_sg_type"] = {"$in": ["actual", "Forecast"]}
            elif data_type == "supply":
                match_stage["data_type"] = {"$in": ["availability", "combine"]}

            unique_keys = []
            # Define unique key combinations based on data type
            if data_type == "demand":
                # For demand data, consider these fields as unique identifiers
                unique_keys = ["schedule_date", "time_block", "dc_sg_type", "market"]
            elif data_type == "supply":
                # For supply data, consider these fields as unique identifiers
                unique_keys = ["schedule_date", "time_block", "dc_sg_type", "category", "plant_name"]

            # Build group stage for uniqueness
            group_id = {}
            for key in unique_keys:
                group_id[key] = f"${key}"
            
            # Use aggregation pipeline to ensure uniqueness at MongoDB level
            pipeline = [
                {"$match": match_stage},
                {"$group": {
                    "_id": group_id,
                    "doc": {"$first": "$$ROOT"}  # Keep the first document for each unique combination
                }},
                {"$replaceRoot": {"newRoot": "$doc"}}  # Restore the original document structure
            ]
            
            cursor = self.dummy_collection.aggregate(pipeline)
            try:
                data = await cursor.to_list(10000)
            finally:
                cursor.close()
            logger.info(
                "Fetched %d unique records for scheduled_date %s (uniqueness applied at MongoDB level)",
                len(data),
                target_date.date(),
            )
            
            return data
        except (ConnectionError, TimeoutError) as e:
            logger.error(
                "Error fetching data for scheduled_date %s: %s",
                target_date.date(),
                str(e),
            )
            return []

    def _check_existing_data(
            self, target_date: datetime, data_type: str
    ) -> Dict[str, Any]:
        """Check if data already exists in serve collections for the given date"""
        try:
            logger.info("Checking existing data for date %s (type: %s)", target_date.date(), data_type)
            
            date_range = self._get_date_range_for_check(target_date)
            record_counts = self._fetch_record_counts_by_type(date_range, data_type)
            existence_status = self._determine_existence_status(record_counts, data_type)
            
            self._log_existence_check_results(target_date, record_counts, existence_status, data_type)
            
            return {
                "demand_count": record_counts.get("demand_count", 0),
                "supply_count": record_counts.get("supply_count", 0),
                "exists": existence_status
            }
            
        except (ConnectionError, TimeoutError) as e:
            logger.error("Error checking existing data for date %s: %s", target_date.date(), str(e))
            return self._get_default_existence_result()
    
    def _get_date_range_for_check(self, target_date: datetime) -> Dict[str, datetime]:
        """Get the date range for existence check"""
        start_timestamp = target_date
        end_timestamp = target_date + timedelta(days=1)
        
        return {
            "start": start_timestamp,
            "end": end_timestamp
        }
    
    def _fetch_record_counts_by_type(self, date_range: Dict[str, datetime], data_type: str) -> Dict[str, int]:
        """Fetch record counts based on the specified data type"""
        try:
            record_counts = {}
            
            if data_type in ["demand", "both"]:
                demand_records = demand_serve_repository.find_demand_serve_by_date_range(
                    date_range["start"], date_range["end"]
                )
                record_counts["demand_count"] = len(demand_records)
                logger.debug("Found %d demand records for date range", record_counts["demand_count"])
            
            if data_type in ["supply", "both"]:
                supply_records = supply_serve_repository.find_supply_serve_by_date_range(
                    date_range["start"], date_range["end"]
                )
                record_counts["supply_count"] = len(supply_records)
                logger.debug("Found %d supply records for date range", record_counts["supply_count"])
            
            return record_counts
            
        except (ConnectionError, TimeoutError) as e:
            logger.error("Error fetching record counts: %s", str(e))
            return {}
    
    def _determine_existence_status(self, record_counts: Dict[str, int], data_type: str) -> bool:
        """Determine if data exists based on record counts and data type"""
        if data_type == "demand":
            return record_counts.get("demand_count", 0) > 0
        elif data_type == "supply":
            return record_counts.get("supply_count", 0) > 0
        elif data_type == "both":
            demand_exists = record_counts.get("demand_count", 0) > 0
            supply_exists = record_counts.get("supply_count", 0) > 0
            return demand_exists or supply_exists
        else:
            logger.warning("Unknown data type: %s, defaulting to False", data_type)
            return False
    
    def _log_existence_check_results(self, target_date: datetime, record_counts: Dict[str, int], 
                                   existence_status: bool, data_type: str) -> None:
        """Log the results of existence check"""
        demand_count = record_counts.get("demand_count", 0)
        supply_count = record_counts.get("supply_count", 0)
        
        logger.info(
            "Data existence check for %s (type: %s) - demand: %d, supply: %d, exists: %s",
            target_date.date(),
            data_type,
            demand_count,
            supply_count,
            existence_status,
        )
    
    def _get_default_existence_result(self) -> Dict[str, Any]:
        """Get default result when existence check fails"""
        return {
            "demand_count": 0,
            "supply_count": 0,
            "exists": False
        }

    def _cleanup_duplicates(
        self, target_date: datetime, data_type: str
    ) -> int:
        """Remove duplicate records for a specific date, keeping only the most recent"""
        try:
            logger.info("Starting duplicate cleanup for %s on %s", data_type, target_date.date())
            
            date_range = self._get_date_range_for_cleanup(target_date)
            records = self._fetch_records_for_cleanup(date_range, data_type)
            
            if not self._has_duplicates(records):
                logger.info("No duplicates found for %s", target_date.date())
                return 0
            
            total_removed = self._remove_duplicates_by_type(records, data_type)
            self._log_cleanup_results(total_removed, target_date)
            
            return total_removed
            
        except (ConnectionError, TimeoutError) as e:
            logger.error("Error cleaning up duplicates for date %s: %s", target_date.date(), str(e))
            return 0
    
    def _get_date_range_for_cleanup(self, target_date: datetime) -> Dict[str, datetime]:
        """Get the date range for duplicate cleanup"""
        start_timestamp = target_date
        end_timestamp = target_date + timedelta(days=1)
        
        return {
            "start": start_timestamp,
            "end": end_timestamp
        }
    
    def _fetch_records_for_cleanup(self, date_range: Dict[str, datetime], data_type: str) -> List[Dict[str, Any]]:
        """Fetch records for duplicate cleanup from repositories"""
        try:
            records = []
            # Fetch records from both repositories
            if data_type == "demand":
                records = demand_serve_repository.find_demand_serve_by_date_range(
                    date_range["start"], date_range["end"]
                )
                records.sort(key=lambda x: x.get("inserted_at", datetime.min), reverse=True)
                logger.debug("Fetched %d demand records for cleanup", len(records))
            elif data_type == "supply":
                records = supply_serve_repository.find_supply_serve_by_date_range(
                    date_range["start"], date_range["end"]
                )
                records.sort(key=lambda x: x.get("inserted_at", datetime.min), reverse=True)
                logger.debug("Fetched %d supply records for cleanup", len(records))
            else:
                raise ValueError(f"Invalid data type: {data_type}")
            
            return records
            
        except (ConnectionError, TimeoutError) as e:
            logger.error("Error fetching records for cleanup: %s", str(e))
            return []
    
    def _has_duplicates(self, records: List[Dict[str, Any]]) -> bool:
        """Check if duplicates exist in the records"""
        count = len(records)
        
        return count > 1
    
    def _remove_duplicates_by_type(self, records: List[Dict[str, Any]], 
                                  data_type: str) -> int:
        """Remove duplicates based on the specified data type"""
        total_removed = 0
        
        if data_type == "demand" and len(records) > 1:
            removed = self._remove_demand_duplicates(records)
            total_removed += removed
            
        elif data_type == "supply" and len(records) > 1:
            removed = self._remove_supply_duplicates(records)
            total_removed += removed
        
        return total_removed
    
    def _remove_demand_duplicates(self, demand_records: List[Dict[str, Any]]) -> int:
        """Remove duplicate demand records, keeping only the most recent"""
        try:
            # Keep only the most recent record (first one after sorting by inserted_date desc)
            records_to_remove = demand_records[1:]
            total_removed = 0
            
            for record in records_to_remove:
                removed = self._remove_single_demand_record(record)
                total_removed += removed
                
            logger.debug("Removed %d duplicate demand records", total_removed)
            return total_removed
            
        except (ConnectionError, TimeoutError) as e:
            logger.error("Error removing demand duplicates: %s", str(e))
            return 0
    
    def _remove_supply_duplicates(self, supply_records: List[Dict[str, Any]]) -> int:
        """Remove duplicate supply records, keeping only the most recent"""
        try:
            # Keep only the most recent record (first one after sorting by inserted_date desc)
            records_to_remove = supply_records[1:]
            total_removed = 0
            
            for record in records_to_remove:
                removed = self._remove_single_supply_record(record)
                total_removed += removed
                
            logger.debug("Removed %d duplicate supply records", total_removed)
            return total_removed
            
        except (ConnectionError, TimeoutError) as e:
            logger.error("Error removing supply duplicates: %s", str(e))
            return 0
    
    def _remove_single_demand_record(self, record: Dict[str, Any]) -> int:
        """Remove a single demand record by its scheduled_at date"""
        try:
            scheduled_at = record.get("scheduled_at")
            if not scheduled_at:
                logger.warning("Demand record missing scheduled_at field, skipping removal")
                return 0
                
            delete_result = demand_serve_repository.delete_demand_serve_by_date(scheduled_at)
            deleted_count = delete_result.get("deleted_count", 0)
            
            if deleted_count > 0:
                logger.debug("Removed demand record with scheduled_at: %s", scheduled_at)
                
            return deleted_count
            
        except (ConnectionError, TimeoutError) as e:
            logger.error("Error removing duplicate demand record: %s", str(e))
            return 0
    
    def _remove_single_supply_record(self, record: Dict[str, Any]) -> int:
        """Remove a single supply record by its scheduled_at date"""
        try:
            scheduled_at = record.get("scheduled_at")
            if not scheduled_at:
                logger.warning("Supply record missing scheduled_at field, skipping removal")
                return 0
                
            delete_result = supply_serve_repository.delete_supply_serve_by_date(scheduled_at)
            deleted_count = delete_result.get("deleted_count", 0)
            
            if deleted_count > 0:
                logger.debug("Removed supply record with scheduled_at: %s", scheduled_at)
                
            return deleted_count
            
        except (ConnectionError, TimeoutError) as e:
            logger.error("Error removing duplicate supply record: %s", str(e))
            return 0
    
    def _log_cleanup_results(self, total_removed: int, target_date: datetime) -> None:
        """Log the results of duplicate cleanup"""
        if total_removed > 0:
            logger.info(
                "Cleaned up %d duplicate records for date %s",
                total_removed,
                target_date.date(),
            )
        else:
            logger.debug("No duplicates found for date %s", target_date.date())

    def _add_processed_timestamp(
        self, data: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Add processing timestamp to data records for time series collection"""
        timestamp = datetime.now()
        for record in data:
            # Update timestamp fields for demand_serve collection
            record["inserted_at"] = timestamp
            record["updated_at"] = timestamp
            record["created_at"] = timestamp
            record["processed_at"] = timestamp
            record["timestamp"] = timestamp  # Add timestamp for time series indexing
            # Generate new _id for demand_serve collection
            record["_id"] = ObjectId()
        return data

    def _process_demand_record(self, record: dict) -> dict:
        data = {}
        
        # Only set fields if they exist in the record
        if "schedule_date" in record:
            data["scheduled_at"] = record["schedule_date"]
        
        if "cronJob_time" in record:
            data["cron_job_time"] = record["cronJob_time"]
        if "start_time" in record:
            data["start_time"] = record["start_time"]
        if "end_time" in record:
            data["end_time"] = record["end_time"]
        if "full_time" in record:
            data["full_time"] = record["full_time"]
        
        if "time_block" in record:
            data["time_block"] = record["time_block"]
        
        # Handle market type with safe access
        if "market" in record and record["market"] == "RTM":
            data["type"] = "IDF"
        elif "market" in record and record["market"] == "DAM":
            data["type"] = "DAF"
        else:
            data["type"] = None
        
        data["forecast"] = 0
        
        if "MP_Demand" in record:
            data["actual"] = record["MP_Demand"]
        if "MP_Schedule" in record:
            data["schedule"] = record["MP_Schedule"]
        if "MP_Drawal" in record:
            data["drawal"] = record["MP_Drawal"]
        if "Raw_Frequency" in record:
            data["frequency"] = record["Raw_Frequency"]
        
        data["rostering"] = random.randint(4,25)
        
        # Calculate MAPE only if required fields exist
        if "actual" in data:
            mpe = (((data["actual"] + data["rostering"]) - data["forecast"]) / (data["actual"] + data["forecast"] if data["actual"] + data["forecast"] != 0 else 1)) * 100
            data["mape"] = abs(mpe)
            data["mpe"] = mpe
        else:
            data["mape"] = 0
            data["mpe"] = 0
        
        data["remarks"] = ""
        data["source_actual"] = ""
        data["source_forecast"] = ""
        data["source_rostering"] = ""
        
        if "area" in record:
            data["zone"] = record["area"]   
        else:
            data["zone"] = None
        
        data["comp_act_demand"] = 0
        data["net_dsm_amount"] = 0
        data["dsm_rate"] = 0
        
        # Calculate UDM only if actual exists
        if "actual" in data:
            data["udm"] = data["actual"] + data["rostering"]
        else:
            data["udm"] = data["rostering"]
        
        data["comp_udm"] = 0

        return data  # Return just the data, not the wrapper

    def _process_supply_record(self, record: dict) -> dict:
        data = {}
        if "schedule_date" in record:   
            data["scheduled_at"] = record["schedule_date"]
        if "plant_name" in record:
            data["plant_name"] = record["plant_name"]
        if "time_block" in record:
            data["time_block"] = record["time_block"]
        if "dc_sg_type" in record:
            data["dc_sg"] = record["dc_sg_type"]
        if "volume" in record:
            data["volume"] = record["volume"]
        if "FC_Price" in record:
            data["fc_price"] = record["FC_Price"]
        if "PX_Price" in record:
            data["px_price"] = record["PX_Price"]
        if "VC_Price" in record:
            data["vc_price"] = record["VC_Price"]
        if "category" in record:
            data["category"] = record["category"]
        if "technology" in record:
            data["technology"] = record["technology"]
        if "price_group" in record:
            data["price_group"] = record["price_group"]
        if "display_category" in record:
            data["c_s"]  = record["display_category"]
        data["tm"]=0
        data["multiplier"]=""
        data["ramping"]=""
        # Add your supply record processing logic here
        return data  # Return just the data, not the wrapper

    def _move_to_historical(
        self, data: List[Dict[str, Any]], data_type: str = "demand"
    ) -> bool:
        """Move data to historical time series collection using entities"""
        try:
            if data:
                logger.info(
                    "Starting to move %d records to historical collection...", len(data)
                )

                # Add historical timestamp for time series data
                historical_timestamp = datetime.now()
                logger.info("Adding historical timestamp: %s", historical_timestamp)

                # Convert dictionaries to historical entities using list comprehension
                if data_type == "demand":
                    historical_entities = [
                        DemandServeHistoricalEntity(**self._add_historical_timestamps(record, historical_timestamp))
                        for record in data
                        if self._is_valid_historical_record(record, data_type)
                    ]
                    demand_serve_repository.bulk_insert_demand_historical(historical_entities)
                else:
                    historical_entities = [
                        SupplyServeHistoricalEntity(**self._add_historical_timestamps(record, historical_timestamp))
                        for record in data
                        if self._is_valid_historical_record(record, data_type)
                    ]
                    supply_serve_repository.bulk_insert_supply_historical(historical_entities)

                logger.info(
                    "Inserting %d records into historical collection...", len(historical_entities)
                )
                
                logger.info(
                    "Successfully moved %d records to historical collection",
                    len(historical_entities),
                )

                # Log some sample data for debugging
                if historical_entities:
                    sample_entity = historical_entities[0]
                    logger.info(
                        "Sample record moved to historical - Original ID: %s, Archived: %s",
                        sample_entity.id,
                        sample_entity.archived_at,
                    )

                return True
            else:
                logger.info("No data to move to historical collection")
                return True
        except (ConnectionError, TimeoutError) as e:
            logger.error("Error moving data to historical collection: %s", str(e))
            logger.exception("Full exception details:")
            return False

    def _insert_new_data(self, data: List[Dict[str, Any]], data_type: str) -> bool:
        """Insert new data into serve collection using entities and repositories with bulk operations"""
        try:
            if data:
                if data_type == "demand" and data:
                    # Convert dictionaries to DemandServeEntity objects using list comprehension
                    entities = [
                        DemandServeEntity(**record) 
                        for record in data 
                        if self._is_valid_demand_record(record)
                    ]
                    
                    # Use bulk insert for better performance
                    if len(entities) > 0:
                        demand_serve_repository.bulk_insert_demand_serve(entities)
                        logger.info("Bulk inserted %d records into demand_serve collection", len(entities))
                
                elif data_type == "supply" and data:
                    # Convert dictionaries to SupplyServeEntity objects using list comprehension
                    entities = [
                        SupplyServeEntity(**record) 
                        for record in data 
                        if self._is_valid_supply_record(record)
                    ]
                    
                    # Use bulk insert for better performance
                    if len(entities) > 0:
                        supply_serve_repository.bulk_insert_supply_serve(entities)
                        logger.info("Bulk inserted %d records into supply_serve collection", len(entities))
                
                return True
            return True
        except (ConnectionError, TimeoutError) as e:
            logger.error("Error inserting new data: %s", str(e))
            return False

    def _is_valid_demand_record(self, record: Dict[str, Any]) -> bool:
        """Validate if a record can be converted to DemandServeEntity"""
        try:
            DemandServeEntity(**record)
            return True
        except (ValueError, TypeError, KeyError) as e:
            logger.error("Error creating DemandServeEntity: %s", str(e))
            return False

    def _is_valid_supply_record(self, record: Dict[str, Any]) -> bool:
        """Validate if a record can be converted to SupplyServeEntity"""
        try:
            SupplyServeEntity(**record)
            return True
        except (ValueError, TypeError, KeyError) as e:
            logger.error("Error creating SupplyServeEntity: %s", str(e))
            return False

    def _is_valid_historical_record(self, record: Dict[str, Any], data_type: str) -> bool:
        """Validate if a record can be converted to historical entity"""
        try:
            if data_type == "demand":
                DemandServeHistoricalEntity(**record)
            else:
                SupplyServeHistoricalEntity(**record)
            return True
        except (ValueError, TypeError, KeyError) as e:
            logger.error("Error creating historical entity: %s", str(e))
            return False

    def _add_historical_timestamps(self, record: Dict[str, Any], historical_timestamp: datetime) -> Dict[str, Any]:
        """Add historical timestamp fields to a record"""
        record_copy = record.copy()
        record_copy["archived_at"] = historical_timestamp
        record_copy["archived_timestamp"] = historical_timestamp
        return record_copy

    async def execute_data_management(self, data_type: str) -> Dict[str, Any]:
        """Execute the data management cron job for a specific data type"""
        start_time = datetime.now()
        logger.info("Starting %s data management cron job", data_type)
        
        try:
            self._prepare_data_management(data_type)
            target_date = self._get_target_processing_date()
            logger.info("Processing data for target date: %s", target_date.date())
            
            duplicates_removed = self._cleanup_duplicates_for_target_date(target_date, data_type)
            existing_data_check = await self._check_and_fetch_data_for_date(target_date, data_type)
            
            if self._should_process_data(existing_data_check):
                process_result = self._process_data_for_target_date(
                    existing_data_check["data"], target_date, data_type
                )
                result = self._build_success_result(
                    start_time, target_date, duplicates_removed, 
                    existing_data_check, process_result, data_type
                )
            else:
                result = self._build_skip_result(
                    start_time, target_date, duplicates_removed, data_type
                )
            
            logger.info("%s data management completed successfully in %.2fs",
                       data_type.title(), (datetime.now() - start_time).total_seconds())
            return result
            
        except (ConnectionError, TimeoutError) as e:
            return self._build_error_result(start_time, str(e), data_type)
    
    def cleanup_old_serve_data(self, retention_days: int) -> int:
        """
        Clean up old serve data based on retention policy
        
        Args:
            retention_days: Number of days to retain data
            
        Returns:
            int: Number of records cleaned up
        """
        try:
            from datetime import datetime, timedelta
            
            cutoff_time = datetime.now() - timedelta(days=retention_days)
            total_cleaned = 0
            
            # Clean up old demand_serve data
            if hasattr(self, 'demand_serve_collection') and self.demand_serve_collection is not None:
                result = self.demand_serve_collection.delete_many({
                    "inserted_date": {"$lt": cutoff_time}
                })
                total_cleaned += result.deleted_count
                logger.info("Cleaned up %s old demand_serve records", result.deleted_count)
            
            # Clean up old supply_serve data
            if hasattr(self, 'supply_serve_collection') and self.supply_serve_collection is not None:
                result = self.supply_serve_collection.delete_many({
                    "inserted_date": {"$lt": cutoff_time}
                })
                total_cleaned += result.deleted_count
                logger.info("Cleaned up %s old supply_serve records", result.deleted_count)
            
            # Clean up old historical data
            if hasattr(self, 'demand_historical_collection') and self.demand_historical_collection is not None:
                result = self.demand_historical_collection.delete_many({
                    "archived_at": {"$lt": cutoff_time}
                })
                total_cleaned += result.deleted_count
                logger.info("Cleaned up %s old demand_historical records", result.deleted_count)
            
            if hasattr(self, 'supply_historical_collection') and self.supply_historical_collection is not None:
                result = self.supply_historical_collection.delete_many({
                    "archived_at": {"$lt": cutoff_time}
                })
                total_cleaned += result.deleted_count
                logger.info("Cleaned up %s old supply_historical records", result.deleted_count)
            
            return total_cleaned
            
        except (ValueError, TypeError, OSError) as e:
            logger.error("Failed to cleanup old serve data: %s", str(e))
            return 0
    
    def _prepare_data_management(self, data_type: str) -> None:
        """Prepare data management by ensuring indexes and validating data type"""
        logger.info("Preparing %s data management...", data_type)
        
        if data_type not in ["demand", "supply"]:
            raise ValueError(f"Invalid data type: {data_type}. Must be 'demand' or 'supply'")
        
        logger.info("Ensuring time series indexes...")
        self._ensure_time_series_indexes(data_type)
    
    def _get_target_processing_date(self) -> datetime:
        """Get the target date for processing (tomorrow)"""
        from ..core.config import settings
        from zoneinfo import ZoneInfo
        
        # Use the configured timezone for date calculation
        tz = ZoneInfo(settings.CRON_TIMEZONE)
        today = datetime.now(tz).date()
        tomorrow = today + timedelta(days=1)
        return datetime.combine(tomorrow, datetime.min.time(), tzinfo=tz)
    
    def _cleanup_duplicates_for_target_date(self, target_date: datetime, data_type: str) -> int:
        """Clean up duplicates for the target date"""
        logger.info("Cleaning up duplicates for %s...", target_date.date())
        duplicates_removed = self._cleanup_duplicates(target_date, data_type)
        
        if duplicates_removed > 0:
            logger.info("Removed %d duplicate records for %s", duplicates_removed, target_date.date())
        else:
            logger.info("No duplicates found for %s", target_date.date())
        
        return duplicates_removed
    
    async def _check_and_fetch_data_for_date(self, target_date: datetime, data_type: str) -> Dict[str, Any]:
        """Check if data exists and fetch new data if needed"""
        logger.info("Checking if data already exists for %s...", target_date.date())
        
        existing_data_check = self._check_existing_data(target_date, data_type)
        data_exists = existing_data_check["exists"]
        
        if data_exists:
            logger.info("Data for %s already exists, skipping data fetch", target_date.date())
            return {
                "data": [],
                "data_exists": True,
                "data_fetched": 0
            }
        else:
            logger.info("Fetching data for %s from dummy collection...", target_date.date())
            fetched_data = await self._fetch_data_for_date(target_date, data_type)
            
            if not fetched_data:
                logger.info("No data found for %s in dummy collection", target_date.date())
            else:
                logger.info("Fetched %d records for %s", len(fetched_data), target_date.date())
            
            return {
                "data": fetched_data,
                "data_exists": False,
                "data_fetched": len(fetched_data)
            }
    
    def _should_process_data(self, existing_data_check: Dict[str, Any]) -> bool:
        """Determine if data should be processed"""
        return existing_data_check["data"] or not existing_data_check["data_exists"]
    
    def _process_data_for_target_date(self, data: List[Dict[str, Any]], 
                                     target_date: datetime, data_type: str) -> Dict[str, Any]:
        """Process data for the target date"""
        logger.info("Processing %d records for %s", len(data), target_date.date())
        return self.process_data_for_date(data, target_date, data_type)
    
    def _get_current_record_counts(self, data_type: str) -> Dict[str, int]:
        """Get current record counts for both demand and supply collections"""
        try:
            current_demand_records = demand_serve_repository.count_demand_serve()
            current_supply_records = supply_serve_repository.count_supply_serve()
            
            logger.info("Current demand_serve: %d, supply_serve: %d records", 
                       current_demand_records, current_supply_records)
            
            return {
                "demand_records": current_demand_records,
                "supply_records": current_supply_records
            }
        except (ConnectionError, TimeoutError) as e:
            logger.error("Error getting current record counts: %s", str(e))
            return {"demand_records": 0, "supply_records": 0}
    
    def _build_success_result(self, start_time: datetime, target_date: datetime,
                             duplicates_removed: int, existing_data_check: Dict[str, Any],
                             process_result: Dict[str, Any], data_type: str) -> Dict[str, Any]:
        """Build success result dictionary"""
        end_time = datetime.now()
        execution_time = (end_time - start_time).total_seconds()
        current_counts = self._get_current_record_counts(data_type)
        
        return {
            "status": process_result.get("status", "unknown"),
            "execution_time_seconds": execution_time,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "data_type": data_type,
            "target_date": target_date.date().isoformat(),
            "data_fetched": existing_data_check["data_fetched"],
            "duplicates_removed": duplicates_removed,
            "data_already_exists": existing_data_check["data_exists"],
            "removal_success": process_result.get("historical_success", False),
            "insertion_success": process_result.get("insertion_success", False),
            "current_demand_serve_records": current_counts["demand_records"],
            "current_supply_serve_records": current_counts["supply_records"],
            "records_inserted": process_result.get("records_inserted", 0),
            "old_records_moved": process_result.get("old_records_moved", 0),
            "cutoff_date": process_result.get("cutoff_date", ""),
            "date_range": {"target_date": target_date.date().isoformat()},
        }
    
    def _build_skip_result(self, start_time: datetime, target_date: datetime,
                          duplicates_removed: int, data_type: str) -> Dict[str, Any]:
        """Build skip result dictionary when no processing is needed"""
        logger.info("No processing needed - data already exists")
        end_time = datetime.now()
        execution_time = (end_time - start_time).total_seconds()
        current_counts = self._get_current_record_counts(data_type)
        
        return {
            "status": "success",
            "execution_time_seconds": execution_time,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "data_type": data_type,
            "target_date": target_date.date().isoformat(),
            "data_fetched": 0,
            "duplicates_removed": duplicates_removed,
            "data_already_exists": True,
            "removal_success": True,
            "insertion_success": True,
            "current_demand_serve_records": current_counts["demand_records"],
            "current_supply_serve_records": current_counts["supply_records"],
            "records_inserted": 0,
            "old_records_moved": 0,
            "date_range": {"target_date": target_date.date().isoformat()},
        }
    
    def _build_error_result(self, start_time: datetime, error_message: str, 
                           data_type: str) -> Dict[str, Any]:
        """Build error result dictionary"""
        end_time = datetime.now()
        execution_time = (end_time - start_time).total_seconds()
        
        logger.error("%s data management failed after %.2fs: %s", 
                    data_type.title(), execution_time, error_message)
        logger.exception("Full exception details:")
        
        return {
            "status": "error",
            "execution_time_seconds": execution_time,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "data_type": data_type,
            "error": error_message,
        }

    def _ensure_time_series_indexes(self, data_type: str):
        """Ensure proper indexes exist for time series collections"""
        try:
            if data_type == "demand":
                self.demand_serve_collection.create_index([("inserted_date", 1)])
                self.demand_serve_collection.create_index([("created_at", 1)])
                self.demand_serve_collection.create_index([("timestamp", 1)])
                self.demand_serve_collection.create_index([("schedule_date", 1)])
                self.demand_historical_collection.create_index([("archived_timestamp", 1)])
                self.demand_historical_collection.create_index([("archived_at", 1)])
            elif data_type == "supply":
                self.supply_serve_collection.create_index([("inserted_date", 1)])
                self.supply_serve_collection.create_index([("created_at", 1)])
                self.supply_serve_collection.create_index([("timestamp", 1)])
                self.supply_serve_collection.create_index([("schedule_date", 1)])
                self.supply_historical_collection.create_index([("archived_timestamp", 1)])
                self.supply_historical_collection.create_index([("archived_at", 1)])

            logger.info("Time series indexes created/verified")
        except (ConnectionError, TimeoutError) as e:
            logger.warning("Could not create time series indexes: %s", str(e))

    def find_record_by_id(
        self, record_id: str, collection: str = "historical", data_type: str = "demand"
    ) -> Optional[Dict[str, Any]]:
        """Find a record by its original _id in the specified collection"""
        try:
            if collection == "historical":
                if data_type == "demand":
                    record = self.demand_historical_collection.find_one(
                        {"_id": record_id}
                    )
                else:
                    record = self.supply_historical_collection.find_one(
                        {"_id": record_id}
                    )
            elif collection == "demand_serve":
                if data_type == "demand":
                    record = self.demand_serve_collection.find_one({"_id": record_id})
                else:
                    record = self.supply_serve_collection.find_one({"_id": record_id})
            else:
                logger.error("Invalid collection: %s", collection)
                return None

            return record
        except (ConnectionError, TimeoutError) as e:
            logger.error(
                "Error finding record %s in %s: %s", record_id, collection, str(e)
            )
            return None

    def cleanup_duplicates_for_date(
        self, target_date: str, data_type: str = "demand"
    ) -> Dict[str, Any]:
        """Manually clean up duplicates for a specific date (YYYY-MM-DD format)"""
        try:
            target_datetime = datetime.strptime(target_date, "%Y-%m-%d")
            target_datetime = datetime.combine(target_datetime.date(), datetime.min.time())
            duplicates_removed = self._cleanup_duplicates(target_datetime, data_type)

            return {
                "status": "success",
                "date": target_date,
                "duplicates_removed": duplicates_removed,
                "message": f"Cleaned up {duplicates_removed} duplicate records for {target_date}",
            }
        except ValueError as e:
            return {
                "status": "error",
                "date": target_date,
                "error": f"Invalid date format. Use YYYY-MM-DD format: {str(e)}",
            }
        except (ConnectionError, TimeoutError) as e:
            return {
                "status": "error",
                "date": target_date,
                "error": f"Error cleaning up duplicates: {str(e)}",
            }

    def close(self):
        """Close MongoDB connection"""
        if self.client:
            self.client.close()
            logger.info("MongoDB connection closed")

    async def backfill_from_date(
        self, start_date: str, data_type: str
    ) -> Dict[str, Any]:
        """Backfill data from a specific date onwards"""
        try:
            start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
            end_datetime = datetime.now() + timedelta(days=1)

            logger.info("Starting backfill from %s to %s", start_date, end_datetime.date())

            processed_days = 0
            total_records = 0

            current_date = start_datetime
            while current_date <= end_datetime:
                try:
                    data = await self._fetch_data_for_date(current_date, data_type)

                    if data:
                        process_result = self.process_data_for_date(data, current_date, data_type)

                        if process_result["status"] == "success":
                            total_records += process_result.get("records_inserted", 0) + process_result.get("old_records_moved", 0)
                            logger.info("Backfilled %d records for %s",
                                       process_result.get("records_inserted", 0), current_date.date())
                            logger.info("Moved %d old records to historical",
                                       process_result.get("old_records_moved", 0))
                        else:
                            logger.error("Failed to process data for %s: %s",
                                       current_date.date(), process_result.get("error", "Unknown error"))
                    else:
                        logger.info("No data found for %s", current_date.date())

                    processed_days += 1

                except (ConnectionError, TimeoutError) as e:
                    logger.error("Error processing %s: %s", current_date.date(), str(e))

                current_date += timedelta(days=1)

            result = {
                "status": "success",
                "data_type": data_type,
                "start_date": start_date,
                "end_date": end_datetime.date().isoformat(),
                "total_days_processed": processed_days,
                "total_records_inserted": total_records,
                "message": f"Backfill completed: {total_records} records inserted over {processed_days} days",
            }

            logger.info("Backfill completed successfully: %s", result["message"])
            return result

        except (ConnectionError, TimeoutError) as e:
            error_result = {
                "status": "error",
                "data_type": data_type,
                "start_date": start_date,
                "error": str(e),
            }
            logger.error("Backfill failed: %s", str(e))
            return error_result

    def process_data_for_date(
        self,
        data: List[Dict[str, Any]],
        target_date: datetime,
        data_type: str
    ) -> Dict[str, Any]:
        """Process data for a specific date: insert into serve collections and remove old data to historical"""
        try:
            logger.info("Processing data for date: %s", target_date.date())

            processed_data = []
            if data_type == "demand":
                processed_data = [self._process_demand_record(rec) for rec in data]
            elif data_type == "supply":
                processed_data = [self._process_supply_record(rec) for rec in data]
            
            processed_data = self._add_processed_timestamp(processed_data)

            if processed_data:
                logger.info("Inserting %d records into serve", len(processed_data))
                self._insert_new_data(processed_data, data_type)
            else:
                logger.info("No data to insert")

            cutoff_date = target_date - timedelta(days=4)
            logger.info("Removing data older than: %s", cutoff_date.date())

            total_old_records_moved = 0
            if data_type == "demand":
                old_demand_data = demand_serve_repository.delete_demand_serve_by_date(cutoff_date)
                if len(old_demand_data["deleted_records"]) > 0:
                    logger.info("Moving %d old demand records to historical", len(old_demand_data["deleted_records"]))
                    self._move_to_historical(old_demand_data["deleted_records"], "demand")
                    total_old_records_moved += len(old_demand_data["deleted_records"])
                else:
                    logger.info("No old demand data to move to historical")
            elif data_type == "supply":
                old_supply_data = supply_serve_repository.delete_supply_serve_by_date(cutoff_date)
                if len(old_supply_data["deleted_records"]) > 0:
                    logger.info("Moving %d old supply records to historical", len(old_supply_data["deleted_records"]))
                    self._move_to_historical(old_supply_data["deleted_records"], "supply")
                    total_old_records_moved += len(old_supply_data["deleted_records"])
                else:
                    logger.info("No old supply data to move to historical")

            result = {
                "status": "success",
                "target_date": target_date.date().isoformat(),
                "records_inserted": len(processed_data) if processed_data else 0,
                "old_records_moved": total_old_records_moved,
                "cutoff_date": cutoff_date.date().isoformat(),
            }

            logger.info("Data processing completed for %s", target_date.date())
            return result

        except (ConnectionError, TimeoutError) as e:
            logger.error("Error processing data for %s: %s", target_date.date(), str(e))
            return {
                "status": "error",
                "target_date": target_date.date().isoformat(),
                "error": str(e),
            }

    def reconcile_and_rerun_crons(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """Simple reconciliation - just run startup if needed then execute cron"""
        try:
            logger.info("Starting system reconciliation...")

            startup_result = self._process_startup_data_flow(job_id)

            if startup_result["status"] == "success":
                logger.info("Startup reconciliation completed - cron jobs will run on schedule")

                return {
                    "status": "success",
                    "startup_result": startup_result,
                    "message": "Reconciliation completed - jobs scheduled",
                }
            else:
                return startup_result

        except (ConnectionError, TimeoutError) as e:
            error_result = {
                "status": "error",
                "error": str(e),
                "message": "Reconciliation failed",
            }
            logger.error("System reconciliation failed: %s", str(e))
            return error_result

    def execute_demand_data_management(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute the demand data management job (business logic only)"""
        try:
            logger.info("Starting demand data management job")
            if payload:
                logger.info("Job payload: %s", payload)
            
            # Execute demand data processing
            result = self.execute_data_management("demand")
            
            # Add job-specific metrics
            result.update({
                "job_type": "demand_data_management",
                "job_name": "Demand Data Management",
                "tomorrow_data_fetched": result.get("records_inserted", 0),
                "duplicates_removed": 0,  # TODO: Implement duplicate detection
                "data_already_exists": False,  # TODO: Implement existence check
                "removal_success": True,  # TODO: Implement removal tracking
                "insertion_success": result.get("status") == "success",
                "current_demand_serve_records": self._get_current_demand_serve_count()
            })
            
            return result
            
        except (ValueError, TypeError, ConnectionError, TimeoutError) as e:
            logger.error("Error executing demand data management: %s", str(e))
            return {
                "status": "error",
                "job_type": "demand_data_management",
                "job_name": "Demand Data Management",
                "error": str(e)
            }

    def execute_supply_data_management(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute the supply data management job"""
        try:
            logger.info("Starting supply data management job")
            if payload:
                logger.info("Job payload: %s", payload)
            
            # Execute supply data processing
            result = self.execute_data_management("supply")
            
            # Add job-specific metrics
            result.update({
                "job_type": "supply_data_management",
                "job_name": "Supply Data Management",
                "supply_data_processed": result.get("records_inserted", 0),
                "current_supply_serve_records": self._get_current_supply_serve_count()
            })
            
            return result
            
        except (ValueError, TypeError, ConnectionError, TimeoutError) as e:
            logger.error("Error executing supply data management: %s", str(e))
            return {
                "status": "error",
                "job_type": "supply_data_management",
                "job_name": "Supply Data Management",
                "error": str(e)
            }

    def _get_current_demand_serve_count(self) -> int:
        """Get current count of demand_serve records"""
        try:
            return self.demand_serve_collection.count_documents({})
        except (ValueError, TypeError, ConnectionError, TimeoutError) as e:
            logger.error("Error getting demand_serve count: %s", str(e))
            return 0

    def _get_current_supply_serve_count(self) -> int:
        """Get current count of supply_serve records"""
        try:
            return self.supply_serve_collection.count_documents({})
        except (ValueError, TypeError, ConnectionError, TimeoutError) as e:
            logger.error("Error getting supply_serve count: %s", str(e))
            return 0
    
    async def execute_cron_job_demand_data_management(self, job_data_repository, job_detail_repository, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Execute the complete demand data management cron job with full lifecycle management
        This method follows the business rule: execution method should be in the target service class
        
        Args:
            job_data_repository: Repository for job configuration data
            job_detail_repository: Repository for job execution logs
            payload: Optional job payload
            
        Returns:
            Dict[str, Any]: Job execution result
        """
        job_type = "demand_data_management"
        job_name = "Demand Data Management"
        schedule = settings.DEMAND_DATA_CRON_SCHEDULE
        start_time = datetime.now()
        
        # Get job data to retrieve payload and other configuration
        job_data = job_data_repository.get_job(job_type)
        if job_data:
            job_name = job_data.get('name', job_name)
            schedule = job_data.get('schedule', schedule)
            payload = job_data.get('payload', payload)
        
        logger.info("Starting %s cron job at %s", job_type, start_time.strftime('%Y-%m-%d %H:%M:%S'))
        if payload:
            logger.info("Job payload: %s", payload)
        
        # Record job start
        execution_id = self._record_job_start(job_detail_repository, job_type, job_name, schedule, start_time)
        if not execution_id:
            logger.error("Failed to record job start for %s", job_type)
            return {'status': 'error', 'error': 'Failed to record job start'}
        
        try:
            # Execute the business logic
            result = self.execute_demand_data_management(payload=payload)
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            # Log detailed results
            logger.info("Execution results for %s:", job_type)
            logger.info("   - Status: %s", result.get('status', 'unknown'))
            logger.info("   - Execution time: %.2f seconds", execution_time)
            logger.info("   - Tomorrow data fetched: %s", result.get('tomorrow_data_fetched', 0))
            logger.info("   - Duplicates removed: %s", result.get('duplicates_removed', 0))
            logger.info("   - Data already exists: %s", result.get('data_already_exists', False))
            logger.info("   - Removal success: %s", result.get('removal_success', False))
            logger.info("   - Insertion success: %s", result.get('insertion_success', False))
            logger.info("   - Current demand_serve records: %s", result.get('current_demand_serve_records', 0))
            
            success = result.get('status') in ['success', 'partial_success']
            self._record_job_completion(
                job_detail_repository,
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status=result.get('status', 'unknown'),
                success=success,
                result=result
            )
            
            if result['status'] == 'success':
                logger.info("Completed %s successfully in %.2fs", job_type, execution_time)
            elif result['status'] == 'partial_success':
                logger.warning("Completed %s with partial success in %.2fs", job_type, execution_time)
            else:
                logger.error("Failed %s after %.2fs", job_type, execution_time)
            
            # Add execution time to result
            result['execution_time'] = execution_time
            return result
                
        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            logger.error("Exception in %s after %.2fs: %s", job_type, execution_time, str(e))
            
            # Record job failure
            self._record_job_completion(
                job_detail_repository,
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status="error",
                success=False,
                error_message=str(e)
            )
            
            return {
                'status': 'error',
                'error': str(e),
                'execution_time': execution_time
            }
    
    def _record_job_start(self, job_detail_repository, job_type: str, job_name: str, schedule: str, execution_start: datetime) -> Optional[str]:
        """Record job start in audit database"""
        from libs.core.job_entities import JobDetail
        
        try:
            # Get the job_id (ObjectId) from job_data collection using job_type
            job_data = job_data_repository.get_job(job_type)
            if not job_data:
                logger.error("Job data not found for job_type: %s", job_type)
                return None
            
            job_id = job_data.get('_id')
            
            # Generate unique execution ID
            execution_id = f"{job_type}_{execution_start.strftime('%Y%m%d_%H%M%S')}"
            
            job_detail = JobDetail(
                job_id=str(job_id),
                execution_id=execution_id,
                job_name=job_name,
                execution_start=execution_start,
                execution_end=execution_start,
                duration_seconds=0,
                status="processing",
                success=False,
                error_message=None,
                error_details=None,
                result_data={},
                service="scheduler",
                metadata={
                    "timezone": settings.CRON_TIMEZONE,
                    "schedule": schedule,
                    "job_type": job_type,
                    "started_by": "cron_scheduler"
                },
                created_at=datetime.now()
            )
            
            job_detail_repository.log_execution(job_detail)
            logger.info("Recorded job start for %s (job_type: %s, execution_id: %s) in audit database", 
                       job_name, job_type, execution_id)
            
            return execution_id
                
        except Exception as e:
            logger.error("Failed to record job start for %s: %s", job_type, str(e))
            return None
    
    def _record_job_completion(self, job_detail_repository, job_type: str, job_name: str, schedule: str,
                              execution_start: datetime, execution_end: datetime, execution_id: str,
                              status: str, success: bool, result: Optional[Dict[str, Any]] = None,
                              error_message: Optional[str] = None) -> None:
        """Record job completion in audit database"""
        from libs.core.job_entities import JobDetail
        
        try:
            duration_seconds = (execution_end - execution_start).total_seconds()
            
            # Get the job_id (ObjectId) from job_data collection using job_type
            job_data = job_data_repository.get_job(job_type)
            if not job_data:
                logger.error("Job data not found for job_type: %s", job_type)
                return
            
            job_id = job_data.get('_id')
            
            job_detail = JobDetail(
                job_id=str(job_id),
                execution_id=execution_id,
                job_name=job_name,
                execution_start=execution_start,
                execution_end=execution_end,
                duration_seconds=duration_seconds,
                status=status,
                success=success,
                error_message=error_message,
                error_details=None,
                result_data=result or {},
                service="scheduler",
                metadata={
                    "timezone": settings.CRON_TIMEZONE,
                    "schedule": schedule,
                    "job_type": job_type,
                    "completed_by": "cron_scheduler"
                },
                created_at=datetime.now()
            )
            
            job_detail_repository.log_execution(job_detail)
            logger.info("Recorded job completion for %s (job_type: %s, execution_id: %s, status: %s) in audit database",
                       job_name, job_type, execution_id, status)
                
        except Exception as e:
            logger.error("Failed to record job completion for %s: %s", job_type, str(e))


# Global service instance
dummy_data_service = DummyDataService()