# from typing import Any, Dict, Optional
# from bson import ObjectId
# from ..repository.plants_repository import ServeMasterPlantsRepository
# import logging

# logger = logging.getLogger(__name__)


# class ServeMasterPlantsService:
#     """
#     Business logic for serve_master_plants + a utility to push plant_id to serve_supply_table.
#     """
#     def __init__(
#         self,
#         repo: ServeMasterPlantsRepository = ServeMasterPlantsRepository(),
#     ):
#         self.repo = repo

#     # ---------- CRUD ----------
#     def create(self, payload: Dict[str, Any]) -> str:
#         # basic normalization: strip strings
#         for k, v in list(payload.items()):
#             if isinstance(v, str):
#                 payload[k] = v.strip()
#         new_id = self.repo.create(payload)
#         logger.info("Master plant created | _id=%s plant_id=%s", new_id, payload.get("plant_id"))
#         return new_id

#     def get(self, _id: str) -> Optional[Dict[str, Any]]:
#         return self.repo.get(_id)

#     def list(self, filters: Optional[Dict[str, Any]], page: int, page_size: int) -> Dict[str, Any]:
#         filters = filters or {}
#         # allow fuzzy filter by keys
#         return self.repo.list(filters=filters, page=page, page_size=page_size, sort=[("plant_name", 1)])

#     def update(self, _id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
#         for k, v in list(updates.items()):
#             if isinstance(v, str):
#                 updates[k] = v.strip()
#         doc = self.repo.update(_id, updates)
#         if doc:
#             logger.info("Master plant updated | _id=%s", _id)
#         return doc

#     def delete(self, _id: str) -> bool:
#         ok = self.repo.delete(_id)
#         if ok:
#             logger.info("Master plant deleted | _id=%s", _id)
#         return ok

#     # # ---------- Utility: push plant_id into serve_supply_table without indexes ----------
#     # def apply_plant_ids_to_supply(self) -> Dict[str, Any]:
#     #     """
#     #     For each doc in serve_master_plants, find matching supply docs by the 4 keys and set plant_id.
#     #     This avoids $merge uniqueness checks (no indexes required).
#     #     """
#     #     master_coll = self.repo.collection
#     #     supply_coll = self.supply_repo.collection

#     #     matched = 0
#     #     modified = 0

#     #     # Stream masters to avoid high memory
#     #     for m in master_coll.find({}, {"plant_id": 1, "plant_name": 1, "c_s": 1, "category": 1, "technology": 1}):
#     #         q = {
#     #             "plant_name": m.get("plant_name"),
#     #             "c_s": m.get("c_s"),
#     #             "category": m.get("category"),
#     #             "technology": m.get("technology"),
#     #         }
#     #         # update many supply docs (if any) with the plant_id
#     #         r = supply_coll.update_many(q, {"$set": {"plant_id": m.get("plant_id")}})
#     #         if r.matched_count:
#     #             matched += r.matched_count
#     #             modified += r.modified_count

#     #     return {"matched": matched, "modified": modified}


from typing import Any, Dict, List, Optional
import logging
from ..repository.plants_repository import ServeMasterPlantsRepository
from ..repository.audit_repository import AuditRepository

from fastapi import UploadFile, HTTPException
import pandas as pd
from io import BytesIO
from libs.models.plant_entity import PlantEntity

# Import audit logger
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
from libs.audit_queue.simple_logger import AuditLogger


logger = logging.getLogger(__name__)


