
import logging
from typing import Dict, Any, Optional, Tuple
from fastapi import HTTPException, status, Request
from datetime import date, timedelta
from typing import List

# keep your existing imports/paths
from ..services.supply_pg_services import supply_pg_services
from ..services.serve_supply_services import serve_supply_services

logger = logging.getLogger(__name__)


class SupplyController:
    def __init__(self, supply_pg_services=supply_pg_services, serve_supply_services=serve_supply_services):
        """
        Controller for Supply endpoints. Delegates to service layer(s).
        """
        self.supply_pg_services = supply_pg_services
        self.serve_supply_services = serve_supply_services

    # --------------------------- Helpers --------------------------- #
    def _extract_request_info(self, request: Request) -> Dict[str, Optional[str]]:
        return {
            "ip_address": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "method": request.method,
            "path": request.url.path,
            "timestamp": request.headers.get("x-request-timestamp"),
        }
    
    # ---------- helpers ----------
    @staticmethod
    def _parse_date(s: str) -> date:
        try:
            return date.fromisoformat(s)
        except Exception:
            raise HTTPException(status_code=422, detail="Dates must be YYYY-MM-DD")

    @staticmethod
    def _block_bounds(b: Optional[int], default: int) -> int:
        if b is None:
            return default
        if not (1 <= b <= 96):
            raise HTTPException(status_code=422, detail="time_block must be in 1..96")
        return b

    # --------------------------- Category --------------------------- #
    def supply_by_category_block(
        self,
        schedule_date: Optional[str],
        time_block: Optional[int],
    ) -> Dict[str, Any]:
        try:
            return self.serve_supply_services.supply_by_category_block(schedule_date, time_block)
        except Exception:
            logger.exception("supply_by_category_block failed | schedule_date=%s time_block=%s", schedule_date, time_block)
            raise

    def supply_by_category(self, schedule_date: Optional[str]) -> Dict[str, Any]:
        try:
            return self.serve_supply_services.supply_by_category(schedule_date)
        except Exception:
            logger.exception("supply_by_category failed | schedule_date=%s", schedule_date)
            raise

    # --------------------------- Technology --------------------------- #
    def supply_by_technology(self, schedule_date: Optional[str], time_block: Optional[int]) -> Dict[str, Any]:
        try:
            return self.serve_supply_services.supply_by_technology(schedule_date, time_block)
        except Exception:
            logger.exception("supply_by_technology failed | schedule_date=%s time_block=%s", schedule_date, time_block)
            raise

    # --------------------------- Price Group (tabular) --------------------------- #
    def supply_by_price_group(self, schedule_date: Optional[str], time_block: Optional[int]) -> Dict[str, Any]:
        try:
            pg = self.serve_supply_services.supply_by_price_group(schedule_date, time_block)
            pr = self.supply_pg_services.price_range()
            return {"price_group": pg, "price_range": pr}
        except Exception:
            logger.exception("supply_by_price_group failed | schedule_date=%s time_block=%s", schedule_date, time_block)
            raise

    # ===================== GRAPH ENDPOINTS (totals only) ===================== #

    def supply_by_technology_graph(self, schedule_date: Optional[str]) -> Dict[str, Any]:
        try:
            return self.serve_supply_services.supply_by_technology_graph(schedule_date)
        except Exception:
            logger.exception("supply_by_technology_graph failed | schedule_date=%s", schedule_date)
            raise

    def supply_by_price_group_graph(self, schedule_date: Optional[str]) -> Dict[str, Any]:
        try:
            price_group = self.serve_supply_services.supply_by_price_group_graph(schedule_date)
            price_range = self.supply_pg_services.price_range()
            return {"price_group": price_group, "price_range": price_range}
        except Exception:
            logger.exception("supply_by_price_group_graph failed | schedule_date=%s", schedule_date)
            raise

    # ===================== NEW: Plants (single block, VC-ordered) ===================== #
    def plants_list(
        self,
        schedule_date: Optional[str] = None,
        time_block: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        NEW behavior: **single-block** list sorted by **VC ascending**, then **volume**, then plant_name,
        with **within-block cumulative** in that sort order.
        - SG priority: if SG/SH exists for plant×block, use SG; else DC.
        - Defaults: schedule_date = today (IST), time_block = current IST time block.

        Returns:
        {
          "result": [
            {
              "time_block": <int>,
              "plant_breakdown": [
                {
                  "plant_name": <str>,
                  "c_s": "Central" | "State",
                  "technology": <str>,
                  "vc": <float | null>,
                  "volume": <float>,               # chosen SG/DC for this plant
                  "cumulative_volume": <float>     # running sum within the block
                }, ...
              ]
            }
          ]
        }
        """
        try:
            return self.serve_supply_services.plants_list(
                schedule_date=schedule_date, time_block=time_block
            )
        except Exception:
            logger.exception(
                "plants_list failed | schedule_date=%s time_block=%s",
                schedule_date,
                time_block,
            )
            raise

    # ---------- plant_list_block (range) ----------
    def plants_list_range(
        self,
        date_from: str,
        date_to: str,
        from_block: Optional[int],
        to_block: Optional[int],
    ) -> List[Dict[str, Any]]:
        d_from = self._parse_date(date_from)
        d_to = self._parse_date(date_to)
        if d_from > d_to:
            d_from, d_to = d_to, d_from

        b_from = self._block_bounds(from_block, 1)
        b_to = self._block_bounds(to_block, 96)
        if b_from > b_to:
            b_from, b_to = b_to, b_from

        out: List[Dict[str, Any]] = []
        cur = d_from
        while cur <= d_to:
            for tb in range(b_from, b_to + 1):
                doc = self.serve_supply_services.plants_list(
                    schedule_date=cur.isoformat(),
                    time_block=tb,
                )
                items = doc.get("result", [])
                plant_breakdown = items[0].get("plant_breakdown", []) if items else []
                out.append({
                    "schedule_date": cur.isoformat(),
                    "time_block": tb,
                    "plant_breakdown": plant_breakdown,
                })
            cur += timedelta(days=1)
        return out

    # ---------- price_group (range) ----------
    def price_group_range(
        self,
        date_from: str,
        date_to: str,
        from_block: Optional[int],
        to_block: Optional[int],
    ) -> List[Dict[str, Any]]:
        d_from = self._parse_date(date_from)
        d_to = self._parse_date(date_to)
        if d_from > d_to:
            d_from, d_to = d_to, d_from

        b_from = self._block_bounds(from_block, 1)
        b_to = self._block_bounds(to_block, 96)
        if b_from > b_to:
            b_from, b_to = b_to, b_from

        out: List[Dict[str, Any]] = []
        cur = d_from
        while cur <= d_to:
            for tb in range(b_from, b_to + 1):
                doc = self.serve_supply_services.supply_by_price_group(
                    schedule_date=cur.isoformat(),
                    time_block=tb,
                )
                items = doc.get("result", [])
                price_group_breakdown = items[0].get("price_group_breakdown", []) if items else []
                out.append({
                    "schedule_date": cur.isoformat(),
                    "time_block": tb,
                    "price_group_breakdown": price_group_breakdown,
                })
            cur += timedelta(days=1)
        return out

    # ---------- technology (range) ----------
    def technology_range(
        self,
        date_from: str,
        date_to: str,
        from_block: Optional[int],
        to_block: Optional[int],
    ) -> List[Dict[str, Any]]:
        d_from = self._parse_date(date_from)
        d_to = self._parse_date(date_to)
        if d_from > d_to:
            d_from, d_to = d_to, d_from

        b_from = self._block_bounds(from_block, 1)
        b_to = self._block_bounds(to_block, 96)
        if b_from > b_to:
            b_from, b_to = b_to, b_from

        out: List[Dict[str, Any]] = []
        cur = d_from
        while cur <= d_to:
            for tb in range(b_from, b_to + 1):
                doc = self.serve_supply_services.supply_by_technology(
                    schedule_date=cur.isoformat(),
                    time_block=tb,
                )
                items = doc.get("result", [])
                technology_breakdown = items[0].get("technology_breakdown", []) if items else []
                out.append({
                    "schedule_date": cur.isoformat(),
                    "time_block": tb,
                    "technology_breakdown": technology_breakdown,
                })
            cur += timedelta(days=1)
        return out

    # ---------- CATEGORY (single block) range ----------
    def category_block_range(
        self,
        date_from: str,
        date_to: str,
        from_block: Optional[int],
        to_block: Optional[int],
    ) -> List[Dict[str, Any]]:
        d_from = self._parse_date(date_from)
        d_to = self._parse_date(date_to)
        if d_from > d_to:
            d_from, d_to = d_to, d_from

        b_from = self._block_bounds(from_block, 1)
        b_to   = self._block_bounds(to_block, 96)
        if b_from > b_to:
            b_from, b_to = b_to, b_from

        out: List[Dict[str, Any]] = []
        cur = d_from
        while cur <= d_to:
            for tb in range(b_from, b_to + 1):
                doc = self.serve_supply_services.supply_by_category_block(
                    schedule_date=cur.isoformat(),
                    time_block=tb,
                )
                items = doc.get("result", [])
                breakdown = items[0].get("category_breakdown", []) if items else []
                out.append({
                    "schedule_date": cur.isoformat(),
                    "time_block": tb,
                    "category_breakdown": breakdown,
                })
            cur += timedelta(days=1)
        return out

    # ---------- CATEGORY (whole day) range with block filtering ----------
    def category_range(
        self,
        date_from: str,
        date_to: str,
        from_block: Optional[int],
        to_block: Optional[int],
    ) -> List[Dict[str, Any]]:
        """
        Reuses day-level service 'supply_by_category(schedule_date)' and filters
        to [from_block, to_block], emitting one item per (date, block).
        """
        d_from = self._parse_date(date_from)
        d_to = self._parse_date(date_to)
        if d_from > d_to:
            d_from, d_to = d_to, d_from

        b_from = self._block_bounds(from_block, 1)
        b_to   = self._block_bounds(to_block, 96)
        if b_from > b_to:
            b_from, b_to = b_to, b_from

        out: List[Dict[str, Any]] = []
        cur = d_from
        while cur <= d_to:
            day_doc = self.serve_supply_services.supply_by_category(cur.isoformat())
            blocks: List[Dict[str, Any]] = day_doc.get("result", [])
            for row in blocks:
                tb = row.get("block")
                if isinstance(tb, int) and b_from <= tb <= b_to:
                    out.append({
                        "schedule_date": cur.isoformat(),
                        "time_block": tb,
                        "byCategory": row.get("byCategory", []),
                        "comp_act_demand": row.get("comp_act_demand"),
                        "comp_udm": row.get("comp_udm"),
                    })
            cur += timedelta(days=1)
        return out
    
    # --------------------------- Raw supply data (filtered, paginated) --------------------------- #
    def supply_raw(
        self,
        date_from: Optional[str],
        date_to: Optional[str],
        from_block: Optional[int],
        to_block: Optional[int],
        filters: Dict[str, Any],
        include_fields: Optional[List[str]],
        exclude_fields: Optional[List[str]],
        sort: Optional[List[Tuple[str, int]]],
        skip: int,
        limit: Optional[int],  # <-- make optional
    ) -> Dict[str, Any]:
        try:
            return self.serve_supply_services.supply_raw(
                date_from=date_from,
                date_to=date_to,
                from_block=from_block,
                to_block=to_block,
                filters=filters,
                include_fields=include_fields,
                exclude_fields=exclude_fields,
                sort=sort,
                skip=skip,
                limit=limit,     # <-- may be None (means no $limit)
            )
        except Exception:
            logger.exception("supply_raw failed")
            raise

# Global instance for routes to import
supply_controller = SupplyController()
