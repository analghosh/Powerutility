"""
Tests for the Report Uploader's rules.

These cover the decision the service exists to make - store this report, or
refuse it and say why - so the parser is stubbed out. What it extracts is
covered by tests/libs/test_exchange_obligation_report_parser.py; what matters
here is that a report which disagrees with the form, or with itself, never
reaches the database.
"""

from datetime import datetime, timezone

import pytest

from libs.parsers.exchange_obligation_report_parser import ObligationReportParseError
from services.processing.app.v2.exchange_reports import registry
from services.processing.app.v2.services import exchange_report_service as module
from services.processing.app.v2.services.exchange_report_service import (
    AlreadyUploaded, CategoryNotAvailable, ExchangeReportService, FileTooLarge,
    UploadRejected, describe_upload_moment)

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
    def __init__(self, replaced=False, existing=None, dates=None):
        self.saved = []
        self.replaced = replaced
        self.existing = existing
        self.identity_lookups = []
        self.dates = dates if dates is not None else []
        self.date_queries = []

    async def find_by_identity(self, identity):
        self.identity_lookups.append(identity)
        return self.existing

    async def save(self, document, upload_event, content=None, content_type=None):
        self.saved.append((document, upload_event))
        self.saved_files = getattr(self, "saved_files", []) + [(content, content_type)]
        stored = dict(document)
        stored["_id"] = "stored-id"
        stored["upload_count"] = 2 if self.replaced else 1
        return {"document": stored, "replaced": self.replaced}

    async def delivery_dates(self, filters, limit=60):
        self.date_queries.append((filters, limit))
        return self.dates


@pytest.fixture
def service(monkeypatch):
    """A service whose parser and repository are both stubs."""

    def build(parsed=None, raises=None, replaced=False, existing=None, dates=None):
        instance = ExchangeReportService()
        instance._obligation_parser = _StubParser(
            result=parsed if parsed is not None else _parsed(), raises=raises
        )
        repository = _StubRepository(replaced=replaced, existing=existing, dates=dates)
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
async def test_the_market_dropdown_is_taken_at_its_word(service):
    """
    The operator picks the market; the file does not get a vote.

    The page names no market except G-DAM, so the only thing that could
    contradict the dropdown is the file name - a convention the exchange owns
    and can change. Refusing an upload on that basis costs more than it saves.
    """
    uploader = service()

    result = await _upload(uploader, market_type="HPDAM")

    assert result["summary"]["market_type"] == "HPDAM"
    assert len(uploader.repository_stub.saved) == 1


@pytest.mark.asyncio
async def test_an_hpdam_report_uploads_at_all(service):
    """
    HPDAM is indistinguishable from DAM in the text of the report.

    Cross-checking the market against the content would therefore have
    refused every HPDAM report ever uploaded, which is the concrete reason
    that check is gone rather than merely relaxed.
    """
    uploader = service(parsed=_parsed(detected_market_type="DAM"))

    result = await _upload(uploader, market_type="HPDAM")

    assert result["summary"]["market_type"] == "HPDAM"


@pytest.mark.asyncio
async def test_what_the_content_suggested_is_still_recorded(service):
    """Kept for reference, so a mis-filing can be found later - not a gate."""
    uploader = service()

    await _upload(uploader, market_type="HPDAM")
    document, _ = uploader.repository_stub.saved[0]

    assert document["market_type"] == "HPDAM"
    assert document["detected_market_type"] == "DAM"


@pytest.mark.asyncio
async def test_the_exchange_dropdown_is_taken_at_its_word(service):
    """The exchange was only ever read off the file name."""
    uploader = service()

    result = await _upload(uploader, exchange="PXIL")

    assert result["summary"]["exchange"] == "PXIL"
    assert len(uploader.repository_stub.saved) == 1


@pytest.mark.asyncio
async def test_a_different_delivery_date_is_not_refused_and_the_file_s_wins(service):
    """The report is filed under the day it prints, whatever the form sent."""
    uploader = service()

    result = await _upload(uploader, delivery_date="2026-09-18")

    assert len(uploader.repository_stub.saved) == 1
    assert result["summary"]["delivery_date"] == "2026-09-19"


