"""
Unit tests for Demand and Supply Serve Entities
"""

import pytest
from datetime import datetime

from libs.models.demand_serve_entity import (
    DemandServeEntity,
    DemandServeHistoricalEntity
)
from libs.models.supply_serve_entity import (
    SupplyServeEntity,
    SupplyServeHistoricalEntity
)


class TestDemandServeEntity:
    """Test cases for DemandServeEntity"""
    
    def test_create_demand_serve_entity(self):
        """Test creating a demand serve entity with valid data"""
        scheduled_at = datetime(2025, 1, 15, 10, 0, 0)
        
        entity = DemandServeEntity(
            scheduled_at=scheduled_at,
            type="IDF",
            forecast=1500.0,
            actual=1480.0,
            rostering=20.0,
            zone="North",
            udm=1500.0
        )
        
        assert entity.scheduled_at == scheduled_at
        assert entity.type == "IDF"
        assert entity.forecast == 1500.0
        assert entity.actual == 1480.0
        assert entity.rostering == 20.0
        assert entity.zone == "North"
        assert entity.udm == 1500.0
        assert entity.id is not None
    
    def test_demand_serve_entity_defaults(self):
        """Test demand serve entity with default values"""
        scheduled_at = datetime(2025, 1, 15, 10, 0, 0)
        
        entity = DemandServeEntity(scheduled_at=scheduled_at)
        
        assert entity.forecast == 0.0
        assert entity.rostering == 0.0
        assert entity.mape == 0.0
        assert entity.mpe == 0.0
        assert entity.remarks == ""
        assert entity.udm == 0.0
    
    def test_demand_serve_entity_validation(self):
        """Test demand serve entity validation"""
        scheduled_at = datetime(2025, 1, 15, 10, 0, 0)
        
        # Test negative values are rejected
        with pytest.raises(ValueError, match="Demand values should be non-negative"):
            DemandServeEntity(
                scheduled_at=scheduled_at,
                forecast=-100.0
            )
        
        # Test extreme percentage values are rejected
        with pytest.raises(ValueError, match="Percentage values should be reasonable"):
            DemandServeEntity(
                scheduled_at=scheduled_at,
                mape=2000.0  # Unreasonable percentage
            )


class TestSupplyServeEntity:
    """Test cases for SupplyServeEntity"""
    
    def test_create_supply_serve_entity(self):
        """Test creating a supply serve entity with valid data"""
        scheduled_at = datetime(2025, 1, 15, 10, 0, 0)
        
        entity = SupplyServeEntity(
            scheduled_at=scheduled_at,
            plant_name="Thermal Plant A",
            dc_sg="DC",
            volume=500.0,
            fc_price=2.5,
            px_price=3.2,
            vc_price=1.8,
            category="Thermal",
            technology="Coal",
            price_group="A",
            c_s="Central"
        )
        
        assert entity.scheduled_at == scheduled_at
        assert entity.plant_name == "Thermal Plant A"
        assert entity.dc_sg == "DC"
        assert entity.volume == 500.0
        assert entity.fc_price == 2.5
        assert entity.px_price == 3.2
        assert entity.vc_price == 1.8
        assert entity.category == "Thermal"
        assert entity.technology == "Coal"
        assert entity.price_group == "A"
        assert entity.c_s == "Central"
        assert entity.id is not None
    
    def test_supply_serve_entity_defaults(self):
        """Test supply serve entity with default values"""
        scheduled_at = datetime(2025, 1, 15, 10, 0, 0)
        
        entity = SupplyServeEntity(scheduled_at=scheduled_at)
        
        assert entity.tm == 0.0
        assert entity.multiplier == ""
        assert entity.ramping == ""
    
    def test_supply_serve_entity_validation(self):
        """Test supply serve entity validation"""
        scheduled_at = datetime(2025, 1, 15, 10, 0, 0)
        
        # Test negative values are rejected
        with pytest.raises(ValueError, match="Supply values should be non-negative"):
            SupplyServeEntity(
                scheduled_at=scheduled_at,
                volume=-100.0
            )
        
        # Test invalid DC/SG type
        with pytest.raises(ValueError, match="DC/SG type must be either 'DC' or 'SG'"):
            SupplyServeEntity(
                scheduled_at=scheduled_at,
                dc_sg="INVALID"
            )
