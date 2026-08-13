# libs/tests/test_block_bid_notification_emit.py
import inspect

import pytest

from libs.notifications.events import REGISTRY
from services.processing.app.v2.services import block_bid_services


def test_the_dead_placeholders_are_gone():
    source = inspect.getsource(block_bid_services)
    assert "notification_data = {" not in source
    assert "bid_for_Approvel" not in source


def test_notify_is_called_from_the_service():
    assert "notify(" in inspect.getsource(block_bid_services)


def test_every_notify_call_is_wrapped_so_it_cannot_fail_a_bid():
    """A bid submission must never fail because Redis hiccuped."""
    source = inspect.getsource(block_bid_services)
    for index, line in enumerate(source.splitlines()):
        if "await notify(" in line:
            preceding = source.splitlines()[max(0, index - 6):index]
            assert any("try:" in p for p in preceding), (
                f"unguarded notify() at source line {index + 1}"
            )


def test_the_hardcoded_rtm_title_is_not_reintroduced():
    """block_bid_services.py:128 said 'RTM Block Bid Submitted for Approval'
    on a path that also serves DAM, GDAM and HPDAM."""
    source = inspect.getsource(block_bid_services)
    assert "RTM Block Bid Submitted for Approval" not in source


# ── Fix rounds 1 & 2: the market lookup must not be able to fail an already-decided
# bid, and must never fall back to a default market. ──────────────────────────────


class _DecisionContextRepo:
    """Stand-in for BlockBidCurrentDataRepository.

    Only implements what update_block_bid_status calls: updatedata_if_pending (always
    matches, so every bid passed in gets updated) and get_bid_decision_context, which
    returns the caller-supplied `groups` (or raises `error`, to simulate a Mongo
    hiccup during the market/maker/date lookup) regardless of the record_ids it is
    given -- tests control exactly what the lookup "finds".
    """

    def __init__(self, groups=None, error=None):
        self.groups = groups if groups is not None else [
            {
                "market_type": "RTM",
                "bid_count": 1,
                "maker_usernames": ["maker_one"],
                "biding_date": "2026-08-09",
            }
        ]
        self.error = error
        self.get_bid_decision_context_called = False
        self.last_record_ids = None

    async def updatedata_if_pending(self, record_id, data):
        return True

    async def get_bid_decision_context(self, record_ids):
        self.get_bid_decision_context_called = True
        self.last_record_ids = list(record_ids)
        if self.error is not None:
            raise self.error
        return self.groups


class _NothingPendingRepo(_DecisionContextRepo):
    async def updatedata_if_pending(self, record_id, data):
        return False


class _NotifyRecorder:
    """Replaces block_bid_services.notify so tests can inspect exactly what context
    and how many separate calls a status change produced, without touching Redis or
    Mongo (the real notify() is exercised by libs/notifications' own tests)."""

    def __init__(self):
        self.calls = []

    async def __call__(self, event_key, context, actor):
        self.calls.append({"event_key": event_key, "context": context, "actor": actor})
        return []


def _make_service(fake_repo):
    # __new__ bypasses BlockBidServices.__init__ (which builds several other
    # repositories update_block_bid_status never touches) and lets us inject only
    # the fake block_bid_repo the method under test actually calls.
    service = block_bid_services.BlockBidServices.__new__(block_bid_services.BlockBidServices)
    service.block_bid_repo = fake_repo
    return service


def _approve_request(ids):
    n = len(ids)
    return {
        "status": ["Approved"] * n,
        "reason": ["ok"] * n,
        "rej_reason": [""] * n,
        "other_remark": [""] * n,
        "other_remark_rej": [""] * n,
        "id": ids,
        "current_user": "approver1",
    }


# Intentionally has no "market_type" or "schedule_date" key -- matches production:
# UpdateStatusRequest (block_bid_routes.py:100-106) has no such fields, and the shared
# RTM/DAM approval screen (BlockBidApprovePage.jsx:620-634) never sends them. Keeping
# them absent here is what makes these tests prove the market/date come from the bids.
_APPROVE_ONE_BID_REQUEST = _approve_request(["507f1f77bcf86cd799439011"])


@pytest.mark.asyncio
async def test_a_failing_bid_decision_context_lookup_does_not_fail_the_status_change():
    """Regression test for the Fix-round-1 defect: the market/maker/date lookup used to
    run outside any try/except, inside update_block_bid_status's outer
    `except Exception: ... raise`. A Mongo hiccup there therefore propagated out of the
    method *after* the bid had already been updated in Mongo -- reporting failure to
    the caller for an approval that had, in fact, already succeeded."""
    fake_repo = _DecisionContextRepo(error=RuntimeError("mongo hiccup"))
    service = _make_service(fake_repo)

    result = await service.update_block_bid_status(_APPROVE_ONE_BID_REQUEST)

    assert result["summary"]["updated_count"] == 1
    assert result["updated"][0]["id"] == "507f1f77bcf86cd799439011"
    assert "successfully" in result["message"].lower()


