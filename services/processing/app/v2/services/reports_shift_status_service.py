"""
Shift Status Report — PTC's shift sheet, built from what EPMS already stores.

The shift desk (EDP) files one sheet per shift: every block's forecasts, the SRLDC
net schedule, the actual drawal and the deviation between them, what was bid and
traded, and a reason wherever the deviation passed 15 MW. Everything except the
reason is already in EPMS, so this service gathers it and the operator only types
the remarks.

Where each column comes from (verified block by block against EDP's sheet for
28 Sep 2026 on the test site):

* Day Ahead Forecast, Net Schedule, Actual Drawal, I-DAM and RTM — the Reports
  Dashboard's own datasets, run through the same engine (`AnalyticsService.fetch`),
  so this sheet and the dashboard can never disagree about a number.
* Real Time Expected Forecast — the latest real-time forecast revision in
  forecast_serve (PTC's manual upload when there is one, else the forecast API).
  It tracks the sheet far better than the frozen intraday figure the dashboard
  plots (26 of 34 blocks within 10 MW against 2); the intraday figure is the
  fallback when no revision is stored.
* Remarks — `shift_status_remarks`, typed on the screen.

The definitions (what a shift covers, net schedule, deviation, when a remark is
due) live in `helpers/shift_status.py`.
"""

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from ..analytics.models import AnalyticsQueryRequest
from ..helpers import shift_status as rules

logger = logging.getLogger(__name__)

FULL_DAY_SHIFT_ID = "full_day"
FULL_DAY_SHIFT = {
    "id": FULL_DAY_SHIFT_ID,
    "shift_name": "Full day",
    "shift_timing": "00:00-24:00",
}

#: Dashboard datasets read in one pass, all on the same (date, block) grid.
FORECAST_DAF = "FORECAST_DAF"
FORECAST_IDF = "FORECAST_IDF"
SG_TOTAL = "SG_TOTAL"
SG_PLANT = "SG_PLANT"
DRAWAL = "DRAWAL"
MARKETS = rules.IDAM_MARKETS + ("RTM",)
DATASETS: Tuple[str, ...] = (FORECAST_DAF, FORECAST_IDF, SG_TOTAL, DRAWAL) + tuple(
    f"PF_{kind}_{market}" for market in MARKETS for kind in ("BIDDED", "TRADED"))

#: forecast_serve sources, most authoritative first: PTC's own upload beats the API.
FORECAST_SOURCES = (("csv", "Manual"), ("rtm_forecast_api", "Auto"))

#: One save covers at most one section, and a section is at most 96 blocks.
MAX_REMARKS_PER_SAVE = 96

#: Remarks may be written for blocks of tomorrow, today and the two days before,
#: in IST — the shift desk's clock (PTC: today and the two days before on
#: 2026-09-29, tomorrow added on 2026-09-30). Offsets are days after today.
REMARK_DAY_OFFSETS = (1, 0, -1, -2)
REMARK_WINDOW_TEXT = "tomorrow, today, yesterday and the day before"
IST = ZoneInfo("Asia/Kolkata")

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

Grid = Dict[Tuple[str, int], Optional[float]]


class ShiftNotFound(ValueError):
    """The shift id is not in Masters > Parameters."""


class RemarkLocked(ValueError):
    """A block already has a remark, and a submitted remark is final."""

    def __init__(self, blocks: List[dict]):
        self.blocks = blocks
        listed = ", ".join(f"{b['date']} block {b['time_block']}" for b in blocks[:10])
        more = f" and {len(blocks) - 10} more" if len(blocks) > 10 else ""
        super().__init__(
            f"Already submitted and cannot be changed: {listed}{more}")


def _ist_today() -> date:
    return datetime.now(IST).date()


def editable_dates(today: date) -> Set[str]:
    """The delivery dates whose blocks may still get a remark."""
    return {(today + timedelta(days=offset)).isoformat() for offset in REMARK_DAY_OFFSETS}


def _parse_date(value: str, field: str) -> date:
    if not isinstance(value, str) or not _ISO_DATE.match(value):
        raise ValueError(f"{field} must be YYYY-MM-DD")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{field} is not a real date") from exc


