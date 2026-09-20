"""
Tests for the Report Uploader's rules.

These cover the decision the service exists to make - store this report, or
refuse it and say why - so the parser is stubbed out. What it extracts is
covered by tests/libs/test_exchange_obligation_report_parser.py; what matters
here is that a report which disagrees with the form, or with itself, never
reaches the database.
"""

import pytest

from libs.parsers.exchange_obligation_report_parser import ObligationReportParseError
from services.processing.app.v2.exchange_reports import registry
from services.processing.app.v2.services import exchange_report_service as module
from services.processing.app.v2.services.exchange_report_service import (
    CategoryNotAvailable, ExchangeReportService, FileTooLarge, UploadRejected)

ACTOR = {"user_id": "u-1", "username": "localdev"}
PDF_NAME = "IEX180926DOR_PTC0136_PY0_Test.pdf"


def _parsed(**overrides):
    """A report that passes every check, unless a test breaks one."""
    document = {
        "report_type": "daily_obligation_report",
        "market_type": "DAM",
        "detected_market_type": "DAM",
        "exchange": "IEX",
        "trading_date": "18-Sep-26",
        "trading_date_iso": "2026-09-18",
        "delivery_date": "19-Sep-26",
        "delivery_date_iso": "2026-09-19",
        "entity_id": "N2DL0PTC0000",
        "entity_name": "Test Trader Ltd.",
        "portfolio_code": "S2PY0PTC0136",
        "portfolio_name": "Test_Portfolio_One",
        "filename_metadata": {"parsed": True, "exchange": "IEX", "market": "DAM"},
        "obligation_summary": {"funds_payin_payout": 25000.0, "total": 24824.43},
        "trade_blocks": [{"time_block": n, "qty_mw": 0.0} for n in range(1, 97)],
        "trade_totals": {"buy_mwh": 5.0, "sell_mwh": 10.0, "gross_mwh": 15.0},
        "validation": {"passed": True, "checks": []},
    }
    document.update(overrides)
    return document


class _StubParser:
    """Stands in for the PDF parser; returns or raises whatever a test needs."""

    def __init__(self, result=None, raises=None):
        self.result = result
        self.raises = raises
        self.calls = []

    def parse_pdf(self, data, filename=None):
        self.calls.append((len(data), filename))
        if self.raises:
            raise self.raises
        return self.result


class _StubRepository:
    def __init__(self, replaced=False):
        self.saved = []
        self.replaced = replaced

    async def save(self, document, upload_event):
        self.saved.append((document, upload_event))
        stored = dict(document)
        stored["_id"] = "stored-id"
        stored["upload_count"] = 2 if self.replaced else 1
        return {"document": stored, "replaced": self.replaced}


@pytest.fixture
def service(monkeypatch):
    """A service whose parser and repository are both stubs."""

    def build(parsed=None, raises=None, replaced=False):
        instance = ExchangeReportService()
        instance._obligation_parser = _StubParser(
            result=parsed if parsed is not None else _parsed(), raises=raises
        )
        repository = _StubRepository(replaced=replaced)
        monkeypatch.setattr(module, "exchange_report_repository", repository)
        instance.repository_stub = repository
        return instance

    return build


async def _upload(service, **overrides):
    payload = {
        "data": b"%PDF-1.4 pretend",
        "filename": PDF_NAME,
        "report_category": registry.OBLIGATION_REPORT,
        "exchange": "IEX",
        "market_type": "DAM",
        "delivery_date": "2026-09-19",
        "actor": ACTOR,
    }
    payload.update(overrides)
    return await service.upload(**payload)


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_matching_report_is_stored(service):
    uploader = service()

    result = await _upload(uploader)

    assert result["id"] == "stored-id"
    assert result["replaced_previous_upload"] is False
    assert len(uploader.repository_stub.saved) == 1


@pytest.mark.asyncio
async def test_the_stored_document_carries_the_operator_choices_and_a_file_digest(service):
    uploader = service()

    await _upload(uploader)
    document, upload_event = uploader.repository_stub.saved[0]

    assert document["report_category"] == registry.OBLIGATION_REPORT
    assert document["report_category_label"] == "Obligation Report"
    assert document["exchange"] == "IEX"
    assert document["market_type"] == "DAM"
    assert document["uploaded_by_username"] == "localdev"
    assert len(document["file_sha256"]) == 64
    assert document["file_size_bytes"] == len(b"%PDF-1.4 pretend")
    assert upload_event["file_sha256"] == document["file_sha256"]