@pytest.mark.asyncio
async def test_get_bid_decision_context_is_not_called_when_nothing_was_updated():
    """A market/maker/date lookup for zero updated bids is a pointless query today,
    and would become a real (unbounded) one if the id list were ever large."""
    fake_repo = _NothingPendingRepo()
    service = _make_service(fake_repo)

    result = await service.update_block_bid_status(_APPROVE_ONE_BID_REQUEST)

    assert result["summary"]["updated_count"] == 0
    assert fake_repo.get_bid_decision_context_called is False


def test_get_bid_decision_context_call_is_guarded_so_a_lookup_failure_cannot_fail_the_bid():
    """Structural guard mirroring test_every_notify_call_is_wrapped_so_it_cannot_fail_a_bid:
    catches a future edit that moves the market/maker/date lookup back outside a
    try/except."""
    source = inspect.getsource(block_bid_services)
    lines = source.splitlines()
    found_call = False
    for index, line in enumerate(lines):
        if "get_bid_decision_context(" in line and "def get_bid_decision_context" not in line:
            found_call = True
            preceding = lines[max(0, index - 6):index]
            assert any("try:" in p for p in preceding), (
                f"unguarded get_bid_decision_context() call at source line {index + 1}"
            )
    assert found_call, "expected a get_bid_decision_context( call site in block_bid_services"


# ── Fix round 2: market and schedule_date must come from the bids, never the request ──


@pytest.mark.asyncio
@pytest.mark.parametrize("market", ["DAM", "GDAM", "HPDAM"])
async def test_status_change_market_comes_from_the_bids_not_the_request(monkeypatch, market):
    """Regression test for the Fix-round-2 defect: UpdateStatusRequest never carries
    market_type (confirmed against block_bid_routes.py and the shared RTM/DAM approval
    screen), so `request.get("market_type") or "RTM"` mislabelled every DAM/GDAM/HPDAM
    decision as RTM -- notifying the wrong approvers (rtm_block_bid_approval:read
    instead of dam_block_bid_approval:read) with a broken submission link. This must
    fail against the current code, which always produces "RTM"/"rtm"."""
    recorder = _NotifyRecorder()
    monkeypatch.setattr(block_bid_services, "notify", recorder)

    fake_repo = _DecisionContextRepo(groups=[
        {
            "market_type": market,
            "bid_count": 1,
            "maker_usernames": ["mukul"],
            "biding_date": "2026-08-09",
        }
    ])
    service = _make_service(fake_repo)

    await service.update_block_bid_status(_APPROVE_ONE_BID_REQUEST)

    assert len(recorder.calls) == 1
    context = recorder.calls[0]["context"]
    assert context["market_type"] == market
    assert context["approval_market"] == "dam"


@pytest.mark.asyncio
async def test_schedule_date_comes_from_the_bids_not_the_request(monkeypatch):
    """_APPROVE_ONE_BID_REQUEST has neither "schedule_date" nor "biding_date" -- matching
    production, where UpdateStatusRequest carries neither. The old
    `request.get("schedule_date") or request.get("biding_date") or ""` therefore always
    rendered an empty date; it must now come from the bid's own biding_date."""
    recorder = _NotifyRecorder()
    monkeypatch.setattr(block_bid_services, "notify", recorder)

    fake_repo = _DecisionContextRepo(groups=[
        {
            "market_type": "RTM",
            "bid_count": 1,
            "maker_usernames": ["mukul"],
            "biding_date": "2026-08-09",
        }
    ])
    service = _make_service(fake_repo)

    await service.update_block_bid_status(_APPROVE_ONE_BID_REQUEST)

    assert len(recorder.calls) == 1
    assert recorder.calls[0]["context"]["schedule_date"] == "2026-08-09"


