"""
tools/resync_role_permissions.py rebuilds roles.permissions from page grants.

It replaces a hand-authored matrix that classified every menu as masters / admin /
ingestion / approval / operational. Under PTC's page model that classification is dead --
holding a page is the whole decision -- so these tests pin the new rule and carry over
the two checks from the old suite that are still load-bearing: that the resource names
match what the guards ask for, and that the tool never prints the Mongo password.
"""
import importlib.util
import pathlib

_TOOL = pathlib.Path(__file__).resolve().parents[2] / "tools" / "resync_role_permissions.py"
_spec = importlib.util.spec_from_file_location("resync_role_permissions", _TOOL)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


# build_plan does ObjectId(role_id), so the fake role id must be valid 24-hex.
ROLE_ID = "694e8ac4e5342637ee485d8e"


class FakeDB:
    def __init__(self, roles, menus, menu_roles):
        self._c = {"roles": roles, "menus": menus, "menu_roles": menu_roles}

    def __getitem__(self, name):
        class Coll:
            def __init__(self, docs):
                self._docs = docs

            def find(self, query=None, *_args, **_kwargs):
                # Honours flat equality filters, so build_plan's {"is_active": True}
                # is actually exercised rather than silently ignored.
                docs = self._docs
                for key, want in (query or {}).items():
                    docs = [d for d in docs if d.get(key) == want]
                return iter(docs)

        return Coll(self._c[name])


def _db(read_only=False, menu_names=("DAM Single Bid",), role=None):
    menus = [{"_id": f"menu{i}", "name": n} for i, n in enumerate(menu_names)]
    role = role or {"_id": ROLE_ID, "name": "trial", "permissions": [],
                    "is_read_only": read_only}
    return FakeDB(
        roles=[role],
        menus=menus,
        menu_roles=[{"_id": f"grant{i}", "role_id": ROLE_ID, "menu_id": m["_id"],
                     "permissions": ["read"], "is_active": True}
                    for i, m in enumerate(menus)],
    )


def _perms(role_updates, name="trial"):
    """role_updates is a list of (ObjectId, name, permissions, old_count)."""
    return next(u[2] for u in role_updates if u[1] == name)


def test_build_plan_keeps_its_four_tuple_contract():
    # main(), apply_plan() and backup() all unpack this. Changing the shape breaks them
    # silently, because main() unpacks positionally.
    result = tool.build_plan(_db())
    assert len(result) == 4
    grant_updates, role_updates, roles, menus = result
    assert isinstance(grant_updates, list)
    assert isinstance(role_updates, list)
    assert isinstance(roles, dict) and isinstance(menus, dict)


def test_an_ordinary_role_gets_read_write_delete_on_every_page_it_holds():
    _g, role_updates, _r, _m = tool.build_plan(_db(read_only=False))
    assert _perms(role_updates) == [
        "dam_single_bid:read",
        "dam_single_bid:write",
        "dam_single_bid:delete",
    ]


def test_a_read_only_role_gets_read_alone():
    _g, role_updates, _r, _m = tool.build_plan(_db(read_only=True))
    assert _perms(role_updates) == ["dam_single_bid:read"]


def test_a_role_document_without_the_flag_is_treated_as_not_read_only():
    db = _db(menu_names=("Dashboard",),
             role={"_id": ROLE_ID, "name": "legacy", "permissions": []})  # no is_read_only
    _g, role_updates, _r, _m = tool.build_plan(db)
    assert "dashboard:write" in _perms(role_updates, "legacy")


def test_no_role_is_granted_approve():
    _g, role_updates, _r, _m = tool.build_plan(
        _db(read_only=False, menu_names=("DAM Block Bid", "RTM Single Bid"))
    )
    assert not any(p.endswith(":approve") for p in _perms(role_updates))


def test_menus_sharing_a_resource_name_collapse():
    # Setup and Administration both resolve to `admin`, an administrative resource, so
    # the collapsed result is read alone.
    _g, role_updates, _r, _m = tool.build_plan(
        _db(read_only=False, menu_names=("Setup", "Administration"))
    )
    assert _perms(role_updates) == ["admin:read"]


def test_menus_sharing_an_operational_resource_name_collapse():
    _g, role_updates, _r, _m = tool.build_plan(
        _db(read_only=False, menu_names=("DAM Single Bid", "DAM Single Bid"))
    )
    assert _perms(role_updates) == [
        "dam_single_bid:read", "dam_single_bid:write", "dam_single_bid:delete",
    ]


def test_grant_rows_carrying_stale_verbs_are_queued_for_normalisation():
    db = FakeDB(
        roles=[{"_id": ROLE_ID, "name": "trial", "permissions": []}],
        menus=[{"_id": "menu0", "name": "Dashboard"}],
        menu_roles=[{"_id": "grant0", "role_id": ROLE_ID, "menu_id": "menu0",
                     "permissions": ["read", "write", "delete", "approve"],
                     "is_active": True}],
    )
    grant_updates, _ru, _r, _m = tool.build_plan(db)
    assert grant_updates == [("grant0", ["read"])]


