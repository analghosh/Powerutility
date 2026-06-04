"""
Trading Data Parser
Handles complex trading data formats including multi-date CSV and raw string formats
"""

import pandas as pd
import numpy as np
import csv
import re
import io
import logging
from typing import List, Dict, Union, Any, Optional

from libs.parsers.base_parser import BaseParser
from libs.models.provider_models import ProviderEntity
from libs.utils.csv_date_parse import parse_csv_scheduled_at

logger = logging.getLogger(__name__)

class TradingParser(BaseParser):
    """
    Universal parser for trading data that can handle both CSV and raw string formats
    """
    
    def __init__(self):
        self.headers = [
            'Date', 'Time_Start', 'Time_End', 
            'market_type', 'Bidded', 'Traded'
        ]
        
        # Market type mapping
        self.market_types = ['DAM', 'RTM', 'GDAM', 'TAM']
    
    def get_parser_type(self) -> str:
        """Get the parser type identifier"""
        return "trading"
    
    def parse(self, content: str, provider: Optional[ProviderEntity] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        Parse trading data content
        
        Args:
            content: Raw content to parse
            provider: Provider entity for context
            **kwargs: Additional parsing parameters
            
        Returns:
            List[Dict[str, Any]]: Parsed trading data records
        """
        try:
            format_type = self.detect_format(content)
            logger.info("Detected trading format: %s", format_type)
            
            if format_type == 'multi_date_csv':
                parsed_data = self.parse_multi_date_csv(content)
            elif format_type == 'raw_string':
                parsed_data = self.parse_raw_string(content)
            elif format_type == 'structured_single_date_csv':
                parsed_data = self.parse_structured_single_date_csv(content)
            else:
                parsed_data = self.parse_standard_csv(content)
            
            # Apply field mapping if provider configuration is available
            if provider and parsed_data:
                mapped_data = self._apply_field_mapping(parsed_data, provider)
                return mapped_data
            
            return parsed_data
                
        except Exception as e:
            logger.error("Error parsing trading data: %s", e, exc_info=True)
            raise ValueError(f"Trading data parsing failed: {str(e)}") from e
    
    def validate_content(self, content: str) -> bool:
        """
        Validate if content is trading data format
        
        Args:
            content: Raw content to validate
            
        Returns:
            bool: True if content appears to be trading data
        """
        try:
            # Check for trading data indicators
            trading_indicators = ['DAM', 'RTM', 'GDAM', 'TAM', 'Bidded', 'Traded']
            content_upper = content.upper()
            
            # Must contain at least 3 trading indicators
            indicator_count = sum(1 for indicator in trading_indicators if indicator in content_upper)
            return indicator_count >= 3
            
        except Exception:
            return False
    
    def detect_format(self, content: str) -> str:
        """Detect whether content is multi-date CSV or raw string format"""
        try:
            # Try to detect CSV format by looking for multiple dates in first row
            lines = content.strip().split('\n')
            if len(lines) > 3:
                first_row = lines[0]
                date_patterns = re.findall(r'\d{1,2}/\d{1,2}/\d{4}', first_row)
                date_patterns += re.findall(r'\d{2}-\d{2}-\d{4}', first_row)
                date_patterns += re.findall(r'\d{4}-\d{2}-\d{2}', first_row)
                # Validate that these are actually valid dates
                valid_dates = [d for d in date_patterns if self._validate_date_format(d)]
                date_count = len(valid_dates)
                if date_count > 1:
                    return 'multi_date_csv'
                    
                if (date_count == 1 and len(lines) >= 3 and 
                    'DAM' in lines[1] and 'RTM' in lines[1] and 
                    'Bidded' in lines[2] and 'Traded' in lines[2]):
                    return 'structured_single_date_csv'

            
            # Check for raw string format (date + headers + time data)
            if re.match(r'^\d{1,2}/\d{1,2}/\d{4}.*DAM.*RTM.*GDAM.*TAM', content):
                return 'raw_string'
            
            # Default to standard CSV
            return 'standard_csv'
            
        except Exception as e:
            logger.warning("Error detecting format: %s", e)
            return 'standard_csv'
    
    def parse_multi_date_csv(self, content: str) -> List[Dict[str, Any]]:
        """Parse multi-date CSV format like test.csv"""
        try:
            df = pd.read_csv(io.StringIO(content), header=None)
            
            # Extract dates from row 0
            dates = []
            date_positions = []
            
            for i, cell in enumerate(df.iloc[0]):
                if pd.notna(cell):
                    date_str = str(cell).strip()
                    if self._validate_date_format(date_str):
                        dates.append(self._parse_date_to_iso(date_str))
                        date_positions.append(i)
            
            # Extract time periods from columns 1 and 2
            time_data = []
            for i in range(3, len(df)):
                row_num = df.iloc[i, 0]
                start_time = df.iloc[i, 1]
                end_time = df.iloc[i, 2]
                
                if pd.notna(start_time) and pd.notna(end_time):
                    time_data.append({
                        'row_num': row_num,
                        'start_time': start_time,
                        'end_time': end_time,
                        'row_index': i
                    })
            
            # Parse data for each date
            parsed_records = []
            
            for date_idx, date in enumerate(dates):
                date_col_start = date_positions[date_idx]
                
                for time_info in time_data:
                    row_idx = time_info['row_index']
                    
                    # Extract 8 values for this date
                    values = []
                    for col_offset in range(8):
                        col = date_col_start + col_offset
                        if col < df.shape[1]:
                            val = df.iloc[row_idx, col]
                            if pd.isna(val):
                                values.append(0)
                            else:
                                try:
                                    values.append(float(val))
                                except:
                                    values.append(0)
                        else:
                            values.append(0)
                    
                    while len(values) < 8:
                        values.append(0)
                    values = values[:8]
                    
                    iso_date = self._parse_date_to_iso(date)
                    
                    # Create separate records for each market type
                    for market_idx, market_type in enumerate(self.market_types):
                        bidded_idx = market_idx * 2
                        traded_idx = market_idx * 2 + 1
                        
                        record = {
                            'Date': iso_date,  # ISO format: YYYY-MM-DD
                            'Time_Start': self._normalize_time_format(time_info['start_time']),
                            'Time_End': self._normalize_time_format(time_info['end_time']),
                            'market_type': market_type,
                            'Bidded': values[bidded_idx] if bidded_idx < len(values) else 0,
                            'Traded': values[traded_idx] if traded_idx < len(values) else 0
                        }
                        parsed_records.append(record)
            
            logger.info("Parsed multi-date CSV: %d records from %d dates (%d time periods × %d market types)", 
                        len(parsed_records), len(dates), len(time_data), len(self.market_types))
            return parsed_records
            
        except Exception as e:
            logger.error("Error parsing multi-date CSV: %s", e)
            raise ValueError(f"Multi-date CSV parsing failed: {str(e)}")
    
    def parse_raw_string(self, content: str) -> List[Dict[str, Any]]:
        """Parse raw string format like the original data"""
        try:
            date_match = re.match(
                r'^(\d{1,2}/\d{1,2}/\d{4}|\d{2}-\d{2}-\d{4}|\d{4}-\d{2}-\d{2})',
                content.strip(),
            )
            if date_match:
                raw_date = date_match.group(1)
                if self._validate_date_format(raw_date):
                    date_str = self._parse_date_to_iso(raw_date)
                    logger.info("Parsed date from raw string: %s -> %s", raw_date, date_str)
                else:
                    logger.warning("Invalid date format in raw string: %s", raw_date)
                    date_str = raw_date
            else:
                logger.warning("No date found in raw string content")
                date_str = "Unknown"
            
            # Remove date and headers
            data_clean = re.sub(r'^\d{1,2}/\d{1,2}/\d{4}.*?(BiddedTraded){4}', '', content)
            
            records = []
            
            # Split by time pattern
            segments = re.split(r'(?=\d{1,2}:\d{2}\d{1,2}:\d{2})', data_clean)
            
            for segment in segments:
                if not segment.strip():
                    continue
                    
                time_match = re.match(r'^(\d{1,2}:\d{2})(\d{1,2}:\d{2})(.*)', segment)
                
                if time_match:
                    start_time = time_match.group(1)
                    end_time = time_match.group(2)
                    values_str = time_match.group(3)
                    
                    # Extract numeric values
                    values = re.findall(r'-?\d+(?:\.\d+)?', values_str)
                    
                    # Pad to 8 values if needed
                    while len(values) < 8:
                        values.append('0')
                    
                    # Convert date to ISO format (YYYY-MM-DD)
                    iso_date = self._parse_date_to_iso(date_str)
                    
                    # Create separate records for each market type
                    for market_idx, market_type in enumerate(self.market_types):
                        bidded_idx = market_idx * 2
                        traded_idx = market_idx * 2 + 1
                        
                        record = {
                            'Date': iso_date,  # ISO format: YYYY-MM-DD
                            'Time_Start': self._normalize_time_format(start_time),
                            'Time_End': self._normalize_time_format(end_time),
                            'market_type': market_type,
                            'Bidded': float(values[bidded_idx]) if bidded_idx < len(values) and values[bidded_idx] != '0' else 0,
                            'Traded': float(values[traded_idx]) if traded_idx < len(values) and values[traded_idx] != '0' else 0
                        }
                        records.append(record)
            
            logger.info("Parsed raw string: %d records (%d time periods × %d market types)", 
                        len(records), len(records) // len(self.market_types), len(self.market_types))
            return records
            
        except Exception as e:
            logger.error("Error parsing raw string: %s", e)
            raise ValueError(f"Raw string parsing failed: {str(e)}")
    
    def _normalize_time_format(self, time_str: str) -> str:
        """
        Ensure time is in hh:mm format by adding a leading zero if needed.
        Example: '9:15' -> '09:15'
        """
        try:
            # Ensure it's a string before processing
            time_str = str(time_str).strip()
            if ':' in time_str and len(time_str.split(':')[0]) == 1:
                # If the hour part is a single digit, pad it with a zero
                return f"0{time_str}"
            return time_str
        except Exception as e:
            logger.warning("Could not normalize time format for '%s': %s", time_str, e)
            return time_str # Return original on error

    def _validate_date_format(self, date_str: str) -> bool:
        """Validate date: DD-MM-YYYY, YYYY-MM-DD, or DD/MM/YYYY."""
        try:
            parse_csv_scheduled_at(str(date_str).strip())
            return True
        except ValueError as e:
            logger.warning("Error validating date format '%s': %s", date_str, e)
            return False
    
    def _normalize_date_format(self, date_str: str) -> str:
        """
        Normalize date format to DD/MM/YYYY for consistency.
        """
        try:
            dt = parse_csv_scheduled_at(date_str)
            normalized = dt.strftime("%d/%m/%Y")
            logger.debug("Normalized date: %s -> %s", date_str, normalized)
            return normalized
        except ValueError as e:
            logger.warning("Error normalizing date '%s': %s", date_str, e)
            return date_str

    def _parse_date_to_iso(self, date_str: str) -> str:
        """Convert supported date string to ISO format YYYY-MM-DD."""
        return parse_csv_scheduled_at(str(date_str).strip()).strftime("%Y-%m-%d")
    
    def get_supported_date_format(self) -> str:
        """
        Get information about supported date format
        
        Returns:
            str: Description of supported date format
        """
        return (
            "Input: DD-MM-YYYY (04-06-2026), YYYY-MM-DD (2026-06-04), "
            "or DD/MM/YYYY (04/06/2026). Output: ISO YYYY-MM-DD."
        )
    
    def get_output_schema(self) -> Dict[str, str]:
        """
        Get information about output schema
        
        Returns:
            Dict[str, str]: Output column descriptions
        """
        return {
            'Date': 'ISO format date (YYYY-MM-DD)',
            'Time_Start': 'Start time (HH:MM)',
            'Time_End': 'End time (HH:MM)', 
            'market_type': 'Market type (DAM, RTM, GDAM, TAM)',
            'Bidded': 'Bidded amount (float)',
            'Traded': 'Traded amount (float)'
        }
    
    def get_transformation_example(self) -> str:
        """
        Get example of data transformation
        
        Returns:
            str: Example showing input vs output format
        """
        return """
        INPUT (Wide Format):
        Date: 04/06/2026, Time: 10:00-10:15
        DAM_Bidded: -4, DAM_Traded: -4, RTM_Bidded: -24, RTM_Traded: -24, ...
        
        OUTPUT (Normalized Format):
        {'Date': '2025-09-01', 'Time_Start': '10:00', 'Time_End': '10:15', 'market_type': 'DAM', 'Bidded': -4, 'Traded': -4}
        {'Date': '2025-09-01', 'Time_Start': '10:00', 'Time_End': '10:15', 'market_type': 'RTM', 'Bidded': -24, 'Traded': -24}
        {'Date': '2025-09-01', 'Time_Start': '10:00', 'Time_End': '10:15', 'market_type': 'GDAM', 'Bidded': 0, 'Traded': 0}
        {'Date': '2025-09-01', 'Time_Start': '10:00', 'Time_End': '10:15', 'market_type': 'TAM', 'Bidded': 0, 'Traded': 0}
        """
    
    def parse_standard_csv(self, content: str) -> List[Dict[str, Any]]:
        """Parse standard CSV format"""
        try:
            df = pd.read_csv(io.StringIO(content))
            return df.to_dict('records')
        except Exception as e:
            logger.error("Error parsing standard CSV: %s", e)
            raise ValueError(f"Standard CSV parsing failed: {str(e)}")


    
    def parse_structured_single_date_csv(self, content: str) -> List[Dict[str, Any]]:
        """
        Parse structured single-date CSV format with:
        - Row 0: Date in column 3
        - Row 1: Market types (DAM, RTM, GDAM, TAM)
        - Row 2: Headers (Bidded, Traded repeated)
        - Row 3+: Data with time periods and values
        """
        try:
            df = pd.read_csv(io.StringIO(content), header=None)
            
            # Extract date from row 0, column 3
            date_str = str(df.iloc[0, 3]).strip()
            logger.info("Extracted date from structured CSV: %s", date_str)
            
            # Validate and convert date format
            if not self._validate_date_format(date_str):
                raise ValueError(f"Invalid date format: {date_str}")
            
            # Convert to ISO format
            iso_date = self._parse_date_to_iso(date_str)
            
            # Extract market types from row 1
            market_types = []
            market_positions = []
            
            for col in range(df.shape[1]):
                cell = df.iloc[1, col]
                if pd.notna(cell) and str(cell).strip() in ['DAM', 'RTM', 'GDAM', 'TAM']:
                    market_types.append(str(cell).strip())
                    market_positions.append(col)
            
            logger.info("Market types found: %s at positions: %s", market_types, market_positions)
            
            # Parse data rows (starting from row 3)
            parsed_records = []
            
            for row_idx in range(3, len(df)):
                row_data = df.iloc[row_idx]
                
                # Extract time information from columns 1 and 2
                time_start = str(row_data[1]).strip() if pd.notna(row_data[1]) else None
                time_end = str(row_data[2]).strip() if pd.notna(row_data[2]) else None
                
                if not time_start or not time_end or time_start == 'nan' or time_end == 'nan':
                    continue
                
                # Normalize time format
                time_start = self._normalize_time_format(time_start)
                time_end = self._normalize_time_format(time_end)
                
                # Extract values for each market type
                for i, market_type in enumerate(market_types):
                    market_col = market_positions[i]
                    
                    # Get Bidded and Traded values (assuming they're in consecutive columns)
                    bidded_col = market_col
                    traded_col = market_col + 1
                    
                    bidded_val = row_data[bidded_col] if bidded_col < len(row_data) else None
                    traded_val = row_data[traded_col] if traded_col < len(row_data) else None
                    
                    # Convert to float, default to 0 if empty/NaN
                    try:
                        bidded = float(bidded_val) if pd.notna(bidded_val) and str(bidded_val).strip() != '' else 0.0
                    except (ValueError, TypeError):
                        bidded = 0.0
                    
                    try:
                        traded = float(traded_val) if pd.notna(traded_val) and str(traded_val).strip() != '' else 0.0
                    except (ValueError, TypeError):
                        traded = 0.0
                    
                    # Create record
                    record = {
                        'Date': iso_date,
                        'Time_Start': time_start,
                        'Time_End': time_end,
                        'market_type': market_type,
                        'Bidded': bidded,
                        'Traded': traded
                    }
                    
                    parsed_records.append(record)
            
            logger.info("Parsed structured single-date CSV: %d records (%d time periods × %d market types)", 
                        len(parsed_records), len(parsed_records) // len(market_types), len(market_types))
            return parsed_records
            
        except Exception as e:
            logger.error("Error parsing structured single-date CSV: %s", e)
            raise ValueError(f"Structured single-date CSV parsing failed: {str(e)}")

    def _apply_field_mapping(self, parsed_data: List[Dict[str, Any]], provider: ProviderEntity) -> List[Dict[str, Any]]:
        """
        Apply field mapping from provider configuration to parsed data
        
        Args:
            parsed_data: Raw parsed data records
            provider: Provider entity with field mapping configuration
            
        Returns:
            List[Dict[str, Any]]: Data with fields mapped according to provider config
        """
        try:
            # Check for mapping in provider object first, then fallback to metadata
            mappings = []
            
            if provider.mapping:
                for m in provider.mapping:
                    mappings.append({
                        "provider_field": m.provider_field,
                        "serve_field": m.serve_field,
                        "transformation": getattr(m, 'transformation', None)
                    })
            elif provider.metadata and provider.metadata.get('field_mappings'):
                # Fallback to metadata field_mappings (Dict[serve_field, provider_field])
                field_mappings = provider.metadata.get('field_mappings')
                logger.info("Using metadata field_mappings for provider %s", provider.code)
                for serve_field, provider_field in field_mappings.items():
                    mappings.append({
                        "provider_field": provider_field,
                        "serve_field": serve_field,
                        "transformation": None # Metadata mapping doesn't support transformation definition simply
                    })
            
            if not mappings:
                logger.debug("No field mapping configured for provider %s", provider.code)
                return parsed_data
            
            mapped_records = []
            
            for record in parsed_data:
                mapped_record = {}
                
                # Apply field mappings
                for mapping in mappings:
                    provider_field = mapping["provider_field"]
                    serve_field = mapping["serve_field"]
                    transformation = mapping["transformation"]
                    
                    # Get value from parsed record
                    if provider_field in record:
                        value = record[provider_field]
                        
                        # Apply transformation if specified
                        if transformation:
                            value = self._apply_transformation(value, transformation)
                        
                        mapped_record[serve_field] = value
                    else:
                        # Check if provider field has a default value in schema
                        default_value = self._get_default_value(provider_field, provider)
                        mapped_record[serve_field] = default_value
                
                # Add any unmapped fields that might be needed
                mapped_provider_fields = [m["provider_field"] for m in mappings]
                for key, value in record.items():
                    if key not in mapped_provider_fields:
                        # Keep unmapped fields as-is (like metadata fields)
                        mapped_record[key] = value
                
                mapped_records.append(mapped_record)
            
            logger.info("Applied field mapping for provider %s: %d records mapped", 
                        provider.code, len(mapped_records))
            return mapped_records
            
        except Exception as e:
            logger.error("Error applying field mapping for provider %s: %s", provider.code, str(e))
            # Return original data if mapping fails
            return parsed_data
    
    def _apply_transformation(self, value: Any, transformation: str) -> Any:
        """
        Apply transformation to field value
        
        Args:
            value: Original field value
            transformation: Transformation rule to apply
            
        Returns:
            Any: Transformed value
        """
        try:
            if not transformation or value is None:
                return value
            
            transformation = transformation.lower().strip()
            
            if transformation == 'uppercase':
                return str(value).upper()
            elif transformation == 'lowercase':
                return str(value).lower()
            elif transformation == 'trim':
                return str(value).strip()
            elif transformation == 'float':
                return float(value) if value != '' else 0.0
            elif transformation == 'int':
                return int(float(value)) if value != '' else 0
            elif transformation == 'abs':
                return abs(float(value)) if value != '' else 0.0
            elif transformation.startswith('multiply_'):
                # Extract multiplier: multiply_4, multiply_0.25, etc.
                multiplier = float(transformation.split('_')[1])
                return float(value) * multiplier if value != '' else 0.0
            elif transformation.startswith('divide_'):
                # Extract divisor: divide_4, divide_100, etc.
                divisor = float(transformation.split('_')[1])
                return float(value) / divisor if value != '' and divisor != 0 else 0.0
            elif transformation == 'iso_date':
                # Convert date to ISO format if it's not already
                if isinstance(value, str) and '/' in value:
                    return self._parse_date_to_iso(value)
                return value
            else:
                logger.warning("Unknown transformation: %s", transformation)
                return value
                
        except Exception as e:
            logger.error("Error applying transformation '%s' to value '%s': %s", transformation, value, str(e))
            return value
    
    def _get_default_value(self, field_name: str, provider: ProviderEntity) -> Any:
        """
        Get default value for a field from provider schema
        
        Args:
            field_name: Name of the field
            provider: Provider entity with schema definition
            
        Returns:
            Any: Default value or None
        """
        try:
            for field in provider.fields:
                if field.field_name == field_name:
                    return field.default_value
            return None
        except Exception as e:
            logger.error("Error getting default value for field %s: %s", field_name, str(e))
            return None
    
    def get_field_mapping_info(self, provider: ProviderEntity) -> Dict[str, Any]:
        """
        Get information about field mappings for a provider
        
        Args:
            provider: Provider entity
            
        Returns:
            Dict[str, Any]: Field mapping information
        """
        if not provider or not provider.mapping:
            return {"mappings": [], "total_mappings": 0}
        
        mappings = []
        for mapping in provider.mapping:
            mappings.append({
                "provider_field": mapping.provider_field,
                "serve_field": mapping.serve_field,
                "transformation": mapping.transformation,
                "has_transformation": bool(mapping.transformation)
            })
        
        return {
            "mappings": mappings,
            "total_mappings": len(mappings),
            "provider_code": provider.code
        }


# Global instance for easy import
trading_parser = TradingParser()