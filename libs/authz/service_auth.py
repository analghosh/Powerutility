"""
Service-to-service credentials for internal EPMS calls.

Some calls between services carry no end user: the processing service asks the scheduler
for its job list, triggers a job, kicks off RTM scheduling. Those calls previously went
out with no credentials at all, which is why the scheduler shipped with its authentication
middleware commented out -- switching it on would have broken them.

They now present a shared secret in this header instead. It is the same idea as the HMAC
already used for the power-optimization endpoint, kept deliberately simple: one secret,
one header, checked with a constant-time comparison in `AuthMiddleware`.

Not a replacement for user authentication. A service credential identifies a *system*, so
it grants system-level access and is never used to stand in for a person -- calls that act
on behalf of a user still carry that user's token.
"""
from __future__ import annotations

from typing import Dict, Optional

# Kept here so the middleware that checks it and the clients that send it cannot drift.
INTERNAL_TOKEN_HEADER = "X-Internal-Token"


def internal_headers(token: Optional[str], extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Build the headers for an internal call.

    An unset token yields no header rather than an empty one, so a service with no secret
    configured fails loudly with a 401 instead of quietly sending something that can never
    match.
    """
    headers: Dict[str, str] = dict(extra or {})
    if token:
        headers[INTERNAL_TOKEN_HEADER] = token
    return headers
