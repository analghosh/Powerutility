"""
Auth adds the System Health menu row on startup when it is missing, because a
deployment's database may be out of anyone's reach. Run against an in-memory
stand-in for the three collections it touches.
"""

import asyncio
from types import SimpleNamespace

from bson import ObjectId

from services.auth.app.core.system_health_menu import MENU_PATH, ensure_system_health_menu


def _matches(doc, query):
    return all(doc.get(key) == value for key, value in query.items())


class FakeCollection:
    def __init__(self, docs=()):
        self.docs = [dict(doc) for doc in docs]

    async def find_one(self, query, projection=None):
        return next((doc for doc in self.docs if _matches(doc, query)), None)

    def find(self, query, projection=None):
        docs = [doc for doc in self.docs if _matches(doc, query)]

        async def iterate():
            for doc in docs:
                yield doc
        return iterate()

    async def update_one(self, query, update, upsert=False):
        if any(_matches(doc, query) for doc in self.docs):
            return SimpleNamespace(upserted_id=None)
        doc = {**update["$setOnInsert"], "_id": ObjectId()}
        self.docs.append(doc)
        return SimpleNamespace(upserted_id=doc["_id"])


def _db(roles=("admin", "SuperAdmin", "user", "operator"), with_admin_menu=True, extra_menus=()):
    admin = {"_id": ObjectId(), "name": "Administration", "path": "/admin", "parent_id": None, "order": 8}
    menus = [admin] if with_admin_menu else []
    if with_admin_menu:
        menus.append({"_id": ObjectId(), "name": "Users", "path": "/admin/users", "parent_id": str(admin["_id"]), "order": 4})
    menus.extend(extra_menus)
    return {
        "menus": FakeCollection(menus),
        "roles": FakeCollection({"_id": ObjectId(), "name": name} for name in roles),
        "menu_roles": FakeCollection(),
    }


def _grants(db):
    names = {str(role["_id"]): role["name"] for role in db["roles"].docs}
    return sorted(names[grant["role_id"]] for grant in db["menu_roles"].docs)


def test_a_missing_row_is_created_under_administration_and_given_to_admin_roles():
    db = _db()
    outcome = asyncio.run(ensure_system_health_menu(db))
    row = next(menu for menu in db["menus"].docs if menu["path"] == MENU_PATH)
    admin = next(menu for menu in db["menus"].docs if menu["path"] == "/admin")
    assert outcome == {"created": True, "granted": ["admin", "SuperAdmin"], "reason": None}
    assert row["name"] == "System Health"
    assert row["parent_id"] == str(admin["_id"])
    assert row["order"] == 5
    assert row["is_active"] is True and row["is_deleted"] is False
    assert _grants(db) == ["SuperAdmin", "admin"]
    assert all(grant["permissions"] == ["read"] for grant in db["menu_roles"].docs)


def test_a_second_start_changes_nothing():
    db = _db()
    asyncio.run(ensure_system_health_menu(db))
    outcome = asyncio.run(ensure_system_health_menu(db))
    assert outcome == {"created": False, "granted": [], "reason": "already present"}
    assert sum(menu["path"] == MENU_PATH for menu in db["menus"].docs) == 1
    assert len(db["menu_roles"].docs) == 2


def test_an_existing_row_is_left_exactly_as_it_is():
    deleted = {"_id": ObjectId(), "name": "System Health", "path": MENU_PATH, "is_deleted": True}
    db = _db(extra_menus=[deleted])
    outcome = asyncio.run(ensure_system_health_menu(db))
    assert outcome["created"] is False
    assert db["menu_roles"].docs == []
    assert next(m for m in db["menus"].docs if m["path"] == MENU_PATH)["is_deleted"] is True


def test_nothing_happens_without_an_administration_menu():
    db = _db(with_admin_menu=False)
    outcome = asyncio.run(ensure_system_health_menu(db))
    assert outcome == {"created": False, "granted": [], "reason": "no Administration menu"}
    assert db["menus"].docs == [] and db["menu_roles"].docs == []


def test_a_deployment_without_admin_roles_gets_the_row_but_no_grants():
    db = _db(roles=("user", "viewer"))
    outcome = asyncio.run(ensure_system_health_menu(db))
    assert outcome == {"created": True, "granted": [], "reason": None}
    assert db["menu_roles"].docs == []


def test_when_another_process_creates_the_row_first_this_one_grants_nothing():
    db = _db()
    real_update = db["menus"].update_one

    async def lose_the_race(query, update, upsert=False):
        await real_update(query, update, upsert)  # the other process's insert
        return SimpleNamespace(upserted_id=None)

    db["menus"].update_one = lose_the_race
    outcome = asyncio.run(ensure_system_health_menu(db))
    assert outcome == {"created": False, "granted": [], "reason": "created by another process"}
    assert db["menu_roles"].docs == []
