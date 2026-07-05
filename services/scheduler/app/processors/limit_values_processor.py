"""
Limit Values Processor for moving approved future-dated entries to final collection
"""

from libs.core.job_entities import JobData
import logging
from datetime import datetime, date
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


from .base_processor import BaseProcessor
from ..core.config import settings
from ..repository.limit_values_repository import LimitValuesRepository
from libs.core.job_repositories import JobDataRepository

logger = logging.getLogger(__name__)


class LimitValuesProcessor(BaseProcessor):
    """
    Processor for handling approved limit values with future dates.
    Moves approved entries from approval collection to final collection when date arrives.
    """
    
    def __init__(self):
        """Initialize the limit values processor"""
        super().__init__()
        self.approval_collection_name = "limit_approval"
        self.final_collection_name = "limit_values"
        self.audit_collection_name = "limit_audit_log"
        self.repository = LimitValuesRepository()
        # job_data_repository is already initialized by BaseProcessor.__init__()
        
    async def execute_rtm_early_cron_job(self) -> Dict[str, Any]:
        """
        Entry point for the RTM early rollover cron job (22:15 IST).

        RTM limit values are rolled to tomorrow before midnight so that the
        00:00 run can skip RTM and avoid double-rollover.
        """
        return await self.execute_cron_job(rtm_early=True)

    async def execute_cron_job(self, rtm_early: bool = False) -> Dict[str, Any]:
        """
        Main entry point for cron job execution.

        Args:
            rtm_early: When True, only RTM market records are rolled to tomorrow.
                       The regular 00:00 run skips RTM records that were already
                       rolled by the early job.
        """
        if rtm_early:
            job_type = "limit_values_rtm_approval_processing"
            job_name = "Limit Values RTM Early Approval Processing"
            default_schedule = "15 22 * * *"
        else:
            job_type = "limit_values_approval_processing"
            job_name = "Limit Values Approval Processing"
            default_schedule = "0 0 * * *"

        job_detail = await self.job_data_repository.get_job(job_type)
        start_time = datetime.now()
        schedule = job_detail.get("schedule", default_schedule)

        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        if not execution_id:
            return {"status": "error", "message": "Failed to record job start"}

        self.set_correlation_id(execution_id)
        
        try:
            logger.info("[LimitValuesProcessor] Starting optimized limit values processing") 
            
            from datetime import timedelta
            
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow = today + timedelta(days=1)
            today_str = today.strftime("%Y-%m-%d")
            tomorrow_str = tomorrow.strftime("%Y-%m-%d")
            
            current_time = datetime.now()

            # Market-specific rollover behaviour:
            # - RTM early run (22:15) only touches RTM records.
            # - Midnight run touches all markets except RTM records already rolled today.
            base_id_filter = {"id": {"$nin": ["RTM_forecasted_price", "DAM_forecasted_price"]}}
            rtm_market = "RTM"
            market_filter = {"market": rtm_market} if rtm_early else {}

            def _build_query(scheduled_at, extra_filters=None):
                query = {"scheduled_at": scheduled_at, **base_id_filter}
                if extra_filters:
                    query.update(extra_filters)
                return query
            
            # 1. Fetch Today's Data for Rollover to Tomorrow
            today_existing_query = _build_query(today, market_filter)
            today_existing_data = await self.repository.find_many(self.final_collection_name, today_existing_query, limit=10000)
            logger.info(f"[LimitValuesProcessor] Found {len(today_existing_data)} records for today to rollover to tomorrow")
            
            merged_data = {}
            tomorrow_rollover_data = {}

            # Build today's existing keys for per-record skip check
            # Normalize scheduled_at to naive datetime to match Python-computed today/tomorrow
            def _normalize_dt(dt):
                if dt is not None and hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
                    return dt.replace(tzinfo=None)
                return dt

            today_existing_keys = {
                (_normalize_dt(r.get("scheduled_at")), r.get("block"), r.get("market"), r.get("id"))
                for r in today_existing_data
            }

            yesterday = today - timedelta(days=1)
            yesterday_existing_data = await self.repository.find_many(
                self.final_collection_name,
                _build_query(yesterday, market_filter),
                limit=10000,
            )
            logger.info(f"[LimitValuesProcessor] Found {len(yesterday_existing_data)} records for yesterday to rollover to today")
            if today_existing_data:
                logger.info(f"[LimitValuesProcessor] Today ({today_str}) already has {len(today_existing_data)} records; will skip per-record duplicates")
            for record in yesterday_existing_data:
                market = record.get("market")
                # RTM early run only rolls RTM; midnight run allows all markets.
                if rtm_early and market != rtm_market:
                    continue
                rollover_key = (today, record.get("block"), market, record.get("id"))
                if rollover_key in today_existing_keys:
                    continue
                record_id = record.get("id")
                unique_key_tuple = (
                    record.get("block"),
                    record_id,
                    record.get("market"),
                    record.get("category"),
                    record.get("area"),
                    today, # Important: merged for today
                    
                )

                # Clone record for today
                today_record = record.copy()
                today_record.pop("_id", None)
                today_record["scheduled_at"] = today
                today_record["source"] = "yesterday_rollover"
                today_record["created_by"] = "system_cron"
                today_record["updated_by"] = "system_cron"

                merged_data[unique_key_tuple] = today_record
            
            # Fetch all of tomorrow's existing data to do per-record skip check
            tomorrow_existing_data = await self.repository.find_many(
                self.final_collection_name,
                _build_query(tomorrow, market_filter),
                limit=10000,
            )
            tomorrow_existing_keys = {
                (_normalize_dt(r.get("scheduled_at")), r.get("block"), r.get("market"), r.get("id"))
                for r in tomorrow_existing_data
            }
            tomorrow_source_map = {
                (r.get("block"), r.get("market"), r.get("id")): r.get("source")
                for r in tomorrow_existing_data
            }
            if tomorrow_existing_data:
                logger.info(f"[LimitValuesProcessor] Tomorrow ({tomorrow_str}) already has {len(tomorrow_existing_data)} records; will skip per-record duplicates")

            # 1a. Prepare Rollover Data — upsert if tomorrow has stale rollover, skip if manual/approval
            for record in today_existing_data:
                market = record.get("market")
                # RTM early run only rolls RTM; midnight run allows all markets.
                if rtm_early and market != rtm_market:
                    continue
                rollover_key = (tomorrow, record.get("block"), market, record.get("id"))
                if rollover_key in tomorrow_existing_keys:
                    # Only overwrite if tomorrow's value is itself a rollover (not manual/approval)
                    existing_source = tomorrow_source_map.get((record.get("block"), market, record.get("id")))
                    if existing_source not in ("today_rollover", "yesterday_rollover"):
                        continue  # tomorrow has manual/approval data, preserve it
                    # If RTM was already rolled by the 22:15 early job, do not roll it again at midnight.
                    if not rtm_early and market == rtm_market and existing_source == "today_rollover":
                        continue
                # Remove _id and other internal fields for fresh upsert
                record_id = record.get("id")
                unique_key_tuple = (
                    record.get("block"),
                    record_id,
                    record.get("market"),
                    record.get("category"),
                    record.get("area"),
                    tomorrow, # Important: merged for tomorrow
                )

                # Clone record for tomorrow
                tomorrow_record = record.copy()
                tomorrow_record.pop("_id", None)
                tomorrow_record["scheduled_at"] = tomorrow
                tomorrow_record["source"] = "today_rollover"
                tomorrow_record["created_by"] = "system_cron"
                tomorrow_record["updated_by"] = "system_cron"

                tomorrow_rollover_data[unique_key_tuple] = tomorrow_record

            # 2. Fetch tomorrow's approved entries — these are built separately and always upsert
            approval_market = rtm_market if rtm_early else None
            approved_entries_tomorrow = await self.repository.get_approved_tomorrow(tomorrow, market=approval_market)
            logger.info(f"[LimitValuesProcessor] Found {len(approved_entries_tomorrow)} approved entries for tomorrow to merge")

            approval_ids_to_delete = []
            approval_docs = {}

            for entry in approved_entries_tomorrow:
                entry_id = entry.get("id") or entry.get("ID")
                unique_key_tuple = (
                    entry.get("block"),
                    entry_id,
                    entry.get("market"),
                    entry.get("category"),
                    entry.get("area"),
                    tomorrow
                )

                approval_doc = {
                    "scheduled_at": tomorrow,
                    "block": entry.get("block"),
                    "id": entry_id,
                    "market": entry.get("market", "RTM"),
                    "category": entry.get("category", "Limits"),
                    "area": entry.get("area", ""),
                    "values": entry.get("value") or entry.get("values"),
                    "unit": entry.get("unit", "MW"),
                    "inserted_at": current_time,
                    "created_by": entry.get("requested_by", "system"),
                    "updated_by": entry.get("approved_by", "system"),
                    "updated_at": current_time,
                    "source_approval_id": str(entry.get("_id")),
                    "source": "approval"
                }
                approval_docs[unique_key_tuple] = approval_doc
                approval_ids_to_delete.append(entry.get("_id"))

            unique_key_fields = ["block", "id", "market", "category", "area", "scheduled_at"]
            audit_logs = []

            # 3a. Rollover docs — yesterday→today insert only, today→tomorrow upsert (to propagate manual edits)
            rollover_documents = list(merged_data.values())
            if rollover_documents:
                logger.info(f"[LimitValuesProcessor] Bulk insert-only (yesterday rollover) of {len(rollover_documents)} records for {today_str}")
                insert_result = await self.repository.bulk_insert_only(
                    self.final_collection_name,
                    rollover_documents,
                    unique_key_fields
                )
                logger.info(f"[LimitValuesProcessor] Yesterday rollover insert result: {insert_result}")

            tomorrow_rollover_documents = list(tomorrow_rollover_data.values())
            if tomorrow_rollover_documents:
                logger.info(f"[LimitValuesProcessor] Bulk upsert (today rollover) of {len(tomorrow_rollover_documents)} records for {tomorrow_str}")
                insert_result = await self.repository.bulk_upsert_optimized(
                    self.final_collection_name,
                    tomorrow_rollover_documents,
                    unique_key_fields
                )
                logger.info(f"[LimitValuesProcessor] Today rollover upsert result: {insert_result}")
                for doc in rollover_documents:
                    audit_logs.append({
                        "ts": current_time,
                        "actor_id": "system_cron",
                        "actor_username": "limit_values_processor",
                        "action": "ROLLOVER_INSERT_LIMIT_VALUES",
                        "entity_type": "limit_values",
                        "operation": "INSERT",
                        "correlation_id": f"cron_{today_str}",
                        "key": {field: doc.get(field) for field in unique_key_fields},
                        "scheduled_at": doc.get("scheduled_at"),
                        "source": doc.get("source"),
                        "success": True
                    })
            if tomorrow_rollover_documents:
                for doc in tomorrow_rollover_documents:
                    audit_logs.append({
                        "ts": current_time,
                        "actor_id": "system_cron",
                        "actor_username": "limit_values_processor",
                        "action": "ROLLOVER_UPSERT_LIMIT_VALUES",
                        "entity_type": "limit_values",
                        "operation": "UPSERT",
                        "correlation_id": f"cron_{today_str}",
                        "key": {field: doc.get(field) for field in unique_key_fields},
                        "scheduled_at": doc.get("scheduled_at"),
                        "source": doc.get("source"),
                        "success": True
                    })
            else:
                logger.info("[LimitValuesProcessor] No rollover records to upsert for tomorrow")

            # 3b. Approval docs — full upsert, can update existing records
            approval_documents = list(approval_docs.values())
            if approval_documents:
                logger.info(f"[LimitValuesProcessor] Bulk upsert (approvals) of {len(approval_documents)} records for {tomorrow_str}")
                upsert_result = await self.repository.bulk_upsert_optimized(
                    self.final_collection_name,
                    approval_documents,
                    unique_key_fields
                )
                logger.info(f"[LimitValuesProcessor] Approval upsert result: {upsert_result}")
                for doc in approval_documents:
                    audit_logs.append({
                        "ts": current_time,
                        "actor_id": "system_cron",
                        "actor_username": "limit_values_processor",
                        "action": "APPROVAL_UPSERT_LIMIT_VALUES",
                        "entity_type": "limit_values",
                        "operation": "UPSERT",
                        "correlation_id": f"cron_{today_str}",
                        "key": {field: doc.get(field) for field in unique_key_fields},
                        "scheduled_at": doc.get("scheduled_at"),
                        "source": doc.get("source"),
                        "success": True
                    })

            # 4. Batch Audit Logs
            if audit_logs:
                await self.repository.insert_many(self.audit_collection_name, audit_logs)

            # 5. Historical Archival (Older than today)
            # Archival is only run at midnight; the 22:15 RTM early run does not move data.
            if rtm_early:
                logger.info("[LimitValuesProcessor] Skipping historical archival during RTM early run")
                archive_result = {"skipped": True, "reason": "rtm_early_run"}
            else:
                archive_result = await self.repository.archive_to_historical(today)
                logger.info(f"[LimitValuesProcessor] Archival result: {archive_result}")

            # 6. Delete processed approval entries
            if approval_ids_to_delete:
                delete_res = await self.repository.delete_many(self.approval_collection_name, {"_id": {"$in": approval_ids_to_delete}})
                logger.info(f"[LimitValuesProcessor] Deleted {delete_res.deleted_count} processed approval entries")
            
            market_scope = "RTM" if rtm_early else "non-RTM/all"
            result = {
                "status": "success",
                "message": f"Limit values processed successfully for {tomorrow_str} (market scope: {market_scope})",
                "execution_time": (datetime.now() - start_time).total_seconds(),
                "job_type": job_type,
                "correlation_id": execution_id
            }

            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=datetime.now(),
                execution_id=execution_id,
                status="success",
                success=True,
                result=result
            )

            logger.info(f"[LimitValuesProcessor] Processing completed successfully")
            return result
            
        except Exception as e:
            end_time = datetime.now()
            error_result = {
                "status": "error",
                "message": f"Limit values processing failed: {str(e)}",
                "execution_time": (end_time - start_time).total_seconds(),
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
            logger.exception(f"[LimitValuesProcessor] Failed to execute cron job: {str(e)}")
            return error_result
        finally:
            self.clear_correlation_id()

    async def process_raw_data_to_serve(
        self, 
        raw_data: List[Dict[str, Any]], 
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any]
    ) -> bool:
        """
        Implementation of abstract method from BaseProcessor.
        LimitValuesProcessor moves approved data to serve, it does not
        process raw data documents from the ingestion flow.
        """
        return True

    async def rename_id_field(self):
        """
        Renames the 'ID' field to 'id' in both limit_values and limit_approval collections.
        """
        try:
            logger.info("[LimitValuesProcessor] Starting 'ID' to 'id' field rename process")
            
            from libs.database.mongo_client_factory import get_scheduler_client
            from services.scheduler.app.core.config import settings
            
            # Use centralized MongoDB client (singleton pattern)
            client = get_scheduler_client(settings.MONGODB_URI)
            db = client[settings.MONGODB_DATABASE]
            
            collections_to_migrate = [self.final_collection_name, self.approval_collection_name]
            
            for collection_name in collections_to_migrate:
                collection = db[collection_name]
                
                # Find documents where 'ID' exists and 'id' does not
                query = {"ID": {"$exists": True}, "id": {"$exists": False}}
                
                # Rename the field
                result = await collection.update_many(query, {"$rename": {"ID": "id"}})

                logger.info(f"[LimitValuesProcessor] In collection '{collection_name}', renamed 'ID' to 'id' in {result.modified_count} documents.")
            
            # Note: Client is managed by MongoClientFactory, no need to close
            
            return True
            
        except Exception as e:
            logger.exception(f"[LimitValuesProcessor] Failed to rename 'ID' field: {str(e)}")
            return False