@pytest.mark.asyncio
async def test_the_summary_reports_the_headline_figures(service):
    uploader = service()

    summary = (await _upload(uploader))["summary"]

    assert summary["delivery_date"] == "2026-09-19"
    assert summary["funds_payin_payout"] == 25000.0
    assert summary["buy_mwh"] == 5.0
    assert summary["sell_mwh"] == 10.0
    assert summary["block_count"] == 96
    assert summary["portfolio_name"] == "Test_Portfolio_One"


@pytest.mark.asyncio
async def test_re_uploading_the_same_report_says_it_replaced_one(service):
    """The exchange re-publishes corrected reports under the same identity."""
    uploader = service(replaced=True)

    result = await _upload(uploader)

    assert result["replaced_previous_upload"] is True
    assert result["upload_count"] == 2


# --------------------------------------------------------------------------
# Refusals, cheapest check first
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_unknown_category_is_refused(service):
    uploader = service()

    with pytest.raises(UploadRejected) as raised:
        await _upload(uploader, report_category="not_a_category")

    assert raised.value.reason == "unknown_category"
    assert uploader.repository_stub.saved == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category", [registry.LTOA, registry.STOA, registry.BACKING_QUANTUM_SCHEDULE_COST]
)
async def test_a_category_with_no_parser_yet_is_refused_by_name(service, category):
    uploader = service()

    with pytest.raises(CategoryNotAvailable) as raised:
        await _upload(uploader, report_category=category, filename="whatever.xlsx")

    assert raised.value.reason == "category_not_available"
    assert registry.CATEGORIES[category].label in raised.value.message


@pytest.mark.asyncio
async def test_a_spreadsheet_is_refused_for_the_obligation_report(service):
    """The exchange publishes this one as a PDF; a CSV would only fail later."""
    uploader = service()

    with pytest.raises(UploadRejected) as raised:
        await _upload(uploader, filename="obligation.csv")

    assert raised.value.reason == "unsupported_file_type"
    assert raised.value.details["accepted_extensions"] == [".pdf"]


@pytest.mark.asyncio
async def test_a_file_over_the_limit_is_refused_before_it_is_read(service):
    uploader = service()
    oversized = b"x" * (registry.MAX_UPLOAD_BYTES + 1)

    with pytest.raises(FileTooLarge) as raised:
        await _upload(uploader, data=oversized)

    assert raised.value.details["limit_bytes"] == registry.MAX_UPLOAD_BYTES
    assert uploader._obligation_parser.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("exchange", "NOTANEXCHANGE", "unknown_exchange"),
        ("market_type", "NOTAMARKET", "unknown_market_type"),
    ],
)
async def test_an_unrecognised_dropdown_value_is_refused(service, field, value, reason):
    uploader = service()

    with pytest.raises(UploadRejected) as raised:
        await _upload(uploader, **{field: value})

    assert raised.value.reason == reason


@pytest.mark.asyncio
async def test_an_unreadable_file_is_refused_with_the_parser_s_reason(service):
    uploader = service(raises=ObligationReportParseError("could not read the PDF: Unexpected EOF"))

    with pytest.raises(UploadRejected) as raised:
        await _upload(uploader)

    assert raised.value.reason == "could_not_parse"
    assert "Unexpected EOF" in raised.value.message
    assert uploader.repository_stub.saved == []


# --------------------------------------------------------------------------
# The file must agree with the form
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_dam_report_filed_as_gdam_is_refused(service):
    """
    The market is read from the report's own pages, so renaming the file
    cannot get it past this check.
    """
    uploader = service()

    with pytest.raises(UploadRejected) as raised:
        await _upload(uploader, market_type="GDAM")

    assert raised.value.reason == "file_does_not_match_selection"
    assert raised.value.details["mismatches"] == [
        {
            "field": "market_type",
            "label": "Market",
            "selected": "GDAM",
            "found_in_file": "DAM",
        }
    ]
    assert uploader.repository_stub.saved == []


@pytest.mark.asyncio
async def test_the_message_names_the_field_as_the_screen_labels_it(service):
    """The operator reads "Market", not the stored field name `market_type`."""
    uploader = service()

    with pytest.raises(UploadRejected) as raised:
        await _upload(uploader, market_type="GDAM")

    assert "Market: you selected GDAM, the file says DAM" in raised.value.message
    assert "market_type:" not in raised.value.message


@pytest.mark.asyncio
async def test_the_wrong_exchange_is_refused(service):
    uploader = service()

    with pytest.raises(UploadRejected) as raised:
        await _upload(uploader, exchange="PXIL")

    fields = [m["field"] for m in raised.value.details["mismatches"]]
    assert fields == ["exchange"]