@pytest.mark.asyncio
async def test_a_report_printing_no_delivery_date_is_filed_under_the_sent_one(service):
    uploader = service(parsed=_parsed(delivery_date=None, delivery_date_iso=None))

    result = await _upload(uploader)

    assert len(uploader.repository_stub.saved) == 1
    assert result["summary"]["delivery_date"] == "2026-09-19"


@pytest.mark.asyncio
async def test_with_no_date_sent_the_file_s_delivery_date_is_used(service):
    """The uploader no longer asks for it; the report prints its own."""
    uploader = service(existing=_stored())

    with pytest.raises(AlreadyUploaded):
        await _upload(uploader, delivery_date=None)

    assert uploader.repository_stub.identity_lookups[0]["delivery_date_iso"] == "2026-09-19"


@pytest.mark.asyncio
async def test_with_no_date_sent_the_report_is_stored_under_the_file_s_day(service):
    uploader = service()

    result = await _upload(uploader, delivery_date=None)

    assert len(uploader.repository_stub.saved) == 1
    assert result["summary"]["delivery_date"] == "2026-09-19"


@pytest.mark.asyncio
async def test_with_no_date_sent_a_report_printing_none_is_refused(service):
    """Stored under no day, it would never show up in the reports section."""
    uploader = service(parsed=_parsed(delivery_date=None, delivery_date_iso=None))

    with pytest.raises(UploadRejected) as raised:
        await _upload(uploader, delivery_date=None)

    assert raised.value.reason == "delivery_date_not_in_file"
    assert uploader.repository_stub.saved == []


def test_only_the_obligation_report_takes_its_delivery_date_from_the_file():
    from_file = {
        e["code"] for e in registry.describe_categories() if e["delivery_date_from_file"]
    }

    assert from_file == {registry.OBLIGATION_REPORT}


@pytest.mark.asyncio
async def test_an_rtm_report_with_no_trading_date_still_uploads(service):
    """RTM reports print no trading date; the delivery date is enough."""
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
async def test_a_report_failing_its_own_checks_is_still_stored(service):
    """Stored as issued; the check results stay on the document."""
    validation = {
        "passed": False,
        "checks": [
            {"check": "nldc_charge_matches_buy_mwh", "passed": False, "detail": "3.01 vs 0.0"},
            {"check": "trade_blocks_contiguous", "passed": True, "detail": ""},
        ],
    }
    uploader = service(parsed=_parsed(validation=validation))

    await _upload(uploader)

    document, _ = uploader.repository_stub.saved[0]
    assert document["validation"] == validation


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
# When the upload happened — the shift report works in time blocks
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "utc,expected_block,expected_ist_date,expected_time",
    [
        # 18:30 UTC is exactly midnight IST: the first block of the NEXT day.
        ("2026-09-20T18:30:00+00:00", 1, "2026-09-21", "00:00"),
        # 18:29:59 UTC is the last second of the IST day: block 96.
        ("2026-09-20T18:29:59+00:00", 96, "2026-09-20", "23:59"),
        ("2026-09-20T00:00:00+00:00", 23, "2026-09-20", "05:30"),
        ("2026-09-20T23:10:00+00:00", 19, "2026-09-21", "04:40"),
    ],
)
def test_the_upload_moment_is_read_in_ist(utc, expected_block, expected_ist_date, expected_time):
    """
    The shift report is laid out by time block, so the block has to be derived
    from the IST reading of the instant, not from UTC. The day boundary is
    where that matters: 23:10 UTC is early morning on the following IST date.
    """
    moment = describe_upload_moment(datetime.fromisoformat(utc))

    assert moment["uploaded_in_time_block"] == expected_block
    assert moment["uploaded_on_ist_date"] == expected_ist_date
    assert moment["uploaded_at_ist_time"] == expected_time


