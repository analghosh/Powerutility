"""
Every processing route must carry an authorization guard.

A router registered without `dependencies=[guard(...)]` does not fail, does not warn and
does not look wrong -- it simply works, for everybody. That silence is how the service
ended up with 72 unauthorized write routes in the first place, so the absence of a guard
has to be an error someone trips over.

Two checks, deliberately:

* a source check that reads main.py with `ast` -- no imports, no database, so it runs
  anywhere including CI, and it catches the realistic regression (someone adds an
  `include_router` line and forgets the guard);
* a runtime check that walks the assembled app, which also proves the guard survives
  FastAPI's lazy `include_router` and covers routes the source check cannot see.
"""
import ast
import pathlib

import pytest

MAIN = pathlib.Path(__file__).resolve().parents[2] / "services" / "processing" / "app" / "main.py"

# Routers that legitimately carry no JWT user context. Both authenticate by other means:
# power-optimization by HMAC, the notification stream by a short-lived ticket. Adding to
# this set is a security decision and should be argued for in review.
EXEMPT_ROUTERS = {"power_optimization_router"}


def _include_router_calls(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "include_router":
            continue
        if not isinstance(func.value, ast.Name) or func.value.id != "v2_app":
            continue
        arg = node.args[0] if node.args else None
        name = arg.id if isinstance(arg, ast.Name) else "<expression>"
        keywords = {kw.arg for kw in node.keywords}
        yield name, keywords, node.lineno


def test_main_py_parses():
    assert MAIN.exists(), MAIN
    ast.parse(MAIN.read_text())


def test_every_included_router_declares_a_guard():
    tree = ast.parse(MAIN.read_text())
    calls = list(_include_router_calls(tree))
    assert calls, "no v2_app.include_router calls found -- has main.py been restructured?"

    unguarded = [
        (name, lineno)
        for name, keywords, lineno in calls
        if "dependencies" not in keywords and name not in EXEMPT_ROUTERS
    ]

    assert not unguarded, (
        "routers included without dependencies=[guard(...)]: "
        + ", ".join(f"{name} (main.py:{lineno})" for name, lineno in unguarded)
    )


def test_the_public_mirror_routers_are_guarded_too():
    # Each *_public_router re-registers the same handlers under /<market>-bidding/public.
    # Guarding only the private router would make "/public" a one-word authorization
    # bypass, so their absence from this list is the whole point of the test.
    tree = ast.parse(MAIN.read_text())
    guarded = {
        name for name, keywords, _ in _include_router_calls(tree) if "dependencies" in keywords
    }
    for market in ("rtm", "dam", "gdam", "hpdam"):
        name = f"new_{market}_bidding_public_router"
        assert name in guarded, f"{name} is registered without a guard"


def test_exempt_routers_are_still_the_ones_we_agreed():
    tree = ast.parse(MAIN.read_text())
    unguarded = {
        name for name, keywords, _ in _include_router_calls(tree) if "dependencies" not in keywords
    }
    assert unguarded == EXEMPT_ROUTERS, (
        f"the set of unguarded routers changed: {unguarded ^ EXEMPT_ROUTERS}"
    )


# --- runtime check ---------------------------------------------------------


def _load_app():
    try:
        from services.processing.app.main import v2_app
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"processing app not importable in this environment: {exc}")
    return v2_app


def test_no_route_is_left_unguarded_at_runtime():
    from libs.authz.route_guard import unguarded_routes

    missing = unguarded_routes(_load_app())
    assert not missing, "unguarded routes: " + ", ".join(f"{','.join(m)} {p}" for m, p in missing)


def test_every_write_route_is_guarded_at_runtime():
    from libs.authz.route_guard import walk_routes

    writes = [
        (methods, path, guarded)
        for methods, path, guarded in walk_routes(_load_app())
        if set(methods) & {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert writes, "no write routes discovered -- the walker is not seeing the app"
    unguarded = [(m, p) for m, p, g in writes if not g]
    assert not unguarded, f"unguarded write routes: {unguarded}"
