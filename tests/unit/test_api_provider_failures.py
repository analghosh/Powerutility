"""
The API processor records which providers failed and why on its run row, so
System Health can name them. Driven through the real tier/trigger path with
only the per-provider fetch stubbed.
"""

import asyncio
from types import SimpleNamespace

from services.ingestion.app.processors.api_raw_data_processor import APIRawDataProcessor


def _provider(code, name=None, data_type="exchange"):
    return SimpleNamespace(code=code, name=name or code, data_type=data_type)


def _processor(outcomes):
    """A processor whose per-provider fetch answers from ``outcomes``."""
    processor = object.__new__(APIRawDataProcessor)
    processor.job_publisher = SimpleNamespace(publish_job_trigger=lambda **kwargs: None)

    async def fake_fetch(provider):
        outcome = outcomes[provider.code]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    processor._process_provider_data = fake_fetch
    return processor


def test_each_failed_provider_is_recorded_with_its_reason():
    processor = _processor({
        "GRID": {"success": False, "error": "API fetch error: Cannot connect to https://g/x?apikey=SECRET9"},
        "HPX": {"success": False, "error": "No data fetched from API"},
        "IEX": {"success": True, "stored_records": 96},
    })
    failures = []
    results = asyncio.run(processor._process_provider_tier(
        [_provider("GRID", "Grid India Supply"), _provider("HPX"), _provider("IEX")], failures
    ))
    assert results == [False, False, True]
    assert [f["provider_code"] for f in failures] == ["GRID", "HPX"]
    assert failures[0]["provider_name"] == "Grid India Supply"
    assert "SECRET9" not in failures[0]["error"]
    assert failures[1]["error"] == "No data fetched from API"


def test_an_exception_the_provider_loop_does_not_catch_is_still_recorded():
    processor = _processor({"BAD": KeyError("price")})
    failures = []
    results = asyncio.run(processor._process_provider_tier([_provider("BAD")], failures))
    assert results == [False]
    assert failures == [{"provider_code": "BAD", "provider_name": "BAD", "error": "'price'"}]


def test_a_caught_exception_is_recorded_once():
    processor = _processor({"NET": OSError("Connection reset by peer")})
    failures = []
    asyncio.run(processor._process_provider_tier([_provider("NET")], failures))
    assert [f["error"] for f in failures] == ["Connection reset by peer"]


def test_callers_that_do_not_ask_for_failures_are_unchanged():
    processor = _processor({"HPX": {"success": False, "error": "No data fetched from API"}})
    assert asyncio.run(processor._process_provider_tier([_provider("HPX")])) == [False]
