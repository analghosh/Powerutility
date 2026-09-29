"""
Tests for the sample files the Report Uploader offers for download.

A sample is only useful if uploading it would work, so each one is put through
its own category's parser. A parser change that stops reading the sample fails
here, in the same release, rather than on an operator's screen.
"""

import pytest
from fastapi import HTTPException

from libs.parsers.exchange_obligation_report_parser import ExchangeObligationReportParser
from services.processing.app.v2.api.router.exchange_report_routes import (
    download_category_sample,
)
from services.processing.app.v2.exchange_reports import dsm_parser, registry

WITH_SAMPLE = {registry.OBLIGATION_REPORT, registry.DSM_REPORT}


def _sample_bytes(code):
    return registry.CATEGORIES[code].sample_path.read_bytes()


def test_only_the_obligation_and_dsm_reports_carry_a_sample():
    offered = {code for code, category in registry.CATEGORIES.items() if category.sample_file}
    assert offered == WITH_SAMPLE


@pytest.mark.parametrize("code", sorted(WITH_SAMPLE))
def test_every_named_sample_is_on_disk(code):
    assert registry.CATEGORIES[code].sample_path.is_file()


@pytest.mark.parametrize("code", sorted(WITH_SAMPLE))
def test_every_sample_has_an_extension_its_category_accepts(code):
    category = registry.CATEGORIES[code]
    assert category.sample_path.suffix.lower() in category.accepted_extensions


def test_the_dropdown_names_each_sample():
    described = {entry["code"]: entry["sample_filename"] for entry in registry.describe_categories()}
    assert described[registry.OBLIGATION_REPORT].endswith(".pdf")
    assert described[registry.DSM_REPORT] == "DSM Report Sample.xlsx"
    assert described[registry.OTHER_REPORTS] is None


def test_the_obligation_sample_parses_and_passes_its_own_checks():
    category = registry.CATEGORIES[registry.OBLIGATION_REPORT]
    parsed = ExchangeObligationReportParser().parse_pdf(
        _sample_bytes(registry.OBLIGATION_REPORT), filename=category.sample_file
    )
    assert parsed["delivery_date_iso"] == "2026-09-19"
    assert len(parsed["trade_blocks"]) == 96
    assert parsed["validation"]["passed"] is True


def test_the_dsm_sample_is_the_latest_format_and_two_rows():
    parsed = dsm_parser.parse(_sample_bytes(registry.DSM_REPORT), "DSM Report Sample.xlsx")
    assert parsed["row_count"] == 2
    assert [day["report_date"] for day in parsed["days"]] == ["2025-01-01"]
    first, second = parsed["days"][0]["blocks"]
    assert (first["block"], first["block_time"]) == (1, "00:00")
    assert (second["block"], second["block_time"]) == (2, "00:15")
    required = [field for field, _ in dsm_parser.COLUMNS if field in dsm_parser.NUMERIC_FIELDS]
    assert all(first[field] is not None for field in required)
    # The two columns the latest format added are in the sample, and blank.
    assert (first["dev_percent"], first["postfacto_charges"]) == (None, None)


def _xlsx(headers, rows):
    import io
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


OLD_HEADERS = ["Date", "Time", "Schedule Total", "Actual Total", "Frequency",
               "Deviation Rate", "Deviation", "Overdrawl_Charges", "Underdrawl_Charges"]


def test_a_statement_in_the_earlier_nine_column_format_still_uploads():
    data = _xlsx(OLD_HEADERS, [["01-01-2025", "00:00", 100, 98.5, 50.01, 450, -1.5, 0, 1687.5]])
    block = dsm_parser.parse(data, "old.xlsx")["days"][0]["blocks"][0]
    assert block["underdrawl_charges"] == 1687.5
    assert (block["dev_percent"], block["postfacto_charges"]) == (None, None)


def test_the_latest_format_s_extra_figures_are_stored():
    headers = OLD_HEADERS[:7] + ["dev_%"] + OLD_HEADERS[7:] + ["Postfacto_Charges"]
    data = _xlsx(headers, [["01-01-2025", "00:00", 100, 98.5, 50.01, 450, -1.5, -1.5, 0, 1687.5, 12.5]])
    block = dsm_parser.parse(data, "new.xlsx")["days"][0]["blocks"][0]
    assert block["dev_percent"] == -1.5
    assert block["postfacto_charges"] == 12.5
    assert block["overdrawl_charges"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code, media_type",
    [
        (registry.OBLIGATION_REPORT, "application/pdf"),
        (
            registry.DSM_REPORT,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    ],
)
async def test_the_sample_downloads_as_an_attachment_under_its_own_name(code, media_type):
    response = await download_category_sample(code)
    assert response.media_type == media_type
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="{registry.CATEGORIES[code].sample_file}"'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [registry.OTHER_REPORTS, registry.LTOA, "no_such_category"])
async def test_a_category_without_a_sample_is_a_404(code):
    with pytest.raises(HTTPException) as raised:
        await download_category_sample(code)
    assert raised.value.status_code == 404
