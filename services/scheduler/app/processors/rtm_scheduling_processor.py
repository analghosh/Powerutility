import asyncio
import copy
import logging
from datetime import datetime, timedelta, date, timezone
from typing import Dict, Any, List, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from pymongo import ReturnDocument


from .base_processor import BaseProcessor
from ..repository.scheduling_requisition_calculation_repository import SchedulingRequisitionCalculationRepository
from ..repository.supply_serve_repository import supply_serve_repository
from ..repository.rtm_product_hardcoded_data_repository import RTMProductHardCodedDataRepository
from ..repository.master_plants_repository import MasterPlantsRepository
from ..repository.limit_values_repository import LimitValuesRepository
from ..repository.demand_serve_repository import demand_serve_repository
from ..repository.exchange_repository import exchange_repository
from ..helpers.rtm_scheduling_helpers import get_time_block_start_end
from libs.core.job_entities import JobDetail
from libs.models.forecast_serve_entity import map_forecast_source_display
from ..core.config import settings


logger = logging.getLogger(__name__)


def round_numeric_value(value):
    """
    Round numeric values: 2 decimals if decimal, otherwise integer.
    
    Args:
        value: Numeric value to round
        
    Returns:
        int if value is integer, float rounded to 2 decimals otherwise
    """
    if value is None:
        return 0
    try:
        val = float(value)
        # Check if value is effectively an integer (no significant decimal part)
        if abs(val - round(val)) < 0.001:
            return int(round(val))
        else:
            return round(val, 2)
    except (ValueError, TypeError):
        return 0