def test_a_grant_row_already_at_read_is_not_queued():
    grant_updates, _ru, _r, _m = tool.build_plan(_db())
    assert grant_updates == []


def test_revoked_grants_do_not_become_permissions():
    """
    A revoked (is_active: false) grant must grant nothing. The matrix this replaced read
    every row regardless and handed them `read`; under the page model the same bug would
    hand out `write`. In the live database the `user` role carries 20 revoked grants and
    740 live accounts.
    """
    db = FakeDB(
        roles=[{"_id": ROLE_ID, "name": "trial", "permissions": []}],
        menus=[{"_id": "menu0", "name": "Dashboard"},
               {"_id": "menu1", "name": "DAM Single Bid"}],
        menu_roles=[
            {"_id": "grant0", "role_id": ROLE_ID, "menu_id": "menu0",
             "permissions": ["read"], "is_active": True},
            {"_id": "grant1", "role_id": ROLE_ID, "menu_id": "menu1",
             "permissions": ["read"], "is_active": False},   # revoked
        ],
    )
    _g, role_updates, _r, _m = tool.build_plan(db)
    perms = _perms(role_updates)
    assert "dashboard:write" in perms
    assert not any(p.startswith("dam_single_bid") for p in perms)


def test_a_role_whose_grants_are_all_revoked_is_reset_not_skipped():
    """
    A role with no active grants must be visited and zeroed, not passed over. Iterating
    only the roles that produced resources silently leaves stale over-privilege in place --
    the same failure the is_active filter closes, one level up.
    """
    db = FakeDB(
        roles=[{"_id": ROLE_ID, "name": "revoked",
                "permissions": ["dam_block_bid:read", "dam_block_bid:write"]}],
        menus=[{"_id": "menu0", "name": "DAM Block Bid"}],
        menu_roles=[{"_id": "grant0", "role_id": ROLE_ID, "menu_id": "menu0",
                     "permissions": ["read"], "is_active": False}],
    )
    _g, role_updates, _r, _m = tool.build_plan(db)
    assert _perms(role_updates, "revoked") == []


def test_the_dead_matrix_is_gone():
    # If any of these survive a rename, two sources of truth are deciding permissions.
    for name in ("classify", "permissions_for", "MASTERS_MENUS", "ADMIN_MENUS",
                 "INGESTION_MENUS", "INGESTION_WRITE_ROLES", "READ_ONLY_ROLES"):
        assert not hasattr(tool, name), f"{name} should have been deleted"


def test_unmapped_menu_names_fall_back_to_slugging():
    assert tool.resource_name("Bid Market", tool.MENU_TO_RESOURCE) == "bid_market"
    assert tool.resource_name("SCADA - Data Input", tool.MENU_TO_RESOURCE) == "scada___data_input"


def test_guard_resources_in_main_are_all_grantable():
    """
    Every resource a guard asks for must be produced by some menu name, or that screen
    403s for everyone below admin. `weather` is the one exception: its router is guarded
    as guard(("weather", "forecast")), so `forecast` satisfies it.
    """
    import re
    main_py = pathlib.Path(__file__).resolve().parents[2] / "services" / "processing" / "app" / "main.py"
    src = main_py.read_text()
    asked = set()
    for m in re.finditer(r'guard\(\s*(\("[^)]*?"\)|"[^"]+")', src, re.S):
        asked.update(re.findall(r'"([^"]+)"', m.group(1)))

    grantable = set(tool.MENU_TO_RESOURCE.values())
    grantable.update({
        tool.resource_name(n, tool.MENU_TO_RESOURCE)
        for n in ("Plant Details", "Bid Market", "IEX API Detail", "Parameter",
                  "Power Exchange", "Price Groups", "Audit Log", "DC SG Template",
                  "OCF Limit Values", "DAM Trade Status", "RTM Trade Status",
                  "Upload CSV", "SCADA Data", "SCADA - Data Input",
                  # The parent DAM and RTM menus. Their names are not keys in
                  # MENU_TO_RESOURCE, so resource_name slugs them to "dam" and "rtm" --
                  # which is exactly what guard(("dam", "dam_single_bid")) at main.py:528
                  # and guard(("rtm", "rtm_single_bid")) at main.py:546 ask for.
                  "DAM", "RTM")
    })
    missing = asked - grantable - {"weather"}
    assert not missing, f"guard resources no menu can grant: {sorted(missing)}"


def test_the_announced_target_never_prints_the_mongo_password():
    assert "s3cr3t" not in tool._safe_url("mongodb://user:s3cr3t@host:27017/db")


def test_a_url_without_credentials_is_left_alone():
    assert tool._safe_url("mongodb://localhost:27017") == "mongodb://localhost:27017"
