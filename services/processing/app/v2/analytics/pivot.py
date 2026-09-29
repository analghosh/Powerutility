"""Canonical rows in, the §7 response contract out.

Every adapter returns the same flat `CanonicalRow`, so the pivot is generic: it never
knows which collection a value came from, only where it belongs on the two axes.

Two properties of this module carry the whole design:

1. **`values` is a numeric matrix indexed `[block][date]`, not a list of row
   objects.** 144,000 values encodes to roughly 1.2MB this way and 5-10MB as
   `{date, block, value}` objects. The Display Options orientation toggles are
   applied client-side by transposing this one shape, so there is no second endpoint
   and no second encoding.

2. **An absent cell is `None`.** Never `0`. The docx is explicit, and the two are
   genuinely different facts: "nothing was scheduled for this block" versus "zero was
   scheduled". A real `0` returned by Mongo therefore survives as `0`, and a `None`
   returned by Mongo stays `None` — the adapters already convert `$sum`'s empty-group
   `0` back to `None` before rows get here.

A dataset whose query *failed* is not the same as one with no data, so it is marked
`quality: "failed"` and listed in `meta.failed_datasets`. The spec is blunt about
this: do not show a successful dashboard when a dataset silently failed. We have form
— `submit-for-approval` already returns HTTP 200 on failure and the frontend never
checks, which is how bids go missing.
"""
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from . import registry

#: Human names for entity values that are stored as codes. Plants and exchanges
#: are already names; forecast types are not.
ENTITY_LABELS = {
    "per_forecast_type": {"DAF": "Day Ahead", "IDF": "Intra Day"},
    "per_dsm_field": registry.DSM_PUBLISHED_LABELS,
}

#: Series kinds whose entities are a fixed, known set. Every one of them becomes a
#: column in this order even when the range holds no value for it, so the file's
#: columns do not depend on what happened to be uploaded. Any other kind sorts by
#: entity name and shows only what came back.
ENTITY_ORDER = {
    "per_dsm_field": registry.DSM_PUBLISHED_FIELDS,
}


def entity_label(dataset, entity):
    """The column name a person should read for this series' entity."""
    if entity is None:
        return None
    return ENTITY_LABELS.get(getattr(dataset, "series", None), {}).get(str(entity), str(entity))


QUALITY_COMPLETE = "complete"
QUALITY_PARTIAL = "partial"
QUALITY_EMPTY = "empty"
QUALITY_FAILED = "failed"

_RANGE_KEY = re.compile(r"^(\d+)-(\d+)$")

# Sort tiers, so a mixed axis is still deterministic: plain block numbers, then
# "lo-hi" custom buckets ordered by their lower bound, then named bands by label.
_TIER_NUMERIC = 0
_TIER_RANGE = 1
_TIER_LABEL = 2


def block_sort_key(key: Any) -> Tuple[int, int, str]:
    """Order block buckets numerically, not lexically — "10" must follow "2"."""
    k = str(key)
    if k.isdigit():
        return (_TIER_NUMERIC, int(k), "")
    m = _RANGE_KEY.match(k)
    if m:
        return (_TIER_RANGE, int(m.group(1)), k)
    return (_TIER_LABEL, 0, k)


def block_axis_value(key: Any) -> Union[int, str]:
    """Individual blocks render as integers; band and custom buckets as their label."""
    k = str(key)
    return int(k) if k.isdigit() else k


def _dedupe(values: Iterable[Any]) -> List[str]:
    seen: Dict[str, None] = {}
    for v in values:
        seen.setdefault(str(v), None)
    return list(seen)


def axes_from(rows: Sequence[dict],
              axes_hint: Optional[dict] = None) -> Tuple[List[str], List[str]]:
    """The two axes as canonical string keys.

    The hint (the date buckets and block buckets the filters asked for) is unioned
    with whatever the rows actually carry rather than intersected: a hint must be
    able to *add* an empty column, never to drop a value the database returned.
    """
    hint = axes_hint or {}
    dates = _dedupe(hint.get("dates") or [])
    blocks = _dedupe(hint.get("blocks") or [])
    dates += [str(r["date_key"]) for r in rows]
    blocks += [str(r["block_key"]) for r in rows]
    return sorted(_dedupe(dates)), sorted(_dedupe(blocks), key=block_sort_key)


def _empty_matrix(n_blocks: int, n_dates: int) -> List[List[Optional[float]]]:
    return [[None] * n_dates for _ in range(n_blocks)]


