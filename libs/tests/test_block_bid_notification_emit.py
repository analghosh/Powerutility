# libs/tests/test_block_bid_notification_emit.py
import inspect

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
