"""
Reports Forecast service — compose DAF/IDF forecasts and market prices for CSV export.
"""

import asyncio
import csv
import io
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from libs.helpers.historical_date_split import (
    REPORTS_FORECAST_MAX_DATE_RANGE_DAYS,
    validate_max_date_range_days,
)
from libs.helpers.reports_forecast_format import (
    REPORTS_FORECAST_FORMAT_JSON,
    normalize_reports_forecast_format,
    reports_forecast_includes_csv_content,
    reports_forecast_includes_flat_records,
    reports_forecast_includes_json_sections,
)
from libs.models.forecast_serve_entity import (
    FORECAST_SOURCE_CSV,
    FORECAST_SOURCE_RTM_API,
)

from ..repository.demand_serve_repository import DemandServeRepository
from ..repository.forecast_serve_repository import forecast_serve_repository
from ..repository.reports_forecast_repository import reports_forecast_repository

logger = logging.getLogger(__name__)

CSV_HEADERS = [
    "Date",
    "Time Block",
    "IDF Demand",
    "DAF Demand",
    "IDF Price",
    "DAM Price",
    "GDAM Price",
    "HPDAM Price",
]

MARKET_DAM = "DAM"
MARKET_RTM = "RTM"
MARKET_GDAM = "GDAM"
MARKET_HPDAM = "HPDAM"


