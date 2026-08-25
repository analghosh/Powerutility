"""
Renewable energy data processor.

Two-step processing pipeline:
  Step 1: raw → dc_sg_renewable  (upsert by scheduled_at, plant_id, time_block, type)
  Step 2: dc_sg_renewable → renewable_serve  (upsert by same unique key)

Day-change carry-forward:
  At midnight, for each unique (time_block, plant_id, type) that has no record for
  today (or tomorrow), the most recent record from dc_sg_renewable is carried forward
  so downstream consumers always have continuous data.
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from .base_processor import BaseProcessor
from libs.models.renewable_serve_entity import RenewableServeEntity
from libs.enum.updated_flag import UpdatedFlag
from ..repository.renewable_serve_repository import renewable_serve_repository
from ..repository.dc_sg_renewable_repository import dc_sg_renewable_repository
from ..core.config import settings
from ..repository.plant_repository import plant_repository

logger = logging.getLogger(__name__)


class RenewableProcessor(BaseProcessor):
    """
    Renewable energy data processor — two-step pipeline with day carry-forward.
    """

    def __init__(self):
        super().__init__()
        self.renewable_repository = renewable_serve_repository
        self.dc_sg_renewable_repository = dc_sg_renewable_repository
        self.plant_repository = plant_repository

    # ---------------------------------------------------------------
    # Cron job entry point
    # ---------------------------------------------------------------

    async def execute_cron_job(self) -> Dict[str, Any]:
        """Execute renewable data processing as a cron job."""
        job_type = "renewable_data_processing"
        job_name = "Renewable Data Processing"
        schedule = "*/15 * * * *"
        start_time = datetime.now()

        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        if not execution_id:
            logger.error("Failed to record job start for %s", job_type)
            return {
                "status": "error",
                "message": "Failed to record job start",
                "execution_time": 0.0,
                "data_type": "renewable"
            }

        self.set_correlation_id(execution_id)
        logger.info("=== EXECUTING RENEWABLE DATA PROCESSING JOB ===")

        try:
            metadata = {
                "data_type": "renewable",
                "scraped_at": start_time,
                "source": "renewable_processor_cron",
                "triggered_by": "cron_job",
                "execution_id": execution_id,
                "correlation_id": execution_id
            }

            trigger_res = await self.process_metadata_trigger(metadata)
            success = trigger_res.get("success", False)

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            result = {
                "status": "success" if success else "error",
                "message": "Renewable data processed successfully" if success else "Renewable data processing failed",
                "execution_time": execution_time,
                "data_type": "renewable",
                "correlation_id": execution_id,
                "cron_job": "renewable",
                "trigger_details": trigger_res
            }

            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status=result["status"],
                success=success,
                result=result
            )

            logger.info("=== RENEWABLE DATA PROCESSING JOB COMPLETED ===")
            return result

        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            error_result = {
                "status": "error",
                "message": f"Renewable data processing failed: {str(e)}",
                "execution_time": execution_time,
                "data_type": "renewable",
                "correlation_id": execution_id,
                "error": str(e),
                "cron_job": "renewable"
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

            logger.error("=== RENEWABLE DATA PROCESSING JOB FAILED ===")
            return error_result

    # ---------------------------------------------------------------
    # Two-step processing — called by base processor's workflow
    # ---------------------------------------------------------------

    @staticmethod
    def _resolve_source(provider_config: Dict[str, Any]) -> str:
        """
        Determine the source label from the provider config's ingestion_type.
        Returns 'upload', 'api', or the raw ingestion_type value as a fallback.
        """
        ingestion_type = (
            provider_config.get("ingestion_type")
            or (provider_config.get("metadata") or {}).get("ingestion_type")
            or ""
        )
        # IngestionType enum or plain string
        if hasattr(ingestion_type, "value"):
            ingestion_type = ingestion_type.value
        ingestion_type = str(ingestion_type).lower()

        if ingestion_type == "upload":
            return "upload"
        if ingestion_type == "api":
            return "api"
        return ingestion_type or "unknown"

    async def process_raw_data_to_serve(
        self,
        raw_data: List[Dict[str, Any]],
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> bool:
        """
        Two-step pipeline with rollover:
          Step 1 — raw → dc_sg_renewable   (upsert; stores staging data; source=upload|api)
          Rollover — missing plant_ids (not in dc_sg today AND not in serve today)
                     are seeded from yesterday's dc_sg_renewable; source=dc_sg_rollover
          Step 2 — dc_sg_renewable → renewable_serve  (promote today/tomorrow)
        """
        try:
            source = self._resolve_source(provider_config)
            logger.info(
                "Processing %d renewable raw items — Step 1: raw → dc_sg_renewable (source=%s)",
                len(raw_data), source
            )

            # --- Step 1: raw → dc_sg_renewable ---
            step1_ok = await self._process_raw_to_dc_sg_renewable(raw_data, metadata, source)

            if not step1_ok:
                logger.error("Step 1 (raw → dc_sg_renewable) failed; aborting")
                return False

            # --- Rollover: fill missing plant_ids from yesterday ---
            today = datetime.now().date()
            await self._rollover_missing_plants(today)

            # --- Step 2: dc_sg_renewable → renewable_serve ---
            tomorrow = today + timedelta(days=1)
            logger.info("Step 2: dc_sg_renewable → renewable_serve for %s and %s", today, tomorrow)
            step2_ok = await self._process_dc_sg_to_serve(start_date=today, end_date=tomorrow)

            return step2_ok

        except Exception as e:
            logger.error("Error in process_raw_data_to_serve: %s", str(e))
            return False

    # ---------------------------------------------------------------
    # Step 1: raw → dc_sg_renewable
    # ---------------------------------------------------------------

    async def _process_raw_to_dc_sg_renewable(
        self,
        raw_data: List[Dict[str, Any]],
        metadata: Dict[str, Any],
        source: str = "unknown"
    ) -> bool:
        """
        Upsert raw renewable records into dc_sg_renewable staging collection.
        Unique key: scheduled_at, plant_id, time_block, type
        source is set from provider ingestion_type ('upload' or 'api').
        """
        try:
            plant_ids = {item.get("plant_id") for item in raw_data if item.get("plant_id")}

            # Bulk-fetch plants
            plants_map: Dict[str, Any] = {}
            if plant_ids:
                try:
                    found_plants = await self.plant_repository.find_many({"plant_id": {"$in": list(plant_ids)}})
                    for p in found_plants:
                        if p.get("plant_id"):
                            plants_map[p["plant_id"]] = p
                except Exception as e:
                    logger.error("Error bulk-fetching plants for dc_sg_renewable: %s", e)

            staging_docs = []
            skipped = 0

            for item in raw_data:
                pid = item.get("plant_id")
                if not pid:
                    skipped += 1
                    continue

                plant = plants_map.get(pid)
                if not plant:
                    skipped += 1
                    continue

                scheduled_at = item.get("scheduled_at")
                if not scheduled_at:
                    skipped += 1
                    continue

                schedule_volume = item.get("schedule_volume", 0.0)
                if not schedule_volume:
                    schedule_volume = item.get("volume", 0.0)

                updated_flag = item.get("updated_flag", UpdatedFlag.UPDATED.value)
                if isinstance(updated_flag, UpdatedFlag):
                    updated_flag = updated_flag.value

                source_revision = item.get("revision_no")

                doc = {
                    "scheduled_at": scheduled_at,
                    "plant_id": pid,
                    "time_block": item.get("time_block"),
                    "type": item.get("type"),
                    "schedule_volume": schedule_volume,
                    "sg_beforelosses": item.get("sg_beforelosses"),
                    "approval_no": plant.get("approval_no"),
                    "revision_no": source_revision,
                    "source_revision_number": source_revision,
                    "updated_flag": updated_flag,
                    "source": source,
                    "carried_forward": False,
                    "carry_source_date": None,
                    "updated_at": datetime.now(),
                    "inserted_at": datetime.now(),
                    "created_at": datetime.now(),
                }
                staging_docs.append(doc)

            if not staging_docs:
                logger.warning("No valid items for dc_sg_renewable (skipped=%d)", skipped)
                return False

            result = await asyncio.to_thread(self.dc_sg_renewable_repository.bulk_upsert, staging_docs)
            logger.info(
                "Step 1 complete: %d docs → dc_sg_renewable | inserted=%s modified=%s upserted=%s skipped=%d",
                len(staging_docs),
                result.get("inserted_count"),
                result.get("modified_count"),
                result.get("upserted_count"),
                skipped
            )
            return True

        except Exception as e:
            logger.error("Error in _process_raw_to_dc_sg_renewable: %s", str(e))
            return False

    # ---------------------------------------------------------------
    # Rollover: fill missing plant_ids from yesterday's dc_sg_renewable
    # ---------------------------------------------------------------

    async def _rollover_missing_plants(self, target_date) -> Dict[str, Any]:
        """
        Plant-id-level rollover check — runs every 15 min as part of the pipeline.

        For each plant that:
          - has NO record in dc_sg_renewable for target_date  AND
          - has NO record in renewable_serve  for target_date
        → fetch yesterday's dc_sg_renewable records for that plant,
          re-date them to target_date, and upsert with source='dc_sg_rollover'.

        The subsequent Step 2 (_process_dc_sg_to_serve) will then push them to serve.
        """
        try:
            yesterday = target_date - timedelta(days=1)
            target_start = datetime.combine(target_date, datetime.min.time())
            target_end = datetime.combine(target_date, datetime.max.time())

            # 1. Which plant_ids have ANY record in dc_sg_renewable for today?
            dc_sg_today = await self.dc_sg_renewable_repository.find_by_date(target_date)
            plants_in_dc_sg_today = {r.get("plant_id") for r in dc_sg_today if r.get("plant_id")}

            # 2. Which plant_ids have ANY record in renewable_serve for today?
            try:
                serve_today = await self.renewable_repository.serve_collection.find(
                    {"scheduled_at": {"$gte": target_start, "$lte": target_end}},
                    projection={"plant_id": 1, "_id": 0}
                ).to_list(length=10000)
                plants_in_serve_today = {r.get("plant_id") for r in serve_today if r.get("plant_id")}
            except Exception as e:
                logger.error("Error querying renewable_serve for today's plant_ids: %s", e)
                plants_in_serve_today = set()

            # 3. All known plants
            try:
                all_plants = await self.plant_repository.collection.find({}, {"plant_id": 1, "_id": 0}).to_list(length=10000)
                all_plant_ids = {p.get("plant_id") for p in all_plants if p.get("plant_id")}
            except Exception as e:
                logger.error("Error fetching all plants for rollover: %s", e)
                return {"rolled_count": 0, "error": str(e)}

            # 4. Missing = plants with no data in dc_sg_renewable AND no data in serve
            covered = plants_in_dc_sg_today | plants_in_serve_today
            missing_plant_ids = all_plant_ids - covered

            if not missing_plant_ids:
                logger.info("Rollover: all %d plants are covered for %s", len(all_plant_ids), target_date)
                return {"rolled_count": 0, "missing": 0}

            logger.info(
                "Rollover: %d plants missing for %s — fetching from yesterday (%s)",
                len(missing_plant_ids), target_date, yesterday
            )

            # 5. For each missing plant, fetch yesterday's dc_sg_renewable records
            yesterday_records = await self.dc_sg_renewable_repository.find_by_date(yesterday)

            # Index yesterday's records by plant_id
            yesterday_by_plant: Dict[str, List[Dict]] = {}
            for rec in yesterday_records:
                pid = rec.get("plant_id")
                if pid in missing_plant_ids:
                    yesterday_by_plant.setdefault(pid, []).append(rec)

            rollover_docs = []
            for pid in missing_plant_ids:
                plant_recs = yesterday_by_plant.get(pid)
                if not plant_recs:
                    logger.debug("Rollover: no yesterday data found in dc_sg_renewable for plant %s", pid)
                    continue

                for rec in plant_recs:
                    new_doc = {k: v for k, v in rec.items() if k != "_id"}
                    new_doc["scheduled_at"] = target_start
                    new_doc["source"] = "dc_sg_rollover"
                    new_doc["carry_source_date"] = rec.get("scheduled_at")
                    new_doc["carried_forward"] = True
                    new_doc["updated_at"] = datetime.now()
                    new_doc["inserted_at"] = datetime.now()
                    new_doc["system_revision_number"] = 1
                    rollover_docs.append(new_doc)

            if not rollover_docs:
                logger.info(
                    "Rollover: %d plants missing but no yesterday dc_sg_renewable data found",
                    len(missing_plant_ids)
                )
                return {"rolled_count": 0, "missing": len(missing_plant_ids)}

            result = await asyncio.to_thread(self.dc_sg_renewable_repository.bulk_upsert, rollover_docs)
            rolled = result.get("upserted_count", 0) + result.get("inserted_count", 0)
            logger.info(
                "Rollover: inserted/upserted %d records into dc_sg_renewable for %d missing plants",
                rolled, len(missing_plant_ids)
            )
            return {"rolled_count": rolled, "missing": len(missing_plant_ids)}

        except Exception as e:
            logger.error("Error in _rollover_missing_plants: %s", str(e))
            return {"rolled_count": 0, "error": str(e)}

    # ---------------------------------------------------------------
    # Step 2: dc_sg_renewable → renewable_serve
    # ---------------------------------------------------------------

    async def _process_dc_sg_to_serve(self, start_date, end_date) -> bool:
        """
        Fetch records from dc_sg_renewable for [start_date, end_date] and
        upsert them into renewable_serve.
        Unique key: scheduled_at, plant_id, time_block, type
        """
        try:
            staging_records = await self.dc_sg_renewable_repository.find_by_date_range(start_date, end_date)

            if not staging_records:
                logger.info("No dc_sg_renewable records found for %s – %s", start_date, end_date)
                return True

            today = datetime.now().date()
            day_before_yesterday = today - timedelta(days=2)

            serve_items = []
            historical_items = []

            for record in staging_records:
                scheduled_at_val = record.get("scheduled_at")
                if isinstance(scheduled_at_val, datetime):
                    scheduled_date = scheduled_at_val.date()
                else:
                    continue

                updated_flag_val = record.get("updated_flag", UpdatedFlag.UPDATED.value)
                if isinstance(updated_flag_val, str):
                    try:
                        updated_flag_val = UpdatedFlag(updated_flag_val)
                    except ValueError:
                        updated_flag_val = UpdatedFlag.UPDATED

                serve_entity = RenewableServeEntity(
                    scheduled_at=scheduled_at_val,
                    time_block=record.get("time_block"),
                    type=record.get("type"),
                    plant_id=record.get("plant_id"),
                    schedule_volume=record.get("schedule_volume", 0.0),
                    sg_beforelosses=record.get("sg_beforelosses"),
                    approval_no=record.get("approval_no"),
                    revision_no=record.get("revision_no"),
                    updated_flag=updated_flag_val,
                    source=record.get("source"),
                    updated_at=datetime.now(),
                    created_at=record.get("created_at", datetime.now()),
                    timestamp=datetime.now(),
                    source_revision_number=record.get("source_revision_number"),
                    system_revision_number=record.get("system_revision_number", 1),
                )

                entity_dict = serve_entity.model_dump(by_alias=True)

                if scheduled_date >= today:
                    serve_items.append(entity_dict)

                if scheduled_date < today:
                    historical_items.append(entity_dict)
                elif scheduled_date >= day_before_yesterday and scheduled_date < today:
                    historical_items.append(entity_dict)

            if serve_items:
                res = await asyncio.to_thread(
                    self.renewable_repository.bulk_upsert_renewable_serve, serve_items
                )
                logger.info(
                    "Step 2 serve upsert: %d items | inserted=%s modified=%s upserted=%s",
                    len(serve_items),
                    res.get("inserted_count"),
                    res.get("modified_count"),
                    res.get("upserted_count")
                )

            if historical_items:
                res_hist = await asyncio.to_thread(
                    self.renewable_repository.bulk_insert_renewable_historical_with_deduplication,
                    historical_items
                )
                logger.info(
                    "Step 2 historical: %d items | inserted=%s duplicates_removed=%s",
                    len(historical_items),
                    res_hist.get("inserted_count"),
                    res_hist.get("duplicates_removed")
                )

            return True

        except Exception as e:
            logger.error("Error in _process_dc_sg_to_serve: %s", str(e))
            return False

    # ---------------------------------------------------------------
    # Day carry-forward job
    # ---------------------------------------------------------------

    async def execute_day_carry_job(self) -> Dict[str, Any]:
        """
        Day-change carry-forward job — run at midnight (00:00).

        For each unique (time_block, plant_id, type) combination:
          - If today's record is missing in dc_sg_renewable → carry forward from last available
          - If tomorrow's record is missing → carry forward from last available (after today is filled)
        After carrying forward in dc_sg_renewable, promote carried records to renewable_serve.

        Scenario: As day changes, yesterday's full set of unique constraints is the
        baseline. Any constraint not yet received for today/tomorrow is seeded from
        the most recent historical record for that constraint.
        """
        job_type = "renewable_day_carry_forward"
        job_name = "Renewable Day Carry-Forward"
        schedule = "0 0 * * *"
        start_time = datetime.now()

        execution_id: str = await self._record_job_start(job_type, job_name, schedule, start_time) or ""
        if execution_id:
            self.set_correlation_id(execution_id)

        logger.info("=== EXECUTING RENEWABLE DAY CARRY-FORWARD JOB ===")

        try:
            today = datetime.now().date()
            tomorrow = today + timedelta(days=1)

            # Carry forward for today
            today_result = await self.dc_sg_renewable_repository.carry_forward_for_date(today)
            logger.info("Carry-forward today (%s): %s", today, today_result)

            # Carry forward for tomorrow
            tomorrow_result = await self.dc_sg_renewable_repository.carry_forward_for_date(tomorrow)
            logger.info("Carry-forward tomorrow (%s): %s", tomorrow, tomorrow_result)

            # Promote carried records to renewable_serve
            logger.info("Promoting carried records to renewable_serve …")
            promote_ok = await self._process_dc_sg_to_serve(start_date=today, end_date=tomorrow)

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            result = {
                "status": "success" if promote_ok else "partial",
                "message": "Day carry-forward completed",
                "today_carry": today_result,
                "tomorrow_carry": tomorrow_result,
                "serve_promoted": promote_ok,
                "execution_time": execution_time,
            }

            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id,
                status=result["status"],
                success=promote_ok,
                result=result
            )

            logger.info("=== RENEWABLE DAY CARRY-FORWARD JOB COMPLETED ===")
            return result

        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            error_result = {
                "status": "error",
                "message": f"Day carry-forward failed: {str(e)}",
                "execution_time": execution_time,
                "error": str(e),
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

            logger.error("=== RENEWABLE DAY CARRY-FORWARD JOB FAILED: %s ===", str(e))
            return error_result

    # ---------------------------------------------------------------
    # Archival job (unchanged logic, kept for compatibility)
    # ---------------------------------------------------------------

    async def execute_archival_job(self) -> Dict[str, Any]:
        """
        Renewable data archival job — runs daily at 12 AM.
        Keeps today, T+1, T-1, T-2 in serve. Archives and purges older records.
        Also triggers the day carry-forward so the two jobs are co-located at midnight.
        """
        job_type = "renewable_serve_archival"
        job_name = "Renewable Serve Data Archival"
        schedule = "0 0 * * *"
        start_time = datetime.now()

        # Generate and record job start
        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        if execution_id:
            self.set_correlation_id(execution_id)

        logger.info("=== EXECUTING RENEWABLE SERVE ARCHIVAL JOB ===")

        try:
            days_to_keep = 2
            today = datetime.now().date()
            archive_cutoff = today
            delete_cutoff = today - timedelta(days=days_to_keep)

            logger.info("Renewable archival: archive < %s, delete < %s", archive_cutoff, delete_cutoff)

            # 1. Find records to archive
            archival_candidates = await self.renewable_repository.find_many({
                "scheduled_at": {"$lt": datetime.combine(archive_cutoff, datetime.min.time())}
            }, limit=10000)

            # Bound before the branch: the result dict below reads this unconditionally,
            # and the "nothing to archive" path never entered the block that used to set
            # it -- so the job raised UnboundLocalError on its own healthy path.
            archived_count_actual = 0

            if not archival_candidates:
                logger.info("No renewable records found for archival")
            else:
                if archival_candidates:
                    logger.info(f"Found {len(archival_candidates)} renewable records to potentially archive")
                    
                    # 2. Prepare for historical insertion with Deduplication
                    historical_inserts = []
                    
                    min_date = min(d["scheduled_at"] for d in archival_candidates)
                    max_date = max(d["scheduled_at"] for d in archival_candidates)
                    
                    # Deduplication using Unique Key: scheduled_at, time_block, type, plant_id
                    existing_hist_docs = await self.renewable_repository.find_historical_many({
                        "scheduled_at": {"$gte": min_date, "$lte": max_date}
                    })

                    existing_keys = set()
                    for doc in existing_hist_docs:
                        key = (
                            doc.get("scheduled_at"),
                            doc.get("time_block"),
                            doc.get("type"),
                            doc.get("plant_id")
                        )
                        existing_keys.add(key)
                    
                    for doc in archival_candidates:
                        key = (
                            doc.get("scheduled_at"),
                            doc.get("time_block"),
                            doc.get("type"),
                            doc.get("plant_id")
                        )
                        
                        if key not in existing_keys:
                            hist_doc = dict(doc)
                            if "_id" in hist_doc: del hist_doc["_id"]
                            
                            hist_doc["archived_at"] = datetime.now()
                            hist_doc["archived_timestamp"] = datetime.now()
                            
                            historical_inserts.append(hist_doc)
                            existing_keys.add(key)
                    
                    if historical_inserts:
                        try:
                            repo_res = await self.renewable_repository.bulk_insert_renewable_historical_with_deduplication(historical_inserts)
                            archived_count_actual = repo_res.get("inserted_count", 0)
                            error_count = repo_res.get("error_count", 0)
                            logger.info(f"✅ Archived {archived_count_actual} new renewable records to historical. Errors: {error_count}")
                            if error_count > 0:
                                logger.warning(f"Renewable archival encountered {error_count} errors, continuing as non-fatal.")
                        except Exception as e:
                            logger.error(f"Error bulk inserting renewable history: {e}")

            # 2. Cleanup old records from serve
            delete_query = {
                "scheduled_at": {"$lt": datetime.combine(delete_cutoff, datetime.min.time())}
            }
            del_res = await self.renewable_repository.serve_collection.delete_many(delete_query)
            deleted_count = del_res.deleted_count
            logger.info("Cleaned up %d renewable records older than %s", deleted_count, delete_cutoff)

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            result = {
                "status": "success",
                "message": f"Archived renewable records. Deleted {deleted_count} old records.",
                "archived_count": archived_count_actual,
                "deleted_count": deleted_count,
                "cutoff_date": delete_cutoff.isoformat(),
                "execution_time": execution_time
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

            logger.info("=== RENEWABLE SERVE ARCHIVAL JOB COMPLETED ===")
            return result

        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            error_result = {
                "status": "error",
                "message": f"Renewable archival job failed: {str(e)}",
                "execution_time": execution_time,
                "error": str(e)
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

            logger.error("=== RENEWABLE SERVE ARCHIVAL JOB FAILED ===")
            logger.error("Error: %s", str(e))
            return error_result

    async def process_raw_data_to_serve(
        self, 
        raw_data: List[Dict[str, Any]], 
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> bool:
        """
        Process raw renewable data and store it in the serve collection using Bulk Operations.
        Implements the abstract method from BaseProcessor.
        """
        try:
            logger.info("Processing %d renewable raw data items (Bulk Mode)", len(raw_data))
            
            # Key extraction for bulk pre-fetching
            plant_ids = set()
            lookup_keys = []
            valid_items = []
            
            for item in raw_data:
                pid = item.get('plant_id')
                if pid:
                    plant_ids.add(pid)
                    # Use same key structure as repository
                    lookup_keys.append({
                        "scheduled_at": item.get('scheduled_at'),
                        "time_block": item.get('time_block'),
                        "type": item.get('type'),
                        "plant_id": pid
                    })
                    valid_items.append(item)
            
            # 1. Bulk Fetch Plants
            plants_map = {}
            if plant_ids:
                try:
                    found_plants = await self.plant_repository.find_many({"plant_id": {"$in": list(plant_ids)}})
                    for p in found_plants:
                        plants_map[p.get('plant_id')] = p
                except Exception as e:
                    logger.error(f"Error bulk fetching plants: {e}")
            
            # 2. Bulk Fetch Existing Records
            existing_records_map = {}
            if lookup_keys:
                found_recs = await self.renewable_repository.find_by_unique_keys(lookup_keys)
                for rec in found_recs:
                    k = (rec.get("scheduled_at"), rec.get("time_block"), rec.get("type"), rec.get("plant_id"))
                    existing_records_map[k] = rec
            
            # Prepare date boundaries
            today = datetime.now().date()
            day_before_yesterday = today - timedelta(days=2)
            
            serve_items = []
            historical_items = []
            
            processed_count = 0
            
            for data_item in valid_items:
                try:
                    pid = data_item.get('plant_id')
                    plant = plants_map.get(pid)
                    if not plant:
                        continue

                    # Parse scheduled_at for routing logic
                    scheduled_at_val = data_item.get('scheduled_at')
                    if isinstance(scheduled_at_val, str):
                        try:
                            # Handle 'Z' or simple ISO
                            scheduled_date = datetime.fromisoformat(scheduled_at_val.replace('Z', '+00:00')).date()
                        except:
                            continue
                    elif isinstance(scheduled_at_val, datetime):
                        scheduled_date = scheduled_at_val.date()
                    else:
                        continue
                        
                    # Lookup existing
                    key_tuple = (scheduled_at_val, data_item.get('time_block'), data_item.get('type'), pid)
                    existing_record = existing_records_map.get(key_tuple)
                    
                    # Create entity using helper methods to preserve timestamps and handle revisions
                    if existing_record:
                        renewable_entity = self._create_renewable_entity_with_preserved_timestamps(
                            data_item, metadata, plant, existing_record
                        )
                    else:
                        renewable_entity = self._create_renewable_entity_from_raw(
                            data_item, metadata, plant
                        )

                    if not renewable_entity:
                        continue

                    # Routing
                    # Serve Logic (Today, Tomorrow, Yesterday, DayBefore)
                    if scheduled_date >= today:
                        # Convert to dict for repository consumption
                        serve_items.append(renewable_entity.model_dump(by_alias=True))
                    
                    # Historical Logic
                    if scheduled_date < today:
                         # Earlier than today => Historical
                         historical_items.append(renewable_entity.model_dump(by_alias=True))
                    elif scheduled_date >= day_before_yesterday and scheduled_date < today:
                         # Yesterday/DayBefore => Both Serve (handled above) AND Historical
                         historical_items.append(renewable_entity.model_dump(by_alias=True))

                    processed_count += 1
                
                except Exception as e:
                    logger.error(f"Error processing item: {e}")
                    continue

            # Execute Bulk Serve Ops
            if serve_items:
                res = await self.renewable_repository.bulk_upsert_renewable_serve(serve_items)
                logger.info(f"Bulk processed {len(serve_items)} serve records. Inserted: {res.get('inserted_count')}, Modified: {res.get('modified_count')}, Upserted: {res.get('upserted_count')}")

            # Execute Historical Ops
            if historical_items:
                res_hist = await self.renewable_repository.bulk_insert_renewable_historical_with_deduplication(historical_items)
                logger.info(f"Processed {len(historical_items)} historical records. Inserted: {res_hist.get('inserted_count')}, Duplicates Removed: {res_hist.get('duplicates_removed')}")

            return processed_count > 0

        except Exception as e:
            logger.error("Error in process_raw_data_to_serve: %s", str(e))
            return False
        finally:
            try:
                if hasattr(self.renewable_repository, 'close'):
                    self.renewable_repository.close()
            except Exception as e:
                logger.warning(f"Error during connection cleanup: {e}")

    def _create_renewable_entity_from_raw(
        self,
        data_item: Dict[str, Any],
        metadata: Dict[str, Any],
        plant: Dict[str, Any]
    ) -> Optional[RenewableServeEntity]:
        """Create a new RenewableServeEntity directly from raw data (legacy helper)."""
        try:
            source_revision = data_item.get("revision_no") or 1

            schedule_volume = data_item.get("schedule_volume", 0.0)
            if not schedule_volume:
                schedule_volume = data_item.get("volume", 0.0)

            updated_flag = data_item.get("updated_flag", UpdatedFlag.UPDATED)
            if isinstance(updated_flag, str):
                updated_flag = UpdatedFlag(updated_flag)

            return RenewableServeEntity(
                scheduled_at=data_item.get("scheduled_at"),
                time_block=data_item.get("time_block"),
                type=data_item.get("type"),
                plant_id=data_item.get("plant_id"),
                schedule_volume=schedule_volume,
                sg_beforelosses=data_item.get("sg_beforelosses"),
                updated_flag=updated_flag,
                updated_at=datetime.now(),
                created_at=datetime.now(),
                timestamp=datetime.now(),
                source_revision_number=source_revision,
                system_revision_number=1,
                approval_no=plant.get("approval_no")
            )
        except Exception as e:
            logger.error("Error creating RenewableServeEntity from raw data: %s", e)
            return None

    def _create_renewable_entity_with_preserved_timestamps(
        self,
        data_item: Dict[str, Any],
        metadata: Dict[str, Any],
        plant: Dict[str, Any],
        existing_record: Dict[str, Any]
    ) -> Optional[RenewableServeEntity]:
        """Create a RenewableServeEntity preserving timestamps from an existing record."""
        try:
            existing_system_revision = existing_record.get("system_revision_number", 0)
            new_system_revision = existing_system_revision + 1

            source_revision = data_item.get("revision_no")
            if source_revision is None:
                source_revision = existing_record.get("source_revision_number") or new_system_revision

            schedule_volume = data_item.get("schedule_volume", 0.0)
            if not schedule_volume:
                schedule_volume = data_item.get("volume", 0.0)

            updated_flag = data_item.get("updated_flag", UpdatedFlag.UPDATED)
            if isinstance(updated_flag, str):
                updated_flag = UpdatedFlag(updated_flag)

            return RenewableServeEntity(
                scheduled_at=data_item.get("scheduled_at"),
                time_block=data_item.get("time_block"),
                type=data_item.get("type"),
                plant_id=data_item.get("plant_id"),
                schedule_volume=schedule_volume,
                sg_beforelosses=data_item.get("sg_beforelosses"),
                updated_flag=updated_flag,
                updated_at=datetime.now(),
                created_at=existing_record.get("created_at", datetime.now()),
                timestamp=datetime.now(),
                source_revision_number=source_revision,
                system_revision_number=new_system_revision,
                approval_no=plant.get("approval_no")
            )
        except Exception as e:
            logger.error("Error creating RenewableServeEntity with preserved timestamps: %s", e)
            return None
