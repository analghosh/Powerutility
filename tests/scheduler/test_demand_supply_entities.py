"""
Unit tests for Scheduler Demand and Supply Serve Repositories
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
from bson import ObjectId

from libs.models.demand_serve_entity import (
    DemandServeEntity,
    DemandServeHistoricalEntity
)
from libs.models.supply_serve_entity import (
    SupplyServeEntity,
    SupplyServeHistoricalEntity
)


class TestDemandServeRepository:
    """Test cases for DemandServeRepository"""
    
    @patch('services.scheduler.app.repository.demand_serve_repository.demand_serve_repository')
    def test_insert_demand_serve(self, mock_repo):
        """Test inserting demand serve record"""
        # Setup mock
        mock_repo.insert_demand_serve.return_value = "test_id_123"
        
        # Create entity
        scheduled_at = datetime(2025, 1, 15, 10, 0, 0)
        entity = DemandServeEntity(
            scheduled_at=scheduled_at,
            type="IDF",
            forecast=1500.0
        )
        
        # Import and test
        from services.scheduler.app.repository.demand_serve_repository import demand_serve_repository
        result_id = demand_serve_repository.insert_demand_serve(entity)
        
        # Verify
        assert result_id == "test_id_123"
        mock_repo.insert_demand_serve.assert_called_once_with(entity)
    
    @patch('services.scheduler.app.repository.demand_serve_repository.demand_serve_repository')
    def test_delete_demand_serve_by_date(self, mock_repo):
        """Test deleting demand serve records by date"""
        # Setup mock
        mock_result = {
            "deleted_count": 5,
            "deleted_records": [
                {"_id": ObjectId(), "scheduled_at": datetime(2025, 1, 15, 10, 0, 0), "type": "IDF", "forecast": 1500.0},
                {"_id": ObjectId(), "scheduled_at": datetime(2025, 1, 15, 11, 0, 0), "type": "IDF", "forecast": 1600.0},
                {"_id": ObjectId(), "scheduled_at": datetime(2025, 1, 15, 12, 0, 0), "type": "DAF", "forecast": 1700.0},
                {"_id": ObjectId(), "scheduled_at": datetime(2025, 1, 15, 13, 0, 0), "type": "IDF", "forecast": 1800.0},
                {"_id": ObjectId(), "scheduled_at": datetime(2025, 1, 15, 14, 0, 0), "type": "DAF", "forecast": 1900.0}
            ]
        }
        mock_repo.delete_demand_serve_by_date.return_value = mock_result
        
        scheduled_at = datetime(2025, 1, 15, 10, 0, 0)
        
        # Import and test
        from services.scheduler.app.repository.demand_serve_repository import demand_serve_repository
        result = demand_serve_repository.delete_demand_serve_by_date(scheduled_at)
        
        # Verify
        assert result["deleted_count"] == 5
        assert len(result["deleted_records"]) == 5
        mock_repo.delete_demand_serve_by_date.assert_called_once_with(scheduled_at)
    
    @patch('services.scheduler.app.repository.demand_serve_repository.demand_serve_repository')
    def test_count_demand_serve(self, mock_repo):
        """Test counting demand serve records"""
        # Setup mock
        mock_repo.count_demand_serve.return_value = 100
        
        # Import and test
        from services.scheduler.app.repository.demand_serve_repository import demand_serve_repository
        count = demand_serve_repository.count_demand_serve()
        
        # Verify
        assert count == 100
        mock_repo.count_demand_serve.assert_called_once()


class TestSupplyServeRepository:
    """Test cases for SupplyServeRepository"""
    
    @patch('services.scheduler.app.repository.supply_serve_repository.supply_serve_repository')
    def test_insert_supply_serve(self, mock_repo):
        """Test inserting supply serve record"""
        # Setup mock
        mock_repo.insert_supply_serve.return_value = "test_id_456"
        
        # Create entity
        scheduled_at = datetime(2025, 1, 15, 10, 0, 0)
        entity = SupplyServeEntity(
            scheduled_at=scheduled_at,
            plant_name="Thermal Plant A",
            dc_sg="DC",
            volume=500.0
        )
        
        # Import and test
        from services.scheduler.app.repository.supply_serve_repository import supply_serve_repository
        result_id = supply_serve_repository.insert_supply_serve(entity)
        
        # Verify
        assert result_id == "test_id_456"
        mock_repo.insert_supply_serve.assert_called_once_with(entity)
    
    @patch('services.scheduler.app.repository.supply_serve_repository.supply_serve_repository')
    def test_delete_supply_serve_by_date(self, mock_repo):
        """Test deleting supply serve records by date"""
        # Setup mock
        mock_result = {
            "deleted_count": 3,
            "deleted_records": [
                {"_id": ObjectId(), "scheduled_at": datetime(2025, 1, 15, 10, 0, 0), "plant_name": "Plant A", "volume": 500.0},
                {"_id": ObjectId(), "scheduled_at": datetime(2025, 1, 15, 11, 0, 0), "plant_name": "Plant B", "volume": 600.0},
                {"_id": ObjectId(), "scheduled_at": datetime(2025, 1, 15, 12, 0, 0), "plant_name": "Plant C", "volume": 700.0}
            ]
        }
        mock_repo.delete_supply_serve_by_date.return_value = mock_result
        
        scheduled_at = datetime(2025, 1, 15, 10, 0, 0)
        
        # Import and test
        from services.scheduler.app.repository.supply_serve_repository import supply_serve_repository
        result = supply_serve_repository.delete_supply_serve_by_date(scheduled_at)
        
        # Verify
        assert result["deleted_count"] == 3
        assert len(result["deleted_records"]) == 3
        mock_repo.delete_supply_serve_by_date.assert_called_once_with(scheduled_at)
    
    @patch('services.scheduler.app.repository.supply_serve_repository.supply_serve_repository')
    def test_count_supply_serve(self, mock_repo):
        """Test counting supply serve records"""
        # Setup mock
        mock_repo.count_supply_serve.return_value = 75
        
        # Import and test
        from services.scheduler.app.repository.supply_serve_repository import supply_serve_repository
        count = supply_serve_repository.count_supply_serve()
        
        # Verify
        assert count == 75
        mock_repo.count_supply_serve.assert_called_once()
