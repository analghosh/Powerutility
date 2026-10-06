"""Single-bid notification emission, shared by all four market controllers.

DAM, GDAM, HPDAM and RTM each have their own thin controller wrapping their own
service, and the emission logic is identical across all four -- only the market
name differs. It lives in libs/notifications/single_bid_emit.py rather than being
copied into each controller for two reasons:

  1. Four copies means four places for the rules below to rot independently.
  2. `new_rtm_bidding_services.py` is FENCED by PTC India ("Don't touch the code
     of new_rtm_bidding_services.py"). Emission therefore has to live outside the
     services entirely, and the controllers are the only shared layer left.

The rules being protected here, each learned on the block-bid path:

  * Emit only for bids the write actually CHANGED. `/submit-pre-exchange-data`
    returns an `updated` list of references whose modified_count was non-zero;
    approving a bid that is no longer pending is a silent no-op that still
    returns HTTP 200, so driving off the request would announce decisions that
    never happened.
  * One notification per STATUS GROUP. One submission can approve some rows and
    reject others.
  * A notification failure must never fail the bid decision that has already
    been persisted.
"""

from datetime import datetime

import pytest

from libs.notifications import single_bid_emit
from libs.notifications.single_bid_emit import (
    notify_single_bid_decisions,
    notify_single_bid_submitted,
    submit_batch_schedule_date,
)


class _NotifyRecorder:
    """Stands in for libs.notifications.emitter.notify."""

    def __init__(self, raises: Exception | None = None):
        self.calls = []
        self.raises = raises

    async def __call__(self, event_key, context, actor):
        self.calls.append({"event_key": event_key, "context": context, "actor": actor})
        if self.raises:
            raise self.raises
        return ["id1"]


class _ApprovalRepo:
    def __init__(self, docs=None, raises: Exception | None = None):
        self.docs = docs or []
        self.raises = raises
        self.filters_seen = []

    async def find_by_filters(self, filters):
        self.filters_seen.append(filters)
        if self.raises:
            raise self.raises
        return self.docs


ACTOR = {"user_id": "u-checker", "username": "approver1"}

# The real payload shape, verified against the client's own builder
# (client/src/features/iDamSingleBid/hooks/useDAMSingleBid.js:2427): the page
# pushes `${payloadDate}_${timeBlock}` where payloadDate is DD-MM-YYYY. There is
# no top-level schedule_date field on this request at all.
SUBMIT_REQUEST = {
    "time_block_range": ["09:00-09:15_37", "09:15-09:30_38"],
    "time_block_dates": ["10-08-2026_37", "10-08-2026_38"],
}

# ObjectId-shaped so the lookup's is_valid() guard lets them through.
REF_A = "507f1f77bcf86cd799439011"
REF_B = "507f1f77bcf86cd799439012"


@pytest.fixture
def notify(monkeypatch):
    recorder = _NotifyRecorder()
    monkeypatch.setattr(single_bid_emit, "notify", recorder)
    return recorder


# ── the schedule date has to be derived, because nothing hands it over ────────


def test_schedule_date_comes_from_time_block_dates_in_day_month_year():
    assert submit_batch_schedule_date(SUBMIT_REQUEST) == "2026-08-10"


def test_schedule_date_is_empty_when_the_request_carries_no_dates():
    """Not an exception: the caller decides what to do with a blank, and it
    chooses to skip the notification rather than announce a bid for no date."""
    assert submit_batch_schedule_date({"time_block_range": ["09:00-09:15_37"]}) == ""
    assert submit_batch_schedule_date({}) == ""


def test_schedule_date_tolerates_an_already_normalised_date():
    """The vendor's own fallback path reads a YYYY-MM-DD segment out of
    time_block_range and then parses it as DD-MM-YYYY, which raises. Accept both
    spellings here rather than inheriting that bug."""
    assert (
        submit_batch_schedule_date({"time_block_dates": ["2026-08-10_37"]})
        == "2026-08-10"
    )


def test_schedule_date_is_empty_for_an_unparseable_value():
    assert submit_batch_schedule_date({"time_block_dates": ["not-a-date_37"]}) == ""


