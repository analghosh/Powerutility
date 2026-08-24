"""
The scheduler's operator UI, and the one exclusion that must never be added.

Authentication was restored on this service, which was correct -- every job-control route
was anonymous. The pages the operator uses to reach those routes are a different matter: a
browser navigation carries no Authorization header, so requiring one on "/login" locks out
the page whose only purpose is to obtain the token.

The trap this file pins is "/" itself. AuthMiddleware._should_skip_auth strips trailing
slashes and then calls startswith(), so an exclude entry of "/" normalises to "" and every
path on the service matches it -- authentication is off, silently, everywhere.
"""

import ast
from pathlib import Path

from libs.middleware.auth_middleware import AuthMiddleware

SCHEDULER_MAIN = Path(__file__).resolve().parents[2] / "services" / "scheduler" / "app" / "main.py"


def _scheduler_exclude_paths():
    """Read the list out of the source: importing main.py needs Mongo, Redis and RabbitMQ."""
    tree = ast.parse(SCHEDULER_MAIN.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        args = [a for a in node.args if isinstance(a, ast.Name)]
        if not any(a.id == "AuthMiddleware" for a in args):
            continue
        for keyword in node.keywords:
            if keyword.arg == "exclude_paths":
                return [e.value for e in keyword.value.elts if isinstance(e, ast.Constant)]
    raise AssertionError("no AuthMiddleware registration with exclude_paths found")


def _skips(exclude_paths, path):
    middleware = AuthMiddleware.__new__(AuthMiddleware)
    middleware.exclude_paths = exclude_paths
    return middleware._should_skip_auth(path)


class TestRootIsNeverExcluded:
    def test_excluding_root_would_disable_authentication_everywhere(self):
        assert _skips(["/"], "/api/v1/scheduler/jobs/dam-sync/disable") is True

    def test_the_scheduler_does_not_exclude_root(self):
        assert "/" not in _scheduler_exclude_paths()


class TestOperatorUiIsReachable:
    def test_the_login_page_and_what_it_reads_are_excluded(self):
        excluded = _scheduler_exclude_paths()
        assert "/login" in excluded
        assert "/config" in excluded

    def test_the_dashboard_shell_is_excluded(self):
        """The page is a shell -- its data arrives over XHR with a Bearer token."""
        assert "/dashboard" in _scheduler_exclude_paths()


class TestJobControlStaysGuarded:
    def test_the_ui_exclusions_do_not_reach_the_job_routes(self):
        excluded = _scheduler_exclude_paths()
        for path in (
            "/api/v1/scheduler/jobs/dam-sync/disable",
            "/api/v1/scheduler/rtm-scheduling/trigger",
            "/jobs/audit-logs/search",
            "/queue/status",
        ):
            assert _skips(excluded, path) is False, path
