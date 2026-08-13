"""Single-bid notification emission, shared by all four market controllers.

DAM, GDAM, HPDAM and RTM each have their own thin controller wrapping their own
3,000-line service. The emission logic is identical across all four -- only the
market name differs -- so it lives here rather than being copied four times:

  1. Four copies means four places for the rules below to rot independently.
  2. `new_rtm_bidding_services.py` is FENCED by PTC India, verbatim: "Don't touch
     the code of new_rtm_bidding_services.py (Frontend & Backend)." Emission
     therefore cannot live in the services at all, and the controllers are the
     only layer all four markets share.

Callers pass their own market and their own approval repository; nothing here
knows which service it was invoked from.

Every entry point is TOTALLY guarded. By the time either function is called the
bids are already written and the checker's decision is already persisted -- a
Redis outage, an unknown market, or a Mongo hiccup must cost the user a
notification, never their work.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId

from libs.notifications.emitter import notify
from libs.notifications.events import (
    approval_permission_market,
    market_display_name,
    single_approval_screen_path,
    single_submission_screen_path,
)
from libs.notifications.single_bid_context import group_single_bid_decisions

logger = logging.getLogger(__name__)

# The order matters. The client builds `time_block_dates` as
# `${payloadDate}_${timeBlock}` with payloadDate in DD-MM-YYYY
# (client/src/features/iDamSingleBid/hooks/useDAMSingleBid.js:2328-2333), which is
# what the services parse. YYYY-MM-DD is accepted second because the vendor's own
# fallback path pulls an already-normalised date out of `time_block_range` and
# then parses it as DD-MM-YYYY, which raises -- accept both spellings here rather
# than inheriting that bug.
_DATE_FORMATS = ("%d-%m-%Y", "%Y-%m-%d")


def submit_batch_schedule_date(request: Dict[str, Any]) -> str:
    """The schedule date a submit-for-approval batch is for, as YYYY-MM-DD.

    Returns "" when it cannot be determined. The submit request carries no
    top-level schedule_date field -- the date is embedded in each entry of
    `time_block_dates` -- and the service computes it internally without
    returning it, so it has to be re-derived here.
    """
    entries = request.get("time_block_dates") or []
    if not entries:
        return ""

    raw = str(entries[0]).split("_")[0].strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    logger.warning(
        "Could not parse a schedule date from time_block_dates[0]=%r; "
        "skipping the single-bid notification rather than announcing a bid for "
        "no date",
        entries[0],
    )
    return ""


async def notify_single_bid_submitted(
    *, market_type: str, request: Dict[str, Any], actor: Dict[str, Any]
) -> None:
    """Tell this market's approvers that single bids are waiting for them."""
    try:
        bid_count = len(request.get("time_block_range") or [])
        schedule_date = submit_batch_schedule_date(request)

        # Both blanks are skips, not errors. A missing notification is
        # recoverable; a row reading "submitted 0 bid(s) for " is not.
        if not bid_count or not schedule_date:
            logger.info(
                "Skipping %s single-bid submission notification "
                "(bid_count=%s, schedule_date=%r)",
                market_type,
                bid_count,
                schedule_date,
            )
            return

        await notify(
            "single_bid.submitted_for_approval",
            {
                "market_type": market_type,
                "market_name": market_display_name(market_type),
                "approval_market": approval_permission_market(market_type),
                "approval_path": single_approval_screen_path(market_type),
                "bid_count": bid_count,
                "schedule_date": schedule_date,
            },
            actor=actor,
        )
    except Exception:
        logger.exception(
            "Notification emit failed for %s single bid submission", market_type
        )


async def notify_single_bid_decisions(
    *,
    market_type: str,
    updated: Optional[List[Dict[str, Any]]],
    approval_repo: Any,
    actor: Dict[str, Any],
) -> None:
    """Tell the makers and the other checkers what was decided.

    `updated` must be the service's own `updated` list -- the references whose
    modified_count was non-zero -- never the raw request. `/submit-pre-exchange-data`
    matches only rows that are still pending, logs the rest as skipped, and
    returns HTTP 200 either way, so driving off the request would announce
    decisions that never happened.
    """
    try:
        if not updated:
            # The normal shape of a double approval, not an error.
            return

        object_ids = [
            ObjectId(str(entry.get("reference_number")))
            for entry in updated
            if ObjectId.is_valid(str(entry.get("reference_number")))
        ]
        if not object_ids:
            logger.info(
                "No valid reference numbers in the %s decision batch; "
                "skipping the notification",
                market_type,
            )
            return

        docs = await approval_repo.find_by_filters({"_id": {"$in": object_ids}})
        groups = group_single_bid_decisions(updated, docs)

        if not groups:
            logger.info(
                "No decided bids found for the %s batch; skipping the notification",
                market_type,
            )
            return
    except Exception:
        logger.exception(
            "Could not build notification context for the %s single bid decision",
            market_type,
        )
        return

    for status_lower, group in groups.items():
        # Guarded per group: one status failing must not take its sibling with
        # it, so an approve/reject batch never loses half its notifications.
        try:
            await notify(
                "single_bid.status_changed",
                {
                    "market_type": market_type,
                    "market_name": market_display_name(market_type),
                    "approval_market": approval_permission_market(market_type),
                    "submission_path": single_submission_screen_path(market_type),
                    "status_lower": status_lower,
                    "maker_usernames": group["maker_usernames"],
                    "bid_count": group["bid_count"],
                    "schedule_date": group["schedule_date"],
                },
                actor=actor,
            )
        except Exception:
            logger.exception(
                "Notification emit failed for the %s single bid %s group",
                market_type,
                status_lower,
            )
