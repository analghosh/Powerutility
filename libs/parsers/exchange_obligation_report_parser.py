"""
Exchange Daily Obligation Report (DOR) parser.

Parses the "DAILY OBLIGATION SUMMARY REPORT" PDF that power exchanges
(IEX / PXIL / HPX) publish per portfolio per delivery day, for the DAM, RTM
and G-DAM markets.

The PDF is a Crystal Reports text document, so every value is extractable
text - no OCR is involved. Three shapes exist:

    DAM   page 1 obligation summary, page 2 "Daily Trade Report"
          (96 blocks laid out in two side-by-side columns of 48)
    RTM   same as DAM, but the header carries no Trading Date and the
          remarks note the session the figures were computed up to
    GDAM  page 1 obligation summary, page 2 "G-DAM Purchase Daily Trade
          Report" (Annexure A, same two-column shape), pages 3-4
          "G-DAM Sell (Wind, Hydro, Other-RE, DRE) Daily Trade Report"
          (Annexure B, one row per block, four quantity columns)

Time blocks are derived from each row's own period text rather than from its
position on the page, so a change in column widths or row order cannot shift
a value onto the wrong block.
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber

from libs.parsers.base_parser import BaseParser


# --------------------------------------------------------------------------
# Report / market vocabulary
# --------------------------------------------------------------------------

REPORT_TYPE_DOR = "daily_obligation_report"

MARKET_DAM = "DAM"
MARKET_RTM = "RTM"
MARKET_GDAM = "GDAM"
MARKET_HPDAM = "HPDAM"

# Filenames the exchange ships, e.g.
#   IEX180926DOR_PTC0136_PY0_Electricity Department Pud.pdf
#   GDAM_IEX180926DOR_PTC0136_PY0_Electricity Department Pud.pdf
#   RTM_IEX180926DOR_PTC0136_PY0_Electricity Department Pud.pdf
# The market prefix is absent for DAM.
FILENAME_RE = re.compile(
    r"^(?:(?P<market>G-?DAM|HP-?DAM|RTM|DAM|TAM)_)?"
    r"(?P<exchange>IEX|PXIL|HPX)"
    r"(?P<date>\d{6})"
    r"(?P<report_code>[A-Z]{2,5})"
    r"_(?P<portfolio>[A-Z0-9]+)"
    r"_(?P<region>[A-Z0-9]+)"
    r"(?:_(?P<portfolio_name>.*))?$",
    re.IGNORECASE,
)

# A money / quantity token: optional sign, thousands separators, 2+ decimals,
# and the trailing '*' the exchange stamps on a capped charge.
NUM = r"-?[\d,]+\.\d+\*?"

PERIOD = r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})"

# One (period, qty, rate, amount) cell of the DAM/RTM/G-DAM-purchase table.
# findall() returns two of these per printed line, one per page column.
TRADE_CELL_RE = re.compile(
    rf"{PERIOD}\s+({NUM})\s+({NUM})\s+({NUM})"
)

# One row of the G-DAM sell annexure: four quantities, then rate and amount.
SELL_ROW_RE = re.compile(
    rf"{PERIOD}\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})"
)

# Charge lines are printed as "> <label> <value>".
CHARGE_LINE_RE = re.compile(rf"^>\s*(?P<label>.+?)\s+(?P<value>{NUM})$")

# Obligation-summary labels, mapped to the field each one is stored under.
CHARGE_FIELDS = {
    "nldc application fees": "nldc_application_fees",
    "ctu transmission charges": "ctu_transmission_charges",
    "nldc scheduling & operating charges - buy": "nldc_scheduling_operating_charges_buy",
    "nldc scheduling & operating charges - sell": "nldc_scheduling_operating_charges_sell",
    "stu transmission charges": "stu_transmission_charges",
    "distribution charges": "distribution_charges",
    "any other charges": "any_other_charges",
    "sldc scheduling and operating charges": "sldc_scheduling_operating_charges",
    "aldc scheduling and operating charges": "aldc_scheduling_operating_charges",
}

TAX_FIELDS = {
    "igst": "igst",
    "sgst": "sgst",
    "cgst": "cgst",
    "utgst": "utgst",
}

BLOCKS_PER_DAY = 96
BLOCK_MINUTES = 15
# A block is 15 minutes, so MWh = MW / 4.
MW_TO_MWH = 0.25

# Sign convention, established against the 18-Sep-26 IEX reports: a positive
# quantity is a buy and a negative quantity is a sell, while the amount column
# carries the opposite sign because the report states funds as
# "Payin(-) / Payout(+)". The proof is in the charge lines - the NLDC
# Scheduling & Operating (Buy) charge equals the rupee rate times the MWh of
# the positive quantities alone, to the paisa, on all three markets.
SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
SIDE_NIL = "NIL"


class ObligationReportParseError(ValueError):
    """Raised when the PDF is not a Daily Obligation Report we can trust."""


# --------------------------------------------------------------------------
# Scalar helpers
# --------------------------------------------------------------------------

def _to_number(token: str) -> float:
    """'-1,819.85*' -> -1819.85. The '*' only flags a capped charge."""
    return float(token.replace(",", "").replace("*", "").strip())


def _is_capped(token: str) -> bool:
    return token.rstrip().endswith("*")


def _time_to_minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _block_from_period(start: str, end: str) -> int:
    """
    Derive the 1-based time block from the printed period.

    '00:00 - 00:15' is block 1 and '23:45 - 24:00' is block 96. The block comes
    from the period itself, never from where the row sits on the page.
    """
    start_minutes = _time_to_minutes(start)
    end_minutes = _time_to_minutes(end)
    if end_minutes - start_minutes != BLOCK_MINUTES:
        raise ObligationReportParseError(
            f"period '{start} - {end}' is not a {BLOCK_MINUTES}-minute block"
        )
    if start_minutes % BLOCK_MINUTES:
        raise ObligationReportParseError(
            f"period '{start} - {end}' does not start on a block boundary"
        )
    return start_minutes // BLOCK_MINUTES + 1


def _to_iso_date(value: Optional[str]) -> Optional[str]:
    """
    '19-Sep-26' -> '2026-09-19'.

    The report prints a two-digit year, so the century is assumed to be 2000s;
    these reports did not exist before 2008 and the format gives nothing else
    to go on.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%d-%b-%y").date().isoformat()
    except ValueError:
        return None


