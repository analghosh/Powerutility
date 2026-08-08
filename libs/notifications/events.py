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

from libs.notifications.audience import HasPermission
from libs.notifications.models import EventType, Severity

REGISTRY: Dict[str, EventType] = {
    "block_bid.submitted_for_approval": EventType(
        key="block_bid.submitted_for_approval",
        severity=Severity.ACTION_REQUIRED,
        title="{market_type} block bid awaiting approval",
        body="{actor_name} submitted {bid_count} {market_type} block bid(s) for {schedule_date}.",
        audience=HasPermission("{market_lower}_block_bid_approval:read"),
        link="/dam/dam-block-bid-approve?date={schedule_date}",
    ),
    "block_bid.status_changed": EventType(
        key="block_bid.status_changed",
        severity=Severity.INFO,
        title="{market_type} block bid {status_lower}",
        body="{actor_name} {status_lower} {bid_count} {market_type} block bid(s) for {schedule_date}.",
        audience=HasPermission("{market_lower}_block_bid_approval:read"),
        link="/dam/dam-block-bid-submission-status?date={schedule_date}",
    ),
}


def get_event(key: str) -> EventType:
    if key not in REGISTRY:
        raise KeyError(f"Unknown notification event: {key}")
    return REGISTRY[key]