class RTMSchedulingProcessor(BaseProcessor):
    def __init__(self, collection_name: str = "scheduling_requisition_calculation_table"):
        super().__init__()
        
        # Initialize all repositories (scheduler service pattern)
        self.supply_repo = supply_serve_repository  # Use scheduler's supply repository instance
        self.rtm_session = RTMProductHardCodedDataRepository()
        self.plants_list_repo = MasterPlantsRepository()
        self.limit_values_repo = LimitValuesRepository()
        self._logged_missing_sessions = set()
        self.serve_demand_repo = demand_serve_repository  # Use scheduler's demand repository instance
        self.serve_exchange_repo = exchange_repository  # Use scheduler's exchange repository instance
        self.scheduling_repo = SchedulingRequisitionCalculationRepository(collection_name=collection_name)
        self.snapshot_collection_name = "rtm_optimization_raw_inputs_snapshot"
        self._snapshot_ttl_seconds = 30 * 24 * 60 * 60
        self.last_snapshot_run_id: Optional[str] = None
        self.last_snapshot_session_id: Optional[int] = None
        self._pending_optimized_snapshot_task: Optional[asyncio.Task] = None
        
        # Calculate current 15-minute block index (0-based, 0-95) from current time
        now = datetime.now()
        minutes_since_midnight = now.hour * 60 + now.minute
        today_current_block = minutes_since_midnight // 15  # 0-indexed: block 0 = 00:00-00:15, block 95 = 23:45-24:00
        logger.debug(f"[RTMSchedulingServices] Current time: {now.strftime('%H:%M')}, Current block index: {today_current_block}")
        
        # Set class attributes
        self.currentBlk = today_current_block
        self.shiftingBlockNo = 2           # Start from current block + 2
        self.shiftingBlockNoRuningTo = 5   # Calculate 5 blocks ahead
        self.shiftingBlockNoREQFrom = 5
        self.shiftingBlockNoREQTo = 18
        
        # Scheduling window: from current block, forward by 16 blocks, capped at 95
        self.BlkNoForRunSchedule = min(today_current_block + self.shiftingBlockNo, 95)
        self.BlkNoToRunSchedule = min(today_current_block + self.shiftingBlockNoRuningTo, 95)
        self.BlkNoFromRunReq = min(today_current_block + self.shiftingBlockNoREQFrom, 95)
        self.BlkNoToRunReq = min(today_current_block + self.shiftingBlockNoREQTo, 95)
        
        # Initialize custom demand
        self.customdemand = 0
        
        # Index creation must be awaited in an async setup method, not in __init__

    async def setup(self):
        """Async initialization for background tasks and indexes."""
        await self.create_indexes()

    async def create_indexes(self):
        """Ensure collections exist and create necessary indexes for scheduling calculations."""
        try:
            db = self.scheduling_repo.db
            
            # 1. Working Collection Indexes
            working_collection = db[self.scheduling_repo.collection_name]
            await working_collection.create_index(
                [("scheduled_at", 1), ("time_block", 1), ("plant_id", 1)],
                name="idx_schedule_block_plant",
                background=True
            )
            
            # 2. History Collection
            history_collection_name = "scheduling_requisition_calculation_history"
            # async motor handles automatic collection creation on insert, 
            # but we define index which creates it if missing
            history_collection = db[history_collection_name]
            await history_collection.create_index(
                [("scheduled_at", 1), ("revision_no", 1), ("time_block", 1), ("plant_id", 1)],
                name="idx_hist_schedule_rev_block_plant",
                background=True
            )
            
            # 3. Revision Tracker Collection
            revision_collection_name = "scheduling_revision_tracker"
            revision_collection = db[revision_collection_name]
            await revision_collection.create_index(
                [("scheduled_at_int", 1)],
                name="idx_rev_schedule_int",
                unique=True,
                background=True
            )

            # 4. Raw Input Snapshot Collection (30-day TTL)
            snapshot_collection = db[self.snapshot_collection_name]
            await snapshot_collection.create_index(
                [("run_id", 1)],
                name="idx_rtm_snapshot_run_id",
                background=True
            )
            await snapshot_collection.create_index(
                [("execution_id", 1)],
                name="idx_rtm_snapshot_execution_id",
                background=True
            )
            await snapshot_collection.create_index(
                [("source_name", 1), ("schedule_date", 1)],
                name="idx_rtm_snapshot_source_schedule_date",
                background=True
            )
            await snapshot_collection.create_index(
                [("schedule_date", 1), ("time_block", 1), ("plant_id", 1)],
                name="idx_rtm_snapshot_schedule_block_plant",
                background=True
            )
            await snapshot_collection.create_index(
                [("created_at", 1)],
                name="idx_rtm_snapshot_created_at_ttl_30d",
                expireAfterSeconds=self._snapshot_ttl_seconds,
                background=True
            )
            await snapshot_collection.create_index(
                [("session_id", 1), ("schedule_date", 1)],
                name="idx_rtm_snapshot_session_schedule_date",
                background=True
            )
            await snapshot_collection.create_index(
                [("session_id", 1), ("run_id", 1)],
                name="idx_rtm_snapshot_session_run_id",
                background=True
            )
            await snapshot_collection.create_index(
                [("source_name", 1), ("run_id", 1)],
                name="idx_rtm_snapshot_source_run_id",
                background=True
            )
            
            logger.info("RTM Scheduling indexes verified/created successfully")
        except Exception as e:
            logger.error(f"Failed to create RTM Scheduling indexes: {str(e)}")
        
    async def process_raw_data_to_serve(
        self, 
        raw_data: List[Dict[str, Any]], 
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> bool:
        """
        Implementation of abstract method.
        RTMSchedulingProcessor does not process raw data in this way.
        """
        logger.warning("process_raw_data_to_serve called on RTMSchedulingProcessor - this is unexpected")
        return True

    async def execute_cron_job(self) -> Dict[str, Any]:
        """
        Execute RTM scheduling calculation as a cron job.
        """
        job_type = "rtm_scheduling_calculation"
        job_name = "RTM Scheduling Calculation"
        schedule = "*/30 * * * *"  # Every 30 minutes
        start_time = datetime.now()

        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        if not execution_id:
            logger.error("Failed to record job start for %s", job_type)
            return {
                "status": "error",
                "message": "Failed to record job start",
                "execution_time": 0.0,
                "job_type": job_type
            }

        self.set_correlation_id(execution_id)

        logger.info("=== EXECUTING RTM SCHEDULING CALCULATION JOB ===")

        try:
            # Use yesterday's date as default (matches processing service behavior)
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            
            # Execute the scheduling calculation
            result_data = await self.calSchedulingTableData(yesterday)
            
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            result = {
                "status": "success",
                "message": f"RTM scheduling calculation completed successfully for {yesterday}",
                "execution_time": execution_time,
                "job_type": job_type,
                "schedule_date": yesterday,
                "correlation_id": execution_id,
                "blocks_calculated": len(result_data) if isinstance(result_data, dict) else 0
            }

            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status="success",
                success=True,
                result=result
            )

            return result

        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            error_result = {
                "status": "error",
                "message": f"RTM scheduling calculation error: {str(e)}",
                "execution_time": execution_time,
                "job_type": job_type,
                "correlation_id": execution_id
            }

            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status="error",
                success=False,
                result=error_result,
                error_message=str(e)
            )

            logger.error("Error executing RTM scheduling calculation job: %s", str(e), exc_info=True)
            return error_result
        finally:
            self.clear_correlation_id()

    async def _parse_hhmm_to_minutes(self, value: Any) -> Optional[int]:
        """Parse 'HH:MM' or legacy period id strings to minutes from midnight; 24:00 -> 1440."""
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        parts = s.split(":")
        if len(parts) < 2:
            return None
        try:
            h = int(parts[0])
            m = int(parts[1])
        except ValueError:
            return None
        if h == 24 and m == 0:
            return 1440
        if h < 0 or h > 23 or m < 0 or m > 59:
            return None
        return h * 60 + m

    async def _ensure_tz_run_start(self, run_started_at: Optional[datetime]) -> datetime:
        tz = ZoneInfo(settings.CRON_TIMEZONE)
        if run_started_at is None:
            return datetime.now(tz)
        if run_started_at.tzinfo is None:
            return run_started_at.replace(tzinfo=tz)
        return run_started_at.astimezone(tz)

    async def _session_id_for_run_start(
        self,
        run_started_at: datetime,
        product_rows: List[Dict[str, Any]],
    ) -> Optional[int]:
        """
        Map job/run start wall time to Session_Id from rtm_product_hardCoded_data intervals
        (from_period_time / To_Period_Id, half-open [start, end) in minutes).
        """
        if not product_rows:
            return None
        t_local = await self._ensure_tz_run_start(run_started_at)
        T = t_local.hour * 60 + t_local.minute
        for row in product_rows:
            start_s = row.get("from_period_time") or row.get("From_Period_Id")
            end_s = row.get("to_period_time") or row.get("To_Period_Id")
            start_m = await self._parse_hhmm_to_minutes(start_s)
            end_m = await self._parse_hhmm_to_minutes(end_s)
            if end_s is not None and str(end_s).strip() == "23:59":
                end_m = 1440
            if start_m is None or end_m is None:
                continue
            if end_m <= start_m and end_m != 1440:
                continue
            if start_m <= T < end_m:
                sid = row.get("session_number") if row.get("session_number") is not None else row.get("Session_Id")
                if sid is None:
                    return None
                try:
                    return int(sid)
                except (ValueError, TypeError):
                    try:
                        return int(float(sid))
                    except (ValueError, TypeError):
                        return None
        logger.warning(
            "[RTMSchedulingServices] No hardcoded product interval matched run start %s (T=%s min); session_id unset",
            t_local.isoformat(),
            T,
        )
        return None

    async def _build_snapshot_documents(
        self,
        records: Any,
        source_name: str,
        run_id: str,
        execution_id: str,
        schedule_date: str,
        session_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        now = datetime.now()
        docs: List[Dict[str, Any]] = []

        if source_name in {"supply_sg_dc_raw", "supply_dc_raw"} and isinstance(records, list):
            for block in records:
                if not isinstance(block, dict):
                    continue
                block_num = block.get("time_block")
                plants = block.get("result", [])
                if isinstance(plants, list):
                    for plant_row in plants:
                        if not isinstance(plant_row, dict):
                            continue
                        docs.append({
                            "run_id": run_id,
                            "execution_id": execution_id,
                            "job_type": "rtm_scheduling_calculation",
                            "schedule_date": schedule_date,
                            "session_id": session_id,
                            "source_name": source_name,
                            "time_block": block_num,
                            "plant_id": plant_row.get("key") or plant_row.get("plant_id"),
                            "raw_payload": dict(plant_row),
                            "created_at": now,
                        })
                else:
                    docs.append({
                        "run_id": run_id,
                        "execution_id": execution_id,
                        "job_type": "rtm_scheduling_calculation",
                        "schedule_date": schedule_date,
                        "session_id": session_id,
                        "source_name": source_name,
                        "time_block": block_num,
                        "plant_id": None,
                        "raw_payload": dict(block),
                        "created_at": now,
                    })
            return docs

        if not isinstance(records, list):
            records = [records]

        for row in records:
            if not isinstance(row, dict):
                continue
            docs.append({
                "run_id": run_id,
                "execution_id": execution_id,
                "job_type": "rtm_scheduling_calculation",
                "schedule_date": schedule_date,
                "session_id": session_id,
                "source_name": source_name,
                "time_block": row.get("time_block", row.get("block")),
                "plant_id": row.get("plant_id", row.get("key")),
                "raw_payload": dict(row),
                "created_at": now,
            })
        return docs

    async def _store_raw_snapshot(
        self,
        records: Any,
        source_name: str,
        run_id: str,
        execution_id: str,
        schedule_date: str,
        session_id: Optional[int] = None,
    ) -> int:
        docs = await self._build_snapshot_documents(
            records=records,
            source_name=source_name,
            run_id=run_id,
            execution_id=execution_id,
            schedule_date=schedule_date,
            session_id=session_id,
        )
        if not docs:
            return 0
        collection = self.scheduling_repo.db[self.snapshot_collection_name]
        await collection.insert_many(docs, ordered=False)
        logger.info(
            "[RTMSchedulingServices] Snapshot captured | source=%s | count=%s | run_id=%s",
            source_name,
            len(docs),
            run_id
        )
        return len(docs)

    async def _store_scheduling_optimized_snapshots(
        self,
        all_insert_data: List[Dict[str, Any]],
        run_id: str,
        execution_id: str,
        schedule_date: str,
        session_id: Optional[int],
    ) -> int:
        """Persist optimizer output rows into the same snapshot collection as raw inputs."""
        if not all_insert_data:
            return 0
        now = datetime.now()
        collection = self.scheduling_repo.db[self.snapshot_collection_name]
        docs: List[Dict[str, Any]] = []
        for row in all_insert_data:
            payload = copy.deepcopy(row)
            docs.append({
                "run_id": run_id,
                "execution_id": execution_id,
                "job_type": "rtm_scheduling_calculation",
                "schedule_date": schedule_date,
                "session_id": session_id,
                "source_name": "scheduling_optimized_row",
                "time_block": row.get("time_block"),
                "plant_id": row.get("plant_id"),
                "raw_payload": payload,
                "created_at": now,
            })
        await collection.insert_many(docs, ordered=False)
        logger.info(
            "[RTMSchedulingServices] Optimized snapshot captured | count=%s | run_id=%s",
            len(docs),
            run_id,
        )
        return len(docs)


        
    async def __build_limit_value_dict(self, limit_values_list, include_time_block=True):
        """Convert list of limit values to dictionary with composite keys using ID field"""
        result = {}
        for item in limit_values_list:
            schedule_date = item.get("scheduled_at", "")
            limit_id = item.get("id", "")  # Use ID field from limit_values (not plant_id)
            time_block = item.get("block", "")
            value = item.get("values", item.get("value", 0))  #  uses 'values' field
            
            if include_time_block:
                key = f"{schedule_date}_{limit_id}_{time_block}"
            else:
                key = f"{schedule_date}_{limit_id}"
            result[key] = value
        return result
    
    async def archive_and_clear_working(self, target_date: datetime):
        try:
            db = self.scheduling_repo.db
            working_collection = db[self.scheduling_repo.collection_name]
            history_collection = db["scheduling_requisition_calculation_history"]
            revision_collection = db["scheduling_revision_tracker"]

            start_of_day = datetime.combine(target_date.date(), datetime.min.time())
            end_of_day = start_of_day + timedelta(days=1)

            scheduled_at_int = (
                target_date.year * 10000
                + target_date.month * 100
                + target_date.day
            )

            # Fetch existing records
            existing_records = await working_collection.find({
                "scheduled_at": {
                    "$gte": start_of_day,
                    "$lt": end_of_day
                }
            }).to_list(length=10000)

            if not existing_records:
                return {"archived": 0, "deleted": 0, "revision_no": None}

            # Atomic revision increment
            now = datetime.now()
            revision_doc = await revision_collection.find_one_and_update(
                {"scheduled_at_int": scheduled_at_int},
                {
                    "$inc": {"latest_revision_no": 1},
                    "$set": {"updated_at": now},
                    "$setOnInsert": {
                        "scheduled_at_int": scheduled_at_int,
                        "created_at": now
                    }
                },
                upsert=True,
                return_document=ReturnDocument.AFTER
            )

            revision_no = revision_doc["latest_revision_no"]
            run_timestamp = now
            run_id = run_timestamp.strftime("%Y%m%d%H%M%S")

            # Prepare records
            for record in existing_records:
                record.pop("_id", None)
                record["revision_no"] = revision_no
                record["run_id"] = run_id
                record["run_timestamp"] = run_timestamp
                record["archived_at"] = now

            # Insert into history
            if existing_records:
                await history_collection.insert_many(existing_records, ordered=False)

            # Delete working records
            delete_result = await working_collection.delete_many({
                "scheduled_at": {
                    "$gte": start_of_day,
                    "$lt": end_of_day
                }
            })

            return {
                "archived": len(existing_records),
                "deleted": delete_result.deleted_count,
                "revision_no": revision_no
            }
        except Exception as e:
            logger.error(f"Error in archive_and_clear_working: {str(e)}")
            return {"archived": 0, "deleted": 0, "revision_no": None, "error": str(e)}

    async def calSchedulingTableData(self, schedule_date, audit_logger=None, user_context: Optional[Dict[str, Any]] = None, run_with_exchange: bool = False, run_started_at: Optional[datetime] = None):
        try:
            run_started_aware = await self._ensure_tz_run_start(run_started_at)
            execution_id = self.get_correlation_id() or f"adhoc_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            run_id = f"rtm_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid4().hex[:8]}"
            self.last_snapshot_run_id = run_id
            getsessionNoRaw = await self.rtm_session.get_Data()
            if getsessionNoRaw is None:
                getsessionNoRaw = []
            elif not isinstance(getsessionNoRaw, list):
                logger.warning(
                    "[RTMSchedulingServices] get_Data() returned type %s; using empty list for session mapping",
                    type(getsessionNoRaw).__name__,
                )
                getsessionNoRaw = []
            logger.info(f"[RTMSchedulingServices] Retrieved {len(getsessionNoRaw)} RTM session records")
            snapshot_session_id = await self._session_id_for_run_start(run_started_aware, getsessionNoRaw)
            self.last_snapshot_session_id = snapshot_session_id
            logger.info(
                "[RTMSchedulingServices] Snapshot session_id=%s for run_start=%s",
                snapshot_session_id,
                run_started_aware.isoformat(),
            )
            snapshot_storage_date = (await self._ensure_tz_run_start(run_started_aware)).strftime("%Y-%m-%d")
            self._pending_optimized_snapshot_task = None
            if snapshot_session_id is not None:
                snapshot_coll = self.scheduling_repo.db[self.snapshot_collection_name]
                delete_result = await snapshot_coll.delete_many({"session_id": snapshot_session_id})
                logger.info(
                    "[RTMSchedulingServices] Snapshot replace-by-session | session_id=%s | removed=%s",
                    snapshot_session_id,
                    delete_result.deleted_count,
                )
            await self._store_raw_snapshot(
                getsessionNoRaw, "rtm_session_raw", run_id, execution_id, snapshot_storage_date, snapshot_session_id
            )

            # Transform session data to dictionary keyed by from_period_code (time_block)
            # Handle both new (session_number, from_period_code) and legacy (Session_Id, From_Period_Code) fields
            getsessionNo = {}
            for session_item in getsessionNoRaw:
                time_block = session_item.get("from_period_code") or session_item.get("From_Period_Code")
                session_number = session_item.get("session_number") or session_item.get("Session_Id")
                if time_block is not None:
                    getsessionNo[time_block] = {
                        "session_number": session_number,
                        "Session_Id": session_number  # Keep both for backward compatibility
                    }
            
            # Fetch all documents from plants_list
            logger.info("[RTMSchedulingServices] Fetching all plant data from master plants repository")
            getPlantListRaw = await self.plants_list_repo.get_all_plants()
            logger.info(f"[RTMSchedulingServices] Retrieved {len(getPlantListRaw)} plant records")
            await self._store_raw_snapshot(
                getPlantListRaw, "plant_master_raw", run_id, execution_id, snapshot_storage_date, snapshot_session_id
            )
            
            # Transform plant list - ensure each plant has a "key" field set to plant_id
            # getPlantList needs to be a list (for iteration) but also accessible by plant_id
            getPlantList = []
            getPlantListDict = {}  # For dict-style access like get_plant_list[key]
            for plant in getPlantListRaw:
                plant_id = plant.get("plant_id")
                if plant_id:
                    # Add "key" field for compatibility with code that uses p.get("key")
                    plant["key"] = plant_id
                    getPlantList.append(plant)
                    getPlantListDict[plant_id] = plant
            
            mustplantPrice = "20"

            yesterday = date.today() - timedelta(days=1)
            tomorrow  = date.today() + timedelta(days=1)

            # ----Archive Existing Data to Hisorical collection + Delete all data from working Collection ------
            archive_result = await self.archive_and_clear_working(
                datetime.combine(date.today(), datetime.min.time())
            )

            logger.info(
                f"[RTMSchedulingServices] Archive Complete | "
                f"Revision: {archive_result['revision_no']} | "
                f"Archived: {archive_result['archived']} | "
                f"Deleted: {archive_result['deleted']}"
            )
        
            
            logger.info("[RTMSchedulingServices] Fetching limit values - TM, SEZ, MLTP")
            allTMValuesRaw = await self.limit_values_repo.get_data(yesterday, tomorrow, "RTM", "TM", "Puducherry")
            logger.info(f"[RTMSchedulingServices] Retrieved {len(allTMValuesRaw)} TM limit values")
            await self._store_raw_snapshot(
                allTMValuesRaw, "limit_tm_raw", run_id, execution_id, snapshot_storage_date, snapshot_session_id
            )
            allSEZValuesRaw = await self.limit_values_repo.get_data(yesterday, tomorrow, "DAM", "SEZ", "Puducherry")
            logger.info(f"[RTMSchedulingServices] Retrieved {len(allSEZValuesRaw)} SEZ limit values")
            await self._store_raw_snapshot(
                allSEZValuesRaw, "limit_sez_raw", run_id, execution_id, snapshot_storage_date, snapshot_session_id
            )
            allMLTPValuesRaw = await self.limit_values_repo.get_data(yesterday, tomorrow, "RTM", "Multiplier", "Puducherry")
            logger.info(f"[RTMSchedulingServices] Retrieved {len(allMLTPValuesRaw)} MLTP limit values")
            await self._store_raw_snapshot(
                allMLTPValuesRaw, "limit_mltp_raw", run_id, execution_id, snapshot_storage_date, snapshot_session_id
            )
            
            # Convert limit values to dictionaries keyed by composite key: "schedule_date_ID_time_block"
            # Format: { "schedule_date_ID_time_block": value, ... }
            # Note:  uses ID field from limit_values (not plant_id), matching  logic:
            # $allTMValues[$getplantTMas["schedule_date"]."_".$getplantTMas["ID"]."_".$getplantTMas["block"]] = $getplantTMas['values'];
            
            
            # Build dictionaries with composite keys
            allTMValues = await self.__build_limit_value_dict(allTMValuesRaw, include_time_block=True)
            allSEZValues = await self.__build_limit_value_dict(allSEZValuesRaw, include_time_block=True)
            allMLTPValues = await self.__build_limit_value_dict(allMLTPValuesRaw, include_time_block=False)  # MLTP doesn't have time_block
            logger.info(f"[RTMSchedulingServices] Converted limit values to dictionaries - TM: {len(allTMValues)}, SEZ: {len(allSEZValues)}, MLTP: {len(allMLTPValues)}")
            
            
            # Apply 22:30 PM cutoff logic to match PHP's ServeDataModel->getAllPlantDataForScheduling behavior
            # PHP logic: After 22:30, only fetch SG data; Before 22:30, fetch both SG and DC
            now = datetime.now()
            now_time = now.time()
            cutoff_time = datetime.strptime("22:30", "%H:%M").time()
            is_after_cutoff = now_time > cutoff_time
            getplantsData = [] 
            dc_sg = "DC_SG"
            if is_after_cutoff:
                logger.info(f"[RTMSchedulingServices] After 22:30 cutoff - Skipping DC data fetch (only SG data as per PHP logic)")
                dc_sg = "SG"

            logger.info(f"[RTMSchedulingServices] Fetching plant data for {dc_sg} (Schedule Date: {schedule_date})")
            getplantsData = await self.getAllPlantsDataForScheduling(schedule_date, dc_sg)
            logger.info(f"[RTMSchedulingServices] Retrieved {len(getplantsData)} {dc_sg} plant data records")
            await self._store_raw_snapshot(
                getplantsData, "supply_sg_dc_raw", run_id, execution_id, snapshot_storage_date, snapshot_session_id
            )
            
            logger.info(f"[RTMSchedulingServices] Fetching plant data for DC (Schedule Date: {schedule_date})")
            getplantsDataDC = await self.getAllPlantsDataForScheduling(schedule_date, "DC")
            logger.info(f"[RTMSchedulingServices] Retrieved {len(getplantsDataDC)} DC plant data records")
            await self._store_raw_snapshot(
                getplantsDataDC, "supply_dc_raw", run_id, execution_id, snapshot_storage_date, snapshot_session_id
            )

            today_dt = datetime.combine(datetime.now().date(), datetime.min.time())
            demand_schedule_date = today_dt.strftime("%Y-%m-%d")
            logger.info(f"[RTMSchedulingServices] Fetching demand forecast and actual data (Using today: {demand_schedule_date})")

            get_forecasted_list = await self.serve_demand_repo.get_forcasted_demand(today_dt)
            get_actual_list = await self.serve_demand_repo.get_actual_demand(today_dt)
            await self._store_raw_snapshot(
                get_forecasted_list or [], "demand_forecast_raw", run_id, execution_id, snapshot_storage_date, snapshot_session_id
            )
            await self._store_raw_snapshot(
                get_actual_list or [], "demand_actual_raw", run_id, execution_id, snapshot_storage_date, snapshot_session_id
            )
            
            get_forecasted_value = {item.get("time_block"): item for item in (get_forecasted_list or []) if item.get("time_block") is not None}
            
            get_actual_value = {}
            for item in (get_actual_list or []):
                tb = item.get("time_block")
                if tb is not None:
                    actual_val = item.get("comp_act_demand")
                    if actual_val is not None and float(actual_val) > 0:
                        act_item = dict(item)
                        act_item["forecast"] = float(actual_val)
                        get_actual_value[tb] = act_item

            # Blend the demand based on the current block
            blended_demand = []
            for blk in range(96):
                time_block_num = blk + 1
                if blk < self.currentBlk:
                    # Past blocks: Use actual demand (fallback to forecast if actual is missing)
                    actual = get_actual_value.get(time_block_num)
                    blended_demand.append(actual if actual is not None else get_forecasted_value.get(time_block_num))
                else:
                    # Future blocks: Use forecast demand
                    blended_demand.append(get_forecasted_value.get(time_block_num))
            
            # Filter out None values to prevent issues in total_availability
            blended_demand = [d for d in blended_demand if d is not None]

            logger.info(f"[RTMSchedulingServices] Retrieved demand records, proceeding to calculate total availability.")
            totalAvailability = await self.total_availability(getplantsData, blended_demand)
            logger.info(f"[RTMSchedulingServices] Calculated total availability for {len(totalAvailability)} time blocks")
            # --- OLD: RTM forecasted price from exchange_serve (yesterday's IEX MCP) — kept for reference ---
            # rtm_forecasted_price_list = await self.limit_values_repo.get_data_byID_with_latest_date(
            #     "RTM_forecasted_price", "RTM", "RTM", "Puducherry", todayt
            # )
            # logger.info("[RTMSchedulingServices] Fetching RTM forecasted price from Exchange Serve table (Yesterday's MCP)")
            # # Get RTM forecasted price from exchange_serve: market="RTM", using yesterday's actual MCP
            # yesterday_dt = datetime.combine(datetime.now().date() - timedelta(days=1), datetime.min.time())
            # rtm_exchange_records = self.serve_exchange_repo.get_market_data_by_date(
            #     yesterday_dt, "RTM", px_name="IEX"
            # )
            # logger.info(
            #     f"[RTMSchedulingServices] Retrieved {len(rtm_exchange_records)} RTM exchange records for {yesterday_dt.date()}"
            # )
            # # Convert list to dictionary keyed by block number for easier access
            # # Use only 'mcp' for RTM forecasted price mapping
            # rtm_forecasted_price = {}
            # for item in rtm_exchange_records:
            #     block = item.get("time_block")
            #     mcp = item.get("mcp", 0.0)
            #     if block is not None:
            #         rtm_forecasted_price[int(block)] = {
            #             "block": block,
            #             "value": mcp,
            #             "values": mcp,
            #         }
            # logger.info(
            #     f"[RTMSchedulingServices] Converted RTM exchange data to dictionary with {len(rtm_forecasted_price)} blocks"
            # )
            # --- END OLD ---

            # RTM forecasted price from limit_values (id=RTM_forecasted_price), today's schedule — not exchange yesterday MCP
            # category="" so documents with category "Limits" (ingestion) match; area=Puducherry
            rtm_forecasted_price_list = await self.limit_values_repo.get_data_byID_with_latest_date(
                "RTM_forecasted_price", "RTM", "", "Puducherry", today_dt
            )
            logger.info(
                f"[RTMSchedulingServices] Retrieved {len(rtm_forecasted_price_list)} RTM_forecasted_price "
                f"limit_values rows for {demand_schedule_date}"
            )
            await self._store_raw_snapshot(
                rtm_forecasted_price_list or [],
                "rtm_forecasted_price_raw",
                run_id,
                execution_id,
                snapshot_storage_date,
                snapshot_session_id,
            )

            # Convert list to dictionary keyed by block (1–96); SchedulingCalculator reads 'values' or 'value'
            rtm_forecasted_price = {}
            for item in rtm_forecasted_price_list or []:
                block = item.get("block") if item.get("block") is not None else item.get("time_block")
                raw_price = item.get("values")
                if raw_price is None:
                    raw_price = item.get("value")
                if raw_price is None:
                    raw_price = item.get("mcp", 0.0)
                try:
                    price = float(raw_price or 0)
                except (ValueError, TypeError):
                    price = 0.0
                if block is not None:
                    try:
                        blk_int = int(block)
                    except (ValueError, TypeError):
                        continue
                    rtm_forecasted_price[blk_int] = {
                        "block": blk_int,
                        "value": price,
                        "values": price,
                    }
            logger.info(
                f"[RTMSchedulingServices] Built rtm_forecasted_price map from limit_values with {len(rtm_forecasted_price)} blocks"
            )
            
            blk_no_for_run_schedule = 1
            blk_no_to_run_schedule = 96
            
            logger.info(f"[RTMSchedulingServices] Starting SchedulingCalculator - Block range: {blk_no_for_run_schedule} to {blk_no_to_run_schedule}")
            reviseDataTotalsupply = await self.SchedulingCalculator(
                total_availability=totalAvailability,
                rtm_forecasted_price=rtm_forecasted_price,
                get_plants_data=getplantsData,
                get_plants_data_dc=getplantsDataDC,
                all_tm_values=allTMValues,
                all_sez_values=allSEZValues,
                all_mltp_values=allMLTPValues,
                get_plant_list=getPlantListDict,
                get_session_no=getsessionNo,
                is_after_cutoff=is_after_cutoff,
                run_with_exchange=run_with_exchange,
                snapshot_run_id=run_id,
                snapshot_execution_id=execution_id,
                snapshot_schedule_date=snapshot_storage_date,
                snapshot_session_id=snapshot_session_id,
            )
            if self._pending_optimized_snapshot_task is not None:
                await self._pending_optimized_snapshot_task
                self._pending_optimized_snapshot_task = None
            logger.info(f"[RTMSchedulingServices] SchedulingCalculator completed - Generated scheduling records")
            logger.info(f"[RTMSchedulingServices] Completed calSchedulingTableData successfully - Schedule Date: {schedule_date}")
            # RePturn reviseDataTotalsupply to match V1 behavior
            # V1 returns the reviseDataTotalsupply dictionary directly
            return reviseDataTotalsupply
        except Exception as e:
            # Check if it's already an HTTPException
            from fastapi import HTTPException, status
            if isinstance(e, HTTPException):
                # Re-raise HTTPExceptions as-is (they have proper status codes)
                raise
            
            logger.exception(f"[RTMSchedulingServices] Unexpected error in calSchedulingTableData - Schedule Date: {schedule_date}, Error: {str(e)}")
            # Raise as HTTPException with 500 status
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to calculate RTM scheduling table data: {str(e)}"
            )
    
    async def __get_plant_rmp_value(self, plant_data, plant_list_dict):
        """Get plant RMP value from plant_master.ramping only (no fallback)"""
        plant_key = plant_data.get("key")
        if plant_key and plant_key in plant_list_dict:
            plant_master_data = plant_list_dict[plant_key]
            # Get RMP from plant_master.ramping field only
            rmp_value = plant_master_data.get("ramping", None)
            # Handle string "None" values from supply_serve
            if rmp_value is not None and rmp_value != "None" and str(rmp_value).lower() != "none":
                try:
                    return float(rmp_value)
                except (ValueError, TypeError):
                    return 0
        return 0  # Return 0 if not found in plant_master (no fallback)
    
    # Helper function to safely get multiplier value (handles string "None")
    async def __get_multiplier_value(self,value, default=1):
        """Safely convert multiplier value, handling string 'None' and other edge cases"""
        if value is None:
            return default
        if isinstance(value, str):
            if value.lower() in ["none", "null", ""]:
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default
        try:
            val = float(value)
            return val if val > 0 else default
        except (ValueError, TypeError):
            return default
    
    # Helper function to create standardized record with ONLY new standard field names
    async def __create_standardized_record(self, base_data, is_after_cutoff=False) -> Dict[str, Any]:
        """
        Creates a record with ONLY standard field names (new field names).
        Old field names are NOT stored in database.
        scheduled_at is stored as datetime object (consistent with other v2 tables).
        """
        schedule_date_val = base_data.get("schedule_date", base_data.get("scheduled_at", ""))
        if schedule_date_val:
            try:
                if isinstance(schedule_date_val, datetime):
                    schedule_date_dt = schedule_date_val
                elif isinstance(schedule_date_val, str):
                    schedule_date_dt = datetime.strptime(schedule_date_val, "%Y-%m-%d")
                else:
                    schedule_date_dt = datetime.now()
            except (ValueError, TypeError):
                schedule_date_dt = datetime.now()
        else:
            schedule_date_dt = datetime.now()
        
        record = {}
        
        # Map to NEW standard field names only (no old field names)
        # Date fields - scheduled_at is datetime object (consistent with v2 pattern)
        record["scheduled_at"] = schedule_date_dt  # Store as datetime object
        record["scheduled_at_timestamp"] = int(schedule_date_dt.timestamp())
        record["scheduled_at_int"] = int(schedule_date_dt.strftime("%Y%m%d"))
        
        # Volume and forecast fields - use new names only
        if "demand_forecast" in base_data or base_data.get("demand_forecast") is not None:
            record["demand_forecast_mw"] = base_data.get("demand_forecast")
        if "forecast_source" in base_data:
            record["forecast_source"] = base_data.get("forecast_source")
        if "total_supply_availablity" in base_data or base_data.get("total_supply_availablity") is not None:
            record["total_supply_available_mw"] = base_data.get("total_supply_availablity")
        if "surplus_deficit" in base_data or base_data.get("surplus_deficit") is not None:
            record["net_surplus_deficit_mw"] = base_data.get("surplus_deficit")
        if "surplus_deficit_updated" in base_data or base_data.get("surplus_deficit_updated") is not None:
            record["updated_surplus_deficit_mw"] = base_data.get("surplus_deficit_updated")
        if "total_availablity_updated" in base_data or base_data.get("total_availablity_updated") is not None:
            record["updated_total_supply_mw"] = base_data.get("total_availablity_updated")
        if "remaining_volume" in base_data or base_data.get("remaining_volume") is not None:
            record["remaining_volume_mw"] = base_data.get("remaining_volume")
        
        # Price fields - store as "price" (old field name) instead of market_clearing_price
        if "price" in base_data or base_data.get("price") is not None:
            record["price"] = base_data.get("price")  # Keep as "price" not "market_clearing_price"
        if "rtm_forecasted_price" in base_data or base_data.get("rtm_forecasted_price") is not None:
            record["rtm_forecasted_price"] = base_data.get("rtm_forecasted_price")  # Keep as "rtm_forecasted_price" not "forecasted_rtm_price"
        
        # Plant value fields - use new names only
        if "plant_RMP_value" in base_data or base_data.get("plant_RMP_value") is not None:
            record["plant_rmp_value"] = base_data.get("plant_RMP_value")
        if "plant_TM_value" in base_data or base_data.get("plant_TM_value") is not None:
            record["plant_tm_value"] = base_data.get("plant_TM_value")
        if "ceiling_price" in base_data or base_data.get("ceiling_price") is not None:
            record["ceiling_price"] = base_data.get("ceiling_price")  # Store ceiling price from limit_values
        if "plant_SEZ_value" in base_data or base_data.get("plant_SEZ_value") is not None:
            record["plant_sez_value"] = base_data.get("plant_SEZ_value")
        if "plant_MLTP_value" in base_data or base_data.get("plant_MLTP_value") is not None:
            record["plant_mltp_value"] = base_data.get("plant_MLTP_value")
        
        # Volume fields - use new names only
        if "up_down_RMP_volume" in base_data or base_data.get("up_down_RMP_volume") is not None:
            record["rmp_adjusted_volume_mw"] = base_data.get("up_down_RMP_volume")
        if "Final_plant_RMP_volume" in base_data or base_data.get("Final_plant_RMP_volume") is not None:
            record["final_rmp_volume_mw"] = base_data.get("Final_plant_RMP_volume")
        if "genrated_sg_volume" in base_data or base_data.get("genrated_sg_volume") is not None:
            record["generated_sg_volume_mw"] = base_data.get("genrated_sg_volume")
        if "actual_plant_volume" in base_data or base_data.get("actual_plant_volume") is not None:
            record["actual_plant_volume_mw"] = base_data.get("actual_plant_volume")
        if "actual_plant_volume_DC" in base_data or base_data.get("actual_plant_volume_DC") is not None:
            record["actual_dc_volume_mw"] = base_data.get("actual_plant_volume_DC")
        if "total_RMP_volume" in base_data or base_data.get("total_RMP_volume") is not None:
            record["total_rmp_volume_mw"] = base_data.get("total_RMP_volume")
        
        # Category and identifier fields - use new names only
        if "category" in base_data or base_data.get("category") is not None:
            record["category"] = base_data.get("category")  # Database field is category
        if "key" in base_data or base_data.get("key") is not None:
            record["plant_id"] = base_data.get("key")
        
        # Scheduling type - use new name only
        scheduling_type = base_data.get("scheduling_requisition_type", base_data.get("scheduling_req_type", "NA"))
        record["scheduling_req_type"] = scheduling_type
        record["area"] = "PUDUCHERRY"
        # Fields that don't change (keep as-is)
        unchanged_fields = ["time_block", "dc_sg_type", "session_number", "start_time", "end_time", 
                            "full_time", "plant_name", "display_category", "dispatch_type", 
                            "revision_no_SG", "revision_no_DC", "plant_min_rum_volume"]
        for field in unchanged_fields:
            if field in base_data:
                record[field] = base_data[field]
        
        # Add timestamps
        now = datetime.now()
        record["created_at"] = now
        record["updated_at"] = now
        
        # Add metadata to identify records calculated after 22:30 (for tomorrow's date)
        # This helps identify that the calculation was run after cutoff time
        record["calculated_after_cutoff"] = is_after_cutoff
        if is_after_cutoff:
            # Store the effective date (tomorrow) for reference
            # scheduled_at is yesterday's date (from data), but calculation is for tomorrow
            effective_date = (datetime.now() + timedelta(days=1)).date()
            record["effective_schedule_date"] = effective_date.strftime("%Y-%m-%d")
            record["effective_schedule_date_int"] = int(effective_date.strftime("%Y%m%d"))
        
        return record
    
    # Helper function to transform new field names back to old field names for API response
    async def __transform_to_legacy_format(self, data):
        """
        Transforms records with new standard field names back to old field names for API compatibility.
        Used only for API responses to maintain frontend compatibility.
        """
        if isinstance(data, dict):
            transformed = {}
            # Map new field names back to old field names for API response
            field_mapping = {
                # Date fields
                "scheduled_at": "schedule_date",
                "scheduled_at_timestamp": "schedule_date_timestamp",
                "scheduled_at_int": "schedule_date_Int",
                # Volume and forecast fields
                "demand_forecast_mw": "demand_forecast",
                "total_supply_available_mw": "total_supply_availablity",
                "net_surplus_deficit_mw": "surplus_deficit",
                "updated_surplus_deficit_mw": "surplus_deficit_updated",
                "updated_total_supply_mw": "total_availablity_updated",
                "remaining_volume_mw": "remaining_volume",
                # Price fields - keep as "price" and "rtm_forecasted_price" (not renamed)
                "price": "price",
                "rtm_forecasted_price": "rtm_forecasted_price",
                # Plant value fields
                "plant_rmp_value": "plant_RMP_value",
                "plant_tm_value": "plant_TM_value",
                "plant_sez_value": "plant_SEZ_value",
                "plant_mltp_value": "plant_MLTP_value",
                # Volume fields
                "rmp_adjusted_volume_mw": "up_down_RMP_volume",
                "final_rmp_volume_mw": "Final_plant_RMP_volume",
                "generated_sg_volume_mw": "genrated_sg_volume",
                "actual_plant_volume_mw": "actual_plant_volume",
                "actual_dc_volume_mw": "actual_plant_volume_DC",
                "total_rmp_volume_mw": "total_RMP_volume",
                # Category and identifier fields
                "category": "category",
                "plant_id": "key",
                # Scheduling type
                "scheduling_req_type": "scheduling_requisition_type",
            }
            
            for new_field, old_field in field_mapping.items():
                if new_field in data:
                    value = data[new_field]
                    # Convert scheduled_at datetime to string format for API response
                    if new_field == "scheduled_at" and isinstance(value, datetime):
                        transformed[old_field] = value.strftime("%Y-%m-%d")
                    else:
                        transformed[old_field] = value
            
            # Copy fields that don't change
            unchanged_fields = ["time_block", "dc_sg_type", "session_number", "start_time", "end_time", 
                                "full_time", "plant_name", "display_category", "dispatch_type", 
                                "revision_no_SG", "revision_no_DC", "plant_min_rum_volume", 
                                "created_at", "updated_at", "_id"]
            for field in unchanged_fields:
                if field in data:
                    value = data[field]
                    # Convert datetime fields to ISO string if needed
                    if field in ["created_at", "updated_at"] and isinstance(value, datetime):
                        transformed[field] = value.isoformat() if hasattr(value, 'isoformat') else str(value)
                    else:
                        transformed[field] = value
            
            # Copy any remaining fields that weren't in mapping
            for key, value in data.items():
                if key not in field_mapping and key not in unchanged_fields:
                    if isinstance(value, (dict, list)):
                        transformed[key] = await self.__transform_to_legacy_format(value)
                    elif isinstance(value, datetime):
                        # Handle datetime objects in remaining fields
                        transformed[key] = value.strftime("%Y-%m-%d") if key.endswith("_at") else value.isoformat()
                    else:
                        transformed[key] = value
            
            return transformed
        elif isinstance(data, list):
            return [await self.__transform_to_legacy_format(item) for item in data]
        else:
            return data
    
    # Helper function to safely get next block data
    async def __get_next_block_volume(self,blk_idx, plnamekey, get_plants_data, reversed_order=False):
        """Safely get volume from next block, returning 0 if out of bounds"""
        if blk_idx + 1 < len(get_plants_data):
            next_block_data = get_plants_data[blk_idx + 1]
            if isinstance(next_block_data, dict) and "result" in next_block_data:
                result = next_block_data["result"]
                if reversed_order:
                    result = list(reversed(result))
            elif hasattr(next_block_data, 'result'):
                result = next_block_data.result
                if reversed_order:
                    result = list(reversed(result))
            else:
                return 0
            
            if isinstance(result, list) and plnamekey < len(result):
                if isinstance(result[plnamekey], dict):
                    return result[plnamekey].get("volume", 0)
                else:
                    return getattr(result[plnamekey], "volume", 0)
            elif hasattr(result, '__contains__') and plnamekey in result:
                # Handle case where result is a sequence with __contains__
                if isinstance(result[plnamekey], dict):
                    return result[plnamekey].get("volume", 0)
                else:
                    return getattr(result[plnamekey], "volume", 0)
        return 0
    
        
    
    async def SchedulingCalculator(self,
            total_availability,
            rtm_forecasted_price,
            get_plants_data,
            get_plants_data_dc,
            all_tm_values,
            all_sez_values,
            all_mltp_values,
            get_plant_list,
            get_session_no,
            is_after_cutoff=False,
            run_with_exchange=False,
            snapshot_run_id=None,
            snapshot_execution_id=None,
            snapshot_schedule_date=None,
            snapshot_session_id=None):
        
        reviseDataArray = {}
        reviseDataTotalsupply = {}
        all_insert_data = []
        block_exchange_updates = []
        today_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        # Pre-process DC data into a dictionary for fast lookup
        # Structure: { time_block_int: { plant_key: { ramp_down, ramp_up, dc_afterloss, ... } } }
        dc_data_map = {}
        if get_plants_data_dc:
            for block_data in get_plants_data_dc:
                time_block = block_data.get("time_block")
                if time_block is not None:
                    try:
                        tb_int = int(time_block)
                        plants_map = {p["key"]: p for p in block_data.get("result", []) if "key" in p}
                        dc_data_map[tb_int] = plants_map
                    except (ValueError, TypeError):
                        pass

        for blk, TotalblkwiseData in enumerate(total_availability):
            current_block_records = []
            time_block_num = blk + 1

            # Get RTM forecasted price for this block
            rtmforecastedpriceblk = 0.0
            if time_block_num in rtm_forecasted_price:
                price_item = rtm_forecasted_price[time_block_num]
                price_val = price_item.get("values") or price_item.get("value") or price_item.get("forecasted_price", 0)
                try:
                    rtmforecastedpriceblk = float(price_val or 0)
                except (ValueError, TypeError):
                    rtmforecastedpriceblk = 0.0

            genratedTotalsupply = 0.0
            total_diff = float(TotalblkwiseData.get("surplus_deficit", 0))

            # Check if this block is within running range
            if self.BlkNoForRunSchedule <= blk <= self.BlkNoToRunSchedule:
                # Use updated surplus/deficit from previous block if available
                if blk in reviseDataTotalsupply:
                    total_diff = reviseDataTotalsupply[blk].get("surplus_deficit_updated", total_diff)

                availability_data_array_b_wise = dict(get_plants_data[blk])
                current_block_records = []
                block_sell_total = 0.0
                block_buy_total = 0.0

                # Separate central and non-central plants
                # Scheduling (back-down/ramp-up) is done ONLY for central plants (c_s == "central")
                all_plants = get_plants_data[blk].get("result", [])
                central_plants = []
                non_central_plants = []
                for p in all_plants:
                    plant_key = p.get("key", "")
                    # Check c_s from pipeline data first, then fallback to plant_master
                    c_s_value = p.get("c_s", "")
                    if not c_s_value and plant_key in get_plant_list:
                        c_s_value = get_plant_list[plant_key].get("c_s", "")
                    if str(c_s_value).strip().lower() == "central":
                        central_plants.append(p)
                    else:
                        non_central_plants.append(p)

                if total_diff > 0:
                    # SURPLUS CASE: Back down expensive central plants first
                    sorted_central = sorted(
                        central_plants,
                        key=lambda x: (x.get("px_price", 0) or 0),
                        reverse=True
                    )
                    remaining_surplus = total_diff
                    
                    for availability_plnt_arrayyyObj in sorted_central:
                        availability_plnt_arrayyy = dict(availability_plnt_arrayyyObj)
                        plant_key = availability_plnt_arrayyy.get("key")

                        # Skip PPCLGPP - pass through with no ramp adjustment
                        if plant_key == 'PPCLGPP':
                            _pv = reviseDataArray.get(blk, {}).get("genrated_sg_volume", {}).get(plant_key)
                            if _pv is None:
                                _pv = float(availability_plnt_arrayyy.get("volume", 0))
                            genratedTotalsupply += _pv
                            reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[plant_key] = _pv
                            continue

                        px_price = float(availability_plnt_arrayyy.get("px_price", 0) or 0)

                        # Technical Minimum from supply_serve (rtm_tm)
                        planttmvalue = float(availability_plnt_arrayyy.get("rtm_tm", 0) or 0)

                        plantSEZvalue = all_sez_values.get(f"{availability_plnt_arrayyy['scheduled_at']}_{plant_key}_{time_block_num}", 0)

                        # Previous block volume (or current SG volume if first block)
                        prev_vol = reviseDataArray.get(blk, {}).get("genrated_sg_volume", {}).get(plant_key)
                        if prev_vol is None:
                             prev_vol = float(availability_plnt_arrayyy.get("volume", 0))

                        dc_afterloss = float(availability_plnt_arrayyy.get("dc_afterloss", 0) or 0)

                        # Lower value = Min(rtm_tm, dc_afterloss) — scheduled value cannot go below this
                        lower_value = min(planttmvalue, dc_afterloss) if dc_afterloss > 0 else planttmvalue

                        # Get ramp_down from DC collection data for this plant+block
                        dc_plant_data = dc_data_map.get(time_block_num, {}).get(plant_key, {})
                        ramping_limit = float(dc_plant_data.get("ramp_down", 0) or 0)
                        if ramping_limit == 0:
                            # Fallback: use dc_afterloss * 10%
                            ramping_limit = dc_afterloss * 0.10
                        if ramping_limit == 0:
                             ramping_limit = await self.__get_plant_rmp_value(availability_plnt_arrayyy, get_plant_list)

                        # Compare from previous block: prev_vol - ramping
                        # A plant cannot go below Lower value = Min(rtm_tm, dc_afterloss)
                        min_vol_to_schedule = max(prev_vol - ramping_limit, lower_value)
                        max_back_down_possible = max(prev_vol - min_vol_to_schedule, 0.0)

                        sell_volume = 0.0
                        if remaining_surplus > 0:
                            back_down_amount = min(remaining_surplus, max_back_down_possible)
                            new_schedule = prev_vol - back_down_amount
                            remaining_surplus -= back_down_amount
                            sell_volume = back_down_amount
                        else:
                            new_schedule = prev_vol

                        scheduling_type = "Scheduling" if sell_volume > 0 else "NA"
                        block_sell_total += sell_volume

                        genratedTotalsupply += new_schedule
                        reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[plant_key] = new_schedule

                        base_insert_data = {
                            "scheduled_at": today_dt,
                            "time_block": time_block_num,
                            "dc_sg_type": availability_data_array_b_wise.get("dc_sg_type", "SG"),
                            "session_number": get_session_no.get(time_block_num, {}).get("session_number", 0),
                            "demand_forecast": float(TotalblkwiseData["forecast_value"]),
                            "forecast_source": TotalblkwiseData.get("forecast_source"),
                            "total_supply_availablity": float(TotalblkwiseData["totalAvailability"]),
                            "surplus_deficit": float(TotalblkwiseData["surplus_deficit"]),
                            "price": px_price,
                            "rtm_forecasted_price": float(rtmforecastedpriceblk),
                            "remaining_volume": float(remaining_surplus),
                            "plant_RMP_value": ramping_limit,
                            "plant_TM_value": planttmvalue,
                            "plant_SEZ_value": float(plantSEZvalue),
                            "up_down_RMP_volume": float(sell_volume),
                            "Final_plant_RMP_volume": float(new_schedule),
                            "genrated_sg_volume": float(prev_vol),
                            "actual_plant_volume": float(availability_plnt_arrayyy.get("volume", 0)),
                            "actual_plant_volume_DC": float(dc_afterloss),
                            "plant_min_run_volume": float(min_vol_to_schedule),
                            "category": availability_plnt_arrayyy.get("category", ""),
                            "plant_name": availability_plnt_arrayyy.get("plant_name", ""),
                            "key": plant_key,
                            "display_category": get_plant_list.get(plant_key, {}).get("scheduling_req_category", "NA"),
                            "scheduling_requisition_type": scheduling_type,
                            "area": "PUDUCHERRY",
                        }
                        insertData = await self.__create_standardized_record(base_insert_data, is_after_cutoff)
                        current_block_records.append(insertData)

                    # Update total_diff to remaining surplus after central plant back-down
                    total_diff = remaining_surplus

                else:
                    # DEFICIT CASE: Ramp up cheap central plants first
                    deficit_to_cover = abs(total_diff)
                    sorted_central = sorted(
                        central_plants,
                        key=lambda x: (x.get("px_price", 0) or 0),
                        reverse=False
                    )
                    
                    for availability_plnt_arrayyyObj in sorted_central:
                        availability_plnt_arrayyy = dict(availability_plnt_arrayyyObj)
                        plant_key = availability_plnt_arrayyy.get("key")

                        # Skip PPCLGPP - pass through with no ramp adjustment
                        if plant_key == 'PPCLGPP':
                            _pv = reviseDataArray.get(blk, {}).get("genrated_sg_volume", {}).get(plant_key)
                            if _pv is None:
                                _pv = float(availability_plnt_arrayyy.get("volume", 0))
                            genratedTotalsupply += _pv
                            reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[plant_key] = _pv
                            continue

                        px_price = float(availability_plnt_arrayyy.get("px_price", 0) or 0)

                        planttmvalue = float(availability_plnt_arrayyy.get("rtm_tm", 0) or 0)

                        plantSEZvalue = all_sez_values.get(f"{availability_plnt_arrayyy['scheduled_at']}_{plant_key}_{time_block_num}", 0)
                        prev_vol = reviseDataArray.get(blk, {}).get("genrated_sg_volume", {}).get(plant_key)
                        if prev_vol is None:
                             prev_vol = float(availability_plnt_arrayyy.get("volume", 0))

                        dc_afterloss = float(availability_plnt_arrayyy.get("dc_afterloss", 0) or 0)
                        rtm_max_raw = availability_plnt_arrayyy.get("rtm_max")
                        rtm_max_val = float(rtm_max_raw) if rtm_max_raw is not None else dc_afterloss

                        # Upper value = Min(rtm_max, dc_afterloss) — scheduled value cannot exceed this
                        upper_value = min(rtm_max_val, dc_afterloss) if dc_afterloss > 0 else rtm_max_val

                        # Get ramp_up from DC collection data for this plant+block
                        dc_plant_data = dc_data_map.get(time_block_num, {}).get(plant_key, {})
                        ramping_limit = float(dc_plant_data.get("ramp_up", 0) or 0)
                        if ramping_limit == 0:
                            # Fallback: use dc_afterloss * 10%
                            ramping_limit = dc_afterloss * 0.10
                        if ramping_limit == 0:
                             ramping_limit = await self.__get_plant_rmp_value(availability_plnt_arrayyy, get_plant_list)

                        # Cannot exceed Upper value = Min(rtm_max, dc_afterloss)
                        max_vol_to_schedule = min(prev_vol + ramping_limit, upper_value)
                        max_ramp_up = max(max_vol_to_schedule - prev_vol, 0.0)

                        ramp_up_amount = 0.0
                        if deficit_to_cover > 0:
                            ramp_up_amount = min(deficit_to_cover, max_ramp_up)
                            new_schedule = prev_vol + ramp_up_amount
                            deficit_to_cover -= ramp_up_amount
                        else:
                            new_schedule = prev_vol

                        scheduling_type = "Scheduling" if ramp_up_amount > 0 else "NA"
                        block_buy_total += ramp_up_amount

                        genratedTotalsupply += new_schedule
                        reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[plant_key] = new_schedule

                        base_insert_data = {
                            "scheduled_at": today_dt,
                            "time_block": time_block_num,
                            "dc_sg_type": availability_data_array_b_wise.get("dc_sg_type", "SG"),
                            "session_number": get_session_no.get(time_block_num, {}).get("session_number", 0),
                            "demand_forecast": float(TotalblkwiseData["forecast_value"]),
                            "forecast_source": TotalblkwiseData.get("forecast_source"),
                            "total_supply_availablity": float(TotalblkwiseData["totalAvailability"]),
                            "surplus_deficit": float(TotalblkwiseData["surplus_deficit"]),
                            "price": px_price,
                            "rtm_forecasted_price": float(rtmforecastedpriceblk),
                            "remaining_volume": float(deficit_to_cover * -1),
                            "plant_RMP_value": ramping_limit,
                            "plant_TM_value": planttmvalue,
                            "plant_SEZ_value": float(plantSEZvalue),
                            "up_down_RMP_volume": float(ramp_up_amount),
                            "Final_plant_RMP_volume": float(new_schedule),
                            "genrated_sg_volume": float(prev_vol),
                            "actual_plant_volume": float(availability_plnt_arrayyy.get("volume", 0)),
                            "actual_plant_volume_DC": float(dc_afterloss),
                            "plant_min_run_volume": float(planttmvalue),
                            "category": availability_plnt_arrayyy.get("category", ""),
                            "plant_name": availability_plnt_arrayyy.get("plant_name", ""),
                            "key": plant_key,
                            "display_category": get_plant_list.get(plant_key, {}).get("scheduling_req_category", "NA"),
                            "scheduling_requisition_type": scheduling_type,
                            "area": "PUDUCHERRY",
                        }
                        insertData = await self.__create_standardized_record(base_insert_data, is_after_cutoff)
                        current_block_records.append(insertData)

                # Queue exchange_serve bid update for this block
                block_exchange_updates.append({
                    "scheduled_at": today_dt,
                    "time_block": time_block_num,
                    "market_type": "RTM",
                    "px_name": "IEX",
                    "sell_bid": round(block_sell_total, 3),
                    "buy_bid": round(block_buy_total, 3),
                    "updated_at": datetime.now(),
                })

                # Non-central plants: pass through with original volumes, no scheduling
                for availability_plnt_arrayyyObj in non_central_plants:
                    availability_plnt_arrayyy = dict(availability_plnt_arrayyyObj)
                    plant_key = availability_plnt_arrayyy.get("key")
                    px_price = float(availability_plnt_arrayyy.get("px_price", 0) or 0)
                    vol = float(availability_plnt_arrayyy.get("volume", 0))
                    dc_afterloss = float(availability_plnt_arrayyy.get("dc_afterloss", 0) or 0)
                    planttmvalue = float(availability_plnt_arrayyy.get("rtm_tm", 0) or 0)
                    plantSEZvalue = all_sez_values.get(f"{availability_plnt_arrayyy['scheduled_at']}_{plant_key}_{time_block_num}", 0)
                    
                    genratedTotalsupply += vol
                    reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[plant_key] = vol

                    base_insert_data = {
                        "scheduled_at": today_dt,
                        "time_block": time_block_num,
                        "dc_sg_type": availability_data_array_b_wise.get("dc_sg_type", "SG"),
                        "session_number": get_session_no.get(time_block_num, {}).get("session_number", 0),
                        "demand_forecast": float(TotalblkwiseData["forecast_value"]),
                        "forecast_source": TotalblkwiseData.get("forecast_source"),
                        "total_supply_availablity": float(TotalblkwiseData["totalAvailability"]),
                        "surplus_deficit": float(TotalblkwiseData["surplus_deficit"]),
                        "price": px_price,
                        "rtm_forecasted_price": float(rtmforecastedpriceblk),
                        "remaining_volume": 0.0,
                        "plant_RMP_value": 0.0,
                        "plant_TM_value": planttmvalue,
                        "plant_SEZ_value": float(plantSEZvalue),
                        "up_down_RMP_volume": 0.0,
                        "Final_plant_RMP_volume": float(vol),
                        "genrated_sg_volume": float(vol),
                        "actual_plant_volume": float(vol),
                        "actual_plant_volume_DC": float(dc_afterloss),
                        "plant_min_run_volume": float(planttmvalue),
                        "category": availability_plnt_arrayyy.get("category", ""),
                        "plant_name": availability_plnt_arrayyy.get("plant_name", ""),
                        "key": plant_key,
                        "display_category": get_plant_list.get(plant_key, {}).get("scheduling_req_category", "NA"),
                        "scheduling_requisition_type": "NA",
                        "area": "PUDUCHERRY",
                    }
                    insertData = await self.__create_standardized_record(base_insert_data, is_after_cutoff)
                    current_block_records.append(insertData)

                # Store block results for next block
                reviseDataTotalsupply[blk + 1] = {
                    "total_supply_updated": genratedTotalsupply,
                    "surplus_deficit_updated": genratedTotalsupply - float(TotalblkwiseData["forecast_value"])
                }
                all_insert_data.extend(current_block_records)

            elif self.BlkNoFromRunReq <= blk <= self.BlkNoToRunReq:
                # REQUISITION RANGE: blocks +5 to +18 — future procurement planning
                if blk in reviseDataTotalsupply:
                    total_diff = reviseDataTotalsupply[blk].get("surplus_deficit_updated", total_diff)

                availability_data_array_b_wise = dict(get_plants_data[blk])
                current_block_records = []

                # Separate central and non-central plants
                all_plants = get_plants_data[blk].get("result", [])
                central_plants = []
                non_central_plants = []
                for p in all_plants:
                    plant_key = p.get("key", "")
                    c_s_value = p.get("c_s", "")
                    if not c_s_value and plant_key in get_plant_list:
                        c_s_value = get_plant_list[plant_key].get("c_s", "")
                    if str(c_s_value).strip().lower() == "central":
                        central_plants.append(p)
                    else:
                        non_central_plants.append(p)

                if total_diff > 0:
                    # SURPLUS: ramp down expensive plants first → type "Requisition"
                    sorted_central = sorted(central_plants, key=lambda x: (x.get("px_price", 0) or 0), reverse=True)
                    remaining_surplus = total_diff

                    for availability_plnt_arrayyyObj in sorted_central:
                        availability_plnt_arrayyy = dict(availability_plnt_arrayyyObj)
                        plant_key = availability_plnt_arrayyy.get("key")

                        if plant_key == 'PPCLGPP':
                            _pv = reviseDataArray.get(blk, {}).get("genrated_sg_volume", {}).get(plant_key)
                            if _pv is None:
                                _pv = float(availability_plnt_arrayyy.get("volume", 0))
                            genratedTotalsupply += _pv
                            reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[plant_key] = _pv
                            continue

                        px_price = float(availability_plnt_arrayyy.get("px_price", 0) or 0)
                        planttmvalue = float(availability_plnt_arrayyy.get("rtm_tm", 0) or 0)
                        plantSEZvalue = all_sez_values.get(f"{availability_plnt_arrayyy.get('scheduled_at', '')}_{plant_key}_{time_block_num}", 0)

                        prev_vol = reviseDataArray.get(blk, {}).get("genrated_sg_volume", {}).get(plant_key)
                        if prev_vol is None:
                            prev_vol = float(availability_plnt_arrayyy.get("volume", 0))

                        dc_afterloss = float(availability_plnt_arrayyy.get("dc_afterloss", 0) or 0)

                        # Lower value = Min(rtm_tm, dc_afterloss) — scheduled value cannot go below this
                        lower_value = min(planttmvalue, dc_afterloss) if dc_afterloss > 0 else planttmvalue

                        dc_plant_data = dc_data_map.get(time_block_num, {}).get(plant_key, {})
                        ramping_limit = float(dc_plant_data.get("ramp_down", 0) or 0)
                        if ramping_limit == 0:
                            ramping_limit = dc_afterloss * 0.10
                        if ramping_limit == 0:
                            ramping_limit = await self.__get_plant_rmp_value(availability_plnt_arrayyy, get_plant_list)

                        # A plant cannot go below Lower value = Min(rtm_tm, dc_afterloss)
                        min_vol_to_schedule = max(prev_vol - ramping_limit, lower_value)
                        max_back_down_possible = max(prev_vol - min_vol_to_schedule, 0.0)

                        sell_volume = 0.0
                        if remaining_surplus > 0:
                            back_down_amount = min(remaining_surplus, max_back_down_possible)
                            new_schedule = prev_vol - back_down_amount
                            remaining_surplus -= back_down_amount
                            sell_volume = back_down_amount
                        else:
                            new_schedule = prev_vol

                        scheduling_type = "Requisition" if sell_volume > 0 else "NA"
                        genratedTotalsupply += new_schedule
                        reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[plant_key] = new_schedule

                        base_insert_data = {
                            "scheduled_at": today_dt,
                            "time_block": time_block_num,
                            "dc_sg_type": availability_data_array_b_wise.get("dc_sg_type", "SG"),
                            "session_number": get_session_no.get(time_block_num, {}).get("session_number", 0),
                            "demand_forecast": float(TotalblkwiseData["forecast_value"]),
                            "forecast_source": TotalblkwiseData.get("forecast_source"),
                            "total_supply_availablity": float(TotalblkwiseData["totalAvailability"]),
                            "surplus_deficit": float(TotalblkwiseData["surplus_deficit"]),
                            "price": px_price,
                            "rtm_forecasted_price": float(rtmforecastedpriceblk),
                            "remaining_volume": float(remaining_surplus),
                            "plant_RMP_value": ramping_limit,
                            "plant_TM_value": planttmvalue,
                            "plant_SEZ_value": float(plantSEZvalue),
                            "up_down_RMP_volume": float(sell_volume),
                            "Final_plant_RMP_volume": float(new_schedule),
                            "genrated_sg_volume": float(prev_vol),
                            "actual_plant_volume": float(availability_plnt_arrayyy.get("volume", 0)),
                            "actual_plant_volume_DC": float(dc_afterloss),
                            "plant_min_run_volume": float(min_vol_to_schedule),
                            "category": availability_plnt_arrayyy.get("category", ""),
                            "plant_name": availability_plnt_arrayyy.get("plant_name", ""),
                            "key": plant_key,
                            "display_category": get_plant_list.get(plant_key, {}).get("scheduling_req_category", "NA"),
                            "scheduling_requisition_type": scheduling_type,
                            "area": "PUDUCHERRY",
                        }
                        insertData = await self.__create_standardized_record(base_insert_data, is_after_cutoff)
                        current_block_records.append(insertData)

                    total_diff = remaining_surplus

                else:
                    # DEFICIT: ramp up cheapest plants first → type "Requisition"
                    deficit_to_cover = abs(total_diff)
                    sorted_central = sorted(central_plants, key=lambda x: (x.get("px_price", 0) or 0), reverse=False)

                    for availability_plnt_arrayyyObj in sorted_central:
                        availability_plnt_arrayyy = dict(availability_plnt_arrayyyObj)
                        plant_key = availability_plnt_arrayyy.get("key")

                        if plant_key == 'PPCLGPP':
                            _pv = reviseDataArray.get(blk, {}).get("genrated_sg_volume", {}).get(plant_key)
                            if _pv is None:
                                _pv = float(availability_plnt_arrayyy.get("volume", 0))
                            genratedTotalsupply += _pv
                            reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[plant_key] = _pv
                            continue

                        px_price = float(availability_plnt_arrayyy.get("px_price", 0) or 0)
                        planttmvalue = float(availability_plnt_arrayyy.get("rtm_tm", 0) or 0)
                        plantSEZvalue = all_sez_values.get(f"{availability_plnt_arrayyy.get('scheduled_at', '')}_{plant_key}_{time_block_num}", 0)

                        prev_vol = reviseDataArray.get(blk, {}).get("genrated_sg_volume", {}).get(plant_key)
                        if prev_vol is None:
                            prev_vol = float(availability_plnt_arrayyy.get("volume", 0))

                        dc_afterloss = float(availability_plnt_arrayyy.get("dc_afterloss", 0) or 0)
                        rtm_max_raw = availability_plnt_arrayyy.get("rtm_max")
                        rtm_max_val = float(rtm_max_raw) if rtm_max_raw is not None else dc_afterloss

                        # Upper value = Min(rtm_max, dc_afterloss) — scheduled value cannot exceed this
                        upper_value = min(rtm_max_val, dc_afterloss) if dc_afterloss > 0 else rtm_max_val

                        dc_plant_data = dc_data_map.get(time_block_num, {}).get(plant_key, {})
                        ramping_limit = float(dc_plant_data.get("ramp_up", 0) or 0)
                        if ramping_limit == 0:
                            ramping_limit = dc_afterloss * 0.10
                        if ramping_limit == 0:
                            ramping_limit = await self.__get_plant_rmp_value(availability_plnt_arrayyy, get_plant_list)

                        # Cannot exceed Upper value = Min(rtm_max, dc_afterloss)
                        max_vol_to_schedule = min(prev_vol + ramping_limit, upper_value)
                        max_ramp_up = max(max_vol_to_schedule - prev_vol, 0.0)

                        ramp_up_amount = 0.0
                        if deficit_to_cover > 0:
                            ramp_up_amount = min(deficit_to_cover, max_ramp_up)
                            new_schedule = prev_vol + ramp_up_amount
                            deficit_to_cover -= ramp_up_amount
                        else:
                            new_schedule = prev_vol

                        scheduling_type = "Requisition" if ramp_up_amount > 0 else "NA"
                        genratedTotalsupply += new_schedule
                        reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[plant_key] = new_schedule

                        base_insert_data = {
                            "scheduled_at": today_dt,
                            "time_block": time_block_num,
                            "dc_sg_type": availability_data_array_b_wise.get("dc_sg_type", "SG"),
                            "session_number": get_session_no.get(time_block_num, {}).get("session_number", 0),
                            "demand_forecast": float(TotalblkwiseData["forecast_value"]),
                            "forecast_source": TotalblkwiseData.get("forecast_source"),
                            "total_supply_availablity": float(TotalblkwiseData["totalAvailability"]),
                            "surplus_deficit": float(TotalblkwiseData["surplus_deficit"]),
                            "price": px_price,
                            "rtm_forecasted_price": float(rtmforecastedpriceblk),
                            "remaining_volume": float(deficit_to_cover * -1),
                            "plant_RMP_value": ramping_limit,
                            "plant_TM_value": planttmvalue,
                            "plant_SEZ_value": float(plantSEZvalue),
                            "up_down_RMP_volume": float(ramp_up_amount),
                            "Final_plant_RMP_volume": float(new_schedule),
                            "genrated_sg_volume": float(prev_vol),
                            "actual_plant_volume": float(availability_plnt_arrayyy.get("volume", 0)),
                            "actual_plant_volume_DC": float(dc_afterloss),
                            "plant_min_run_volume": float(planttmvalue),
                            "category": availability_plnt_arrayyy.get("category", ""),
                            "plant_name": availability_plnt_arrayyy.get("plant_name", ""),
                            "key": plant_key,
                            "display_category": get_plant_list.get(plant_key, {}).get("scheduling_req_category", "NA"),
                            "scheduling_requisition_type": scheduling_type,
                            "area": "PUDUCHERRY",
                        }
                        insertData = await self.__create_standardized_record(base_insert_data, is_after_cutoff)
                        current_block_records.append(insertData)

                # Non-central plants: pass through with original volumes
                for availability_plnt_arrayyyObj in non_central_plants:
                    availability_plnt_arrayyy = dict(availability_plnt_arrayyyObj)
                    plant_key = availability_plnt_arrayyy.get("key")
                    px_price = float(availability_plnt_arrayyy.get("px_price", 0) or 0)
                    vol = float(availability_plnt_arrayyy.get("volume", 0))
                    dc_afterloss = float(availability_plnt_arrayyy.get("dc_afterloss", 0) or 0)
                    planttmvalue = float(availability_plnt_arrayyy.get("rtm_tm", 0) or 0)
                    plantSEZvalue = all_sez_values.get(f"{availability_plnt_arrayyy.get('scheduled_at', '')}_{plant_key}_{time_block_num}", 0)

                    genratedTotalsupply += vol
                    reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[plant_key] = vol

                    base_insert_data = {
                        "scheduled_at": today_dt,
                        "time_block": time_block_num,
                        "dc_sg_type": availability_data_array_b_wise.get("dc_sg_type", "SG"),
                        "session_number": get_session_no.get(time_block_num, {}).get("session_number", 0),
                        "demand_forecast": float(TotalblkwiseData["forecast_value"]),
                        "forecast_source": TotalblkwiseData.get("forecast_source"),
                        "total_supply_availablity": float(TotalblkwiseData["totalAvailability"]),
                        "surplus_deficit": float(TotalblkwiseData["surplus_deficit"]),
                        "price": px_price,
                        "rtm_forecasted_price": float(rtmforecastedpriceblk),
                        "remaining_volume": 0.0,
                        "plant_RMP_value": 0.0,
                        "plant_TM_value": planttmvalue,
                        "plant_SEZ_value": float(plantSEZvalue),
                        "up_down_RMP_volume": 0.0,
                        "Final_plant_RMP_volume": float(vol),
                        "genrated_sg_volume": float(vol),
                        "actual_plant_volume": float(vol),
                        "actual_plant_volume_DC": float(dc_afterloss),
                        "plant_min_run_volume": float(planttmvalue),
                        "category": availability_plnt_arrayyy.get("category", ""),
                        "plant_name": availability_plnt_arrayyy.get("plant_name", ""),
                        "key": plant_key,
                        "display_category": get_plant_list.get(plant_key, {}).get("scheduling_req_category", "NA"),
                        "scheduling_requisition_type": "NA",
                        "area": "PUDUCHERRY",
                    }
                    insertData = await self.__create_standardized_record(base_insert_data, is_after_cutoff)
                    current_block_records.append(insertData)

                reviseDataTotalsupply[blk + 1] = {
                    "total_supply_updated": genratedTotalsupply,
                    "surplus_deficit_updated": genratedTotalsupply - float(TotalblkwiseData["forecast_value"])
                }
                all_insert_data.extend(current_block_records)

            else:
                # OUTSIDE RUN RANGE: Pass through original volumes and insert static (pass-through) records
                # This ensures all 96 blocks have records with supply/demand data in the DB
                availability_data_array_b_wise = dict(get_plants_data[blk])
                for plnamekey, availability_plnt_arrayyyObj in enumerate(availability_data_array_b_wise.get("result", [])):
                    availability_plnt_arrayyy = dict(availability_plnt_arrayyyObj)
                    plant_key = availability_plnt_arrayyy.get("key")
                    vol = float(availability_plnt_arrayyy.get("volume", 0))
                    px_price = float(availability_plnt_arrayyy.get("px_price", 0) or 0)
                    dc_afterloss = float(availability_plnt_arrayyy.get("dc_afterloss", 0) or 0)
                    planttmvalue = float(availability_plnt_arrayyy.get("rtm_tm", 0) or 0)
                    plantSEZvalue = all_sez_values.get(f"{availability_plnt_arrayyy.get('scheduled_at', '')}_{plant_key}_{time_block_num}", 0)

                    genratedTotalsupply += vol
                    reviseDataArray.setdefault(blk + 1, {}).setdefault("genrated_sg_volume", {})[plant_key] = vol

                    # Insert static pass-through record so all 96 blocks have data
                    base_insert_data = {
                        "scheduled_at": today_dt,
                        "time_block": time_block_num,
                        "dc_sg_type": availability_data_array_b_wise.get("dc_sg_type", "SG"),
                        "session_number": get_session_no.get(time_block_num, {}).get("session_number", 0),
                        "demand_forecast": float(TotalblkwiseData.get("forecast_value", 0)),
                        "forecast_source": TotalblkwiseData.get("forecast_source"),
                        "total_supply_availablity": float(TotalblkwiseData.get("totalAvailability", 0)),
                        "surplus_deficit": float(TotalblkwiseData.get("surplus_deficit", 0)),
                        "price": px_price,
                        "rtm_forecasted_price": float(rtmforecastedpriceblk),
                        "remaining_volume": 0.0,
                        "plant_RMP_value": 0.0,
                        "plant_TM_value": planttmvalue,
                        "plant_SEZ_value": float(plantSEZvalue),
                        "up_down_RMP_volume": 0.0,
                        "Final_plant_RMP_volume": float(vol),
                        "genrated_sg_volume": float(vol),
                        "actual_plant_volume": float(vol),
                        "actual_plant_volume_DC": float(dc_afterloss),
                        "plant_min_run_volume": float(planttmvalue),
                        "category": availability_plnt_arrayyy.get("category", ""),
                        "plant_name": availability_plnt_arrayyy.get("plant_name", ""),
                        "key": plant_key,
                        "display_category": get_plant_list.get(plant_key, {}).get("scheduling_req_category", "NA"),
                        "scheduling_requisition_type": "NA",
                        "area": "PUDUCHERRY",
                    }
                    insertData = await self.__create_standardized_record(base_insert_data, is_after_cutoff)
                    current_block_records.append(insertData)

                reviseDataTotalsupply[blk + 1] = {
                    "total_supply_updated": genratedTotalsupply,
                    "surplus_deficit_updated": genratedTotalsupply - float(TotalblkwiseData.get("forecast_value", 0))
                }
                all_insert_data.extend(current_block_records)

        # Bulk upsert computed sell/buy bids to exchange_serve (only when run_with_exchange=True)
        if run_with_exchange and block_exchange_updates:
            try:
                await self.serve_exchange_repo.bulk_upsert_exchange_records(block_exchange_updates)
                logger.info(f"[RTMSchedulingServices] Upserted {len(block_exchange_updates)} exchange bid records to exchange_serve")
            except Exception as e:
                logger.error(f"[RTMSchedulingServices] Failed to upsert exchange bids to exchange_serve: {str(e)}")

        # Bulk insert all records accumulated across all blocks
        if all_insert_data:
            try:
                collection = self.scheduling_repo.db[self.scheduling_repo.collection_name]
                await collection.insert_many(all_insert_data)
                logger.info(f"[RTMSchedulingServices] Bulk inserted {len(all_insert_data)} scheduling records")
                if snapshot_run_id:
                    try:
                        self._pending_optimized_snapshot_task = asyncio.create_task(
                            self._store_scheduling_optimized_snapshots(
                                all_insert_data,
                                snapshot_run_id,
                                snapshot_execution_id or "",
                                snapshot_schedule_date or "",
                                snapshot_session_id,
                            )
                        )
                    except Exception as snap_err:
                        logger.error(
                            "[RTMSchedulingServices] Optimized snapshot insert failed (working rows saved): %s",
                            snap_err,
                            exc_info=True,
                        )
            except Exception as e:
                if "MongoClient after close" in str(e):
                    logger.warning("[RTMSchedulingServices] MongoClient was closed, refreshing connection and retrying bulk insert...")
                    # Reset cached connection so lazy property re-creates it
                    self.scheduling_repo._client = None
                    self.scheduling_repo._db = None
                    try:
                        collection = self.scheduling_repo.db[self.scheduling_repo.collection_name]
                        await collection.insert_many(all_insert_data)
                        logger.info(f"[RTMSchedulingServices] Bulk inserted {len(all_insert_data)} scheduling records (after reconnect)")
                        if snapshot_run_id:
                            try:
                                self._pending_optimized_snapshot_task = asyncio.create_task(
                                    self._store_scheduling_optimized_snapshots(
                                        all_insert_data,
                                        snapshot_run_id,
                                        snapshot_execution_id or "",
                                        snapshot_schedule_date or "",
                                        snapshot_session_id,
                                    )
                                )
                            except Exception as snap_err:
                                logger.error(
                                    "[RTMSchedulingServices] Optimized snapshot insert failed after reconnect (working rows saved): %s",
                                    snap_err,
                                    exc_info=True,
                                )
                    except Exception as retry_err:
                        logger.error(f"Error in bulk insert after reconnect: {str(retry_err)}")
                        raise
                else:
                    logger.error(f"Error in bulk insert: {str(e)}")
                    raise

        return reviseDataTotalsupply

    async def _limit_values_lookups(self):
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
    
    async def getAllPlantsDataForScheduling(self, schedule_date, dc_sg):
        """
        Wrapper function to get all plant data for scheduling.
        Calls the internal method with proper parameters.
        
        Args:
            schedule_date (str): Schedule date parameter
            dc_sg (str): "DC" or "SG" to filter plant data
            
        Returns:
            list: List of plant data grouped by time block
        """
        # Use today's date — supply_serve stores data with today's date
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        schedule_date_used = today_start.strftime("%Y-%m-%d")
        logger.info(f"[RTMSchedulingServices] get_all_plant_data_for_scheduling: Using date {schedule_date_used} (today) for dc_sg={dc_sg}")

        # Handle "DC_SG" as "DC or SG" - the database stores them separately
        if dc_sg == "DC_SG":
            metch = {
                "dc_sg": {"$in": ["DC", "SG"]},
                "scheduled_at": {"$gte": today_start, "$lt": today_end}
            }
        else:
            metch = {
                "scheduled_at": {"$gte": today_start, "$lt": today_end},
                "dc_sg": dc_sg
            }
        
        logger.debug(f"[RTMSchedulingServices] MongoDB match query: {metch}")

        pipeline = [
            {
                "$match": metch
            },
            {
                "$addFields": {
                    "effective_volume": {
                        "$cond": {
                            "if": {"$eq": ["$dc_sg", "DC"]},
                            "then": {"$ifNull": ["$eightThirtyValue", "$volume"]},
                            "else": "$volume"
                        }
                    }
                }
            }, 
            # Lookup plant_master for plant_name and category
            {
                "$lookup": {
                    "from": "plant_master",
                    "localField": "plant_id",
                    "foreignField": "plant_id",
                    "as": "plant_details"
                }
            },
            {"$unwind": {"path": "$plant_details", "preserveNullAndEmptyArrays": True}},
            # Deduplicate plants within block (keep latest revision, prioritize SG over DC)
            {
                "$sort": {"revision_no": -1, "dc_sg": -1}
            },
            {
                "$group": {
                    "_id": {
                        "time_block": "$time_block",
                        "plant_id": "$plant_id"
                    },
                    "plant_details": {"$first": "$plant_details"},
                    "volume": {"$first": "$volume"},
                    "effective_volume": {"$first": "$effective_volume"},
                    "dc_sg": {"$first": "$dc_sg"},
                    "scheduled_at": {"$first": "$scheduled_at"},
                    "time_block": {"$first": "$time_block"},
                    "plant_id": {"$first": "$plant_id"},
                    "revision_no": {"$first": "$revision_no"},
                    "dispatch_type": {"$first": "$dispatch_type"}
                }
            },
            {
                "$group": {
                    "_id": {
                        "scheduled_at": "$scheduled_at",
                        "time_block": "$time_block"
                    },
                    "result": {
                        "$push": {
                            "plant_name": "$plant_details.plant_name",
                            "key": "$plant_id",
                            "volume": "$effective_volume",
                            "original_volume": "$volume",
                            "px_price": "$plant_details.px_price",
                            "scheduled_at": "$scheduled_at",
                            "category": "$plant_details.category",
                            "c_s": "$plant_details.c_s",
                            "scheduling_req_category": "$plant_details.scheduling_req_category",
                            "revision_no": "$revision_no",
                            "dispatch_type": "$dispatch_type",
                            "rtm_tm": "$rtm_tm",
                            "dc_afterloss": "$dc_afterloss",
                            "ramp_up": "$ramp_up",
                            "ramp_down": "$ramp_down"
                        }
                    },
                    "dc_sg":{ "$first": "$dc_sg" },
                    "totalAvailability": {
                        "$sum": "$effective_volume"
                    },
                    "time_block": {
                        "$first": "$time_block"
                    },
                    "scheduled_at": {
                        "$first": "$scheduled_at"
                    }
                }
            },
            {
                "$project": {
                    "result": 1,
                    "time_block": 1,
                    "scheduled_at": 1,
                    "totalAvailability": 1,
                    "dc_sg": 1
                }
            },
            {
                "$sort": {
                    "px_price": -1, 
                    "key": -1,
                    "scheduled_at": 1, 
                    "time_block": 1
                }
            }
        ]

        # Add timeout to prevent hanging (180 seconds = 3 minutes)
        try:
            posted_jobs = await self.supply_repo.aggregate(pipeline)
        except Exception as e:
            if "MaxTimeMSExpired" in str(e) or "time limit" in str(e).lower():
                logger.error(f"[RTMSchedulingServices] get_all_plant_data_for_scheduling: Aggregation timed out for date {schedule_date_used}, dc_sg={dc_sg}. "
                            f"This may indicate missing indexes or a very large dataset.")
                posted_jobs = []
            else:
                logger.exception(f"[RTMSchedulingServices] get_all_plant_data_for_scheduling: Error during aggregation: {str(e)}")
                raise
        
        logger.info(f"[RTMSchedulingServices] get_all_plant_data_for_scheduling: Aggregation returned {len(posted_jobs)} documents for date {schedule_date_used}, dc_sg={dc_sg}")
        if len(posted_jobs) == 0:
            logger.warning(f"[RTMSchedulingServices] No data found in supply_serve for date {schedule_date_used} with dc_sg={dc_sg}. This might indicate:")
            logger.warning("  1. No data exists for this date")
            logger.warning("  2. Date format mismatch (check if scheduled_at uses different format)")
            logger.warning("  3. dc_sg filter is too restrictive")
            # Construct dc_sg_value outside f-string to avoid backslash issue
            dc_sg_value = dc_sg if dc_sg != 'DC_SG' else '{"$in": ["DC", "SG"]}'
            logger.warning(
                f"  4. Check if data exists with query: {{'scheduled_at': "
                f"{{'$gte': {today_start}, '$lt': {today_end}}}, 'dc_sg': {dc_sg_value}}}"
            )
        
        # Add missing fields to match original structure (start_time, end_time, full_time, dc_sg_type)
        
        for block_data in posted_jobs:
            time_block = block_data.get('time_block')
            if time_block is not None:
                try:
                    time_block_int = int(time_block)
                    time_info = get_time_block_start_end(time_block_int)
                    # Handle both dict and sequence (tuple/list) return types from helper
                    if isinstance(time_info, dict):
                        block_data['start_time'] = time_info.get('start_time', '')
                        block_data['end_time'] = time_info.get('end_time', '')
                        block_data['full_time'] = time_info.get('full_time', '')
                    elif isinstance(time_info, (tuple, list)) and len(time_info) >= 3:
                        block_data['start_time'] = time_info[0]
                        block_data['end_time'] = time_info[1]
                        block_data['full_time'] = time_info[2]
                    else:
                        block_data['start_time'] = ''
                        block_data['end_time'] = ''
                        block_data['full_time'] = ''
                except (ValueError, TypeError):
                    block_data['start_time'] = ''
                    block_data['end_time'] = ''
                    block_data['full_time'] = ''
            else:
                block_data['start_time'] = ''
                block_data['end_time'] = ''
                block_data['full_time'] = ''
            
            # Map dc_sg to dc_sg_type to match original
            if 'dc_sg' in block_data:
                block_data['dc_sg_type'] = block_data['dc_sg']
        
        return posted_jobs
    
    async def total_availability(self, getplantsData, getForecastedValue):
        """
        Calculates total availability and surplus/deficit for each block
        
        Args:
            getplantsData: Plant data dictionary keyed by block (or list that will be converted)
            getForecastedValue: Forecast data list
        
        Returns:
            List of block-wise availability data
        """
        try:
            finalarray = []
            
            # Convert list to dict if needed (to match original structure)
            if isinstance(getplantsData, list):
                # Convert list to dict keyed by block number
                getplantsDataDict = {}
                for block_data in getplantsData:
                    time_block = block_data.get('time_block')
                    if time_block is not None:
                        try:
                            blk_key = int(time_block)
                            getplantsDataDict[blk_key] = block_data
                        except (ValueError, TypeError):
                            continue
                getplantsData = getplantsDataDict
            
            # Convert forecast list to dict keyed by time_block for safe lookup
            forecast_map = {}
            if getForecastedValue:
                # Handle if it's a cursor (though we fixed repo to return list) or list
                iterator = getForecastedValue
                if not isinstance(getForecastedValue, list) and not isinstance(getForecastedValue, dict):
                    # It might be a cursor or iterable
                    iterator = list(getForecastedValue)
                
                if isinstance(iterator, list):
                    for item in iterator:
                        tb = item.get('time_block')
                        if tb is not None:
                            try:
                                forecast_map[int(tb)] = item
                            except (ValueError, TypeError):
                                pass
            
            # Process each block
            for i, getplantsDataaa in enumerate(getplantsData.values()):
                getplantsDataaa = dict(getplantsDataaa) if not isinstance(getplantsDataaa, dict) else getplantsDataaa
                
                # Get time block to look up forecast
                time_block = getplantsDataaa.get('time_block')
                forecast_item = {}
                if time_block is not None:
                    try:
                        forecast_item = forecast_map.get(int(time_block), {})
                    except (ValueError, TypeError):
                        pass
                
                # Get forecast value
                forecast_value = forecast_item.get('forecast', 0) + self.customdemand
                forecast_source = map_forecast_source_display(
                    forecast_item.get("source_forecast")
                )

                # Get total availability
                bltotalvolume = getplantsDataaa.get('totalAvailability', 0)
                
                # Build data array
                data_ar = {
                    'full_time': getplantsDataaa.get('full_time', ''),
                    'BlockNo': getplantsDataaa.get('time_block', 0),
                    'dc_sg_type': getplantsDataaa.get('dc_sg_type', ''),
                    'forecast_value': forecast_value,
                    'forecast_source': forecast_source,
                    'surplus_deficit': bltotalvolume - forecast_value,
                    'totalAvailability': bltotalvolume
                }
                
                finalarray.append(data_ar)
            
            return finalarray
            
        except Exception as e:
            logger.error(f"Error in total_availability: {str(e)}")
            return []
      
    async def check_forecast_price_apply_for_surplus(self, rtmforecastedpriceblk: float, 
                                               px_price: float, s_d_type: str) -> bool:
        """
        Checks if forecast price condition is met for surplus/deficit
        
        Args:
            rtmforecastedpriceblk: RTM forecasted price for block
            px_price: Plant price
            s_d_type: "surplus" or "deficit"
        
        Returns:
            bool: True if condition is met, False otherwise
        """
        try:
            from ..helpers.rtm_scheduling_helpers import get_parameter_value
            
            # Get parameter value
            # Note: Need to pass a repo that has get_parameter_value method
            # For now, using a simplified version
            # The original has this commented out, so we'll use the direct comparison
            forecast_price_checkbox = get_parameter_value(
                "Scheduling_ForecastPrice_checkbox", 
                self.scheduling_repo  # Pass repo for parameter lookup
            )
            
            # Convert to boolean if needed
            if isinstance(forecast_price_checkbox, str):
                forecast_price_checkbox = forecast_price_checkbox.lower() in ('true', '1', 'yes')
            elif isinstance(forecast_price_checkbox, (int, float)):
                forecast_price_checkbox = bool(forecast_price_checkbox)
            else:
                forecast_price_checkbox = False
            
            if s_d_type == "surplus":
                if forecast_price_checkbox:
                    # For surplus: RTM price < plant price
                    return rtmforecastedpriceblk < px_price
                else:
                    return True
            else:  # deficit
                if forecast_price_checkbox:
                    # For deficit: RTM price > plant price
                    return rtmforecastedpriceblk > px_price
                else:
                    return True
                    
        except Exception as e:
            logger.error(f"Error in check_forecast_price_apply_for_surplus: {str(e)}")
            # Fallback to direct comparison if parameter check fails
            if s_d_type == "surplus":
                return rtmforecastedpriceblk < px_price
            else:
                return rtmforecastedpriceblk > px_price
      
    @staticmethod
    async def get_session_from():
        time_block = [
            "00:00", "00:30", "01:00", "01:30", "02:00", "02:30", "03:00", "03:30",
            "04:00", "04:30", "05:00", "05:30", "06:00", "06:30", "07:00", "07:30",
            "08:00", "08:30", "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
            "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00", "15:30",
            "16:00", "16:30", "17:00", "17:30", "18:00", "18:30", "19:00", "19:30",
            "20:00", "20:30", "21:00", "21:30", "22:00", "22:30", "23:00", "23:30"
        ]
        return time_block

    async def get_revision_no(self, schedule_date: str) -> Dict[str, Any]:
        """
        Gets latest revision numbers for ISP and SLDC categories
        
        Args:
            schedule_date: Schedule date in YYYY-MM-DD format
        
        Returns:
            Dict with revision numbers: srldc_revision_DC, srldc_revision_SG, sldc_revision_DC, sldc_revision_SG
        """
        try:
            from ..helpers.rtm_scheduling_helpers import format_date_for_display
            
            # Query SR-LDC revision (ISP category) from scheduling_requisition_calculation_table
            collection = self.scheduling_repo.db[self.scheduling_repo.collection_name]
            
            getsrldcrevision = await collection.find_one(
                {
                    "schedule_date": schedule_date,
                    "category": "ISP"
                },
                sort=[("revision_no_SG", -1)]
            )
            if getsrldcrevision:
                getsrldcrevision = {k: v for k, v in getsrldcrevision.items() if k in ["revision_no_DC", "revision_no_SG"]}
            
            # Query SLDC revision from supply_serve using dc_sg field
            # Convert schedule_date to datetime for scheduled_at field
            schedule_date_dt = datetime.strptime(schedule_date, "%Y-%m-%d")
            schedule_date_start = datetime.combine(schedule_date_dt.date(), datetime.min.time())
            schedule_date_end = schedule_date_start + timedelta(days=1)
            
            # Get max revision number for DC from supply_serve (prioritize source_revision_number, then system_revision_number)
            supply_collection = self.supply_repo.db[self.supply_repo.collection_name]
            
            # For DC: Sort by source_revision_number first, then system_revision_number
            sldc_revision_dc_doc = await supply_collection.find_one(
                {
                    "scheduled_at": {
                        "$gte": schedule_date_start,
                        "$lt": schedule_date_end
                    },
                    "dc_sg": "DC"
                },
                sort=[("source_revision_number", -1), ("system_revision_number", -1)]
            )

            # For SG: Sort by source_revision_number first, then system_revision_number
            sldc_revision_sg_doc = await supply_collection.find_one(
                {
                    "scheduled_at": {
                        "$gte": schedule_date_start,
                        "$lt": schedule_date_end
                    },
                    "dc_sg": "SG"
                },
                sort=[("source_revision_number", -1), ("system_revision_number", -1)]
            )
            
            # Extract revision numbers (first priority: source_revision_number, secondary: system_revision_number)
            sldc_revision_DC = 0
            if sldc_revision_dc_doc:
                source_rev = sldc_revision_dc_doc.get('source_revision_number')
                system_rev = sldc_revision_dc_doc.get('system_revision_number')
                # Use source_revision_number first if available and not None/0, otherwise use system_revision_number
                sldc_revision_DC = source_rev if source_rev is not None and source_rev != 0 else (system_rev if system_rev is not None else 0)
            
            sldc_revision_SG = 0
            if sldc_revision_sg_doc:
                source_rev = sldc_revision_sg_doc.get('source_revision_number')
                system_rev = sldc_revision_sg_doc.get('system_revision_number')
                # Use source_revision_number first if available and not None/0, otherwise use system_revision_number
                sldc_revision_SG = source_rev if source_rev is not None and source_rev != 0 else (system_rev if system_rev is not None else 0)
            
            # Build response with only the 4 required fields
            getrevision = {
                'srldc_revision_DC': getsrldcrevision.get('revision_no_DC', 0) if getsrldcrevision else 0,
                'srldc_revision_SG': getsrldcrevision.get('revision_no_SG', 0) if getsrldcrevision else 0,
                'sldc_revision_DC': sldc_revision_DC,
                'sldc_revision_SG': sldc_revision_SG
            }
            
            logger.info(f"[RTMSchedulingServices] get_revision_no: schedule_date={schedule_date}, "
                       f"srldc_revision_DC={getrevision['srldc_revision_DC']}, "
                       f"srldc_revision_SG={getrevision['srldc_revision_SG']}, "
                       f"sldc_revision_DC={getrevision['sldc_revision_DC']}, "
                       f"sldc_revision_SG={getrevision['sldc_revision_SG']}")
            
            return getrevision
            
        except Exception as e:
            logger.error(f"Error in get_revision_no: {str(e)}")
            logger.exception(f"Exception details: {type(e).__name__}: {str(e)}")
            return {
                'srldc_revision_DC': 0,
                'srldc_revision_SG': 0,
                'sldc_revision_DC': 0,
                'sldc_revision_SG': 0
            }

    async def get_scheduling_graph_data(self, schedule_date: str, from_block: Optional[int] = None,
                                        to_block: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Provides aggregated data for graph visualization
        
        UPDATED: Now uses supply_serve + demand_serve instead of availability_PG
        
        Args:
            schedule_date: Schedule date in YYYY-MM-DD format
            from_block: Optional starting time block (ignored to match original - original doesn't have this)
            to_block: Optional ending time block (ignored to match original - original doesn't have this)
        
        Returns:
            List of time block data for graphs
        """
        try:
            from ..helpers.rtm_scheduling_helpers import get_time_new, sum_of_data_filter
            
            # Format schedule date
            schedule_date = datetime.strptime(schedule_date, "%Y-%m-%d").strftime("%Y-%m-%d")
            
            # Calculate time block range
            from_time_block = self.currentBlk - 95
            to_time_block = self.BlkNoToRunReq - 96
            
            # Get filterData from supply_serve (replaces availability_PG.filterData)
            # Filter by category
            filter_data_list = await self.supply_repo.get_filter_data_by_category(
                schedule_date=schedule_date,
                filter_type="category",
                dc_sg_type=None,  # Get all DC_SG types
                from_block=from_time_block,
                to_block=to_time_block
            )
            
            # Convert filter_data_list to dict keyed by timeBlock for easier lookup
            filter_data_dict = {}
            for item in filter_data_list:
                time_block = item.get('timeBlock')
                if time_block is not None:
                    filter_data_dict[int(time_block)] = item
            
            # Get demand forecast data from demand_serve (replaces availability_PG.Demand_actual_vs_forecast)
            demand_data = await self.serve_demand_repo.get_demand(schedule_date)
            
            # Convert demand_data to dict keyed by time_block for easier lookup
            demand_dict = {}
            for item in demand_data:
                time_block = item.get('time_block')
                if time_block is not None:
                    demand_dict[int(time_block)] = item
            
            finalarray = []
            time_blocks = get_time_new()
            
            # Process each time block in the range
            for time_block_num in range(from_time_block, to_time_block + 1):
                # Get filterData for this time block
                filter_data_item = filter_data_dict.get(time_block_num)
                if not filter_data_item:
                    # Skip if no data for this time block
                    continue
                
                filter_data = filter_data_item.get('filterData', [])
                
                # Extract totalvolcat values from filterData objects
                # filterData structure: [{"category": "ISP", "totalvolcat": 1000.0}, ...]
                # We need to extract the numeric values (totalvolcat) for summing
                filter_data_values = []
                if isinstance(filter_data, list):
                    for item in filter_data:
                        if isinstance(item, dict):
                            # Extract totalvolcat value from each object
                            totalvolcat = item.get('totalvolcat', 0)
                            if totalvolcat is not None:
                                try:
                                    filter_data_values.append(float(totalvolcat))
                                except (ValueError, TypeError):
                                    continue
                        elif isinstance(item, (int, float)):
                            # If it's already a number, use it directly
                            filter_data_values.append(float(item))
                elif isinstance(filter_data, (int, float)):
                    # If filterData is a single number
                    filter_data_values.append(float(filter_data))
                
                # Calculate total availability
                total_availability = sum_of_data_filter(filter_data_values)
                
                # Get demand forecast for this time block
                demand_item = demand_dict.get(time_block_num, {})
                demand_actual_vs_forecast = demand_item.get('Demand_actual_vs_forecast', 0)
                
                # Calculate demand forecast
                demand_forecast = demand_actual_vs_forecast + self.customdemand
                
                # Get time strings
                array_key = time_block_num
                start_time = time_blocks[array_key - 1] if array_key > 0 else "00:00"
                end_time = time_blocks[array_key] if array_key < len(time_blocks) else "24:00"
                full_time = f"{start_time}-{end_time}"
                
                # Build response
                finalarray.append({
                    "timeBlock": time_block_num,
                    "start_time": start_time,
                    "end_time": end_time,
                    "full_time": full_time,
                    "total_avialability": total_availability,
                    "demand_forecast": demand_forecast,
                    "surplus_deficit":total_availability - demand_forecast,
                    "block_bid_submitted_volume": 0,
                    "block_bid_approved_volume": 0,
                    "block_bid_cleared_volume": 0,
                    "single_bid_submitted_volume": 0,
                    "single_bid_approved_volume": 0,
                    "single_bid_cleared_volume": 0
                })
            
            logger.info(f"Generated scheduling graph data for {len(finalarray)} time blocks (schedule_date={schedule_date})")
            return finalarray
            
        except Exception as e:
            logger.error(f"Error in get_scheduling_graph_data: {str(e)}")
            return []
        
    
# # Global Instances
rtm_scheduling_processor = RTMSchedulingProcessor()
