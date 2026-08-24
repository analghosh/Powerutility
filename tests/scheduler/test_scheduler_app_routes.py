"""
Two defects on the scheduler's own app-level routes.

/health returned `scheduler_running` and `database` and omitted `version` and
`timestamp`, which its response model requires. FastAPI validates the response against
the model, so every call to it returned HTTP 500 -- on every deployment, including
production, since before the VAPT work. Any monitor watching it has been red or ignored.

The three diagnostic routes are declared with @app.get, so the `dependencies=_admin_only`
attached at include_router time never applied to them. Authentication alone left the job
inventory, a stack trace and the cache metrics open to any of the ~790 accounts.

Read from source: importing the scheduler app opens Mongo, Redis and RabbitMQ.
"""

import ast
from pathlib import Path

import pytest

SCHEDULER_MAIN = Path(__file__).resolve().parents[2] / "services" / "scheduler" / "app" / "main.py"
TREE = ast.parse(SCHEDULER_MAIN.read_text(encoding="utf-8"))


def _class_fields(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {t.target.id for t in node.body if isinstance(t, ast.AnnAssign)}
    raise AssertionError(f"class {name} not found")


def _returned_keys(function_name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name:
            for statement in ast.walk(node):
                if isinstance(statement, ast.Return) and isinstance(statement.value, ast.Dict):
                    return {k.value for k in statement.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError(f"no dict return found in {function_name}")


def _route_decorator(path):
    """Return the @app.get(...) call for a path declared on the app itself."""
    for node in ast.walk(TREE):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            first = decorator.args[0]
            if isinstance(first, ast.Constant) and first.value == path:
                return decorator
    raise AssertionError(f"no app-level route declared for {path}")


def _has_admin_dependency(decorator):
    for keyword in decorator.keywords:
        if keyword.arg == "dependencies" and isinstance(keyword.value, ast.Name):
            return keyword.value.id == "_admin_only"
    return False


class TestHealthMatchesItsResponseModel:
    def test_every_required_field_is_returned(self):
        missing = _class_fields("HealthResponse") - _returned_keys("health_check")
        assert not missing, f"response model requires fields the handler never returns: {missing}"

    def test_nothing_returned_is_undeclared(self):
        """An undeclared key is silently dropped from the response rather than served."""
        extra = _returned_keys("health_check") - _class_fields("HealthResponse")
        assert not extra, f"handler returns fields the response model drops: {extra}"


class TestDiagnosticRoutesRequireAdmin:
    @pytest.mark.parametrize("path", ["/scheduler/diagnostics", "/cache/health", "/cache/stats"])
    def test_app_level_diagnostic_routes_carry_the_admin_guard(self, path):
        assert _has_admin_dependency(_route_decorator(path)), (
            f"{path} is declared with @app.get, so include_router dependencies do not "
            f"reach it -- it needs dependencies=_admin_only of its own"
        )
