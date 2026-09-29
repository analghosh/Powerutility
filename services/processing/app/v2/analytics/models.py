"""Request and response models for the analytics dashboard.

Validation happens here rather than in the routes so that the query path, the export
path and the queued export worker all reject the same things in the same way — an
export re-runs its query from the stored filters, so a filter set that was legal at
request time has to still be legal a day later when a worker picks it up.

Everything the pipeline builder can choke on is rejected here with a 422 instead:
an unknown dataset code, a block outside 1-96, a CUSTOM block band with no ranges,
a per-plant dataset with no plants chosen. `analytics:*` permission filtering is a
separate concern and lives in the service layer (Task 20), because a denied dataset
is dropped from the selection rather than failing the whole request.
"""
from datetime import date
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import block_bands, grouping, registry
from .grouping import DATE_GROUPINGS

DEFAULT_DATE_GROUPING = grouping.BLOCK_DATE_WISE
EXPORT_FORMATS = ("csv", "xlsx")

#: Sanity cap on a caller-supplied file name, before sanitising. Generous:
#: the stem that actually reaches disk is capped much lower.
MAX_FILE_NAME_INPUT = 200


def normalise_dsm_fields(fields: Any, *, empty_means_all: bool) -> List[str]:
    """The DSM figures asked for, de-duplicated and in the order the RPC prints them.

    Raises ValueError on a figure the statement does not carry. An empty choice is
    every figure for a query (so the stored export and the Filters sheet name what
    was actually shown) and stays empty for a saved default ("no preference").
    """
    chosen = {str(f).strip() for f in (fields or []) if str(f).strip()}
    unknown = sorted(chosen - set(registry.DSM_PUBLISHED_FIELDS))
    if unknown:
        raise ValueError(f"Unknown DSM field(s): {', '.join(unknown)}")
    if not chosen and empty_means_all:
        return list(registry.DSM_PUBLISHED_FIELDS)
    return [f for f in registry.DSM_PUBLISHED_FIELDS if f in chosen]


