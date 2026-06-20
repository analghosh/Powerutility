"""
Unit tests for active market / active exchange handling in single-bid-table.
"""

import pytest

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
