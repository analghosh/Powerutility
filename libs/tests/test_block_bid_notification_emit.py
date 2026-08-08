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


# ── Fix round 1: the maker lookup must not be able to fail an already-decided bid ──


class _FakeBlockBidRepo:
    """Minimal stand-in for BlockBidCurrentDataRepository.

    Only implements what update_block_bid_status calls: updatedata_if_pending (always
    matches, so every bid passed in gets updated) and get_creators (optionally raises,
    to simulate a Mongo hiccup during the maker lookup).
    """

    def __init__(self, get_creators_error=None, get_creators_result=None):
        self.get_creators_error = get_creators_error
        self.get_creators_result = get_creators_result or ["maker_one"]
        self.get_creators_called = False

    async def updatedata_if_pending(self, record_id, data):
        return True

    async def get_creators(self, record_ids):
        self.get_creators_called = True
        if self.get_creators_error is not None:
            raise self.get_creators_error
        return self.get_creators_result


class _NothingPendingRepo(_FakeBlockBidRepo):
    async def updatedata_if_pending(self, record_id, data):
        return False


def _make_service(fake_repo):
    # __new__ bypasses BlockBidServices.__init__ (which builds several other
    # repositories update_block_bid_status never touches) and lets us inject only
    # the fake block_bid_repo the method under test actually calls.
    service = block_bid_services.BlockBidServices.__new__(block_bid_services.BlockBidServices)
    service.block_bid_repo = fake_repo
    return service


_APPROVE_ONE_BID_REQUEST = {
    "status": ["Approved"],
    "reason": ["ok"],
    "rej_reason": [""],
    "other_remark": [""],
    "other_remark_rej": [""],
    "id": ["507f1f77bcf86cd799439011"],
    "current_user": "approver1",
}


@pytest.mark.asyncio
async def test_a_failing_maker_lookup_does_not_fail_the_status_change():
    """Regression test for the Fix-round-1 defect: get_creators used to run outside any
    try/except, inside update_block_bid_status's outer `except Exception: ... raise`.
    A Mongo hiccup during the maker lookup therefore propagated out of the method
    *after* the bid had already been updated in Mongo -- reporting failure to the
    caller for an approval that had, in fact, already succeeded."""
    fake_repo = _FakeBlockBidRepo(get_creators_error=RuntimeError("mongo hiccup"))
    service = _make_service(fake_repo)

    result = await service.update_block_bid_status(_APPROVE_ONE_BID_REQUEST)

    assert result["summary"]["updated_count"] == 1
    assert result["updated"][0]["id"] == "507f1f77bcf86cd799439011"
    assert "successfully" in result["message"].lower()


@pytest.mark.asyncio
async def test_get_creators_is_not_called_when_nothing_was_updated():
    """A maker lookup for zero updated bids is a pointless query today, and would
    become a real (unbounded) one if the id list were ever large."""
    fake_repo = _NothingPendingRepo()
    service = _make_service(fake_repo)

    result = await service.update_block_bid_status(_APPROVE_ONE_BID_REQUEST)

    assert result["summary"]["updated_count"] == 0
    assert fake_repo.get_creators_called is False


def test_get_creators_call_is_guarded_so_a_lookup_failure_cannot_fail_the_bid():
    """Structural guard mirroring test_every_notify_call_is_wrapped_so_it_cannot_fail_a_bid:
    catches a future edit that moves the maker lookup back outside a try/except."""
    source = inspect.getsource(block_bid_services)
    lines = source.splitlines()
    found_call = False
    for index, line in enumerate(lines):
        if "get_creators(" in line and "def get_creators" not in line:
            found_call = True
            preceding = lines[max(0, index - 6):index]
            assert any("try:" in p for p in preceding), (
                f"unguarded get_creators() call at source line {index + 1}"
            )
    assert found_call, "expected a get_creators( call site in block_bid_services"
