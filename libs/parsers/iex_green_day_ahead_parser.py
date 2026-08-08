from typing import Dict, Any, List, Optional
import json
import logging
import re
from bs4 import BeautifulSoup
from datetime import datetime

from libs.parsers.base_parser import BaseParser
from libs.models.provider_models import ProviderEntity
from libs.utils.csv_date_parse import parse_ingestion_date

logger = logging.getLogger(__name__)

# The market snapshot page ships its rows twice: as a server-rendered <table> and as a
# JSON payload inside the Next.js flight chunks. The payload is preferred because it is
# keyed by name, so a column being added or reordered upstream cannot shift values into
# the wrong field.
_FLIGHT_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\s*\]\)')

# Payload field name -> output field. Each group is discovered by suffix so a new
# generation bucket shows up on its own instead of being silently dropped.
_PAYLOAD_GROUPS = (
    ("sell_bid", "_sell_bid"),
    ("mcv", "_unconstrained_m_c_v"),
    ("final_scheduled_volume", "_cleared_volume"),
)
_PAYLOAD_BUY_BID = "purchase_bid"
_PAYLOAD_MCP = "unconstrained_m_c_p"
_PAYLOAD_DATE = "date"
_PAYLOAD_TIME_BLOCK = "period"

_TABLE_GROUP_COUNT = 3  # sell bid, MCV, final scheduled volume
_HEADER_SIGNATURES = (["Date", "Hour", "Time Block"], ["Date", "Hour", "Session ID", "Time Block"])


