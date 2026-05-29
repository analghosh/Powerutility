import asyncio
import logging
from typing import Dict, Any, Optional, List

from services.processing.app.v2.services.new_dam_bidding_services import dam_bidding_services

logger = logging.getLogger(__name__)


class DAMBidCaptureProcessor:
    async def execute_block_capture_job(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Process queued DAM bid-capture tasks after submit-for-approval (one or many blocks).
        All capture tasks run concurrently instead of sequentially.
        """
        captures: List[Dict[str, Any]] = (payload or {}).get("captures") or []
        if not captures:
            return {"status": "success", "processed": 0, "errors": []}

        results = await asyncio.gather(
            *[dam_bidding_services.apply_capture_task(task) for task in captures],
            return_exceptions=True,
        )

        errors: List[Dict[str, Any]] = []
        processed = 0
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.error("DAM block capture task %s failed: %s", i, result, exc_info=result)
                errors.append({"index": i, "error": str(result)})
            else:
                processed += 1

        return {
            "status": "success" if not errors else "partial",
            "processed": processed,
            "errors": errors,
        }

    async def execute_cron_job(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            response = await dam_bidding_services.capture_daily_snapshot()
            status = (response or {}).get("status")
            if status != "success":
                return {
                    "status": "error",
                    "code": (response or {}).get("code", 500),
                    "message": "DAM daily bid capture snapshot failed",
                    "response": response,
                }
            return {
                "status": "success",
                "code": (response or {}).get("code", 200),
                "message": "DAM daily bid capture snapshot triggered internally",
                "response": response,
            }
        except Exception as exc:
            logger.error("DAM bid capture cron failed: %s", exc)
            return {"status": "error", "message": str(exc)}


dam_bid_capture_processor = DAMBidCaptureProcessor()
