"""Every label a notification row shows is rendered server-side.

The /notifications page shows six fields per row -- type, date and time,
description, area, actor and a link button. Only three of them existed before:
title, body and link. Area and category are added HERE, in the registry, rather
than derived on the client from `event_key`, for two reasons:

  1. A client-side event_key -> label map is a second copy of the registry. It
     drifts the first time an event is added on the backend alone, and the
     symptom is a blank column, not an error.
  2. The label is frozen into the Mongo document at emit time, so a row always
     reads the way it read when it was sent. Renaming a screen later cannot
     retroactively relabel a year of approval history.

Documents written before this change carry neither field. They are NOT
backfilled -- they age out on the 90-day TTL -- so the client renders a neutral
placeholder for them.
"""

from datetime import datetime

import pytest

from libs.notifications.events import REGISTRY, get_event, market_display_name
from libs.notifications.models import Notification, Severity


def test_every_registered_event_declares_a_category_and_an_area():
    for key, event in REGISTRY.items():
        assert event.category, f"{key} has no category"
        assert event.area, f"{key} has no area"


def test_render_returns_category_and_area():
    event = get_event("block_bid.submitted_for_approval")

    title, body, link, category, area = event.render(
        {
            "market_type": "DAM",
            "market_name": market_display_name("DAM"),
            "approval_market": "dam",
            "approval_path": "/dam/block-bid-approval",
            "bid_count": 3,
            "schedule_date": "2026-08-10",
            "actor_name": "mukul",
        }
    )

    assert category == "bidding"
    assert area == "iDAM Block Bid"
    assert link == "/dam/block-bid-approval?date=2026-08-10"


def test_the_title_is_the_type_and_no_longer_repeats_the_market():
    """The row shows Area beside Type. "DAM block bid awaiting approval" next to
    an Area column reading "iDAM Block Bid" said the same thing twice."""
    event = get_event("block_bid.submitted_for_approval")

    title, _, _, _, _ = event.render(
        {
            "market_type": "DAM",
            "market_name": market_display_name("DAM"),
            "approval_market": "dam",
            "approval_path": "/dam/block-bid-approval",
            "bid_count": 3,
            "schedule_date": "2026-08-10",
            "actor_name": "mukul",
        }
    )

    assert title == "Awaiting approval"
    assert "DAM" not in title


def test_status_change_title_carries_the_decision():
    event = get_event("block_bid.status_changed")

    title, body, _, _, area = event.render(
        {
            "market_type": "RTM",
            "market_name": market_display_name("RTM"),
            "approval_market": "rtm",
            "submission_path": "/rtm/rtm-block-bid-submission",
            "status_lower": "approved",
            "maker_usernames": ["mukul"],
            "bid_count": 2,
            "schedule_date": "2026-08-10",
            "actor_name": "ashutosh",
        }
    )

    assert title == "Bid approved"
    assert area == "RTM Block Bid"
    assert "ashutosh approved 2 RTM block bid(s) for 2026-08-10." == body


def test_dam_displays_as_idam_because_that_is_what_the_sidebar_calls_it():
    """client/src/components/common/sidebar/sidemenu/sidemenu.jsx labels every
    DAM screen "iDAM ...". A notification row that says "DAM" sends the user
    looking for a menu entry that does not exist under that name."""
    assert market_display_name("DAM") == "iDAM"
    assert market_display_name("dam") == "iDAM"
    assert market_display_name("RTM") == "RTM"
    assert market_display_name("GDAM") == "GDAM"
    assert market_display_name("HPDAM") == "HPDAM"


def test_document_carries_category_and_area():
    doc = Notification(
        user_id="u1",
        event_key="block_bid.submitted_for_approval",
        title="Awaiting approval",
        body="body",
        payload={},
        link="/x",
        actor={"user_id": "u2", "name": "mukul"},
        severity=Severity.ACTION_REQUIRED.value,
        category="bidding",
        area="iDAM Block Bid",
        created_at=datetime(2026, 8, 9, 10, 0, 0),
    ).to_document()

    assert doc["category"] == "bidding"
    assert doc["area"] == "iDAM Block Bid"
