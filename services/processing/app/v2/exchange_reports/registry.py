"""
What the Report Uploader will accept, in one place.

The screen's Report Category dropdown, the file types its drop zone advertises
and the parser the backend reaches for all come from this table. Keeping them
together is the point: a category the frontend offers but the backend cannot
parse is a user who fills in the form, waits for the upload and then gets an
error, and a file type advertised in the hint text but rejected on submit is
the same failure one step later.

Three of the four categories have no parser yet because no sample file exists
for them. They are listed rather than hidden, so the screen shows the full set
PTC asked for, and each says plainly that it is not ready instead of failing
at submit with something that reads like a bug.
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


OBLIGATION_REPORT = "obligation_report"
BACKING_QUANTUM_SCHEDULE_COST = "backing_quantum_schedule_cost"
LTOA = "ltoa"
STOA = "stoa"

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
        description=(
            "The exchange's Daily Obligation Summary Report: the day's funds "
            "position, charges and fees, and the traded quantity, rate and "
            "amount for all 96 time blocks."
        ),
    ),
    BACKING_QUANTUM_SCHEDULE_COST: ReportCategory(
        code=BACKING_QUANTUM_SCHEDULE_COST,
        label="Backing Quantum/Schedule cost",
        accepted_extensions=(".xlsx", ".xls", ".csv"),
        implemented=False,
        unavailable_reason=_NO_SAMPLE,
    ),
    LTOA: ReportCategory(
        code=LTOA,
        label="LTOA",
        accepted_extensions=(".xlsx", ".xls", ".csv"),
        implemented=False,
        unavailable_reason=_NO_SAMPLE,
    ),
    STOA: ReportCategory(
        code=STOA,
        label="STOA",
        accepted_extensions=(".xlsx", ".xls", ".csv"),
        implemented=False,
        unavailable_reason=_NO_SAMPLE,
    ),
}

#: Dropdown order on the screen, matching the agreed design.
CATEGORY_ORDER: List[str] = [
    OBLIGATION_REPORT,
    BACKING_QUANTUM_SCHEDULE_COST,
    LTOA,
    STOA,
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
        }
        for category in (CATEGORIES[code] for code in CATEGORY_ORDER)
    ]
