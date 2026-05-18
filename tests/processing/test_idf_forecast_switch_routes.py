"""
Tests for IDF forecast switch API (service + routes).
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from libs.audit_queue.simple_logger import AuditLogger
from services.processing.app.v2.api.router.idf_forecast_switch_routes import router
from services.processing.app.v2.dependencies import get_user_context
from services.processing.app.core.dependencies import get_audit_logger


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)

    mock_audit = MagicMock(spec=AuditLogger)
    mock_audit.log_user_action = MagicMock()
    mock_audit.log_error_event = MagicMock()

    app.dependency_overrides[get_user_context] = lambda: {
        "user_id": "test-user",
        "correlation_id": "corr-1",
    }
    app.dependency_overrides[get_audit_logger] = lambda: mock_audit

    with TestClient(app) as test_client:
        yield test_client, mock_audit


@pytest.mark.asyncio
async def test_service_get_switch_default():
    from services.processing.app.v2.services.idf_forecast_switch_service import (
        IdfForecastSwitchService,
    )

    svc = IdfForecastSwitchService()
    svc.parameter_repository = AsyncMock()
    svc.parameter_repository.ensure_idf_forecast_switch_default = AsyncMock()
    svc.parameter_repository.get_by_key = AsyncMock(
        return_value={"para_value": "true", "updated_at": datetime(2026, 5, 15, 10, 0, 0)}
    )

    data = await svc.get_switch()

    assert data["use_rtm_api"] is True
    assert data["active_source"] == "rtm_forecast_api"
    assert data["para_value"] == "true"


@pytest.mark.asyncio
async def test_service_set_switch_false():
    from services.processing.app.v2.services.idf_forecast_switch_service import (
        IdfForecastSwitchService,
    )

    svc = IdfForecastSwitchService()
    svc.parameter_repository = AsyncMock()
    svc.parameter_repository.upsert_by_key = AsyncMock()
    svc.parameter_repository.ensure_idf_forecast_switch_default = AsyncMock()
    svc.parameter_repository.get_by_key = AsyncMock(
        return_value={"para_value": "false", "updated_at": datetime(2026, 5, 15, 10, 0, 0)}
    )

    data = await svc.set_switch(use_rtm_api=False, updated_by="op1")

    svc.parameter_repository.upsert_by_key.assert_called_once()
    assert data["use_rtm_api"] is False
    assert data["active_source"] == "csv"
    assert data["updated_by"] == "op1"


def test_get_switch_route(client):
    test_client, mock_audit = client
    mock_data = {
        "use_rtm_api": True,
        "active_source": "rtm_forecast_api",
        "para_key": "idf_forecast_switch",
        "para_value": "true",
        "updated_at": "2026-05-15T10:00:00",
    }

    with patch(
        "services.processing.app.v2.api.router.idf_forecast_switch_routes.idf_forecast_switch_controller.get_switch",
        new=AsyncMock(return_value=mock_data),
    ):
        response = test_client.get("/idf-forecast/switch")

    assert response.status_code == 200
    body = response.json()
    assert body["use_rtm_api"] is True
    assert body["active_source"] == "rtm_forecast_api"
    mock_audit.log_user_action.assert_called_once()
    assert mock_audit.log_user_action.call_args.kwargs.get("action") == "idf_forecast_switch_get"


def test_put_switch_route(client):
    test_client, mock_audit = client
    mock_data = {
        "use_rtm_api": False,
        "active_source": "csv",
        "para_key": "idf_forecast_switch",
        "para_value": "false",
        "updated_at": "2026-05-15T10:00:00",
        "updated_by": "test-user",
    }

    with patch(
        "services.processing.app.v2.api.router.idf_forecast_switch_routes.idf_forecast_switch_controller.set_switch",
        new=AsyncMock(return_value=mock_data),
    ):
        response = test_client.put("/idf-forecast/switch", json={"use_rtm_api": False})

    assert response.status_code == 200
    assert response.json()["use_rtm_api"] is False
    mock_audit.log_user_action.assert_called_once()
    assert mock_audit.log_user_action.call_args.kwargs.get("action") == "idf_forecast_switch_update"


def test_put_switch_invalid_body_422(client):
    test_client, _ = client
    response = test_client.put("/idf-forecast/switch", json={})
    assert response.status_code == 422
