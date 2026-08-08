import pytest

from libs.notifications.audience import HasPermission
from libs.notifications.events import REGISTRY, get_event
from libs.notifications.models import Severity


def test_block_bid_submitted_notifies_approval_screen_holders():
    event = get_event("block_bid.submitted_for_approval")
    assert isinstance(event.audience, HasPermission)
    assert event.severity == Severity.ACTION_REQUIRED
    assert event.audience.permission == "{market_lower}_block_bid_approval:read"


def test_block_bid_status_changed_is_informational_not_actionable():
    event = REGISTRY["block_bid.status_changed"]
    assert event.severity == Severity.INFO
    assert isinstance(event.audience, HasPermission)


def test_titles_are_templated_never_hardcoded_to_one_market():
    """block_bid_services.py:128 hardcodes 'RTM' on a path serving four markets."""
    for key, event in REGISTRY.items():
        assert "RTM" not in event.title, f"{key} hardcodes RTM"
        assert "DAM" not in event.title, f"{key} hardcodes DAM"


def test_every_registry_key_matches_its_event_key():
    for key, event in REGISTRY.items():
        assert event.key == key


def test_unknown_event_key_raises_with_a_useful_message():
    with pytest.raises(KeyError, match="not_a_real_event"):
        get_event("not_a_real_event")


def test_block_bid_submitted_renders_for_every_day_ahead_market():
    event = get_event("block_bid.submitted_for_approval")
    for market in ("DAM", "GDAM", "HPDAM", "RTM"):
        title, body, link = event.render({
            "market_type": market,
            "market_lower": market.lower(),
            "actor_name": "A. Kumar",
            "bid_count": 3,
            "schedule_date": "2026-08-09",
        })
        assert market in title
        assert "3" in body
