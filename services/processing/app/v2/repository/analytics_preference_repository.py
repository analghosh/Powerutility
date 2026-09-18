"""Per-user dashboard defaults for the analytics screen (`epms.analytics_user_preferences`).

One document per user, keyed on `user_id`, holding what that person wants the filter
panel to open with. It is a convenience store, not a record of anything: losing it
costs a user three clicks, so every read is defensive and a failure never propagates
as an error to the screen.

Two rules the rest of the stack depends on:

* **The row is not authority.** A stored dataset code is replayed into the filter
  panel, never into a query — `/query` re-checks the grant and the registry on every
  request. A preference therefore cannot widen what its owner may see, which is why
  a code the caller is no longer granted can be stored without harm; it is dropped
  on read like any other stale code.

* **A stale code is dropped, not fatal.** The registry is code, not data: a dataset
  that disappears in a release would otherwise make the whole preference unreadable
  for everyone who had ticked it. `sanitize()` drops what the registry no longer
  knows and reports it, so the screen can say so rather than silently opening with a
  shorter selection than the user saved.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ...core.config import settings
from ..analytics import registry
from .base_repository import GenericRepository

logger = logging.getLogger(__name__)

#: Everything a preference document may carry. A key outside this set is refused
#: rather than stored: the document is replayed into the filter panel, and an
#: unvalidated blob replayed into a form is how a preference becomes a payload.
ALLOWED_KEYS = ("datasets", "plants", "format")

#: Export formats a preference may name — the same two the export model accepts.
FORMATS = ("csv", "xlsx")

#: Ceilings, so one caller cannot turn a convenience row into a large document.
MAX_DATASETS = 60
MAX_PLANTS = 200
MAX_ID_LENGTH = 120

_NO_ID = {"_id": 0}


class InvalidPreference(ValueError):
    """The submitted preference is not something the filter panel could replay."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _string_list(value: Any, field: str, limit: int) -> List[str]:
    """A de-duplicated list of non-empty strings, order preserved."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise InvalidPreference(f"{field} must be a list")
    out: List[str] = []
    for item in value:
        text = str(item).strip()
        if not text or text in out:
            continue
        if len(text) > MAX_ID_LENGTH:
            raise InvalidPreference(f"{field} entry is too long: {text[:32]}…")
        out.append(text)
    if len(out) > limit:
        raise InvalidPreference(f"{field} holds more than {limit} entries")
    return out


def validate(preference: Any) -> Dict[str, Any]:
    """Return a stored-ready copy of `preference`, or raise `InvalidPreference`.

    Dataset codes are checked against the registry *here*, on the way in, because at
    write time the caller picked them out of the catalogue this same process serves:
    an unknown code means the client sent something the screen cannot have offered.
    On the way out the rule is the opposite — see `sanitize`.
    """
    if not isinstance(preference, dict):
        raise InvalidPreference("A preference must be an object")

    unknown_keys = [k for k in preference if k not in ALLOWED_KEYS]
    if unknown_keys:
        raise InvalidPreference(
            f"Unsupported preference field(s): {', '.join(sorted(unknown_keys))}. "
            f"Allowed: {', '.join(ALLOWED_KEYS)}")

    datasets = _string_list(preference.get("datasets"), "datasets", MAX_DATASETS)
    unknown = [c for c in datasets if c not in registry.DATASETS]
    if unknown:
        raise InvalidPreference(f"Unknown dataset code(s): {', '.join(unknown)}")

    fmt = preference.get("format")
    if fmt is not None:
        fmt = str(fmt).strip().lower()
        if fmt not in FORMATS:
            raise InvalidPreference(
                f"Unknown format {fmt!r}; expected one of {', '.join(FORMATS)}")

    return {
        "datasets": datasets,
        "plants": _string_list(preference.get("plants"), "plants", MAX_PLANTS),
        "format": fmt or FORMATS[0],
    }


def sanitize(stored: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    """Split a stored row into (what the panel can use, what the registry lost)."""
    stored = stored or {}
    datasets = [str(c) for c in (stored.get("datasets") or [])]
    live = [c for c in datasets if c in registry.DATASETS]
    dropped = [c for c in datasets if c not in registry.DATASETS]
    fmt = str(stored.get("format") or FORMATS[0]).lower()
    return (
        {
            "datasets": live,
            "plants": [str(p) for p in (stored.get("plants") or [])],
            "format": fmt if fmt in FORMATS else FORMATS[0],
            "saved_at": stored.get("saved_at"),
        },
        dropped,
    )


def empty_preference() -> Dict[str, Any]:
    """What a user who has never saved anything gets."""
    return {"datasets": [], "plants": [], "format": FORMATS[0], "saved_at": None}


class AnalyticsPreferenceRepository(GenericRepository):
    """Async repository for one preference document per user."""

    def __init__(self, collection_name: Optional[str] = None):
        super().__init__()
        self.collection_name = (
            collection_name or settings.MONGODB_ANALYTICS_PREFS_COLLECTION)
        self._collection_override = None

    @property
    def collection(self):
        # Settable so unit tests can hand in an AsyncMock, the house convention.
        override = getattr(self, "_collection_override", None)
        if override is not None:
            return override
        return self.db[self.collection_name]

    @collection.setter
    def collection(self, value):
        self._collection_override = value

    async def get(self, user_id: str) -> Optional[Dict[str, Any]]:
        """This user's stored row, or None. A read failure is None, never a raise.

        Deliberately unlike `analytics_repository`, which raises so a broken query
        cannot masquerade as an empty result. Here an empty result and a failed read
        mean the same thing to the caller — open with nothing preselected — and a
        preference store outage must not take the dashboard down with it.
        """
        try:
            return await self.collection.find_one({"user_id": str(user_id)}, _NO_ID)
        except Exception as exc:
            logger.warning("Analytics preference read failed for %s: %s", user_id, exc)
            return None

    async def save(self, user_id: str, preference: Dict[str, Any]) -> Dict[str, Any]:
        """Replace this user's row. Raises `InvalidPreference` before touching Mongo."""
        document = validate(preference)
        document["saved_at"] = _utcnow()
        await self.collection.update_one(
            {"user_id": str(user_id)},
            {"$set": document, "$setOnInsert": {"user_id": str(user_id),
                                                "created_at": document["saved_at"]}},
            upsert=True,
        )
        return document

    async def clear(self, user_id: str) -> bool:
        """Delete this user's row. True when one was there to delete."""
        result = await self.collection.delete_one({"user_id": str(user_id)})
        return bool(getattr(result, "deleted_count", 0))


analytics_preference_repository = AnalyticsPreferenceRepository()
