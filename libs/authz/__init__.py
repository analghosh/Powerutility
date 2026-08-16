"""Route-level authorization for EPMS services.

Deliberately a separate package from `libs.core.authz`: importing that one executes
`guard.py`, which opens a synchronous Redis connection at import time.
"""
