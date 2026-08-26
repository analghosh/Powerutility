"""What the day-ahead block bid submission does when a pre-flight call to IEX hangs.

Reported 2026-08-26 from the UAT deployment: "DAM: Exchange API timed out
fetching DAM asset/delivery info." on iDAM Block Bid Approval, with nothing
submitted and nothing in Bid History.

Before a block bid can be built the flow makes two calls to the exchange:

  * /master/assets     — required. AssetId and OrderQtyDecimal shape every bid.
  * /deliverydates     — a cross-check only. Its answer is deliberately not used;
                         the delivery date is derived from the bid date (6942b0f).

Both sat in one `asyncio.gather` with no `return_exceptions`, so a hang on the
cross-check — a call whose result the code throws away — failed the whole
submission, and the message named neither which call hung nor whether it failed
to connect or failed to answer. These tests pin both behaviours.

Timeouts and the position of the /deliverydates call are deliberately left as
they have always been; the only change is that the cross-check can no longer
veto a submission.

Nothing here touches the network or mongo: the exchange client's methods and the
repositories are replaced in place.
"""
import asyncio
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.iex import masterApi  # noqa: E402
from services.processing.app.v2.services import block_bid_services as bbs  # noqa: E402

BID_DATE = "2026-08-27"

ASSETS = {"AssetDetails": [{"AssetId": "ASSET-1", "OrderQtyDecimal": 2}]}
DELIVERY_DATES = {"DeliveryDateId": "T+1", "DeliveryDate": 1787788800}

APPROVED_BID = {
    "_id": "bid-1",
    "from": "00:00",
    "to": "00:15",
    "quantum": -50,
    "bid_price": 1,
    "type": "Buy",
    "number_of_bids": 1,
    "ex_split_string": "IEX-100_PXIL-0",
    "ocf_config": "60_S_0",
    "market_type": "DAM",
    "biding_date": BID_DATE,
}

CONFIG = {
    "baseUrl": "https://exchange.invalid",
    "LoginUserId": "PTCA1",
    "ParticipantId": "N2DLTEST0001",
    "PortfolioId": "S2PYTEST0001",
    "BearerToken": "token",
    "BidAreaId": "S2",
}


class RecordingExchange:
    """Stands in for ExchangeAPIClient. Records calls, raises what it is told to."""

    def __init__(self, calls, asset_result, delivery_result):
        self.calls = calls
        self._asset_result = asset_result
        self._delivery_result = delivery_result

    async def get_asset_details(self, bid_type=None):
        self.calls.append("assets")
        if isinstance(self._asset_result, Exception):
            raise self._asset_result
        return self._asset_result

    async def get_delivery_dates(self, bid_type=None):
        self.calls.append("deliverydates")
        if isinstance(self._delivery_result, Exception):
            raise self._delivery_result
        return self._delivery_result

    async def get_dam_block_bids(self, delivery_date, **kwargs):
        self.calls.append("existing")
        return {"BlockBidDetails": [], "TotalRecord": 0}

    async def get_dam_block_bid_ref_count(self):
        self.calls.append("refcount")
        return {"BlockBidRefCount": 41}

    async def submit_dam_block_bid(self, bid_request):
        self.calls.append("submit")
        return {"TransactionId": "TXN-1", "Status": "Success", "StatusMessage": "OK"}

    async def get_bid_details(self, transaction_id, delivery_date, bid_type=None):
        self.calls.append("biddetails")
        return {"BlockBidDetails": []}


def _arrange(monkeypatch, asset_result, delivery_result=DELIVERY_DATES):
    """Point the service at a stub exchange and stub repositories. Returns the call log."""
    calls = []

    monkeypatch.setattr(
        bbs,
        "ExchangeAPIClient",
        lambda **kwargs: RecordingExchange(calls, asset_result, delivery_result),
    )

    async def noop(*args, **kwargs):
        return None

    async def get_config(bid_type, market):
        return dict(CONFIG)

    async def get_approved(biding_date, market_type="DAM", latest_revision_only=True):
        return [dict(APPROVED_BID)]

    service = bbs.block_bid_service
    monkeypatch.setattr(service.exchange_config_repo, "get_bidType_market_active_config", get_config)
    monkeypatch.setattr(service.block_bid_repo, "get_approved_bids", get_approved)
    monkeypatch.setattr(service.block_bid_repo, "update_one", noop)
    monkeypatch.setattr(service.block_bid_repo, "bulk_update_status", noop)
    monkeypatch.setattr(service.dam_iex_response_repo, "bulk_insert_response_docs", noop)

    # The flow waits 4s for the exchange to register the bid; that wait is not
    # what these tests are about.
    monkeypatch.setattr(asyncio, "sleep", noop)

    return calls


