# libs/tests/test_block_bid_notification_emit.py
import inspect

import pytest

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
