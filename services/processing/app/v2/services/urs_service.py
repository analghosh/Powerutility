"""
URS Availed report.

Builds the grid the operator reads: one row per time block, one column per
plant that had surplus that day, then the block total and the weighted average
rate.

Deliberate differences from mplive's ursavailedreport, which this replaces:

1. The weighted average is computed here, not in the browser. mplive rebuilt it
   in JavaScript inside the render loop, which meant the exported spreadsheet
   and the screen could disagree and neither could be checked.

2. A block whose plants all have zero volume reports a null rate rather than
   zero. mplive divided by a hardcoded 1 when the total was zero, printing a
   rate of "0.00" for a block that had no surplus to price - indistinguishable
   from surplus that happened to be free.

3. Rates are flagged as partial when a plant has no fixed cost on record. Most
   plants are in that state, so the average is in practice variable-cost-only,
   and a reader has no other way to tell.
"""

import csv
import io
import logging
from datetime import datetime
from typing import Any, Dict, List

from ..repository.urs_repository import urs_repository

logger = logging.getLogger(__name__)

BLOCK_COUNT = 96
BLOCK_MINUTES = 15

#: A day at a time. The grid is plants-as-columns, so two days stacked would
#: repeat every column header and the weighted average would silently span
#: prices from both days.
MAX_BLOCK = BLOCK_COUNT
MIN_BLOCK = 1


def _block_window(time_block: int) -> Dict[str, str]:
    """"00:15-00:30" for block 2, the way the exchange labels them."""
    start = (time_block - 1) * BLOCK_MINUTES
    end = time_block * BLOCK_MINUTES
    fmt = lambda m: f"{(m // 60) % 24:02d}:{m % 60:02d}"
    return {"start_time": fmt(start), "end_time": fmt(end)}


