import asyncio
import logging
from typing import Dict, Any, Optional, List

from services.processing.app.v2.services.new_gdam_bidding_services import gdam_bidding_services

logger = logging.getLogger(__name__)


class GDAMBidCaptureProcessor:
    async def execute_block_capture_job(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Process queued GDAM bid-capture tasks after submit-for-approval (one or many blocks).
        All capture tasks run concurrently instead of sequentially.
        """
        captures: List[Dict[str, Any]] = (payload or {}).get("captures") or []
        if not captures:
            return {"status": "success", "processed": 0, "errors": []}

        results = await asyncio.gather(
            *[gdam_bidding_services.apply_capture_task(task) for task in captures],
            return_exceptions=True,
        )

        errors: List[Dict[str, Any]] = []
        processed = 0
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.error("GDAM block capture task %s failed: %s", i, result, exc_info=result)
                errors.append({"index": i, "error": str(result)})
            else:
                processed += 1

        return {
            "status": "success" if not errors else "partial",
            "processed": processed,
            "errors": errors,
        }


gdam_bid_capture_processor = GDAMBidCaptureProcessor()
