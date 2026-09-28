"""
Short, safe failure reasons for run logs and the System Health page.

Provider errors often quote the URL they failed on, and some provider URLs
carry their API key in the query string (Grid India's does). Anything that
stores or shows an error message outside the service log goes through
``safe_reason`` first, so a key never reaches ``audit_db`` or a browser.
"""

import re
from typing import Any, Optional

REASON_LIMIT = 200

_SECRET_PARAM = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|token|secret|password|passwd|pwd|auth)=([^&\s'\"]+)"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")


def redact_secrets(text: str) -> str:
    """Mask credentials in query strings and Authorization-style values."""
    text = _SECRET_PARAM.sub(lambda m: f"{m.group(1)}=***", text)
    return _BEARER.sub(lambda m: f"{m.group(1)} ***", text)


def safe_reason(error: Any, limit: int = REASON_LIMIT) -> Optional[str]:
    """
    One line, secrets masked, at most ``limit`` characters. Exceptions whose
    message is empty (``asyncio.TimeoutError()``) are named by their type,
    because an empty reason is the one thing an operator can't act on.
    """
    if error is None:
        return None
    if isinstance(error, BaseException):
        text = str(error).strip() or type(error).__name__
    else:
        text = str(error).strip()
    if not text:
        return None
    text = redact_secrets(" ".join(text.split()))
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text
