"""
Unit tests for active market / active exchange handling in single-bid-table.
"""

import pytest
from unittest.mock import AsyncMock, patch

from services.processing.app.v2.services.parameter_service import parameter_service
from services.processing.app.v2.services.new_dam_bidding_services import DAMBiddingServices
from services.processing.app.v2.services.new_gdam_bidding_services import GDAMBiddingServices
from services.processing.app.v2.services.new_hpdam_bidding_services import HPDAMBiddingServices


@pytest.fixture
def sample_table_data():
    return {
        "table_data": {
            0: {
                1: {
                    "Date": "2026-06-20",
                    "time_block": 1,
                    "dam_forecasted_price": 100,
                    "ceiling_price": 200,
                    "dam_buffer_value": 300,
                    "dam_nminusone_quantum": 400,
                    "dam_noc_buy_value": 500,
                    "dam_noc_sell_value": 600,
                    "price": 0.2,
                    "plant_name": "Test Plant",
                }
            }
        }
    }


@pytest.mark.parametrize("service_class", [DAMBiddingServices, GDAMBiddingServices, HPDAMBiddingServices])
def test_apply_active_limit_prefix_renames_limit_fields(service_class, sample_table_data):
    svc = service_class()
    result = svc._apply_active_limit_prefix(sample_table_data, "IEX", "GDAM")

    row = result["table_data"][0][1]

    assert "IEX_GDAM_forecasted_price" in row
    assert "IEX_GDAM_ceiling_price" in row
    assert "IEX_GDAM_buffer_value" in row
    assert "IEX_GDAM_nminusone_quantum" in row
    assert "IEX_GDAM_noc_buy_value" in row
    assert "IEX_GDAM_noc_sell_value" in row

    # Original keys should be gone
    assert "dam_forecasted_price" not in row
    assert "dam_buffer_value" not in row
    assert "dam_nminusone_quantum" not in row
    assert "dam_noc_buy_value" not in row
    assert "dam_noc_sell_value" not in row

    # Non-limit fields should be untouched
    assert row["Date"] == "2026-06-20"
    assert row["time_block"] == 1
    assert row["price"] == 0.2
    assert row["plant_name"] == "Test Plant"


@pytest.mark.parametrize("service_class", [DAMBiddingServices, GDAMBiddingServices, HPDAMBiddingServices])
def test_apply_active_limit_prefix_with_pxil_hpdam(service_class, sample_table_data):
    svc = service_class()
    result = svc._apply_active_limit_prefix(sample_table_data, "PXIL", "HPDAM")

    row = result["table_data"][0][1]
    assert "PXIL_HPDAM_forecasted_price" in row
    assert "PXIL_HPDAM_ceiling_price" in row


@pytest.mark.parametrize("service_class", [DAMBiddingServices, GDAMBiddingServices, HPDAMBiddingServices])
def test_apply_active_limit_prefix_empty_data(service_class):
    svc = service_class()
    assert svc._apply_active_limit_prefix({}, "IEX", "DAM") == {}
    assert svc._apply_active_limit_prefix(None, "IEX", "DAM") is None
    assert svc._apply_active_limit_prefix({"table_data": {}}, "IEX", "DAM") == {"table_data": {}}


@pytest.mark.parametrize(
    "input_value,expected",
    [
        (["dam", "GDAM"], ["DAM", "GDAM"]),
        ("DAM,GDAM", ["DAM", "GDAM"]),
        ("dam", ["DAM"]),
        (["GDAM", "GDAM", "HPDAM"], ["GDAM", "HPDAM"]),
    ],
)
def test_normalize_markets_valid(input_value, expected):
    assert parameter_service._normalize_markets(input_value) == expected


def test_normalize_markets_invalid():
    with pytest.raises(ValueError, match="Invalid active market"):
        parameter_service._normalize_markets(["DAM", "INVALID"])


def test_normalize_markets_empty():
    assert parameter_service._normalize_markets([]) == []
    assert parameter_service._normalize_markets("") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("service_class", [DAMBiddingServices, GDAMBiddingServices, HPDAMBiddingServices])
async def test_add_additional_market_limit_values(service_class, sample_table_data):
    svc = service_class()

    async def fake_fetch(market, exchange, target_date_dt, from_block, to_block):
        return {
            "forecasted_price": {1: 111},
            "ceiling_price": {1: 222},
            "buffer_value": {1: 333},
            "nminusone_quantum": {1: 444},
            "noc_buy_value": {1: 555},
            "noc_sell_value": {1: 666},
        }

    svc._fetch_limit_values_by_block = fake_fetch

    # First apply primary market prefix so the row looks like production output
    svc._apply_active_limit_prefix(sample_table_data, "IEX", "DAM")

    result = await svc._add_additional_market_limit_values(
        sample_table_data,
        ["DAM", "GDAM"],
        "IEX",
        None,
        1,
        96,
    )

    row = result["table_data"][0][1]
    assert "IEX_DAM_forecasted_price" in row
    assert "IEX_GDAM_forecasted_price" in row
    assert row["IEX_GDAM_forecasted_price"] == 111
    assert row["IEX_GDAM_ceiling_price"] == 222
    assert row["IEX_GDAM_buffer_value"] == 333
    assert row["IEX_GDAM_nminusone_quantum"] == 444
    assert row["IEX_GDAM_noc_buy_value"] == 555
    assert row["IEX_GDAM_noc_sell_value"] == 666


@pytest.mark.asyncio
@pytest.mark.parametrize("service_class", [DAMBiddingServices, GDAMBiddingServices, HPDAMBiddingServices])
async def test_add_additional_market_limit_values_single_market(service_class, sample_table_data):
    svc = service_class()
    result = await svc._add_additional_market_limit_values(
        sample_table_data,
        ["DAM"],
        "IEX",
        None,
        1,
        96,
    )
    row = result["table_data"][0][1]
    # No additional market keys should be added
    assert "IEX_GDAM_forecasted_price" not in row
