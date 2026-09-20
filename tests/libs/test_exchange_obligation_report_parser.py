"""
Tests for the exchange Daily Obligation Report parser.

The fixtures are synthetic page text in the exact layout the exchange's
Crystal Reports PDFs produce, rather than real report PDFs, so no client
portfolio data or signatory name lands in the repository. They are built by
the same rules the real pages follow: 96 blocks printed as two side-by-side
columns of 48, comma-grouped money, a '*' on a capped charge, and a gross MWh
total that adds the buy and sell legs instead of netting them.
"""

import pytest

from libs.parsers.exchange_obligation_report_parser import (
    ExchangeObligationReportParser,
    ObligationReportParseError,
    parse_filename,
)


# --------------------------------------------------------------------------
# Fixture builders
# --------------------------------------------------------------------------

def _period(block: int) -> str:
    """Block 1 prints as '00:00 - 00:15'; block 96 ends at '24:00'."""
    start = (block - 1) * 15
    end = block * 15
    return f"{start // 60:02d}:{start % 60:02d} - {end // 60:02d}:{end % 60:02d}"


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _summary_page(
    *,
    trading_date: str | None,
    delivery_date: str,
    funds: float,
    nldc_fee: float,
    so_buy: float,
    so_sell: str,
    fees: float,
    igst: float,
    total: float,
    extra_remarks: str = "",
) -> str:
    trading_line = (
        f"        Trading Date                      :   {trading_date}"
        f"                             Delivery Date        : {delivery_date}"
        if trading_date
        else f"        Delivery Date                     :   {delivery_date}"
    )
    return f"""                                                                              REPORT - I
                                    DAILY OBLIGATION SUMMARY REPORT

{trading_line}
        Entity ID                         :   N2DL0PTC0000              Entity Name          : Test Trader Ltd.
        Portfolio Code                    :   S2PY0PTC0136              Portfolio Name       : Test_Portfolio_One

                Funds Payin(-) / Payout(+)                                    {_money(funds)}
                Charges
                         > NLDC Application Fees                              {_money(nldc_fee)}
                         > CTU Transmission Charges                                    0.00
                         > NLDC Scheduling & Operating Charges - Buy           {_money(so_buy)}
                         > NLDC Scheduling & Operating Charges - Sell          {so_sell}
                         > STU Transmission Charges                                    0.00
                         > Distribution Charges                                        0.00
                         > Any other Charges                                           0.00
                         > SLDC Scheduling and Operating Charges                       0.00
                         > ALDC Scheduling and Operating Charges                       0.00
                Fees                                                          {_money(fees)}
                         > IGST                                                {_money(igst)}
                         > SGST                                                        0.00
                         > CGST                                                        0.00
                         > UTGST                                                       0.00
                                                   Total                      {_money(total)}

       Remarks :
       - NLDC Application Fee = ` 5000.00 / (No of Successful Portfolios - 879 ).
       - Injection CTU Transmission Charges = ` 0.00/ Rs./MW/Timeblock.
       - Drawal CTU Transmission Charges = ` 122.55/ Rs./MW/Timeblock.
       - NLDC Scheduling & Operating Charges - Buy = ` 1.00 x (Total Traded Buy Qty in MWh)
       NLDC Scheduling & Operating Charges - Sell = ` 1.00 x (Total Traded Sell Qty in MWh)
       * Max Charges applied
{extra_remarks}
** This is a computer generated report.                                        Page 1 of 2
"""


def _trade_page(blocks: dict[int, tuple[float, float, float]], side_label: str) -> str:
    """
    Render the two-column block table.

    Blocks 1-48 are printed in the left column and 49-96 alongside them in the
    right, which is what makes position-based column slicing unsafe and block
    numbers derived from the printed period necessary.
    """
    lines = [
        "                                    DAILY OBLIGATION SUMMARY REPORT",
        "",
        "                                              Daily Trade Report",
        "",
        "         Period     Qty in MW    Rate/MWh     Amount in `        Period      Qty in MW    Rate/MWh    Amount in `",
    ]
    gross_mw = 0.0
    total_amount = 0.0
    for left in range(1, 49):
        right = left + 48
        cells = []
        for block in (left, right):
            qty, rate, amount = blocks.get(block, (0.0, 10000.0, 0.0))
            gross_mw += abs(qty)
            total_amount += amount
            cells.append(
                f"{_period(block)}   {_money(qty):>10}  {_money(rate):>11}  {_money(amount):>14}"
            )
        lines.append("       " + cells[0] + " " + cells[1])

    lines.append(
        f"                              Total Trade ({side_label}) MWh"
        f"          {gross_mw / 4:,.4f}                  {_money(total_amount)}"
    )
    lines += [
        "",
        "                                                        Authorised Signatory",
        "",
        "                                                        Name            :      Test Signatory",
        "                                                        Designation     :      ED Market Operations",
        "",
        "** This is a computer generated report.                              Page 2 of 2",
    ]
    return "\n".join(lines)