def _submit():
    return asyncio.run(
        bbs.block_bid_service.submit_dam_to_exchange(
            BID_DATE, submitted_by="tester", correlation_id="test", market="DAM"
        )
    )


# --------------------------------------------- the cross-check is not fatal ---

def test_a_delivery_dates_timeout_does_not_stop_the_submission(monkeypatch):
    calls = _arrange(monkeypatch, ASSETS, delivery_result=httpx.ReadTimeout("no answer"))

    result = _submit()

    assert result["status"] == "success"
    assert "submit" in calls, "the bid must still reach the exchange"


def test_a_delivery_dates_failure_of_any_kind_is_survivable(monkeypatch):
    calls = _arrange(monkeypatch, ASSETS, delivery_result=httpx.ConnectError("refused"))

    result = _submit()

    assert result["status"] == "success"
    assert "submit" in calls


# ------------------------------------------------- the required call is not ---

def test_an_asset_details_timeout_stops_the_submission(monkeypatch):
    calls = _arrange(monkeypatch, httpx.ReadTimeout("no answer"))

    with pytest.raises(HTTPException) as raised:
        _submit()

    assert raised.value.status_code == 504
    assert "submit" not in calls, "no bid may be sent when the asset lookup failed"


def test_the_error_names_the_endpoint_and_the_kind_of_failure(monkeypatch):
    _arrange(monkeypatch, httpx.ConnectTimeout("no route"))

    with pytest.raises(HTTPException) as raised:
        _submit()

    detail = raised.value.detail
    assert "asset details" in detail
    assert "ConnectTimeout" in detail, "connect and read failures must be distinguishable"
    assert "master/assets" in detail
    assert "No bid was sent" in detail


def test_the_error_carries_no_participant_identifiers(monkeypatch):
    _arrange(monkeypatch, httpx.ReadTimeout("no answer"))

    with pytest.raises(HTTPException) as raised:
        _submit()

    # The full URL — with LoginUserId and ParticipantId — belongs in the log,
    # not in a dialog in a trader's browser.
    assert CONFIG["ParticipantId"] not in raised.value.detail
    assert CONFIG["LoginUserId"] not in raised.value.detail


def test_a_refusal_from_the_exchange_is_passed_through(monkeypatch):
    response = httpx.Response(403, request=httpx.Request("GET", "https://exchange.invalid"))
    calls = _arrange(monkeypatch, httpx.HTTPStatusError("forbidden", request=response.request, response=response))

    with pytest.raises(HTTPException) as raised:
        _submit()

    assert raised.value.status_code == 403
    assert calls.count("assets") == 1, "an answer from IEX is an answer — do not ask again"


# ------------------------------------------- a failed call leaves a record ---

def test_a_failed_master_call_is_written_to_the_api_log(monkeypatch):
    """Timed-out calls used to log nothing at all, so they left no trace to reconcile."""
    logged = []

    async def capture(**kwargs):
        logged.append(kwargs)

    monkeypatch.setattr(masterApi.integration_api_log_repository, "log_api_call", capture)

    client = masterApi.ExchangeAPIClient(
        base_url="https://exchange.invalid",
        login_user_id="PTCA1",
        participant_id="N2DLTEST0001",
        portfolio_id="S2PYTEST0001",
    )

    class FailingClient:
        async def get(self, url, headers=None):
            raise httpx.ReadTimeout("no answer")

    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(
            client._get_or_log_failure(
                FailingClient(), "https://exchange.invalid/dam/api/v2/master/assets/a,b",
                {}, "dam", "block-bid", "getAssetDetail",
            )
        )

    assert len(logged) == 1
    body = logged[0]["response_body"]
    assert body["error"] == "ReadTimeout"
    assert "elapsed_seconds" in body
    assert logged[0]["method"] == "getAssetDetail"
