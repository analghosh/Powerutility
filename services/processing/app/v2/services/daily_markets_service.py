"""
Daily Markets Report service.

Ports mplive's `ReportsController@dailyReport`
(app/Http/Controllers/front/v1/Reports/ReportsController.php) onto EPMS serve
data. mplive read one pre-aggregated table, `bid_trade_volume_data`
(n2_acp / single_bid_quantum / block_bid_quantum / cleared_volume); the EPMS
equivalents are `bid_trade_serve` (bidded, traded — single and block bids as
separate documents per block) and `exchange_serve` (acp).

Everything on the summary panel below the volume rows is a *formula*, not
stored data — mplive computes fees, charges and FCR from cleared volume using
four hardcoded rates. Those rates live in settings (REPORTS_MARKETS_*) and are
echoed back on every response under `charge_rates` so a reader can see which
numbers the money rows were built from.

Deviations from mplive, all deliberate:
  * Blocks are numbered 1-96. mplive emits a 0-based `timeblock` on the detail
    rows while numbering everything else from 1.
  * `weighted_average_buy_price` is guarded on cleared buy volume. mplive
    guards on buy outgo instead and divides by a possibly-zero volume.
  * mplive's Annexures II and III (TAM and GTAM trade details) are not returned
    at all. EPMS has no Term-Ahead pipeline — no exchange API client, no live
    sync, and none of mplive's manually-entered TAM_*/GTAM_* limit values — so
    those sections could only ever have been empty.
"""

import asyncio
import csv
import io
import logging
import math
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from ...core.config import settings
from .parameter_service import REPORT_FEE_RATE_BASIS, parameter_service
from ..repository.daily_markets_repository import (
    BLOCKS_PER_DAY,
    LIMIT_VALUE_BUFFER_ID,
    LIMIT_VALUE_CEILING_PRICE_ID,
    LIMIT_VALUE_N_MINUS_1_ID,
    daily_markets_repository,
)

logger = logging.getLogger(__name__)

SUPPORTED_MARKETS = ("RTM", "DAM", "GDAM", "HPDAM")
DEFAULT_MARKET = "RTM"
DEFAULT_EXCHANGE = "IEX"

FORMAT_JSON = "json"
FORMAT_CSV = "csv"
FORMAT_BOTH = "both"
SUPPORTED_FORMATS = (FORMAT_JSON, FORMAT_CSV, FORMAT_BOTH)

# MW held for one 15-minute block -> million units: MW / 4 (MWh) / 1000 (MU).
MW_BLOCK_TO_MU = 4000.0
# MW held for one 15-minute block -> MWh.
MW_BLOCK_TO_MWH = 4.0

CSV_HEADERS = [
    "Delivery Date",
    "Block",
    "Trade Session",
    "From",
    "To",
    "Bid Qty (MW)",
    "Cleared Qty (MW)",
    "ACP (Rs./MWh)",
    "Amount (in INR)",
]


def _php_round(value: float, precision: int = 0) -> float:
    """
    Round half away from zero, the way PHP's round()/number_format() do.

    Python rounds half to even, so round(2.5) is 2 here and 3 in mplive. The
    report is meant to reproduce figures PTC already reads, so the rounding
    rule has to match rather than merely be defensible.
    """
    if value is None:
        return 0.0
    quantum = Decimal(1).scaleb(-precision)
    rounded = float(Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP))
    # Normalise -0.0; a money row rendering as "-0.00" reads as a defect.
    return rounded if rounded else 0.0