@pytest.mark.asyncio
async def test_a_batch_spanning_two_markets_emits_two_notifications_with_split_counts(monkeypatch):
    """The API accepts arbitrary ids even though the UI only ever submits one market at
    a time. A mixed batch must emit one notification per market, each carrying only
    that market's count -- never a single notification labelling the whole batch with
    one market's name (the exact bug this task exists to remove)."""
    recorder = _NotifyRecorder()
    monkeypatch.setattr(block_bid_services, "notify", recorder)

    fake_repo = _DecisionContextRepo(groups=[
        {
            "market_type": "RTM",
            "bid_count": 2,
            "maker_usernames": ["mukul"],
            "biding_date": "2026-08-09",
        },
        {
            "market_type": "DAM",
            "bid_count": 1,
            "maker_usernames": ["admin"],
            "biding_date": "2026-08-09",
        },
    ])
    service = _make_service(fake_repo)

    ids = ["id-1", "id-2", "id-3"]
    result = await service.update_block_bid_status(_approve_request(ids))

    assert result["summary"]["updated_count"] == 3
    assert len(recorder.calls) == 2
    counts_by_market = {
        call["context"]["market_type"]: call["context"]["bid_count"] for call in recorder.calls
    }
    assert counts_by_market == {"RTM": 2, "DAM": 1}


@pytest.mark.asyncio
async def test_no_market_found_skips_the_notification_without_raising(monkeypatch):
    """If the lookup yields no market at all, no notification is sent rather than
    guessing a default -- a missing notification is recoverable, a wrong one sent to
    the wrong approvers is not. The bid decision itself must still be reported as
    successful: the notification path can never fail an already-completed decision."""
    recorder = _NotifyRecorder()
    monkeypatch.setattr(block_bid_services, "notify", recorder)

    fake_repo = _DecisionContextRepo(groups=[])  # lookup found no market at all
    service = _make_service(fake_repo)

    result = await service.update_block_bid_status(_APPROVE_ONE_BID_REQUEST)

    assert recorder.calls == []
    assert result["summary"]["updated_count"] == 1
    assert "successfully" in result["message"].lower()


# ── Final review, finding 4: join the template to the context that feeds it ───


@pytest.mark.asyncio
async def test_status_changed_renders_from_the_exact_context_the_service_builds(monkeypatch):
    """Nothing else joins REGISTRY["block_bid.status_changed"] to the context that
    actually reaches it: test_notification_events.py renders only
    block_bid.submitted_for_approval, and every test above monkeypatches notify and
    inspects the context without ever rendering with it.

    So if status_lower, submission_path, approval_market, bid_count or schedule_date
    drifts out of _notify_status_change's context, EventType.render raises KeyError,
    the per-market `except Exception` in block_bid_services swallows it, and every
    approval and rejection notification disappears with the whole suite green. This
    renders the real registry entry with the real emitted context, so that drift
    fails here instead of silently in production.
    """
    recorder = _NotifyRecorder()
    monkeypatch.setattr(block_bid_services, "notify", recorder)

    fake_repo = _DecisionContextRepo(groups=[
        {
            "market_type": "GDAM",
            "bid_count": 4,
            "maker_usernames": ["mukul"],
            "biding_date": "2026-08-09",
        }
    ])
    service = _make_service(fake_repo)

    await service.update_block_bid_status(_APPROVE_ONE_BID_REQUEST)

    assert len(recorder.calls) == 1
    context = dict(recorder.calls[0]["context"])
    # actor_name is the one placeholder the emitter itself supplies
    # (NotificationEmitter.notify) rather than the call site; everything else on
    # the template has to come from the context asserted above.
    context["actor_name"] = recorder.calls[0]["actor"]["username"]

    title, body, link, category, area = REGISTRY["block_bid.status_changed"].render(
        context
    )

    assert title == "Bid approved"
    assert body == "approver1 approved 4 GDAM block bid(s) for 2026-08-09."
    assert link == "/dam/dam-block-bid-submission?date=2026-08-09"
    # GDAM approves on the DAM screen but is still its own market to the reader,
    # so the row says GDAM even though the link goes to /dam/.
    assert category == "bidding"
    assert area == "GDAM Block Bid"


# ── Final review, finding 6: a mixed batch must not claim one status for all ──


class _StatusAwareDecisionRepo(_DecisionContextRepo):
    """get_bid_decision_context answers for the ids it is actually given.

    _DecisionContextRepo returns the same groups for any id list, which cannot show
    whether the caller split the batch. This one groups the ids it receives, exactly
    as the real Mongo aggregation over those ids would -- so a caller that queries
    once per status gets that status's own counts, and a caller that queries once for
    the whole batch gets a single mixed group.
    """

    def __init__(self, bid_facts):
        super().__init__()
        self.bid_facts = bid_facts
        self.id_lists = []

    async def get_bid_decision_context(self, record_ids):
        self.get_bid_decision_context_called = True
        self.id_lists.append(list(record_ids))
        grouped = {}
        for record_id in record_ids:
            facts = self.bid_facts[record_id]
            group = grouped.setdefault(
                facts["market_type"],
                {
                    "market_type": facts["market_type"],
                    "bid_count": 0,
                    "maker_usernames": facts["maker_usernames"],
                    "biding_date": facts["biding_date"],
                },
            )
            group["bid_count"] += 1
        return list(grouped.values())