def _quality(code: str, matrix: List[List[Optional[float]]],
             failed: Sequence[str]) -> str:
    if code in failed:
        return QUALITY_FAILED
    filled = sum(1 for row in matrix for v in row if v is not None)
    if not filled:
        return QUALITY_EMPTY
    total = sum(len(row) for row in matrix)
    return QUALITY_COMPLETE if filled == total else QUALITY_PARTIAL


def build_response(rows: Sequence[dict], *, requested: Sequence[str],
                   axes_hint: Optional[dict] = None,
                   meta: Optional[dict] = None,
                   entity_order: Optional[Dict[str, Sequence[str]]] = None) -> dict:
    """Lay canonical rows out on the shared axes and complete the response envelope.

    `requested` is the user's ticked dataset list, in their order — it drives the
    series order and guarantees a series exists for every dataset they asked for,
    even one that returned nothing, so the UI can say so explicitly instead of
    quietly rendering a shorter list than the user selected.

    `entity_order` narrows a fixed-entity series kind (see ENTITY_ORDER) to the
    entities this request chose — the DSM figures a user ticked, for one.
    """
    fixed_order = {**ENTITY_ORDER, **(entity_order or {})}
    codes = _dedupe(requested or [])
    entries = [(code, registry.get(code)) for code in codes]  # raises on unknown
    wanted = {code for code, _ in entries}

    meta_in = dict(meta or {})
    failed = list(meta_in.get("failed_datasets") or [])

    kept = [r for r in (rows or []) if r.get("dataset") in wanted]
    date_keys, block_keys = axes_from(kept, axes_hint)
    date_at = {k: i for i, k in enumerate(date_keys)}
    block_at = {k: i for i, k in enumerate(block_keys)}

    matrices: Dict[str, Dict[Optional[str], List[List[Optional[float]]]]] = {
        code: {} for code in codes
    }
    for r in kept:
        code = r["dataset"]
        entity = r.get("entity")
        entity = None if entity is None else str(entity)
        by_entity = matrices[code]
        matrix = by_entity.get(entity)
        if matrix is None:
            matrix = _empty_matrix(len(block_keys), len(date_keys))
            by_entity[entity] = matrix
        bi = block_at.get(str(r["block_key"]))
        di = date_at.get(str(r["date_key"]))
        if bi is None or di is None:  # pragma: no cover - axes are a union of these
            continue
        value = r.get("value")
        if value is None and matrix[bi][di] is not None:
            # A duplicate cell: a real number beats a null. Two adapters cannot
            # produce the same dataset, so this only guards against a repeated key.
            continue
        matrix[bi][di] = value

    series: List[dict] = []
    computed_missing: List[str] = []
    for code, dataset in entries:
        by_entity = matrices[code]
        fixed = fixed_order.get(getattr(dataset, "series", None))
        if fixed:
            by_entity = {entity: by_entity.get(entity) or
                         _empty_matrix(len(block_keys), len(date_keys))
                         for entity in fixed}
            ordered = list(by_entity.items())
        else:
            if not by_entity:
                # Asked for, nothing came back. Still render the series so the
                # absence is visible rather than the row silently vanishing.
                by_entity = {None: _empty_matrix(len(block_keys), len(date_keys))}
            ordered = sorted(by_entity.items(),
                             key=lambda kv: (kv[0] is not None, kv[0] or ""))
        has_value = False
        for entity, matrix in ordered:
            quality = _quality(code, matrix, failed)
            has_value = has_value or quality in (QUALITY_COMPLETE, QUALITY_PARTIAL)
            series.append({
                "dataset": code,
                "label": dataset.label,
                "unit": dataset.unit,
                "entity": entity,
                # Readers see this; `entity` stays the stable machine key.
                "entity_label": entity_label(dataset, entity),
                # What the entity IS (plant, exchange, forecast type) so a writer
                # can name its column instead of a generic "Entity".
                "series_kind": getattr(dataset, "series", None),
                "values": matrix,
                "quality": quality,
            })
        if not has_value and code not in failed:
            computed_missing.append(code)

    out_meta = dict(meta_in)
    out_meta["truncated"] = bool(meta_in.get("truncated", False))
    out_meta["missing_datasets"] = _dedupe(
        list(meta_in.get("missing_datasets") or []) + computed_missing)
    out_meta["failed_datasets"] = _dedupe(failed)
    if "cells" not in out_meta:
        out_meta["cells"] = len(series) * len(date_keys) * len(block_keys)
    if not out_meta.get("generated_at"):
        out_meta["generated_at"] = datetime.now(timezone.utc).isoformat()

    return {
        "axes": {
            "dates": date_keys,
            "blocks": [block_axis_value(b) for b in block_keys],
        },
        "series": series,
        "meta": out_meta,
    }