class AnalyticsQueryRequest(BaseModel):
    """One dashboard query: a date range, a block range, and what to plot in it."""

    from_date: date = Field(..., description="First trading date, inclusive")
    to_date: date = Field(..., description="Last trading date, inclusive")
    from_block: int = Field(block_bands.MIN_BLOCK, ge=block_bands.MIN_BLOCK,
                            le=block_bands.MAX_BLOCK, description="First block (1-96)")
    to_block: int = Field(block_bands.MAX_BLOCK, ge=block_bands.MIN_BLOCK,
                          le=block_bands.MAX_BLOCK, description="Last block (1-96)")
    date_grouping: str = Field(DEFAULT_DATE_GROUPING,
                               description="One of " + ", ".join(DATE_GROUPINGS))
    block_band: str = Field(block_bands.BAND_INDIVIDUAL,
                            description="Block bucket band code")
    custom_blocks: List[List[int]] = Field(
        default_factory=list,
        description="Inclusive [lo, hi] block pairs; required when block_band=CUSTOM")
    block_band_ranges: List[List[int]] = Field(
        default_factory=list,
        description=("Server-stamped: the [lo, hi] pairs the named block_band resolved "
                     "to when the request was accepted. Ignored on input — the service "
                     "always re-reads the band's parameter — and stored on an export job "
                     "so a replay reproduces the file it made."))
    datasets: List[str] = Field(..., min_length=1,
                                description="Ticked dataset codes, in display order")
    plants: List[str] = Field(default_factory=list,
                              description="Plant ids; required for a per-plant dataset")
    exchanges: List[str] = Field(default_factory=list,
                                 description="Exchange names; empty means all")
    dsm_fields: List[str] = Field(
        default_factory=list, validate_default=True,
        description=("Which DSM Published (As Per RPC) figures to show; empty means "
                     "all. Every upload stores the whole statement — this only "
                     "narrows the columns."))

    @model_validator(mode="before")
    @classmethod
    def _block_grouping_alias(cls, data: Any) -> Any:
        """Accept the UI's `block_grouping` for `block_band`.

        The filter panel, the stored job echo and the CSV header all call the
        band `block_grouping`; only this model calls it `block_band`. A body
        that sends one and not the other must not fall back to INDIVIDUAL — it
        did, silently, for every Custom or named-band request until 2026-09-17.
        """
        if isinstance(data, dict) and "block_band" not in data and data.get("block_grouping"):
            data = dict(data)
            data["block_band"] = data["block_grouping"]
        return data

    @field_validator("datasets")
    @classmethod
    def _known_datasets(cls, v: List[str]) -> List[str]:
        out: List[str] = []
        for code in v:
            if code in out:
                continue  # a double-tick is not an error, just a duplicate series
            try:
                registry.get(code)
            except registry.UnknownDatasetError:
                raise ValueError(f"Unknown dataset code: {code}") from None
            out.append(code)
        return out

    @field_validator("dsm_fields")
    @classmethod
    def _known_dsm_fields(cls, v: List[str]) -> List[str]:
        return normalise_dsm_fields(v, empty_means_all=True)

    @field_validator("date_grouping")
    @classmethod
    def _known_date_grouping(cls, v: str) -> str:
        """Canonicalise, so the retired codes keep working.

        `BLOCK_WISE` and `DATE_WISE` were merged into `BLOCK_DATE_WISE` on
        2026-09-20 — they produced identical files. Stored export jobs and saved
        user preferences still hold the old spellings and are replayed straight back
        into this model, so they are mapped forward here rather than migrated on
        disk. An unknown code is still a 422.
        """
        try:
            return grouping.canonical(v)
        except ValueError:
            raise ValueError(
                f"Unknown date_grouping {v!r}; expected one of "
                f"{', '.join(DATE_GROUPINGS)}") from None

    @field_validator("block_band")
    @classmethod
    def _known_block_band(cls, v: str) -> str:
        if v not in block_bands.DEFAULT_BANDS:
            raise ValueError(
                f"Unknown block_band {v!r}; expected one of "
                f"{', '.join(block_bands.DEFAULT_BANDS)}")
        return v

    @field_validator("block_band_ranges")
    @classmethod
    def _valid_band_ranges(cls, v: List[List[int]]) -> List[List[int]]:
        return [list(p) for p in block_bands.parse_ranges(v)]

    @field_validator("custom_blocks")
    @classmethod
    def _valid_custom_blocks(cls, v: List[List[int]]) -> List[List[int]]:
        out: List[List[int]] = []
        for pair in v:
            if len(pair) != 2:
                raise ValueError(
                    f"custom_blocks entries must be [lo, hi] pairs, got {list(pair)}")
            lo, hi = int(pair[0]), int(pair[1])
            try:
                block_bands.validate_block_range(lo, hi)
            except ValueError as exc:
                raise ValueError(f"custom_blocks range [{lo}, {hi}] is invalid: {exc}") from None
            out.append([lo, hi])
        return out

    @model_validator(mode="after")
    def _coherent(self) -> "AnalyticsQueryRequest":
        if self.from_date > self.to_date:
            raise ValueError(
                f"from_date ({self.from_date}) is after to_date ({self.to_date})")
        if self.from_block > self.to_block:
            raise ValueError(
                f"from_block ({self.from_block}) is after to_block ({self.to_block})")
        if self.block_band == block_bands.BAND_CUSTOM and not self.custom_blocks:
            raise ValueError(
                "block_band=CUSTOM requires at least one custom_blocks range")
        # Whether a named band is active is a Masters → Parameters fact, not a
        # model fact: `AnalyticsService.with_band` reads it and refuses with 422.
        # An empty `plants` list means EVERY plant (user decision 2026-09-18: the
        # per-plant datasets have no picker). It used to be refused here. The service
        # resolves it to the full list before anything is priced or stored — see
        # `AnalyticsService.with_plants` — so nothing downstream ever sees the empty
        # form, and the budget cannot mistake 34 series for one.
        return self

    def to_filters(self) -> Dict[str, Any]:
        """The filter dict the source group adapters consume.

        Deliberately excludes `datasets`: the planner buckets those by source group
        and hands each adapter only its own slice.
        """
        return {
            "from_date": self.from_date,
            "to_date": self.to_date,
            "from_block": self.from_block,
            "to_block": self.to_block,
            "date_grouping": self.date_grouping,
            "block_band": self.block_band,
            "block_band_ranges": [list(p) for p in self.block_band_ranges],
            "custom_blocks": [list(p) for p in self.custom_blocks],
            "plants": list(self.plants),
            "exchanges": list(self.exchanges),
            "dsm_fields": list(self.dsm_fields),
        }


