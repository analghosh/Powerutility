"""Streaming CSV for the analytics dashboard export.

`csv_rows` is a **generator** and must stay one. Two callers share it — the
`StreamingResponse` on `POST /analytics/export` and the chunked export worker — and
neither may hold the file in memory: a 500k-cell CSV is the whole point of the
streaming tier, and a worker that materialised its chunk would defeat the chunking.
Nothing here builds a list of rows, calls `len()` on the series, or joins the output;
the response's `series` is walked exactly once, lazily.

Layout is flat (long) rather than pivoted, which is what makes the stream possible and
what the spec asks for: a `dataset` identifier column, one line per cell, preceded by a
`#`-commented block recording the filters that produced it. A per-plant, per-exchange
or per-forecast-type series carries its entity inside `label` — `Declared Capacity —
by plant (PPCLGPP)` — rather than in a column of its own: most datasets are scalar,
and a column that is empty on most rows was the first thing users asked to remove. An export
always re-runs from the stored filters, so that block is the only record of what the
user actually picked — it is printed for the person who opens the file a week later
and has to know whether they are looking at DAM or RTM.

**A missing value is an empty cell.** Never `0`, never the string `"None"`. The pivot
keeps "nothing was scheduled" and "zero was scheduled" apart all the way down the
pipeline; rendering the first as the second here would put a wrong number in front of
a trader at the last possible moment.
"""

import csv
import io
import re
from datetime import date, datetime
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

#: The flat column contract. The entity (plant, exchange, forecast type) is folded
#: into `label` by `series_label`; `dataset` stays the stable machine key.
COLUMNS = ("dataset", "label", "unit", "date", "block", "value")


def series_label(series: Dict[str, Any]) -> str:
    """`label (entity)` when the series has an entity, else just the label."""
    label = _text(series.get("label"))
    entity = series.get("entity_label") or series.get("entity")
    return f"{label} ({entity})" if entity not in (None, "") else label

#: Formats `filename_for` will name. Mirrors `models.EXPORT_FORMATS`.
SUPPORTED_FORMATS = ("csv", "xlsx")

DEFAULT_MODULE = "analytics"
TITLE_LINE = "EPMS Analytics Export"
COMMENT_PREFIX = "# "

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9_.-]+")


def _text(value: Any) -> str:
    """Render a filter value for the header block. A missing filter is blank."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return ", ".join(_text(v) for v in value)
    return str(value)


def _ranges_text(pairs: Any) -> str:
    """`[[1, 8], [9, 16]]` -> `1-8, 9-16`."""
    out: List[str] = []
    for pair in pairs or ():
        try:
            lo, hi = pair[0], pair[1]
        except (TypeError, IndexError, KeyError):
            continue
        out.append(f"{_text(lo)}-{_text(hi)}")
    return ", ".join(out)


def _block_grouping(filters: Dict[str, Any]) -> str:
    """The stored job echo says `block_grouping`; the request model says `block_band`."""
    return _text(filters.get("block_grouping") or filters.get("block_band"))


def filter_pairs(filters: Optional[Dict[str, Any]],
                 response: Optional[Dict[str, Any]] = None) -> List[Tuple[str, str]]:
    """The applied-filter summary, shared by the CSV header block and the XLSX sheet.

    One source of truth on purpose: the two files must not disagree about what was
    asked for.
    """
    filters = filters or {}
    meta = (response or {}).get("meta") or {}

    pairs: List[Tuple[str, str]] = [
        ("Generated", _text(meta.get("generated_at"))),
        ("Date range", f"{_text(filters.get('from_date'))} to {_text(filters.get('to_date'))}"),
        ("Block range", f"{_text(filters.get('from_block'))} to {_text(filters.get('to_block'))}"),
        ("Date grouping", _text(filters.get("date_grouping"))),
        ("Block grouping", _block_grouping(filters)),
    ]

    custom = _ranges_text(filters.get("custom_blocks"))
    if custom:
        pairs.append(("Custom blocks", custom))

    pairs.append(("Datasets", _text(filters.get("datasets"))))

    for label, key in (("Plants", "plants"), ("Exchanges", "exchanges")):
        value = _text(filters.get(key))
        if value:
            pairs.append((label, value))

    fmt = _text(filters.get("format"))
    if fmt:
        pairs.append(("Format", fmt))

    # A weekly/monthly bucket that the range only partly covers is labelled with the
    # whole period; say so, or a "W35" of three days reads as a full week.
    cov = meta.get("period_coverage") or {}
    partial = [k for k, v in cov.items() if isinstance(v, dict) and v.get("partial")]
    if partial:
        detail = "; ".join(
            f"{k} ({cov[k]['days_covered']} of {cov[k]['days_in_period']} days)" for k in partial)
        pairs.append(("Partial periods", detail))

    truncated = meta.get("truncated")
    if truncated:
        pairs.append(("Truncated", "yes - the result hit the cell cap"))
    for label, key in (("Missing datasets", "missing_datasets"),
                       ("Failed datasets", "failed_datasets")):
        value = _text(meta.get(key))
        if value:
            pairs.append((label, value))

    return pairs


def header_lines(filters: Optional[Dict[str, Any]],
                 response: Optional[Dict[str, Any]] = None) -> Iterator[str]:
    """The `#`-commented filter block, newline-terminated, one line at a time."""
    yield f"{COMMENT_PREFIX}{TITLE_LINE}\n"
    for label, value in filter_pairs(filters, response):
        yield f"{COMMENT_PREFIX}{label}: {value}\n"


def _row_writer():
    """A one-row CSV encoder that reuses a single buffer instead of growing one."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    def emit(row: Sequence[Any]) -> str:
        buffer.seek(0)
        buffer.truncate(0)
        writer.writerow(row)
        return buffer.getvalue()

    return emit


