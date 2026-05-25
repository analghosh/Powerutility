"""Tests for Manual mode switch rejection."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from services.processing.app.v2.services.idf_forecast_switch_service import (
    IdfForecastSwitchService,
    ManualSwitchNotAllowedError,
)


@pytest.mark.asyncio
async def test_set_switch_manual_rejected_without_csv():
    svc = IdfForecastSwitchService()
    svc.parameter_repository = AsyncMock()
    svc.parameter_repository.ensure_idf_forecast_switch_default = AsyncMock()
    svc.parameter_repository.get_by_key = AsyncMock(
        return_value={"para_value": "true", "updated_at": datetime(2026, 5, 15)}
    )
    svc.forecast_repository = AsyncMock()
    svc.forecast_repository.find_by_scheduled_at_and_source = AsyncMock(return_value=[])

    with pytest.raises(ManualSwitchNotAllowedError):
        await svc.set_switch(use_rtm_api=False, updated_by="user1")

    svc.parameter_repository.upsert_by_key.assert_not_called()


@pytest.mark.asyncio
async def test_set_switch_auto_to_auto_skips_validation():
    svc = IdfForecastSwitchService()
    svc.parameter_repository = AsyncMock()
    svc.parameter_repository.ensure_idf_forecast_switch_default = AsyncMock()
    svc.parameter_repository.get_by_key = AsyncMock(
        return_value={"para_value": "true", "updated_at": datetime(2026, 5, 15)}
    )
    svc.parameter_repository.upsert_by_key = AsyncMock()
    svc.forecast_repository = AsyncMock()

    await svc.set_switch(use_rtm_api=True, updated_by="user1")

    svc.forecast_repository.find_by_scheduled_at_and_source.assert_not_called()
