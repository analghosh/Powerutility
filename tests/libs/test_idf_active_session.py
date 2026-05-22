"""Tests for idf_active_session helpers."""

from libs.helpers.idf_active_session import (
    active_session_should_flip_manual_to_auto,
    csv_forecast_mw,
    merge_time_blocks_active_through_session_48,
)


def test_both_api_only_does_not_flip():
    flip, trigger = active_session_should_flip_manual_to_auto(
        [9, 10], {},  # no csv
    )
    assert flip is True  # missing csv on active blocks -> flip


def test_both_csv_valid_stays_manual():
    csv_by = {
        9: {"forecast": 100.0},
        10: {"forecast": 110.0},
    }
    flip, trigger = active_session_should_flip_manual_to_auto([9, 10], csv_by)
    assert flip is False
    assert trigger == ""


def test_mixed_csv_api_does_not_flip_without_zero():
    csv_by = {9: {"forecast": 100.0}}
    flip, trigger = active_session_should_flip_manual_to_auto([9, 10], csv_by)
    assert flip is True  # block 10 missing csv


def test_zero_in_active_session_csv_flips():
    csv_by = {9: {"forecast": 0}, 10: {"forecast": 100.0}}
    flip, trigger = active_session_should_flip_manual_to_auto([9, 10], csv_by)
    assert flip is True
    assert trigger == "active_session_zero_or_blank_forecast"


def test_requires_exactly_two_active_blocks():
    csv_by = {9: {"forecast": 0}}
    flip, _ = active_session_should_flip_manual_to_auto([9], csv_by)
    assert flip is False


def test_merge_scope_active_through_96():
    assert merge_time_blocks_active_through_session_48(9) == list(range(9, 97))
    assert merge_time_blocks_active_through_session_48(96) == [96]


def test_csv_zero_writes_zero_when_treat_blank():
    assert csv_forecast_mw(0, treat_blank_as_zero=True) == 0.0
    assert csv_forecast_mw(None, treat_blank_as_zero=True) == 0.0
    assert csv_forecast_mw(0, treat_blank_as_zero=False) is None