@pytest.mark.asyncio
async def test_the_wrong_delivery_date_is_refused(service):
    """
    An easy slip: the filename carries the trading date, so picking 18 Sep for
    a report delivered on 19 Sep would file the day's trading under the wrong
    delivery day.
    """
    uploader = service()

    with pytest.raises(UploadRejected) as raised:
        await _upload(uploader, delivery_date="2026-09-18")

    assert raised.value.details["mismatches"] == [
        {
            "field": "delivery_date",
            "label": "Delivery Date",
            "selected": "2026-09-18",
            "found_in_file": "2026-09-19",
        }
    ]


@pytest.mark.asyncio
async def test_every_mismatch_is_reported_at_once(service):
    """One round trip should tell the operator everything they got wrong."""
    uploader = service()

    with pytest.raises(UploadRejected) as raised:
        await _upload(uploader, market_type="RTM", exchange="HPX", delivery_date="2026-01-01")

    fields = [m["field"] for m in raised.value.details["mismatches"]]
    assert fields == ["market_type", "exchange", "delivery_date"]


@pytest.mark.asyncio
async def test_an_rtm_report_with_no_trading_date_still_uploads(service):
    """RTM reports print no trading date; only the delivery date is checked."""
    uploader = service(
        parsed=_parsed(
            market_type="RTM",
            detected_market_type="RTM",
            trading_date=None,
            trading_date_iso=None,
            delivery_date_iso="2026-09-18",
            filename_metadata={"parsed": True, "exchange": "IEX", "market": "RTM"},
        )
    )

    result = await _upload(uploader, market_type="RTM", delivery_date="2026-09-18")

    assert result["summary"]["trading_date"] is None
    assert result["summary"]["delivery_date"] == "2026-09-18"


# --------------------------------------------------------------------------
# A report that disagrees with itself
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_report_failing_its_own_checks_is_not_stored(service):
    uploader = service(
        parsed=_parsed(
            validation={
                "passed": False,
                "checks": [
                    {"check": "trade_blocks_count", "passed": False, "detail": "95 of 96 blocks"},
                    {"check": "trade_blocks_contiguous", "passed": True, "detail": ""},
                ],
            }
        )
    )

    with pytest.raises(UploadRejected) as raised:
        await _upload(uploader)

    assert raised.value.reason == "failed_validation"
    assert "Nothing has been stored" in raised.value.message
    assert [c["check"] for c in raised.value.details["failed_checks"]] == ["trade_blocks_count"]
    assert uploader.repository_stub.saved == []


# --------------------------------------------------------------------------
# Listing rows
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_block_count_is_stored_not_derived(service):
    """
    Listings project the 96-row block arrays away, so a count derived on read
    would report 0 blocks and read as a report that stored nothing.
    """
    uploader = service()

    await _upload(uploader)
    document, _ = uploader.repository_stub.saved[0]

    assert document["block_count"] == 96

    as_listed = {k: v for k, v in document.items() if k not in ("trade_blocks", "sell_blocks")}
    assert ExchangeReportService.summarise(as_listed)["block_count"] == 96


@pytest.mark.asyncio
async def test_the_gdam_sell_annexure_is_counted_separately(service):
    uploader = service(
        parsed=_parsed(
            market_type="GDAM",
            detected_market_type="GDAM",
            sell_blocks=[{"time_block": n} for n in range(1, 97)],
            filename_metadata={"parsed": True, "exchange": "IEX", "market": "GDAM"},
        )
    )

    result = await _upload(uploader, market_type="GDAM")

    assert result["summary"]["block_count"] == 96
    assert result["summary"]["sell_block_count"] == 96


# --------------------------------------------------------------------------
# The registry the screen reads
# --------------------------------------------------------------------------

def test_the_dropdown_lists_all_four_categories_in_order():
    codes = [entry["code"] for entry in registry.describe_categories()]

    assert codes == [
        registry.OBLIGATION_REPORT,
        registry.BACKING_QUANTUM_SCHEDULE_COST,
        registry.LTOA,
        registry.STOA,
    ]


def test_only_the_obligation_report_is_marked_ready():
    ready = {e["code"] for e in registry.describe_categories() if e["implemented"]}

    assert ready == {registry.OBLIGATION_REPORT}


def test_every_unavailable_category_says_why():
    for entry in registry.describe_categories():
        if not entry["implemented"]:
            assert entry["unavailable_reason"]
