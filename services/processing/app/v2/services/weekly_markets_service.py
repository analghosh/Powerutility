"""
Weekly Markets Report service.

Ports mplive's `ReportsController@weeklyReport`, which is the daily report's
block loop run once per day across an ISO week and then totalled. This service
reuses the daily service's block assembly and summary maths rather than
restating them, so the two reports cannot drift apart.

Two deliberate departures from mplive:

  * Buy Cost and Sell Proceeds are computed, not read. mplive's weekly reads
    them from limit_values (category "Report", ids Buy_Cost / Sell_Proceeds) -
    values an operator typed in by hand. EPMS holds none of those, so those
    rows plus the Transmission Charge derived from them would render empty.
    Computing them the way the daily report already does fills all three, and
    makes the weekly and daily figures agree for the same day.

  * The Total column is recomputed from raw block volumes, not summed from the
    seven day columns. Day columns are rounded to 2dp for display, and the
    weighted average prices divide by those MU figures; summing rounded values
    moved the weekly weighted average by ~45 Rs/MWh on a two-day week.
"""

import asyncio
import csv
import io
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .daily_markets_service import (
    DEFAULT_EXCHANGE,
    FORMAT_BOTH,
    FORMAT_CSV,
    FORMAT_JSON,
    daily_markets_service,
)
from ..repository.daily_markets_repository import BLOCKS_PER_DAY

logger = logging.getLogger(__name__)

DAYS_PER_WEEK = 7
MIN_ISO_WEEK = 1
MAX_ISO_WEEK = 53

CSV_HEADERS = [
    "Particulars",
    "UoM",
    *[f"Day{i}" for i in range(1, DAYS_PER_WEEK + 1)],
    "Total",
]

# (row label, unit, summary key). Ordered as the legacy weekly screen renders.
SUMMARY_ROWS: Tuple[Tuple[str, str, str], ...] = (
    ("Funds Pay in (+)/Pay out (-)", "INR", "funds_pay_in_out"),
    ("Buy Outgo", "INR", "buy_outgo"),
    ("Sell Revenue", "INR", "sell_revenue"),
    ("Transmission Charge", "INR", "fees_incl_gst"),
    ("SLDC Scheduling and Operating Charges", "INR", "sldc_scheduling_operating_charges"),
    ("Buy Cost", "INR", "buy_cost"),
    ("Sell Proceeds", "INR", "sell_proceeds"),
    ("Average ACP", "Rs/MWh", "average_acp"),
    ("Wt. Average Buy Price", "Rs/MWh", "weighted_average_buy_price"),
    ("Wt. Average Sell Price", "Rs/MWh", "weighted_average_sell_price"),
    ("Total FCR", "INR", "total_fcr"),
)

# Rendered as "bid || cleared" in one cell, the way the daily report does.
PAIR_ROWS: Tuple[Tuple[str, str, str, str], ...] = (
    ("BUY: Bid || Cleared", "MUs", "buy_bid_mu", "buy_cleared_mu"),
    ("SELL: Bid || Cleared", "MUs", "sell_bid_mu", "sell_cleared_mu"),
)