def _decide_request(ids, statuses):
    n = len(ids)
    return {
        "status": list(statuses),
        "reason": ["ok"] * n,
        "rej_reason": ["price too high"] * n,
        "other_remark": [""] * n,
        "other_remark_rej": [""] * n,
        "id": list(ids),
        "current_user": "approver1",
    }


@pytest.mark.asyncio
async def test_a_mixed_approve_reject_batch_tells_each_group_its_own_status(monkeypatch):
    """status used to be `updated[0]["status"]` -- the first decided bid's status
    applied to every notification in the batch. UpdateStatusRequest.status is a
    List[Optional[str]], so a batch that approves some bids and rejects others told
    the makers and every checker that all N bids were whatever the first one happened
    to be, including in a maker's notification about their own bid."""
    recorder = _NotifyRecorder()
    monkeypatch.setattr(block_bid_services, "notify", recorder)

    facts = {
        "id-1": {"market_type": "RTM", "maker_usernames": ["mukul"], "biding_date": "2026-08-09"},
        "id-2": {"market_type": "RTM", "maker_usernames": ["mukul"], "biding_date": "2026-08-09"},
        "id-3": {"market_type": "RTM", "maker_usernames": ["mukul"], "biding_date": "2026-08-09"},
    }
    service = _make_service(_StatusAwareDecisionRepo(facts))

    await service.update_block_bid_status(
        _decide_request(["id-1", "id-2", "id-3"], ["Approved", "Rejected", "Rejected"])
    )

    assert len(recorder.calls) == 2
    counts_by_status = {
        call["context"]["status_lower"]: call["context"]["bid_count"]
        for call in recorder.calls
    }
    assert counts_by_status == {"approved": 1, "rejected": 2}


@pytest.mark.asyncio
async def test_a_mixed_batch_spanning_two_markets_splits_on_both_market_and_status(monkeypatch):
    """Grouping is on the (market, status) pair, not either one alone: four
    notifications for two markets each carrying an approval and a rejection."""
    recorder = _NotifyRecorder()
    monkeypatch.setattr(block_bid_services, "notify", recorder)

    facts = {
        "rtm-ok": {"market_type": "RTM", "maker_usernames": ["mukul"], "biding_date": "2026-08-09"},
        "rtm-no": {"market_type": "RTM", "maker_usernames": ["mukul"], "biding_date": "2026-08-09"},
        "dam-ok": {"market_type": "DAM", "maker_usernames": ["admin"], "biding_date": "2026-08-09"},
        "dam-no": {"market_type": "DAM", "maker_usernames": ["admin"], "biding_date": "2026-08-09"},
    }
    service = _make_service(_StatusAwareDecisionRepo(facts))

    await service.update_block_bid_status(
        _decide_request(
            ["rtm-ok", "dam-ok", "rtm-no", "dam-no"],
            ["Approved", "Approved", "Rejected", "Rejected"],
        )
    )

    pairs = {
        (call["context"]["market_type"], call["context"]["status_lower"]): call["context"]["bid_count"]
        for call in recorder.calls
    }
    assert pairs == {
        ("RTM", "approved"): 1,
        ("DAM", "approved"): 1,
        ("RTM", "rejected"): 1,
        ("DAM", "rejected"): 1,
    }


# ── Final review, finding 5: the submit path must group by market too ─────────


class _SubmitRepo:
    """Stand-in for BlockBidCurrentDataRepository covering only the two methods
    insert_block_bid calls."""

    def __init__(self):
        self.inserted = None

    async def get_max_revisions(self, formatted_date, market_type, sessions):
        return {}

    async def insert_many(self, data):
        self.inserted = list(data)
        return True


def _submit_request(markets):
    """A submission whose parallel arrays span the given markets, one bid each.

    market_type really is a List[str] parallel to the bid arrays
    (SubmitForApprovalRequest), which is exactly why reading element [0] for the
    whole batch was wrong.
    """
    n = len(markets)
    return {
        "dates": "09-08-2026",
        "market_type": list(markets),
        "from": ["00:00"] * n,
        "to": ["00:15"] * n,
        "trade_session": [0] * n,
        "quantum": [10] * n,
        "bid_price": [3000.0] * n,
        "number_of_bids": [1] * n,
        "type": "Buy",
        "exchange": ["IEX"] * n,
        "selected_for": ["IEX"] * n,
        "iex_price": [3000] * n,
        "pxil_price": [0] * n,
        "current_user": "maker1",
    }