class PayloadMapper:
    """
    Simple payload mapper to handle field name transformations between legacy and entity formats
    """
    
    def __init__(self):
        # Field mapping from legacy format to entity format
        self.field_mapping = {
            # Legacy field name -> Entity field name
            'plant_id': 'plant_id',  # Keep as is
            'TM': 'tm',
            'FC_price': 'fc_price',
            'VC_price': 'vc_price', 
            'PX_price': 'px_price',
            'dc_sg': 'dc_sg',  # Keep as is
        }
        
        # Reverse mapping for output (entity -> legacy format)
        self.reverse_mapping = {v: k for k, v in self.field_mapping.items()}
    
    def transform_input_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform input payload from legacy format to entity format
        
        Args:
            payload: Input payload in legacy format
            
        Returns:
            Transformed payload in entity format
        """
        if not payload:
            return payload
        
        transformed = {}
        
        for key, value in payload.items():
            # Check if this legacy field should be mapped to entity format
            if key in self.field_mapping:
                # Map to entity field name
                entity_key = self.field_mapping[key]
                transformed[entity_key] = value
            else:
                # Keep as is
                transformed[key] = value
        
        return transformed
    
    def transform_output_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform output payload from entity format to legacy format
        
        Args:
            payload: Output payload in entity format
            
        Returns:
            Transformed payload in legacy format
        """
        if not payload:
            return payload
        
        transformed = {}
        
        for key, value in payload.items():
            # Check if this entity field should be mapped to legacy format
            if key in self.reverse_mapping:
                # Map to legacy field name
                legacy_key = self.reverse_mapping[key]
                transformed[legacy_key] = value
            else:
                # Keep as is
                transformed[key] = value
        
        return transformed


