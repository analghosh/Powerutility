"""
Unit Tests for Scheduler Service
Tests the application service layer in isolation
"""

import pytest
from unittest.mock import Mock, AsyncMock
from datetime import datetime

from ..application.services.scheduler_service import SchedulerService
from ..shared.exceptions.custom_exceptions import JobNotFoundException, ServiceUnavailableException


class TestSchedulerService:
    """Test cases for SchedulerService"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.mock_cron_manager = Mock()
        self.mock_dummy_data_service = Mock()
        self.scheduler_service = SchedulerService(
            cron_manager=self.mock_cron_manager,
            dummy_data_service=self.mock_dummy_data_service
        )
    
    @pytest.mark.asyncio
    async def test_get_scheduler_status_success(self):
        """Test successful scheduler status retrieval"""
        # Arrange
        mock_jobs_info = {
            'active_jobs': 5,
            'total_jobs': 10,
            'scheduler_running': True
        }
        self.mock_cron_manager.get_all_jobs.return_value = mock_jobs_info
        self.mock_cron_manager.is_running.return_value = True
        
        # Act
        result = await self.scheduler_service.get_scheduler_status()
        
        # Assert
        assert result['status'] == 'active'
        assert result['active_jobs'] == 5
        assert result['total_jobs'] == 10
        assert 'last_activity' in result
        self.mock_cron_manager.get_all_jobs.assert_called_once()
        self.mock_cron_manager.is_running.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_scheduler_status_inactive(self):
        """Test scheduler status when inactive"""
        # Arrange
        mock_jobs_info = {'active_jobs': 0, 'total_jobs': 10}
        self.mock_cron_manager.get_all_jobs.return_value = mock_jobs_info
        self.mock_cron_manager.is_running.return_value = False
        
        # Act
        result = await self.scheduler_service.get_scheduler_status()
        
        # Assert
        assert result['status'] == 'inactive'
        assert result['active_jobs'] == 0
    
    @pytest.mark.asyncio
    async def test_get_all_jobs_success(self):
        """Test successful retrieval of all jobs"""
        # Arrange
        mock_jobs = {'jobs': [{'id': 'job1'}, {'id': 'job2'}]}
        self.mock_cron_manager.get_all_jobs.return_value = mock_jobs
        
        # Act
        result = await self.scheduler_service.get_all_jobs()
        
        # Assert
        assert result == mock_jobs
        self.mock_cron_manager.get_all_jobs.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_job_execution_history_success(self):
        """Test successful job execution history retrieval"""
        # Arrange
        job_type = "test_job"
        limit = 5
        mock_history = [{'execution_id': '1'}, {'execution_id': '2'}]
        self.mock_cron_manager.get_job_execution_history.return_value = mock_history
        
        # Act
        result = await self.scheduler_service.get_job_execution_history(job_type, limit)
        
        # Assert
        assert result == mock_history
        self.mock_cron_manager.get_job_execution_history.assert_called_once_with(job_type, limit)
    
    @pytest.mark.asyncio
    async def test_enable_job_success(self):
        """Test successful job enabling"""
        # Arrange
        job_type = "test_job"
        self.mock_cron_manager.enable_job.return_value = True
        
        # Act
        result = await self.scheduler_service.enable_job(job_type)
        
        # Assert
        assert result is True
        self.mock_cron_manager.enable_job.assert_called_once_with(job_type)
    
    @pytest.mark.asyncio
    async def test_enable_job_not_found(self):
        """Test job enabling when job not found"""
        # Arrange
        job_type = "nonexistent_job"
        self.mock_cron_manager.enable_job.return_value = False
        
        # Act
        result = await self.scheduler_service.enable_job(job_type)
        
        # Assert
        assert result is False
    
    @pytest.mark.asyncio
    async def test_reconcile_and_rerun_crons_success(self):
        """Test successful reconciliation"""
        # Arrange
        mock_result = {'status': 'success', 'message': 'Reconciliation completed'}
        self.mock_dummy_data_service.reconcile_and_rerun_crons.return_value = mock_result
        
        # Act
        result = await self.scheduler_service.reconcile_and_rerun_crons()
        
        # Assert
        assert result == mock_result
        self.mock_dummy_data_service.reconcile_and_rerun_crons.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_service_exception_handling(self):
        """Test exception handling in service methods"""
        # Arrange
        self.mock_cron_manager.get_all_jobs.side_effect = Exception("Database error")
        
        # Act & Assert
        with pytest.raises(Exception) as exc_info:
            await self.scheduler_service.get_scheduler_status()
        
        assert "Database error" in str(exc_info.value)