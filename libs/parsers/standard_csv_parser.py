import csv
import io
import logging
from typing import Dict, Any, List, Optional

from libs.parsers.base_parser import BaseParser
from libs.models.provider_models import ProviderEntity

logger = logging.getLogger(__name__)


class StandardCSVParser(BaseParser):
    """
    Standard CSV parser - maintains existing CSV parsing logic
    """
    
    def get_parser_type(self) -> str:
        """Get the parser type identifier"""
        return "standard"
    
    def parse(self, content: str, provider: Optional[ProviderEntity] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        Parse standard CSV content into list of dictionaries
        
        Args:
            content: Raw CSV content string
            provider: Provider entity containing schema definition (optional)
            **kwargs: Additional parsing parameters (delimiter, etc.)
            
        Returns:
            List[Dict[str, Any]]: Parsed CSV data as list of dictionaries
        """
        try:
            # Remove BOM (Byte Order Mark) if present
            bom_removed = False
            if content.startswith('\ufeff'):  # UTF-8 BOM or UTF-16 BE BOM
                content = content[1:]
                bom_removed = True
            elif content.startswith('\ufffe'):  # UTF-16 LE BOM
                content = content[1:]
                bom_removed = True

            if bom_removed:
                logger.info("Removed BOM character from CSV content")

            # Get CSV parsing parameters
            delimiter = kwargs.get('delimiter', ',')
            has_header = kwargs.get('has_header', True)
            
            # Parse CSV content
            csv_file = io.StringIO(content)
            
            if has_header:
                reader = csv.DictReader(csv_file, delimiter=delimiter)
                raw_data = [dict(row) for row in reader]
            else:
                # If no header, create generic column names
                reader = csv.reader(csv_file, delimiter=delimiter)
                rows = list(reader)
                if not rows:
                    return []
                
                # Create column names like col_0, col_1, etc.
                num_cols = len(rows[0]) if rows else 0
                headers = [f'col_{i}' for i in range(num_cols)]
                
                raw_data = []
                for row in rows:
                    # Pad row to match header length
                    padded_row = row + [''] * (len(headers) - len(row))
                    raw_data.append(dict(zip(headers, padded_row[:len(headers)])))

            logger.info("Parsed standard CSV content: %d records", len(raw_data))
            return raw_data

        except (ValueError, UnicodeDecodeError, OSError) as e:
            logger.error("Error parsing standard CSV content: %s", e, exc_info=True)
            raise ValueError(f"Standard CSV parsing failed: {str(e)}") from e
    
    def validate_content(self, content: str) -> bool:
        """
        Validate if content is valid CSV
        
        Args:
            content: Raw content to validate
            
        Returns:
            bool: True if content appears to be valid CSV
        """
        try:
            # Simple validation - check if we can read at least one row
            csv_file = io.StringIO(content.strip())
            reader = csv.reader(csv_file)
            first_row = next(reader, None)
            return first_row is not None and len(first_row) > 0
        except Exception:
            return False
