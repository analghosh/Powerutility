"""
GET /gdam-bidding/run-live-sync and /hpdam-bidding/run-live-sync proxy to the
scheduler's trigger for their own market.

They pass the chosen delivery date through, hand the scheduler's 400/502 back to
the caller instead of flattening them, and exist only behind authentication.
"""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from services.processing.app.v2.api.router import new_gdam_bidding_routes as gdam_routes
from services.processing.app.v2.api.router import new_hpdam_bidding_routes as hpdam_routes

MARKETS = [
    ("gdam", gdam_routes, gdam_routes.run_gdam_live_sync),
    ("hpdam", hpdam_routes, hpdam_routes.run_hpdam_live_sync),
]


class _Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def scheduler_answering(status_code, body, calls):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, params=None):
            calls.append((url, params))
            return _Response(status_code, body)

    return _Client


async def run(handler, bid_status_date, status_code, body):
    calls = []
    with patch("httpx.AsyncClient", scheduler_answering(status_code, body, calls)):
        result = await handler(request=None, bid_status_date=bid_status_date)
    return result, calls


@pytest.mark.parametrize("market,routes,handler", MARKETS)
class TestRunDayAheadLiveSync:
    @pytest.mark.asyncio
    async def test_date_is_passed_to_the_markets_own_trigger(self, market, routes, handler):
        result, calls = await run(handler, "2026-10-03", 200, {"status": "success", "data": {}})
        url, params = calls[0]
        assert url.endswith(f"/api/v1/scheduler/bid-trade-live-sync/trigger-{market}")
        assert params == {"delivery_date": "2026-10-03"}
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_without_a_date_no_date_is_sent(self, market, routes, handler):
        _, calls = await run(handler, None, 200, {"status": "success", "data": {}})
        assert calls[0][1] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [400, 502])
    async def test_scheduler_errors_keep_their_status_and_reason(self, market, routes, handler, status_code):
        reason = f"{market.upper()} sync failed: Server error '500 Internal Server Error'"
        with pytest.raises(HTTPException) as err:
            await run(handler, "2026-10-03", status_code, {"detail": reason})
        assert err.value.status_code == status_code
        assert err.value.detail == reason

    def test_route_is_authenticated_only(self, market, routes, handler):
        authenticated = {route.path for route in routes.router.routes}
        public = {route.path for route in routes.public_router.routes}
        assert f"/{market}-bidding/run-live-sync" in authenticated
        assert f"/{market}-bidding/public/run-live-sync" not in public
