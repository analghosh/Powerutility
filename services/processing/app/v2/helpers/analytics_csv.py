"""Streaming CSV for the analytics dashboard export.

`csv_rows` is a **generator** and must stay one. Two callers share it — the
`StreamingResponse` on `POST /analytics/export` and the chunked export worker — and
neither may hold the file in memory: a 500k-cell CSV is the whole point of the
streaming tier, and a worker that materialised its chunk would defeat the chunking.
Nothing here builds a list of rows, calls `len()` on the series, or joins the output;
the response's `series` is walked exactly once, lazily.

Layout is the one table `analytics_layout` builds, the same one the workbook holds:
one row per (day, block), the identity columns the chosen Group Date By calls for,
then one column per series. It replaced the flat
`dataset,label,unit,date,block,value` form on 2026-09-20 — that shape repeated the
dataset name on every line and put two datasets a user wanted to compare a hundred
thousand rows apart.

A `#`-commented block of the filters precedes it. An export always re-runs from the
stored filters, so that block is the only record of what the user actually picked —
it is printed for the person who opens the file a week later and has to know whether
they are looking at DAM or RTM.

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

from ..analytics import registry
from . import analytics_layout

#: Columns are no longer fixed — they are the identity columns for the chosen
#: grouping plus one per series, and `analytics_layout.column_titles` builds them.
#: The name is kept because the route and the tests import it as the CSV contract.
COLUMNS = analytics_layout.column_titles


def series_label(series: Dict[str, Any]) -> str:
    """`label · entity (unit)` — the heading this series gets in the table."""
    return analytics_layout.column_label(series)

#: Formats `filename_for` will name. Mirrors `models.EXPORT_FORMATS`.
SUPPORTED_FORMATS = ("csv", "xlsx")

DEFAULT_MODULE = "analytics"

#: Longest stem a caller-supplied file name may contribute. Well under every
#: filesystem's 255-byte limit, with room for the extension and a job suffix.
MAX_FILENAME_STEM = 120
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

    `Date grouping` is read from the response, not from the request. It used to be
    read from the request, and a ticked weekly dataset silently rewrote the grouping
    for the whole query — so a workbook whose columns were ISO weeks carried the line
    `Date grouping: BLOCK_WISE`, and nobody reconciling it could tell.
    """
    filters = filters or {}
    meta = (response or {}).get("meta") or {}

    pairs: List[Tuple[str, str]] = [
        ("Generated", _text(meta.get("generated_at"))),
        ("Date range", f"{_text(filters.get('from_date'))} to {_text(filters.get('to_date'))}"),
        ("Block range", f"{_text(filters.get('from_block'))} to {_text(filters.get('to_block'))}"),
        ("Date grouping", analytics_layout.grouping_of(response, filters)),
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

    dsm_fields = filters.get("dsm_fields") or []
    if "DSM_PUBLISHED" in (filters.get("datasets") or []) and dsm_fields:
        pairs.append(("DSM fields", ", ".join(
            registry.DSM_PUBLISHED_LABELS.get(f, f) for f in dsm_fields)))

    fmt = _text(filters.get("format"))
    if fmt:
        pairs.append(("Format", fmt))

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
    """A gap is an empty field. Never `0`, never `"None"` — a real `0` survives.

    A `date` is rendered ISO, which sorts correctly as text and is what a
    spreadsheet reimports as a date.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def csv_rows(response: Dict[str, Any],
             filters: Optional[Dict[str, Any]] = None,
             on_row=None) -> Iterator[str]:
    """Yield the export one newline-terminated line at a time.

    Order is the header block, the column headings, then one line per (day, block) —
    including the rows that are entirely empty, so the grid the user asked for is
    reconstructible from the file rather than silently losing its gaps.

    This must stay a generator: the streaming tier exists so a large export never
    sits in memory as one string.
    """
    response = response or {}

    for line in header_lines(filters, response):
        yield line

    emit = _row_writer()
    yield emit(analytics_layout.column_titles(response, filters))

    for row in analytics_layout.data_rows(response, filters, on_row=on_row):
        yield emit([_cell(v) for v in row])


def _slug(value: Any) -> str:
    return _SLUG_STRIP.sub("_", _text(value).strip().lower()).strip("_")


def _date_slug(value: Any) -> str:
    """ISO date, reduced to filename-safe characters.

    Anything else becomes `unknown` rather than reaching a `Content-Disposition`
    header: the date range is caller-supplied, and a `/` or a newline in a filename
    is a header-injection bug, not a cosmetic one.
    """
    return _UNSAFE_FILENAME.sub("", _text(value)[:10].strip()) or "unknown"


def safe_file_stem(value: Any) -> str:
    """A caller-supplied file name reduced to a stem this server will write.

    Everything here is defence, in order of how badly it goes wrong:

    * The name travels in a `Content-Disposition` header, so a `"`, a newline or a
      `;` is header injection, not a cosmetic problem.
    * It is joined onto the export directory, so `..`, `/` and `\\` are a path
      traversal — `os.path.join(dir, "../../etc/x")` escapes the directory.
    * A leading dot hides the file; a trailing dot or space is silently stripped by
      Windows, which is where PTC opens these.

    Whatever survives is truncated, and an empty result means "the caller gave us
    nothing usable" — the caller then falls back to the generated name rather than
    writing a file called `.csv`.
    """
    text = _text(value).strip()
    if not text:
        return ""
    # A supplied extension is dropped: the format decides it, and `report.exe` must
    # not become `report.exe.csv` in a mail client that trusts the first one.
    for suffix in SUPPORTED_FORMATS:
        if text.lower().endswith("." + suffix):
            text = text[: -(len(suffix) + 1)]
            break
    # `/` and `\` are not in the allow-list, so they become `_` here — which is what
    # defeats traversal: once there is no separator left, a surviving `..` is just
    # two dots in a name. Stripping leading dots then stops a hidden file, and
    # trailing dots and spaces stop the silent rename Windows performs.
    text = _UNSAFE_FILENAME.sub("_", text).strip("._ ")
    return text[:MAX_FILENAME_STEM]


def filename_for(filters: Optional[Dict[str, Any]], fmt: str) -> str:
    """`analytics_2026-04-01_to_2026-08-31.csv` — module name and date range.

    The date range is in the name because that is what people search their Downloads
    folder by, and it is deterministic so a regenerated export overwrites the stale
    copy rather than accumulating `(1)` suffixes.

    `filters["file_name"]` overrides the whole stem when the user typed one. The
    override is sanitised, never trusted: see `safe_file_stem`. A name that sanitises
    away to nothing falls back to the generated one rather than failing the export —
    the file is the thing the user asked for, and its name is not worth losing it over.
    """
    normalised = _text(fmt).strip().lower().lstrip(".")
    if normalised not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported export format {fmt!r}; expected one of "
            f"{', '.join(SUPPORTED_FORMATS)}")

    filters = filters or {}

    chosen = safe_file_stem(filters.get("file_name"))
    if chosen:
        return f"{chosen}.{normalised}"

    module = _slug(filters.get("module")) or DEFAULT_MODULE
    start = _date_slug(filters.get("from_date"))
    end = _date_slug(filters.get("to_date"))

    return f"{module}_{start}_to_{end}.{normalised}"
