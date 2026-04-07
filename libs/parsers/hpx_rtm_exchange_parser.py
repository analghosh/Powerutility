from typing import Dict, Any, List, Optional
import logging
from datetime import datetime
import json

from libs.parsers.base_parser import BaseParser
from libs.models.provider_models import ProviderEntity

logger = logging.getLogger(__name__)

class HPXRTMExchangeParser(BaseParser):
    """
    Parser for HPX RTM Exchange data.
    """
    
    def get_parser_type(self) -> str:
        return "hpx_rtm_exchange"

    def _safe_float_conversion(self, val):
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    def parse(self, content: str, provider: Optional[ProviderEntity] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        Parse HPX RTM Exchange data API response.
        
        Args:
            content: Raw JSON content string
            provider: Provider entity for context
            **kwargs: Additional arguments
            
        Returns:
            List[Dict[str, Any]]: Parsed data records
        """
        try:
            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error("Failed to parse JSON content: %s", str(e))
                return []

            # RTM Format: { "DD/MM/YYYY": { "1": [ ... ], "2": [ ... ] } }
            raw_records = []
            
            # Iterate over dates
            for date_key, hour_data in data.items():
                if isinstance(hour_data, dict):
                    for hour_key, records in hour_data.items():
                        if isinstance(records, list):
                            raw_records.extend(records)

            if not raw_records:
                logger.warning("No data found in HPX RTM response")
                return []

            result = []
            
            # Use metadata for fallback or config
            data_type = "exchange"
            source = "HPX" 
            market_type = "RTM" 

            if provider:
                data_type = provider.data_type if hasattr(provider, 'data_type') else "exchange"
                if provider.metadata:
                    source = provider.metadata.get('source', source)
                    if provider.metadata.get('config'):
                         market_type = provider.metadata.get('config').get('market_type', market_type)
            
            current_time = datetime.now()

            for row in raw_records:
                # Extract fields
                date_str = row.get("date") # "15/02/2026"
                time_block_str = row.get("time_block") # "00:00-00:15"
                
                # Parse Date
                try:
                    # Input date format: DD/MM/YYYY
                    scheduled_date = datetime.strptime(date_str, "%d/%m/%Y")
                except (ValueError, TypeError):
                    logger.warning("Invalid date format: %s", date_str)
                    continue

                # Parse Time Block to number
                # Input format: "00:00-00:15"
                try:
                    start_time_str = time_block_str.split("-")[0]
                    h, m = map(int, start_time_str.split(":"))
                    # Calculate block number (1-96)
                    block_num = (h * 4) + (m // 15) + 1
                except (ValueError, AttributeError, IndexError):
                    logger.warning("Invalid time block format: %s", time_block_str)
                    continue

                # Validation: 96 blocks
                if block_num > 96:
                     logger.warning("Block number %d exceeds 96 for time %s", block_num, time_block_str)
                     continue

                # Calculate scheduled_at (Timestamp for the block start)
                scheduled_at = scheduled_date.replace(hour=0, minute=0, second=0, microsecond=0)

                # Convert values
                buy_bid = self._safe_float_conversion(row.get("purchase bid(mw)"))
                sell_bid = self._safe_float_conversion(row.get("sell bid(mw)"))
                mcv = self._safe_float_conversion(row.get("mcv(mw)"))
                final_scheduled_volume = self._safe_float_conversion(row.get("final scheduled volume (mw)"))
                mcp = self._safe_float_conversion(row.get("mcp (rs/mwh)"))
                # Fallback if mcp is 0 (possible key change in API)
                if mcp == 0:
                    mcp = self._safe_float_conversion(row.get("price(rs/mwh)"))
                
                cleared_volume = self._safe_float_conversion(row.get("cleared volume(mw)"))
                
                # Logic: "mcv is also acv"
                acv = cleared_volume if cleared_volume > 0 else mcv
                acp = mcp 

                record = {
                    "scheduled_at": scheduled_at,
                    "Date": scheduled_at, # Backward compatibility for ExchangeProcessor
                    "time_block": block_num,
                    "buy_bid": buy_bid,
                    "sell_bid": sell_bid,
                    "mcv": mcv,
                    "final_scheduled_volume": final_scheduled_volume,
                    "mcp": mcp,
                    "acv": acv,
                    "acp": acp,
                    
                    # Metadata fields for processor to use
                    "data_type": data_type,
                    "px_name": source, 
                    "market_type": market_type,
                    "source": source,
                    "inserted_at": current_time,
                    "updated_at": current_time,
                    "created_at": current_time,
                    "timestamp": current_time,
                    
                    # Store original values if needed for debugging
                    "raw_date": date_str,
                    "raw_time_block": time_block_str
                }
                
                result.append(record)

            logger.info("HPXRTMExchangeParser: Extracted %d records", len(result))
            return result

        except Exception as e:
            logger.error("Error in HPX RTM Exchange parser: %s", str(e))
            return []
