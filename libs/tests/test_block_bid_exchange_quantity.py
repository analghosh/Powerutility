"""Block bid Quantity must be a magnitude signed by direction.

Buy block bids are stored negative in `block_bid_current_data` (743 of 745 Buy rows
in the UAT data), while Sell rows are stored positive. The payload builder used to
negate only for Sell, so a stored Buy went to IEX with a negative Quantity — a
sell-side figure on a "B" order. Every Buy block bid IEX ever saw was rejected
(19/19 on RTM), while Sells reached "Submitted".
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.services.block_bid_services import (  # noqa: E402
    exchange_quantity,
)

DECIMAL = 10  # OrderQtyDecimal as the exchange reports it
FULL_SPLIT = 100


def test_buy_stored_negative_is_sent_positive():
    """The bug: -50 MW stored for a Buy used to travel as -500."""
    assert exchange_quantity(-50, FULL_SPLIT, DECIMAL, "B") == 500


def test_sell_stored_positive_is_sent_negative():
    assert exchange_quantity(100, FULL_SPLIT, DECIMAL, "S") == -1000


def test_sell_stored_negative_is_still_sent_negative():
    """A handful of Sell rows are stored negative; they must not double-negate."""
    assert exchange_quantity(-25, FULL_SPLIT, DECIMAL, "S") == -250


def test_buy_stored_positive_stays_positive():
    assert exchange_quantity(50, FULL_SPLIT, DECIMAL, "B") == 500


@pytest.mark.parametrize("buy_sell", ["B", "S"])
def test_direction_only_changes_the_sign(buy_sell):
    magnitude = abs(exchange_quantity(-40, FULL_SPLIT, DECIMAL, buy_sell))
    assert magnitude == abs(exchange_quantity(40, FULL_SPLIT, DECIMAL, buy_sell))
    assert magnitude == 400


def test_iex_split_scales_the_magnitude():
    """IEX-60_PXIL-40 sends only the IEX share."""
    assert exchange_quantity(-50, 60, DECIMAL, "B") == 300


def test_zero_stays_zero():
    assert exchange_quantity(0, FULL_SPLIT, DECIMAL, "B") == 0
    assert exchange_quantity(0, FULL_SPLIT, DECIMAL, "S") == 0
