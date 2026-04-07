import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from services.processing.app.controller.dashboard_controller import DashboardController

@pytest.mark.asyncio
async def test_demand_success():
    controller = DashboardController()
    controller.dashboard_service = MagicMock()
    
    # ✅ ONLY mock the core service return
    controller.dashboard_service.demand_data = AsyncMock(
        return_value=[{"key": "value"}]
    )

    response = await controller.demand()

    assert "data" in response
    assert "records" in response
    assert response["data"] == 1
    assert isinstance(response["records"], list)
    assert response["records"][0]["key"] == "value"



@pytest.mark.asyncio
async def test_demand_raises_exception():
    controller = DashboardController()
    controller.dashboard_service = MagicMock()
    controller.dashboard_service.demand_data = AsyncMock(side_effect=Exception("DB Error"))

    with pytest.raises(HTTPException):
        await controller.demand()