def _sell_annexure_pages(blocks: dict[int, tuple[float, float, float, float, float, float]]) -> list[str]:
    """Annexure B: one row per block, four quantity columns, split over two pages."""
    header = (
        "                                    DAILY OBLIGATION SUMMARY REPORT\n"
        "\n"
        "                        G-DAM Sell (Wind, Hydro, Other-RE, DRE) Daily Trade Report\n"
        "            Period      Wind Qty (MW)  Hydro Qty (MW)  Other-RE Qty (MW)  DRE Qty (MW)   Rate/MWh   Amount in\n"
    )
    rows: list[str] = []
    totals = [0.0, 0.0, 0.0, 0.0]
    total_amount = 0.0
    for block in range(1, 97):
        wind, hydro, other_re, dre, rate, amount = blocks.get(
            block, (0.0, 0.0, 0.0, 0.0, 10000.0, 0.0)
        )
        for index, value in enumerate((wind, hydro, other_re, dre)):
            totals[index] += value
        total_amount += amount
        rows.append(
            f"      {_period(block)}    {_money(wind):>10} {_money(hydro):>10}"
            f" {_money(other_re):>12} {_money(dre):>10} {rate:>12.2f} {_money(amount):>14}"
        )

    first = header + "\n".join(rows[:60]) + "\n\n** This is a computer generated report.   Page 3 of 4"
    tail = (
        "                                    DAILY OBLIGATION SUMMARY REPORT\n"
        "            Period      Wind Qty (MW)  Hydro Qty (MW)  Other-RE Qty (MW)  DRE Qty (MW)   Rate/MWh   Amount in\n"
        + "\n".join(rows[60:])
        + f"\n      Total Trade (Sell) MWh   {totals[0] / 4:,.4f} {totals[1] / 4:,.4f}"
        f" {totals[2] / 4:,.4f} {totals[3] / 4:,.4f} {_money(total_amount)}\n"
        "\n"
        "                                                        Authorised Signatory\n"
        "                                                        Name            :      Test Signatory\n"
        "                                                        Designation     :      ED Market Operations\n"
        "\n** This is a computer generated report.   Page 4 of 4"
    )
    return [first, tail]


# A day with one buy block and one sell block, so the legs are distinguishable.
# Buy is +20 MW for a block = 5 MWh, charged at ` 1.00/MWh = ` 5.00.
# Sell is -40 MW for a block = 10 MWh, charged at ` 1.00/MWh = ` 10.00.
DAM_BLOCKS = {
    10: (20.0, 3000.0, -15000.0),
    60: (-40.0, 4000.0, 40000.0),
}
DAM_FUNDS = 25000.0
DAM_SUMMARY = dict(
    funds=DAM_FUNDS,
    nldc_fee=-5.69,
    so_buy=-5.00,
    so_sell="-10.00",
    fees=-131.25,
    igst=-23.63,
    total=DAM_FUNDS + (-5.69) + (-5.00) + (-10.00) + (-131.25) + (-23.63),
)


@pytest.fixture
def parser() -> ExchangeObligationReportParser:
    return ExchangeObligationReportParser()


@pytest.fixture
def dam_pages() -> list[str]:
    return [
        _summary_page(trading_date="18-Sep-26", delivery_date="19-Sep-26", **DAM_SUMMARY),
        _trade_page(DAM_BLOCKS, "Buy + Sell"),
    ]


