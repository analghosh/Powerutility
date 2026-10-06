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
    # `category` is the /notifications tab this row files under; `area` is the
    # module label the row displays. Both are templates resolved from the event
    # context, exactly like title/body/link, so one registry entry serves every
    # market. They are rendered here rather than derived on the client from
    # `event_key` because a client-side label map is a second copy of this
    # registry -- it drifts the first time an event is added backend-only, and
    # the symptom is a blank column rather than an error.
    category: str
    area: str

    def render(self, context: Dict[str, Any]) -> tuple[str, str, str, str, str]:
        """Render title, body, link, category and area.

        Raises KeyError on a missing placeholder. Failing loudly at emit time is
        preferable to storing half-rendered text in a maker-checker trail.
        """
        return (
            self.title.format(**context),
            self.body.format(**context),
            self.link.format(**context),
            self.category.format(**context),
            self.area.format(**context),
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
    category: str
    area: str
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
            "category": self.category,
            "area": self.area,
            "read_at": self.read_at,
            "created_at": self.created_at,
        }
