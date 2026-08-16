import logging
from typing import Dict, Any, List
from datetime import datetime
from fastapi import HTTPException, status
from ..repository.iex_api_detail_repository import IexApiDetailRepository
from ..utils.encryption import encrypt_api_key
from ..repository.exchange_config_repository import ExchangeConfigRepository

logger = logging.getLogger(__name__)

# Placeholder handed out in place of a stored API key. The exchange key is a
# production trading credential and is only ever needed server-side, so it never
# leaves the service; the placeholder still lets the UI tell a configured
# credential apart from an empty one.
#
# Dots, not asterisks: the client filters this field through /[^A-Za-z0-9._-]/g and
# validates it against the same character set, so an asterisk mask is stripped to an
# empty string and the edit form then refuses to save with "API Key is required".
API_KEY_MASK = "........"
_MASK_CHARS = {".", "*", "•"}


def is_masked_api_key(value: Any) -> bool:
    """True if the value is a mask placeholder rather than a real API key."""
    text = str(value or "").strip()
    return bool(text) and set(text) <= _MASK_CHARS


class IexApiDetailService:
    def __init__(self):
        self.repository = IexApiDetailRepository()
        self.exchange_config_repo = ExchangeConfigRepository()

    async def get_all(self) -> List[Dict[str, Any]]:
        """Get all IEX API details. The api_key is masked, never returned in plaintext."""
        details = await self.repository.get_all_details()

        for detail in details:
            has_key = bool(detail.get("api_key"))
            detail["api_key"] = API_KEY_MASK if has_key else ""
            detail["has_api_key"] = has_key

        return details

    async def create(self, data: Dict[str, Any], user_info: Dict[str, Any] = None) -> Dict[str, Any]:
        try:
            # Check for duplicate before creating
            existing = await self.repository.check_duplicate(data)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A record with the same asset_id, bid_area_id, user_id, participant_id, portfolio_id, server_type, and server_url already exists"
                )
            
            # Refuse the mask get_all() hands out — storing it would silently
            # configure a credential that can never authenticate to the exchange.
            if is_masked_api_key(data.get("api_key")):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="API key is a masked placeholder; enter the real key"
                )

            # Encrypt api_key before saving
            if data.get("api_key"):
                try:
                    data["api_key"] = encrypt_api_key(data["api_key"])
                except Exception as e:
                    logger.error(f"Failed to encrypt api_key: {e}")
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to encrypt API key"
                    )
            
            # Date handling: use datetime object
            now = datetime.now()
            
            # Audit fields
            data["created_at"] = now

            if data.get("status") == "Active":
                await self.repository.deactivate_other_environment(
                    exchange=data.get("exchange")
                )
            # data["updated_at"] = now
            if user_info:
                data["created_by"] = user_info.get("user_id") or user_info.get("username")
                # data["updated_by"] = user_info.get("user_id") or user_info.get("username")

            result = await self.repository.create_detail(data)
            if result:
                return {"status": "success", "message": "Data Create successfully", "id": str(result.inserted_id)}
            return {"status": "fail", "message": "Data could not Create!"}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error creating Exchange API detail: {e}")
            raise e

    async def update(self, id: str, data: Dict[str, Any], user_info: Dict[str, Any] = None) -> Dict[str, Any]:
        try:
            # Check if document exists and is not soft-deleted
            existing = await self.repository.find_by_id(id, include_deleted=False)
            if not existing:
                # Check if it's soft-deleted
                deleted_record = await self.repository.find_by_id(id, include_deleted=True)
                if deleted_record and deleted_record.get("is_deleted"):
                    raise HTTPException(
                        status_code= status.HTTP_400_BAD_REQUEST,
                        detail="Cannot update a deleted record"
                    )
                raise HTTPException(
                    status_code= status.HTTP_404_NOT_FOUND,
                    detail="Record not found"
                )
            
            # Check for duplicate before updating (excluding current document)
            existing_duplicate = await self.repository.check_duplicate(data, exclude_id=id)
            if existing_duplicate:
                raise HTTPException(
                    status_code= status.HTTP_400_BAD_REQUEST,
                    detail="A record with the same asset_id, bid_area_id, user_id, participant_id, portfolio_id, server_type, and server_url already exists"
                )
            
            now = datetime.now()
            
            update_payload = {
                
                "asset_id": data.get("asset_id"),
                "bid_area_id": data.get("bid_area_id"),
                "exchange": data.get("exchange"),
                "environment": data.get("environment"),
                "user_ip_address": data.get("user_ip_address"),
                "user_mac_address": data.get("user_mac_address"),
                "user_id": data.get("user_id"),
                "participant_id": data.get("participant_id"),
                "portfolio_id": data.get("portfolio_id"),
                "api_key": data.get("api_key"),
                "server_type": data.get("server_type"),
                "server_url": data.get("server_url"),
                "status": data.get("status"),
                "updated_at": now
            }

            # An unchanged edit form posts back the mask from get_all(). Drop it so the
            # stored key survives instead of being overwritten with the placeholder.
            if is_masked_api_key(update_payload.get("api_key")):
                logger.info(f"Ignoring masked api_key on update of {id}; keeping stored key")
                update_payload["api_key"] = None

            # Encrypt api_key if it's being updated
            if update_payload.get("api_key"):
                try:
                    update_payload["api_key"] = encrypt_api_key(update_payload["api_key"])
                except Exception as e:
                    logger.error(f"Failed to encrypt api_key: {e}")
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to encrypt API key"
                    )

            if user_info:
                update_payload["updated_by"] = user_info.get("user_id") or user_info.get("username")
            
            # Remove keys with None values
            update_payload = {k: v for k, v in update_payload.items() if v is not None}
            exchange = (
                    update_payload.get("exchange")
                    or existing.get("exchange")
                    or "IEX"
                ).strip().upper()

            environment = (
                    update_payload.get("environment")
                    or existing.get("environment")
                    or ""
                ).strip().upper()
            status = update_payload.get("status")   
            if status:              
               if status is not None:
                status_lower = str(status).strip().lower()

                if status_lower == "active":
                    update_payload["is_active"] = True

                elif status_lower == "inactive":
                    update_payload["is_active"] = False
      
                if not exchange or not environment:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Exchange or Environment missing"
                    )

                await self.repository.deactivate_other_environment(
                    exchange=str(exchange),
                    exclude_id=id
                )

            result = await self.repository.update_detail(id, update_payload)
            
            if result and result.modified_count > 0:
                exchange_payload = {}
                field_mapping = {                
                "user_id": "LoginUserId",
                "participant_id": "ParticipantId",
                "portfolio_id": "PortfolioId",
                "bid_area_id": "BidAreaId",
                "description": "description",
                "is_active": "is_active",        
                "exchange": "market"
                 }

                for source_key, target_key in field_mapping.items():
                    value = update_payload.get(source_key)
                    if value is not None:
                        exchange_payload[target_key] = value
                        
                update_exchange_result = await self.exchange_config_repo.update_exchange_environment(
                exchange=exchange,
                environment=environment,
                data=exchange_payload
            )

            # ✅ Check result
                active_count = int(update_exchange_result.get("active_modified", 0))
                inactive_count = int(update_exchange_result.get("inactive_modified", 0))
                
                if active_count > 0 or inactive_count > 0:
                    logger.info(f"Exchange config updated: {update_exchange_result}")

                    return {
                        "status": "success",
                        "message": "Data updated successfully",
                        "exchange_update": update_exchange_result
                    }

                else:
                    logger.warning("No exchange config records were modified")

                    return {
                        "status": "success",
                        "message": "No changes detected",
                        "exchange_update": update_exchange_result
                    }
            
            # Check if exists
            existing = await self.repository.find_by_id(id)
            if existing:
                return {"status": "success", "message": "Data Update successfully (No changes detected)"}
                
            return {"status": "fail", "message": "Data could not Update Successfully!"}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating IEX API detail: {e}")
            raise e

    async def update_status(self, id: str, new_status: str, user_info: Dict[str, Any] = None) -> Dict[str, Any]:
        try:
            # Check if document exists and is not soft-deleted
            existing = await self.repository.find_by_id(id, include_deleted=False)
            if not existing:
                # Check if it's soft-deleted
                deleted_record = await self.repository.find_by_id(id, include_deleted=True)
                if deleted_record and deleted_record.get("is_deleted"):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Cannot update status of a deleted record"
                    )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Record not found"
                )
            
            update_data = {
                "status": new_status,
                "updated_at": datetime.now()
            }
            if user_info:
                update_data["updated_by"] = user_info.get("user_id") or user_info.get("username")

            if new_status == "Active":
                exchange = existing.get("exchange")
                environment = existing.get("environment")

                if not exchange or not environment:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Exchange or Environment missing for this record"
                    )

                await self.repository.deactivate_other_environment(
                    exchange=exchange,
                    exclude_id=id
                )

            result = await self.repository.update_detail(id, update_data)
            if result and result.modified_count > 0:
                return {"status": "success", "message": "Status changed Successfully!"}
            return {"status": "fail", "message": "Status could not been updated!"}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating status: {e}")
            raise e

    async def delete(self, id: str, user_info: Dict[str, Any] = None) -> Dict[str, Any]:
        try:
            # Check if document exists and is not already deleted
            existing = await self.repository.find_by_id(id, include_deleted=False)
            if not existing:
                # Check if it's already soft-deleted
                deleted_record = await self.repository.find_by_id(id, include_deleted=True)
                if deleted_record and deleted_record.get("is_deleted"):
                    return {"status": "fail", "message": "Record is already deleted"}
                return {"status": "fail", "message": "Record not found"}
            
            # Get user info for deleted_by field
            deleted_by = None
            if user_info:
                deleted_by = user_info.get("user_id") or user_info.get("username")
            
            # Perform soft delete
            result = await self.repository.delete_detail(id, deleted_by=deleted_by)
            if result and result.modified_count > 0:
                return {"status": "success", "message": "Data Deleted successfully"}
            return {"status": "fail", "message": "Data could not be deleted!"}
        except Exception as e:
            logger.error(f"Error deleting IEX API detail: {e}")
            raise e

iex_api_detail_service = IexApiDetailService()
