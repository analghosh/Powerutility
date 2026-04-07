import logging
from typing import Dict, Any, Optional, List

from services.processing.app.v2.services.new_rtm_bidding_services import rtm_bidding_services

logger = logging.getLogger(__name__)


class RTMBidCaptureProcessor:
    async def execute_block_capture_job(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Process queued RTM bid-capture tasks after submit-for-approval (one or many blocks).
        """
        captures: List[Dict[str, Any]] = (payload or {}).get("captures") or []
        if not captures:
            return {"status": "success", "processed": 0, "errors": []}
        errors: List[Dict[str, Any]] = []
        processed = 0
        for i, task in enumerate(captures):
            try:
                await rtm_bidding_services.apply_capture_task(task)
                processed += 1
            except Exception as exc:
                logger.error("RTM block capture task %s failed: %s", i, exc, exc_info=True)
                errors.append({"index": i, "error": str(exc)})
        return {
            "status": "success" if not errors else "partial",
            "processed": processed,
            "errors": errors,
        }


rtm_bid_capture_processor = RTMBidCaptureProcessor()