def _to_float(value: Any) -> float:
    """
    Coerce a serve value to float.

    exchange_serve types acp/mcp as `Union[float, Dict]` — the volume fields on
    that collection are per-fuel breakdowns — so a dict is read from `total`.
    """
    if value is None:
        return 0.0
    if isinstance(value, dict):
        value = value.get("total", 0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class DailyMarketsService:
    def __init__(self):
        self.repository = daily_markets_repository

    # ---------- validation ----------

    @staticmethod
    def normalize_market(market: Optional[str]) -> str:
        resolved = (market or DEFAULT_MARKET).strip().upper()
        if resolved not in SUPPORTED_MARKETS:
            raise ValueError(
                f"market must be one of {', '.join(SUPPORTED_MARKETS)}"
            )
        return resolved

    @staticmethod
    def normalize_format(response_format: Optional[str]) -> str:
        resolved = (response_format or FORMAT_JSON).strip().lower()
        if resolved not in SUPPORTED_FORMATS:
            raise ValueError(f"format must be one of {', '.join(SUPPORTED_FORMATS)}")
        return resolved

    @staticmethod
    def resolve_report_date(report_date: Optional[str]) -> str:
        if not report_date:
            return datetime.now().strftime("%Y-%m-%d")
        # Raises ValueError on a malformed date, which the route maps to 422.
        datetime.strptime(report_date, "%Y-%m-%d")
        return report_date

    @staticmethod
    def _iso_to_display_date(iso_date: str) -> str:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d-%m-%Y")

    # ---------- source maps ----------

    @staticmethod
    def _build_trade_map(rows: List[Dict[str, Any]]) -> Dict[int, Dict[str, float]]:
        trades: Dict[int, Dict[str, float]] = {}
        for row in rows:
            block = row.get("time_block")
            if block is None:
                continue
            trades[int(block)] = {
                "bidded": _to_float(row.get("bidded")),
                "traded": _to_float(row.get("traded")),
            }
        return trades

    @staticmethod
    def _build_price_map(rows: List[Dict[str, Any]]) -> Dict[int, float]:
        """
        Block -> area clearing price.

        exchange_serve falls back to mcp when acp is zero at ingest, so acp is
        the single price EPMS carries per market and exchange.
        """
        prices: Dict[int, float] = {}
        for row in rows:
            block = row.get("time_block")
            if block is None:
                continue
            prices[int(block)] = _to_float(row.get("acp"))
        return prices

    @staticmethod
    def _build_limit_map(
        rows: List[Dict[str, Any]],
    ) -> Dict[int, Dict[str, Optional[float]]]:
        limits: Dict[int, Dict[str, Optional[float]]] = {}
        for row in rows:
            block = row.get("block")
            limit_id = row.get("id")
            if block is None or limit_id is None:
                continue
            limits.setdefault(int(block), {})[limit_id] = _to_float(row.get("values"))
        return limits

    @staticmethod
    def _build_time_block_map(rows: List[Dict[str, Any]]) -> Dict[int, Tuple[str, str]]:
        blocks: Dict[int, Tuple[str, str]] = {}
        for row in rows:
            block = row.get("timeblock")
            if block is None:
                continue
            blocks[int(block)] = (
                str(row.get("starttime") or ""),
                str(row.get("endtime") or ""),
            )
        return blocks

    @staticmethod
    def _build_session_map(rows: List[Dict[str, Any]]) -> Dict[int, int]:
        sessions: Dict[int, int] = {}
        for row in rows:
            block = row.get("time_block")
            session = row.get("session_number")
            if block is None or session is None:
                continue
            sessions[int(block)] = int(session)
        return sessions

    @staticmethod
    def _fallback_block_window(block: int) -> Tuple[str, str]:
        """Clock window for a block when the time_block collection is empty."""

        def label(minutes: int) -> str:
            return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"

        return label((block - 1) * 15), label(block * 15)

    # ---------- report assembly ----------

    def _build_trade_details(
        self,
        report_date: str,
        trades: Dict[int, Dict[str, float]],
        prices: Dict[int, float],
        time_blocks: Dict[int, Tuple[str, str]],
        sessions: Dict[int, int],
    ) -> List[Dict[str, Any]]:
        display_date = self._iso_to_display_date(report_date)
        rows: List[Dict[str, Any]] = []
        for block in range(1, BLOCKS_PER_DAY + 1):
            trade = trades.get(block, {})
            bid_qty = trade.get("bidded", 0.0)
            cleared_qty = trade.get("traded", 0.0)
            acp = prices.get(block, 0.0)
            # mplive: $amount = (-1 * $rtm_cleare_vol * $rtm_mcp) * 0.25
            # Cleared volume is signed, so a buy (positive MW) produces a
            # negative amount (money out) and a sell produces a positive one.
            amount = -1 * cleared_qty * acp / MW_BLOCK_TO_MWH
            start, end = time_blocks.get(block) or self._fallback_block_window(block)
            rows.append(
                {
                    "delivery_date": display_date,
                    "block": block,
                    "trade_session": sessions.get(block, math.ceil(block / 2)),
                    "from": start,
                    "to": end,
                    "bid_qty": _php_round(bid_qty, 2),
                    "cleared_qty": _php_round(cleared_qty, 2),
                    "acp": _php_round(acp, 2),
                    "amount": _php_round(amount, 2),
                }
            )
        return rows

    def _build_other_details(
        self,
        report_date: str,
        limits: Dict[int, Dict[str, Optional[float]]],
        time_blocks: Dict[int, Tuple[str, str]],
    ) -> List[Dict[str, Any]]:
        display_date = self._iso_to_display_date(report_date)
        rows: List[Dict[str, Any]] = []
        for block in range(1, BLOCKS_PER_DAY + 1):
            limit_row = limits.get(block, {})
            start, end = time_blocks.get(block) or self._fallback_block_window(block)
            rows.append(
                {
                    "date": display_date,
                    "block": block,
                    "from": start,
                    "to": end,
                    "ceiling_price": limit_row.get(LIMIT_VALUE_CEILING_PRICE_ID),
                    "buffer_price": limit_row.get(LIMIT_VALUE_BUFFER_ID),
                    "n1_quantum": limit_row.get(LIMIT_VALUE_N_MINUS_1_ID),
                }
            )
        return rows

    def _build_summary(
        self,
        trade_details: List[Dict[str, Any]],
        rates: Dict[str, Any],
        block_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        mplive's RTM Trade Summary, formula for formula.

        Amounts are rounded per block before being accumulated because mplive
        accumulates `round($amount)`; summing first and rounding last drifts
        from the published figure by a few rupees a day.
        """
        fee_rate = float(rates["fee_rate_per_mwh"])
        gst_multiplier = float(rates["gst_multiplier"])
        sldc_flat = float(rates["sldc_flat_charge"])
        fcr_rate = float(rates["fcr_rate_per_mu"])

        total_amount = 0.0
        total_positive_amount = 0.0
        total_negative_amount = 0.0
        total_bid_vol = 0.0
        total_positive_bid_vol = 0.0
        total_negative_bid_vol = 0.0
        total_cleared_vol = 0.0
        total_positive_cleared_vol = 0.0
        total_negative_cleared_vol = 0.0
        acp_sum = 0.0

        for row in trade_details:
            amount = _php_round(row["amount"])
            bid_vol = row["bid_qty"]
            cleared_vol = row["cleared_qty"]

            total_amount += amount
            if amount > 0:
                total_positive_amount += amount
            elif amount < 0:
                total_negative_amount += amount

            total_bid_vol += bid_vol
            if bid_vol > 0:
                total_positive_bid_vol += bid_vol
            elif bid_vol < 0:
                total_negative_bid_vol += bid_vol

            total_cleared_vol += cleared_vol
            if cleared_vol > 0:
                total_positive_cleared_vol += cleared_vol
            elif cleared_vol < 0:
                total_negative_cleared_vol += cleared_vol

            acp_sum += row["acp"]

        buy_outgo = -total_negative_amount if total_negative_amount < 0 else 0.0
        sell_revenue = total_positive_amount if total_positive_amount > 0 else 0.0

        bid_buy_mu = (
            total_positive_bid_vol / MW_BLOCK_TO_MU if total_positive_bid_vol > 0 else 0.0
        )
        bid_sell_mu = (
            total_negative_bid_vol / -MW_BLOCK_TO_MU if total_negative_bid_vol < 0 else 0.0
        )
        cleared_buy_mu = (
            total_positive_cleared_vol / MW_BLOCK_TO_MU
            if total_positive_cleared_vol > 0
            else 0.0
        )
        cleared_sell_mu = (
            total_negative_cleared_vol / -MW_BLOCK_TO_MU
            if total_negative_cleared_vol < 0
            else 0.0
        )

        # Rs/MWh: the MU totals are divided back out by 1000 to land on MWh.
        weighted_average_sell_price = (
            total_positive_amount / (cleared_sell_mu * 1000)
            if total_positive_amount > 0 and cleared_sell_mu > 0
            else 0.0
        )
        weighted_average_buy_price = (
            buy_outgo / (cleared_buy_mu * 1000)
            if total_negative_amount < 0 and cleared_buy_mu > 0
            else 0.0
        )

        total_fcr = cleared_sell_mu * fcr_rate if cleared_sell_mu > 0 else 0.0

        buy_cost = buy_outgo + (total_positive_cleared_vol / MW_BLOCK_TO_MWH) * fee_rate
        sell_proceeds = (
            sell_revenue + (total_negative_cleared_vol / MW_BLOCK_TO_MWH) * fee_rate
        )
        # mplive computes this as `transmission_charges` but the daily screen
        # renders it in the "Fees (incl. GST)" row.
        fees_incl_gst = (
            -((total_cleared_vol / MW_BLOCK_TO_MWH) * fee_rate) * gst_multiplier
        )
        sldc_charges = sldc_flat if (bid_sell_mu > 0 or bid_buy_mu > 0) else 0.0

        # Verbatim from mplive. This adds outgo to revenue rather than netting
        # them, and subtracts a fee figure that is itself negative. Reproduced
        # as-is so the EPMS number matches the report PTC reads today.
        funds_pay_in_out = (buy_outgo + sell_revenue) - (fees_incl_gst + sldc_charges)

        # Over a multi-day period the denominator is the blocks in the whole
        # period, not one day's 96, or the average reads 7x high on a week.
        blocks = block_count or BLOCKS_PER_DAY
        average_acp = acp_sum / blocks if blocks else 0.0

        return {
            "funds_pay_in_out": _php_round(funds_pay_in_out, 2),
            "buy_outgo": _php_round(buy_outgo, 2),
            "sell_revenue": _php_round(sell_revenue, 2),
            "fees_incl_gst": _php_round(fees_incl_gst, 2),
            "sldc_scheduling_operating_charges": _php_round(sldc_charges, 2),
            "buy_cost": _php_round(buy_cost, 2),
            "sell_proceeds": _php_round(sell_proceeds, 2),
            "buy_bid_mu": _php_round(bid_buy_mu, 2),
            "buy_cleared_mu": _php_round(cleared_buy_mu, 2),
            "sell_bid_mu": _php_round(bid_sell_mu, 2),
            "sell_cleared_mu": _php_round(cleared_sell_mu, 2),
            "average_acp": _php_round(average_acp, 2),
            "weighted_average_sell_price": _php_round(weighted_average_sell_price, 2),
            "weighted_average_buy_price": _php_round(weighted_average_buy_price, 2),
            "total_fcr": _php_round(total_fcr, 2),
            "total_amount": _php_round(total_amount, 2),
        }

    @staticmethod
    def _build_coverage(
        trades: Dict[int, Dict[str, float]],
        prices: Dict[int, float],
        limits: Dict[int, Dict[str, Optional[float]]],
    ) -> Dict[str, Any]:
        """
        How many of the 96 blocks each source actually returned.

        A day with no trade data and a day of genuine zeros both render as
        zeros, so the counts are reported rather than left for the caller to
        infer from the numbers.
        """
        priced_blocks = sum(1 for value in prices.values() if value)
        return {
            "trade_blocks": len(trades),
            "price_blocks": len(prices),
            "priced_blocks_non_zero": priced_blocks,
            "limit_blocks": len(limits),
            "expected_blocks": BLOCKS_PER_DAY,
            "has_trade_data": bool(trades),
            "has_price_data": priced_blocks > 0,
        }

    # ---------- charge rates ----------

    async def _resolve_charge_rates(self) -> Dict[str, Any]:
        """
        Read the charge rates from `parameters`, falling back to the settings
        constants if that store cannot be reached at all.

        The parameter service already falls back per-key on a malformed value;
        this outer guard covers the whole store being unavailable, which would
        otherwise take every money row on the report to zero.
        """
        try:
            return await parameter_service.get_report_charge_rates()
        except Exception as e:
            logger.error(
                "Could not read report charge rates from parameters (%s); "
                "falling back to settings constants",
                e,
            )
            gst_multiplier = float(settings.REPORTS_MARKETS_GST_MULTIPLIER)
            return {
                "fee_rate_per_mwh": float(settings.REPORTS_MARKETS_FEE_RATE_PER_MWH),
                "gst_percent": (gst_multiplier - 1) * 100,
                "gst_multiplier": gst_multiplier,
                "sldc_flat_charge": float(settings.REPORTS_MARKETS_SLDC_FLAT_CHARGE),
                "fcr_rate_per_mu": float(settings.REPORTS_MARKETS_FCR_RATE_PER_MU),
                "sources": {"all": "settings_fallback"},
                "fee_rate_basis": REPORT_FEE_RATE_BASIS,
            }

    # ---------- entry point ----------

    async def get_daily_markets(
        self,
        report_date: Optional[str] = None,
        market: Optional[str] = None,
        exchange: Optional[str] = None,
        response_format: str = FORMAT_JSON,
    ) -> Dict[str, Any]:
        resolved_date = self.resolve_report_date(report_date)
        resolved_market = self.normalize_market(market)
        resolved_exchange = (exchange or DEFAULT_EXCHANGE).strip().upper()
        fmt = self.normalize_format(response_format)

        rates = await self._resolve_charge_rates()

        (
            trade_rows,
            price_rows,
            limit_rows,
            time_block_rows,
            session_rows,
        ) = await asyncio.gather(
            self.repository.find_trade_volumes_by_date(resolved_date, resolved_market),
            self.repository.find_area_prices_by_date(
                resolved_date, resolved_market, resolved_exchange
            ),
            self.repository.find_limit_values_by_date(resolved_date, resolved_market),
            self.repository.find_time_blocks(),
            self.repository.find_session_mapping(),
        )

        trades = self._build_trade_map(trade_rows)
        prices = self._build_price_map(price_rows)
        limits = self._build_limit_map(limit_rows)
        time_blocks = self._build_time_block_map(time_block_rows)
        sessions = self._build_session_map(session_rows)

        trade_details = self._build_trade_details(
            resolved_date, trades, prices, time_blocks, sessions
        )
        other_details = self._build_other_details(resolved_date, limits, time_blocks)
        summary = self._build_summary(trade_details, rates)

        csv_export: Dict[str, Any] = {
            "headers": CSV_HEADERS,
            "row_count": len(trade_details),
        }
        if fmt in (FORMAT_CSV, FORMAT_BOTH):
            csv_export["content"] = self.generate_csv(trade_details)
            csv_export["filename"] = self.build_csv_filename(
                resolved_date, resolved_market
            )

        return {
            "query": {
                "report_date": resolved_date,
                "market": resolved_market,
                "exchange": resolved_exchange,
                "format": fmt,
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
            "data_coverage": self._build_coverage(trades, prices, limits),
            "summary": summary,
            "trade_details": trade_details,
            "other_details": other_details,
            "csv_export": csv_export,
        }

    # ---------- csv ----------

    @staticmethod
    def build_csv_filename(report_date: str, market: str) -> str:
        return f"daily_markets_report_{market.lower()}_{report_date}.csv"

    def generate_csv(self, trade_details: List[Dict[str, Any]]) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(CSV_HEADERS)
        for row in trade_details:
            writer.writerow(
                [
                    row.get("delivery_date"),
                    row.get("block"),
                    row.get("trade_session"),
                    row.get("from"),
                    row.get("to"),
                    row.get("bid_qty"),
                    row.get("cleared_qty"),
                    row.get("acp"),
                    row.get("amount"),
                ]
            )
        return output.getvalue()


daily_markets_service = DailyMarketsService()