def _series_grids(response: dict) -> Dict[Tuple[str, Optional[str]], Grid]:
    """`{(dataset, entity): {(iso_date, block): value}}` from a pivot response.

    The pivot lays values out as `values[block_index][date_index]`.
    """
    axes = response.get("axes") or {}
    dates = list(axes.get("dates") or [])
    blocks = list(axes.get("blocks") or [])
    grids: Dict[Tuple[str, Optional[str]], Grid] = {}
    for series in response.get("series") or []:
        grid: Grid = {}
        values = series.get("values") or []
        for bi, block in enumerate(blocks):
            row = values[bi] if bi < len(values) else []
            for di, day in enumerate(dates):
                value = row[di] if di < len(row) else None
                if value is not None:
                    grid[(str(day)[:10], int(block))] = float(value)
        grids[(series.get("dataset"), series.get("entity"))] = grid
    return grids


def _utc_iso(value: Any) -> Optional[str]:
    """ISO text with its zone. Motor hands back naive datetimes that are UTC."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _forecast_grid(docs: List[dict]) -> Dict[Tuple[str, int], Tuple[float, str]]:
    """Latest real-time forecast per block, preferring PTC's manual upload."""
    rank = {source: i for i, (source, _label) in enumerate(FORECAST_SOURCES)}
    label = dict(FORECAST_SOURCES)
    best: Dict[Tuple[str, int], Tuple[int, float, str]] = {}
    for doc in docs:
        source = doc.get("source")
        value = doc.get("forecast")
        if source not in rank or value is None or doc.get("time_block") is None:
            continue
        at = doc.get("scheduled_at")
        day = at.strftime("%Y-%m-%d") if hasattr(at, "strftime") else str(at)[:10]
        key = (day, int(doc["time_block"]))
        if key not in best or rank[source] < best[key][0]:
            best[key] = (rank[source], float(value), label[source])
    return {key: (value, source) for key, (_r, value, source) in best.items()}


