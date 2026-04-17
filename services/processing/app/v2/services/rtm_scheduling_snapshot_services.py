"""
RTM Scheduling Snapshot Service (V2)
Standalone data shaping + excel export for RTM scheduling snapshots.
"""

import logging
import re
from datetime import datetime
from io import BytesIO
from typing import Dict, Any, Optional, List

import pandas as pd
from fastapi import HTTPException, status

from ..repository.rtm_scheduling_snapshot_repository import rtm_scheduling_snapshot_repository

logger = logging.getLogger(__name__)


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
    def _build_snapshot_filename(cls, schedule_date: str, session_id: Optional[str] = None) -> str:
        parts = ["rtm_scheduling_snapshot", cls._sanitize_filename_part(schedule_date)]
        if session_id:
            parts.append(cls._sanitize_filename_part(session_id))
        return "_".join(parts) + ".xlsx"

    @staticmethod
    def _build_snapshot_excel_bytes(snapshot_data: Dict[str, Any]) -> bytes:
        output = BytesIO()
        sheet_names: Dict[str, str] = snapshot_data.get("sheet_names", {})
        data_by_source: Dict[str, List[Dict[str, Any]]] = snapshot_data.get("data_by_source", {})

        try:
            with pd.ExcelWriter(output) as writer:
                for source, rows in data_by_source.items():
                    if not rows:
                        continue
                    sheet_name = sheet_names.get(source, source)[:31]
                    pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name, index=False)
        except Exception:
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                for source, rows in data_by_source.items():
                    if not rows:
                        continue
                    sheet_name = sheet_names.get(source, source)[:31]
                    pd.DataFrame(rows).to_excel(writer, sheet_name=sheet_name, index=False)

        output.seek(0)
        return output.getvalue()

    async def get_rtm_scheduling_snapshot_data(
        self,
        session_id: Optional[str],
        schedule_date: str,
    ) -> Dict[str, Any]:
        try:
            match_filter: Dict[str, Any] = {"source_name": {"$in": self.RTM_SNAPSHOT_SOURCE_LIST}}
            if session_id:
                match_filter["session_id"] = session_id

            pipeline: List[Dict[str, Any]] = [
                {"$match": match_filter},
                {
                    "$addFields": {
                        "_schedule_date_filter": {
                            "$let": {
                                "vars": {"candidate": {"$ifNull": ["$schedule_date", "$raw_payload.schedule_date"]}},
                                "in": {
                                    "$trim": {
                                        "input": {
                                            "$cond": [
                                                {"$eq": [{"$type": "$$candidate"}, "date"]},
                                                {
                                                    "$dateToString": {
                                                        "date": "$$candidate",
                                                        "format": "%Y-%m-%d",
                                                        "timezone": "Asia/Kolkata",
                                                    }
                                                },
                                                {"$toString": "$$candidate"},
                                            ]
                                        }
                                    }
                                },
                            }
                        }
                    }
                },
                {"$match": {"_schedule_date_filter": schedule_date}},
                {"$project": {"_schedule_date_filter": 0}},
            ]

            docs = await self.repository.get_rtm_snapshot_rows(pipeline)
            grouped_data: Dict[str, List[Dict[str, Any]]] = {source: [] for source in self.RTM_SNAPSHOT_SOURCE_LIST}

            for doc in docs:
                source_name = doc.get("source_name")
                if source_name in grouped_data:
                    grouped_data[source_name].append(self._flatten_snapshot_document(doc))

            ordered_sources = [s for s in self.RTM_SNAPSHOT_SOURCE_LIST if s != "scheduling_optimized_row"] + ["scheduling_optimized_row"]
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

            return {
                "session_id": session_id,
                "schedule_date": schedule_date,
                "sheet_names": self.RTM_SNAPSHOT_SHEET_NAME_MAP,
                "data_by_source": output_data,
            }
        except Exception as e:
            logger.exception("Failed to fetch RTM scheduling snapshot data")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch RTM scheduling snapshot data: {str(e)}",
            )

    async def get_rtm_scheduling_snapshot_excel(
        self,
        session_id: Optional[str],
        schedule_date: str,
    ) -> Dict[str, Any]:
        snapshot_data = await self.get_rtm_scheduling_snapshot_data(
            session_id=session_id,
            schedule_date=schedule_date,
        )
        file_name = self._build_snapshot_filename(schedule_date=schedule_date, session_id=session_id)
        file_bytes = self._build_snapshot_excel_bytes(snapshot_data)
        return {"file_name": file_name, "file_bytes": file_bytes}


rtm_scheduling_snapshot_service = RTMSchedulingSnapshotService()