# ── submit for approval ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_notifies_the_approvers_once(notify):
    await notify_single_bid_submitted(
        market_type="DAM", request=SUBMIT_REQUEST, actor=ACTOR
    )

    assert len(notify.calls) == 1
    call = notify.calls[0]
    assert call["event_key"] == "single_bid.submitted_for_approval"
    assert call["context"]["market_type"] == "DAM"
    assert call["context"]["market_name"] == "iDAM"
    assert call["context"]["bid_count"] == 2
    assert call["context"]["schedule_date"] == "2026-08-10"
    assert call["context"]["approval_market"] == "dam"
    assert call["context"]["approval_path"] == "/dam/dam-bid-approval"
    assert call["actor"] == ACTOR


@pytest.mark.asyncio
async def test_submit_renders_through_the_real_registry_entry(notify):
    """Rendering the real event with the real emitted context, so a context key
    the registry needs but the call site stopped sending fails HERE rather than
    silently in production."""
    from libs.notifications.events import REGISTRY

    await notify_single_bid_submitted(
        market_type="RTM", request=SUBMIT_REQUEST, actor=ACTOR
    )

    context = dict(notify.calls[0]["context"])
    context["actor_name"] = ACTOR["username"]  # supplied by the emitter itself

    title, body, link, category, area = REGISTRY[
        "single_bid.submitted_for_approval"
    ].render(context)

    assert title == "Awaiting approval"
    assert body == "approver1 submitted 2 RTM single bid(s) for 2026-08-10."
    assert link == "/rtm/single-bid-approval?date=2026-08-10"
    assert category == "bidding"
    assert area == "RTM Single Bid"


@pytest.mark.asyncio
async def test_submit_with_no_resolvable_date_emits_nothing(notify):
    """A missing notification is recoverable; one that reads "for " is not."""
    await notify_single_bid_submitted(
        market_type="DAM", request={"time_block_range": ["09:00-09:15_37"]}, actor=ACTOR
    )

    assert notify.calls == []


@pytest.mark.asyncio
async def test_submit_with_no_blocks_emits_nothing(notify):
    await notify_single_bid_submitted(
        market_type="DAM",
        request={"time_block_range": [], "time_block_dates": []},
        actor=ACTOR,
    )

    assert notify.calls == []


@pytest.mark.asyncio
async def test_a_notify_failure_never_reaches_the_caller(monkeypatch):
    monkeypatch.setattr(
        single_bid_emit, "notify", _NotifyRecorder(raises=RuntimeError("redis down"))
    )

    # No raise: the bids are already inserted and the request must still succeed.
    await notify_single_bid_submitted(
        market_type="DAM", request=SUBMIT_REQUEST, actor=ACTOR
    )


# ── approve / reject ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_mixed_batch_emits_once_per_status(notify):
    repo = _ApprovalRepo(
        docs=[
            {"_id": REF_A, "created_by": "mukul", "schedule_date": "2026-08-10"},
            {"_id": REF_B, "created_by": "ashutosh", "schedule_date": "2026-08-10"},
        ]
    )

    await notify_single_bid_decisions(
        market_type="DAM",
        updated=[
            {"reference_number": REF_A, "bid_status": "approved"},
            {"reference_number": REF_B, "bid_status": "rejected"},
        ],
        approval_repo=repo,
        actor=ACTOR,
    )

    assert len(notify.calls) == 2
    by_status = {c["context"]["status_lower"]: c["context"] for c in notify.calls}
    assert set(by_status) == {"approved", "rejected"}
    assert by_status["approved"]["maker_usernames"] == ["mukul"]
    assert by_status["approved"]["bid_count"] == 1
    assert by_status["rejected"]["maker_usernames"] == ["ashutosh"]
    assert by_status["approved"]["submission_path"] == "/dam/bid-submission-status"


