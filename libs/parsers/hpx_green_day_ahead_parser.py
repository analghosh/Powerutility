from typing import Dict, Any, List, Optional
import logging
import json
from datetime import datetime

from libs.parsers.base_parser import BaseParser
from libs.models.provider_models import ProviderEntity
from libs.utils.csv_date_parse import parse_ingestion_date

logger = logging.getLogger(__name__)

class HPXGreenDayAheadParser(BaseParser):
    """
    Parser for HPX Green Day Ahead Market data.
    Parses JSON content from HPX API.
    """
    
    def get_parser_type(self) -> str:
        return "hpx_green_day_ahead"
        
    def _safe_float_conversion(self, val):
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    def _parse_date_str(self, date_str: str) -> str:
        return parse_ingestion_date(date_str).date().isoformat()

    def parse(self, content: str, provider: Optional[ProviderEntity] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        Parse HPX Green Day Ahead Market JSON content.
        
        Args:
            content: Raw JSON content
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

            result = []
            
            # Use metadata for fallback or config
            data_type = "unknown"
            source = "HPX"
            if provider:
                data_type = provider.data_type if hasattr(provider, 'data_type') else "unknown"
                if provider.metadata:
                    source = provider.metadata.get('source', 'HPX')

            # Iterate through dates
            for date_str, hours_data in data.items():
                if not isinstance(hours_data, dict):
                    continue
                try:
                    normalized_date = self._parse_date_str(date_str)
                except ValueError:
                    logger.warning("Invalid date key in HPX GDAM response: %s", date_str)
                    continue
                    
                # Iterate through hours
                for hour_str, blocks in hours_data.items():
                    if not isinstance(blocks, list):
                        continue
                        
                    for block in blocks:
                        try:
                            # Map fields to match IEXGreenDayAheadParser output structure
                            
                            buy_bid = self._safe_float_conversion(block.get("purchase_bid", 0))
                            
                            # Sell Bid Mapping (mw_*)
                            sell_bid = {
                                "total": self._safe_float_conversion(block.get("mw_total", 0)),
                                "solar": self._safe_float_conversion(block.get("mw_solar", 0)),
                                "non_solar": self._safe_float_conversion(block.get("mw_nsolar", 0)),
                                "hydro": self._safe_float_conversion(block.get("mw_hydro", 0)),
                            }
                            
                            # MCV Mapping (mcv_*)
                            mcv = {
                                "total": self._safe_float_conversion(block.get("mcv_total", 0)),
                                "solar": self._safe_float_conversion(block.get("mcv_solar", 0)),
                                "non_solar": self._safe_float_conversion(block.get("mcv_nsolar", 0)),
                                "hydro": self._safe_float_conversion(block.get("mcv_hydro", 0)),
                            }
                            
                            # Final Scheduled Volume Mapping (schedvol_*)
                            final_scheduled_volume = {
                                "total": self._safe_float_conversion(block.get("schedvol_total", 0)),
                                "solar": self._safe_float_conversion(block.get("schedvol_solar", 0)),
                                "non_solar": self._safe_float_conversion(block.get("schedvol_nsolar", 0)),
                                "hydro": self._safe_float_conversion(block.get("schedvol_hydro", 0)),
                            }
                            
                            mcp = self._safe_float_conversion(block.get("mcp", 0))
                            
                            block_obj = {
                                "date": normalized_date,
                                "time_block": block.get("time_block"),
                                "buy_bid": buy_bid,
                                "sell_bid": sell_bid,
                                "mcv": mcv,
                                "final_scheduled_volume": final_scheduled_volume,
                                "mcp": mcp,
                                
                                # Metadata
                                "data_type": data_type,
                                "source": source,
                                "scraped_at": datetime.now(),
                                "createdAt": datetime.now(),
                                "updatedAt": datetime.now()
                            }
                            
                            result.append(block_obj)
                            
                        except Exception as e:
                            logger.warn(f"Error parsing block: {e}")
                            continue

            logger.info("HPXGreenDayAheadParser: Extracted %d rows", len(result))
            return result

        except Exception as e:
            logger.error("Error in HPX Green Day Ahead parser: %s", str(e))
            return []
