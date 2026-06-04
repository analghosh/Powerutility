"""
PXIL DSM Parser
Parses the specific DSM data format from PXIL
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from .base_parser import BaseParser

from libs.models.provider_models import ProviderEntity
from libs.utils.csv_date_parse import parse_ingestion_date

logger = logging.getLogger(__name__)


class PXILDsmParser(BaseParser):
    """
    Parser for PXIL DSM API data
    """

    def parse(self, content: Any, provider: Optional[ProviderEntity] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        Parse PXIL DSM data
        
        Format:
        {
            "data": [
                {
                    "delivery_date": "21-Feb-2026",
                    "time_block": "00:00-00:15",
                    "cleared_buy": 0.0,
                    "cleared_sell": 0.0,
                    "price": 0.0
                },
                ...
            ]
        }
        """
        data = content
        logger.info(f"PXILDsmParser: Received content type: {type(content)}")
        
        if isinstance(content, str):
            try:
                import json
                data = json.loads(content)
                logger.debug("PXILDsmParser: Successfully parsed JSON string")
            except Exception as e:
                logger.error(f"PXILDsmParser: Failed to parse JSON string: {e}")
                return []

        if not data or not isinstance(data, dict):
            logger.warning(f"PXILDsmParser: Data is not a dictionary. Type: {type(data)}")
            return []
            
        if "data" not in data:
            logger.warning(f"PXILDsmParser: 'data' key missing. Keys found: {list(data.keys())}")
            return []

        raw_records = data.get("data", [])
        logger.info(f"PXILDsmParser: Found {len(raw_records)} raw records")
        parsed_records = []

        for record in raw_records:
            try:
                # 1. Parse Delivery Date
                delivery_date_str = record.get("delivery_date")
                scheduled_at = parse_ingestion_date(delivery_date_str)

                # 2. Parse Time Block
                # "00:00-00:15" -> 1, "00:15-00:30" -> 2, ..., "23:45-24:00" -> 96
                time_range = record.get("time_block")
                time_block = self._parse_time_block_range(time_range)

                # 3. Map values
                parsed_record = {
                    "scheduled_at": scheduled_at,
                    "time_block": time_block,
                    "total_buy_cleared_vol": float(record.get("cleared_buy", 0.0)),
                    "total_sell_cleared_vol": float(record.get("cleared_sell", 0.0)),
                    "price": float(record.get("price", 0.0))
                }
                
                # Add metadata context if available
                metadata = provider.metadata if provider and provider.metadata else {}
                config = metadata.get("config", {})
                
                parsed_record["market_type"] = config.get("market_type", "UNKNOWN")
                parsed_record["px_name"] = config.get("px_name", "UNKNOWN")

                parsed_records.append(parsed_record)
            except Exception as e:
                logger.error(f"Error parsing PXIL DSM record: {record}. Error: {e}")
                continue

        return parsed_records

    def get_parser_type(self) -> str:
        return "pxil_dsm"

    def _parse_time_block_range(self, time_range: str) -> int:
        """
        Convert "HH:MM-HH:MM" to 1-96 index
        """
        try:
            start_time_str = time_range.split("-")[0]
            # Handle "24:00" as end of day
            if start_time_str == "24:00":
                return 96
                
            hours, minutes = map(int, start_time_str.split(":"))
            # Each hour has 4 blocks of 15 min
            # 00:00 -> (0*4) + (0/15) + 1 = 1
            # 00:15 -> (0*4) + (15/15) + 1 = 2
            # 23:45 -> (23*4) + (45/15) + 1 = 92 + 3 + 1 = 96
            return (hours * 4) + (minutes // 15) + 1
        except Exception:
            return 0
