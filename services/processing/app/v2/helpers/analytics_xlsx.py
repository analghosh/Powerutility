"""Write-only XLSX for the analytics dashboard export.

`openpyxl`'s **write-only mode** is not an optimisation here, it is the reason this
tier exists at all: a normal `Workbook` keeps every `Cell` object alive until `save()`,
which for 150k cells is hundreds of megabytes on a box that also serves bids. In
write-only mode each appended row is streamed to a temporary file and dropped, so the
peak cost is one row.

    wb = Workbook(write_only=True)          # streams rows to a temp file

The layout is the spec's: one sheet per dataset plus a `Filters` sheet. Inside a
dataset sheet the grid is the one the user saw — `Entity | Block | <date> ...` — with
one row per (entity, block), because a spreadsheet is opened to be read, not parsed.
The flat long form lives in `analytics_csv`, which is the machine-readable half.

Two rules this module exists to keep:

- **A missing value is an empty cell.** `None` is appended as `None`, which openpyxl
  writes as a genuinely blank cell — not `0`, not the string `"None"`. A real `0`
  survives as `0`. In a settlement spreadsheet those are different numbers.
- **The directory is created before the first write.** `ANALYTICS_EXPORT_DIR` defaults
  to `/var/lib/epms/exports`, which does not exist on a dev machine and is not
  writable by the service user on the PROD box either. Failing at `makedirs` with the
  path and the setting named beats a bare `PermissionError` out of the zip writer.
"""

import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence

from openpyxl import Workbook

from .analytics_csv import COLUMNS, SUPPORTED_FORMATS, filename_for, filter_pairs

logger = logging.getLogger(__name__)

__all__ = ["ExportWriteError", "FILTERS_SHEET", "SHEET_TITLE_LIMIT",
           "sheet_titles_for", "write_xlsx", "filename_for", "COLUMNS",
           "SUPPORTED_FORMATS"]

#: The filters sheet is named in the spec; the frontend links to it by name.
FILTERS_SHEET = "Filters"

#: Excel's hard limit. openpyxl raises past it, and its own de-duplication suffix can
#: itself overflow, so titles are truncated and de-duplicated here first.
SHEET_TITLE_LIMIT = 31

#: Characters Excel refuses in a sheet title.
_INVALID_TITLE_CHARS = set(r"[]:*?/\"'")

_IDENTITY_COLUMNS = ("Entity", "Block")


class ExportWriteError(OSError):
    """The export file could not be written — directory, permissions or disk."""


def _safe_title(label: Any, fallback: str) -> str:
    text = "" if label is None else str(label)
    cleaned = "".join(" " if ch in _INVALID_TITLE_CHARS else ch for ch in text).strip()
    cleaned = cleaned.strip("'")
    return (cleaned or str(fallback) or "Sheet")[:SHEET_TITLE_LIMIT].strip() or "Sheet"


def _dedupe_title(title: str, taken: Dict[str, None]) -> str:
    """Keep the title unique *and* inside Excel's 31 characters.

    The suffix eats into the name rather than extending past the limit, which is
    precisely what openpyxl's own `avoid_duplicate_name` does not do.
    """
    if title not in taken:
        taken[title] = None
        return title
    for n in range(2, 1000):
        suffix = f"~{n}"
        candidate = (title[:SHEET_TITLE_LIMIT - len(suffix)].strip() + suffix)
        if candidate not in taken:
            taken[candidate] = None
            return candidate
    raise ExportWriteError(  # pragma: no cover - 998 identically named datasets
        f"Cannot make sheet title {title!r} unique within "
        f"{SHEET_TITLE_LIMIT} characters")


def sheet_titles_for(series: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    """Dataset code -> sheet title, truncated to 31 chars and de-duplicated.

    Exposed so the routes can name a sheet in an error message without rebuilding
    the workbook.
    """
    taken: Dict[str, None] = {FILTERS_SHEET: None}
    titles: Dict[str, str] = {}
    for entry in series or ():
        code = str(entry.get("dataset") or "")
        if code in titles:
            continue
        titles[code] = _dedupe_title(_safe_title(entry.get("label"), code), taken)
    return titles


def _ensure_directory(path: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise ExportWriteError(
            f"Cannot create the analytics export directory {directory!r}: {exc}. "
            f"Check ANALYTICS_EXPORT_DIR and that the service user owns it."
        ) from exc
    if not os.path.isdir(directory):  # pragma: no cover - makedirs would have raised
        raise ExportWriteError(
            f"Analytics export directory {directory!r} is not a directory. "
            f"Check ANALYTICS_EXPORT_DIR.")
    if not os.access(directory, os.W_OK):
        raise ExportWriteError(
            f"Analytics export directory {directory!r} is not writable by "
            f"{os.getuid()}. Check ANALYTICS_EXPORT_DIR and its ownership.")


def _cell(value: Any) -> Any:
    """`None` stays `None` so openpyxl writes a blank cell. `0` stays `0`."""
    return value


def _date_header(dates: Iterable[Any]) -> List[str]:
    return [str(d) for d in dates]


def write_xlsx(response: Dict[str, Any], filters: Optional[Dict[str, Any]],
               path: str) -> Dict[str, Any]:
    """Write one sheet per dataset plus `Filters`; return `{path, bytes, row_count}`.

    `row_count` counts data rows only — the header rows and the `Filters` sheet are
    not part of what the user asked for, and the job row reports this number back to
    them as "rows exported".
    """
    response = response or {}
    axes = response.get("axes") or {}
    dates = _date_header(axes.get("dates") or ())
    blocks = list(axes.get("blocks") or ())

    # Unlike the CSV, the workbook needs the series grouped by dataset, so this one
    # is materialised. It is the cell matrices that are large, not the series list
    # (a few hundred at most), and each matrix is still walked once.
    series = list(response.get("series") or ())
    titles = sheet_titles_for(series)

    _ensure_directory(path)

    workbook = Workbook(write_only=True)
    row_count = 0
    try:
        sheets: Dict[str, Any] = {}
        for entry in series:
            code = str(entry.get("dataset") or "")
            sheet = sheets.get(code)
            if sheet is None:
                sheet = workbook.create_sheet(title=titles[code])
                sheet.append(list(_IDENTITY_COLUMNS) + dates)
                sheets[code] = sheet

            entity = entry.get("entity")
            matrix = entry.get("values") or ()
            for bi, block in enumerate(blocks):
                values = matrix[bi] if bi < len(matrix) else ()
                cells = [values[di] if di < len(values) else None
                         for di in range(len(dates))]
                sheet.append([_cell(entity), _cell(block)] + [_cell(v) for v in cells])
                row_count += 1

        filters_sheet = workbook.create_sheet(title=FILTERS_SHEET)
        filters_sheet.append(["Filter", "Value"])
        for label, value in filter_pairs(filters, response):
            filters_sheet.append([label, value])

        workbook.save(path)
    except ExportWriteError:
        raise
    except OSError as exc:
        raise ExportWriteError(
            f"Cannot write the analytics export {path!r}: {exc}. "
            f"Check ANALYTICS_EXPORT_DIR, its free space and its permissions."
        ) from exc
    finally:
        workbook.close()

    size = os.path.getsize(path)
    logger.info("Analytics XLSX export written: path=%s bytes=%s rows=%s sheets=%s",
                path, size, row_count, len(titles) + 1)
    return {"path": path, "bytes": size, "row_count": row_count}
