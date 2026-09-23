"""
Reading a DSM settlement workbook.

The file is a block-by-block deviation statement: one row per 15-minute block,
with the settlement day in a Date column of its own. A single upload therefore
covers however many days the settlement period ran to — the sample PTC supplied
holds seven — which is why this category asks the operator for no delivery date.
Asking for one would record a single day as though it were the whole file, and
the file itself already knows the answer.

What comes out is grouped by day rather than left as a flat list. Every screen
that reads this asks a day-shaped question ("show me 1-15 April"), the storage
is keyed on the day, and a re-upload replaces a day at a time. Grouping once
here means the rest of the flow never re-derives it.

Two decisions worth stating, because both are guesses the file cannot settle:

* **A date written ``03-04-2026`` is 3 April, not 4 March.** Ambiguous only in
  a CSV — a real workbook carries a date cell, which has no ambiguity at all —
  and day-first is what every Indian settlement file uses. ISO is tried first
  regardless, so ``2026-04-03`` is never misread.
* **A row whose date or time will not parse is refused, not skipped.** A
  silently dropped block is a missing quarter-hour of settlement that nothing
  on the screen would show as missing.
"""

import csv
import io
import re
from datetime import date as date_type
from datetime import datetime, time as time_type, timedelta
from typing import Any, Dict, List, Optional, Tuple

#: Minutes in one settlement block, and how many of them make a day.
MINUTES_PER_BLOCK = 15
BLOCKS_PER_DAY = 24 * 60 // MINUTES_PER_BLOCK


