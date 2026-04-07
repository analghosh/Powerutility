from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional, Dict, Any
import logging
from services.processing.app.v2.services.exchange_trade_services import ExchangeTradeServices

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("", response_model=Dict[str, Any])
async def get_exchange_trade_data(
    scheduled_at: Optional[str] = Query(None, description="Date in YYYY-MM-DD format"),
    from_timeblock: int = Query(1, ge=1, le=96),
    to_timeblock: int = Query(96, ge=1, le=96),
    market: Optional[str] = Query(None, description="Market type e.g. DAM, RTM")
):
    """
    Get exchange trade serve data based on filters.
    """
    try:
        service = ExchangeTradeServices()
        result = await service.get_exchange_trade_data(
            scheduled_at=scheduled_at,
            from_timeblock=from_timeblock,
            to_timeblock=to_timeblock,
            market=market
        )
        if "error" in result:
             raise HTTPException(status_code=500, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving exchange trade data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch exchange trade data: {str(e)}")