class ReportsShiftStatusService:

    def __init__(self, analytics: Any = None, forecast_repository: Any = None,
                 remarks_repository: Any = None,
                 shifts_loader: Optional[Callable[[], Any]] = None,
                 today: Optional[Callable[[], date]] = None):
        self._today = today or _ist_today
        self._analytics = analytics
        self._forecast_repository = forecast_repository
        self._remarks_repository = remarks_repository
        self._shifts_loader = shifts_loader

    # ------------------------------------------------------------- plumbing
    # Lazy, like every v2 repository: a Motor client built at import binds to the
    # wrong event loop (see localpatch/sitecustomize.py and the analytics service).

    @property
    def analytics(self):
        if self._analytics is None:
            from .analytics_service import AnalyticsService
            self._analytics = AnalyticsService()
        return self._analytics

    @property
    def forecast_repository(self):
        if self._forecast_repository is None:
            from ..repository.forecast_serve_repository import forecast_serve_repository
            self._forecast_repository = forecast_serve_repository
        return self._forecast_repository

    @property
    def remarks_repository(self):
        if self._remarks_repository is None:
            from ..repository.shift_status_remarks_repository import (
                shift_status_remarks_repository)
            self._remarks_repository = shift_status_remarks_repository
        return self._remarks_repository

    async def _load_shifts(self) -> List[dict]:
        if self._shifts_loader is None:
            from .parameter_service import parameter_service
            return await parameter_service.get_shifts()
        return await self._shifts_loader()

    # --------------------------------------------------------------- shifts

    async def shifts(self) -> List[dict]:
        """The picker: every configured shift that parses, then Full day."""
        options = []
        for shift in await self._load_shifts():
            try:
                rules.shift_window(shift.get("shift_timing"))
            except ValueError:
                logger.warning("Shift %s has an unreadable timing %r; left out",
                               shift.get("id"), shift.get("shift_timing"))
                continue
            options.append({key: shift.get(key) for key in FULL_DAY_SHIFT})
        options.append(dict(FULL_DAY_SHIFT))
        return options

    async def resolve_shift(self, shift_id: Optional[str]) -> Tuple[dict, List[dict]]:
        options = await self.shifts()
        wanted = shift_id or FULL_DAY_SHIFT_ID
        for option in options:
            if option["id"] == wanted:
                return option, options
        raise ShiftNotFound(f"Unknown shift: {wanted}")

    # --------------------------------------------------------------- report

    async def get_report(self, from_date: str, to_date: str,
                         shift_id: Optional[str] = None) -> dict:
        start_day = _parse_date(from_date, "from_date")
        end_day = _parse_date(to_date, "to_date")
        days = rules.section_dates(start_day, end_day)
        shift, options = await self.resolve_shift(shift_id)
        start, end = (rules.FULL_DAY_WINDOW if shift["id"] == FULL_DAY_SHIFT_ID
                      else rules.shift_window(shift["shift_timing"]))

        layout = [(day, rules.section_blocks(day, start, end)) for day in days]
        row_dates = sorted({d for _day, rows in layout for d, _b in rows})
        span_from, span_to = row_dates[0].isoformat(), row_dates[-1].isoformat()

        main, plants, forecast_docs, remarks = await asyncio.gather(
            self.analytics.fetch(AnalyticsQueryRequest(
                from_date=span_from, to_date=span_to, datasets=list(DATASETS))),
            self.analytics.fetch(AnalyticsQueryRequest(
                from_date=span_from, to_date=span_to, datasets=[SG_PLANT],
                plants=list(rules.NON_SRLDC_PLANTS))),
            self.forecast_repository.find_by_date_range_and_block_range(
                span_from, span_to, 1, rules.BLOCKS_PER_DAY, "IDF"),
            self.remarks_repository.find_for_dates(row_dates),
        )
        grids = _series_grids(main)
        plant_grids = _series_grids(plants)
        non_srldc: Grid = {}
        for plant in rules.NON_SRLDC_PLANTS:
            for key, value in plant_grids.get((SG_PLANT, plant), {}).items():
                non_srldc[key] = non_srldc.get(key, 0.0) + value
        realtime = _forecast_grid(forecast_docs or [])

        warnings = []
        failed = (main.get("meta") or {}).get("failed_datasets") or []
        failed += (plants.get("meta") or {}).get("failed_datasets") or []
        if failed:
            warnings.append(
                "Some figures could not be read and are blank: " + ", ".join(failed))

        skipped = self._duplicate_markets(grids, row_dates)
        for iso, markets in sorted(skipped.items()):
            warnings.append(
                f"{', '.join(sorted(markets))} on {iso} repeat DAM block for block "
                "and are left out of I-DAM.")

        writable = editable_dates(self._today())
        sections = []
        for day, rows in layout:
            blocks = [self._row(d.isoformat(), b, grids, non_srldc, realtime,
                                remarks, skipped, writable) for d, b in rows]
            section_remarks = [remarks[(d.isoformat(), b)] for d, b in rows
                               if (d.isoformat(), b) in remarks]
            sections.append({
                "date": day.isoformat(),
                "shift_person": rules.section_person(section_remarks),
                "block_count": len(blocks),
                "drawal_block_count": sum(r["actual_drawal"] is not None for r in blocks),
                "over_threshold_count": sum(
                    r["deviation"] is not None
                    and abs(r["deviation"]) > rules.REMARK_THRESHOLD_MW for r in blocks),
                "needs_remark_count": sum(r["needs_remark"] for r in blocks),
                "remark_count": len(section_remarks),
                "editable_count": sum(r["remark_editable"] for r in blocks),
                "blocks": blocks,
            })

        return {
            "query": {"from_date": from_date, "to_date": to_date, "shift_id": shift["id"]},
            "shift": {**shift, "start_minute": start, "end_minute": end},
            "shifts": options,
            "remark_threshold_mw": rules.REMARK_THRESHOLD_MW,
            "remark_dates": sorted(writable),
            "sections": sections,
            "warnings": warnings,
        }

    @staticmethod
    def _duplicate_markets(grids, row_dates) -> Dict[str, set]:
        """Per date, the GDAM/HPDAM markets that only repeat DAM (see the rule)."""
        out: Dict[str, set] = {}
        for d in row_dates:
            iso = d.isoformat()
            series = {}
            for market in rules.IDAM_MARKETS:
                bid = grids.get((f"PF_BIDDED_{market}", None), {})
                traded = grids.get((f"PF_TRADED_{market}", None), {})
                series[market] = {
                    block: (bid.get((iso, block)), traded.get((iso, block)))
                    for block in range(1, rules.BLOCKS_PER_DAY + 1)
                    if (iso, block) in bid or (iso, block) in traded
                }
            dupes = rules.duplicate_markets(series)
            if dupes:
                out[iso] = dupes
        return out

    @staticmethod
    def _row(iso: str, block: int, grids, non_srldc, realtime, remarks, skipped,
             writable: Set[str]) -> dict:
        def value(code: str) -> Optional[float]:
            return grids.get((code, None), {}).get((iso, block))

        def market_sum(kind: str) -> Optional[float]:
            markets = [m for m in rules.IDAM_MARKETS if m not in skipped.get(iso, ())]
            return rules.idam_total(value(f"PF_{kind}_{m}") for m in markets)

        if (iso, block) in realtime:
            rt_value, rt_source = realtime[(iso, block)]
        else:
            rt_value = value(FORECAST_IDF)
            rt_source = "Intra Day" if rt_value is not None else None

        net = rules.net_schedule(value(SG_TOTAL), non_srldc.get((iso, block)))
        drawal = rules.drawal_value(value(DRAWAL))
        dev = rules.deviation(drawal, net)
        remark_doc = remarks.get((iso, block)) or {}
        remark = remark_doc.get("remark") or ""
        time_from, time_to = rules.block_times(block)
        return {
            "date": iso,
            "time_block": block,
            "time_from": time_from,
            "time_to": time_to,
            "day_ahead_forecast": rules.round_mw(value(FORECAST_DAF)),
            "real_time_forecast": rules.round_mw(rt_value),
            "real_time_forecast_source": rt_source,
            "net_schedule": rules.round_mw(net),
            "actual_drawal": rules.round_mw(drawal),
            "deviation": rules.round_mw(dev),
            "idam_bid": rules.round_mw(market_sum("BIDDED")),
            "idam_traded": rules.round_mw(market_sum("TRADED")),
            "rtm_bid": rules.round_mw(value("PF_BIDDED_RTM")),
            "rtm_traded": rules.round_mw(value("PF_TRADED_RTM")),
            "remark": remark,
            "remark_updated_by": remark_doc.get("updated_by_name"),
            "remark_updated_at": _utc_iso(remark_doc.get("updated_at")),
            "needs_remark": rules.needs_remark(dev, remark),
            # A block takes a remark once, and only while its date is in the window.
            "remark_editable": not remark_doc and iso in writable,
        }

    # -------------------------------------------------------------- remarks

    @staticmethod
    def validate_remarks(entries: Any, writable: Set[str]) -> List[dict]:
        """Refuse anything the screen could not have sent."""
        from ..repository.shift_status_remarks_repository import MAX_REMARK_LENGTH

        if not isinstance(entries, list) or not entries:
            raise ValueError("remarks must be a non-empty list")
        if len(entries) > MAX_REMARKS_PER_SAVE:
            raise ValueError(f"At most {MAX_REMARKS_PER_SAVE} remarks per save")
        clean, seen = [], set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("each remark must be an object")
            day = _parse_date(entry.get("date"), "date").isoformat()
            block = entry.get("time_block")
            if isinstance(block, bool) or not isinstance(block, int) \
                    or not 1 <= block <= rules.BLOCKS_PER_DAY:
                raise ValueError("time_block must be a whole number from 1 to 96")
            remark = entry.get("remark")
            if remark is None:
                remark = ""
            if not isinstance(remark, str):
                raise ValueError("remark must be text")
            if not remark.strip():
                raise ValueError(f"Block {block} of {day}: a remark cannot be empty")
            if day not in writable:
                raise ValueError(
                    f"Block {block} of {day}: remarks can only be entered for "
                    f"{REMARK_WINDOW_TEXT}")
            if len(remark.strip()) > MAX_REMARK_LENGTH:
                raise ValueError(f"A remark can be at most {MAX_REMARK_LENGTH} characters")
            if (day, block) in seen:
                raise ValueError(f"Block {block} of {day} appears twice")
            seen.add((day, block))
            clean.append({"date": day, "time_block": block, "remark": remark.strip()})
        return clean

    async def save_remarks(self, entries: Any, *, user_id: str, username: str) -> dict:
        """Submit remarks. Each block takes one, and it is final.

        Nothing is written when any block already has a remark: the caller learns
        which, rather than having half the batch saved behind its back.
        """
        clean = self.validate_remarks(entries, editable_dates(self._today()))
        existing = await self.remarks_repository.find_for_dates(
            {_parse_date(e["date"], "date") for e in clean})
        taken = [{"date": e["date"], "time_block": e["time_block"]}
                 for e in clean if (e["date"], e["time_block"]) in existing]
        if taken:
            raise RemarkLocked(taken)
        result = await self.remarks_repository.insert_new(
            clean, user_id=user_id, username=username)
        if result["locked"]:
            # Another operator saved one of these blocks in the last instant.
            raise RemarkLocked(result["locked"])
        return {"saved": result["saved"], "blocks": len(clean)}


reports_shift_status_service = ReportsShiftStatusService()