@pytest.fixture
def rtm_pages() -> list[str]:
    return [
        _summary_page(
            trading_date=None,
            delivery_date="18-Sep-26",
            extra_remarks="         *Obligation,Charges and Fees calculated upto session 48.",
            **DAM_SUMMARY,
        ),
        _trade_page(DAM_BLOCKS, "Buy + Sell"),
    ]


@pytest.fixture
def gdam_pages() -> list[str]:
    buy_only = {10: (20.0, 3000.0, -15000.0)}
    summary = dict(DAM_SUMMARY)
    summary["funds"] = -15000.0
    summary["so_sell"] = "0.00*"
    summary["total"] = -15000.0 + (-5.69) + (-5.00) + (-131.25) + (-23.63)
    return [
        _summary_page(trading_date="18-Sep-26", delivery_date="19-Sep-26", **summary),
        _trade_page(buy_only, "Buy").replace("Daily Trade Report", "G-DAM Purchase Daily Trade Report"),
        *_sell_annexure_pages({20: (5.0, 0.0, 0.0, 0.0, 2500.0, 3125.0)}),
    ]


# --------------------------------------------------------------------------
# Header, summary and remarks
# --------------------------------------------------------------------------

def test_dam_header_fields(parser, dam_pages):
    doc = parser.parse_pages(dam_pages, filename="IEX180926DOR_PTC0136_PY0_Test.pdf")

    assert doc["trading_date"] == "18-Sep-26"
    assert doc["delivery_date"] == "19-Sep-26"
    assert doc["entity_id"] == "N2DL0PTC0000"
    assert doc["entity_name"] == "Test Trader Ltd."
    assert doc["portfolio_code"] == "S2PY0PTC0136"
    assert doc["portfolio_name"] == "Test_Portfolio_One"


def test_signatory_is_read_from_the_signature_block_not_the_entity_name(parser, dam_pages):
    """'Entity Name' sits above the signatory block and must not win the match."""
    doc = parser.parse_pages(dam_pages)

    assert doc["authorised_signatory"] == {
        "name": "Test Signatory",
        "designation": "ED Market Operations",
    }


def test_obligation_summary_charges_and_taxes(parser, dam_pages):
    summary = parser.parse_pages(dam_pages)["obligation_summary"]

    assert summary["funds_payin_payout"] == DAM_FUNDS
    assert summary["charges"]["nldc_application_fees"] == -5.69
    assert summary["charges"]["nldc_scheduling_operating_charges_buy"] == -5.00
    assert summary["charges"]["stu_transmission_charges"] == 0.00
    assert summary["taxes"] == {"igst": -23.63, "sgst": 0.0, "cgst": 0.0, "utgst": 0.0}
    assert summary.get("unmapped_lines") is None


def test_capped_charge_is_flagged_and_its_asterisk_stripped(parser, gdam_pages):
    summary = parser.parse_pages(gdam_pages)["obligation_summary"]

    assert summary["charges"]["nldc_scheduling_operating_charges_sell"] == 0.0
    assert "nldc_scheduling_operating_charges_sell" in summary["max_charges_applied_to"]


def test_remarks_carry_the_rates_the_charges_are_billed_at(parser, dam_pages):
    remarks = parser.parse_pages(dam_pages)["remarks"]

    assert remarks["successful_portfolios"] == 879
    assert remarks["drawal_ctu_rate"] == 122.55
    assert remarks["nldc_so_buy_rate"] == 1.0
    assert remarks["max_charges_applied"] is True


def test_rtm_records_the_session_the_figures_stop_at(parser, rtm_pages):
    doc = parser.parse_pages(rtm_pages)

    assert doc["remarks"]["computed_upto_session"] == 48


# --------------------------------------------------------------------------
# Block table
# --------------------------------------------------------------------------

def test_all_96_blocks_are_read_in_order(parser, dam_pages):
    blocks = parser.parse_pages(dam_pages)["trade_blocks"]

    assert len(blocks) == 96
    assert [row["time_block"] for row in blocks] == list(range(1, 97))
    assert blocks[0]["period_start"] == "00:00"
    assert blocks[-1]["period_end"] == "24:00"


