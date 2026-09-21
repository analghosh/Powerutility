"""
Reports PLF/PAF service — plant load factor and plant availability factor per plant.

Ported from mplive ``ReportsController@getplfreport``:

    PLF = avg(SG) / avg(DC) * 100      (0 when avg DC is 0)
    PAF = avg(DC) / capacity * 100

Three deliberate deviations from the legacy implementation, all noted in the
handover:

1.  **Capacity source.** mplive divided by ``TIE_Price`` — the tie-line capacity
    MPPMCL maintained per plant. EPMS has no tie-line column; the equivalent
    per-plant number is ``plant_master.ppa``, PTC's contracted share in MW, which
    is the meaningful denominator for a trader. Where that is missing or zero we
    return ``null`` rather than mplive's ``tie_capacity = 1`` fallback, which
    silently turned PAF into "avg DC x 100".

2.  **Plant matching.** mplive walked the DC result set and read SG by the same
    positional index, so the two independently-grouped aggregations only lined up
    while Mongo happened to return plants in the same order. Here DC and SG are
    joined on ``plant_id``.

3.  **SG-only plants are listed.** Because mplive iterated the DC result set, any
    plant with a schedule but no declared capacity vanished from the report —
    several renewables. They are listed here with a null DC, PLF and PAF, so the
    schedule is visible without implying a 0% load factor. Open-access blocks,
    which that change would also have surfaced, are excluded instead: see
    ``EXCLUDED_TECHNOLOGIES``.
"""

import csv
import io
import logging
from typing import Any, Dict, List, Optional, Tuple

from libs.helpers.historical_date_split import validate_max_date_range_days
from libs.helpers.reports_forecast_format import (
    REPORTS_FORECAST_FORMAT_JSON,
    normalize_reports_forecast_format,
    reports_forecast_includes_csv_content,
)

from ..repository.reports_plf_paf_repository import (
    DC_TYPE,
    SG_TYPE,
    reports_plf_paf_repository,
)

logger = logging.getLogger(__name__)

CSV_HEADERS = [
    "Plant",
    "Technology",
    "DC (MW)",
    "SG (MW)",
    "PLF (%)",
    "PAF (%)",
    "Contracted Capacity (MW)",
]

_ROUND_DP = 2

# OA_PX / OA_GNA / OA_TGNA are open-access transaction blocks, not generating
# units: they carry a schedule but never a declared capacity, and a load factor
# for them means nothing. mplive dropped them as a side effect of iterating DC
# rows only; deviation 3 above would otherwise put them back.
EXCLUDED_TECHNOLOGIES = {"open access"}


