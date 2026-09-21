from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
import logging
import mimetypes
import re

from bson import ObjectId
from fastapi import UploadFile, HTTPException

from ..repository.ppa_details_repository import ppa_details_repository
from ..repository.master_plants_repository import master_plants_repository
from ...core.config import settings

logger = logging.getLogger(__name__)

ALLOWED_PPA_EXTENSIONS = {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg", ".xls", ".xlsx"}
MAX_PPA_FILE_BYTES = 25 * 1024 * 1024  # 25 MB


class PpaDetailsService:
    def __init__(self):
        self.repository = ppa_details_repository
        self.plants_repository = master_plants_repository

    def _prepare_for_output(self, doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None
        if "_id" in doc and isinstance(doc["_id"], ObjectId):
            doc["_id"] = str(doc["_id"])
        for key in ("ppa_signing_date", "expiry", "commercial_operation_date", "created_at", "updated_at"):
            value = doc.get(key)
            if isinstance(value, datetime):
                doc[key] = value.isoformat()
        files = doc.get("ppa_files") or []
        for f in files:
            uploaded_at = f.get("uploaded_at")
            if isinstance(uploaded_at, datetime):
                f["uploaded_at"] = uploaded_at.isoformat()
            # Hyperlink path for UI — client opens via GET download endpoint
            f["download_url"] = f"/api/v2/ppa_details/{doc['_id']}/files/{f.get('file_id')}"
        return doc

    def _parse_date(self, value: Any) -> Optional[datetime]:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            text = value.strip()
            for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%d-%m-%Y", "%d/%m/%Y"):
                try:
                    return datetime.strptime(text.replace("Z", ""), fmt.replace("Z", ""))
                except ValueError:
                    continue
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                raise ValueError(f"Invalid date format: {value}")
        raise ValueError(f"Unsupported date value: {value}")

    def _compute_derived_fields(self, updates: Dict[str, Any], existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Compute expiry (COD + agreement_term) and total_allocation when inputs present."""
        existing = existing or {}

        for date_field in ("ppa_signing_date", "commercial_operation_date", "expiry"):
            if date_field in updates:
                updates[date_field] = self._parse_date(updates[date_field])

        # Expiry based on COD + agreement term (years), unless expiry explicitly provided
        if "expiry" not in updates or updates.get("expiry") is None:
            cod = updates.get("commercial_operation_date")
            if cod is None:
                cod = existing.get("commercial_operation_date")
                if isinstance(cod, str):
                    try:
                        cod = self._parse_date(cod)
                    except ValueError:
                        cod = None

            term = updates.get("agreement_term")
            if term is None:
                term = existing.get("agreement_term")

            if cod is not None and term is not None:
                try:
                    years = float(term)
                    # Approximate year as 365.25 days
                    updates["expiry"] = cod + timedelta(days=years * 365.25)
                except (TypeError, ValueError):
                    pass

        # Total allocation = firm + unallocated when either side changes and total not explicitly set
        if "total_allocation_mw" not in updates or updates.get("total_allocation_mw") is None:
            firm = updates.get("mppmcl_firm_allocation_mw")
            if firm is None:
                firm = existing.get("mppmcl_firm_allocation_mw")
            unalloc = updates.get("unallocated_share_cs_mw")
            if unalloc is None:
                unalloc = existing.get("unallocated_share_cs_mw")
            if firm is not None and unalloc is not None:
                try:
                    updates["total_allocation_mw"] = float(firm) + float(unalloc)
                except (TypeError, ValueError):
                    pass

        if "status" in updates and updates["status"] is not None:
            status = str(updates["status"]).strip()
            if status.lower() in ("active", "inactive"):
                updates["status"] = status.capitalize()
            elif status not in ("Active", "Inactive"):
                raise ValueError("status must be Active or Inactive")

        return updates

    async def sync_from_plant(self, plant_id: str, plant_name: str) -> str:
        """Create or refresh ppa_details stub from plant_master values."""
        return await self.repository.upsert_from_plant(plant_id, plant_name)

    async def sync_all_from_plants(self) -> Dict[str, Any]:
        """Backfill ppa_details for every plant in plant_master."""
        plants = await self.plants_repository.get_all_plants()
        result = await self.repository.bulk_upsert_from_plants(plants)
        return {
            "message": "PPA details synced from plant_master",
            **result,
            "plants_scanned": len(plants),
        }

    async def get_ppa(self, ppa_oid: str, actor: Dict[str, Any], request_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        doc = await self.repository.get_by_id(ppa_oid)
        return self._prepare_for_output(doc)

    async def list_ppa(
        self,
        filters: Optional[Dict[str, Any]],
        search: Optional[str],
        page: int,
        page_size: int,
        actor: Dict[str, Any],
        request_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        result = await self.repository.list_ppa(
            filters=filters,
            search=search,
            page=page,
            page_size=page_size,
            sort=[("plant_name", 1)],
        )
        if result.get("items"):
            # Attach S.no based on pagination offset
            start = (page - 1) * page_size
            prepared = []
            for idx, item in enumerate(result["items"]):
                prepared_item = self._prepare_for_output(item)
                if prepared_item is not None:
                    prepared_item["s_no"] = start + idx + 1
                    prepared.append(prepared_item)
            result["items"] = prepared
        return result

    async def update_ppa(
        self,
        ppa_oid: str,
        updates: Dict[str, Any],
        actor: Dict[str, Any],
        request_info: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        existing = await self.repository.get_by_id(ppa_oid)
        if not existing:
            return None

        # plant_id / plant_name are owned by plant_master
        updates.pop("plant_id", None)
        updates.pop("plant_name", None)
        updates.pop("_id", None)
        updates.pop("ppa_files", None)

        cleaned = {k: (v.strip() if isinstance(v, str) else v) for k, v in updates.items() if v is not None}
        if not cleaned:
            return self._prepare_for_output(existing)

        cleaned = self._compute_derived_fields(cleaned, existing)
        updated = await self.repository.update_ppa(ppa_oid, cleaned)
        return self._prepare_for_output(updated)

    async def delete_ppa(self, ppa_oid: str, actor: Dict[str, Any], request_info: Dict[str, Any]) -> bool:
        doc = await self.repository.get_by_id(ppa_oid)
        if not doc:
            return False
        # Remove files from disk
        for f in doc.get("ppa_files") or []:
            self._delete_stored_file(f.get("stored_filename"))
        return await self.repository.delete_by_id(ppa_oid)

    async def delete_by_plant_id(self, plant_id: str) -> bool:
        doc = await self.repository.get_by_plant_id(plant_id)
        if not doc:
            return False
        for f in doc.get("ppa_files") or []:
            self._delete_stored_file(f.get("stored_filename"))
        return await self.repository.delete_by_plant_id(plant_id)

    def _upload_dir(self) -> Path:
        path = Path(settings.PPA_DETAILS_UPLOAD_DIR)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _safe_filename(self, original: str) -> str:
        base = Path(original or "ppa_file").name
        base = re.sub(r"[^\w.\-]+", "_", base)
        return base[:180] or "ppa_file"

    def _delete_stored_file(self, stored_filename: Optional[str]) -> None:
        if not stored_filename:
            return
        try:
            path = self._upload_dir() / stored_filename
            if path.exists() and path.is_file():
                path.unlink()
        except Exception as e:
            logger.warning("Failed to delete PPA file %s: %s", stored_filename, e)

    async def upload_ppa_file(
        self,
        ppa_oid: str,
        file: UploadFile,
        actor: Dict[str, Any],
        request_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        doc = await self.repository.get_by_id(ppa_oid)
        if not doc:
            raise HTTPException(status_code=404, detail="PPA details not found")

        original = file.filename or "ppa_file"
        ext = Path(original).suffix.lower()
        if ext not in ALLOWED_PPA_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type. Allowed: {', '.join(sorted(ALLOWED_PPA_EXTENSIONS))}",
            )

        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        if len(content) > MAX_PPA_FILE_BYTES:
            raise HTTPException(status_code=400, detail="File exceeds maximum size of 25 MB")

        file_id = str(uuid4())
        stored_filename = f"{ppa_oid}_{file_id}_{self._safe_filename(original)}"
        dest = self._upload_dir() / stored_filename
        dest.write_bytes(content)

        content_type = file.content_type or mimetypes.guess_type(original)[0]
        file_info = {
            "file_id": file_id,
            "original_filename": original,
            "stored_filename": stored_filename,
            "content_type": content_type,
            "size_bytes": len(content),
            "uploaded_at": datetime.utcnow(),
        }

        updated = await self.repository.push_file(ppa_oid, file_info)
        prepared = self._prepare_for_output(updated)
        return {
            "message": "PPA file uploaded successfully",
            "file": {
                **{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in file_info.items()},
                "download_url": f"/api/v2/ppa_details/{ppa_oid}/files/{file_id}",
            },
            "ppa_details": prepared,
        }

    async def get_file_path(self, ppa_oid: str, file_id: str) -> Dict[str, Any]:
        doc = await self.repository.get_by_id(ppa_oid)
        if not doc:
            raise HTTPException(status_code=404, detail="PPA details not found")

        match = None
        for f in doc.get("ppa_files") or []:
            if f.get("file_id") == file_id:
                match = f
                break
        if not match:
            raise HTTPException(status_code=404, detail="PPA file not found")

        path = self._upload_dir() / match["stored_filename"]
        if not path.exists():
            raise HTTPException(status_code=404, detail="PPA file missing on disk")

        return {
            "path": path,
            "original_filename": match.get("original_filename") or path.name,
            "content_type": match.get("content_type") or "application/octet-stream",
        }

    async def delete_ppa_file(
        self,
        ppa_oid: str,
        file_id: str,
        actor: Dict[str, Any],
        request_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        doc = await self.repository.get_by_id(ppa_oid)
        if not doc:
            raise HTTPException(status_code=404, detail="PPA details not found")

        match = None
        for f in doc.get("ppa_files") or []:
            if f.get("file_id") == file_id:
                match = f
                break
        if not match:
            raise HTTPException(status_code=404, detail="PPA file not found")

        self._delete_stored_file(match.get("stored_filename"))
        updated = await self.repository.remove_file(ppa_oid, file_id)
        return {
            "message": "PPA file deleted successfully",
            "file_id": file_id,
            "ppa_details": self._prepare_for_output(updated),
        }


ppa_details_service = PpaDetailsService()
