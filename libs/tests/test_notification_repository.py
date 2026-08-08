# libs/tests/test_notification_repository.py
from datetime import datetime

import pytest

from libs.notifications.models import Notification
from libs.notifications.repository import TTL_SECONDS, NotificationRepository


class _FakeIndexRecorder:
    def __init__(self):
        self.calls = []

    async def create_index(self, keys, **kwargs):
        self.calls.append((keys, kwargs))


def _repo_with(collection):
    repo = NotificationRepository.__new__(NotificationRepository)
    repo.collection = collection
    return repo


def _notification(user_id="u1"):
    return Notification(
        user_id=user_id,
        event_key="bid.submitted",
        title="t",
        body="b",
        payload={"market_type": "DAM"},
        link="/x",
        actor={"user_id": "u9", "name": "A"},
        severity="action_required",
        created_at=datetime(2026, 8, 8, 12, 0, 0),
    )


@pytest.mark.asyncio
async def test_ensure_indexes_creates_a_90_day_ttl():
    recorder = _FakeIndexRecorder()
    await _repo_with(recorder).ensure_indexes()

    ttl = [c for c in recorder.calls if "expireAfterSeconds" in c[1]]
    assert len(ttl) == 1
    assert ttl[0][1]["expireAfterSeconds"] == TTL_SECONDS
    assert TTL_SECONDS == 7776000


@pytest.mark.asyncio
async def test_ensure_indexes_creates_the_list_and_unread_indexes():
    recorder = _FakeIndexRecorder()
    await _repo_with(recorder).ensure_indexes()

    keys = [c[0] for c in recorder.calls]
    assert [("user_id", 1), ("created_at", -1)] in keys
    assert [("user_id", 1), ("read_at", 1)] in keys


@pytest.mark.asyncio
async def test_insert_many_writes_one_document_per_recipient():
    class _Coll:
        def __init__(self):
            self.documents = None

        async def insert_many(self, docs):
            self.documents = docs

            class _R:
                inserted_ids = ["id1", "id2"]
            return _R()

    coll = _Coll()
    ids = await _repo_with(coll).insert_many_for([_notification("u1"), _notification("u2")])

    assert ids == ["id1", "id2"]
    assert [d["user_id"] for d in coll.documents] == ["u1", "u2"]
    assert coll.documents[0]["read_at"] is None


@pytest.mark.asyncio
async def test_insert_many_with_no_recipients_does_not_touch_mongo():
    class _Exploding:
        async def insert_many(self, docs):
            raise AssertionError("must not be called for an empty audience")

    assert await _repo_with(_Exploding()).insert_many_for([]) == []
