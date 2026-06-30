"""
Merges forecast_serve (rtm_forecast_api or csv) into demand_serve IDF records.
Runs every 15 minutes; respects idf_forecast_switch in parameters collection.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from libs.helpers.idf_forecast_audit import switch_change_audit_fields
from libs.services.idf_forecast_merge_service import IdfForecastMergeService
from .base_processor import BaseProcessor
from ..core.config import settings
from ..repository.demand_serve_repository import demand_serve_repository
from ..repository.forecast_serve_repository import forecast_serve_repository
from ..repository.parameter_repository import parameter_repository

logger = logging.getLogger(__name__)


class IdfForecastMergeProcessor(BaseProcessor):
    """Publish active forecast source from forecast_serve into demand_serve (IDF)."""

    def __init__(self):
        super().__init__()
        self.demand_repository = demand_serve_repository
        self.forecast_repository = forecast_serve_repository
        self.parameter_repository = parameter_repository
        self._merge_service = IdfForecastMergeService(
            parameter_repository=self.parameter_repository,
            forecast_repository=self.forecast_repository,
            demand_serve_collection=self.demand_repository.serve_collection,
            audit_callback=self._audit_switch_change,
        )

    async def execute_cron_job(self) -> Dict[str, Any]:
        job_type = "idf_forecast_merge"
        job_name = "IDF Forecast Merge"
        schedule = getattr(settings, "IDF_FORECAST_MERGE_CRON_SCHEDULE", "*/15 * * * *")
        start_time = datetime.now()

        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        self.set_correlation_id(execution_id or "")

        logger.info("=== EXECUTING IDF FORECAST MERGE JOB ===")

        try:
            merged, skipped, failed = await self.run_merge()

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            result = {
                "status": "success" if failed == 0 else "partial",
                "message": f"IDF forecast merge — merged={merged}, skipped={skipped}, failed={failed}",
                "merged": merged,
                "skipped": skipped,
                "failed": failed,
                "execution_time": execution_time,
                "data_type": "idf_forecast_merge",
            }

            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id or "",
                status=result["status"],
                success=failed == 0,
                result=result,
            )
            return result
        except Exception as e:
            logger.error("Error in IDF forecast merge job: %s", e, exc_info=True)
            return {"status": "error", "message": str(e)}
        finally:
            self.clear_correlation_id()

    async def execute_forecast_serve_archival_job(self) -> Dict[str, Any]:
        """Archive old forecast_serve rows to forecast_serve_historical (daily job)."""
        job_type = "forecast_serve_archival"
        job_name = "Forecast Serve Data Archival"
        schedule = "0 0 * * *"
        start_time = datetime.now()

        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        self.set_correlation_id(execution_id or "")

        logger.info("=== EXECUTING FORECAST SERVE ARCHIVAL JOB ===")

        try:
            days_to_keep = getattr(settings, "FORECAST_SERVE_DAYS_TO_KEEP", 2)
            archive_result = await self.forecast_repository.archive_old_forecast_data(
                days_to_keep=days_to_keep
            )

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()

            if archive_result.get("error"):
                result = {
                    "status": "error",
                    "message": f"Forecast serve archival error: {archive_result['error']}",
                    "execution_time": execution_time,
                    "data_type": "forecast_serve",
                    "correlation_id": execution_id,
                }
                await self._record_job_completion(
                    job_type=job_type,
                    job_name=job_name,
                    schedule=schedule,
                    execution_start=start_time,
                    execution_end=end_time,
                    execution_id=execution_id or "",
                    status="error",
                    success=False,
                    result=result,
                    error_message=archive_result["error"],
                )
                return result

            archived_count = archive_result.get("archived_count", 0)
            deleted_count = archive_result.get("deleted_count", 0)
            result = {
                "status": "success",
                "message": (
                    f"Archived {archived_count} forecast records, "
                    f"deleted {deleted_count} from forecast_serve"
                ),
                "execution_time": execution_time,
                "archived_count": archived_count,
                "deleted_count": deleted_count,
                "cutoff_date": archive_result.get("cutoff_date"),
                "data_type": "forecast_serve",
                "correlation_id": execution_id,
            }
            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id or "",
                status="success",
                success=True,
                result=result,
            )
            logger.info(
                "Forecast serve archival completed: archived=%d deleted=%d in %.2fs",
                archived_count,
                deleted_count,
                execution_time,
            )
            return result
        except Exception as e:
            logger.error("Error executing forecast serve archival job: %s", e, exc_info=True)
            return {"status": "error", "message": str(e)}
        finally:
            self.clear_correlation_id()

    def _audit_switch_change(
        self,
        action: str,
        message: str,
        previous: Dict[str, Any],
        new_state: Dict[str, Any],
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write audit trail for switch changes (system or future callers)."""
        try:
            from libs.middleware import get_correlation_id
            from ..core.dependencies import get_audit_logger

            correlation_id = get_correlation_id() or self.get_correlation_id() or ""
            audit_logger = get_audit_logger()
            audit_logger.log_system_operation(
                service="scheduler",
                operation=action,
                message=message,
                correlation_id=correlation_id,
                success=True,
                **switch_change_audit_fields(previous, new_state),
                **(extra or {}),
            )
        except Exception as audit_err:
            logger.warning("Failed to write IDF forecast switch audit log: %s", audit_err)

    async def run_merge(
        self,
        skip_daily_reset: bool = False,
        skip_auto_flip: bool = False,
    ) -> Tuple[int, int, int]:
        tz_name = getattr(settings, "CRON_TIMEZONE", None) or "Asia/Kolkata"
        min_ahead = max(
            0,
            int(getattr(settings, "RTM_FORECAST_MIN_AHEAD_BLOCKS", 5)),
        )
        return await self._merge_service.run_merge(
            skip_daily_reset=skip_daily_reset,
            skip_auto_flip=skip_auto_flip,
            tz_name=tz_name,
            min_ahead_blocks=min_ahead,
        )

    async def process_raw_data_to_serve(
        self,
        raw_data: List[Dict[str, Any]],
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any],
    ) -> bool:
        """Cron-only processor; raw-data pipeline not used."""
        return True