def test_block_number_comes_from_the_period_not_the_column(parser, dam_pages):
    """
    Block 49 is printed beside block 1, so a position-based reader would put
    the right-hand column's values on the wrong blocks.
    """
    blocks = {row["time_block"]: row for row in parser.parse_pages(dam_pages)["trade_blocks"]}

    assert blocks[10]["qty_mw"] == 20.0
    assert blocks[10]["rate_per_mwh"] == 3000.0
    assert blocks[60]["qty_mw"] == -40.0
    assert blocks[60]["amount"] == 40000.0
    assert blocks[49]["period_start"] == "12:00"


def test_sign_of_the_quantity_sets_the_side(parser, dam_pages):
    blocks = {row["time_block"]: row for row in parser.parse_pages(dam_pages)["trade_blocks"]}

    assert blocks[10]["side"] == "BUY"
    assert blocks[60]["side"] == "SELL"
    assert blocks[1]["side"] == "NIL"


def test_printed_total_is_gross_volume_and_the_legs_are_derived(parser, dam_pages):
    totals = parser.parse_pages(dam_pages)["trade_totals"]

    assert totals["side"] == "Buy + Sell"
    assert totals["buy_mwh"] == 5.0
    assert totals["sell_mwh"] == 10.0
    # The exchange prints buy and sell added, not netted.
    assert totals["gross_mwh"] == 15.0
    assert totals["total_mwh"] == 15.0
    assert totals["net_mwh"] == -5.0


# --------------------------------------------------------------------------
# G-DAM sell annexure
# --------------------------------------------------------------------------

def test_gdam_sell_annexure_is_read_across_both_its_pages(parser, gdam_pages):
    doc = parser.parse_pages(gdam_pages, filename="GDAM_IEX180926DOR_PTC0136_PY0_Test.pdf")
    sell = {row["time_block"]: row for row in doc["sell_blocks"]}

    assert len(doc["sell_blocks"]) == 96
    assert sell[20]["wind_qty_mw"] == 5.0
    assert sell[20]["rate_per_mwh"] == 2500.0
    assert sell[96]["period_end"] == "24:00"
    assert doc["sell_totals"]["wind_total_mwh"] == 1.25


def test_sell_annexure_rows_do_not_leak_into_the_purchase_table(parser, gdam_pages):
    doc = parser.parse_pages(gdam_pages)
    purchase = {row["time_block"]: row for row in doc["trade_blocks"]}

    assert len(doc["trade_blocks"]) == 96
    assert purchase[20]["qty_mw"] == 0.0
    assert doc["trade_totals"]["side"] == "Buy"


# --------------------------------------------------------------------------
# Market detection
# --------------------------------------------------------------------------

def test_market_is_detected_from_the_body_when_the_filename_says_nothing(parser, dam_pages):
    doc = parser.parse_pages(dam_pages, filename="report.pdf")

    assert doc["detected_market_type"] == "DAM"


def test_rtm_is_recognised_by_its_missing_trading_date(parser, rtm_pages):
    doc = parser.parse_pages(rtm_pages, filename="report.pdf")

    assert doc["trading_date"] is None
    assert doc["detected_market_type"] == "RTM"


def test_gdam_is_recognised_by_its_annexure_headings(parser, gdam_pages):
    doc = parser.parse_pages(gdam_pages, filename="report.pdf")

    assert doc["detected_market_type"] == "GDAM"


def test_a_filename_naming_the_wrong_market_fails_validation(parser, dam_pages):
    """A DAM report uploaded under a G-DAM name must not be stored as G-DAM."""
    doc = parser.parse_pages(dam_pages, filename="GDAM_IEX180926DOR_PTC0136_PY0_Test.pdf")

    assert doc["market_type"] == "GDAM"
    assert doc["detected_market_type"] == "DAM"
    assert doc["validation"]["passed"] is False
    failed = [c["check"] for c in doc["validation"]["checks"] if not c["passed"]]
    assert failed == ["market_type_consistent"]


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def test_a_clean_report_passes_every_check(parser, dam_pages):
    validation = parser.parse_pages(
        dam_pages, filename="IEX180926DOR_PTC0136_PY0_Test.pdf"
    )["validation"]

    assert validation["passed"] is True
    names = {check["check"] for check in validation["checks"]}
    assert {
        "trade_blocks_count",
        "trade_blocks_contiguous",
        "trade_total_mwh_matches_rows",
        "trade_total_amount_matches_rows",
        "funds_matches_trade_total",
        "summary_total_matches_components",
        "nldc_charge_matches_buy_mwh",
        "nldc_charge_matches_sell_mwh",
    } <= names


