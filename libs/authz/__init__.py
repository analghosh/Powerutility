"""Route-level authorization for EPMS services.

Deliberately a separate package from `libs.core.authz`: importing that one executes
`guard.py`, which opens a synchronous Redis connection at import time.
"""

from .page_model import (
    PAGE_VERBS,
    READ_ONLY_VERBS,
    permission_strings,
    verbs_for_role,
)

__all__ = [
    "PAGE_VERBS",
    "READ_ONLY_VERBS",
    "permission_strings",
    "verbs_for_role",
]
