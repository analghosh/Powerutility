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
        category="bidding",
        area="iDAM Block Bid",
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
async def test_ensure_indexes_creates_the_category_index():
    recorder = _FakeIndexRecorder()
    await _repo_with(recorder).ensure_indexes()

    keys = [c[0] for c in recorder.calls]
    assert [("user_id", 1), ("category", 1), ("created_at", -1)] in keys


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


@pytest.mark.asyncio
async def test_mark_read_with_malformed_id_returns_false():
    class _Coll:
        def __init__(self):
            self.update_called = False

        async def update_one(self, filter, update):
            self.update_called = True
            raise AssertionError("update_one must not be called for invalid id")

    coll = _Coll()
    result = await _repo_with(coll).mark_read("u1", "not-an-object-id")

    assert result is False
    assert coll.update_called is False


@pytest.mark.asyncio
async def test_list_for_user_clamps_limit_and_skip():
    class _Coll:
        def __init__(self):
            self.skip_value = None
            self.limit_value = None

        def find(self, filter):
            self.filter_value = filter
            return self

        def sort(self, field, direction):
            return self

        def skip(self, value):
            self.skip_value = value
            return self

        def limit(self, value):
            self.limit_value = value
            return self

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    coll = _Coll()
    repo = _repo_with(coll)

    # Test limit clamping (99999 should clamp to 200)
    await repo.list_for_user("u1", limit=99999, skip=-5)

    assert coll.limit_value == 200, f"Expected limit to be 200, got {coll.limit_value}"
    assert coll.skip_value == 0, f"Expected skip to be 0, got {coll.skip_value}"
    assert coll.filter_value == {"user_id": "u1"}


class _FakeCursor:
    """Applies sort/skip/limit to an in-memory doc list, mirroring the subset
    of the Motor cursor API list_for_user relies on."""

    def __init__(self, docs, recorder):
        self._docs = list(docs)
        self._recorder = recorder
        self._index = 0

    def sort(self, field, direction):
        self._docs.sort(key=lambda d: d[field], reverse=(direction == -1))
        return self

    def skip(self, value):
        self._recorder.last_skip = value
        self._docs = self._docs[value:]
        return self

    def limit(self, value):
        self._recorder.last_limit = value
        self._docs = self._docs[:value]
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._docs):
            raise StopAsyncIteration
        doc = self._docs[self._index]
        self._index += 1
        return doc


class _FakeQueryableCollection:
    """Fake collection whose find() actually honours a flat equality query
    dict against a fixed doc set, unlike _Coll above (which only records
    what it was called with). Needed to prove the category predicate
    changes which rows come back, not just what gets passed to find()."""

    def __init__(self, docs):
        self._docs = docs
        self.last_query = None
        self.last_skip = None
        self.last_limit = None

    def find(self, query):
        self.last_query = dict(query)
        matched = [
            d for d in self._docs
            if all(d.get(k) == v for k, v in query.items())
        ]
        return _FakeCursor(matched, self)


def _doc(user_id="u1", category="bidding", created_at=None):
    return {
        "_id": "abc",
        "user_id": user_id,
        "category": category,
        "read_at": None,
        "created_at": created_at or datetime(2026, 8, 8, 12, 0, 0),
    }


@pytest.mark.asyncio
async def test_list_for_user_filters_by_category():
    docs = [
        _doc(category="bidding"),
        _doc(category="scheduling"),
        _doc(category="bidding"),
    ]
    coll = _FakeQueryableCollection(docs)
    repo = _repo_with(coll)

    items = await repo.list_for_user("u1", category="bidding")

    assert coll.last_query == {"user_id": "u1", "category": "bidding"}
    assert len(items) == 2
    assert all(item["category"] == "bidding" for item in items)


@pytest.mark.asyncio
async def test_list_for_user_without_category_returns_everything():
    docs = [
        _doc(category="bidding"),
        _doc(category="scheduling"),
    ]
    coll = _FakeQueryableCollection(docs)
    repo = _repo_with(coll)

    items = await repo.list_for_user("u1")

    assert coll.last_query == {"user_id": "u1"}
    assert len(items) == 2


@pytest.mark.asyncio
async def test_list_for_user_category_filter_respects_max_limit():
    docs = [_doc(category="bidding") for _ in range(3)]
    coll = _FakeQueryableCollection(docs)
    repo = _repo_with(coll)

    await repo.list_for_user("u1", limit=99999, skip=-5, category="bidding")

    assert coll.last_limit == 200
    assert coll.last_skip == 0
    assert coll.last_query == {"user_id": "u1", "category": "bidding"}
