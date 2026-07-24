import asyncio
import logging
from typing import Dict, Any, Optional, List

from services.processing.app.v2.services.new_hpdam_bidding_services import hpdam_bidding_services

logger = logging.getLogger(__name__)


class HPDAMBidCaptureProcessor:
    async def execute_block_capture_job(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Process queued HPDAM bid-capture tasks after submit-for-approval (one or many blocks).
        All capture tasks run concurrently instead of sequentially.
        """
        captures: List[Dict[str, Any]] = (payload or {}).get("captures") or []
        if not captures:
            return {"status": "success", "processed": 0, "errors": []}

        results = await asyncio.gather(
            *[hpdam_bidding_services.apply_capture_task(task) for task in captures],
            return_exceptions=True,
        )

        errors: List[Dict[str, Any]] = []
        processed = 0
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.error("HPDAM block capture task %s failed: %s", i, result, exc_info=result)
                errors.append({"index": i, "error": str(result)})
            else:
                processed += 1

        return {
            "status": "success" if not errors else "partial",
            "processed": processed,
            "errors": errors,
        }


hpdam_bid_capture_processor = HPDAMBidCaptureProcessor()
