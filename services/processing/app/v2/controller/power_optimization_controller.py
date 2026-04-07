"""
Power Optimization Controller
Handles requests for the unified forecasting API for power optimization.
"""
import logging
from typing import Dict, Any, List, Optional
from fastapi import HTTPException, status

from datetime import datetime, timedelta
from ..services.power_optimization_service import PowerOptimizationService

logger = logging.getLogger(__name__)

class PowerOptimizationController:
    def __init__(self):
        self.service = PowerOptimizationService()

    async def get_power_optimization_input(
        self,
        from_date: str,
        to_date: str,
        user_id: str,
        username: str,
        filter_type: str = "optimization",
        exchange: Optional[List[str]] = None,
        market_type: Optional[List[str]] = None,
        forecast_market: Optional[str] = None,
        demand_type: str = "IDF"
    ) -> Dict[str, Any]:
        """
        Handle request for power optimization input data range.
        
        Args:
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)
            user_id: ID of the user for auditing
            username: Name of the user for auditing
            filter_type: Filter type - "forecast" or "optimization"
            demand_type: "IDF" or "DAF"
        """
        try:
            logger.info(f"User {username} ({user_id}) requested power optimization input range: {from_date} to {to_date} with filter={filter_type}")
            
            # ... (validation logic remains same) ...
            
            # Validate filter type
            if filter_type not in ["forecast", "optimization"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="filter must be either 'forecast' or 'optimization'"
                )
                       # Validate forecast_market if provided
            if forecast_market:
                m_up = forecast_market.upper()
                if m_up not in {"DAM", "RTM"}:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="forecast_market must be either 'DAM' or 'RTM'")
                forecast_market = m_up

             # Validate exchange entries if provided
            allowed_exchange = {"IEX", "PXIL", "HPX"}
            if exchange:
                ex_up = [e.upper() for e in exchange]
                bad = [e for e in ex_up if e not in allowed_exchange]
                if bad:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid exchange values: {bad}")
                exchange = ex_up

             # Validate market_type entries if provided
            allowed_market = {"DAM", "RTM", "GDAM", "HPDAM"}
            if market_type:
                mt_up = [m.upper() for m in market_type]
                bad = [m for m in mt_up if m not in allowed_market]
                if bad:
                     raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid market_type values: {bad}")
                market_type = mt_up

                
            # Generate list of dates between from_date and to_date
            start = datetime.strptime(from_date, "%Y-%m-%d")
            end = datetime.strptime(to_date, "%Y-%m-%d")
            
            if start > end:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="from_date cannot be after to_date"
                )
            
            date_list = []
            current = start
            while current <= end:
                date_list.append(current.strftime("%Y-%m-%d"))
                current += timedelta(days=1)
                
            if len(date_list) > 31: # Safety limit
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Date range cannot exceed 31 days"
                )
            
            # TODO: Add more robust date format validation if needed
            
            data = await self.service.get_power_optimization_input(
                date_list, 
                filter_type,
                exchange=exchange,
                market_type=market_type,
                forecast_market=forecast_market,
                demand_type=demand_type
            )
            
            logger.info(f"Successfully generated power optimization input for {len(date_list)} dates with filter={filter_type}.")
            return data
            
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error generating power optimization input: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate power optimization input data: {str(e)}"
            )