class IEXGreenDayAheadParser(BaseParser):
    """
    Parser for IEX Green Day Ahead Market data.

    Reads the embedded market snapshot payload when present and falls back to the
    server-rendered HTML table. The table layout is read from its own header row, so both
    the current 5-bucket breakdown (Total / Hydro / Wind / Other RE / DRE) and the older
    4-bucket one (Total / Solar / Non-Solar / Hydro) parse without a code change.
    """

    def get_parser_type(self) -> str:
        return "iex_green_day_ahead"

    def _safe_float_conversion(self, val):
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    def _parse_date_str(self, date_str: str) -> str:
        return parse_ingestion_date(date_str).date().isoformat()

    def parse(self, content: str, provider: Optional[ProviderEntity] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        Parse IEX Green Day Ahead Market content.

        Args:
            content: Raw HTML content
            provider: Provider entity for context
            **kwargs: Additional arguments (can accept 'soup' if already parsed)

        Returns:
            List[Dict[str, Any]]: Parsed data records
        """
        try:
            data_type, source = self._provider_context(provider)

            rows = self._parse_embedded_payload(content or "", data_type, source)
            if rows:
                logger.info("IEXGreenDayAheadParser: Extracted %d rows from embedded payload", len(rows))
                return rows

            soup = kwargs.get('soup')
            if not soup:
                soup = BeautifulSoup(content or "", "html.parser")

            rows = self._parse_html_table(soup, data_type, source)
            logger.info("IEXGreenDayAheadParser: Extracted %d rows from HTML table", len(rows))
            return rows

        except Exception as e:
            logger.error("Error in IEX Green Day Ahead parser: %s", str(e))
            return []

    def _provider_context(self, provider: Optional[ProviderEntity]):
        data_type = "unknown"
        source = "unknown"
        if provider:
            data_type = provider.data_type if hasattr(provider, 'data_type') else "unknown"
            if provider.metadata:
                source = provider.metadata.get('source', 'unknown')
        return data_type, source

    # ------------------------------------------------------------------ payload

    def _parse_embedded_payload(self, content: str, data_type: str, source: str) -> List[Dict[str, Any]]:
        records = self._extract_payload_records(content)
        if not records:
            return []

        rows = []
        for record in records:
            raw_date = record.get(_PAYLOAD_DATE)
            time_block = record.get(_PAYLOAD_TIME_BLOCK)
            if not raw_date or not time_block:
                continue

            rows.append(self._build_row(
                date=self._parse_date_str(raw_date),
                time_block=time_block,
                buy_bid=self._safe_float_conversion(record.get(_PAYLOAD_BUY_BID)),
                groups={
                    field: {
                        key[: -len(suffix)]: self._safe_float_conversion(value)
                        for key, value in record.items()
                        if key.endswith(suffix)
                    }
                    for field, suffix in _PAYLOAD_GROUPS
                },
                mcp=self._safe_float_conversion(record.get(_PAYLOAD_MCP)),
                data_type=data_type,
                source=source,
            ))
        return rows

    def _extract_payload_records(self, content: str) -> List[Dict[str, Any]]:
        """Pull reportData.records out of the Next.js flight chunks, if the page carries them."""
        if "reportData" not in content:
            return []

        chunks = []
        for raw_chunk in _FLIGHT_CHUNK_RE.findall(content):
            try:
                chunks.append(json.loads(raw_chunk))
            except ValueError:
                continue
        if not chunks:
            return []

        # A payload can straddle two chunks, so search the concatenation rather than each part.
        decoded = "".join(chunks)
        marker = decoded.find('"reportData"')
        if marker == -1:
            return []

        try:
            report_data, _ = json.JSONDecoder().raw_decode(decoded, decoded.index("{", marker))
        except ValueError as e:
            logger.warning("Found reportData but could not decode it: %s", e)
            return []

        records = report_data.get("records") if isinstance(report_data, dict) else None
        return records if isinstance(records, list) else []

    # ------------------------------------------------------------------- table

    def _parse_html_table(self, soup, data_type: str, source: str) -> List[Dict[str, Any]]:
        target_table, header_row_index = self._find_snapshot_table(soup)
        if target_table is None:
            logger.warning("Could not find IEX Market Snapshot table with expected headers")
            return []

        rows = target_table.find_all("tr")
        bucket_names = self._bucket_names(rows, header_row_index)

        current_date = None
        result = []

        for tr in rows[header_row_index + 1:]:
            texts = [td.get_text(strip=True) for td in tr.find_all("td")]
            if not texts:
                continue

            # The only prefix cell that ever contains a colon is the time block, which makes
            # it a reliable anchor whatever combination of Date / Hour / Session ID precedes
            # it after the rowspans collapse. Sub-header rows have no such cell.
            block_index = next((i for i, text in enumerate(texts[:4]) if ":" in text), None)
            if block_index is None:
                continue

            if block_index and self._looks_like_date(texts[0]):
                current_date = self._parse_date_str(texts[0])
            if not current_date:
                logger.warning("Skipping row: Missing Date. Row text: %s", texts[:3])
                continue

            time_block = texts[block_index]
            nums = [self._safe_float_conversion(text) for text in texts[block_index + 1:]]

            width = len(bucket_names) if bucket_names else self._infer_bucket_width(len(nums))
            if width <= 0 or len(nums) != 2 + _TABLE_GROUP_COUNT * width:
                logger.warning("Skipping row: unexpected column count %d for time block %s", len(texts), time_block)
                continue

            names = bucket_names or [f"bucket_{i}" for i in range(width)]
            groups = {}
            for group_index, (field, _) in enumerate(_PAYLOAD_GROUPS):
                offset = 1 + group_index * width
                groups[field] = dict(zip(names, nums[offset:offset + width]))

            result.append(self._build_row(
                date=current_date,
                time_block=time_block,
                buy_bid=nums[0],
                groups=groups,
                mcp=nums[-1],
                data_type=data_type,
                source=source,
            ))

        return result

    def _find_snapshot_table(self, soup):
        for table in soup.find_all("table"):
            for index, row in enumerate(table.find_all("tr")[:5]):
                headers = [cell.get_text(strip=True) for cell in row.find_all(["th", "td"])]
                for signature in _HEADER_SIGNATURES:
                    if headers[:len(signature)] == signature:
                        return table, index
        return None, -1

    def _bucket_names(self, rows, header_row_index) -> List[str]:
        """Read the generation buckets (Total / Hydro / Wind / ...) off the sub-header row."""
        if header_row_index + 1 >= len(rows):
            return []

        labels = [cell.get_text(strip=True) for cell in rows[header_row_index + 1].find_all(["th", "td"])]
        if not labels or len(labels) % _TABLE_GROUP_COUNT:
            return []
        # Guard against a table with no sub-header row, where this would be the first data row.
        if any(self._looks_like_date(label) or self._is_numeric(label) for label in labels):
            return []

        width = len(labels) // _TABLE_GROUP_COUNT
        names = [self._normalize_bucket_label(label) for label in labels[:width]]
        return names if all(names) else []

    def _normalize_bucket_label(self, label: str) -> str:
        # "Other RE (MW)" and "Other-RE (MW)" both appear upstream and mean the same bucket.
        cleaned = re.sub(r"\(.*?\)", " ", label)
        return re.sub(r"[^a-z0-9]+", "_", cleaned.strip().lower()).strip("_")

    def _infer_bucket_width(self, num_count: int) -> int:
        # buy bid + three equally sized groups + MCP
        remainder = num_count - 2
        return remainder // _TABLE_GROUP_COUNT if remainder > 0 and remainder % _TABLE_GROUP_COUNT == 0 else 0

    def _looks_like_date(self, text: str) -> bool:
        return bool(re.match(r"^\d{1,4}[-/]\d{1,2}[-/]\d{2,4}$", text))

    def _is_numeric(self, text: str) -> bool:
        try:
            float(text)
            return True
        except (ValueError, TypeError):
            return False

    # -------------------------------------------------------------------- rows

    def _build_row(self, date, time_block, buy_bid, groups, mcp, data_type, source) -> Dict[str, Any]:
        now = datetime.now()
        return {
            "date": date,
            # Normalize "00:00 - 00:15" to the "00:00-00:15" form the serve layer keys on.
            "time_block": time_block.replace(" ", "") if time_block else time_block,
            "buy_bid": buy_bid,
            "sell_bid": groups["sell_bid"],
            "mcv": groups["mcv"],
            "final_scheduled_volume": groups["final_scheduled_volume"],
            "mcp": mcp,

            # Metadata
            "data_type": data_type,
            "source": source,
            "scraped_at": now,
            "createdAt": now,
            "updatedAt": now,
        }
