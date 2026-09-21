
from typing import Any, Dict, List, Optional
import logging
from fastapi import UploadFile, HTTPException
import pandas as pd
from io import BytesIO
from bson import ObjectId
import numpy as np

from ..repository.master_plants_repository import master_plants_repository
from libs.models.plant_entity import PlantEntity

logger = logging.getLogger(__name__)


async def _sync_ppa_details_from_plant(
    plant_id: Optional[str],
    plant_name: Optional[str],
    category: Optional[str] = None,
) -> None:
    """Best-effort upsert of ppa_details stub when plant_master changes."""
    if not plant_id:
        return
    try:
        from ..services.ppa_details_services import ppa_details_service
        await ppa_details_service.sync_from_plant(
            str(plant_id).strip(),
            plant_name or "",
            category,
        )
    except Exception as e:
        logger.error("Failed to sync ppa_details for plant_id=%s: %s", plant_id, e)


async def _delete_ppa_details_for_plant(plant_id: Optional[str]) -> None:
    if not plant_id:
        return
    try:
        from ..services.ppa_details_services import ppa_details_service
        await ppa_details_service.delete_by_plant_id(str(plant_id).strip())
    except Exception as e:
        logger.error("Failed to delete ppa_details for plant_id=%s: %s", plant_id, e)
class PayloadMapper:
    def __init__(self):
        self.field_mapping = {
            'TM': 'tm',
            'FC_price': 'fc_price',
            'VC_price': 'vc_price',
            'PX_price': 'px_price',
        }
        self.reverse_mapping = {v: k for k, v in self.field_mapping.items()}

    def transform_input_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not payload:
            return payload
        transformed = {}
        for key, value in payload.items():
            if key in self.field_mapping:
                entity_key = self.field_mapping[key]
                transformed[entity_key] = value
            else:
                transformed[key] = value
        return transformed

    def transform_output_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not payload:
            return payload
        transformed = {}
        for key, value in payload.items():
            if key in self.reverse_mapping:
                legacy_key = self.reverse_mapping[key]
                transformed[legacy_key] = value
            else:
                transformed[key] = value
        return transformed