class DsmParseError(Exception):
    """
    The file could not be read as a DSM statement.

    `reason` is the machine-readable code the upload route turns into a refusal
    the screen can branch on; `details` carries whatever the operator needs in
    order to fix the file.
    """

    def __init__(self, reason: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.details = details or {}


#: Stored field -> the column header it is published under, normalised.
#:
#: Matched on the normalised header rather than on position, because the
#: sample's own headers already mix spaces and underscores
#: (``Schedule Total``, ``Overdrawl_Charges``) and a column order is not
#: something the publisher has promised to keep.
COLUMNS: Tuple[Tuple[str, str], ...] = (
    ("report_date", "date"),
    ("block_time", "time"),
    ("schedule_total", "schedule total"),
    ("actual_total", "actual total"),
    ("frequency", "frequency"),
    ("deviation_rate", "deviation rate"),
    ("deviation", "deviation"),
    ("overdrawl_charges", "overdrawl charges"),
    ("underdrawl_charges", "underdrawl charges"),
)

#: The fields carrying figures, in the order the screen and the export show
#: them. Separate from COLUMNS because the date and time are not figures.
NUMERIC_FIELDS: Tuple[str, ...] = (
    "schedule_total",
    "actual_total",
    "frequency",
    "deviation_rate",
    "deviation",
    "overdrawl_charges",
    "underdrawl_charges",
)

#: The header each stored field is published under, as PTC reads it. Used for
#: the export and for naming a missing column in a refusal.
FIELD_LABELS: Dict[str, str] = {
    "report_date": "Date",
    "block_time": "Time",
    "schedule_total": "Schedule Total",
    "actual_total": "Actual Total",
    "frequency": "Frequency",
    "deviation_rate": "Deviation Rate",
    "deviation": "Deviation",
    "overdrawl_charges": "Overdrawl_Charges",
    "underdrawl_charges": "Underdrawl_Charges",
}

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def normalise_header(value: Any) -> str:
    """``"Overdrawl_Charges"`` and ``"Overdrawl Charges"`` both -> ``"overdrawl charges"``."""
    return _NON_ALPHANUMERIC.sub(" ", str(value or "").strip().lower()).strip()


# --- cell readers -------------------------------------------------------------


def _as_date(value: Any, row_number: int) -> str:
    """One Date cell as ``YYYY-MM-DD``."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date_type):
        return value.isoformat()

    text = str(value or "").strip()
    if not text:
        raise DsmParseError(
            "missing_date",
            f"Row {row_number} has no date in its Date column.",
            {"row": row_number},
        )
    # A CSV written out of Excel often carries the time along with the date.
    text = text.split(" ")[0] if " " in text and ":" in text else text
    for pattern in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    raise DsmParseError(
        "unreadable_date",
        f"'{text}' in row {row_number} is not a date this report can be filed under.",
        {"row": row_number, "value": text},
    )


def _as_time(value: Any, row_number: int) -> str:
    """One Time cell as ``HH:MM``."""
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    if isinstance(value, time_type):
        return value.strftime("%H:%M")
    if isinstance(value, timedelta):
        # Excel stores a duration-shaped time this way once it passes 24:00.
        minutes = int(value.total_seconds() // 60) % (24 * 60)
        return f"{minutes // 60:02d}:{minutes % 60:02d}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # A bare Excel serial time is a fraction of a day.
        minutes = round(float(value) % 1 * 24 * 60)
        return f"{minutes // 60 % 24:02d}:{minutes % 60:02d}"

    text = str(value or "").strip()
    if not text:
        raise DsmParseError(
            "missing_time",
            f"Row {row_number} has no time in its Time column.",
            {"row": row_number},
        )
    for pattern in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"):
        try:
            return datetime.strptime(text.upper(), pattern).strftime("%H:%M")
        except ValueError:
            continue
    raise DsmParseError(
        "unreadable_time",
        f"'{text}' in row {row_number} is not a time of day.",
        {"row": row_number, "value": text},
    )


def _as_number(value: Any) -> Optional[float]:
    """
    One figure cell.

    A blank stays None rather than becoming 0. The two mean different things
    here: an Overdrawl_Charges of 0 is a block that was not overdrawn, and a
    blank is a block the statement said nothing about.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "NA", "N/A", "na"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def block_of(block_time: str) -> int:
    """``"00:15"`` -> block 2. The block a time of day falls in, 1-96."""
    hour, minute = (int(part) for part in block_time.split(":")[:2])
    return hour * (60 // MINUTES_PER_BLOCK) + minute // MINUTES_PER_BLOCK + 1


# --- readers for each file type -----------------------------------------------


def _rows_from_xlsx(data: bytes) -> List[List[Any]]:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # pragma: no cover - corrupt upload only
        raise DsmParseError(
            "unreadable_file",
            "That file could not be opened as a spreadsheet.",
            {"detail": str(exc)},
        ) from exc
    try:
        # The first sheet. The sample names its sheet after the portfolio, so
        # there is no fixed name to look for, and every sample so far has one.
        sheet = workbook.worksheets[0]
        return [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()


def _rows_from_csv(data: bytes) -> List[List[Any]]:
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:  # pragma: no cover - latin-1 decodes anything
        raise DsmParseError("unreadable_file", "That CSV could not be decoded.")

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [list(row) for row in csv.reader(io.StringIO(text), dialect)]


def _grid(data: bytes, filename: str) -> List[List[Any]]:
    if (filename or "").lower().endswith(".csv"):
        return _rows_from_csv(data)
    return _rows_from_xlsx(data)


# --- the parse ----------------------------------------------------------------


def _locate_header(grid: List[List[Any]]) -> Tuple[int, Dict[str, int]]:
    """
    Which row holds the headers, and which column each field is in.

    Searched for rather than assumed to be row 1, because a statement exported
    with a title or a portfolio line above the table is common and costs
    nothing to tolerate. The search stops at the first row that carries every
    column, so a stray earlier row that happens to say "Date" is not mistaken
    for the header.
    """
    wanted = {header: field for field, header in COLUMNS}
    for index, row in enumerate(grid[:25]):
        positions: Dict[str, int] = {}
        for column, cell in enumerate(row):
            field = wanted.get(normalise_header(cell))
            # First occurrence wins: a statement that repeats a column later
            # is reporting the same figure twice, not a different one.
            if field and field not in positions:
                positions[field] = column
        if len(positions) == len(COLUMNS):
            return index, positions

    # Nothing matched in full. Report against the best row found, so the
    # message names the columns that are actually absent.
    best: Dict[str, int] = {}
    for row in grid[:25]:
        found = {
            wanted[normalise_header(cell)]: column
            for column, cell in enumerate(row)
            if normalise_header(cell) in wanted
        }
        if len(found) > len(best):
            best = found
    missing = [FIELD_LABELS[field] for field, _ in COLUMNS if field not in best]
    # Listing nine absent columns reads as a wall of text that says nothing a
    # reader can act on; at that point the file is simply not a DSM statement.
    detail = (
        "none of its columns are the ones a DSM statement carries"
        if len(missing) == len(COLUMNS)
        else "it has no " + ", ".join(missing) + " column"
    )
    raise DsmParseError(
        "missing_columns",
        f"That file is not a DSM statement: {detail}.",
        {"missing_columns": missing},
    )


def parse(data: bytes, filename: str) -> Dict[str, Any]:
    """
    Read one DSM workbook into its days.

    Returns ``{"days": [...], "row_count": int, "date_from": str, "date_to": str}``
    where each day is ``{"report_date": "YYYY-MM-DD", "blocks": [...]}`` and a
    block carries its number, its time and the seven figures.
    """
    grid = _grid(data, filename)
    if not grid:
        raise DsmParseError("empty_file", "That file has no rows in it.")

    header_row, positions = _locate_header(grid)

    def cell(row: List[Any], field: str) -> Any:
        column = positions[field]
        return row[column] if column < len(row) else None

    # Insertion-ordered, so the days come back in the order the file lists
    # them and a same-day re-read is idempotent.
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    seen: Dict[str, set] = {}

    for offset, row in enumerate(grid[header_row + 1 :]):
        # Spreadsheet row number, 1-based, so a refusal names the row the
        # operator sees in Excel.
        row_number = header_row + offset + 2
        if not any(value not in (None, "") for value in row):
            continue  # a blank spacer row, which Excel adds freely
        report_date = _as_date(cell(row, "report_date"), row_number)
        block_time = _as_time(cell(row, "block_time"), row_number)
        block = block_of(block_time)

        blocks_seen = seen.setdefault(report_date, set())
        if block in blocks_seen:
            raise DsmParseError(
                "duplicate_block",
                f"{report_date} block {block} ({block_time}) appears twice in "
                "that file, so there is no single figure to store for it.",
                {"report_date": report_date, "block": block, "row": row_number},
            )
        blocks_seen.add(block)

        by_date.setdefault(report_date, []).append(
            {
                "block": block,
                "block_time": block_time,
                **{field: _as_number(cell(row, field)) for field in NUMERIC_FIELDS},
            }
        )

    if not by_date:
        raise DsmParseError(
            "no_data_rows",
            "That file has the right columns but no settlement rows under them.",
        )

    days = [
        {
            "report_date": report_date,
            "blocks": sorted(blocks, key=lambda entry: entry["block"]),
            "block_count": len(blocks),
            # Said out loud rather than left to be inferred. A settlement day
            # is 96 blocks; a day that came in short is worth showing as short
            # rather than quietly displaying 40 rows as if that were the day.
            "complete": len(blocks) == BLOCKS_PER_DAY,
        }
        for report_date, blocks in by_date.items()
    ]
    days.sort(key=lambda entry: entry["report_date"])

    return {
        "days": days,
        "row_count": sum(day["block_count"] for day in days),
        "date_from": days[0]["report_date"],
        "date_to": days[-1]["report_date"],
    }
