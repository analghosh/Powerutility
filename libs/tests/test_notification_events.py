import pytest

from libs.notifications.audience import AnyOf, HasPermission, UsernamesFromContext
from libs.notifications.events import (
    REGISTRY,
    approval_permission_market,
    approval_screen_path,
    get_event,
    submission_screen_path,
)
from libs.notifications.models import Severity


def test_block_bid_submitted_notifies_approval_screen_holders():
    event = get_event("block_bid.submitted_for_approval")
    assert isinstance(event.audience, HasPermission)
    assert event.severity == Severity.ACTION_REQUIRED
    assert event.audience.permission == "{approval_market}_block_bid_approval:read"


def test_block_bid_status_changed_is_informational_not_actionable():
    event = REGISTRY["block_bid.status_changed"]
    assert event.severity == Severity.INFO
    assert isinstance(event.audience, AnyOf)


def test_titles_are_templated_never_hardcoded_to_one_market():
    """block_bid_services.py:128 hardcodes 'RTM' on a path serving four markets.

    The vendor bug this guards against put "RTM" in the description as well as the
    title ("RTM Block Bid Submitted for Approval" / "... RTM block bid(s) ..."), so
    the body is checked too -- a title-only assertion would pass a registry whose
    body still names one market on a path serving four.
    """
    for key, event in REGISTRY.items():
        assert "RTM" not in event.title, f"{key} hardcodes RTM in the title"
        assert "DAM" not in event.title, f"{key} hardcodes DAM in the title"
        assert "RTM" not in event.body, f"{key} hardcodes RTM in the body"
        assert "DAM" not in event.body, f"{key} hardcodes DAM in the body"


def test_every_registry_key_matches_its_event_key():
    for key, event in REGISTRY.items():
        assert event.key == key


def test_unknown_event_key_raises_with_a_useful_message():
    with pytest.raises(KeyError, match="not_a_real_event"):
        get_event("not_a_real_event")


def test_block_bid_submitted_renders_for_every_day_ahead_market():
    event = get_event("block_bid.submitted_for_approval")
    for market in ("DAM", "GDAM", "HPDAM", "RTM"):
        context = {
            "market_type": market,
            "market_lower": market.lower(),
            "actor_name": "A. Kumar",
            "bid_count": 3,
            "schedule_date": "2026-08-09",
            "approval_market": approval_permission_market(market),
            "approval_path": approval_screen_path(market),
        }
        title, body, link = event.render(context)
        assert market in title
        assert "3" in body


def test_approval_permission_market_maps_day_ahead_to_dam():
    assert approval_permission_market("DAM") == "dam"
    assert approval_permission_market("GDAM") == "dam"
    assert approval_permission_market("HPDAM") == "dam"
    assert approval_permission_market("RTM") == "rtm"


def test_approval_permission_market_tolerates_whitespace():
    assert approval_permission_market(" GDAM ") == "dam"
    assert approval_permission_market("  rtm  ") == "rtm"


def test_approval_permission_market_returns_empty_string_for_empty():
    assert approval_permission_market("") == ""


def test_approval_screen_path_returns_real_routes():
    assert approval_screen_path("DAM") == "/dam/block-bid-approval"
    assert approval_screen_path("GDAM") == "/dam/block-bid-approval"
    assert approval_screen_path("HPDAM") == "/dam/block-bid-approval"
    assert approval_screen_path("RTM") == "/rtm/rtm-block-bid-approval"


def test_submission_screen_path_returns_real_routes():
    assert submission_screen_path("DAM") == "/dam/dam-block-bid-submission"
    assert submission_screen_path("GDAM") == "/dam/dam-block-bid-submission"
    assert submission_screen_path("HPDAM") == "/dam/dam-block-bid-submission"
    assert submission_screen_path("RTM") == "/rtm/rtm-block-bid-submission"


def test_no_registry_link_contains_dead_paths():
    """Regression guard: dead paths from the brief's defects must not reappear."""
    for key, event in REGISTRY.items():
        assert "dam-block-bid-approve" not in event.link, f"{key} contains dead path"
        assert "submission-status" not in event.link, f"{key} contains dead path"


def test_approval_screen_path_raises_for_unknown_market():
    with pytest.raises(ValueError, match="TAM"):
        approval_screen_path("TAM")


def test_approval_screen_path_raises_for_empty_market():
    with pytest.raises(ValueError, match=""):
        approval_screen_path("")


def test_submission_screen_path_raises_for_unknown_market():
    with pytest.raises(ValueError, match="TAM"):
        submission_screen_path("TAM")


def test_submission_screen_path_raises_for_empty_market():
    with pytest.raises(ValueError, match=""):
        submission_screen_path("")
