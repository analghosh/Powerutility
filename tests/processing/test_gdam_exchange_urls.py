"""
The GDAM book and trade URLs follow IEX GDAM API 2.1, and HP-DAM's follow HPDAM API 2.0, not DAM's.

GDAM's single bid book and trade book take a Type that DAM and HP-DAM do not. GDAM's
and HP-DAM's block bid books have FetchDuplicate where DAM has ExceptionId (GDAM's with a
Type before it) and take BuySell as 0/1/2. Sent with DAM's parameter list, IEX answers
500 and the GDAM live sync stores nothing.
"""

from unittest.mock import AsyncMock, patch

import pytest

from services.processing.app.v2.iex.masterApi import ExchangeAPIClient
from services.scheduler.app.services.scheduler_exchange_api_client import scheduler_exchange_client

DELIVERY_DATE = 1791072000
CONFIG = {
    "baseUrl": "https://idamapi.example",
    "LoginUserId": "EDPA1",
    "ParticipantId": "N2DL0PTC0000",
    "PortfolioId": "S2PY0PTC0136",
    "BearerToken": "token",
}


def params_after(url, path):
    """The comma-separated path parameters that follow `path` in `url`."""
    return url.split(path, 1)[1].split(",")


class _Response:
    status_code = 200

    def json(self):
        return {}

    def raise_for_status(self):
        pass


def recording_http_client(seen):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            seen.append(url)
            return _Response()

    return _Client


async def processing_url(market, call, **kwargs):
    seen = []
    client = ExchangeAPIClient(
        base_url=CONFIG["baseUrl"],
        login_user_id=CONFIG["LoginUserId"],
        participant_id=CONFIG["ParticipantId"],
        portfolio_id=CONFIG["PortfolioId"],
        bearer_token=CONFIG["BearerToken"],
        market_type=market,
    )
    with patch("services.processing.app.v2.iex.masterApi.httpx.AsyncClient", recording_http_client(seen)), \
         patch.object(ExchangeAPIClient, "_log_to_db", AsyncMock()):
        await getattr(client, call)(delivery_date=DELIVERY_DATE, **kwargs)
    assert len(seen) == 1
    return seen[0]


class TestProcessingSingleBidBook:
    @pytest.mark.asyncio
    async def test_gdam_sends_type_after_status(self):
        url = await processing_url("gdam", "get_single_bid_dam")
        assert "/gdam/api/v2/singlebid/" in url
        assert params_after(url, "/singlebid/") == [
            "EDPA1", "N2DL0PTC0000", str(DELIVERY_DATE), "EDPA1", "S2PY0PTC0136",
            "ALL", "S2", "ALL", "ALL", "0", "0", "0", "0",
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("market", ["dam", "hpdam"])
    async def test_dam_and_hpdam_keep_their_twelve_parameters(self, market):
        url = await processing_url(market, "get_single_bid_dam")
        assert params_after(url, "/singlebid/") == [
            "EDPA1", "N2DL0PTC0000", str(DELIVERY_DATE), "EDPA1", "S2PY0PTC0136",
            "ALL", "S2", "ALL", "0", "0", "0", "0",
        ]


class TestProcessingTradeBook:
    @pytest.mark.asyncio
    async def test_gdam_sends_type_after_bid_area(self):
        url = await processing_url("gdam", "get_dam_trade_book", bid_area_id="S2")
        assert params_after(url, "/trades/") == [
            "EDPA1", "N2DL0PTC0000", str(DELIVERY_DATE), "EDPA1", "S2PY0PTC0136",
            "ALL", "ALL", "0", "S2", "ALL", "0", "0", "0", "0",
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("market", ["dam", "hpdam"])
    async def test_dam_and_hpdam_keep_their_thirteen_parameters(self, market):
        url = await processing_url(market, "get_dam_trade_book", bid_area_id="S2")
        assert params_after(url, "/trades/") == [
            "EDPA1", "N2DL0PTC0000", str(DELIVERY_DATE), "EDPA1", "S2PY0PTC0136",
            "ALL", "ALL", "0", "S2", "0", "0", "0", "0",
        ]


class TestProcessingBlockBidBook:
    @pytest.mark.asyncio
    async def test_gdam_sends_type_and_fetch_duplicate_instead_of_exception_id(self):
        url = await processing_url("gdam", "get_dam_block_bids")
        # BuySell is 0 (both): GDAM takes 0/1/2, not "ALL".
        assert params_after(url, "/blockbid/") == [
            "EDPA1", "N2DL0PTC0000", str(DELIVERY_DATE), "EDPA1", "S2PY0PTC0136",
            "ALL", "ALL", "0", "ALL", "ALL", "ALL", "N", "0", "0", "0",
        ]

    @pytest.mark.asyncio
    async def test_hpdam_sends_fetch_duplicate_instead_of_exception_id(self):
        url = await processing_url("hpdam", "get_dam_block_bids")
        # HPDAM 2.0 §10.3: ...,{Status},{FetchDuplicate},{OrderId},...; BuySell 0 (both).
        assert params_after(url, "/blockbid/") == [
            "EDPA1", "N2DL0PTC0000", str(DELIVERY_DATE), "EDPA1", "S2PY0PTC0136",
            "ALL", "ALL", "0", "ALL", "ALL", "N", "0", "0", "0",
        ]

    @pytest.mark.asyncio
    async def test_dam_keeps_exception_id(self):
        url = await processing_url("dam", "get_dam_block_bids")
        assert params_after(url, "/blockbid/") == [
            "EDPA1", "N2DL0PTC0000", str(DELIVERY_DATE), "EDPA1", "S2PY0PTC0136",
            "ALL", "ALL", "ALL", "ALL", "ALL", "0", "0", "0", "0",
        ]


class TestSchedulerGdamBook:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "call,config_lookup,status",
        [
            ("get_live_single_bid_gdam", "_get_live_exchange_config", "Executed"),
            ("get_single_bid_gdam", "_get_exchange_config", "ALL"),
        ],
    )
    async def test_gdam_book_sends_type_after_status(self, call, config_lookup, status):
        execute = AsyncMock(return_value={"SingleBidDetails": [], "TotalRecord": 0})
        with patch.object(scheduler_exchange_client, config_lookup, AsyncMock(return_value=CONFIG)), \
             patch.object(scheduler_exchange_client, "_execute_and_log", execute):
            await getattr(scheduler_exchange_client, call)(DELIVERY_DATE)
        url = execute.await_args.args[0]
        assert "/gdam/api/v2/singlebid/" in url
        assert params_after(url, "/singlebid/") == [
            "EDPA1", "N2DL0PTC0000", str(DELIVERY_DATE), "EDPA1", "S2PY0PTC0136",
            "ALL", "S2", status, "ALL", "0", "0", "0", "0",
        ]

    @pytest.mark.asyncio
    async def test_live_dam_book_is_unchanged(self):
        execute = AsyncMock(return_value={"SingleBidDetails": [], "TotalRecord": 0})
        with patch.object(scheduler_exchange_client, "_get_live_exchange_config", AsyncMock(return_value=CONFIG)), \
             patch.object(scheduler_exchange_client, "_execute_and_log", execute):
            await scheduler_exchange_client.get_live_single_bid_dam(DELIVERY_DATE)
        assert params_after(execute.await_args.args[0], "/singlebid/") == [
            "EDPA1", "N2DL0PTC0000", str(DELIVERY_DATE), "EDPA1", "S2PY0PTC0136",
            "ALL", "S2", "Executed", "0", "0", "0", "0",
        ]
