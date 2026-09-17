"""Block grouping bands.

The five named peak/off-peak bands have no definition anywhere in EPMS or in the
legacy mplive system. They are seeded inactive with empty ranges; supplying ranges
and flipping `active` is a data change, not a code change. `effective_from` exists
because PTC asked for block definitions to be datable rather than permanent.
"""
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

BAND_INDIVIDUAL = "INDIVIDUAL"
BAND_CUSTOM = "CUSTOM"

MIN_BLOCK = 1
MAX_BLOCK = 96


@dataclass(frozen=True)
class Band:
    code: str
    label: str
    ranges: Tuple[Tuple[int, int], ...] = ()
    active: bool = False
    effective_from: Optional[str] = None


DEFAULT_BANDS: Dict[str, Band] = {
    BAND_INDIVIDUAL: Band(BAND_INDIVIDUAL, "Individual Blocks", (), True, None),
    BAND_CUSTOM: Band(BAND_CUSTOM, "Custom", (), True, None),
    "NIGHT_OFF_PEAK": Band("NIGHT_OFF_PEAK", "Night Off-Peak", (), False, None),
    "DAY_OFF_PEAK": Band("DAY_OFF_PEAK", "Day Off-Peak", (), False, None),
    "DAY_PEAK": Band("DAY_PEAK", "Day Peak", (), False, None),
    "EVENING_PEAK": Band("EVENING_PEAK", "Evening Peak", (), False, None),
    "PEAK": Band("PEAK", "Peak", (), False, None),
}


def validate_block_range(from_block: int, to_block: int) -> None:
    for name, v in (("from_block", from_block), ("to_block", to_block)):
        if not isinstance(v, int) or v < MIN_BLOCK or v > MAX_BLOCK:
            raise ValueError(f"{name} must be between {MIN_BLOCK} and {MAX_BLOCK}, got {v}")
    if from_block > to_block:
        raise ValueError(f"from_block ({from_block}) cannot be after to_block ({to_block})")


def bucket_for(block: int, band: Band) -> Optional[str]:
    """Bucket label for a block, or None when the block falls outside the band."""
    if band.code == BAND_INDIVIDUAL:
        return str(block)
    for lo, hi in band.ranges:
        if lo <= block <= hi:
            return band.code
    return None


def bucket_count(band: Band, from_block: int, to_block: int,
                 custom: Optional[Sequence[Sequence[int]]] = None) -> int:
    """How many columns/rows this band produces — feeds the cell budget."""
    if band.code == BAND_INDIVIDUAL:
        return to_block - from_block + 1
    if band.code == BAND_CUSTOM:
        return max(1, len(custom or []))
    return 1