class ExportRequest(AnalyticsQueryRequest):
    """A query plus the file it should become. Everything else validates identically."""

    format: Literal["csv", "xlsx"] = Field(
        "xlsx", description="Export file format: xlsx or csv")
    file_name: str = Field(
        "", max_length=MAX_FILE_NAME_INPUT,
        description=("What to call the file. Empty means the generated "
                     "`analytics_<from>_to_<to>` name. Sanitised before it reaches a "
                     "filesystem or a Content-Disposition header — see "
                     "`analytics_csv.safe_file_stem` — so this bound is only a sanity "
                     "cap on the raw input, not the safety check."))

    @field_validator("file_name")
    @classmethod
    def _trimmed_file_name(cls, v: str) -> str:
        return (v or "").strip()


class RenameExportRequest(BaseModel):
    """A new name for a finished export. Empty means the generated name."""

    model_config = ConfigDict(extra="forbid")

    file_name: str = Field("", max_length=MAX_FILE_NAME_INPUT,
                           description="Stem only; the format decides the extension")

    @field_validator("file_name")
    @classmethod
    def _trimmed(cls, v: str) -> str:
        return (v or "").strip()


class PreferenceRequest(BaseModel):
    """The filter defaults one user wants this screen to open with.

    `extra="forbid"` because the document is replayed straight back into the filter
    panel: an unvalidated blob replayed into a form is how a preference turns into a
    payload. The dataset codes themselves are checked against the registry in
    `analytics_preference_repository.validate`, which is also what the caps live in.
    """

    model_config = ConfigDict(extra="forbid")

    datasets: List[str] = Field(default_factory=list,
                                description="Dataset codes to tick on load")
    plants: List[str] = Field(default_factory=list,
                              description="Plant ids to preselect for by-plant datasets")
    dsm_fields: List[str] = Field(
        default_factory=list,
        description="DSM Published figures to preselect; empty means all")
    format: Literal["csv", "xlsx"] = Field(
        "xlsx", description="File format the Generate button opens on")


class AnalyticsAxes(BaseModel):
    dates: List[str] = Field(default_factory=list)
    blocks: List[Union[int, str]] = Field(
        default_factory=list,
        description="Block numbers for INDIVIDUAL, bucket labels for a band")


class AnalyticsSeries(BaseModel):
    dataset: str
    label: str
    unit: str
    entity: Optional[str] = None
    values: List[List[Optional[float]]] = Field(
        default_factory=list,
        description="Indexed [block][date]; a missing value is null, never 0")
    quality: str = Field("complete", description="complete | partial | empty | failed")


class AnalyticsMeta(BaseModel):
    # Extras are allowed so the service layer can attach the budget band, the cache
    # state and anything else diagnostic without this model having to know about it.
    model_config = ConfigDict(extra="allow")

    cells: int = 0
    truncated: bool = False
    missing_datasets: List[str] = Field(default_factory=list)
    failed_datasets: List[str] = Field(default_factory=list)
    generated_at: Optional[str] = None


class AnalyticsQueryResponse(BaseModel):
    axes: AnalyticsAxes
    series: List[AnalyticsSeries] = Field(default_factory=list)
    meta: AnalyticsMeta = Field(default_factory=AnalyticsMeta)
