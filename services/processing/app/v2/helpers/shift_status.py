"""
Shift Status Report — the rules, with no I/O.

PTC's shift desk (EDP) files one sheet per shift: for every block of the shift, the
day-ahead and real-time forecasts, the SRLDC net schedule, the actual drawal, the
deviation between those two, what was bid and traded on the exchange, and a remark
wherever the deviation passed 15 MW. This module holds the definitions of those
figures; the service only gathers the inputs.

Shift windows are the ones in Masters > Parameters and are read with the same parser
the masters screen validates them with, so a timing that saves there works here.
"""

from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..services.parameter_service import MINUTES_PER_DAY, parse_shift_timing

BLOCKS_PER_DAY = 96
BLOCK_MINUTES = 15

#: The whole delivery day, 00:00 up to 24:00 — the picker's "Full day".
FULL_DAY_WINDOW: Tuple[int, int] = (0, MINUTES_PER_DAY)

#: Plants carried in supply_serve that SRLDC does not schedule. PPCLGPP is
#: Puducherry's own gas plant, scheduled by the state; the WBES feed has no row for
#: it, so the SRLDC net schedule is the supply total without it (verified against
#: the shift sheet of 28 Sep 2026: 28 of 34 blocks within 0.5 MW).
NON_SRLDC_PLANTS: Tuple[str, ...] = ("PPCLGPP",)

#: The shift sheet asks for a reason when the deviation is beyond this, either way.
REMARK_THRESHOLD_MW = 15.0

#: Widest range one request may cover: a month of full days is ~3,000 rows.
MAX_RANGE_DAYS = 31

#: The markets whose sum is I-DAM, and the one the other two are checked against.
IDAM_MARKETS: Tuple[str, ...] = ("DAM", "GDAM", "HPDAM")
DAM = "DAM"


def shift_window(timing: str) -> Tuple[int, int]:
    """`(start, end)` in minutes past midnight for a Masters > Parameters timing."""
    start, end, _stored = parse_shift_timing(timing)
    return start, end


def shift_blocks(start: int, end: int) -> List[Tuple[int, int]]:
    """The `(day_offset, block)` pairs a shift starting on day 0 covers, in time order.

    A block belongs to the shift when the minute it starts at falls in the half-open
    window `[start, end)` — the rule the masters screen states for its timings. A
    window whose end is not after its start runs past midnight into day 1; equal ends
    mean 24 hours from the start.
    """
    span_end = end if end > start else end + MINUTES_PER_DAY
    pairs = []
    for day_offset in (0, 1):
        for block in range(1, BLOCKS_PER_DAY + 1):
            minute = day_offset * MINUTES_PER_DAY + (block - 1) * BLOCK_MINUTES
            if start <= minute < span_end:
                pairs.append((day_offset, block))
    return pairs


def section_blocks(section_date: date, start: int, end: int) -> List[Tuple[date, int]]:
    """The `(delivery date, block)` rows of the shift that starts on `section_date`."""
    return [
        (section_date + timedelta(days=offset), block)
        for offset, block in shift_blocks(start, end)
    ]


def section_dates(from_date: date, to_date: date) -> List[date]:
    """Every date in the inclusive range, refusing a reversed or over-long one."""
    if from_date > to_date:
        raise ValueError("from_date must be on or before to_date")
    days = (to_date - from_date).days + 1
    if days > MAX_RANGE_DAYS:
        raise ValueError(f"Date range cannot exceed {MAX_RANGE_DAYS} days")
    return [from_date + timedelta(days=i) for i in range(days)]


def block_times(block: int) -> Tuple[str, str]:
    """`("15:30", "15:45")` for block 63; block 96 ends at `00:00`, as on the sheet."""
    start = (block - 1) * BLOCK_MINUTES
    end = (start + BLOCK_MINUTES) % MINUTES_PER_DAY

    def clock(minutes: int) -> str:
        return f"{minutes // 60:02d}:{minutes % 60:02d}"

    return clock(start), clock(end)


def net_schedule(final_schedule: Optional[float],
                 non_srldc: Optional[float]) -> Optional[float]:
    """The SRLDC net schedule: the supply total less the plants SRLDC does not schedule."""
    if final_schedule is None:
        return None
    return final_schedule - (non_srldc or 0.0)


def drawal_value(stored: Optional[float]) -> Optional[float]:
    """The drawal to show, or None.

    Drawal is entered by operators (SCADA Data Input) rather than fed, and a block
    nobody entered is stored as 0 or not at all. Neither is a reading: treating it
    as one turns the deviation into minus the whole schedule.
    """
    if stored is None or stored == 0:
        return None
    return float(stored)


def deviation(drawal: Optional[float], net: Optional[float]) -> Optional[float]:
    """Under Drawl (-) / Over Drawl (+): drawal minus net schedule, blank if either is."""
    if drawal is None or net is None:
        return None
    return drawal - net


def needs_remark(dev: Optional[float], remark: Optional[str]) -> bool:
    """True when the deviation is beyond 15 MW either way and no reason is written."""
    if dev is None or abs(dev) <= REMARK_THRESHOLD_MW:
        return False
    return not (remark or "").strip()


def duplicate_markets(series: Dict[str, Dict[int, Tuple[Optional[float], Optional[float]]]]
                      ) -> Set[str]:
    """GDAM/HPDAM markets whose whole day repeats DAM's, block for block.

    The test site holds one such day (3 Aug 2026): GDAM and HPDAM rows equal to the
    DAM bid and traded volumes in all 71 blocks, while the SRLDC schedule shows no
    green or high-price trade at all. Summed, I-DAM would count that volume three
    times, so a market that exactly mirrors DAM for the day is treated as a copy.
    """
    dam = series.get(DAM) or {}
    if not dam:
        return set()
    return {
        market for market in IDAM_MARKETS
        if market != DAM and series.get(market) and series[market] == dam
    }


def idam_total(values: Iterable[Optional[float]]) -> Optional[float]:
    """Sum of the day-ahead markets' volumes; blank only when every market is blank."""
    present = [v for v in values if v is not None]
    if not present:
        return None
    return float(sum(present))


def round_mw(value: Optional[float]) -> Optional[float]:
    """Two decimals, the precision the exchange and SRLDC publish in."""
    return None if value is None else round(float(value), 2)


def section_person(remarks: Sequence[dict]) -> Optional[str]:
    """Who last saved a remark in the section — the sheet's Shift Person Name."""
    dated = [r for r in remarks if r.get("updated_at") and r.get("updated_by_name")]
    if not dated:
        return None
    return max(dated, key=lambda r: r["updated_at"])["updated_by_name"]