@pytest.mark.asyncio
async def test_decisions_render_through_the_real_registry_entry(notify):
    from libs.notifications.events import REGISTRY

    repo = _ApprovalRepo(
        docs=[{"_id": REF_A, "created_by": "mukul", "schedule_date": "2026-08-10"}]
    )

    await notify_single_bid_decisions(
        market_type="RTM",
        updated=[{"reference_number": REF_A, "bid_status": "approved"}],
        approval_repo=repo,
        actor=ACTOR,
    )

    context = dict(notify.calls[0]["context"])
    context["actor_name"] = ACTOR["username"]

    title, body, link, category, area = REGISTRY["single_bid.status_changed"].render(
        context
    )

    assert title == "Bid approved"
    assert body == "approver1 approved 1 RTM single bid(s) for 2026-08-10."
    assert link == "/rtm/bid-submission-status?date=2026-08-10"
    assert area == "RTM Single Bid"


@pytest.mark.asyncio
async def test_a_skipped_bid_produces_no_notification(notify):
    """The lapsed-bid case. update-status matches only pending rows, logs
    "skipped", and still returns HTTP 200 -- so an empty `updated` is the normal
    shape of a double approval, not an error."""
    repo = _ApprovalRepo(docs=[])

    await notify_single_bid_decisions(
        market_type="DAM", updated=[], approval_repo=repo, actor=ACTOR
    )

    assert notify.calls == []
    assert repo.filters_seen == []  # not even a lookup


@pytest.mark.asyncio
async def test_a_non_decision_status_is_dropped_not_coerced(notify):
    repo = _ApprovalRepo(
        docs=[{"_id": REF_A, "created_by": "mukul", "schedule_date": "2026-08-10"}]
    )

    await notify_single_bid_decisions(
        market_type="DAM",
        updated=[{"reference_number": REF_A, "bid_status": "pending"}],
        approval_repo=repo,
        actor=ACTOR,
    )

    assert notify.calls == []


@pytest.mark.asyncio
async def test_a_failed_document_lookup_never_reaches_the_caller(notify):
    repo = _ApprovalRepo(raises=RuntimeError("mongo hiccup"))

    await notify_single_bid_decisions(
        market_type="DAM",
        updated=[{"reference_number": REF_A, "bid_status": "approved"}],
        approval_repo=repo,
        actor=ACTOR,
    )

    assert notify.calls == []


@pytest.mark.asyncio
async def test_one_status_group_failing_does_not_stop_the_others(monkeypatch):
    """A market or status whose notify() blows up must not take its siblings
    with it -- the same per-group guarding the block-bid path uses."""

    calls = []

    async def flaky(event_key, context, actor):
        calls.append(context["status_lower"])
        if context["status_lower"] == "approved":
            raise RuntimeError("boom")
        return ["id"]

    monkeypatch.setattr(single_bid_emit, "notify", flaky)

    repo = _ApprovalRepo(
        docs=[
            {"_id": REF_A, "created_by": "mukul", "schedule_date": "2026-08-10"},
            {"_id": REF_B, "created_by": "mukul", "schedule_date": "2026-08-10"},
        ]
    )

    await notify_single_bid_decisions(
        market_type="DAM",
        updated=[
            {"reference_number": REF_A, "bid_status": "approved"},
            {"reference_number": REF_B, "bid_status": "rejected"},
        ],
        approval_repo=repo,
        actor=ACTOR,
    )

    assert sorted(calls) == ["approved", "rejected"]


@pytest.mark.asyncio
async def test_gdam_and_hpdam_notify_the_dam_approvers(notify):
    """Verified in live data: no gdam_* or hpdam_* approval permission exists,
    and both markets are approved on the DAM screen. Templating the permission on
    the market name would build a string no role grants -- notifying nobody, with
    no error raised."""
    for market in ("GDAM", "HPDAM"):
        notify.calls.clear()
        await notify_single_bid_submitted(
            market_type=market, request=SUBMIT_REQUEST, actor=ACTOR
        )

        context = notify.calls[0]["context"]
        assert context["approval_market"] == "dam"
        assert context["approval_path"] == "/dam/dam-bid-approval"
        # ...but the row still says which market it really was.
        assert context["market_name"] == market
