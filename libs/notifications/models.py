"""Core types for the notification system.

Kept free of I/O so the registry and rendering can be tested without Mongo,
Redis, or an event loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(str, Enum):
    INFO = "info"
    ACTION_REQUIRED = "action_required"
    ERROR = "error"


class Audience(ABC):
    """A rule describing who should receive an event."""

    @abstractmethod
    async def resolve(self, context: Dict[str, Any]) -> List[str]:
        """Return recipient user_ids. The actor is subtracted by the emitter."""


@dataclass(frozen=True)
class EventType:
    key: str
    severity: Severity
    title: str
    body: str
    audience: Audience
    link: str

    def render(self, context: Dict[str, Any]) -> tuple[str, str, str]:
        """Render title, body and link.

        Raises KeyError on a missing placeholder. Failing loudly at emit time is
        preferable to storing half-rendered text in a maker-checker trail.
        """
        return (
            self.title.format(**context),
            self.body.format(**context),
            self.link.format(**context),
        )


@dataclass
class Notification:
    user_id: str
    event_key: str
    title: str
    body: str
    payload: Dict[str, Any]
    link: str
    actor: Dict[str, Any]
    severity: str
    created_at: datetime
    read_at: Optional[datetime] = None
    id: Optional[str] = None

    def to_document(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "event_key": self.event_key,
            "title": self.title,
            "body": self.body,
            "payload": self.payload,
            "link": self.link,
            "actor": self.actor,
            "severity": self.severity,
            "read_at": self.read_at,
            "created_at": self.created_at,
        }
