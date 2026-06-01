"""Tests for idf_forecast_session helpers."""

from libs.helpers.idf_forecast_session import (
    resolve_session_merge_ops,
    session_to_time_blocks,
    time_block_to_session_id,
)
from libs.models.forecast_serve_entity import FORECAST_SOURCE_CSV, FORECAST_SOURCE_RTM_API


def test_session_blocks_pair():
    assert session_to_time_blocks(10) == (19, 20)
    assert time_block_to_session_id(19) == 10
    assert time_block_to_session_id(20) == 10


def test_resolve_both_csv():
    csv_by = {
        19: {"forecast": 100.0, "forecasted_frequency": 49.0},
        20: {"forecast": 110.0},
    }
    ops = resolve_session_merge_ops(
        10, csv_by, {}, {}, FORECAST_SOURCE_CSV, "rtm_forecast_api"
    )
    assert ops is not None
    assert all(o[3] == FORECAST_SOURCE_CSV for o in ops)


def test_resolve_csv_gap_uses_api_both():
    csv_by = {19: {"forecast": 0}, 20: {"forecast": 150.0}}
    api_by = {
        19: {"forecast": 80.0, "forecasted_frequency": 48.0},
        20: {"forecast": 90.0, "forecasted_frequency": 48.5},
    }
    ops = resolve_session_merge_ops(
        10, csv_by, api_by, {}, FORECAST_SOURCE_CSV, "rtm_forecast_api"
    )
    assert ops is not None
    assert ops[0][1] == 80.0
    assert ops[1][1] == 90.0
    assert all(o[3] == "rtm_forecast_api" for o in ops)
