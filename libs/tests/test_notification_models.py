import pytest
from libs.notifications.models import EventType, Severity


class _FixedAudience:
    async def resolve(self, context):
        return ["u1"]


def _event():
    return EventType(
        key="bid.submitted",
        severity=Severity.ACTION_REQUIRED,
        title="{market_type} bid awaiting approval",
        body="{actor_name} submitted {bid_count} {market_type} bid(s) for {schedule_date}.",
        audience=_FixedAudience(),
        link="/dam/dam-block-bid-approve?date={schedule_date}",
    )


def test_render_substitutes_every_placeholder():
    title, body, link = _event().render({
        "market_type": "DAM",
        "actor_name": "A. Kumar",
        "bid_count": 3,
        "schedule_date": "2026-08-09",
    })
    assert title == "DAM bid awaiting approval"
    assert body == "A. Kumar submitted 3 DAM bid(s) for 2026-08-09."
    assert link == "/dam/dam-block-bid-approve?date=2026-08-09"


def test_missing_placeholder_raises_rather_than_emitting_broken_text():
    with pytest.raises(KeyError):
        _event().render({"market_type": "DAM"})


def test_severity_values_are_the_stored_strings():
    assert Severity.ACTION_REQUIRED.value == "action_required"
    assert Severity.INFO.value == "info"
    assert Severity.ERROR.value == "error"
