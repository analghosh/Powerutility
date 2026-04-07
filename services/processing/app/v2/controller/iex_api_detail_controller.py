from typing import Dict, Any, Optional, Literal
from pydantic import BaseModel, field_validator
import re
from ..services.iex_api_detail_service import iex_api_detail_service

class IexApiDetailCreate(BaseModel):
    exchange: Literal["IEX", "PXIL", "HPX"]
    environment: Literal["UAT", "Production"]

    asset_id: Optional[str] = None
    bid_area_id: Optional[str] = None
    user_id: Optional[str] = None
    participant_id: Optional[str] = None
    portfolio_id: Optional[str] = None
    api_key: Optional[str] = None
    server_type: Optional[str] = None
    server_url: Optional[str] = None
    status: Optional[str] = None
    user_ip_address: Optional[str] = None
    user_mac_address: Optional[str] = None

    @field_validator('user_mac_address')
    def validate_mac_address(cls, v):
        if v and not re.match(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$", v):
            raise ValueError('Invalid MAC address format')
        return v

class IexApiDetailUpdate(BaseModel):
    exchange: Optional[Literal["IEX", "PXIL", "HPX"]] = None
    environment: Optional[Literal["UAT", "PROD"]] = None

    asset_id: Optional[str] = None
    bid_area_id: Optional[str] = None
    user_id: Optional[str] = None
    participant_id: Optional[str] = None
    portfolio_id: Optional[str] = None
    api_key: Optional[str] = None
    server_type: Optional[str] = None
    server_url: Optional[str] = None
    status: Optional[str] = None
    user_ip_address: Optional[str] = None
    user_mac_address: Optional[str] = None

    @field_validator('user_mac_address')
    def validate_mac_address(cls, v):
        if v and not re.match(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$", v):
            raise ValueError('Invalid MAC address format')
        return v

class StatusUpdate(BaseModel):
    status: str

class IexApiDetailController:

    async def get_all(self):
        return await iex_api_detail_service.get_all()

    async def create(self, data: IexApiDetailCreate, user_info: Dict[str, Any]):
        return await iex_api_detail_service.create(data.model_dump(), user_info)

    async def update(self, id: str, data: IexApiDetailUpdate, user_info: Dict[str, Any]):
        return await iex_api_detail_service.update(id, data.model_dump(exclude_unset=True), user_info)

    async def update_status(self, id: str, status_data: StatusUpdate, user_info: Dict[str, Any]):
        return await iex_api_detail_service.update_status(id, status_data.status, user_info)

    async def delete(self, id: str, user_info: Dict[str, Any]):
        return await iex_api_detail_service.delete(id, user_info)

iex_api_detail_controller = IexApiDetailController()