@pytest.mark.asyncio
async def test_a_multi_market_submission_emits_one_notification_per_market(monkeypatch):
    """The submit path used to do `str(request.get("market_type", ["RTM"])[0]).upper()`
    -- the first element labelling the whole batch -- so a multi-market submission
    mislabelled every bid with one market's name and notified only that market's
    approvers. It must group and emit like _notify_status_change does."""
    recorder = _NotifyRecorder()
    monkeypatch.setattr(block_bid_services, "notify", recorder)
    service = _make_service(_SubmitRepo())

    await service.insert_block_bid(_submit_request(["RTM", "DAM", "RTM"]))

    assert len(recorder.calls) == 2
    assert {call["event_key"] for call in recorder.calls} == {
        "block_bid.submitted_for_approval"
    }
    counts_by_market = {
        call["context"]["market_type"]: call["context"]["bid_count"]
        for call in recorder.calls
    }
    assert counts_by_market == {"RTM": 2, "DAM": 1}
    # Each notification must reach its OWN market's approvers, not one market's.
    approval_by_market = {
        call["context"]["market_type"]: call["context"]["approval_market"]
        for call in recorder.calls
    }
    assert approval_by_market == {"RTM": "rtm", "DAM": "dam"}


@pytest.mark.asyncio
async def test_a_submission_with_no_market_emits_nothing_instead_of_defaulting_to_rtm(monkeypatch):
    """The removed `["RTM"]` default meant a submission carrying no market at all
    still notified RTM's approvers about bids that were not theirs."""
    recorder = _NotifyRecorder()
    monkeypatch.setattr(block_bid_services, "notify", recorder)
    service = _make_service(_SubmitRepo())

    result = await service.insert_block_bid(_submit_request([""]))

    assert recorder.calls == []
    # The bid itself still went in: the notification path can never fail a bid.
    assert "successfully" in result["message"].lower()


@pytest.mark.asyncio
async def test_rows_left_at_pending_are_not_announced_as_a_decision(monkeypatch):
    """The approval screen submits EVERY row it is showing, not just the ones the
    checker touched: untouched rows arrive with status "Pending"
    (BlockBidApprovePage.jsx sends `bid.status || "Pending"` for each row). Those
    are not decisions -- nobody approved or rejected anything -- so approving one
    bid out of three produced a truthful-looking but nonsense second notification,
    "RTM block bid pending / notifytest pending 2 RTM block bid(s)", alongside the
    real one. Observed in the browser 2026-08-09.

    This is a regression from removing the vendor's
    `status_label = "Approved" if statuses[i] == "Approved" else "Rejected"`,
    which coerced every status into a decision. That coercion was wrong for mixed
    batches (it relabelled Pending as Rejected), so the fix is to DROP
    non-decisions, not to restore the coercion.
    """
    recorder = _NotifyRecorder()
    monkeypatch.setattr(block_bid_services, "notify", recorder)

    facts = {
        "id-1": {"market_type": "RTM", "maker_usernames": ["mukul"], "biding_date": "2026-08-09"},
        "id-2": {"market_type": "RTM", "maker_usernames": ["mukul"], "biding_date": "2026-08-09"},
        "id-3": {"market_type": "RTM", "maker_usernames": ["mukul"], "biding_date": "2026-08-09"},
    }
    service = _make_service(_StatusAwareDecisionRepo(facts))

    await service.update_block_bid_status(
        _decide_request(["id-1", "id-2", "id-3"], ["Approved", "Pending", "Pending"])
    )

    assert len(recorder.calls) == 1, (
        "only the approval is a decision; the two untouched rows must stay silent"
    )
    assert recorder.calls[0]["context"]["status_lower"] == "approved"
    assert recorder.calls[0]["context"]["bid_count"] == 1


@pytest.mark.asyncio
async def test_a_batch_of_only_untouched_rows_notifies_nobody(monkeypatch):
    """Pressing Confirm without choosing anything must not notify 116 people."""
    recorder = _NotifyRecorder()
    monkeypatch.setattr(block_bid_services, "notify", recorder)

    facts = {
        "id-1": {"market_type": "DAM", "maker_usernames": ["admin"], "biding_date": "2026-08-09"},
        "id-2": {"market_type": "DAM", "maker_usernames": ["admin"], "biding_date": "2026-08-09"},
    }
    service = _make_service(_StatusAwareDecisionRepo(facts))

    await service.update_block_bid_status(
        _decide_request(["id-1", "id-2"], ["Pending", "pending"])
    )

    assert recorder.calls == []
