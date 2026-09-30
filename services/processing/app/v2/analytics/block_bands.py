"""Block grouping bands.

The five named peak/off-peak bands have no definition anywhere in EPMS or in the
legacy mplive system, so PTC defines them: each band is one row in the `parameters`
collection (Masters → Parameters), its value the block ranges as text — `1-24, 89-96`.
An empty value leaves the band inactive; the pill in the dashboard is disabled and
says which parameter to fill in. `bands_from_parameters` overlays those values on
`DEFAULT_BANDS`; nothing in code needs to change when PTC settles on the numbers.
`effective_from` exists because PTC asked for block definitions to be datable.
"""
import re
from dataclasses import dataclass, replace
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

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


#: The `parameters.para_key` that defines each named band. Codes not listed here
#: (INDIVIDUAL, CUSTOM) are structural and never parametrised.
PARAMETER_KEYS: Dict[str, str] = {
    "NIGHT_OFF_PEAK": "analytics_band_night_off_peak",
    "DAY_OFF_PEAK": "analytics_band_day_off_peak",
    "DAY_PEAK": "analytics_band_day_peak",
    "EVENING_PEAK": "analytics_band_evening_peak",
    "PEAK": "analytics_band_peak",
}

#: What the Masters screen shows beside each key.
PARAMETER_DESCRIPTIONS: Dict[str, str] = {
    code: (f"Analytics dashboard — blocks in the \"{DEFAULT_BANDS[code].label}\" band, "
           f"as inclusive ranges of 1-96, e.g. 1-24, 89-96. Leave empty to disable the band.")
    for code in PARAMETER_KEYS
}

RANGES_EXAMPLE = "1-24, 89-96"

_RANGE_SPLIT = re.compile(r"[,;]\s*|\s+")
_RANGE = re.compile(r"^\s*(\d{1,2})\s*(?:(?:-|–|to)\s*(\d{1,2}))?\s*$")


def parse_ranges(text: Any) -> Tuple[Tuple[int, int], ...]:
    """`"1-24, 89-96"` → `((1, 24), (89, 96))`. `"5"` is the single block 5.

    Raises `ValueError` naming the offending piece, so the Masters screen and the
    dashboard can both show it. An empty or whitespace-only value is no ranges,
    which is a legitimate "not defined yet" rather than an error.
    """
    if text is None:
        return ()
    if isinstance(text, (list, tuple)):
        text = ", ".join(
            f"{p[0]}-{p[1]}" if isinstance(p, (list, tuple)) and len(p) == 2 else str(p)
            for p in text)
    raw = str(text).strip()
    if not raw:
        return ()
    ranges = []
    for piece in (p for p in _RANGE_SPLIT.split(raw) if p):
        m = _RANGE.match(piece)
        if not m:
            raise ValueError(
                f"Block range '{piece}' is not valid; expected e.g. {RANGES_EXAMPLE}")
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        if lo < MIN_BLOCK or hi > MAX_BLOCK or lo > hi:
            raise ValueError(
                f"Block range '{piece}' must lie within {MIN_BLOCK}-{MAX_BLOCK} "
                f"with the start before the end")
        ranges.append((lo, hi))
    return tuple(ranges)


def band_with_ranges(band: Band, ranges: Sequence[Sequence[int]]) -> Band:
    """The same band carrying these ranges; active exactly when it has any."""
    pairs = tuple((int(lo), int(hi)) for lo, hi in ranges)
    return replace(band, ranges=pairs, active=bool(pairs))


def bands_from_parameters(values: Mapping[str, Any]) -> Dict[str, Band]:
    """`DEFAULT_BANDS` with each named band's ranges read from its parameter.

    `values` maps `para_key` → stored `para_value`. A key that is missing, empty
    or unparsable leaves that band inactive — a typo in Masters must not take the
    whole dashboard down, so the parse error is swallowed here and surfaced by
    `parameter_errors` for the band list to display.
    """
    bands = dict(DEFAULT_BANDS)
    for code, key in PARAMETER_KEYS.items():
        try:
            bands[code] = band_with_ranges(DEFAULT_BANDS[code], parse_ranges(values.get(key)))
        except ValueError:
            bands[code] = band_with_ranges(DEFAULT_BANDS[code], ())
    return bands


def parameter_errors(values: Mapping[str, Any]) -> Dict[str, str]:
    """Per band code, the parse error its parameter currently has (if any)."""
    errors: Dict[str, str] = {}
    for code, key in PARAMETER_KEYS.items():
        try:
            parse_ranges(values.get(key))
        except ValueError as exc:
            errors[code] = str(exc)
    return errors


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
