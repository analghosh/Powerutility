import pytest
from unittest.mock import AsyncMock, MagicMock
from ...services.dashboard_services import dashboard_service
# from services.processing.app.services.dashboard_services import DashboardService

@pytest.mark.asyncio
async def test_demand_data_returns_documents():
    # Arrange
    mock_repo = MagicMock()
    mock_repo.find_many.return_value = [{"_id": "123", "data": "value"}]
    mock_repo.count.return_value = 1

    service = dashboard_service
    service.dashboard_repo = mock_repo

    # Act
    result = await service.demand_data()

    # Assert
    assert result is not None
    assert "documents" in result
    assert result["documents"][0]["data"] == "value"
