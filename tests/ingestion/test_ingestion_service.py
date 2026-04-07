"""
Tests for Ingestion Service
"""

import pytest


class TestIngestionService:
    """Test cases for Ingestion Service"""
    
    def test_health_check_structure(self, test_client):
        """Test health check response structure"""
        response = test_client.get("/health")
        
        assert response.status_code == 200
        response_data = response.json()
        assert response_data["status"] == "healthy"
        assert response_data["service"] == "ingestion-service"
        assert "version" in response_data
        assert "timestamp" in response_data


if __name__ == "__main__":
    pytest.main([__file__]) 