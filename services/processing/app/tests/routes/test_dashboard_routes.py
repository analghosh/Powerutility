import pytest
from httpx import AsyncClient
from fastapi import FastAPI
from unittest.mock import AsyncMock
from services.processing.app.api.v1.routes.dashboard_routes import router
from services.processing.app.controller import dashboard_controller
from httpx import ASGITransport

app = FastAPI()
app.include_router(router)

@pytest.mark.asyncio
async def test_demand_route_success(monkeypatch):
    mock_response = {"data": 1, "records": [{"mock": "data"}]}

    monkeypatch.setattr(
        dashboard_controller.dashboard_controller,
        "demand",
        AsyncMock(return_value=mock_response)
    )

    transport = ASGITransport(app=app, raise_app_exceptions=True)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/processing/dashboard/demand")

    assert response.status_code == 200
    json_resp = response.json()
    print(json_resp)
    assert "data" in json_resp
    assert json_resp["data"]["data"] == 1
    assert json_resp["data"]["records"][0]["mock"] == "data"
