"""The event registry.

This is the only file that changes when a new notification event is added:
one entry here plus one notify() call at the source.

Wording is adapted from the vendor's own abandoned notification payloads at
block_bid_services.py:125-133 and :200-209, which were written and never
delivered. Their hardcoded "RTM" is replaced with a template -- that path also
serves DAM, GDAM and HPDAM, and market-hardcoding is a bug already fixed twice
in this codebase.
"""

from __future__ import annotations

from typing import Dict

from libs.notifications.audience import AnyOf, HasPermission, UsernamesFromContext
from libs.notifications.models import EventType, Severity

DAY_AHEAD_MARKETS = frozenset({"DAM", "GDAM", "HPDAM"})

CATEGORY_BIDDING = "bidding"

APPROVAL_SCREEN = {"dam": "/dam/block-bid-approval", "rtm": "/rtm/rtm-block-bid-approval"}
SUBMISSION_SCREEN = {
    "dam": "/dam/dam-block-bid-submission",
    "rtm": "/rtm/rtm-block-bid-submission",
}

# What the user calls each market. The sidebar
# (client/src/components/common/sidebar/sidemenu/sidemenu.jsx) labels every DAM
# screen "iDAM ..." -- "iDAM Single Bid", "iDAM Block Bid Approval". A
# notification row reading "DAM" therefore sends the reader looking for a menu
# entry that does not exist under that name. Only DAM diverges; the rest are
# spelled the same in the menu as in the data.
MARKET_DISPLAY_NAME = {"DAM": "iDAM"}


def market_display_name(market_type: str) -> str:
    """The market's name as the UI spells it, for the row's Area column."""
    market = (market_type or "").strip().upper()
    return MARKET_DISPLAY_NAME.get(market, market)


def approval_permission_market(market_type: str) -> str:
    """Which market's approval permission gates this market's block bids.

    DAM, GDAM and HPDAM are approved on one screen, so they share its permission.
    Verified against live data: only dam_ and rtm_ approval permissions exist, so
    templating the permission on the market name builds strings no role grants and
    those markets notify nobody with no error raised.
    """
    market = (market_type or "").strip().upper()
    return "dam" if market in DAY_AHEAD_MARKETS else market.lower()


# Single-bid screens. Note these are NOT symmetrical with each other the way the
# block screens are -- DAM approves at /dam/dam-bid-approval while RTM approves at
# /rtm/single-bid-approval -- which is exactly why this is a lookup table and not
# an f-string. Verified against client/src/main.jsx.
SINGLE_APPROVAL_SCREEN = {
    "dam": "/dam/dam-bid-approval",
    "rtm": "/rtm/single-bid-approval",
}
SINGLE_SUBMISSION_SCREEN = {
    "dam": "/dam/bid-submission-status",
    "rtm": "/rtm/bid-submission-status",
}


def approval_screen_path(market_type: str) -> str:
    key = approval_permission_market(market_type)
    if key not in APPROVAL_SCREEN:
        raise ValueError(
            f"Unknown market type: {market_type!r}. "
            f"Supported markets: DAM, GDAM, HPDAM, RTM"
        )
    return APPROVAL_SCREEN[key]


def submission_screen_path(market_type: str) -> str:
    key = approval_permission_market(market_type)
    if key not in SUBMISSION_SCREEN:
        raise ValueError(
            f"Unknown market type: {market_type!r}. "
            f"Supported markets: DAM, GDAM, HPDAM, RTM"
        )
    return SUBMISSION_SCREEN[key]


def single_approval_screen_path(market_type: str) -> str:
    key = approval_permission_market(market_type)
    if key not in SINGLE_APPROVAL_SCREEN:
        raise ValueError(
            f"Unknown market type: {market_type!r}. "
            f"Supported markets: DAM, GDAM, HPDAM, RTM"
        )
    return SINGLE_APPROVAL_SCREEN[key]


def single_submission_screen_path(market_type: str) -> str:
    key = approval_permission_market(market_type)
    if key not in SINGLE_SUBMISSION_SCREEN:
        raise ValueError(
            f"Unknown market type: {market_type!r}. "
            f"Supported markets: DAM, GDAM, HPDAM, RTM"
        )
    return SINGLE_SUBMISSION_SCREEN[key]


REGISTRY: Dict[str, EventType] = {
    "block_bid.submitted_for_approval": EventType(
        key="block_bid.submitted_for_approval",
        severity=Severity.ACTION_REQUIRED,
        # The market moved out of the title and into `area`. The row now shows
        # both side by side, and "DAM block bid awaiting approval" beside an
        # Area column reading "iDAM Block Bid" said the same thing twice.
        title="Awaiting approval",
        body="{actor_name} submitted {bid_count} {market_type} block bid(s) for {schedule_date}.",
        audience=HasPermission("{approval_market}_block_bid_approval:read"),
        link="{approval_path}?date={schedule_date}",
        category=CATEGORY_BIDDING,
        area="{market_name} Block Bid",
    ),
    "block_bid.status_changed": EventType(
        key="block_bid.status_changed",
        severity=Severity.INFO,
        title="Bid {status_lower}",
        body="{actor_name} {status_lower} {bid_count} {market_type} block bid(s) for {schedule_date}.",
        audience=AnyOf(
            UsernamesFromContext("maker_usernames"),
            HasPermission("{approval_market}_block_bid_approval:read"),
        ),
        link="{submission_path}?date={schedule_date}",
        category=CATEGORY_BIDDING,
        area="{market_name} Block Bid",
    ),
    # Single bids. One pair of entries covers all four markets, because the
    # permission and the screen are both templated: `dam_single_bid_approval:read`
    # and `rtm_single_bid_approval:read` are the only two that exist in live data,
    # and approval_permission_market() folds GDAM and HPDAM onto dam.
    "single_bid.submitted_for_approval": EventType(
        key="single_bid.submitted_for_approval",
        severity=Severity.ACTION_REQUIRED,
        title="Awaiting approval",
        body="{actor_name} submitted {bid_count} {market_type} single bid(s) for {schedule_date}.",
        audience=HasPermission("{approval_market}_single_bid_approval:read"),
        link="{approval_path}?date={schedule_date}",
        category=CATEGORY_BIDDING,
        area="{market_name} Single Bid",
    ),
    "single_bid.status_changed": EventType(
        key="single_bid.status_changed",
        severity=Severity.INFO,
        title="Bid {status_lower}",
        body="{actor_name} {status_lower} {bid_count} {market_type} single bid(s) for {schedule_date}.",
        audience=AnyOf(
            UsernamesFromContext("maker_usernames"),
            HasPermission("{approval_market}_single_bid_approval:read"),
        ),
        link="{submission_path}?date={schedule_date}",
        category=CATEGORY_BIDDING,
        area="{market_name} Single Bid",
    ),
}


def get_event(key: str) -> EventType:
    if key not in REGISTRY:
        raise KeyError(f"Unknown notification event: {key}")
    return REGISTRY[key]
