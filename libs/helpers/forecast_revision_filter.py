"""
Filter forecast_serve rows to the latest ingest revision per source batch.

After partial API/CSV updates, blocks not touched in the latest batch keep an older
forecast_revision. Consumers (merge, read API, switch validation) must only use rows
at the current max revision for that delivery date + source.
"""

from typing import Any, Dict, List, Optional


def max_forecast_revision(docs: List[Dict[str, Any]]) -> Optional[int]:
    """Highest forecast_revision in a doc list, or None if no revision is set."""
    max_rev: Optional[int] = None
    for doc in docs:
        rev = doc.get("forecast_revision")
        if rev is None:
            continue
        try:
            rev_i = int(rev)
        except (TypeError, ValueError):
            continue
        if max_rev is None or rev_i > max_rev:
            max_rev = rev_i
    return max_rev


def filter_forecast_docs_latest_revision(
    docs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Keep only documents at the latest forecast_revision.

    If no document has forecast_revision (legacy data), returns the input unchanged.
    """
    if not docs:
        return []
    latest = max_forecast_revision(docs)
    if latest is None:
        return docs
    return [
        doc
        for doc in docs
        if doc.get("forecast_revision") is not None
        and int(doc.get("forecast_revision")) == latest
    ]
