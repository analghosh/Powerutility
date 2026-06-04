"""
Optimization Parser
Parses optimization JSON data for demand forecasts and price forecasts.
Handles: rtm_forecasted_price, and exchange data (DAM, GDAM, HPDAM).
Outputs data for demand_raw_data and limit_values collections.
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from libs.parsers.base_parser import BaseParser
from libs.models.provider_models import ProviderEntity
from libs.utils.csv_date_parse import parse_ingestion_date

logger = logging.getLogger(__name__)


class OptimizationParser(BaseParser):
    """
    Parser for Optimization JSON data.
    Extracts demand forecast data and various price forecasts.
    
    Supported price types:
        - rtm_forecasted_price → id: "rtm_forecasted_price", market: "RTM"
        - exchange.px_data.DAM → id: "dam_forecasted_price", market: "DAM"
        - exchange.px_data.GDAM → id: "gdam_forecasted_price", market: "GDAM"
        - exchange.px_data.HPDAM → id: "hpdam_forecasted_price", market: "HPDAM"
    
    Output:
        - demand_data: Goes to demand_raw_data (normal raw data flow)
        - limit_values_data: Goes directly to limit_values (bypasses approval)
    """
    
    # Flag to indicate limit_values should bypass approval flow
    skip_approval_flow_for_limit_values = True
    
    # Mapping of exchange market types: (limit_id, price_field_name)
    EXCHANGE_MARKET_MAPPING = {
        "DAM": ("DAM_forecasted_price", "DAM_forecasted_price"),
        "GDAM": ("GDAM_forecasted_price", "GDAM_forecasted_price"),
        "HPDAM": ("HPDAM_forecasted_price", "HPDAM_forecasted_price")
    }
    
    def get_parser_type(self) -> str:
        """Get the parser type identifier"""
        return "optimization"

    def parse(self, content: str, provider: Optional[ProviderEntity] = None, **kwargs) -> Dict[str, List[Dict[str, Any]]]:
        """
        Parse Optimization JSON content into structured data.

        Args:
            content: Raw JSON content string
            provider: Provider entity (optional)
            **kwargs: Additional parsing parameters

        Returns:
            Dict with two keys:
                - demand_data: List of demand forecast records for demand_raw_data
                - limit_values_data: List of limit values records for limit_values collection
        """
        try:
            # Parse JSON content
            if isinstance(content, str):
                data = json.loads(content)
            else:
                data = content

            # Get the data array
            data_list = data.get("data", [])
            
            if not data_list:
                logger.warning("No 'data' array found in optimization content")
                return {"demand_data": [], "limit_values_data": []}

            demand_records = []
            limit_values_records = []
            current_time = datetime.now()

            for entry in data_list:
                date_str = entry.get("date")
                if not date_str:
                    logger.warning("Skipping entry without date")
                    continue

                # Parse date to datetime
                try:
                    scheduled_at = parse_ingestion_date(date_str)
                except ValueError:
                    logger.warning("Could not parse date: %s", date_str)
                    continue

                # =========================================
                # DEMAND DATA EXTRACTION
                # =========================================
                demand = entry.get("demand", {})
                if demand:
                    market_type = demand.get("market", "")
                    demand_data_list = demand.get("data", [])

                    for demand_item in demand_data_list:
                        time_block = demand_item.get("time_block")
                        demand_forecast_mw = demand_item.get("demand_forecast_mw")
                        forecasted_frequency = demand_item.get("forecasted_frequency")

                        if time_block is None:
                            continue

                        demand_record = {
                            "scheduled_at": scheduled_at,
                            "type": market_type,
                            "time_block": int(time_block),
                            "forecast": float(demand_forecast_mw) if demand_forecast_mw is not None else None,
                            "frequency": float(forecasted_frequency) if forecasted_frequency is not None else None,
                            "created_at": current_time,
                            "updated_at": current_time,
                            "source": "optimization"
                        }
                        demand_records.append(demand_record)

                # =========================================
                # RTM FORECASTED PRICE DATA EXTRACTION
                # =========================================
                rtm_prices = entry.get("rtm_forecasted_price", [])
                if rtm_prices:
                    for price_item in rtm_prices:
                        limit_record = self._create_limit_record(
                            scheduled_at=scheduled_at,
                            time_block=price_item.get("time_block"),
                            price=price_item.get("price_inr_mwh"),
                            limit_id="RTM_forecasted_price",
                            market="RTM",
                            current_time=current_time
                        )
                        if limit_record:
                            limit_values_records.append(limit_record)

                # =========================================
                # EXCHANGE DATA EXTRACTION (DAM, GDAM, HPDAM)
                # =========================================
                exchange_list = entry.get("exchange", [])
                for exchange_item in exchange_list:
                    px_name = exchange_item.get("px_name", "")
                    px_data = exchange_item.get("px_data", {})
                    
                    # Process each market type (DAM, GDAM, HPDAM)
                    for market_type, (limit_id, price_field) in self.EXCHANGE_MARKET_MAPPING.items():
                        market_prices = px_data.get(market_type, [])
                        
                        for price_item in market_prices:
                            limit_record = self._create_limit_record(
                                scheduled_at=scheduled_at,
                                time_block=price_item.get("time_block"),
                                price=price_item.get(price_field),
                                limit_id=limit_id,
                                market=market_type,
                                current_time=current_time,
                                px_name=px_name
                            )
                            if limit_record:
                                limit_values_records.append(limit_record)

            logger.info(
                "OptimizationParser: Extracted %d demand records and %d limit values records",
                len(demand_records),
                len(limit_values_records)
            )

            return {
                "demand_data": demand_records,
                "limit_values_data": limit_values_records
            }

        except json.JSONDecodeError as e:
            logger.error("Error decoding JSON in Optimization parser: %s", e)
            raise ValueError(f"Invalid JSON content: {str(e)}") from e
        except Exception as e:
            logger.error("Error parsing Optimization content: %s", e, exc_info=True)
            raise ValueError(f"Optimization parsing failed: {str(e)}") from e

    def _create_limit_record(
        self,
        scheduled_at: datetime,
        time_block: Optional[int],
        price: Optional[float],
        limit_id: str,
        market: str,
        current_time: datetime,
        px_name: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        Create a limit values record.
        
        Args:
            scheduled_at: Scheduled date
            time_block: Time block number
            price: Price value
            limit_id: Limit identifier (e.g., "rtm_forecasted_price", "dam_forecasted_price")
            market: Market type (RTM, DAM, GDAM, HPDAM)
            current_time: Current timestamp
            px_name: Power exchange name (e.g., "IEX")
            
        Returns:
            Dict: Limit record or None if time_block is missing
        """
        if time_block is None:
            return None
            
        return {
            "scheduled_at": scheduled_at,
            "id": limit_id,
            "block": int(time_block),
            "values": float(price) if price is not None else None,
            "market": market,
            "area": "Puducherry",
            "category": "Limits",
            "unit": "INR/MWh",
            "px_name": px_name if px_name else None,
            "inserted_at": current_time,
            "created_by": "optimization_parser",
            "updated_by": "optimization_parser",
            "updated_at": current_time,
            "source": "optimization_system"
        }

    def validate_content(self, content: str) -> bool:
        """
        Validate if content looks like Optimization JSON
        """
        try:
            if isinstance(content, str):
                data = json.loads(content)
            else:
                data = content
            
            # Check for key signatures
            if "data" in data and isinstance(data["data"], list):
                if len(data["data"]) > 0:
                    first_entry = data["data"][0]
                    # Check for expected fields
                    if "date" in first_entry:
                        # Must have at least one of these
                        if any(key in first_entry for key in ["demand", "rtm_forecasted_price", "exchange"]):
                            return True
            return False
        except Exception:
            return False


# Alias for backwards compatibility
RTMOptimizationParser = OptimizationParser
