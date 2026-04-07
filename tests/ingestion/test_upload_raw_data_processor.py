"""
Unit tests for Upload Raw Data Processor
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime, timedelta

from services.ingestion.app.services.upload_raw_data_processor import UploadRawDataProcessor
from libs.enum.raw_data_status import RawDataStatus


class TestUploadRawDataProcessor:
    """Test cases for Upload Raw Data Processor"""

    @pytest.fixture(autouse=True)
    def setup_service(self, mock_raw_data_service, mock_raw_data_processor):
        """Set up service instance with mocked dependencies"""
        self.service = UploadRawDataProcessor()
        self.mock_raw_data_service = mock_raw_data_service
        self.mock_raw_data_processor = mock_raw_data_processor

    @pytest.mark.asyncio
    async def test_process_uploaded_file_success(self, mock_file_storage_service, mock_upload_tracking_repository):
        """Test successful file processing"""
        # Test data
        upload_id = "test_upload_123"
        data_type = "demand"
        provider_type = "test_data"
        
        # Mock file content
        test_file_content = "timeblock,value\n1,100.5\n2,200.0"
        
        with patch('services.ingestion.app.services.upload_raw_data_processor.file_storage_service', mock_file_storage_service), \
             patch('services.ingestion.app.services.upload_raw_data_processor.RawDataService', return_value=self.mock_raw_data_service), \
             patch('services.ingestion.app.services.upload_raw_data_processor.RawDataProcessor', return_value=self.mock_raw_data_processor), \
             patch('services.ingestion.app.services.upload_raw_data_processor.UploadTrackingRepository', return_value=mock_upload_tracking_repository.return_value), \
             patch("builtins.open", mock_open(read_data=test_file_content)):
            result = await self.service.process_uploaded_file(upload_id, data_type, provider_type)
        
        # Verify result
        assert result["success"] is True
        assert result["upload_id"] == upload_id
        assert result["document_id"] == "test_doc_123"
        assert result["total_records"] == 2
        
        # Verify upload record was retrieved
        mock_upload_tracking_repository.return_value.get_upload_by_id.assert_called_once_with(upload_id)
        
        # Verify file processing was called
        self.mock_raw_data_service.process_csv_upload.assert_called_once()
        
        # Verify status updates
        assert mock_upload_tracking_repository.return_value.update_upload_status.call_count >= 2  # PROCESSING and COMPLETED

    @pytest.mark.asyncio
    async def test_process_uploaded_file_file_not_found(self):
        """Test file processing when file is not found"""
        upload_id = "test_upload_123"
        data_type = "demand"
        provider_type = "test_data"
        
        # Mock file not found
        self.mock_file_storage.get_file_path.return_value = None
        
        with patch('services.ingestion.app.services.upload_raw_data_processor.file_storage_service', self.mock_file_storage), \
             patch.object(self.service, 'raw_data_service', self.mock_raw_data_service_instance), \
             patch.object(self.service, 'raw_data_processor', self.mock_raw_data_processor_instance), \
             patch.object(self.service, 'upload_tracking_repository', self.mock_upload_repo_instance):
            result = await self.service.process_uploaded_file(upload_id, data_type, provider_type)
        
        # Verify error result
        assert result["success"] is False
        assert "File not found" in result["error"]
        assert result["upload_id"] == upload_id

    @pytest.mark.asyncio
    async def test_process_uploaded_file_upload_record_not_found(self):
        """Test file processing when upload record is not found"""
        upload_id = "test_upload_123"
        data_type = "demand"
        provider_type = "test_data"
        
        # Mock upload record not found
        self.mock_upload_repo_instance.get_upload_by_id.return_value = None
        
        with patch('services.ingestion.app.services.upload_raw_data_processor.file_storage_service', self.mock_file_storage), \
             patch.object(self.service, 'raw_data_service', self.mock_raw_data_service_instance), \
             patch.object(self.service, 'raw_data_processor', self.mock_raw_data_processor_instance), \
             patch.object(self.service, 'upload_tracking_repository', self.mock_upload_repo_instance):
            result = await self.service.process_uploaded_file(upload_id, data_type, provider_type)
        
        # Verify error result
        assert result["success"] is False
        assert "Upload record not found" in result["error"]
        assert result["upload_id"] == upload_id

    @pytest.mark.asyncio
    async def test_process_uploaded_file_processing_failure(self):
        """Test file processing when CSV processing fails"""
        upload_id = "test_upload_123"
        data_type = "demand"
        provider_type = "test_data"
        
        # Mock CSV processing failure
        self.mock_raw_data_service_instance.process_csv_upload = AsyncMock(return_value={
            "success": False,
            "error": "CSV processing failed"
        })
        
        test_file_content = "timeblock,value\n1,100.5\n2,200.0"
        
        with patch("builtins.open", mock_open(read_data=test_file_content)):
            result = await self.service.process_uploaded_file(upload_id, data_type, provider_type)
        
        # Verify error result
        assert result["success"] is False
        assert "CSV processing failed" in result["error"]
        assert result["upload_id"] == upload_id

    @pytest.mark.asyncio
    async def test_process_uploaded_file_exception_handling(self):
        """Test file processing exception handling"""
        upload_id = "test_upload_123"
        data_type = "demand"
        provider_type = "test_data"
        
        # Mock exception during processing
        self.mock_raw_data_service_instance.process_csv_upload = AsyncMock(
            side_effect=Exception("Processing error")
        )
        
        test_file_content = "timeblock,value\n1,100.5\n2,200.0"
        
        with patch("builtins.open", mock_open(read_data=test_file_content)):
            result = await self.service.process_uploaded_file(upload_id, data_type, provider_type)
        
        # Verify error result
        assert result["success"] is False
        assert "Processing error" in result["error"]
        assert result["upload_id"] == upload_id

    @pytest.mark.asyncio
    async def test_trigger_raw_data_processing_demand(self):
        """Test raw data processing trigger for demand data"""
        data_type = "demand"
        provider_type = "test_data"
        
        # Mock pending data
        mock_pending_data = [{
            "_id": "raw_data_123",
            "created_at": datetime.now() - timedelta(minutes=5),
            "csv_data": [{"timeblock": 1, "value": 100.5}]
        }]
        
        self.mock_raw_data_processor_instance.get_pending_demand_data.return_value = mock_pending_data
        self.mock_raw_data_processor_instance.translate_demand_data.return_value = [{
            "timeblock": 1,
            "value": 100.5,
            "created_at": datetime.now() - timedelta(minutes=5)
        }]
        
        # Mock demand serve service
        with patch('services.ingestion.app.services.demand_serve_service.DemandServeService') as mock_demand_serve:
            mock_demand_serve_instance = Mock()
            mock_demand_serve.return_value = mock_demand_serve_instance
            mock_demand_serve_instance.process_demand_data.return_value = {
                "processed_count": 1,
                "success": True
            }
            
            await self.service._trigger_raw_data_processing(data_type, provider_type)
        
        # Verify demand data processing
        self.mock_raw_data_processor_instance.get_pending_demand_data.assert_called_once_with(limit=100)
        self.mock_raw_data_processor_instance.translate_demand_data.assert_called_once()
        mock_demand_serve_instance.process_demand_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_trigger_raw_data_processing_supply(self):
        """Test raw data processing trigger for supply data"""
        data_type = "supply"
        provider_type = "test_data"
        
        # Mock pending data
        mock_pending_data = [{
            "_id": "raw_data_456",
            "created_at": datetime.now() - timedelta(minutes=5),
            "csv_data": [{"timeblock": 1, "value": 200.0}]
        }]
        
        self.mock_raw_data_processor_instance.get_pending_supply_data.return_value = mock_pending_data
        self.mock_raw_data_processor_instance.translate_supply_data.return_value = [{
            "timeblock": 1,
            "value": 200.0,
            "created_at": datetime.now() - timedelta(minutes=5)
        }]
        
        # Mock supply serve service
        with patch('services.ingestion.app.services.supply_serve_service.SupplyServeService') as mock_supply_serve:
            mock_supply_serve_instance = Mock()
            mock_supply_serve.return_value = mock_supply_serve_instance
            mock_supply_serve_instance.process_supply_data.return_value = {
                "processed_count": 1,
                "success": True
            }
            
            await self.service._trigger_raw_data_processing(data_type, provider_type)
        
        # Verify supply data processing
        self.mock_raw_data_processor_instance.get_pending_supply_data.assert_called_once_with(limit=100)
        self.mock_raw_data_processor_instance.translate_supply_data.assert_called_once()
        mock_supply_serve_instance.process_supply_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_trigger_raw_data_processing_no_pending_data(self):
        """Test raw data processing trigger when no pending data exists"""
        data_type = "demand"
        provider_type = "test_data"
        
        # Mock no pending data
        self.mock_raw_data_processor_instance.get_pending_demand_data.return_value = []
        
        # Should not raise exception
        await self.service._trigger_raw_data_processing(data_type, provider_type)
        
        # Verify no processing occurred
        self.mock_raw_data_processor_instance.get_pending_demand_data.assert_called_once_with(limit=100)

    @pytest.mark.asyncio
    async def test_trigger_raw_data_processing_exception_handling(self):
        """Test raw data processing trigger exception handling"""
        data_type = "demand"
        provider_type = "test_data"
        
        # Mock exception during processing
        self.mock_raw_data_processor_instance.get_pending_demand_data.side_effect = Exception("Processing error")
        
        # Should not raise exception
        await self.service._trigger_raw_data_processing(data_type, provider_type)

    @pytest.mark.asyncio
    async def test_update_upload_status_success(self, mock_upload_tracking_repository):
        """Test successful upload status update"""
        upload_id = "test_upload_123"
        status = RawDataStatus.COMPLETED
        message = "Processing completed"
        
        with patch.object(self.service, 'upload_tracking_repository', mock_upload_tracking_repository.return_value):
            await self.service._update_upload_status(upload_id, status, message)
        
        # Verify status update
        mock_upload_tracking_repository.return_value.update_upload_status.assert_called_once()
        call_args = mock_upload_tracking_repository.return_value.update_upload_status.call_args
        assert call_args[0][0] == upload_id
        assert call_args[0][1]["status"] == status.value
        assert call_args[0][1]["status_message"] == message

    @pytest.mark.asyncio
    async def test_update_upload_status_with_completion_time(self):
        """Test upload status update with completion time"""
        upload_id = "test_upload_123"
        status = RawDataStatus.COMPLETED
        message = "Processing completed"
        
        with patch.object(self.service, 'upload_tracking_repository', self.mock_upload_repo_instance):
            await self.service._update_upload_status(upload_id, status, message)
        
        # Verify completion time was set
        call_args = self.mock_upload_repo_instance.update_upload_status.call_args
        update_data = call_args[0][1]
        assert "completed_at" in update_data
        assert isinstance(update_data["completed_at"], datetime)

    @pytest.mark.asyncio
    async def test_update_upload_status_with_failure_time(self):
        """Test upload status update with failure time"""
        upload_id = "test_upload_123"
        status = RawDataStatus.FAILED
        message = "Processing failed"
        
        with patch.object(self.service, 'upload_tracking_repository', self.mock_upload_repo_instance):
            await self.service._update_upload_status(upload_id, status, message)
        
        # Verify failure time was set
        call_args = self.mock_upload_repo_instance.update_upload_status.call_args
        update_data = call_args[0][1]
        assert "failed_at" in update_data
        assert isinstance(update_data["failed_at"], datetime)

    @pytest.mark.asyncio
    async def test_update_upload_status_exception_handling(self):
        """Test upload status update exception handling"""
        upload_id = "test_upload_123"
        status = RawDataStatus.COMPLETED
        message = "Processing completed"
        
        # Mock exception during status update
        self.mock_upload_repo_instance.update_upload_status.side_effect = Exception("Database error")
        
        # Should not raise exception
        await self.service._update_upload_status(upload_id, status, message)


def mock_open(read_data):
    """Helper function to mock file open"""
    from unittest.mock import mock_open as original_mock_open
    return original_mock_open(read_data=read_data)


if __name__ == "__main__":
    pytest.main([__file__])
