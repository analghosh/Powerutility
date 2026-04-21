"""
RTM Scheduling Snapshot Service (V2)
Standalone data shaping + excel export for RTM scheduling snapshots.
"""

import logging
import re
from datetime import datetime
from io import BytesIO
from typing import Dict, Any, Optional, List, Tuple

import pandas as pd
from fastapi import HTTPException, status

from ..repository.rtm_scheduling_snapshot_repository import rtm_scheduling_snapshot_repository

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 2000
MAX_PAGE_SIZE = 10000
EXCEL_AGGREGATE_MAX_TIME_MS = 180_000


class RTMSchedulingSnapshotService:
    RTM_SNAPSHOT_SOURCE_LIST = [
        "rtm_session_raw",
        "demand_forecast_raw",
        "demand_actual_raw",
        "plant_master_raw",
        "rtm_forecasted_price_raw",
        "supply_dc_raw",
        "supply_sg_dc_raw",
        "scheduling_optimized_row",
    ]

    RTM_SNAPSHOT_COLUMN_CONFIG: Dict[str, Dict[str, Any]] = {
        "supply_sg_dc_raw": {
            "keep": [
                "schedule_date", "source_name", "time_block",
                "plant_id", "plant_name", "key", "volume", "original_volume",
                "px_price", "scheduled_at", "category", "c_s",
            ],
            "rename": {"schedule_date": "inserted_date", "key": "plant_id"},
        },
        "supply_dc_raw": {
            "keep": [
                "schedule_date", "source_name", "time_block",
                "plant_id", "plant_name", "key", "volume", "original_volume",
                "px_price", "scheduled_at", "category", "c_s",
            ],
            "rename": {"schedule_date": "inserted_date", "key": "plant_id"},
        },
        "rtm_forecasted_price_raw": {
            "keep": [
                "schedule_date", "source_name",
                "market", "id", "scheduled_at", "category",
                "area", "block", "unit", "values",
            ],
            "rename": {"schedule_date": "inserted_date", "block": "time_block"},
        },
        "plant_master_raw": {
            "keep": [
                "schedule_date", "source_name",
                "plant_name", "c_s", "category", "technology",
                "dispatch_type", "price_group", "px_price",
                "vc_price", "multiplier", "volume", "ppa",
                "fc_price", "ramping", "tm", "approval_no", "dc_sg",
            ],
            "rename": {"schedule_date": "inserted_date"},
        },
        "demand_actual_raw": {
            "keep": [
                "schedule_date", "source_name", "time_block",
                "scheduled_at", "type", "actual", "drawal",
                "frequency", "rostering", "comp_act_demand",
                "udm", "comp_udm",
            ],
            "rename": {"schedule_date": "inserted_date"},
        },
        "demand_forecast_raw": {
            "keep": [
                "schedule_date", "source_name", "time_block",
                "scheduled_at", "type", "forecast",
                "comp_act_demand", "udm", "comp_udm",
            ],
            "rename": {"schedule_date": "inserted_date"},
        },
        "rtm_session_raw": {
            "keep": [
                "schedule_date", "source_name",
                "product_code", "from_period_code", "to_period_code",
                "from_period_time", "to_period_time",
                "delivery_type", "session_number",
            ],
            "rename": {"schedule_date": "inserted_date"},
        },
        "scheduling_optimized_row": {
            "keep": [
                "schedule_date", "source_name", "time_block",
                "plant_id", "plant_name", "scheduled_at",
                "demand_forecast_mw", "total_supply_available_mw",
                "net_surplus_deficit_mw", "remaining_volume_mw",
                "price", "rtm_forecasted_price",
                "final_rmp_volume_mw", "generated_sg_volume_mw",
                "actual_plant_volume_mw", "actual_dc_volume_mw",
                "category", "area", "dc_sg_type", "session_number",
            ],
            "rename": {"schedule_date": "inserted_date"},
        },
    }

    RTM_SNAPSHOT_SHEET_NAME_MAP = {
        "supply_sg_dc_raw": "Supply SG",
        "supply_dc_raw": "Supply DC",
        "rtm_forecasted_price_raw": "RTM Forecasted Price",
        "plant_master_raw": "Plant Master",
        "demand_actual_raw": "Demand Actual",
        "demand_forecast_raw": "Demand Forecast",
        "rtm_session_raw": "RTM Session",
        "scheduling_optimized_row": "Scheduling Optimized Output",
    }

    def __init__(self):
        self.repository = rtm_scheduling_snapshot_repository

    @classmethod
    def _build_slim_projection(cls) -> Dict[str, int]:
        proj: Dict[str, int] = {
            "source_name": 1,
            "schedule_date": 1,
            "session_id": 1,
            "time_block": 1,
            "plant_id": 1,
            "raw_payload": 1,
            "run_id": 1,
            "created_at": 1,
            "execution_id": 1,
        }
        for cfg in cls.RTM_SNAPSHOT_COLUMN_CONFIG.values():
            for col in cfg.get("keep", []):
                proj.setdefault(col, 1)
        return proj

    @staticmethod
    def _normalize_schedule_date(schedule_date: str) -> str:
        raw = (schedule_date or "").strip()
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="schedule_date is required",
            )
        candidate = raw[:10] if len(raw) >= 10 else raw
        if re.match(r"^\d{4}-\d{2}-\d{2}$", candidate):
            return candidate
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="schedule_date must be in YYYY-MM-DD format",
        )

    @staticmethod
    def _coerce_pagination(page: int, page_size: int) -> Tuple[int, int]:
        p = max(1, int(page))
        ps = int(page_size)
        ps = max(1, min(ps, MAX_PAGE_SIZE))
        return p, ps

    @staticmethod
    def _build_session_id_match(session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        Build a resilient session_id matcher.
        Snapshot writer often stores session_id as int; API query params arrive as string.
        """
        if session_id is None:
            return None
        raw = str(session_id).strip()
        if not raw:
            return None

        candidates: List[Any] = [raw]
        try:
            candidates.append(int(raw))
        except (ValueError, TypeError):
            pass

        deduped: List[Any] = []
        for value in candidates:
            if value not in deduped:
                deduped.append(value)
        return {"$in": deduped}

    def _build_base_match(
        self,
        schedule_date_norm: str,
        session_id: Optional[str],
        run_id: Optional[str],
    ) -> Dict[str, Any]:
        match_filter: Dict[str, Any] = {
            "schedule_date": schedule_date_norm,
            "source_name": {"$in": self.RTM_SNAPSHOT_SOURCE_LIST},
        }
        session_match = self._build_session_id_match(session_id)
        if session_match:
            match_filter["session_id"] = session_match
        if run_id is not None and str(run_id).strip():
            match_filter["run_id"] = str(run_id).strip()
        return match_filter

    @staticmethod
    def _parse_mongo_value(value: Any) -> Any:
        if isinstance(value, dict):
            if "$date" in value:
                return value.get("$date")
            if "$oid" in value:
                return value.get("$oid")
        return value

    def _flatten_snapshot_document(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        flat: Dict[str, Any] = {}
        for key, value in doc.items():
            if key in ["_id", "raw_payload"]:
                continue
            flat[key] = self._parse_mongo_value(value)

        raw_payload = doc.get("raw_payload", {}) or {}
        if isinstance(raw_payload, dict):
            for key, value in raw_payload.items():
                if key == "_id":
                    continue
                flat[key] = self._parse_mongo_value(value)
        return flat

    @staticmethod
    def _format_scheduled_at(value: Any) -> Any:
        if value in (None, ""):
            return value
        if isinstance(value, datetime):
            return value.strftime("%d-%m-%Y")
        try:
            cleaned = str(value).replace("Z", "+00:00")
            return datetime.fromisoformat(cleaned).strftime("%d-%m-%Y")
        except Exception:
            return value

    @staticmethod
    def _sort_by_time_block(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def _key_fn(row: Dict[str, Any]) -> int:
            val = row.get("time_block")
            try:
                return int(val)
            except Exception:
                return 10**9

        return sorted(rows, key=_key_fn)

    @staticmethod
    def _sanitize_filename_part(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")

    @classmethod
    def _build_snapshot_filename(
        cls,
        schedule_date: str,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> str:
        parts = ["rtm_scheduling_snapshot", cls._sanitize_filename_part(schedule_date)]
        if session_id:
            parts.append(cls._sanitize_filename_part(session_id))
        if run_id:
            parts.append(cls._sanitize_filename_part(run_id))
        return "_".join(parts) + ".xlsx"

    @staticmethod
    def _build_snapshot_excel_bytes(snapshot_data: Dict[str, Any]) -> bytes:
        output = BytesIO()
        sheet_names: Dict[str, str] = snapshot_data.get("sheet_names", {})
        data_by_source: Dict[str, List[Dict[str, Any]]] = snapshot_data.get("data_by_source", {})
        non_empty_sources = [source for source, rows in data_by_source.items() if rows]

        if not non_empty_sources:
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                pd.DataFrame(
                    [{
                        "message": "No snapshot data found for the requested filters",
                        "schedule_date": snapshot_data.get("schedule_date"),
                        "session_id": snapshot_data.get("session_id"),
                        "run_id": snapshot_data.get("run_id"),
                    }]
                ).to_excel(writer, sheet_name="No Data", index=False)
            output.seek(0)
            return output.getvalue()

        try:
            with pd.ExcelWriter(output) as writer:
                for source in non_empty_sources:
                    rows = data_by_source[source]
                    sheet_name = sheet_names.get(source, source)[:31]
                    pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name, index=False)
        except Exception:
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                for source in non_empty_sources:
                    rows = data_by_source[source]
                    sheet_name = sheet_names.get(source, source)[:31]
                    pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name, index=False)

        output.seek(0)
        return output.getvalue()

    def _shape_output_rows(self, grouped_data: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
        ordered_sources = (
            [s for s in self.RTM_SNAPSHOT_SOURCE_LIST if s != "scheduling_optimized_row"]
            + ["scheduling_optimized_row"]
        )
        output_data: Dict[str, List[Dict[str, Any]]] = {}

        for source in ordered_sources:
            records = grouped_data.get(source, [])
            if not records:
                continue

            config = self.RTM_SNAPSHOT_COLUMN_CONFIG.get(source)
            if not config:
                continue

            keep_cols = config.get("keep", [])
            rename_map = config.get("rename", {})
            rows: List[Dict[str, Any]] = []

            for record in records:
                row: Dict[str, Any] = {}
                if "session_id" in record:
                    row["session_id"] = record.get("session_id")

                for col in keep_cols:
                    if col in record:
                        row[rename_map.get(col, col)] = record.get(col)

                if "scheduled_at" in row:
                    row["scheduled_at"] = self._format_scheduled_at(row.get("scheduled_at"))
                rows.append(row)

            if any("time_block" in r for r in rows):
                rows = self._sort_by_time_block(rows)
            output_data[source] = rows

        return output_data

    async def get_rtm_scheduling_snapshot_data(
        self,
        session_id: Optional[str],
        schedule_date: str,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        run_id: Optional[str] = None,
        skip_pagination: bool = False,
        max_time_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        try:
            schedule_date_norm = self._normalize_schedule_date(schedule_date)
            base_match = self._build_base_match(schedule_date_norm, session_id, run_id)

            total_matched = await self.repository.count_snapshot_documents(base_match)

            slim = self._build_slim_projection()
            sort_stage: Dict[str, int] = {
                "session_id": 1,
                "source_name": 1,
                "time_block": 1,
                "plant_id": 1,
                "run_id": 1,
                "created_at": 1,
            }

            pipeline: List[Dict[str, Any]] = [
                {"$match": base_match},
                {"$project": slim},
                {"$sort": sort_stage},
            ]

            if skip_pagination:
                page_eff, page_size_eff = 1, total_matched
                has_next = False
            else:
                page_eff, page_size_eff = self._coerce_pagination(page, page_size)
                skip = (page_eff - 1) * page_size_eff
                pipeline.extend([{"$skip": skip}, {"$limit": page_size_eff}])
                has_next = skip + page_size_eff < total_matched

            docs = await self.repository.get_rtm_snapshot_rows(pipeline, max_time_ms=max_time_ms)

            grouped_data: Dict[str, List[Dict[str, Any]]] = {source: [] for source in self.RTM_SNAPSHOT_SOURCE_LIST}
            for doc in docs:
                source_name = doc.get("source_name")
                if source_name in grouped_data:
                    grouped_data[source_name].append(self._flatten_snapshot_document(doc))

            output_data = self._shape_output_rows(grouped_data)

            run_id_out = (
                str(run_id).strip() if run_id is not None and str(run_id).strip() else None
            )
            return {
                "session_id": session_id,
                "run_id": run_id_out,
                "schedule_date": schedule_date_norm,
                "sheet_names": self.RTM_SNAPSHOT_SHEET_NAME_MAP,
                "data_by_source": output_data,
                "pagination": {
                    "page": page_eff,
                    "page_size": page_size_eff if not skip_pagination else total_matched,
                    "total_matched": total_matched,
                    "has_next": has_next,
                },
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Failed to fetch RTM scheduling snapshot data")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch RTM scheduling snapshot data: {str(e)}",
            )

    def validate_excel_export_scope(
        self,
        session_id: Optional[str],
        run_id: Optional[str],
    ) -> None:
        """Excel export must be bounded: require session_id or run_id."""
        has_session = session_id is not None and str(session_id).strip() != ""
        has_run = run_id is not None and str(run_id).strip() != ""
        if not has_session and not has_run:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Excel export requires session_id or run_id to avoid unbounded downloads. "
                    "Use the JSON snapshot endpoint with pagination for all sessions on a date."
                ),
            )

    async def get_rtm_scheduling_snapshot_excel(
        self,
        session_id: Optional[str],
        schedule_date: str,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.validate_excel_export_scope(session_id=session_id, run_id=run_id)
        snapshot_data = await self.get_rtm_scheduling_snapshot_data(
            session_id=session_id,
            schedule_date=schedule_date,
            run_id=run_id,
            skip_pagination=True,
            max_time_ms=EXCEL_AGGREGATE_MAX_TIME_MS,
        )
        file_name = self._build_snapshot_filename(
            schedule_date=snapshot_data["schedule_date"],
            session_id=session_id,
            run_id=run_id,
        )
        file_bytes = self._build_snapshot_excel_bytes(snapshot_data)
        return {"file_name": file_name, "file_bytes": file_bytes}


rtm_scheduling_snapshot_service = RTMSchedulingSnapshotService()
