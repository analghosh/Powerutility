from typing import List, Dict, Any, Optional
import logging
from datetime import datetime
from bs4 import BeautifulSoup

from libs.parsers.base_parser import BaseParser
from libs.models.provider_models import ProviderEntity

logger = logging.getLogger(__name__)

class StandardHTMLTableParser(BaseParser):
    """
    Standard HTML Table Parser
    
    Extracts data from standard HTML tables using heuristics to identify the main table.
    Supports mapping headers to serve_field names using provider configuration.
    """
    
    def get_parser_type(self) -> str:
        return "standard_html_table"

    def parse(self, content: str, provider: Optional[ProviderEntity] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        Parse HTML content into structured data
        
        Args:
            content: HTML content string
            provider: Provider entity for context and mapping
            **kwargs: Additional parameters (can include 'soup' if already parsed)
            
        Returns:
            List[Dict[str, Any]]: Parsed data records
        """
        try:
            soup = kwargs.get('soup')
            if not soup:
                soup = BeautifulSoup(content, 'html.parser')
                
            config = getattr(provider, 'metadata', {}) if provider else {}
            
            # Find the main data table
            table = self._find_table(soup, config)
            
            if not table:
                logger.warning("⚠️ No suitable table found in HTML content")
                return []
                
            # Extract headers and rows
            headers = self._extract_headers(table, config)
            if not headers:
                logger.warning("⚠️ No headers found in table")
                return []
                
            rows = self._extract_rows(table, config)
            if not rows:
                logger.warning("⚠️ No data rows found in table")
                return []
                
            logger.debug("📊 Found %d headers and %d rows", len(headers), len(rows))
            
            # Process and map data
            return self._process_table_data(headers, rows, provider)
            
        except Exception as e:
            logger.error("Error parsing HTML table: %s", str(e))
            return []
            
    def _find_table(self, soup: BeautifulSoup, config: Dict[str, Any]) -> Any:
        """Find the most relevant table in the soup"""
        # Strategy 1: Look for specific aria-label "sticky table" (Generic heuristic often used in UI libs)
        table = soup.find("table", attrs={"aria-label": "sticky table"})
        
        # Strategy 2: If not found, look for ANY table with class "MuiTable-root" (Material UI)
        if not table:
            table = soup.find("table", class_=lambda c: c and "MuiTable-root" in c)
            
        # Strategy 3: Just take the first table found
        if not table:
            tables = soup.find_all("table")
            if tables:
                table = tables[0]
                if len(tables) > 1:
                    logger.debug("Multiple tables found (%d), selecting the first one", len(tables))
                    
        return table

    def _extract_headers(self, table: Any, config: Dict[str, Any]) -> List[str]:
        """Extract table headers"""
        try:
            headers = []
            # Try to find headers in thead first
            thead = table.find("thead")
            if thead:
                rows = thead.find_all("tr")
                if rows:
                    # Strategy: Use the last row for direct headers
                    header_row = rows[-1]
                    headers = [
                        th.get_text(strip=True) for th in header_row.find_all(["th", "td"])
                    ]
                    
            # Fallback: use first row of the table
            if not headers:
                first_row = table.find("tr")
                if first_row:
                    headers = [
                        cell.get_text(strip=True)
                        for cell in first_row.find_all(["th", "td"])
                    ]
            
            # Ensure headers align by keeping empty ones
            cleaned_headers = [h if h else f"Column_{i}" for i, h in enumerate(headers)]
            return cleaned_headers
            
        except Exception as e:
            logger.error("Error extracting headers: %s", str(e))
            return []

    def _extract_rows(self, table: Any, config: Dict[str, Any]) -> List[List[str]]:
        """Extract table rows with rowspan/colspan handling"""
        try:
            rows = []
            tbody = table.find("tbody")
            table_body = tbody if tbody else table
            
            # Track cells that need to be filled due to rowspan
            span_map = {}
            
            for row_idx, row in enumerate(table_body.find_all("tr")):
                # Skip header row if it's the first row and we are in the main table body 
                # (usually thead is separate, but if nested in tbody/table directly, extract_headers might have picked it)
                # For safety, if a row contains only 'th', we might skip it?
                # But ScrapeRawDataProcessor logic didn't verify this. Stick to original logic.
                
                cells = []
                col_idx = 0
                for cell in row.find_all(["td", "th"]):
                    # Fill cells from previous rowspans
                    while span_map.get(col_idx, 0):
                        cells.append(span_map[col_idx][0])
                        span_map[col_idx][1] -= 1
                        if span_map[col_idx][1] == 0:
                            del span_map[col_idx]
                        col_idx += 1

                    cell_text = cell.get_text(strip=True)
                    rowspan = int(cell.get("rowspan", 1))
                    colspan = int(cell.get("colspan", 1))

                    # Add cell value for colspan
                    for i in range(colspan):
                        cells.append(cell_text)
                        # Handle rowspan for this column
                        if rowspan > 1:
                            span_map[col_idx] = [cell_text, rowspan - 1]
                        col_idx += 1

                # Fill remaining cells from previous rowspans
                while span_map.get(col_idx, 0):
                    cells.append(span_map[col_idx][0])
                    span_map[col_idx][1] -= 1
                    if span_map[col_idx][1] == 0:
                        del span_map[col_idx]
                    col_idx += 1

                if cells and any(cell for cell in cells):  # Skip completely empty rows
                    rows.append(cells)

            return rows
            
        except Exception as e:
            logger.error("Error extracting rows: %s", str(e))
            return []

    def _process_table_data(
        self, headers: List[str], rows: List[List[str]], provider: Optional[ProviderEntity]
    ) -> List[Dict[str, Any]]:
        """Process table data and map keys to serve_field"""
        try:
            processed_data = []
            current_time = datetime.now()
            
            # Build mapping dictionary: {header_name: serve_field_name}
            # Provider mapping is: provider_field -> serve_field
            # So if header == provider_field, we rename it to serve_field
            
            header_map = {}
            if provider and provider.mapping:
                # Create lookup: standard case and lowercase for case-insensitive matching
                mapping_lookup = {} #{provider_field_lower: serve_field}
                for mapping_obj in provider.mapping:
                    if hasattr(mapping_obj, 'provider_field') and hasattr(mapping_obj, 'serve_field'):
                        mapping_lookup[mapping_obj.provider_field.lower()] = mapping_obj.serve_field
                
                # Map headers
                for header in headers:
                    serve_field = mapping_lookup.get(header.lower())
                    if serve_field:
                        header_map[header] = serve_field
                    else:
                        header_map[header] = header # Keep original if no mapping found
            else:
                # No mapping, keep headers as is
                header_map = {h: h for h in headers}

            # Process each row
            for row in rows:
                row_data = {}
                for i, header in enumerate(headers):
                    target_key = header_map.get(header, header)
                    
                    if i < len(row):
                        row_data[target_key] = row[i].strip()
                    else:
                        row_data[target_key] = ""
                
                # Add generic metadata (optional, can be added by processor)
                # But ScrapeRawDataProcessor adds them, so parsers usually return just the record data.
                # However, the original ScrapeRawDataProcessor._process_table_data added: 
                # scraped_at, data_type, source, createdAt, updatedAt.
                # Parsers should probably return clean data, and processor adds metadata.
                # But to maintain 1:1 behavior with original extracting logic:
                if provider:
                    row_data["data_type"] = provider.data_type
                    # source comes from config, but I don't have full config dict passed easily unless I assume provider metadata has it
                    # Let's rely on processor to add metadata, or add what we can.
                    
                row_data["scraped_at"] = current_time
                    
                processed_data.append(row_data)

            return processed_data
            
        except Exception as e:
            logger.error("Error processing table data: %s", str(e))
            return []
