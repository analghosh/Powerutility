"""
Security headers middleware for EPMS services.
Adds standard HTTP security headers to every response.
"""

from typing import Iterable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Interactive API documentation. Swagger UI and ReDoc are real HTML pages that bootstrap
# themselves with an inline <script> and pull their assets from a CDN, so they need a
# policy that the JSON endpoints emphatically do not.
DEFAULT_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

# Everything that is not a documentation page. These responses are JSON: no scripts, no
# styles, no images, no frames, nothing to fetch. VAPT F17 flagged `unsafe-eval` in the
# previous single policy; the deeper problem was applying a *document* policy to an API in
# the first place. `default-src 'none'` is both stricter and more accurate -- there is no
# legitimate resource for an API response to load.
API_CSP = (
    "default-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)

# Swagger UI / ReDoc. `unsafe-inline` stays: both bootstrap from an inline <script> block,
# and there is no nonce plumbing here to replace it with. `unsafe-eval` is gone -- neither
# needs it, which is verified by loading both pages under this policy with a clean console.
DOCS_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://cdn.jsdelivr.net; "
    "font-src 'self' https://cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)


# Server-rendered HTML pages a service owns itself -- today the scheduler's operator UI
# (its index, login and job dashboard). They are real documents built from inline <script>
# and <style> blocks, so the API policy blanks them: no styling, no behaviour. Nothing they
# load comes from a CDN, so this is `'self'` plus the inline allowance those blocks need.
APP_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Adds security headers to every outbound response.

    The Content-Security-Policy depends on what is being served. Documentation pages get a
    policy permissive enough for Swagger UI; pages named in `html_paths` get a policy that
    lets a server-rendered document run its own inline script and style; every other
    response -- which is to say every API response -- gets `default-src 'none'`.

    Note on scope: this middleware only covers responses from these FastAPI services. The
    single-page app is served by PM2 behind nginx and never passes through here, so the
    CSP protecting the actual browser application has to be set on that vhost. See
    docs/EPMS-VAPT-triage.md (F17).
    """

    def __init__(
        self,
        app,
        docs_paths: Optional[Iterable[str]] = None,
        html_paths: Optional[Iterable[str]] = None,
    ):
        super().__init__(app)
        self._docs_paths = tuple(docs_paths) if docs_paths is not None else DEFAULT_DOCS_PATHS
        self._html_paths = tuple(html_paths) if html_paths is not None else ()

    def _is_docs(self, path: str) -> bool:
        # Suffix match, not equality: the services mount sub-applications under a base
        # path, so the same page is reachable as both "/docs" and "/api/v2/docs".
        return any(path == p or path.endswith(p) for p in self._docs_paths)

    def _is_html_page(self, path: str) -> bool:
        # Suffix match for the same reason as _is_docs: nginx serves these pages under a
        # base path, so "/login" arrives as "/scheduler/login". "/" is the exception and is
        # compared exactly -- as a suffix it normalises to "" and matches every path,
        # which would quietly hand every API response the document policy.
        stripped = path.rstrip("/") or "/"
        for candidate in self._html_paths:
            normalized = candidate.rstrip("/") or "/"
            if normalized == "/":
                if stripped == "/":
                    return True
            elif stripped == normalized or stripped.endswith(normalized):
                return True
        return False

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if self._is_docs(request.url.path):
            policy = DOCS_CSP
        elif self._is_html_page(request.url.path):
            policy = APP_CSP
        else:
            policy = API_CSP
        response.headers["Content-Security-Policy"] = policy
        return response
