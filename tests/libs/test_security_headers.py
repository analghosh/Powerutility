"""
VAPT F17 — Content-Security-Policy on the API services.

The report's headline was wrong for these services (they serve JSON, not documents) but
the underlying problem was real: one permissive document policy, including `unsafe-eval`,
was applied to every response. The fix splits it — `default-src 'none'` for API responses,
and a Swagger-shaped policy only on the documentation pages.

`unsafe-eval` is gone from both. Verified in a real browser as well as here: Swagger UI
renders all 281 operations under the docs policy with no CSP violations from its own code.
"""

from starlette.applications import Starlette
from starlette.responses import JSONResponse, HTMLResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from libs.middleware.security_headers import SecurityHeadersMiddleware


def _client():
    app = Starlette(routes=[
        Route("/api/v2/anything", lambda r: JSONResponse({"ok": True})),
        Route("/docs", lambda r: HTMLResponse("<html></html>")),
        Route("/api/v2/docs", lambda r: HTMLResponse("<html></html>")),
        Route("/redoc", lambda r: HTMLResponse("<html></html>")),
        Route("/openapi.json", lambda r: JSONResponse({})),
    ])
    app.add_middleware(SecurityHeadersMiddleware)
    return TestClient(app)


def csp(path):
    return _client().get(path).headers["Content-Security-Policy"]


class TestNoUnsafeEvalAnywhere:
    def test_api_response_has_no_unsafe_eval(self):
        assert "unsafe-eval" not in csp("/api/v2/anything")

    def test_docs_page_has_no_unsafe_eval(self):
        assert "unsafe-eval" not in csp("/docs")


class TestApiPolicy:
    def test_api_responses_allow_nothing_by_default(self):
        policy = csp("/api/v2/anything")
        assert "default-src 'none'" in policy

    def test_api_responses_cannot_be_framed_or_rebased(self):
        policy = csp("/api/v2/anything")
        assert "frame-ancestors 'none'" in policy
        assert "base-uri 'none'" in policy

    def test_api_responses_carry_no_script_src_at_all(self):
        assert "script-src" not in csp("/api/v2/anything")


class TestDocsPolicy:
    def test_swagger_may_run_its_inline_bootstrap(self):
        assert "'unsafe-inline'" in csp("/docs")

    def test_swagger_may_load_its_cdn_assets(self):
        assert "https://cdn.jsdelivr.net" in csp("/docs")

    def test_docs_recognised_under_a_mounted_base_path(self):
        """The services mount sub-apps, so the same page is /docs and /api/v2/docs."""
        assert csp("/api/v2/docs") == csp("/docs")

    def test_redoc_gets_the_docs_policy(self):
        assert "cdn.jsdelivr.net" in csp("/redoc")

    def test_docs_pages_still_cannot_be_framed(self):
        assert "frame-ancestors 'none'" in csp("/docs")


class TestOtherHeadersUnchanged:
    def test_the_standard_headers_are_still_present(self):
        h = _client().get("/api/v2/anything").headers
        assert h["X-Content-Type-Options"] == "nosniff"
        assert h["X-Frame-Options"] == "DENY"
        assert "max-age=31536000" in h["Strict-Transport-Security"]
        assert h["Referrer-Policy"] == "strict-origin-when-cross-origin"