def _cell(value: Any) -> Any:
    """A gap is an empty field. Never `0`, never `"None"` — a real `0` survives."""
    return "" if value is None else value


def csv_rows(response: Dict[str, Any],
             filters: Optional[Dict[str, Any]] = None) -> Iterator[str]:
    """Yield the export one newline-terminated line at a time.

    Order is header block, column header, then one line per (series, block, date)
    cell — including the empty ones, so the grid the user saw is reconstructible
    from the file rather than silently losing its gaps.
    """
    response = response or {}
    axes = response.get("axes") or {}
    dates = [_text(d) for d in (axes.get("dates") or ())]
    blocks = list(axes.get("blocks") or ())

    for line in header_lines(filters, response):
        yield line

    emit = _row_writer()
    yield emit(COLUMNS)

    # `series` is walked lazily and exactly once: it may be a cursor-backed iterator
    # in the worker, and it is never counted, sorted or copied here.
    for series in (response.get("series") or ()):
        dataset = _text(series.get("dataset"))
        label = series_label(series)
        unit = _text(series.get("unit"))
        matrix = series.get("values") or ()

        for bi, block in enumerate(blocks):
            row = matrix[bi] if bi < len(matrix) else ()
            for di, day in enumerate(dates):
                value = row[di] if di < len(row) else None
                yield emit((dataset, label, unit, day, block, _cell(value)))


def _slug(value: Any) -> str:
    return _SLUG_STRIP.sub("_", _text(value).strip().lower()).strip("_")


def _date_slug(value: Any) -> str:
    """ISO date, reduced to filename-safe characters.

    Anything else becomes `unknown` rather than reaching a `Content-Disposition`
    header: the date range is caller-supplied, and a `/` or a newline in a filename
    is a header-injection bug, not a cosmetic one.
    """
    return _UNSAFE_FILENAME.sub("", _text(value)[:10].strip()) or "unknown"


def filename_for(filters: Optional[Dict[str, Any]], fmt: str) -> str:
    """`analytics_2026-04-01_to_2026-08-31.csv` — module name and date range.

    The date range is in the name because that is what people search their Downloads
    folder by, and it is deterministic so a regenerated export overwrites the stale
    copy rather than accumulating `(1)` suffixes.
    """
    normalised = _text(fmt).strip().lower().lstrip(".")
    if normalised not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported export format {fmt!r}; expected one of "
            f"{', '.join(SUPPORTED_FORMATS)}")

    filters = filters or {}
    module = _slug(filters.get("module")) or DEFAULT_MODULE
    start = _date_slug(filters.get("from_date"))
    end = _date_slug(filters.get("to_date"))

    return f"{module}_{start}_to_{end}.{normalised}"