class ReportsForecastService:
    def __init__(self):
        self.demand_serve_repository = DemandServeRepository()
        self.forecast_serve_repository = forecast_serve_repository
        self.reports_forecast_repository = reports_forecast_repository

    @staticmethod
    def _resolve_date_range(
        schedule_date: Optional[str],
        from_date: Optional[str],
        to_date: Optional[str],
    ) -> Tuple[str, str]:
        if schedule_date and (from_date or to_date):
            raise ValueError(
                "Provide either schedule_date or both from_date and to_date, not both"
            )
        if schedule_date:
            return schedule_date, schedule_date
        if from_date and to_date:
            if from_date > to_date:
                raise ValueError("from_date must be less than or equal to to_date")
            return from_date, to_date
        raise ValueError("Provide schedule_date or both from_date and to_date")

    @staticmethod
    def _iter_dates(from_date: str, to_date: str) -> List[str]:
        start = datetime.strptime(from_date, "%Y-%m-%d")
        end = datetime.strptime(to_date, "%Y-%m-%d")
        dates: List[str] = []
        cursor = start
        while cursor <= end:
            dates.append(cursor.strftime("%Y-%m-%d"))
            cursor += timedelta(days=1)
        return dates

    @staticmethod
    def _date_str_from_scheduled_at(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d")
        text = str(value)
        return text[:10] if len(text) >= 10 else text

    @staticmethod
    def _iso_to_display_date(iso_date: str) -> str:
        """Convert YYYY-MM-DD to DD-MM-YYYY for report output."""
        parsed = datetime.strptime(iso_date, "%Y-%m-%d")
        return parsed.strftime("%d-%m-%Y")

    @staticmethod
    def _format_csv_value(value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    def _build_forecast_maps(
        self,
        forecast_docs: List[Dict[str, Any]],
    ) -> Tuple[Dict[Tuple[str, int], float], Dict[Tuple[str, int], float]]:
        auto: Dict[Tuple[str, int], float] = {}
        manual: Dict[Tuple[str, int], float] = {}
        for doc in forecast_docs:
            date_str = self._date_str_from_scheduled_at(doc.get("scheduled_at"))
            block = doc.get("time_block")
            if date_str is None or block is None:
                continue
            key = (date_str, int(block))
            forecast = doc.get("forecast")
            source = doc.get("source")
            if source == FORECAST_SOURCE_RTM_API and forecast is not None:
                auto[key] = forecast
            elif source == FORECAST_SOURCE_CSV and forecast is not None:
                manual[key] = forecast
        return auto, manual

    def _build_daf_map(
        self,
        daf_docs: List[Dict[str, Any]],
    ) -> Dict[Tuple[str, int], float]:
        daf: Dict[Tuple[str, int], float] = {}
        for doc in daf_docs:
            date_str = doc.get("date")
            block = doc.get("time_block")
            forecast = doc.get("forecast")
            if date_str is None or block is None or forecast is None:
                continue
            daf[(date_str, int(block))] = forecast
        return daf

    def _build_price_map(
        self,
        price_docs: List[Dict[str, Any]],
    ) -> Dict[Tuple[str, int], Dict[str, float]]:
        from ..repository.reports_forecast_repository import FORECAST_PRICE_IDS

        market_by_id = {v: k for k, v in FORECAST_PRICE_IDS.items()}
        prices: Dict[Tuple[str, int], Dict[str, float]] = {}
        for doc in price_docs:
            date_str = self._date_str_from_scheduled_at(doc.get("scheduled_at"))
            block = doc.get("block")
            market = doc.get("market") or market_by_id.get(doc.get("id"))
            value = doc.get("values")
            if date_str is None or block is None or market is None or value is None:
                continue
            key = (date_str, int(block))
            prices.setdefault(key, {})[market] = value
        return prices

    def _build_block_record(
        self,
        date_str: str,
        block: int,
        auto: Dict[Tuple[str, int], float],
        daf: Dict[Tuple[str, int], float],
        prices: Dict[Tuple[str, int], Dict[str, float]],
    ) -> Dict[str, Any]:
        key = (date_str, block)
        price_row = prices.get(key, {})
        return {
            "date": self._iso_to_display_date(date_str),
            "time_block": block,
            "idf_demand": auto.get(key),
            "daf_demand": daf.get(key),
            "idf_price": price_row.get(MARKET_RTM),
            "dam_price": price_row.get(MARKET_DAM),
            "gdam_price": price_row.get(MARKET_GDAM),
            "hpdam_price": price_row.get(MARKET_HPDAM),
        }

    def _build_by_date(
        self,
        from_date: str,
        to_date: str,
        from_block: int,
        to_block: int,
        auto: Dict[Tuple[str, int], float],
        daf: Dict[Tuple[str, int], float],
        prices: Dict[Tuple[str, int], Dict[str, float]],
    ) -> List[Dict[str, Any]]:
        by_date: List[Dict[str, Any]] = []
        for date_str in self._iter_dates(from_date, to_date):
            blocks: List[Dict[str, Any]] = []
            for block in range(from_block, to_block + 1):
                blocks.append(
                    self._build_block_record(date_str, block, auto, daf, prices)
                )
            by_date.append(
                {
                    "date": self._iso_to_display_date(date_str),
                    "block_count": len(blocks),
                    "blocks": blocks,
                }
            )
        return by_date

    def _build_report_rows(
        self,
        from_date: str,
        to_date: str,
        from_block: int,
        to_block: int,
        auto: Dict[Tuple[str, int], float],
        daf: Dict[Tuple[str, int], float],
        prices: Dict[Tuple[str, int], Dict[str, float]],
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for date_str in self._iter_dates(from_date, to_date):
            for block in range(from_block, to_block + 1):
                records.append(
                    self._build_block_record(date_str, block, auto, daf, prices)
                )
        return records

    @staticmethod
    def build_csv_filename(
        from_date: str,
        to_date: str,
        from_block: int,
        to_block: int,
    ) -> str:
        return (
            f"reports_forecast_{from_date}_to_{to_date}"
            f"_blocks_{from_block}-{to_block}.csv"
        )

    async def get_reports_forecast(
        self,
        from_block: int,
        to_block: int,
        schedule_date: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        response_format: str = REPORTS_FORECAST_FORMAT_JSON,
        include_flat_records: bool = True,
    ) -> Dict[str, Any]:
        if from_block > to_block:
            raise ValueError("from_block must be less than or equal to to_block")

        resolved_from, resolved_to = self._resolve_date_range(
            schedule_date, from_date, to_date
        )
        validate_max_date_range_days(resolved_from, resolved_to)
        fmt = normalize_reports_forecast_format(response_format)
        include_json = reports_forecast_includes_json_sections(fmt)
        include_records = reports_forecast_includes_flat_records(
            fmt,
            include_flat_records=include_flat_records,
        )
        include_csv_content = reports_forecast_includes_csv_content(fmt)

        daf_docs, forecast_docs, price_docs = await asyncio.gather(
            self.demand_serve_repository.find_daf_forecast_by_date_range(
                from_date=resolved_from,
                to_date=resolved_to,
                from_block=from_block,
                to_block=to_block,
            ),
            self.forecast_serve_repository.find_by_date_range_and_block_range(
                from_date=resolved_from,
                to_date=resolved_to,
                from_block=from_block,
                to_block=to_block,
            ),
            self.reports_forecast_repository.find_forecasted_prices_by_date_range(
                from_date=resolved_from,
                to_date=resolved_to,
                from_block=from_block,
                to_block=to_block,
            ),
        )

        auto, manual = self._build_forecast_maps(forecast_docs)
        daf = self._build_daf_map(daf_docs)
        prices = self._build_price_map(price_docs)

        dates = self._iter_dates(resolved_from, resolved_to)
        blocks_per_day = to_block - from_block + 1
        total_rows = len(dates) * blocks_per_day

        records: List[Dict[str, Any]] = []
        needs_flat_rows = include_records or include_json or include_csv_content
        if needs_flat_rows:
            records = self._build_report_rows(
                resolved_from,
                resolved_to,
                from_block,
                to_block,
                auto,
                daf,
                prices,
            )
            total_rows = len(records)

        csv_export: Dict[str, Any] = {
            "headers": CSV_HEADERS,
            "row_count": total_rows,
        }
        if include_csv_content and records:
            csv_export["content"] = self.generate_csv(records)
            csv_export["filename"] = self.build_csv_filename(
                resolved_from,
                resolved_to,
                from_block,
                to_block,
            )

        response: Dict[str, Any] = {
            "response_format": fmt,
            "query": {
                "from_date": resolved_from,
                "to_date": resolved_to,
                "from_block": from_block,
                "to_block": to_block,
                "format": fmt,
                "include_flat_records": (
                    include_flat_records if fmt == REPORTS_FORECAST_FORMAT_JSON else None
                ),
            },
            "summary": {
                "total_rows": total_rows,
            },
            "csv_export": csv_export,
        }

        if include_json:
            response["by_date"] = self._build_by_date(
                resolved_from,
                resolved_to,
                from_block,
                to_block,
                auto,
                daf,
                prices,
            )

        if include_records:
            response["records"] = records

        return response

    def generate_csv(self, records: List[Dict[str, Any]]) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(CSV_HEADERS)
        for row in records:
            writer.writerow(
                [
                    row.get("date"),
                    row.get("time_block"),
                    self._format_csv_value(row.get("idf_demand")),
                    self._format_csv_value(row.get("daf_demand")),
                    self._format_csv_value(row.get("idf_price")),
                    self._format_csv_value(row.get("dam_price")),
                    self._format_csv_value(row.get("gdam_price")),
                    self._format_csv_value(row.get("hpdam_price")),
                ]
            )
        return output.getvalue()


reports_forecast_service = ReportsForecastService()
