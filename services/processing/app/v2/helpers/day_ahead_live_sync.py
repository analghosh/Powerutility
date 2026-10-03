"""
Proxy for the GDAM / HP-DAM "Get Exn. Status" sync.

The scheduler owns the IEX calls; processing forwards the request and hands the
scheduler's verdict back unchanged: 400 for a bad date, 502 for a failed IEX fetch.
"""

import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException, status

from services.processing.app.core.config import settings

logger = logging.getLogger(__name__)


async def proxy_day_ahead_live_sync(market: str, bid_status_date: Optional[str]) -> Dict[str, Any]:
    """Run the scheduler's live sync for `market` ("gdam" / "hpdam"), optionally for a YYYY-MM-DD date."""
    label = market.upper()
    endpoint = f"{settings.SCHEDULER_SERVICE_URL}/api/v1/scheduler/bid-trade-live-sync/trigger-{market}"
    params = {"delivery_date": bid_status_date} if bid_status_date else None

    try:
        logger.info(f"[{label} Live Sync] Calling scheduler service at {endpoint} for {bid_status_date or 'current date'}")
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(endpoint, params=params)
    except Exception as e:
        logger.error(f"[{label} Live Sync] Error calling scheduler service: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger {label} live sync: {str(e)}",
        )

    if response.status_code != 200:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        logger.error(f"[{label} Live Sync] Scheduler service returned error: {response.status_code} - {detail}")
        raise HTTPException(status_code=response.status_code, detail=detail)

    result = response.json()
    logger.info(f"[{label} Live Sync] Received response from scheduler: status={result.get('status')}")
    return result
