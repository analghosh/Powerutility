"""
DAM Block Bid Controller
Handles all DAM block bid operations: submit for approval, approve/reject,
submit to exchange (with cancel-then-resubmit), withdraw, cancel on exchange.
"""
import logging
from typing import Dict, Any, List

from fastapi import HTTPException, status

from ..services.block_bid_services import block_bid_service

logger = logging.getLogger(__name__)


class DAMBlockBidController:

    def __init__(self):
        self.block_bid_service = block_bid_service

    async def submit_for_approval(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert DAM block bid records with status=pending for approval."""
        try:
            post_data["market_type"] = "DAM"
            return await self.block_bid_service.insert_block_bid(post_data)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error submitting DAM block bid for approval")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to submit DAM block bid for approval.",
            )

    async def approval_request_list(self, schedule_date: str) -> Dict[str, Any]:
        """Return pending/approved DAM block bids for a given date."""
        try:
            data = await self.block_bid_service.get_block_bid_submitted_for_approval_list(schedule_date)
            # Filter to DAM only
            dam_data = [d for d in data if d.get("market_type", "").upper() == "DAM"]
            return {"data": dam_data}
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error fetching DAM block bid approval list")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch DAM block bid approval list.",
            )

    async def update_status(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
        """Approve or reject DAM block bids."""
        try:
            return await self.block_bid_service.update_block_bid_status(post_data)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error updating DAM block bid status")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update DAM block bid status.",
            )

    async def submit_to_exchange(
        self,
        biding_date: str,
        submitted_by: str = "",
        correlation_id: str = "",
        market: str = "DAM",
    ) -> Dict[str, Any]:
        """
        Submit approved DAM/GDAM block bids to IEX exchange
        (cancel-then-resubmit if a duplicate period already exists).
        """
        try:
            return await self.block_bid_service.submit_dam_to_exchange(
                biding_date, submitted_by, correlation_id, market=market
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception(f"Error submitting {market} block bids to exchange")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to submit {market} block bids to exchange.",
            )

    async def mark_withdrawal(self, post_data: Dict[str, Any]) -> Dict[str, Any]:
        """Mark a DAM block bid as Cancelled locally (no exchange call)."""
        bid_id = post_data.get("bid_id", "")
        remark = post_data.get("remark", "")
        cancelled_by = post_data.get("username", "")
        if not bid_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bid_id is required")
        try:
            return await self.block_bid_service.mark_bid_for_withdrawal(bid_id, remark, cancelled_by)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error marking DAM block bid for withdrawal")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to mark DAM block bid for withdrawal.",
            )

    async def cancel_on_exchange(
        self, post_data: Dict[str, Any], correlation_id: str = "", market: str = "DAM"
    ) -> Dict[str, Any]:
        """Cancel DAM/GDAM block bids on IEX exchange for given order_ids."""
        order_ids    = post_data.get("order_ids", [])
        biding_date  = post_data.get("schedule_date", "")
        cancelled_by = post_data.get("username", "")
        if not order_ids or not biding_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="order_ids and schedule_date are required")
        try:
            return await self.block_bid_service.cancel_dam_block_bid_on_exchange(
                order_ids=[int(oid) for oid in order_ids],
                biding_date=biding_date,
                cancelled_by=cancelled_by,
                correlation_id=correlation_id,
                market=market,
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error cancelling DAM block bids on exchange")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to cancel DAM block bids on exchange.",
            )


dam_block_bid_controller = DAMBlockBidController()