def test_nldc_charge_ties_the_buy_leg_back_to_the_billed_rate(parser, dam_pages):
    """
    The charge lines are billed per MWh on each leg, so they independently
    confirm the per-block signs: 5 MWh bought at ` 1.00 is a ` 5.00 charge.
    """
    checks = {
        c["check"]: c for c in parser.parse_pages(dam_pages)["validation"]["checks"]
    }

    assert checks["nldc_charge_matches_buy_mwh"]["passed"] is True
    assert checks["nldc_charge_matches_sell_mwh"]["passed"] is True


def test_a_capped_charge_is_reported_as_untieable_rather_than_failed(parser, gdam_pages):
    checks = {
        c["check"]: c for c in parser.parse_pages(gdam_pages)["validation"]["checks"]
    }

    assert "nldc_charge_capped:sell_mwh" in checks
    assert checks["nldc_charge_capped:sell_mwh"]["passed"] is True
    assert "nldc_charge_matches_sell_mwh" not in checks


def test_a_missing_block_is_caught_rather_than_stored(parser, dam_pages):
    without_block_30 = [
        page for page in dam_pages
    ]
    without_block_30[1] = "\n".join(
        line for line in dam_pages[1].splitlines() if "07:15 - 07:30" not in line
    )
    validation = parser.parse_pages(without_block_30)["validation"]

    assert validation["passed"] is False
    failed = {c["check"] for c in validation["checks"] if not c["passed"]}
    assert "trade_blocks_count" in failed


def test_a_tampered_summary_total_is_caught(parser, dam_pages):
    tampered = list(dam_pages)
    tampered[0] = dam_pages[0].replace("24,824.43", "99,999.99")
    validation = parser.parse_pages(tampered)["validation"]

    assert validation["passed"] is False
    failed = {c["check"] for c in validation["checks"] if not c["passed"]}
    assert "summary_total_matches_components" in failed


# --------------------------------------------------------------------------
# Rejection of files that are not this report
# --------------------------------------------------------------------------

def test_a_pdf_that_is_not_this_report_is_rejected(parser):
    with pytest.raises(ObligationReportParseError, match="Daily Obligation Summary Report"):
        parser.parse_pages(["Some other document entirely."])


def test_a_corrupt_upload_raises_one_exception_type(parser):
    with pytest.raises(ObligationReportParseError, match="could not read the PDF"):
        parser.parse_pdf(b"this is not a pdf", filename="x.pdf")


def test_a_scanned_report_is_rejected_rather_than_stored_empty(parser):
    with pytest.raises(ObligationReportParseError, match="probably a scan"):
        parser.parse_pages(["", "   "])


# --------------------------------------------------------------------------
# Filename metadata
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename,market,exchange,date_iso",
    [
        ("IEX180926DOR_PTC0136_PY0_Electricity Department Pud.pdf", "DAM", "IEX", "2026-09-18"),
        ("GDAM_IEX180926DOR_PTC0136_PY0_Electricity Department Pud.pdf", "GDAM", "IEX", "2026-09-18"),
        ("RTM_IEX180926DOR_PTC0136_PY0_Electricity Department Pud.pdf", "RTM", "IEX", "2026-09-18"),
        ("HPDAM_PXIL010126DOR_PTC0136_PY0_Something.pdf", "HPDAM", "PXIL", "2026-01-01"),
    ],
)
def test_filename_metadata(filename, market, exchange, date_iso):
    meta = parse_filename(filename)

    assert meta["parsed"] is True
    assert meta["market"] == market
    assert meta["exchange"] == exchange
    assert meta["report_code"] == "DOR"
    assert meta["date_iso"] == date_iso


def test_a_renamed_download_is_reported_as_unparsed_not_guessed():
    """A browser's 'report (1).pdf' must not be turned into a market guess."""
    meta = parse_filename("report (1).pdf")

    assert meta["parsed"] is False
    assert "market" not in meta


def test_the_market_prefix_being_absent_means_dam():
    meta = parse_filename("IEX180926DOR_PTC0136_PY0.pdf")

    assert meta["market"] == "DAM"
    assert meta["market_from_filename"] is False