class WeeklyMarketsService:
    def __init__(self):
        self.daily = daily_markets_service
        self.repository = daily_markets_service.repository

    # ---------- validation ----------

    @staticmethod
    def resolve_week(year: Optional[int], week: Optional[int]) -> Tuple[int, int]:
        """Default to the ISO week containing today when either part is absent."""
        if year is None or week is None:
            today = date.today().isocalendar()
            return (year or today[0]), (week or today[1])
        if not MIN_ISO_WEEK <= week <= MAX_ISO_WEEK:
            raise ValueError(f"week must be between {MIN_ISO_WEEK} and {MAX_ISO_WEEK}")
        try:
            date.fromisocalendar(year, week, 1)
        except ValueError as e:
            # Week 53 does not exist in every year.
            raise ValueError(f"{year} has no ISO week {week}") from e
        return year, week

    @staticmethod
    def week_dates(year: int, week: int) -> List[str]:
        monday = date.fromisocalendar(year, week, 1)
        return [
            (monday + timedelta(days=offset)).strftime("%Y-%m-%d")
            for offset in range(DAYS_PER_WEEK)
        ]

    # ---------- per-day assembly ----------

    async def _build_day(
        self,
        report_date: str,
        market: str,
        exchange: str,
        time_blocks: Dict[int, Tuple[str, str]],
        sessions: Dict[int, int],
    ) -> Dict[str, Any]:
        """
        One day's 96 blocks, assembled with the daily service's own helpers so
        the weekly figures are the daily figures.
        """
        trade_rows, price_rows = await asyncio.gather(
            self.repository.find_trade_volumes_by_date(report_date, market),
            self.repository.find_area_prices_by_date(report_date, market, exchange),
        )
        trades = self.daily._build_trade_map(trade_rows)
        prices = self.daily._build_price_map(price_rows)
        details = self.daily._build_trade_details(
            report_date, trades, prices, time_blocks, sessions
        )
        return {
            "date": report_date,
            "details": details,
            "has_trade_data": bool(trades),
            "price_blocks": sum(1 for v in prices.values() if v),
        }

    # ---------- report assembly ----------

    def _build_rows(
        self,
        day_summaries: List[Dict[str, Any]],
        total_summary: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for label, uom, key in SUMMARY_ROWS:
            row: Dict[str, Any] = {"particulars": label, "uom": uom}
            for index, summary in enumerate(day_summaries, start=1):
                row[f"day{index}"] = summary[key]
            row["total"] = total_summary[key]
            rows.append(row)

        for label, uom, bid_key, cleared_key in PAIR_ROWS:
            row = {"particulars": label, "uom": uom}
            for index, summary in enumerate(day_summaries, start=1):
                row[f"day{index}"] = f"{summary[bid_key]:.2f} || {summary[cleared_key]:.2f}"
            row["total"] = (
                f"{total_summary[bid_key]:.2f} || {total_summary[cleared_key]:.2f}"
            )
            rows.append(row)
        return rows

    async def get_weekly_markets(
        self,
        year: Optional[int] = None,
        week: Optional[int] = None,
        market: Optional[str] = None,
        exchange: Optional[str] = None,
        response_format: str = FORMAT_JSON,
    ) -> Dict[str, Any]:
        resolved_year, resolved_week = self.resolve_week(year, week)
        resolved_market = self.daily.normalize_market(market)
        resolved_exchange = (exchange or DEFAULT_EXCHANGE).strip().upper()
        fmt = self.daily.normalize_format(response_format)
        dates = self.week_dates(resolved_year, resolved_week)

        rates, time_block_rows, session_rows = await asyncio.gather(
            self.daily._resolve_charge_rates(),
            self.repository.find_time_blocks(),
            self.repository.find_session_mapping(),
        )
        time_blocks = self.daily._build_time_block_map(time_block_rows)
        sessions = self.daily._build_session_map(session_rows)

        days = await asyncio.gather(
            *[
                self._build_day(d, resolved_market, resolved_exchange, time_blocks, sessions)
                for d in dates
            ]
        )

        day_summaries = [
            self.daily._build_summary(day["details"], rates) for day in days
        ]
        # Totals come from every block in the week at full precision. Summing the
        # day columns would carry their 2dp rounding into the weighted averages.
        all_details = [block for day in days for block in day["details"]]
        total_summary = self.daily._build_summary(
            all_details, rates, block_count=BLOCKS_PER_DAY * DAYS_PER_WEEK
        )
        total_summary = self._apply_per_day_charges(total_summary, day_summaries)

        rows = self._build_rows(day_summaries, total_summary)

        csv_export: Dict[str, Any] = {
            "headers": CSV_HEADERS,
            "row_count": len(rows),
        }
        if fmt in (FORMAT_CSV, FORMAT_BOTH):
            csv_export["content"] = self.generate_csv(rows)
            csv_export["filename"] = self.build_csv_filename(
                resolved_year, resolved_week, resolved_market
            )

        return {
            "query": {
                "year": resolved_year,
                "week": resolved_week,
                "market": resolved_market,
                "exchange": resolved_exchange,
                "format": fmt,
                "from_date": dates[0],
                "to_date": dates[-1],
            },
            "charge_rates": {
                "fee_rate_per_mwh": rates["fee_rate_per_mwh"],
                "gst_percent": rates["gst_percent"],
                "gst_multiplier": rates["gst_multiplier"],
                "sldc_flat_charge": rates["sldc_flat_charge"],
                "fcr_rate_per_mu": rates["fcr_rate_per_mu"],
                "sources": rates["sources"],
                "fee_rate_basis": rates["fee_rate_basis"],
            },
            "days": [
                {
                    "day": index,
                    "date": day["date"],
                    "has_trade_data": day["has_trade_data"],
                    "price_blocks": day["price_blocks"],
                }
                for index, day in enumerate(days, start=1)
            ],
            "data_coverage": {
                "days_with_trade_data": sum(1 for d in days if d["has_trade_data"]),
                "expected_days": DAYS_PER_WEEK,
                "expected_blocks": BLOCKS_PER_DAY * DAYS_PER_WEEK,
            },
            "rows": rows,
            "summary": total_summary,
            "csv_export": csv_export,
        }

    @staticmethod
    def _apply_per_day_charges(
        total_summary: Dict[str, Any],
        day_summaries: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Correct the two totals that are not sums of block-level values.

        SLDC is a flat charge per trading day, so a week where bids were placed
        on two days owes it twice. Computing the totals from the week's blocks in
        one pass applies it once, which also understated Funds Pay in/out by the
        same amount, since that row subtracts SLDC. Everything else stays as
        computed from raw blocks.
        """
        corrected = dict(total_summary)
        sldc_total = sum(
            day["sldc_scheduling_operating_charges"] for day in day_summaries
        )
        corrected["sldc_scheduling_operating_charges"] = round(sldc_total, 2)
        corrected["funds_pay_in_out"] = round(
            (corrected["buy_outgo"] + corrected["sell_revenue"])
            - (corrected["fees_incl_gst"] + sldc_total),
            2,
        )
        return corrected

    # ---------- csv ----------

    @staticmethod
    def build_csv_filename(year: int, week: int, market: str) -> str:
        return f"weekly_markets_report_{market.lower()}_{year}_W{week:02d}.csv"

    def generate_csv(self, rows: List[Dict[str, Any]]) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(CSV_HEADERS)
        for row in rows:
            writer.writerow(
                [row.get("particulars"), row.get("uom")]
                + [row.get(f"day{i}") for i in range(1, DAYS_PER_WEEK + 1)]
                + [row.get("total")]
            )
        return output.getvalue()


weekly_markets_service = WeeklyMarketsService()
