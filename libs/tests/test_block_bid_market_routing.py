"""
Which markets the block bid routes hand to the day-ahead controller.

Markets outside DAY_AHEAD_BLOCK_MARKETS fall through to the RTM controller, which
loads the RTM exchange config and queries get_approved_bids(market_type="RTM").
For HPDAM that means a submit/withdraw/cancel issued for HPDAM acts on RTM bids —
so membership of this set is a correctness boundary, not a formatting detail.
"""
from services.processing.app.v2.api.router.block_bid_routes import (
    DAY_AHEAD_BLOCK_MARKETS,
)


def test_hpdam_is_handled_by_the_day_ahead_controller():
    assert "HPDAM" in DAY_AHEAD_BLOCK_MARKETS


def test_every_day_ahead_market_is_routed():
    assert DAY_AHEAD_BLOCK_MARKETS == {"DAM", "GDAM", "HPDAM"}


def test_rtm_is_not_routed_to_the_day_ahead_controller():
    assert "RTM" not in DAY_AHEAD_BLOCK_MARKETS


def test_tam_variants_are_not_silently_treated_as_day_ahead():
    # TAM/GRTM/R-TAM/D-TAM exist in block_bid_current_data but have no block bid
    # exchange contract. They must not be added here without one.
    for market in ("TAM", "D-TAM", "R-TAM", "GRTM"):
        assert market not in DAY_AHEAD_BLOCK_MARKETS
