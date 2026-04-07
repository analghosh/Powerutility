"""
V2 specific middleware
"""

from .auth import V2AuthMiddleware

__all__ = ["V2AuthMiddleware"]