class MasterPlantsService:
    def __init__(self):
        self.repository = master_plants_repository
        self.payload_mapper = PayloadMapper()

    def _prepare_for_output(self, doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Converts _id to string and transforms payload to legacy format."""
        if not doc:
            return None
        
        if '_id' in doc and isinstance(doc['_id'], ObjectId):
            doc['_id'] = str(doc['_id'])
        
        return self.payload_mapper.transform_output_payload(doc)

    async def create_plant(self, payload: Dict[str, Any], actor: Dict[str, Any], request_info: Dict[str, Any]) -> str:
        try:
            transformed_payload = self.payload_mapper.transform_input_payload(payload)
            cleaned_payload = {k: v.strip() if isinstance(v, str) else v for k, v in transformed_payload.items()}
            
            new_id = await self.repository.create_plant(cleaned_payload)

            await _sync_ppa_details_from_plant(
                cleaned_payload.get("plant_id"),
                cleaned_payload.get("plant_name"),
                cleaned_payload.get("category"),
            )

            logger.info(f"Master plant created | _id={new_id}")
            return new_id
        except Exception as e:
            raise

    async def get_plant(self, plant_oid: str, actor: Dict[str, Any], request_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            doc = await self.repository.get_plant_by_id(plant_oid)
            prepared_doc = self._prepare_for_output(doc)
            

            return prepared_doc
        except Exception as e:

            raise

    async def list_plants(self, filters: Optional[Dict[str, Any]], page: int, page_size: int, actor: Dict[str, Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        try:
            result = await self.repository.list_plants(filters=filters, page=page, page_size=page_size, sort=[("plant_name", 1)])
            
            if 'items' in result and result['items']:
                result['items'] = [self._prepare_for_output(item) for item in result['items']]
            

            return result
        except Exception as e:

            raise

    async def update_plant(self, plant_oid: str, updates: Dict[str, Any], actor: Dict[str, Any], request_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            transformed_updates = self.payload_mapper.transform_input_payload(updates)
            cleaned_updates = {k: v.strip() if isinstance(v, str) else v for k, v in transformed_updates.items() if v is not None and k != '_id'}

            if not cleaned_updates:
                doc = await self.repository.get_plant_by_id(plant_oid)
                return self._prepare_for_output(doc)

            existing = await self.repository.get_plant_by_id(plant_oid)
            old_plant_id = (existing or {}).get("plant_id")

            updated_doc = await self.repository.update_plant(plant_oid, cleaned_updates)
            prepared_doc = self._prepare_for_output(updated_doc)

            new_plant_id = (updated_doc or {}).get("plant_id") or old_plant_id
            new_plant_name = (updated_doc or {}).get("plant_name")
            new_category = (updated_doc or {}).get("category")
            # If plant_id changed, drop the old ppa row then upsert under the new id
            if old_plant_id and new_plant_id and str(old_plant_id) != str(new_plant_id):
                await _delete_ppa_details_for_plant(old_plant_id)
            await _sync_ppa_details_from_plant(new_plant_id, new_plant_name, new_category)

            return prepared_doc
        except Exception as e:

            raise

    async def delete_plant(self, plant_oid: str, actor: Dict[str, Any], request_info: Dict[str, Any]) -> bool:
        try:
            # Capture plant_id before delete so we can clear staging for today/tomorrow.
            existing = await self.repository.get_plant_by_id(plant_oid)
            plant_id = (existing or {}).get("plant_id") if existing else None

            ok = await self.repository.delete_plant(plant_oid)
            if not ok:
                return False

            if plant_id:
                await _delete_ppa_details_for_plant(plant_id)
                try:
                    from ..repository.supply_manual_repository import supply_manual_repository
                    deleted_count = await supply_manual_repository.delete_by_plant_for_today_tomorrow(
                        str(plant_id).strip()
                    )
                    logger.info(
                        "Plant delete cleanup | plant_oid=%s plant_id=%s dc_sg_deleted=%s",
                        plant_oid,
                        plant_id,
                        deleted_count,
                    )
                except Exception as cleanup_err:
                    # Plant master delete already succeeded; do not fail the API.
                    logger.error(
                        "Plant delete succeeded but dc_sg cleanup failed | plant_oid=%s plant_id=%s error=%s",
                        plant_oid,
                        plant_id,
                        cleanup_err,
                    )
            else:
                logger.warning(
                    "Plant delete: no plant_id on document | plant_oid=%s — skipped dc_sg cleanup",
                    plant_oid,
                )

            return True
        except Exception as e:
            raise

    async def get_all_plants(self) -> List[Dict[str, Any]]:
        """Return all master plant documents without pagination."""
        docs = await self.repository.get_all_plants()
        return [self._prepare_for_output(doc) for doc in docs]

    async def export_csv(self) -> BytesIO:
        """Generate CSV for all master plants and return a BytesIO buffer."""
        plants = await self.repository.get_all_plants()
        columns = [
            'plant_name', 'c_s', 'category', 'technology', 'price_group',
            'ramping', 'tm', 'fc_price', 'px_price', 'vc_price',
            'multiplier', 'volume', 'ppa', 'plant_id', 'dispatch_type',
            'approval_no', 'min_up_time', 'min_shut_down', 'max_shut_down'
        ]
        df = pd.DataFrame(plants)
        for col in columns:
            if col not in df.columns:
                df[col] = None
        df = df[columns]
        buffer = BytesIO()
        df.to_csv(buffer, index=False)
        buffer.seek(0)
        return buffer

    def _create_plant_upsert_filter(self, plant: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "technology": plant.get("technology"),
            "c_s": plant.get("c_s"),
            "plant_name": plant.get("plant_name")
        }

    async def process_plant_file(self, file: UploadFile, actor: Dict[str, Any], request_info: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if not file.filename or not file.filename.lower().endswith(('.csv', '.xlsx')):
                raise HTTPException(status_code=400, detail="Invalid file type. Only CSV and XLSX files are allowed.")

            file_content = await file.read()
            df = pd.read_csv(BytesIO(file_content)) if file.filename.lower().endswith('.csv') else pd.read_excel(BytesIO(file_content))
            df = df.replace({np.nan: None}) # Replace numpy NaN with None

            required_columns = ['plant_name', 'c_s', 'category', 'technology', 'price_group', 'vc_price', 'multiplier', 'volume', 'ppa', 'plant_id']
            if not all(col in df.columns for col in required_columns):
                raise HTTPException(status_code=400, detail=f"Missing required columns. Required: {', '.join(required_columns)}")

            plants_data = []
            for _, row in df.iterrows():
                try:
                    plant_dict = row.to_dict()
                    # Remove backend-managed fields from the uploaded data
                    plant_dict.pop('created_at', None)
                    plant_dict.pop('updated_at', None)

                    # Basic validation using pydantic model
                    PlantEntity(**plant_dict)
                    plants_data.append(plant_dict)
                except Exception as e:
                    logger.warning(f"Skipping invalid plant data: {row.to_dict()}, Error: {e}")
                    continue
            
            if not plants_data:
                raise HTTPException(status_code=400, detail="No valid plant data found in the file")

            result = await self.repository.bulk_upsert_plants(
                plants=plants_data,
                filter_criteria=self._create_plant_upsert_filter
            )

            try:
                from ..services.ppa_details_services import ppa_details_service
                ppa_sync = await ppa_details_service.repository.bulk_upsert_from_plants(plants_data)
                logger.info("PPA details synced after plant file upload | %s", ppa_sync)
            except Exception as ppa_err:
                logger.error("Plant file upload succeeded but PPA sync failed: %s", ppa_err)

            return {
                "message": "File processed successfully",
                "filename": file.filename,
                "total_records": len(plants_data),
                **result
            }
        except Exception as e:

            raise

master_plants_service = MasterPlantsService()