class UrsService:
    async def get_urs_availed(
        self,
        schedule_date: str,
        from_block: int = MIN_BLOCK,
        to_block: int = MAX_BLOCK,
    ) -> Dict[str, Any]:
        from_block, to_block = self._validate_blocks(from_block, to_block)
        self._validate_date(schedule_date)

        rows = await urs_repository.get_day(schedule_date, from_block, to_block)

        plants = self._plant_columns(rows)
        blocks = self._build_blocks(rows, from_block, to_block, plants)

        return {
            "query": {
                "schedule_date": schedule_date,
                "from_block": from_block,
                "to_block": to_block,
            },
            "plants": plants,
            "blocks": blocks,
            "summary": self._summarise(rows, plants, blocks),
        }

    # ---- shaping -------------------------------------------------------

    @staticmethod
    def _plant_columns(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        The columns, in a stable order.

        Ordered by name rather than by first appearance: a plant that reports
        nothing until block 40 would otherwise jump into the middle of the
        header when yesterday's report is compared with today's.
        """
        seen: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            plant_id = row.get("plant_id")
            if plant_id and plant_id not in seen:
                seen[plant_id] = {
                    "plant_id": plant_id,
                    "plant_name": row.get("plant_name") or plant_id,
                    "technology": row.get("technology"),
                    "rate": row.get("total_fc_vc_price"),
                    "rate_complete": bool(row.get("price_complete")),
                }
        return sorted(seen.values(), key=lambda p: (p["plant_name"] or "").lower())

    def _build_blocks(
        self,
        rows: List[Dict[str, Any]],
        from_block: int,
        to_block: int,
        plants: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        by_block: Dict[int, List[Dict[str, Any]]] = {}
        for row in rows:
            by_block.setdefault(row.get("time_block"), []).append(row)

        blocks = []
        for time_block in range(from_block, to_block + 1):
            block_rows = by_block.get(time_block, [])
            volumes = {}
            total_volume = 0.0
            weighted_cost = 0.0
            priced_volume = 0.0
            partial = False

            for row in block_rows:
                volume = float(row.get("volume") or 0.0)
                volumes[row["plant_id"]] = volume
                total_volume += volume

                rate = row.get("total_fc_vc_price")
                if rate is not None:
                    # Only volume that carries a rate may weight the average.
                    # Including an unpriced plant in the divisor would drag the
                    # average towards zero and read as a cheaper block.
                    weighted_cost += volume * float(rate)
                    priced_volume += volume
                    if not row.get("price_complete"):
                        partial = True

            blocks.append(
                {
                    "time_block": time_block,
                    **_block_window(time_block),
                    "volumes": volumes,
                    "total": round(total_volume, 3),
                    # None, not 0: a block with no priced surplus has no rate,
                    # and a printed 0.00 would read as free power.
                    "weighted_avg_rate": (
                        round(weighted_cost / priced_volume, 2) if priced_volume else None
                    ),
                    # Only meaningful next to a rate. A block with no priced
                    # surplus has nothing to caveat, and flagging one anyway
                    # puts a warning against an empty cell.
                    "rate_is_partial": partial and bool(priced_volume),
                }
            )
        return blocks

    @staticmethod
    def _summarise(
        rows: List[Dict[str, Any]],
        plants: List[Dict[str, Any]],
        blocks: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        total = sum(block["total"] for block in blocks)
        rated = [b for b in blocks if b["weighted_avg_rate"] is not None]
        revisions = {r.get("source_revision_number") for r in rows} - {None}
        published = {r.get("schedule_published_time") for r in rows} - {None}
        return {
            "plant_count": len(plants),
            "block_count": len(blocks),
            # MW summed across 15-minute blocks is MW-blocks, not MWh; divide by
            # 4 for energy. Named so nobody reads it as the latter.
            "total_volume_mw_blocks": round(total, 3),
            "blocks_with_rate": len(rated),
            "plants_missing_fixed_cost": sum(
                1 for p in plants if p["rate"] is not None and not p["rate_complete"]
            ),
            "plants_missing_rate": sum(1 for p in plants if p["rate"] is None),
            "source_revision_number": max(revisions) if revisions else None,
            "schedule_published_time": max(published) if published else None,
        }

    # ---- validation ----------------------------------------------------

    @staticmethod
    def _validate_blocks(from_block: int, to_block: int) -> tuple:
        if not MIN_BLOCK <= from_block <= MAX_BLOCK:
            raise ValueError(f"from_block must be between {MIN_BLOCK} and {MAX_BLOCK}")
        if not MIN_BLOCK <= to_block <= MAX_BLOCK:
            raise ValueError(f"to_block must be between {MIN_BLOCK} and {MAX_BLOCK}")
        if from_block > to_block:
            raise ValueError("from_block cannot be after to_block")
        return from_block, to_block

    @staticmethod
    def _validate_date(schedule_date: str) -> None:
        try:
            datetime.strptime(schedule_date, "%Y-%m-%d")
        except (TypeError, ValueError) as exc:
            raise ValueError("schedule_date must be YYYY-MM-DD") from exc

    # ---- export --------------------------------------------------------

    @staticmethod
    def generate_csv(report: Dict[str, Any]) -> str:
        """
        The grid as the operator sees it, one row per block.

        Written with csv rather than by joining commas: plant names carry
        commas ("NTPC Tamil Nadu Energy Company Ltd, Vallur") and would
        otherwise split a column in two.
        """
        plants = report.get("plants") or []
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            ["TimeBlock", "Time"]
            + [p["plant_name"] for p in plants]
            + ["Total", "Wtd. Avg URS rate (Rs/MWh)"]
        )
        for block in report.get("blocks") or []:
            volumes = block.get("volumes") or {}
            rate = block.get("weighted_avg_rate")
            writer.writerow(
                [block["time_block"], f"{block['start_time']}-{block['end_time']}"]
                + [f"{volumes.get(p['plant_id'], 0.0):.2f}" for p in plants]
                + [
                    f"{block['total']:.2f}",
                    "" if rate is None else f"{rate:.2f}",
                ]
            )
        return buffer.getvalue()

    @staticmethod
    def build_csv_filename(schedule_date: str, from_block: int, to_block: int) -> str:
        return f"urs_availed_{schedule_date}_blocks_{from_block}_to_{to_block}.csv"


urs_service = UrsService()
