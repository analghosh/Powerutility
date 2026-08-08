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

APPROVAL_SCREEN = {"dam": "/dam/block-bid-approval", "rtm": "/rtm/rtm-block-bid-approval"}
SUBMISSION_SCREEN = {
    "dam": "/dam/dam-block-bid-submission",
    "rtm": "/rtm/rtm-block-bid-submission",
}


def approval_permission_market(market_type: str) -> str:
    """Which market's approval permission gates this market's block bids.

    DAM, GDAM and HPDAM are approved on one screen, so they share its permission.
    Verified against live data: only dam_ and rtm_ approval permissions exist, so
    templating the permission on the market name builds strings no role grants and
    those markets notify nobody with no error raised.
    """
    market = (market_type or "").strip().upper()
    return "dam" if market in DAY_AHEAD_MARKETS else market.lower()


def approval_screen_path(market_type: str) -> str:
    return APPROVAL_SCREEN[approval_permission_market(market_type)]


def submission_screen_path(market_type: str) -> str:
    return SUBMISSION_SCREEN[approval_permission_market(market_type)]


REGISTRY: Dict[str, EventType] = {
    "block_bid.submitted_for_approval": EventType(
        key="block_bid.submitted_for_approval",
        severity=Severity.ACTION_REQUIRED,
        title="{market_type} block bid awaiting approval",
        body="{actor_name} submitted {bid_count} {market_type} block bid(s) for {schedule_date}.",
        audience=HasPermission("{approval_market}_block_bid_approval:read"),
        link="{approval_path}?date={schedule_date}",
    ),
    "block_bid.status_changed": EventType(
        key="block_bid.status_changed",
        severity=Severity.INFO,
        title="{market_type} block bid {status_lower}",
        body="{actor_name} {status_lower} {bid_count} {market_type} block bid(s) for {schedule_date}.",
        audience=AnyOf(
            UsernamesFromContext("maker_usernames"),
            HasPermission("{approval_market}_block_bid_approval:read"),
        ),
        link="{submission_path}?date={schedule_date}",
    ),
}


def get_event(key: str) -> EventType:
    if key not in REGISTRY:
        raise KeyError(f"Unknown notification event: {key}")
    return REGISTRY[key]
