"""
Failure reasons leave the service log for the run history and the System
Health page, so they must never carry a credential.
"""

import asyncio

from libs.utils.failure_reasons import redact_secrets, safe_reason

GRID_URL = (
    "https://gateway.grid-india.in/POSOCO/reports/1.0/WebAccessAPI/"
    "GetUtilityExternalSharedData?apikey=b8476349-aaaa-bbbb-cccc-e223d5cb0125&date=2026-09-28"
)


def test_an_api_key_in_a_query_string_is_masked():
    masked = redact_secrets(f"Cannot connect: {GRID_URL}")
    assert "b8476349" not in masked
    assert "apikey=***&date=2026-09-28" in masked


def test_other_credential_shapes_are_masked():
    text = "api_key=abc123 token=zzz password=hunter2 Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.x.y"
    masked = redact_secrets(text)
    for secret in ("abc123", "zzz", "hunter2", "eyJhbGciOiJIUzI1NiJ9"):
        assert secret not in masked


def test_an_exception_with_no_message_is_named_by_its_type():
    assert safe_reason(asyncio.TimeoutError()) == "TimeoutError"


def test_reasons_are_one_line_and_bounded():
    assert safe_reason("first\n   second") == "first second"
    long = safe_reason("x" * 500, limit=50)
    assert len(long) == 50 and long.endswith("…")


def test_nothing_in_nothing_out():
    assert safe_reason(None) is None
    assert safe_reason("   ") is None
