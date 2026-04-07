from typing import Dict, Any, List, Optional
import logging
from bs4 import BeautifulSoup
from datetime import datetime

from libs.parsers.base_parser import BaseParser
from libs.models.provider_models import ProviderEntity

logger = logging.getLogger(__name__)

class IEXGreenDayAheadParser(BaseParser):
    """
    Parser for IEX Green Day Ahead Market data.
    Handles HTML content with complex rowspans.
    """
    
    def get_parser_type(self) -> str:
        return "iex_green_day_ahead"
        
    def _safe_float_conversion(self, val):
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    def parse(self, content: str, provider: Optional[ProviderEntity] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        Parse IEX Green Day Ahead Market HTML content.
        
        Args:
            content: Raw HTML content
            provider: Provider entity for context
            **kwargs: Additional arguments (can accept 'soup' if already parsed)
            
        Returns:
            List[Dict[str, Any]]: Parsed data records
        """
        try:
            # Allow passing pre-parsed soup to avoid re-parsing if available
            soup = kwargs.get('soup')
            if not soup:
                soup = BeautifulSoup(content, "html.parser")

            tables = soup.find_all("table")
            target_table = None
            has_session_id = False
            header_row_index = -1

            for tbl in tables:
                rows = tbl.find_all("tr")
                # Check first few rows for header signature
                for idx, row in enumerate(rows[:5]):
                    headers = [th.get_text(strip=True) for th in row.find_all(["th", "td"])]
                    
                    # Debug log to see what we are finding (can be removed later or kept for deep debug)
                    # logger.debug(f"Scanning table row {idx}: {headers}")

                    if len(headers) >= 3:
                         if headers[:3] == ["Date", "Hour", "Time Block"]:
                             target_table = tbl
                             has_session_id = False
                             header_row_index = idx
                             break
                         elif len(headers) >= 4 and headers[:4] == ["Date", "Hour", "Session ID", "Time Block"]:
                             target_table = tbl
                             has_session_id = True
                             header_row_index = idx
                             break
                
                if target_table:
                    break

            if target_table is None:
                logger.warning("Could not find IEX Market Snapshot table with expected headers")
                return []

            rows = target_table.find_all("tr")
            result = []

            
            # Use metadata for fallback or config
            data_type = "unknown"
            source = "unknown"
            if provider:
                data_type = provider.data_type if hasattr(provider, 'data_type') else "unknown"
                if provider.metadata:
                    source = provider.metadata.get('source', 'unknown')

            current_date = None
            current_hour = None
            
            # Start processing from the row after the detected header
            # We skip the detected header row. The logical flow inside the loop 
            # (float conversion, length checks) should handle any subsequent sub-header rows safely.
            start_index = header_row_index + 1
            if start_index >= len(rows):
                 logger.warning("Table has no data rows after header")
                 return []
                 
            for tr in rows[start_index:]:
                tds = tr.find_all("td")
                if not tds:
                    continue

                texts = [td.get_text(strip=True) for td in tds]
                
                # Check if this row is likely a sub-header (contains text 'Total', 'Solar', etc.)
                # and skip it to avoid treating it as data with 0.0 values.
                # Heuristic: Check if first few cols are not numeric and resemble header names
                if any(x in texts[0] for x in ["Total", "Solar", "Non-Solar", "Hydro", "Purchase", "Sell"]):
                     continue # Skip sub-headers

                # DEBUG: Log the first few rows to understand structure
                if len(result) < 3:
                     msg = f"DEBUG: Processing row {len(result)+1}. Len: {len(texts)}. Texts: {texts}"
                     logger.info(msg)
                     print(msg, flush=True) # Force print to stdout

                # Logic to identify row type based on column count
                # Full row (Date change):
                #   No Session ID: 17 cols
                #   With Session ID: 18 cols
                
                # Hour change (same Date):
                #   No Session ID: 
                #       If Hour is rowspanned: ? (Usually Hour and Date are rowspanned together? No, Hour changes every 4 blocks)
                #       If Hour is NOT rowspanned: ?
                
                # Let's infer based on relative length
                # Base columns (Data) = 14 columns (Buy + 4 Sell + 4 MCV + 4 Final + 1 MCP)
                # Prefix columns:
                #   Full: Date, Hour, [Session], TimeBlock -> 3 or 4
                #   Hour Change: Hour, [Session], TimeBlock -> 2 or 3
                #   Block Change: TimeBlock -> 1
                
                num_data_cols = 14
                
                if has_session_id: 
                    # Expect 18 for full, 16 ?? or block only
                    # Actually, usually Date is rowspan=96, Hour is rowspan=4, Session ID is likely rowspan=4 (aligned with Hour)
                    
                    if len(texts) == 18: # Date, Hour, Session, TimeBlock, Data...
                        current_date = texts[0]
                        current_hour = texts[1]
                        # session = texts[2] # skip
                        time_block = texts[3]
                        nums = list(map(self._safe_float_conversion, texts[4:]))
                        
                    elif len(texts) == 17: # Hour, Session, TimeBlock, Data... (Same Date, New Hour)
                        # Wait, Date is rowspanned.
                        current_hour = texts[0]
                        # session = texts[1]
                        time_block = texts[2]
                        nums = list(map(self._safe_float_conversion, texts[3:]))
                        
                    elif len(texts) == 16: # Session, TimeBlock ?? Or just TimeBlock?
                         # If Hour is rowspanned, Session probably is too?
                         # If only TimeBlock is new: 1 + 14 = 15?
                         # Let's look at the structure.
                         # If len is 15: TimeBlock + Data
                         # If len is 16: ??
                         
                         # Let's handle the "Time Block" only case
                         if ":" in texts[0]: # Heuristic for Time Block "00:00 - 00:15"
                             time_block = texts[0]
                             nums = list(map(self._safe_float_conversion, texts[1:]))
                         else:
                             # Fallback / Unknown structure
                             # Try assuming it matches the pattern:
                             # Indices if 16: Hour, TimeBlock? (No session?)
                             # Let's assume consistent rowspan behavior.
                             
                             # If len 16 and has_session_id, maybe Date is implicit, Hour/Session implicit?
                             # Unlikely to have 16.
                             # Likely pattern:
                             # 1. New Date: 18 cols
                             # 2. New Hour: 17 cols (Date spanned, Hour+Session+Block new)
                             # 3. Same Hour: 15 cols (Date+Hour+Session spanned, Block new)
                             
                             logger.warning(f"Unexpected column count {len(texts)} with SessionID=True")
                             continue
                             
                    elif len(texts) == 15: # TimeBlock + Data
                        time_block = texts[0]
                        nums = list(map(self._safe_float_conversion, texts[1:]))
                        
                    else:
                        continue
                        
                else: # Old Logic (No Session ID)
                    if len(texts) == 17:
                        current_date = texts[0]
                        current_hour = texts[1]
                        time_block = texts[2]
                        nums = list(map(self._safe_float_conversion, texts[3:]))
                    elif len(texts) == 16: # Hour + TimeBlock (Date spanned)
                        current_hour = texts[0]
                        time_block = texts[1]
                        nums = list(map(self._safe_float_conversion, texts[2:]))
                    elif len(texts) == 15: # TimeBlock only (Date + Hour spanned)
                         time_block = texts[0]
                         nums = list(map(self._safe_float_conversion, texts[1:]))
                    elif ":" in texts[0]: # Fallback for just TimeBlock
                        time_block = texts[0]
                        nums = list(map(self._safe_float_conversion, texts[1:]))
                    else:
                        continue

                # Validation of extracted numbers
                if len(nums) < 14:
                    continue
                    
                # Ensure we have a date (critical for validation)
                if not current_date:
                    logger.warning("Skipping row: Missing Date. Row text: %s", texts[:3])
                    continue

                # Normalize time_block format (e.g., "00:00 - 00:15" -> "00:00-00:15")
                if time_block:
                    time_block = time_block.replace(" ", "")

                # Map directly to "serve_field" keys as requested
                block_obj = {
                    "date": current_date,
                    "time_block": time_block,
                    "buy_bid": nums[0],
                    "sell_bid": {
                        "total": nums[1],
                        "solar": nums[2],
                        "non_solar": nums[3],
                        "hydro": nums[4],
                    },
                    "mcv": {
                        "total": nums[5],
                        "solar": nums[6],
                        "non_solar": nums[7],
                        "hydro": nums[8],
                    },
                    "final_scheduled_volume": {
                        "total": nums[9],
                        "solar": nums[10],
                        "non_solar": nums[11],
                        "hydro": nums[12],
                    },
                    "mcp": nums[13],
                    
                    # Metadata
                    "data_type": data_type,
                    "source": source,
                    "scraped_at": datetime.now(),
                    "createdAt": datetime.now(),
                    "updatedAt": datetime.now()
                }

                result.append(block_obj)

            logger.info("IEXGreenDayAheadParser: Extracted %d rows", len(result))
            return result

        except Exception as e:
            logger.error("Error in IEX Green Day Ahead parser: %s", str(e))
            return []