class ReportsPlfPafService:
    def __init__(self):
        self.repository = reports_plf_paf_repository

    @staticmethod
    def _resolve_date_range(
        schedule_date: Optional[str],
        from_date: Optional[str],
        to_date: Optional[str],
    ) -> Tuple[str, str]:
        if schedule_date and (from_date or to_date):
            raise ValueError(
                "Provide either schedule_date or both from_date and to_date, not both"
            )
        if schedule_date:
            return schedule_date, schedule_date
        if from_date and to_date:
            if from_date > to_date:
                raise ValueError("from_date must be less than or equal to to_date")
            return from_date, to_date
        raise ValueError("Provide schedule_date or both from_date and to_date")

    @staticmethod
    def _capacity_mw(master_row: Dict[str, Any]) -> Optional[float]:
        """Contracted capacity in MW, or None when the master has no usable value."""
        raw = master_row.get("ppa")
        if raw in (None, ""):
            return None
        try:
            capacity = float(raw)
        except (TypeError, ValueError):
            return None
        return capacity if capacity > 0 else None

    @staticmethod
    def _round(value: Optional[float]) -> Optional[float]:
        return None if value is None else round(value, _ROUND_DP)

    def _build_plant_rows(
        self,
        averages: Dict[Tuple[str, str], Dict[str, float]],
        plant_master: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        plant_ids = sorted({plant_id for plant_id, _ in averages})

        rows: List[Dict[str, Any]] = []
        for plant_id in plant_ids:
            technology = (plant_master.get(plant_id, {}).get("technology") or "").strip()
            if technology.lower() in EXCLUDED_TECHNOLOGIES:
                continue

            dc_bucket = averages.get((plant_id, DC_TYPE))
            sg_bucket = averages.get((plant_id, SG_TYPE))
            # No DC rows at all is a different statement from "DC declared as 0".
            # Open-access blocks and some renewables only ever carry SG, so their
            # DC, PLF and PAF stay null instead of reading as a 0% load factor.
            dc = None if dc_bucket is None else float(dc_bucket.get("average") or 0.0)
            sg = None if sg_bucket is None else float(sg_bucket.get("average") or 0.0)

            master = plant_master.get(plant_id, {})
            capacity = self._capacity_mw(master)

            if dc is None:
                plf = None
                paf = None
            else:
                # mplive's guard: a plant that declared zero capacity reports PLF 0
                # rather than dividing by zero. Nuclear and other must-run plants
                # sit here — RLDC publishes SG for them against a DC of 0.
                plf = ((sg or 0.0) / dc * 100) if dc else 0.0
                paf = (dc / capacity * 100) if capacity else None

            rows.append(
                {
                    "plant_id": plant_id,
                    "plant_name": master.get("plant_name") or plant_id,
                    "technology": master.get("technology"),
                    "category": master.get("category"),
                    "dc": self._round(dc),
                    "sg": self._round(sg),
                    "plf": self._round(plf),
                    "paf": self._round(paf),
                    "contracted_capacity": self._round(capacity),
                    "block_samples": int(
                        (dc_bucket or {}).get("block_count")
                        or (sg_bucket or {}).get("block_count")
                        or 0
                    ),
                }
            )
        return rows

    @staticmethod
    def build_csv_filename(
        from_date: str,
        to_date: str,
        from_block: int,
        to_block: int,
    ) -> str:
        return (
            f"reports_plf_paf_{from_date}_to_{to_date}"
            f"_blocks_{from_block}-{to_block}.csv"
        )

    async def get_reports_plf_paf(
        self,
        from_block: int = 1,
        to_block: int = 96,
        schedule_date: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        response_format: str = REPORTS_FORECAST_FORMAT_JSON,
    ) -> Dict[str, Any]:
        if from_block > to_block:
            raise ValueError("from_block must be less than or equal to to_block")

        resolved_from, resolved_to = self._resolve_date_range(
            schedule_date, from_date, to_date
        )
        validate_max_date_range_days(resolved_from, resolved_to)
        fmt = normalize_reports_forecast_format(response_format)

        averages = await self.repository.find_plant_dc_sg_averages(
            from_date=resolved_from,
            to_date=resolved_to,
            from_block=from_block,
            to_block=to_block,
        )
        plant_master = await self.repository.find_plant_master()
        plants = self._build_plant_rows(averages, plant_master)

        csv_export: Dict[str, Any] = {
            "headers": CSV_HEADERS,
            "row_count": len(plants),
        }
        if reports_forecast_includes_csv_content(fmt) and plants:
            csv_export["content"] = self.generate_csv(plants)
            csv_export["filename"] = self.build_csv_filename(
                resolved_from,
                resolved_to,
                from_block,
                to_block,
            )

        response: Dict[str, Any] = {
            "response_format": fmt,
            "query": {
                "from_date": resolved_from,
                "to_date": resolved_to,
                "from_block": from_block,
                "to_block": to_block,
                "format": fmt,
            },
            "summary": {
                "plant_count": len(plants),
                "plants_without_capacity": sum(
                    1 for row in plants if row["contracted_capacity"] is None
                ),
                "capacity_source": "plant_master.ppa",
            },
            "csv_export": csv_export,
            # One row per plant — at most a few dozen — so it is always built.
            # format=csv simply streams the file instead of serialising this.
            "plants": plants,
        }

        return response

    def generate_csv(self, plants: List[Dict[str, Any]]) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(CSV_HEADERS)
        for row in plants:
            writer.writerow(
                [
                    row.get("plant_name"),
                    row.get("technology") or "",
                    self._format_csv_value(row.get("dc")),
                    self._format_csv_value(row.get("sg")),
                    self._format_csv_value(row.get("plf")),
                    self._format_csv_value(row.get("paf")),
                    self._format_csv_value(row.get("contracted_capacity")),
                ]
            )
        return output.getvalue()

    @staticmethod
    def _format_csv_value(value: Any) -> str:
        return "" if value is None else str(value)


reports_plf_paf_service = ReportsPlfPafService()
