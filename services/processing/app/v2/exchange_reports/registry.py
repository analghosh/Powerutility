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
is set, by calendar month. A third shape exists for parsed categories whose
file carries its own dates: `dates_from_file` means the operator is asked for
neither, because the answer is inside the workbook.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

#: Where the sample files the uploader offers for download live. They ship
#: with the code rather than in the database, because a sample is part of the
#: category's definition: it has to match the parser in the same release.
SAMPLES_DIR = Path(__file__).resolve().parent / "samples"


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
    #: True when the dates the report covers are printed inside the file
    #: itself, so the uploader asks for no date at all beyond the Uploaded
    #: Date. A DSM workbook carries a fortnight of settlement days in one
    #: sheet; asking for a single Delivery Date would record one of them as
    #: though it were the whole file. Exchange and market are dropped for the
    #: same reason they are dropped from a store-only category: a deviation
    #: settlement is against the grid, not against an exchange.
    dates_from_file: bool = False
    #: True when the one delivery day the report covers is printed in the
    #: file, so the uploader does not ask for it. An IEX Daily Obligation
    #: Report names its own Delivery Date; asking the operator to type it
    #: again only gave them a way to get it wrong. Unlike `dates_from_file`,
    #: exchange and market are still asked for.
    delivery_date_from_file: bool = False
    #: True when the report covers a calendar month rather than a delivery
    #: day. The uploader asks for Year and Month instead of a Delivery Date,
    #: and the file is filed under that month. A monthly report has no single
    #: day the power flowed on, so forcing one would record an answer nobody
    #: meant — the same reasoning that keeps exchange and market off a
    #: store-only category.
    period_keyed: bool = False
    #: File name, inside SAMPLES_DIR, of a file the operator can download to
    #: see what this category expects. Only parsed categories carry one: a
    #: store-only category accepts anything, so there is no layout to show.
    sample_file: Optional[str] = None

    @property
    def sample_path(self) -> Optional[Path]:
        return SAMPLES_DIR / self.sample_file if self.sample_file else None


OBLIGATION_REPORT = "obligation_report"
BACKING_QUANTUM_SCHEDULE_COST = "backing_quantum_schedule_cost"
LTOA = "ltoa"
STOA = "stoa"
DSM_REPORT = "dsm_report"
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
        delivery_date_from_file=True,
        # A real IEX Daily Obligation Report, under the name the exchange
        # gives it, so the sample also shows the file-name convention.
        sample_file="IEX180926DOR_PTC0136_PY0_Electricity Department Pud.pdf",
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
    # The one parsed category that is not filed under a date the operator
    # types. A DSM workbook is a block-by-block settlement statement covering
    # however many days the period ran to, and every one of those days is
    # named in its own Date column.
    DSM_REPORT: ReportCategory(
        code=DSM_REPORT,
        label="DSM Report",
        # No .xls: reading the old binary format needs xlrd, which is not in
        # this service's dependency set. Advertising it here would mean an
        # operator picking a file the parser then cannot open.
        accepted_extensions=(".xlsx", ".csv"),
        implemented=True,
        dates_from_file=True,
        # The nine headers the parser looks for and two rows of made-up
        # figures, to be overwritten with the real statement.
        sample_file="DSM Report Sample.xlsx",
        description=(
            "Block-wise deviation settlement. The days it covers are read "
            "out of the file's own Date column."
        ),
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
    DSM_REPORT,
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
            "dates_from_file": category.dates_from_file,
            "delivery_date_from_file": category.delivery_date_from_file,
            "sample_filename": category.sample_file,
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
