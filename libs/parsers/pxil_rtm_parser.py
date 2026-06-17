from typing import Dict, Any, List, Optional
import logging
import json
from datetime import datetime

from libs.parsers.base_parser import BaseParser
from libs.models.provider_models import ProviderEntity
from libs.utils.csv_date_parse import parse_ingestion_date

logger = logging.getLogger(__name__)

class PXILRTMParser(BaseParser):
    """
    Parser for PXIL Real-Time Market (RTM) data.
    Parses JSON content from PXIL API.
    """
    
    def get_parser_type(self) -> str:
        return "pxil_rtm"
        
    def _safe_float_conversion(self, val):
        if val == "-" or val is None:
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    def parse(self, content: str, provider: Optional[ProviderEntity] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        Parse PXIL RTM Market JSON content.
        
        Args:
            content: Raw JSON content
            provider: Provider entity for context
            **kwargs: Additional arguments
            
        Returns:
            List[Dict[str, Any]]: Parsed data records
        """
        try:
            try:
                # Handle potential BOM or whitespace
                content = content.strip()
                if content.startswith('\ufeff'):
                    content = content[1:]
                
                data_json = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error("Failed to parse JSON content: %s", str(e))
                return []

            data_list = data_json.get("data", [])
            if not data_list:
                logger.warning("No 'data' field found in PXIL response")
                return []

            result = []
            
            # Use metadata for fallback or config
            data_type = "exchange"
            source = "PXIL"
            market_type = "RTM"
            
            if provider:
                data_type = provider.data_type if hasattr(provider, 'data_type') else "exchange"
                if provider.metadata:
                    source = provider.metadata.get('source', 'PXIL')
                    if provider.metadata.get('config'):
                        market_type = provider.metadata.get('config').get('market_type', 'RTM')

            current_time = datetime.now()

            for row in data_list:
                try:
                    # Date format: "15-02-2026"
                    date_str = row.get("delivery")
                    time_block_str = row.get("time_block")
                    
                    if not date_str:
                         continue

                    # Parse Date
                    try:
                        scheduled_date = parse_ingestion_date(date_str)
                    except ValueError:
                         logger.warning(f"Invalid date format: {date_str}")
                         continue
                        
                    # Calculate block number if needed, or just use string
                    # But traditionally we might need integer block number for some systems.
                    # BaseParser/Models usually prefer integer time_block if possible, but schema in provider_seeder might allow string?
                    # Checks: provider_seeder hpx used integer. 
                    # Let's try to convert to integer block number from "00:00-00:15"
                    block_num = 0
                    if time_block_str:
                        try:
                            start_time_str = time_block_str.split("-")[0]
                            h, m = map(int, start_time_str.split(":"))
                            block_num = (h * 4) + (m // 15) + 1
                        except (ValueError, AttributeError, IndexError):
                            block_num = 0 # Fallback
                    
                    # Values
                    buy_bid = self._safe_float_conversion(row.get("purchase_bid"))
                    sell_bid = self._safe_float_conversion(row.get("sell_bid"))
                    mcv = self._safe_float_conversion(row.get("mcv"))
                    final_scheduled_volume = self._safe_float_conversion(row.get("final_mcv")) # Mapping final_mcv -> final_scheduled_volume
                    mcp = self._safe_float_conversion(row.get("mcp"))
                    
                    # Logic: "mcv is also acv" usually implies cleared volume
                    # But here we have mcv and final_mcv. 
                    # Let's map typical fields. 
                    
                    record = {
                        # Schema fields (for validation)
                        "delivery": date_str,
                        "time_block": time_block_str,
                        "purchase_bid": buy_bid,
                        "sell_bid": sell_bid,
                        "mcv": mcv,
                        "final_mcv": final_scheduled_volume,
                        "mcp": mcp,
                        
                        # Computed/Mapped fields (for downstream processing)
                        "scheduled_at": scheduled_date.replace(hour=0, minute=0, second=0, microsecond=0),
                        "block": block_num,        # Renaming to 'block' to match standard if needed, or keep 'time_block' as int? 
                                                  # Provider seeder often uses 'time_block' as string in schema but int in logic. 
                                                  # Let's keep 'time_block' as per Schema (string) and use 'block' or 'time_block_num' for int?
                                                  # Actually, let's keep 'time_block' as string (as per schema) and use 'block' for the integer index if that's what's used.
                                                  # But wait, existing code had "time_block": block_num. 
                                                  # Schema says field "time_block" is string. 
                                                  # If I set "time_block": time_block_str, validation passes.
                                                  # But downstream might expect integer "time_block".
                                                  # Let's provide both: "time_block" (string match schema), and "block" (integer).
                        
                        "block_num": block_num, # Explicit integer block
                        
                        "date": date_str, 
                        "time_block_str": time_block_str,
                        
                        "buy_bid": buy_bid,
                        "final_scheduled_volume": final_scheduled_volume,
                        
                        "acv": mcv, 
                        "acp": mcp,
                        
                        # Metadata
                        "data_type": data_type,
                        "px_name": source,
                        "market_type": market_type,
                        "source": source,
                        "inserted_at": current_time,
                        "updated_at": current_time,
                        "created_at": current_time
                    }
                    
                    result.append(record)
                    
                except Exception as e:
                    logger.warning(f"Error parsing row: {e}")
                    continue

            logger.info("PXILRTMParser: Extracted %d rows", len(result))
            return result

        except Exception as e:
            logger.error("Error in PXIL RTM parser: %s", str(e))
            return []
