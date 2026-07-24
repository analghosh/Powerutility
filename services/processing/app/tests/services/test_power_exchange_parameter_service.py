"""
Unit Test Suite: PowerExchangeParameterService
Module: services.processing.app.v2.services.power_exchange_parameter_services

Tests cover:
 - Filtration behavior (exchange, market, combined)
 - Validation rules (market values, RTM ocf_standalone constraint, allocation range)
 - CRUD operations (create, update, delete)

Standard: pytest + unittest.mock (AsyncMock)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ...v2.services.power_exchange_parameter_services import PowerExchangeParameterService

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures & Shared Test Data
# ─────────────────────────────────────────────────────────────────────────────

MOCK_SEEDED_DOCUMENTS = [
    {"_id": "id1",  "exchange": "iex",  "market": "rtm",   "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "standalone", "premium_discount": "premium", "is_deleted": False},
    {"_id": "id2",  "exchange": "iex",  "market": "dam",   "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "ocf",        "premium_discount": "premium", "is_deleted": False},
    {"_id": "id3",  "exchange": "iex",  "market": "gdam",  "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "ocf",        "premium_discount": "premium", "is_deleted": False},
    {"_id": "id4",  "exchange": "iex",  "market": "hpdam", "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "standalone", "premium_discount": "premium", "is_deleted": False},
    {"_id": "id5",  "exchange": "hpx",  "market": "rtm",   "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "standalone", "premium_discount": "premium", "is_deleted": False},
    {"_id": "id6",  "exchange": "hpx",  "market": "dam",   "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "ocf",        "premium_discount": "premium", "is_deleted": False},
    {"_id": "id7",  "exchange": "hpx",  "market": "gdam",  "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "ocf",        "premium_discount": "premium", "is_deleted": False},
    {"_id": "id8",  "exchange": "hpx",  "market": "hpdam", "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "standalone", "premium_discount": "premium", "is_deleted": False},
    {"_id": "id9",  "exchange": "pxil", "market": "rtm",   "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "standalone", "premium_discount": "premium", "is_deleted": False},
    {"_id": "id10", "exchange": "pxil", "market": "dam",   "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "ocf",        "premium_discount": "premium", "is_deleted": False},
    {"_id": "id11", "exchange": "pxil", "market": "gdam",  "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "ocf",        "premium_discount": "premium", "is_deleted": False},
    {"_id": "id12", "exchange": "pxil", "market": "hpdam", "bid_type": "single_bid", "allocation_percent": 100.0, "ocf_standalone": "standalone", "premium_discount": "premium", "is_deleted": False},
]


@pytest.fixture
def mock_repo():
    """Patches the repository singleton used by the service layer"""
    with patch('app.v2.services.power_exchange_parameter_services.power_exchange_parameter_repository') as repo:
        yield repo


# ─────────────────────────────────────────────────────────────────────────────
# TC-01 to TC-05: Filtration Behaviour
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tc01_get_all_no_filters_returns_all_12(mock_repo):
    """TC-01: No query filters → returns all 12 seeded configurations"""
    service = PowerExchangeParameterService()
    mock_repo.get_all_parameters = AsyncMock(return_value={
        "parameters": MOCK_SEEDED_DOCUMENTS,
        "total": 12, "skip": 0, "limit": 100
    })

    result = await service.get_all_parameters()

    assert result["total"] == 12
    assert len(result["parameters"]) == 12
    mock_repo.get_all_parameters.assert_called_once_with(
        skip=0, limit=100, sort_by="created_at", sort_order=-1, exchange=None, market=None
    )


@pytest.mark.asyncio
async def test_tc02_filter_by_exchange_returns_correct_docs(mock_repo):
    """TC-02: exchange=iex → returns only iex configs (4 docs)"""
    service = PowerExchangeParameterService()
    filtered = [d for d in MOCK_SEEDED_DOCUMENTS if d["exchange"] == "iex"]
    mock_repo.get_all_parameters = AsyncMock(return_value={
        "parameters": filtered, "total": len(filtered), "skip": 0, "limit": 100
    })

    result = await service.get_all_parameters(exchange="iex")

    assert result["total"] == 4
    assert all(d["exchange"] == "iex" for d in result["parameters"])


@pytest.mark.asyncio
async def test_tc03_filter_by_market_rtm_returns_rtm_only(mock_repo):
    """TC-03: market=rtm → returns only 3 RTM docs (one per exchange)"""
    service = PowerExchangeParameterService()
    filtered = [d for d in MOCK_SEEDED_DOCUMENTS if d["market"] == "rtm"]
    mock_repo.get_all_parameters = AsyncMock(return_value={
        "parameters": filtered, "total": len(filtered), "skip": 0, "limit": 100
    })

    result = await service.get_all_parameters(market="rtm")

    assert result["total"] == 3
    assert all(d["market"] == "rtm" for d in result["parameters"])


@pytest.mark.asyncio
async def test_tc04_filter_by_market_dam_returns_all_dam_types(mock_repo):
    """TC-04: market=dam → returns dam, gdam, hpdam (9 docs total)"""
    service = PowerExchangeParameterService()
    filtered = [d for d in MOCK_SEEDED_DOCUMENTS if d["market"] in {"dam", "gdam", "hpdam"}]
    mock_repo.get_all_parameters = AsyncMock(return_value={
        "parameters": filtered, "total": len(filtered), "skip": 0, "limit": 100
    })

    result = await service.get_all_parameters(market="dam")

    assert result["total"] == 9
    assert all(d["market"] in {"dam", "gdam", "hpdam"} for d in result["parameters"])


@pytest.mark.asyncio
async def test_tc05_filter_by_exchange_and_market_combined(mock_repo):
    """TC-05: exchange=iex&market=dam → returns 3 DAM configs for iex only"""
    service = PowerExchangeParameterService()
    filtered = [d for d in MOCK_SEEDED_DOCUMENTS if d["exchange"] == "iex" and d["market"] in {"dam", "gdam", "hpdam"}]
    mock_repo.get_all_parameters = AsyncMock(return_value={
        "parameters": filtered, "total": len(filtered), "skip": 0, "limit": 100
    })

    result = await service.get_all_parameters(exchange="iex", market="dam")

    assert result["total"] == 3
    assert all(d["exchange"] == "iex" for d in result["parameters"])
    assert all(d["market"] in {"dam", "gdam", "hpdam"} for d in result["parameters"])


# ─────────────────────────────────────────────────────────────────────────────
# TC-06 to TC-09: Validation Constraints
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tc06_invalid_exchange_filter_raises_value_error(mock_repo):
    """TC-06: Invalid exchange query param → raises ValueError"""
    service = PowerExchangeParameterService()

    with pytest.raises(ValueError) as ctx:
        await service.get_all_parameters(exchange="invalid_exchange")

    assert "Invalid exchange filter" in str(ctx.value)


@pytest.mark.asyncio
async def test_tc07_invalid_market_filter_raises_value_error(mock_repo):
    """TC-07: Invalid market query param (idam removed) → raises ValueError"""
    service = PowerExchangeParameterService()

    with pytest.raises(ValueError) as ctx:
        await service.get_all_parameters(market="idam")

    assert "Invalid market filter 'idam'" in str(ctx.value)


def test_tc08_rtm_market_ocf_standalone_must_be_standalone():
    """TC-08: market=rtm with ocf_standalone=ocf → raises ValueError"""
    service = PowerExchangeParameterService()

    with pytest.raises(ValueError) as ctx:
        service._validate_parameter_data({
            "exchange": "iex",
            "market": "rtm",
            "ocf_standalone": "ocf"
        })

    assert "ocf_standalone must be 'standalone' when market is 'rtm'" in str(ctx.value)


def test_tc09_rtm_market_with_standalone_passes_validation():
    """TC-09: market=rtm with ocf_standalone=standalone → passes validation"""
    service = PowerExchangeParameterService()

    data = {"exchange": "iex", "market": "rtm", "ocf_standalone": "standalone"}
    service._validate_parameter_data(data)  # Should not raise

    assert data["ocf_standalone"] == "standalone"


def test_tc10_allocation_percent_out_of_range_raises_value_error():
    """TC-10: allocation_percent > 100 → raises ValueError"""
    service = PowerExchangeParameterService()

    with pytest.raises(ValueError) as ctx:
        service._validate_parameter_data({
            "exchange": "iex",
            "market": "dam",
            "allocation_percent": 150
        })

    assert "allocation_percent must be between 0 and 100" in str(ctx.value)


def test_tc11_invalid_bid_type_raises_value_error():
    """TC-11: Unknown bid_type value → raises ValueError"""
    service = PowerExchangeParameterService()

    with pytest.raises(ValueError) as ctx:
        service._validate_parameter_data({
            "exchange": "iex",
            "market": "dam",
            "bid_type": "unknown_bid"
        })

    assert "Invalid bid_type" in str(ctx.value)


def test_tc12_removed_market_idam_raises_value_error():
    """TC-12: market=idam (removed from valid set) → raises ValueError"""
    service = PowerExchangeParameterService()

    with pytest.raises(ValueError) as ctx:
        service._validate_parameter_data({
            "exchange": "iex",
            "market": "idam"
        })

    assert "Invalid market 'idam'" in str(ctx.value)


# ─────────────────────────────────────────────────────────────────────────────
# TC-13 to TC-15: CRUD Operations
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tc13_create_parameter_duplicate_raises_value_error(mock_repo):
    """TC-13: Creating duplicate exchange+market config → raises ValueError"""
    service = PowerExchangeParameterService()
    mock_repo.get_parameter_by_exchange_and_market = AsyncMock(
        return_value=MOCK_SEEDED_DOCUMENTS[0]  # Existing document
    )

    with pytest.raises(ValueError) as ctx:
        await service.create_parameter({
            "exchange": "iex",
            "market": "rtm",
            "ocf_standalone": "standalone"
        })

    assert "already exists" in str(ctx.value)


@pytest.mark.asyncio
async def test_tc14_update_parameter_not_found_returns_none(mock_repo):
    """TC-14: Update with non-existent ID → returns None"""
    service = PowerExchangeParameterService()
    mock_repo.get_parameter_by_id = AsyncMock(return_value=None)

    result = await service.update_parameter("nonexistent_id", {"bid_type": "block_bid"})

    assert result is None


@pytest.mark.asyncio
async def test_tc15_delete_parameter_not_found_returns_false(mock_repo):
    """TC-15: Soft delete with non-existent ID → returns False"""
    service = PowerExchangeParameterService()
    mock_repo.delete_parameter = AsyncMock(return_value=False)

    result = await service.delete_parameter("nonexistent_id", deleted_by="test_user")

    assert result is False