@pytest.mark.asyncio
async def test_the_stored_report_records_the_block_it_was_uploaded_in(service):
    uploader = service()

    result = await _upload(uploader)
    document, upload_event = uploader.repository_stub.saved[0]

    assert 1 <= document["uploaded_in_time_block"] <= 96
    assert document["uploaded_at_ist"].endswith("+05:30")
    assert document["uploaded_on_ist_date"]
    # The history entry carries it too, so a replaced upload keeps the block
    # it originally landed in.
    assert upload_event["uploaded_in_time_block"] == document["uploaded_in_time_block"]
    assert result["summary"]["uploaded_in_time_block"] == document["uploaded_in_time_block"]


@pytest.mark.asyncio
async def test_the_utc_instant_stays_authoritative(service):
    """The IST fields are a reading of it, not a replacement for it."""
    uploader = service()

    await _upload(uploader)
    document, _ = uploader.repository_stub.saved[0]

    assert document["uploaded_at"].tzinfo is timezone.utc


# --------------------------------------------------------------------------
# Already uploaded — ask before overwriting a day's numbers
# --------------------------------------------------------------------------

def _stored(filename="IEX180926DOR_PTC0136_PY0_Test.pdf", sha="a" * 64):
    return {
        "source_filename": filename,
        "file_sha256": sha,
        "uploaded_at": datetime(2026, 9, 20, 6, 30, tzinfo=timezone.utc),
        "uploaded_at_ist": "2026-09-20T12:00:00+05:30",
        "uploaded_in_time_block": 49,
        "uploaded_by_username": "someone_else",
        "upload_count": 1,
    }


@pytest.mark.asyncio
async def test_a_second_upload_of_the_same_report_asks_first(service):
    uploader = service(existing=_stored())

    with pytest.raises(AlreadyUploaded) as raised:
        await _upload(uploader)

    assert raised.value.reason == "already_uploaded"
    assert uploader.repository_stub.saved == []


@pytest.mark.asyncio
async def test_the_question_names_the_file_already_stored(service):
    """The operator has to recognise what they are about to overwrite."""
    uploader = service(existing=_stored(filename="yesterdays_download.pdf"))

    with pytest.raises(AlreadyUploaded) as raised:
        await _upload(uploader)

    assert "yesterdays_download.pdf" in raised.value.message
    previous = raised.value.details["previous"]
    assert previous["source_filename"] == "yesterdays_download.pdf"
    assert previous["uploaded_by_username"] == "someone_else"
    assert previous["uploaded_in_time_block"] == 49


@pytest.mark.asyncio
async def test_the_same_file_again_is_reported_as_the_same_file(service):
    """
    A double submit and a reissued report both land here, and they mean
    different things. The digest is what tells them apart.
    """
    import hashlib

    digest = hashlib.sha256(b"%PDF-1.4 pretend").hexdigest()
    uploader = service(existing=_stored(sha=digest))

    with pytest.raises(AlreadyUploaded) as raised:
        await _upload(uploader)

    assert raised.value.details["identical_file"] is True
    assert "This is the same file" in raised.value.message


@pytest.mark.asyncio
async def test_a_different_file_for_the_same_report_says_it_will_replace(service):
    uploader = service(existing=_stored(sha="b" * 64))

    with pytest.raises(AlreadyUploaded) as raised:
        await _upload(uploader)

    assert raised.value.details["identical_file"] is False
    assert "will replace it" in raised.value.message


@pytest.mark.asyncio
async def test_the_question_survives_being_turned_into_an_http_body(service):
    """
    The details of a refusal become a JSON error body. The stored timestamp
    comes back from Mongo as a datetime, which is not JSON, so it has to be
    rendered here rather than passed through - otherwise the 409 that was
    meant to ask a question arrives as a 500.
    """
    import json

    uploader = service(existing=_stored())

    with pytest.raises(AlreadyUploaded) as raised:
        await _upload(uploader)

    body = json.dumps({"reason": raised.value.reason, **raised.value.details})
    assert "2026-09-20T06:30:00+00:00" in body


