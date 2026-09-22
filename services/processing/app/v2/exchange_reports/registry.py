"""
What the Report Uploader will accept, in one place.

The screen's Report Category dropdown, the file types its drop zone advertises
and the parser the backend reaches for all come from this table. Keeping them
together is the point: a category the frontend offers but the backend cannot
parse is a user who fills in the form, waits for the upload and then gets an
error, and a file type advertised in the hint text but rejected on submit is
the same failure one step later.

A category that has no parser is not necessarily unfinished. Three states are
kept apart here, because the screen has to say something different about each:

* **parsed** - a sample file exists and figures are read out of it.
* **store-only** (`stores_file_only`) - the file is kept and handed back byte
  for byte, and nothing is read out of it. A finished state, not a pending one.
* **not available** (`implemented=False`) - listed so the screen shows the full
  set PTC asked for, and says plainly that it is not ready instead of failing
  at submit with something that reads like a bug.

A store-only category is filed either by delivery day or, when `period_keyed`
is set, by calendar month.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ReportCategory:
    """One entry in the Report Category dropdown."""

    code: str
    label: str
    #: Lower-case extensions, with the dot, that the drop zone accepts.
    accepted_extensions: tuple
    #: False until a sample file exists and a parser has been written for it.
    implemented: bool
    #: Shown on the screen when the category is picked but not yet available.
    unavailable_reason: Optional[str] = None
    description: str = ""
    #: True when the file is kept and handed back as uploaded, with nothing
    #: read out of it. A category is store-only because there is no fixed
    #: layout to parse, not because a parser is still to come — so it is a
    #: finished state, unlike `implemented=False`.
    stores_file_only: bool = False
    #: True when the report covers a calendar month rather than a delivery
    #: day. The uploader asks for Year and Month instead of a Delivery Date,
    #: and the file is filed under that month. A monthly report has no single
    #: day the power flowed on, so forcing one would record an answer nobody
    #: meant — the same reasoning that keeps exchange and market off a
    #: store-only category.
    period_keyed: bool = False


OBLIGATION_REPORT = "obligation_report"
BACKING_QUANTUM_SCHEDULE_COST = "backing_quantum_schedule_cost"
LTOA = "ltoa"
STOA = "stoa"
OTHER_REPORTS = "other_reports"

_NO_SAMPLE = (
    "No sample file has been supplied for this category yet, so there is "
    "nothing to parse it with. Send one and it will be enabled."
)

CATEGORIES: Dict[str, ReportCategory] = {
    OBLIGATION_REPORT: ReportCategory(
        code=OBLIGATION_REPORT,
        label="Obligation Report",
        # The exchange publishes this one as a Crystal Reports PDF. Accepting a
        # spreadsheet here would only fail later, in the parser.
        accepted_extensions=(".pdf",),
        implemented=True,
    ),
    BACKING_QUANTUM_SCHEDULE_COST: ReportCategory(
        code=BACKING_QUANTUM_SCHEDULE_COST,
        label="Backing Quantum/Schedule cost",
        accepted_extensions=(".xlsx", ".xls", ".csv"),
        implemented=False,
        unavailable_reason=_NO_SAMPLE,
    ),
    # LTOA and STOA are month reports, and store-only for the same reason
    # Other Reports is: no sample file has been supplied, so there is no
    # layout to parse. Keeping the file is still worth doing — an upload that
    # is refused outright leaves the operator nowhere to put the month's
    # report, and a stored file is what a parser will be written against when
    # a sample does arrive.
    LTOA: ReportCategory(
        code=LTOA,
        label="LTOA",
        # Wider than a spreadsheet: the open-access reports these carry are
        # published as PDFs as often as workbooks.
        accepted_extensions=(".xlsx", ".xls", ".csv", ".pdf"),
        implemented=True,
        stores_file_only=True,
        period_keyed=True,
    ),
    STOA: ReportCategory(
        code=STOA,
        label="STOA",
        accepted_extensions=(".xlsx", ".xls", ".csv", ".pdf"),
        implemented=True,
        stores_file_only=True,
        period_keyed=True,
    ),
    OTHER_REPORTS: ReportCategory(
        code=OTHER_REPORTS,
        label="Other Reports",
        # Deliberately wide. This is the category for everything the exchange
        # or the desk produces that has no place of its own yet, so narrowing
        # it would only send people back to asking for a new category.
        accepted_extensions=(
            ".pdf",
            ".xlsx",
            ".xls",
            ".csv",
            ".doc",
            ".docx",
            ".txt",
        ),
        implemented=True,
        stores_file_only=True,
        # No description: the label says enough, and the hint under the
        # dropdown was repeating what the drop zone and the success panel
        # already say.
    ),
}

#: Dropdown order on the screen, matching the agreed design.
CATEGORY_ORDER: List[str] = [
    OBLIGATION_REPORT,
    BACKING_QUANTUM_SCHEDULE_COST,
    LTOA,
    STOA,
    # Last: it is the fallback, so anything with a category of its own should
    # be found before a reader gets here.
    OTHER_REPORTS,
]

#: Exchanges that publish these reports.
EXCHANGES: List[str] = ["IEX", "PXIL", "HPX"]

#: Markets a report can belong to.
MARKETS: List[str] = ["DAM", "GDAM", "RTM", "HPDAM"]

#: Matches the drop zone's stated limit. A Daily Obligation Report is about
#: 150 KB, so this is generous for the categories still to come.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def get_category(code: str) -> Optional[ReportCategory]:
    return CATEGORIES.get((code or "").strip().lower())


def describe_categories() -> List[Dict[str, object]]:
    """The dropdown, as the frontend needs it."""
    return [
        {
            "code": category.code,
            "label": category.label,
            "accepted_extensions": list(category.accepted_extensions),
            "implemented": category.implemented,
            "unavailable_reason": category.unavailable_reason,
            "description": category.description,
            "stores_file_only": category.stores_file_only,
            "period_keyed": category.period_keyed,
        }
        for category in (CATEGORIES[code] for code in CATEGORY_ORDER)
    ]


# --- Which date a list of reports is keyed on ---------------------------------
#
# A delivery date is the day the power flows; an upload date is the day the
# report was actually put in. They are rarely the same day, and the screens
# were opening on today's *delivery* date, which is almost always empty — the
# operator asking "what came in today?" saw nothing and had to hunt backwards
# for the day the report described. The lists are keyed on the upload day now.
# Delivery date is still stored, still shown as a column, and still selectable
# here for anything that genuinely asks about the flow day.

DATE_BASIS_UPLOADED = "uploaded"
DATE_BASIS_DELIVERY = "delivery"

DEFAULT_DATE_BASIS = DATE_BASIS_UPLOADED

#: Basis -> the field on the stored document that carries that date as
#: YYYY-MM-DD. Both are plain strings, so a range compares lexically.
DATE_BASIS_FIELDS: Dict[str, str] = {
    DATE_BASIS_UPLOADED: "uploaded_on_ist_date",
    DATE_BASIS_DELIVERY: "delivery_date_iso",
}


def resolve_date_field(date_basis: Optional[str]) -> str:
    """The document field a `date_basis` selects. Unknown values fall back."""
    return DATE_BASIS_FIELDS.get(
        (date_basis or DEFAULT_DATE_BASIS).strip().lower(),
        DATE_BASIS_FIELDS[DEFAULT_DATE_BASIS],
    )


def date_basis_of(date_field: str) -> str:
    """The basis name for a document field — echoed back so the caller can see
    which one actually applied when it sent something unrecognised."""
    for basis, field in DATE_BASIS_FIELDS.items():
        if field == date_field:
            return basis
    return DEFAULT_DATE_BASIS


# --- Month reports ------------------------------------------------------------
#
# LTOA and STOA cover a calendar month rather than a delivery day, so the
# uploader asks for a Year and a Month. Both are validated here rather than at
# the route, so the screen, the uploader and any later parser agree on what a
# period is.

#: Month names in the order they are numbered, so `MONTH_NAMES[0]` is January.
#: Used for the stored label and for anything that has to print a period back.
MONTH_NAMES: List[str] = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

#: The earliest year a report can be filed under. Open access in India dates
#: from well after this, so anything below it is a mistyped year rather than an
#: old report.
MIN_PERIOD_YEAR = 2000


def month_name(month: int) -> str:
    """"9" -> "September". Falls back to the number for an out-of-range month,
    so a stored label never reads as a crash."""
    if isinstance(month, int) and 1 <= month <= 12:
        return MONTH_NAMES[month - 1]
    return str(month)


def period_label(year: int, month: int) -> str:
    """The period as the operator picked it, e.g. "September 2021"."""
    return f"{month_name(month)} {year}"
