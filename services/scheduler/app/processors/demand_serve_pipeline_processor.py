"""
Sequential demand_serve pipeline: IDF forecast merge → demand processing → new DSM.

Replaces independent 15-minute cron runs for those steps when demand_serve_pipeline is enabled.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .base_processor import BaseProcessor
from .demand_processor import DemandProcessor
from .idf_forecast_merge_processor import IdfForecastMergeProcessor
from .new_dsm_processor import NewDSMProcessor
from ..core.config import settings

logger = logging.getLogger(__name__)

PIPELINE_STEPS: Tuple[Tuple[str, str], ...] = (
    ("idf_forecast_merge", "IDF Forecast Merge"),
    ("demand_data_processing", "Demand Data Processing"),
    ("new_dsm_rate_calculation", "New DSM Rate Calculation"),
)


class DemandServePipelineProcessor(BaseProcessor):
    """Runs IDF merge, demand processing, and new DSM in strict order."""

    def __init__(
        self,
        idf_processor: Optional[IdfForecastMergeProcessor] = None,
        demand_processor: Optional[DemandProcessor] = None,
        dsm_processor: Optional[NewDSMProcessor] = None,
    ):
        super().__init__()
        self.idf_processor = idf_processor or IdfForecastMergeProcessor()
        self.demand_processor = demand_processor or DemandProcessor()
        self.dsm_processor = dsm_processor or NewDSMProcessor()

    def process_raw_data_to_serve(
        self,
        raw_data: List[Dict[str, Any]],
        metadata: Dict[str, Any],
        provider_config: Dict[str, Any],
    ) -> bool:
        """
        Satisfy BaseProcessor abstract contract.
        This processor is orchestration-only and does not process raw records directly.
        """
        logger.debug(
            "DemandServePipelineProcessor.process_raw_data_to_serve called (no-op): "
            "raw_data=%s provider=%s",
            len(raw_data or []),
            provider_config.get("code") if isinstance(provider_config, dict) else None,
        )
        return True

    async def execute_cron_job(self) -> Dict[str, Any]:
        job_type = "demand_serve_pipeline"
        job_name = "Demand Serve Pipeline (IDF → Demand → DSM)"
        schedule = getattr(settings, "DEMAND_SERVE_PIPELINE_CRON_SCHEDULE", "*/15 * * * *")
        start_time = datetime.now()

        execution_id = await self._record_job_start(job_type, job_name, schedule, start_time)
        self.set_correlation_id(execution_id or "")

        logger.info("=== EXECUTING DEMAND SERVE PIPELINE (IDF → Demand → DSM) ===")

        step_results: List[Dict[str, Any]] = []
        overall_status = "success"
        abort_remaining = False

        try:
            runners = (
                self.idf_processor.execute_cron_job,
                self.demand_processor.execute_cron_job,
                self.dsm_processor.execute_cron_job,
            )

            for (step_job_type, step_name), run_step in zip(PIPELINE_STEPS, runners):
                if abort_remaining:
                    step_results.append(
                        {
                            "step": step_job_type,
                            "name": step_name,
                            "status": "skipped",
                            "message": "Skipped due to prior step failure",
                        }
                    )
                    continue

                logger.info("Pipeline step start: %s (%s)", step_name, step_job_type)
                step_start = datetime.now()
                try:
                    result = await run_step()
                except Exception as step_exc:
                    logger.error(
                        "Pipeline step %s raised: %s", step_job_type, step_exc, exc_info=True
                    )
                    result = {
                        "status": "error",
                        "message": str(step_exc),
                        "execution_time": (datetime.now() - step_start).total_seconds(),
                    }

                if isinstance(result, dict):
                    step_status = result.get("status", "error")
                elif result is True:
                    step_status = "success"
                elif result is False:
                    step_status = "error"
                else:
                    step_status = "error"
                result_dict = result if isinstance(result, dict) else {}
                step_entry = {
                    "step": step_job_type,
                    "name": step_name,
                    "status": step_status,
                    "message": result_dict.get("message"),
                    "execution_time": result_dict.get("execution_time"),
                    "result": result,
                }
                step_results.append(step_entry)

                if step_status == "partial":
                    overall_status = "partial"
                elif step_status != "success":
                    overall_status = "error"
                    abort_remaining = True
                    logger.error(
                        "Pipeline stopping after failed step %s (status=%s)",
                        step_job_type,
                        step_status,
                    )
                else:
                    logger.info("Pipeline step completed: %s", step_job_type)

            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            completed_steps = sum(1 for s in step_results if s.get("status") == "success")

            result = {
                "status": overall_status,
                "message": (
                    f"Demand serve pipeline finished — {completed_steps}/{len(PIPELINE_STEPS)} "
                    f"steps succeeded"
                ),
                "execution_time": execution_time,
                "data_type": "demand_serve_pipeline",
                "steps": step_results,
                "correlation_id": execution_id,
            }

            await self._record_job_completion(
                job_type=job_type,
                job_name=job_name,
                schedule=schedule,
                execution_start=start_time,
                execution_end=end_time,
                execution_id=execution_id or "",
                status=result["status"],
                success=overall_status == "success",
                result=result,
            )
            return result
        except Exception as e:
            logger.error("Demand serve pipeline error: %s", e, exc_info=True)
            return {"status": "error", "message": str(e), "steps": step_results}
        finally:
            self.clear_correlation_id()
