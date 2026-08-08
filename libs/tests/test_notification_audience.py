import pytest

from libs.notifications.audience import (
    AnyOf,
    AuthDirectory,
    Everyone,
    HasPermission,
    SpecificUsers,
    UsernamesFromContext,
)


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class _FakeCollection:
    def __init__(self, docs):
        self.docs = docs
        self.last_filter = None

    def find(self, flt, projection=None):
        self.last_filter = flt
        return _FakeCursor(self.docs)


@pytest.mark.asyncio
async def test_has_permission_resolves_via_roles_not_the_empty_user_field():
    roles = _FakeCollection([{"name": "admin"}, {"name": "operator"}])
    users = _FakeCollection([{"_id": "u1"}, {"_id": "u2"}])
    directory = AuthDirectory(users_collection=users, roles_collection=roles)

    result = await HasPermission("dam_block_bid_approval:read").resolve(
        {"_directory": directory}
    )

    assert result == ["u1", "u2"]
    assert roles.last_filter == {"permissions": "dam_block_bid_approval:read"}
    assert users.last_filter == {
        "roles": {"$in": ["admin", "operator"]},
        "is_active": True,
        "is_deleted": {"$ne": True},
    }


@pytest.mark.asyncio
async def test_has_permission_templates_from_context():
    roles = _FakeCollection([])
    users = _FakeCollection([])
    directory = AuthDirectory(users_collection=users, roles_collection=roles)

    await HasPermission("{market_lower}_block_bid_approval:read").resolve(
        {"_directory": directory, "market_lower": "rtm"}
    )

    assert roles.last_filter == {"permissions": "rtm_block_bid_approval:read"}


@pytest.mark.asyncio
async def test_no_role_grants_it_means_no_recipients_and_no_user_query():
    roles = _FakeCollection([])
    users = _FakeCollection([{"_id": "should_not_be_reached"}])
    directory = AuthDirectory(users_collection=users, roles_collection=roles)

    assert await HasPermission("nobody:has_this").resolve({"_directory": directory}) == []
    assert users.last_filter is None


@pytest.mark.asyncio
async def test_specific_users_passes_through():
    assert await SpecificUsers(["a", "b"]).resolve({}) == ["a", "b"]


@pytest.mark.asyncio
async def test_everyone_returns_all_active():
    users = _FakeCollection([{"_id": "u1"}, {"_id": "u2"}])
    directory = AuthDirectory(users_collection=users, roles_collection=_FakeCollection([]))

    assert await Everyone().resolve({"_directory": directory}) == ["u1", "u2"]
    assert users.last_filter == {"is_active": True, "is_deleted": {"$ne": True}}


@pytest.mark.asyncio
async def test_any_of_unions_two_rules_preserves_order_and_deduplicates():
    rule1 = SpecificUsers(["u1", "u2"])
    rule2 = SpecificUsers(["u2", "u3"])

    result = await AnyOf(rule1, rule2).resolve({})

    assert result == ["u1", "u2", "u3"]


@pytest.mark.asyncio
async def test_usernames_from_context_returns_empty_when_key_missing():
    users = _FakeCollection([{"_id": "should_not_query"}])
    directory = AuthDirectory(users_collection=users, roles_collection=_FakeCollection([]))

    result = await UsernamesFromContext("maker_usernames").resolve({"_directory": directory})

    assert result == []
    assert users.last_filter is None


@pytest.mark.asyncio
async def test_usernames_from_context_returns_empty_when_key_empty():
    users = _FakeCollection([{"_id": "should_not_query"}])
    directory = AuthDirectory(users_collection=users, roles_collection=_FakeCollection([]))

    result = await UsernamesFromContext("maker_usernames").resolve(
        {"_directory": directory, "maker_usernames": []}
    )

    assert result == []
    assert users.last_filter is None


@pytest.mark.asyncio
async def test_user_ids_for_usernames_filters_on_active_and_deleted():
    users = _FakeCollection([{"_id": "u1"}, {"_id": "u2"}])
    directory = AuthDirectory(users_collection=users, roles_collection=_FakeCollection([]))

    result = await directory.user_ids_for_usernames(["admin", "mukul"])

    assert result == ["u1", "u2"]
    assert users.last_filter == {
        "username": {"$in": ["admin", "mukul"]},
        "is_active": True,
        "is_deleted": {"$ne": True},
    }


@pytest.mark.asyncio
async def test_user_ids_for_usernames_returns_empty_for_empty_list():
    users = _FakeCollection([{"_id": "should_not_query"}])
    directory = AuthDirectory(users_collection=users, roles_collection=_FakeCollection([]))

    result = await directory.user_ids_for_usernames([])

    assert result == []
    assert users.last_filter is None
