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

from . import block_bands, registry
from .grouping import DATE_GROUPINGS

DEFAULT_DATE_GROUPING = "BLOCK_WISE"
EXPORT_FORMATS = ("csv", "xlsx")


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
    datasets: List[str] = Field(..., min_length=1,
                                description="Ticked dataset codes, in display order")
    plants: List[str] = Field(default_factory=list,
                              description="Plant ids; required for a per-plant dataset")
    exchanges: List[str] = Field(default_factory=list,
                                 description="Exchange names; empty means all")

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

    @field_validator("date_grouping")
    @classmethod
    def _known_date_grouping(cls, v: str) -> str:
        if v not in DATE_GROUPINGS:
            raise ValueError(
                f"Unknown date_grouping {v!r}; expected one of {', '.join(DATE_GROUPINGS)}")
        return v

    @field_validator("block_band")
    @classmethod
    def _known_block_band(cls, v: str) -> str:
        if v not in block_bands.DEFAULT_BANDS:
            raise ValueError(
                f"Unknown block_band {v!r}; expected one of "
                f"{', '.join(block_bands.DEFAULT_BANDS)}")
        return v

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
        band = block_bands.DEFAULT_BANDS[self.block_band]
        if not band.active:
            raise ValueError(
                f"block_band {self.block_band} is inactive — PTC has not supplied its "
                f"block ranges yet")
        if not self.plants:
            per_plant = [c for c in self.datasets
                         if registry.get(c).series == "per_plant"]
            if per_plant:
                raise ValueError(
                    f"plants must be selected for per-plant dataset(s): "
                    f"{', '.join(per_plant)}")
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
            "custom_blocks": [list(p) for p in self.custom_blocks],
            "plants": list(self.plants),
            "exchanges": list(self.exchanges),
        }


class ExportRequest(AnalyticsQueryRequest):
    """A query plus the file it should become. Everything else validates identically."""

    format: Literal["csv", "xlsx"] = Field(
        "csv", description="Export file format: csv or xlsx")


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
