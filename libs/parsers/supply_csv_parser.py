"""
Supply CSV Parser
Handles wide format supply data where plant IDs are columns
"""

import csv
import io
import logging
from typing import Dict, Any, List, Optional

from libs.parsers.base_parser import BaseParser
from libs.utils.csv_date_parse import parse_csv_scheduled_at
from libs.models.provider_models import ProviderEntity

logger = logging.getLogger(__name__)


class SupplyCSVParser(BaseParser):
    """
    Special CSV parser for supply data.
    Flattens wide format (plant IDs as columns) into individual raw records.
    """

    def get_parser_type(self) -> str:
        """Get the parser type identifier"""
        return "supply_csv"

    def parse(self, content: str, provider: Optional[ProviderEntity] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        Parse supply CSV content into a flattened list of dictionaries

        Args:
            content: Raw CSV content string
            provider: Provider entity (optional)
            **kwargs: Additional parsing parameters

        Returns:
            List[Dict[str, Any]]: Flattened supply data records
        """
        try:
            # Remove BOM if present
            if content.startswith('\ufeff') or content.startswith('\ufffe'):
                content = content[1:]

            delimiter = kwargs.get('delimiter', ',')
            
            # Read CSV content
            csv_file = io.StringIO(content)
            reader = csv.DictReader(csv_file, delimiter=delimiter)
            
            raw_data = []
            
            # Identify special columns
            date_cols = ['date', 'scheduled_at', 'scheduledAt', 'Date']
            time_block_cols = ['time_block', 'timeBlock', 'time_block_id', 'TimeBlock']
            type_cols = ['type', 'type', 'Type', 'dc_sg', 'DC/SG']
            
            for row in reader:
                # Find the actual column names in the row
                row_keys = row.keys()
                
                # Extract base information
                date_val = None
                for col in date_cols:
                    if col in row_keys:
                        date_val = row[col]
                        break
                
                time_block_val = None
                for col in time_block_cols:
                    if col in row_keys:
                        time_block_val = row[col]
                        break
                
                type_val = None
                for col in type_cols:
                    if col in row_keys:
                        type_val = row[col]
                        break
                
                if not date_val:
                    # STRICT VALIDATION: Raise error immediately if date is missing
                    raise ValueError(f"Missing date/scheduled_at in row: {row}")

                parsed_date = parse_csv_scheduled_at(date_val)
                
                # Identify plant columns (those that are not date, time_block, or type)
                special_consumed_cols = set()
                for col in date_cols + time_block_cols + type_cols:
                    if col in row_keys:
                        special_consumed_cols.add(col)
                
                plant_cols = [col for col in row_keys if col not in special_consumed_cols and col is not None]
                
                for plant_id in plant_cols:
                    volume_val = row[plant_id]
                    
                    # Create a flattened record
                    # We use 'scheduled_at' as the primary key for the date
                    record = {
                        "scheduled_at": parsed_date,
                        "time_block": time_block_val,
                        "dc_sg": type_val,
                        "plant_id": plant_id,
                        "volume": volume_val
                    }
                    raw_data.append(record)

            logger.info("Parsed Supply CSV: created %d flattened records from %d rows", len(raw_data), len(list(reader)) + 1)
            return raw_data

        except Exception as e:
            logger.error("Error parsing Supply CSV content: %s", e, exc_info=True)
            raise ValueError(f"Supply CSV parsing failed: {str(e)}") from e

    def validate_content(self, content: str) -> bool:
        """
        Validate if content is valid CSV with supply-like structure
        """
        try:
            csv_file = io.StringIO(content.strip())
            reader = csv.reader(csv_file)
            header = next(reader, None)
            if not header:
                return False
            
            # Check for at least one identifying column
            identifying_cols = ['date', 'scheduled_at', 'time_block', 'type', 'dc_sg']
            header_lower = [h.lower() for h in header if h]
            
            has_identifying = any(col in header_lower for col in identifying_cols)
            return has_identifying and len(header) > 2  # Must have more than just identifiers
            
        except Exception:
            return False
