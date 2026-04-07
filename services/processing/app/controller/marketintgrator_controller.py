import logging
from typing import Dict, Any, Optional
from fastapi import HTTPException, status, Request
from ..services.serveexchangetable_services import serveexchangetable_services
from ..services.servepxbidtrade_services import servepxbidtrade_services

logger = logging.getLogger(__name__)


class MarketintgratorController:
    """
    Controller for handling DsmoverviewController-related business logic and request processing.
    """

    def __init__(self):
        self.serveexchangetable_services = serveexchangetable_services
        self.servepxbidtrade_services = servepxbidtrade_services

    def _extract_request_info(self, request: Request) -> Dict[str, Optional[str]]:
        """
        Extract useful information from the request for auditing/logging.

        Args:
            request (Request): The FastAPI request object.

        Returns:
            Dict[str, Optional[str]]: Extracted request metadata.
        """
        return {
            "ip_address": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "method": request.method,
            "path": request.url.path,
            "timestamp": request.headers.get("x-request-timestamp")
        }

    async def exchange_garph(self, schedule_date) -> Dict[str, Any]:
        try:
            data = await self.serveexchangetable_services.exchange_by_date(schedule_date)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in MarketintgratorController.exchange_garph")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process exchange_garph data request."
            )
    
    async def market_overview_garph(self, schedule_date) -> Dict[str, Any]:
        try:
            data = await self.servepxbidtrade_services.exchange_by_date(schedule_date)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in MarketintgratorController.exchange_garph")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process exchange_garph data request."
            )
    
    async def exchange_table(self, schedule_date, time_block) -> Dict[str, Any]:
        try:
            data = await self.serveexchangetable_services.exchange_by_date_and_time_block(schedule_date, time_block)
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in MarketintgratorController.exchange_garph")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process exchange_garph data request."
            )
        
    async def exchange_bidding_selected_summary_table(self, schedule_date, time_block) -> Dict[str, Any]:
        try:
            data = await self.servepxbidtrade_services.exchange_by_date_and_time_block(schedule_date, time_block)
            logger.warning(f"[DashboardService] =========++++++++.: {data}")
            return {"data": data}
        except HTTPException:
            raise 
        except Exception:
            logger.exception("Error occurred in MarketintgratorController.exchange_garph")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to process exchange_garph data request."
            )


# Global controller instance
marketintgrator_controller = MarketintgratorController()