@pytest.mark.asyncio
async def test_confirming_the_replacement_goes_through(service):
    uploader = service(existing=_stored(), replaced=True)

    result = await _upload(uploader, confirm_replace=True)

    assert result["replaced_previous_upload"] is True
    assert len(uploader.repository_stub.saved) == 1
    # Confirmed means do not ask again; the lookup is skipped entirely.
    assert uploader.repository_stub.identity_lookups == []


@pytest.mark.asyncio
async def test_the_identity_checked_includes_the_portfolio_from_inside_the_file(service):
    """
    The form has no portfolio field - it is printed in the report - so the
    duplicate check can only run after parsing.
    """
    uploader = service(existing=_stored())

    with pytest.raises(AlreadyUploaded):
        await _upload(uploader)

    assert uploader.repository_stub.identity_lookups == [
        {
            "report_category": registry.OBLIGATION_REPORT,
            "exchange": "IEX",
            "market_type": "DAM",
            "delivery_date_iso": "2026-09-19",
            "portfolio_code": "S2PY0PTC0136",
        }
    ]


@pytest.mark.asyncio
async def test_a_first_upload_is_not_questioned(service):
    uploader = service(existing=None)

    result = await _upload(uploader)

    assert result["replaced_previous_upload"] is False
    assert len(uploader.repository_stub.identity_lookups) == 1


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


# --------------------------------------------------------------------------
# Which delivery dates the daily view can open
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delivery_dates_are_scoped_to_one_category(service):
    """
    The daily view only ever shows one category, and the dropdown lists four.
    Offering dates that belong to a different report would open empty days.
    """
    uploader = service()

    await uploader.delivery_dates(report_category=registry.OBLIGATION_REPORT)

    filters, limit = uploader.repository_stub.date_queries[0]
    assert filters == {"report_category": registry.OBLIGATION_REPORT}
    assert limit == 60


@pytest.mark.asyncio
async def test_delivery_dates_without_a_category_asks_for_all_of_them(service):
    uploader = service()

    await uploader.delivery_dates()

    filters, _ = uploader.repository_stub.date_queries[0]
    assert filters == {}


@pytest.mark.asyncio
async def test_delivery_dates_are_returned_with_a_count(service):
    rows = [
        {
            "delivery_date": "2026-09-19",
            "report_count": 3,
            "markets": ["DAM", "GDAM", "RTM"],
            "exchanges": ["IEX"],
            "last_uploaded_at": datetime(2026, 9, 21, 5, 30, tzinfo=timezone.utc),
        },
        {
            "delivery_date": "2026-09-18",
            "report_count": 1,
            "markets": ["DAM"],
            "exchanges": ["IEX"],
            "last_uploaded_at": None,
        },
    ]
    uploader = service(dates=rows)

    result = await uploader.delivery_dates()

    assert result["total"] == 2
    assert [row["delivery_date"] for row in result["items"]] == [
        "2026-09-19",
        "2026-09-18",
    ]
    assert result["items"][0]["markets"] == ["DAM", "GDAM", "RTM"]


@pytest.mark.asyncio
async def test_no_uploads_yet_is_an_empty_list_not_an_error(service):
    """An empty archive and a failed read must not look the same on screen."""
    uploader = service(dates=[])

    result = await uploader.delivery_dates()

    assert result == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_an_upload_history_entry_reads_the_same_way_as_the_document(service):
    """
    "Earlier uploads" is rendered by the same code as "uploaded", so a history
    entry has to carry the same fields. Leaving the split IST reading off would
    make a reader localise the UTC instant and print a clock time that
    contradicts the time block beside it.
    """
    uploader = service()

    await _upload(uploader)
    document, upload_event = uploader.repository_stub.saved[0]

    for field in (
        "uploaded_at",
        "uploaded_at_ist",
        "uploaded_on_ist_date",
        "uploaded_at_ist_time",
        "uploaded_in_time_block",
        "uploaded_by",
        "uploaded_by_username",
    ):
        assert upload_event[field] == document[field], field