class ServeMasterPlantsService:
    """
    Business logic for serve_master_plants + audit logging for user actions.
    """
    def __init__(
        self,
        repo: ServeMasterPlantsRepository = ServeMasterPlantsRepository(),
        audit_repo: AuditRepository = AuditRepository(),
    ):
        self.repo = repo
        self.audit_repo = audit_repo
        self.payload_mapper = PayloadMapper()

    # ---------- helpers ----------
    def _actor(self, actor: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        actor = actor or {}
        return {
            "user_id": actor.get("user_id"),
            "username": actor.get("username") or "anonymous",
        }

    # ---------- CRUD ----------
    def create(self, payload: Dict[str, Any], actor: Optional[Dict[str, Any]], request_info: Dict[str, Any]) -> str:
        try:
            # Transform input payload from legacy format to entity format
            transformed_payload = self.payload_mapper.transform_input_payload(payload)
            
            # Clean up string values
            cleaned_payload = {}
            for k, v in transformed_payload.items():
                if isinstance(v, str):
                    cleaned_payload[k] = v.strip()
                else:
                    cleaned_payload[k] = v
            
            # Create the document directly without using PlantEntity model
            new_id = self.repo.create(cleaned_payload)

            a = self._actor(actor)
            audit_logger.log_user_action(
                service="processing",
                user_id=a["user_id"],
                action="master_plant_create",
                message=f"Master plant operation successful: master_plant_create",
                username=a["username"],
                ip_address=request_info.get("ip_address"),
                user_agent=request_info.get("user_agent"),
                resource="serve_master_plants",
                resource_id=new_id,
                payload=cleaned_payload,
                success=True
            )
            logger.info("Master plant created | _id=%s plant_id=%s", new_id, cleaned_payload.get("plant_id"))
            return new_id
        except Exception as e:
            a = self._actor(actor)
            audit_logger.log_user_action(
                service="processing",
                user_id=a["user_id"],
                action="master_plant_create",
                message=f"Master plant operation failed: master_plant_create - {str(e)}",
                username=a["username"],
                ip_address=request_info.get("ip_address"),
                user_agent=request_info.get("user_agent"),
                resource="serve_master_plants",
                payload=payload,
                success=False,
                error_message=str(e)
            )
            raise

    def get(self, _id: str, actor: Optional[Dict[str, Any]], request_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            doc = self.repo.find_by_id(_id)
            
            # Transform output payload to legacy format
            transformed_doc = self.payload_mapper.transform_output_payload(doc) if doc else None
            
            a = self._actor(actor)
            audit_logger.log_user_action(
                service="processing",
                user_id=a["user_id"],
                action="master_plant_get",
                message=f"Master plant get successful: {_id}",
                username=a["username"],
                ip_address=request_info.get("ip_address"),
                user_agent=request_info.get("user_agent"),
                resource="serve_master_plants",
                resource_id=_id,
                found=bool(doc),
                success=True
            )
            return transformed_doc
        except Exception as e:
            a = self._actor(actor)
            audit_logger.log_user_action(
                service="processing",
                user_id=a["user_id"],
                action="master_plant_get",
                message=f"Master plant get failed: {_id} - {str(e)}",
                username=a["username"],
                ip_address=request_info.get("ip_address"),
                user_agent=request_info.get("user_agent"),
                resource="serve_master_plants",
                resource_id=_id,
                success=False,
                error_message=str(e)
            )
            raise

    def list(self, filters: Optional[Dict[str, Any]], page: int, page_size: int,
             actor: Optional[Dict[str, Any]], request_info: Dict[str, Any]) -> Dict[str, Any]:
        try:
            filters = filters or {}
            result = self.repo.list(filters=filters, page=page, page_size=page_size, sort=[("plant_name", 1)])
            
            # Transform items in the list to legacy format
            if 'items' in result and result['items']:
                transformed_items = []
                for item in result['items']:
                    transformed_item = self.payload_mapper.transform_output_payload(item)
                    transformed_items.append(transformed_item)
                result['items'] = transformed_items
            
            a = self._actor(actor)
            audit_logger.log_user_action(
                service="processing",
                user_id=a["user_id"],
                action="master_plant_list",
                message=f"Master plant list successful: {len(result.get('items', []))} items returned",
                username=a["username"],
                ip_address=request_info.get("ip_address"),
                user_agent=request_info.get("user_agent"),
                resource="serve_master_plants",
                filters=filters,
                page=page,
                page_size=page_size,
                returned=len(result.get("items", [])),
                total=result.get("total"),
                success=True
            )
            return result
        except Exception as e:
            a = self._actor(actor)
            audit_logger.log_user_action(
                service="processing",
                user_id=a["user_id"],
                action="master_plant_list",
                message=f"Master plant list failed: {str(e)}",
                username=a["username"],
                ip_address=request_info.get("ip_address"),
                user_agent=request_info.get("user_agent"),
                resource="serve_master_plants",
                filters=filters,
                page=page,
                page_size=page_size,
                success=False,
                error_message=str(e)
            )
            raise

    def update(self, _id: str, updates: Dict[str, Any],
               actor: Optional[Dict[str, Any]], request_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            # Transform input updates from legacy format to entity format
            transformed_updates = self.payload_mapper.transform_input_payload(updates)
            
            # Clean up string values and filter out None values and immutable fields
            cleaned_updates = {}
            for k, v in transformed_updates.items():
                if v is not None and k != '_id':  # Only include non-None values and exclude _id
                    if isinstance(v, str):
                        cleaned_updates[k] = v.strip()
                    else:
                        cleaned_updates[k] = v
            
            # Only proceed if there are actual updates
            if not cleaned_updates:
                # Return the existing document if no updates
                existing_doc = self.repo.find_by_id(_id)
                return existing_doc if existing_doc else None
            
            # Update the document with only the provided fields
            updated_doc = self.repo.update(_id, cleaned_updates)
            
            if not updated_doc:
                return None
                
            # Transform the updated document to legacy format
            transformed_doc = self.payload_mapper.transform_output_payload(updated_doc)
            
            a = self._actor(actor)
            audit_logger.log_user_action(
                service="processing",
                user_id=a["user_id"],
                action="master_plant_update",
                message=f"Master plant update successful: {_id}",
                username=a["username"],
                ip_address=request_info.get("ip_address"),
                user_agent=request_info.get("user_agent"),
                resource="serve_master_plants",
                resource_id=_id,
                updates=cleaned_updates,
                found=bool(updated_doc),
                success=bool(updated_doc)
            )
            return transformed_doc
        except Exception as e:
            a = self._actor(actor)
            audit_logger.log_user_action(
                service="processing",
                user_id=a["user_id"],
                action="master_plant_update",
                message=f"Master plant update failed: {_id} - {str(e)}",
                username=a["username"],
                ip_address=request_info.get("ip_address"),
                user_agent=request_info.get("user_agent"),
                resource="serve_master_plants",
                resource_id=_id,
                updates=updates,
                success=False,
                error_message=str(e)
            )
            raise

    def delete(self, _id: str, actor: Optional[Dict[str, Any]], request_info: Dict[str, Any]) -> bool:
        try:
            ok = self.repo.delete(_id)
            a = self._actor(actor)
            audit_logger.log_user_action(
                service="processing",
                user_id=a["user_id"],
                action="master_plant_delete",
                message=f"Master plant delete {'successful' if ok else 'failed'}: {_id}",
                username=a["username"],
                ip_address=request_info.get("ip_address"),
                user_agent=request_info.get("user_agent"),
                resource="serve_master_plants",
                resource_id=_id,
                success=ok
            )
            return ok
        except Exception as e:
            a = self._actor(actor)
            audit_logger.log_user_action(
                service="processing",
                user_id=a["user_id"],
                action="master_plant_delete",
                message=f"Master plant delete failed: {_id} - {str(e)}",
                username=a["username"],
                ip_address=request_info.get("ip_address"),
                user_agent=request_info.get("user_agent"),
                resource="serve_master_plants",
                resource_id=_id,
                success=False,
                error_message=str(e)
            )
            raise

    def create_plant_upsert_filter(self, plant: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create filter criteria for plant upsert based on sector, state_ut, and plant_name combination
        
        Args:
            plant: Plant document dictionary
            
        Returns:
            Filter dictionary for finding existing plants
        """
        return {
            "technology": plant.get("technology"),
            "c_s": plant.get("c_s"),
            "plant_name": plant.get("plant_name")
        }
    
    async def process_plant_file(self, file: UploadFile) -> Dict[str, Any]:
        """
        Process uploaded CSV or XLSX file and store plant data
        
        Args:
            file: Uploaded file (CSV or XLSX)
            
        Returns:
            Dictionary with processing results
        """
        try:
            # Validate file type
            if not file.filename or not self._is_valid_file_type(file.filename):
                raise HTTPException(
                    status_code=400, 
                    detail="Invalid file type. Only CSV and XLSX files are allowed."
                )
            
            # Read file content
            file_content = await file.read()
            
            # Parse file based on type
            if file.filename.lower().endswith('.csv'):
                df = pd.read_csv(BytesIO(file_content))
            else:  # XLSX
                df = pd.read_excel(BytesIO(file_content))
            
            # Validate minimum required columns (at least one identifier column)
            minimum_required = ['plant_name', 'c_s', 'technology','vc_price','plant_id']
            missing_required = [col for col in minimum_required if col not in df.columns]
            
            if missing_required:
                raise HTTPException(
                    status_code=400,
                    detail=f"Missing minimum required columns: {', '.join(missing_required)}. At least 'plant_name' is required."
                )
            
            # Define field mapping from Excel columns to PlantEntity fields
            # Since Excel column names match entity field names, we use direct mapping
            field_mapping = {
                # Excel Column Name -> PlantEntity Field Name (same names)
                'plant_name': 'plant_name',
                'c_s': 'c_s',
                'category': 'category',
                'technology': 'technology',
                'price_group': 'price_group',
                'ramping': 'ramping',
                'tm': 'tm',
                'fc_price': 'fc_price',
                'px_price': 'px_price',
                'vc_price': 'vc_price',
                'multiplier': 'multiplier',
                'volume': 'volume',
                'ppa': 'ppa',
                'plant_id': 'plant_id'
            }
            
            # Define default values based on field types
            default_values = {
                'plant_name': '',
                'c_s': '',
                'category': '',
                'technology': '',
                'price_group': '',
                'ramping': None,
                'tm': None,
                'fc_price': 0.0,
                'px_price': 0.0,
                'vc_price': 0.0,
                'multiplier': None,
                'volume': 0.0,
                'ppa': '',
                'plant_id': ''
            }
            
            # Convert DataFrame to list of dictionaries
            plants_data = []
            for _, row in df.iterrows():
                plant_dict = {}
                
                # Map Excel columns to PlantEntity fields
                for excel_col, entity_field in field_mapping.items():
                    if excel_col in df.columns:
                        value = row[excel_col]
                        # Check if value is valid (not NaN and not empty string)
                        value_str = str(value).strip()
                        if value_str and value_str != 'nan' and value_str != '':
                            # Convert based on expected data type
                            if entity_field in ['fc_price', 'px_price', 'vc_price', 'volume', 'ramping', 'tm', 'multiplier']:
                                try:
                                    plant_dict[entity_field] = float(value)
                                except (ValueError, TypeError):
                                    plant_dict[entity_field] = None
                            else:
                                plant_dict[entity_field] = str(value).strip()
                        else:
                            plant_dict[entity_field] = default_values[entity_field]
                    else:
                        plant_dict[entity_field] = default_values[entity_field]
                
                # Ensure all required fields are present with defaults
                for field, default_value in default_values.items():
                    if field not in plant_dict:
                        plant_dict[field] = default_value
                
                # Validate plant data
                try:
                    # Create a temporary entity for validation
                    temp_entity = PlantEntity(**plant_dict)
                    plants_data.append(plant_dict)
                except Exception as e:
                    logger.warning(f"Skipping invalid plant data: {plant_dict}, Error: {e}")
                    continue
            if not plants_data:
                raise HTTPException(
                    status_code=400,
                    detail="No valid plant data found in the file"
                )
            
            # Store plants in database using bulk upsert with custom filter criteria
            result = self.repo.bulk_upsert_plants(
                plants=plants_data,
                filter_criteria=self.create_plant_upsert_filter
            )
            
            logger.info(f"Successfully processed plant file: {result}")
            
            return {
                "message": "File processed successfully",
                "filename": file.filename,
                "total_records": len(plants_data),
                "inserted": result["inserted"],
                "updated": result["updated"],
                "total_processed": result["total"]
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error processing plant file: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Internal server error: {str(e)}"
            )
    
    def get_plants(
        self, 
        filter_by: Optional[str] = None, 
        filter_value: Optional[str] = None,
        skip: int = 0, 
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get plants with optional filtering and pagination
        
        Args:
            filter_by: Field to filter by (name, state, owner, sector, type)
            filter_value: Value to filter by
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            List of plant dictionaries
        """
        try:
            # Define filter field mapping
            filter_mapping = {
                'name': 'plant_name',
                'state': 'state_ut', 
                'owner': 'owner',
                'sector': 'sector',
                'type': 'plant_type'
            }
            
            # Build filter criteria
            filter_criteria = {}
            if filter_by and filter_value:
                if filter_by not in filter_mapping:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid filter_by value. Must be one of: {', '.join(filter_mapping.keys())}"
                    )
                filter_criteria[filter_mapping[filter_by]] = filter_value
            
            # Get plants from repository
            result = self.repo.list(
                filters=filter_criteria,
                page=(skip // limit) + 1,
                page_size=limit
            )
            raw_plants = result.get("items", [])
            
            # Convert raw MongoDB documents to PlantEntity instances and transform to legacy format
            plants = []
            for raw_plant in raw_plants:
                plant_entity = PlantEntity(**raw_plant)
                plant_dict = plant_entity.model_dump()
                # Transform to legacy format
                transformed_plant = self.payload_mapper.transform_output_payload(plant_dict)
                plants.append(transformed_plant)
            
            return plants
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error fetching plants: {e}")
            raise HTTPException(
                status_code=500,
                detail="Error fetching plants"
            )
    
    def _is_valid_file_type(self, filename: str) -> bool:
        """Check if file type is valid (CSV or XLSX)"""
        if not filename:
            return False
        return filename.lower().endswith(('.csv', '.xlsx'))


serve_master_plants_services = ServeMasterPlantsService()