def _side_of(qty_mw: float) -> str:
    if qty_mw > 0:
        return SIDE_BUY
    if qty_mw < 0:
        return SIDE_SELL
    return SIDE_NIL


def _normalise_market(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    key = token.upper().replace("-", "")
    return {
        "GDAM": MARKET_GDAM,
        "HPDAM": MARKET_HPDAM,
        "RTM": MARKET_RTM,
        "DAM": MARKET_DAM,
        "TAM": "TAM",
    }.get(key)


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

class ExchangeObligationReportParser(BaseParser):
    """Parses a Daily Obligation Report PDF into one storable document."""

    def get_parser_type(self) -> str:
        return "exchange_obligation_report_pdf"

    # BaseParser speaks in strings; the PDF entry point is parse_pdf().
    def parse(self, content: str, provider=None, **kwargs) -> List[Dict[str, Any]]:
        raise NotImplementedError(
            "Daily Obligation Reports are binary PDFs; call parse_pdf() instead"
        )

    def parse_pdf(
        self,
        data: bytes,
        filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Parse the report from PDF bytes."""
        return self.parse_pages(self._extract_pages(data), filename=filename)

    def parse_pages(
        self,
        pages: List[str],
        filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Parse the report from already-extracted page text.

        Split out from parse_pdf so the field, table and validation logic can
        be exercised on fixture text without shipping a client PDF as a test
        fixture.
        """
        if not any(page.strip() for page in pages):
            raise ObligationReportParseError(
                "no extractable text in PDF - the file is probably a scan"
            )

        text = "\n".join(pages)

        if "DAILY OBLIGATION SUMMARY REPORT" not in text.upper():
            raise ObligationReportParseError(
                "not a Daily Obligation Summary Report - expected heading not found"
            )

        filename_meta = parse_filename(filename) if filename else {}

        header = self._parse_header(text)
        summary = self._parse_summary(text)
        remarks = self._parse_remarks(text)

        purchase_rows, purchase_total = self._parse_trade_table(text)
        sell_rows, sell_total = self._parse_sell_annexure(text)

        market = (
            _normalise_market(filename_meta.get("market"))
            or self._detect_market(text, header)
        )

        document: Dict[str, Any] = {
            "report_type": REPORT_TYPE_DOR,
            "market_type": market,
            "detected_market_type": self._detect_market(text, header),
            "exchange": (filename_meta.get("exchange") or "").upper() or None,
            "source_filename": filename,
            "filename_metadata": filename_meta,
            "page_count": len(pages),
            **header,
            "obligation_summary": summary,
            "remarks": remarks,
            "trade_blocks": purchase_rows,
            "trade_totals": purchase_total,
        }

        if sell_rows:
            document["sell_blocks"] = sell_rows
            document["sell_totals"] = sell_total

        document["validation"] = self._validate(document)
        return document

    # -- extraction --------------------------------------------------------

    @staticmethod
    def _extract_pages(data: bytes) -> List[str]:
        pages: List[str] = []
        try:
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for page in pdf.pages:
                    pages.append(page.extract_text(layout=True) or "")
        except ObligationReportParseError:
            raise
        except Exception as exc:
            # A corrupt, truncated or non-PDF upload surfaces here as a
            # pdfminer error; the caller needs one exception type to map to a
            # 400, not the internals of the PDF library.
            raise ObligationReportParseError(
                f"could not read the PDF: {exc}"
            ) from exc
        return pages

    # -- header ------------------------------------------------------------

    @staticmethod
    def _field(text: str, label: str) -> Optional[str]:
        """
        Read 'Label : value' out of the header.

        Header lines pack two fields side by side, so the value stops at the
        next label or at a run of whitespace wide enough to be a column gap.
        """
        match = re.search(
            rf"{re.escape(label)}\s*:\s*(?P<value>\S(?:[^\s:]|\s(?!\s))*)",
            text,
        )
        if not match:
            return None
        return re.sub(r"\s+", " ", match.group("value")).strip()

    def _parse_header(self, text: str) -> Dict[str, Any]:
        trading_date = self._field(text, "Trading Date")
        delivery_date = self._field(text, "Delivery Date")
        return {
            "trading_date": trading_date,
            "trading_date_iso": _to_iso_date(trading_date),
            "delivery_date": delivery_date,
            "delivery_date_iso": _to_iso_date(delivery_date),
            "entity_id": self._field(text, "Entity ID"),
            "entity_name": self._field(text, "Entity Name"),
            "portfolio_code": self._field(text, "Portfolio Code"),
            "portfolio_name": self._field(text, "Portfolio Name"),
            "authorised_signatory": self._parse_signatory(text),
        }

    def _parse_signatory(self, text: str) -> Dict[str, Any]:
        """
        Read the signatory block at the foot of the last page.

        'Name' is scoped to the text after 'Authorised Signatory' because the
        header above it also carries an 'Entity Name' field.
        """
        marker = re.search(r"Authorised\s+Signatory", text)
        section = text[marker.end():] if marker else text
        return {
            "name": self._field(section, "Name"),
            "designation": self._field(section, "Designation"),
        }

    # -- obligation summary ------------------------------------------------

    def _parse_summary(self, text: str) -> Dict[str, Any]:
        charges: Dict[str, Any] = {}
        taxes: Dict[str, Any] = {}
        capped: List[str] = []
        unmapped: List[Dict[str, Any]] = []

        for raw_line in text.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line.startswith(">"):
                continue
            match = CHARGE_LINE_RE.match(line)
            if not match:
                continue
            label = match.group("label").strip().lower()
            token = match.group("value")
            value = _to_number(token)

            if label in CHARGE_FIELDS:
                field = CHARGE_FIELDS[label]
                charges[field] = value
            elif label in TAX_FIELDS:
                field = TAX_FIELDS[label]
                taxes[field] = value
            else:
                unmapped.append({"label": match.group("label").strip(), "value": value})
                continue

            if _is_capped(token):
                capped.append(field)

        summary: Dict[str, Any] = {
            "funds_payin_payout": self._labelled_amount(text, r"Funds\s+Payin\(-\)\s*/\s*Payout\(\+\)"),
            "charges": charges,
            "fees": self._labelled_amount(text, r"^\s*Fees\b"),
            "taxes": taxes,
            "total": self._labelled_amount(text, r"\bTotal\b(?!\s+Trade)"),
            "max_charges_applied_to": capped,
        }
        if unmapped:
            summary["unmapped_lines"] = unmapped
        return summary

    @staticmethod
    def _labelled_amount(text: str, label_pattern: str) -> Optional[float]:
        match = re.search(rf"{label_pattern}\s+({NUM})", text, re.MULTILINE)
        return _to_number(match.group(1)) if match else None

    # -- remarks -----------------------------------------------------------

    @staticmethod
    def _parse_remarks(text: str) -> Dict[str, Any]:
        flat = re.sub(r"\s+", " ", text)
        remarks: Dict[str, Any] = {}

        patterns = {
            "nldc_application_fee_pool": r"NLDC Application Fee = . ([\d,]+\.\d+)",
            "successful_portfolios": r"No of Successful Portfolios\s*-\s*(\d+)",
            "injection_ctu_rate": r"Injection CTU Transmission Charges = . ([\d,]+\.\d+)",
            "drawal_ctu_rate": r"Drawal CTU Transmission Charges = . ([\d,]+\.\d+)",
            "nldc_so_buy_rate": r"NLDC Scheduling & Operating Charges - Buy = . ([\d,]+\.\d+)",
            "nldc_so_sell_rate": r"NLDC Scheduling & Operating Charges - Sell = . ([\d,]+\.\d+)",
            "computed_upto_session": r"calculated upto session (\d+)",
        }
        for field, pattern in patterns.items():
            match = re.search(pattern, flat)
            if not match:
                continue
            token = match.group(1)
            remarks[field] = int(token) if token.isdigit() else _to_number(token)

        remarks["max_charges_applied"] = "* Max Charges applied" in flat
        return remarks

    # -- trade tables ------------------------------------------------------

    def _parse_trade_table(self, text: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Read the DAM / RTM / G-DAM-purchase block table.

        The page prints blocks 1-48 and 49-96 side by side, so each printed
        line yields two cells; the period on each cell decides its block.
        """
        # The sell annexure has its own six-column shape and is parsed
        # separately - drop it first so its rows cannot leak in here.
        purchase_text = self._strip_sell_annexure(text)

        rows: Dict[int, Dict[str, Any]] = {}
        for line in purchase_text.splitlines():
            for start, end, qty, rate, amount in TRADE_CELL_RE.findall(line):
                block = _block_from_period(start, end)
                if block in rows:
                    raise ObligationReportParseError(
                        f"time block {block} ({start} - {end}) appears twice"
                    )
                qty_mw = _to_number(qty)
                rows[block] = {
                    "time_block": block,
                    "period_start": start,
                    "period_end": end,
                    "qty_mw": qty_mw,
                    "side": _side_of(qty_mw),
                    "rate_per_mwh": _to_number(rate),
                    "amount": _to_number(amount),
                }

        ordered = [rows[block] for block in sorted(rows)]
        totals = self._parse_trade_total(purchase_text)
        totals.update(self._derive_leg_totals(ordered))
        return ordered, totals

    @staticmethod
    def _strip_sell_annexure(text: str) -> str:
        marker = re.search(r"Sell \(Wind, Hydro, Other-RE, DRE\)", text)
        return text[: marker.start()] if marker else text

    @staticmethod
    def _derive_leg_totals(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Split the day into its buy and sell legs.

        The report prints one gross MWh figure for both sides added together,
        which is what the NLDC charges are billed on, so the legs have to be
        derived from the per-block signs to be available at all.
        """
        buy_mw = sum(row["qty_mw"] for row in rows if row["qty_mw"] > 0)
        sell_mw = sum(-row["qty_mw"] for row in rows if row["qty_mw"] < 0)
        return {
            "buy_mwh": round(buy_mw * MW_TO_MWH, 4),
            "sell_mwh": round(sell_mw * MW_TO_MWH, 4),
            "gross_mwh": round((buy_mw + sell_mw) * MW_TO_MWH, 4),
            "net_mwh": round((buy_mw - sell_mw) * MW_TO_MWH, 4),
        }

    @staticmethod
    def _parse_trade_total(text: str) -> Dict[str, Any]:
        match = re.search(
            rf"Total Trade \((?P<side>[^)]+)\) MWh\s+({NUM})\s+({NUM})",
            text,
        )
        if not match:
            return {}
        return {
            "side": match.group("side").strip(),
            "total_mwh": _to_number(match.group(2)),
            "total_amount": _to_number(match.group(3)),
        }

    def _parse_sell_annexure(self, text: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        marker = re.search(r"Sell \(Wind, Hydro, Other-RE, DRE\)", text)
        if not marker:
            return [], {}

        section = text[marker.start():]
        rows: Dict[int, Dict[str, Any]] = {}
        for line in section.splitlines():
            match = SELL_ROW_RE.search(line)
            if not match:
                continue
            start, end, wind, hydro, other_re, dre, rate, amount = match.groups()
            block = _block_from_period(start, end)
            if block in rows:
                raise ObligationReportParseError(
                    f"sell annexure time block {block} appears twice"
                )
            rows[block] = {
                "time_block": block,
                "period_start": start,
                "period_end": end,
                "wind_qty_mw": _to_number(wind),
                "hydro_qty_mw": _to_number(hydro),
                "other_re_qty_mw": _to_number(other_re),
                "dre_qty_mw": _to_number(dre),
                "rate_per_mwh": _to_number(rate),
                "amount": _to_number(amount),
            }

        totals: Dict[str, Any] = {}
        total_match = re.search(
            rf"Total Trade \(Sell\) MWh\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})",
            section,
        )
        if total_match:
            totals = {
                "side": "Sell",
                "wind_total_mwh": _to_number(total_match.group(1)),
                "hydro_total_mwh": _to_number(total_match.group(2)),
                "other_re_total_mwh": _to_number(total_match.group(3)),
                "dre_total_mwh": _to_number(total_match.group(4)),
                "total_amount": _to_number(total_match.group(5)),
            }
        return [rows[block] for block in sorted(rows)], totals

    # -- market detection --------------------------------------------------

    @staticmethod
    def _detect_market(text: str, header: Dict[str, Any]) -> Optional[str]:
        """
        Infer the market from the document body.

        G-DAM names itself in its annexure headings. RTM is the only shape
        without a Trading Date, and it is also the only one whose remarks
        name the session the figures were computed up to.
        """
        if "G-DAM" in text or "G-DAM Purchase" in text:
            return MARKET_GDAM
        if re.search(r"calculated upto session", text) or not header.get("trading_date"):
            return MARKET_RTM
        return MARKET_DAM

    # -- validation --------------------------------------------------------

    @staticmethod
    def _validate(document: Dict[str, Any]) -> Dict[str, Any]:
        """
        Cross-check the parsed numbers against the totals the PDF prints.

        Every check compares extracted rows to a figure printed elsewhere in
        the same document, so a silent extraction slip shows up as a failed
        check rather than as plausible-looking data.
        """
        checks: List[Dict[str, Any]] = []

        def check(name: str, ok: bool, detail: str = "") -> None:
            checks.append({"check": name, "passed": bool(ok), "detail": detail})

        blocks = document.get("trade_blocks") or []
        check(
            "trade_blocks_count",
            len(blocks) == BLOCKS_PER_DAY,
            f"{len(blocks)} of {BLOCKS_PER_DAY} blocks",
        )
        check(
            "trade_blocks_contiguous",
            [row["time_block"] for row in blocks] == list(range(1, len(blocks) + 1)),
            "time blocks run 1..n without gaps",
        )

        totals = document.get("trade_totals") or {}
        if blocks and "total_mwh" in totals:
            # The printed total is the GROSS volume - buy and sell MWh added,
            # not netted - which is what the NLDC charges are billed on.
            computed = round(totals.get("gross_mwh", 0.0), 4)
            printed = round(totals["total_mwh"], 4)
            check(
                "trade_total_mwh_matches_rows",
                abs(computed - printed) <= 0.01,
                f"gross_rows={computed} printed={printed}",
            )
        if blocks and "total_amount" in totals:
            computed = round(sum(row["amount"] for row in blocks), 2)
            printed = round(totals["total_amount"], 2)
            check(
                "trade_total_amount_matches_rows",
                abs(computed - printed) <= 1.0,
                f"rows={computed} printed={printed}",
            )

        summary = document.get("obligation_summary") or {}
        funds = summary.get("funds_payin_payout")
        if funds is not None and "total_amount" in totals:
            check(
                "funds_matches_trade_total",
                abs(funds - totals["total_amount"]) <= 1.0,
                f"funds={funds} trade_total={totals['total_amount']}",
            )

        if funds is not None and summary.get("total") is not None:
            deductions = sum((summary.get("charges") or {}).values())
            deductions += sum((summary.get("taxes") or {}).values())
            fees = summary.get("fees") or 0.0
            computed = round(funds + deductions + fees, 2)
            printed = round(summary["total"], 2)
            check(
                "summary_total_matches_components",
                abs(computed - printed) <= 1.0,
                f"funds+charges+fees+taxes={computed} printed={printed}",
            )

        # The NLDC Scheduling & Operating charges are billed at a flat rupee
        # rate per MWh on each leg separately, so they are an independent
        # witness that the per-block buy/sell signs were read correctly. A
        # charge the exchange capped (flagged with '*') cannot be tied back.
        remarks = document.get("remarks") or {}
        charges = summary.get("charges") or {}
        capped = set(summary.get("max_charges_applied_to") or [])
        for leg, rate_field, charge_field in (
            ("buy_mwh", "nldc_so_buy_rate", "nldc_scheduling_operating_charges_buy"),
            ("sell_mwh", "nldc_so_sell_rate", "nldc_scheduling_operating_charges_sell"),
        ):
            rate = remarks.get(rate_field)
            charged = charges.get(charge_field)
            if rate is None or charged is None or leg not in totals:
                continue
            if charge_field in capped:
                check(
                    f"nldc_charge_capped:{leg}",
                    True,
                    f"exchange capped this charge at {charged}; not tied to volume",
                )
                continue
            expected = round(totals[leg] * rate, 2)
            check(
                f"nldc_charge_matches_{leg}",
                abs(abs(charged) - expected) <= 0.05,
                f"{leg}={totals[leg]} x {rate} = {expected} vs charged {charged}",
            )

        sell_blocks = document.get("sell_blocks")
        if sell_blocks is not None:
            check(
                "sell_blocks_count",
                len(sell_blocks) == BLOCKS_PER_DAY,
                f"{len(sell_blocks)} of {BLOCKS_PER_DAY} blocks",
            )
            sell_totals = document.get("sell_totals") or {}
            for field, total_field in (
                ("wind_qty_mw", "wind_total_mwh"),
                ("hydro_qty_mw", "hydro_total_mwh"),
                ("other_re_qty_mw", "other_re_total_mwh"),
                ("dre_qty_mw", "dre_total_mwh"),
            ):
                if total_field not in sell_totals:
                    continue
                computed = round(sum(row[field] for row in sell_blocks) * MW_TO_MWH, 4)
                printed = round(sell_totals[total_field], 4)
                check(
                    f"sell_total_matches_rows:{field}",
                    abs(computed - printed) <= 0.01,
                    f"rows={computed} printed={printed}",
                )

        declared = document.get("market_type")
        detected = document.get("detected_market_type")
        check(
            "market_type_consistent",
            declared == detected,
            f"filename={declared} content={detected}",
        )

        return {
            "passed": all(entry["passed"] for entry in checks),
            "checks": checks,
        }


def parse_filename(filename: str) -> Dict[str, Any]:
    """
    Pull the metadata the exchange encodes in the report filename.

    'GDAM_IEX180926DOR_PTC0136_PY0_Electricity Department Pud.pdf' carries the
    market, exchange, trading date (ddMMyy), report code, portfolio and region.
    """
    stem = re.sub(r"\.pdf$", "", filename.strip(), flags=re.IGNORECASE)
    match = FILENAME_RE.match(stem)
    if not match:
        return {"parsed": False, "stem": stem}

    parts = match.groupdict()
    raw_date = parts.get("date") or ""
    meta: Dict[str, Any] = {
        "parsed": True,
        "market": _normalise_market(parts.get("market")) or MARKET_DAM,
        "market_from_filename": bool(parts.get("market")),
        "exchange": (parts.get("exchange") or "").upper(),
        "report_code": (parts.get("report_code") or "").upper(),
        "portfolio_ref": parts.get("portfolio"),
        "region_code": parts.get("region"),
        "portfolio_name_fragment": parts.get("portfolio_name"),
        "date_token": raw_date,
    }
    if len(raw_date) == 6:
        meta["date_iso"] = f"20{raw_date[4:6]}-{raw_date[2:4]}-{raw_date[0:2]}"
    return meta
