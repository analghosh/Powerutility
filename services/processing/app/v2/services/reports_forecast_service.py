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
    split_date_range_by_today,
    today_date_str,
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
    FORECAST_SOURCE_DISPLAY_AUTO,
    FORECAST_SOURCE_DISPLAY_MANUAL,
    FORECAST_SOURCE_RTM_API,
)

from ..repository.demand_serve_repository import DemandServeRepository
from ..repository.forecast_serve_repository import forecast_serve_repository
from ..repository.reports_forecast_repository import (
    FORECAST_PRICE_IDS,
    reports_forecast_repository,
)

logger = logging.getLogger(__name__)

CSV_HEADERS = [
    "Date",
    "Time Block",
    "Auto Forecast",
    "Manual Forecast",
    "Day Ahead Forecast(DAF)",
    "Market(DAM/RTM)",
    "RTM Forecasted Price",
    "DAM Forecasted Price",
    "GDAM Forecasted Price",
    "HPDAM Forecasted Price",
]

MARKET_DAM = "DAM"
MARKET_RTM = "RTM"


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

    def _build_by_date(
        self,
        from_date: str,
        to_date: str,
        from_block: int,
        to_block: int,
        auto: Dict[Tuple[str, int], float],
        manual: Dict[Tuple[str, int], float],
        daf: Dict[Tuple[str, int], float],
        prices: Dict[Tuple[str, int], Dict[str, float]],
    ) -> List[Dict[str, Any]]:
        by_date: List[Dict[str, Any]] = []
        for date_str in self._iter_dates(from_date, to_date):
            blocks: List[Dict[str, Any]] = []
            for block in range(from_block, to_block + 1):
                key = (date_str, block)
                price_row = prices.get(key, {})
                blocks.append(
                    {
                        "time_block": block,
                        "idf_forecast": {
                            "auto": auto.get(key),
                            "manual": manual.get(key),
                        },
                        "daf_forecast": daf.get(key),
                        "dam": {
                            "day_ahead_forecast_daf": daf.get(key),
                            "forecasted_prices": {
                                "dam": price_row.get(MARKET_DAM),
                                "gdam": price_row.get("GDAM"),
                                "hpdam": price_row.get("HPDAM"),
                            },
                        },
                        "rtm": {
                            "idf_forecast": {
                                "auto": auto.get(key),
                                "manual": manual.get(key),
                            },
                            "forecasted_prices": {
                                "rtm": price_row.get(MARKET_RTM),
                            },
                        },
                    }
                )
            by_date.append(
                {
                    "date": date_str,
                    "block_count": len(blocks),
                    "blocks": blocks,
                }
            )
        return by_date

    def _build_dam_market_records(
        self,
        records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        dam_records = []
        for row in records:
            if row.get("market") != MARKET_DAM:
                continue
            dam_records.append(
                {
                    "date": row["date"],
                    "time_block": row["time_block"],
                    "idf_forecast": {
                        "auto": row.get("auto_forecast"),
                        "manual": row.get("manual_forecast"),
                    },
                    "day_ahead_forecast_daf": row.get("day_ahead_forecast_daf"),
                    "forecasted_prices": {
                        "dam": row.get("dam_forecasted_price"),
                        "gdam": row.get("gdam_forecasted_price"),
                        "hpdam": row.get("hpdam_forecasted_price"),
                    },
                }
            )
        return dam_records

    def _build_rtm_market_records(
        self,
        records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        rtm_records = []
        for row in records:
            if row.get("market") != MARKET_RTM:
                continue
            rtm_records.append(
                {
                    "date": row["date"],
                    "time_block": row["time_block"],
                    "idf_forecast": {
                        "auto": row.get("auto_forecast"),
                        "manual": row.get("manual_forecast"),
                    },
                    "forecasted_prices": {
                        "rtm": row.get("rtm_forecasted_price"),
                    },
                }
            )
        return rtm_records

    @staticmethod
    def _count_forecast_docs_by_source(
        forecast_docs: List[Dict[str, Any]],
    ) -> Tuple[int, int]:
        auto_count = 0
        manual_count = 0
        for doc in forecast_docs:
            source = doc.get("source")
            if source == FORECAST_SOURCE_RTM_API:
                auto_count += 1
            elif source == FORECAST_SOURCE_CSV:
                manual_count += 1
        return auto_count, manual_count

    def _build_report_rows(
        self,
        from_date: str,
        to_date: str,
        from_block: int,
        to_block: int,
        auto: Dict[Tuple[str, int], float],
        manual: Dict[Tuple[str, int], float],
        daf: Dict[Tuple[str, int], float],
        prices: Dict[Tuple[str, int], Dict[str, float]],
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for date_str in self._iter_dates(from_date, to_date):
            for block in range(from_block, to_block + 1):
                key = (date_str, block)
                price_row = prices.get(key, {})
                auto_f = auto.get(key)
                manual_f = manual.get(key)
                daf_f = daf.get(key)

                records.append(
                    {
                        "date": date_str,
                        "time_block": block,
                        "auto_forecast": auto_f,
                        "manual_forecast": manual_f,
                        "day_ahead_forecast_daf": daf_f,
                        "market": MARKET_DAM,
                        "rtm_forecasted_price": None,
                        "dam_forecasted_price": price_row.get(MARKET_DAM),
                        "gdam_forecasted_price": price_row.get("GDAM"),
                        "hpdam_forecasted_price": price_row.get("HPDAM"),
                    }
                )
                records.append(
                    {
                        "date": date_str,
                        "time_block": block,
                        "auto_forecast": auto_f,
                        "manual_forecast": manual_f,
                        "day_ahead_forecast_daf": None,
                        "market": MARKET_RTM,
                        "rtm_forecasted_price": price_row.get(MARKET_RTM),
                        "dam_forecasted_price": None,
                        "gdam_forecasted_price": None,
                        "hpdam_forecasted_price": None,
                    }
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
        total_rows = len(dates) * blocks_per_day * 2
        dam_rows = len(dates) * blocks_per_day
        rtm_rows = dam_rows

        records: List[Dict[str, Any]] = []
        needs_flat_rows = include_records or include_json or include_csv_content
        if needs_flat_rows:
            records = self._build_report_rows(
                resolved_from,
                resolved_to,
                from_block,
                to_block,
                auto,
                manual,
                daf,
                prices,
            )
            total_rows = len(records)
            dam_rows = sum(1 for row in records if row.get("market") == MARKET_DAM)
            rtm_rows = sum(1 for row in records if row.get("market") == MARKET_RTM)

        auto_fetched, manual_fetched = self._count_forecast_docs_by_source(forecast_docs)
        today = today_date_str()
        past_from, past_to, current_from, current_to = split_date_range_by_today(
            resolved_from, resolved_to, today
        )
        historical_routing = {
            "today": today,
            "past_dates_from": past_from,
            "past_dates_to": past_to,
            "current_dates_from": current_from,
            "current_dates_to": current_to,
        }

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
                "date_count": len(dates),
                "blocks_per_day": blocks_per_day,
                "rows_per_day": blocks_per_day * 2,
                "max_date_range_days": REPORTS_FORECAST_MAX_DATE_RANGE_DAYS,
                "format": fmt,
                "include_flat_records": include_flat_records if fmt == REPORTS_FORECAST_FORMAT_JSON else None,
            },
            "sources": {
                "daf_forecast": {
                    "collection": "demand_serve",
                    "historical_collection": "demand_serve_historical",
                    "routing": historical_routing,
                    "type": "DAF",
                    "field": "forecast",
                    "records_fetched": len(daf_docs),
                },
                "idf_forecast": {
                    "collection": "forecast_serve",
                    "historical_collection": "forecast_serve_historical",
                    "routing": historical_routing,
                    "type": "IDF",
                    "revision_policy": "latest forecast_revision per source per day",
                    "auto": {
                        "source": FORECAST_SOURCE_RTM_API,
                        "forecast_source": FORECAST_SOURCE_DISPLAY_AUTO,
                        "records_fetched": auto_fetched,
                    },
                    "manual": {
                        "source": FORECAST_SOURCE_CSV,
                        "forecast_source": FORECAST_SOURCE_DISPLAY_MANUAL,
                        "records_fetched": manual_fetched,
                    },
                },
                "forecasted_prices": {
                    "collection": "limit_values",
                    "historical_collection": "limit_values_historical",
                    "routing": historical_routing,
                    "category": "Limits",
                    "markets": list(FORECAST_PRICE_IDS.keys()),
                    "price_ids": list(FORECAST_PRICE_IDS.values()),
                    "records_fetched": len(price_docs),
                },
            },
            "summary": {
                "total_rows": total_rows,
                "dam_rows": dam_rows,
                "rtm_rows": rtm_rows,
                "dates": dates,
            },
            "csv_export": csv_export,
        }

        if include_json:
            dam_records = self._build_dam_market_records(records) if records else []
            rtm_records = self._build_rtm_market_records(records) if records else []
            response["markets"] = {
                "dam": {
                    "market": MARKET_DAM,
                    "label": "Day Ahead Market",
                    "description": (
                        "DAF demand forecast with DAM, GDAM, and HPDAM forecasted prices"
                    ),
                    "count": len(dam_records),
                    "records": dam_records,
                },
                "rtm": {
                    "market": MARKET_RTM,
                    "label": "Real Time Market",
                    "description": (
                        "IDF auto and manual forecasts with RTM forecasted price"
                    ),
                    "count": len(rtm_records),
                    "records": rtm_records,
                },
            }
            response["by_date"] = self._build_by_date(
                resolved_from,
                resolved_to,
                from_block,
                to_block,
                auto,
                manual,
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
                    self._format_csv_value(row.get("auto_forecast")),
                    self._format_csv_value(row.get("manual_forecast")),
                    self._format_csv_value(row.get("day_ahead_forecast_daf")),
                    row.get("market"),
                    self._format_csv_value(row.get("rtm_forecasted_price")),
                    self._format_csv_value(row.get("dam_forecasted_price")),
                    self._format_csv_value(row.get("gdam_forecasted_price")),
                    self._format_csv_value(row.get("hpdam_forecasted_price")),
                ]
            )
        return output.getvalue()


reports_forecast_service = ReportsForecastService()
