import pytest
import asyncio
from unittest.mock import MagicMock, patch
from datetime import datetime, time
from services.scheduler.app.processors.supply_fill_processor import SupplyFillProcessor
from libs.models.dc_sg_entity import DCSgEntity
from libs.models.supply_serve_entity import SupplyServeEntity

@pytest.mark.asyncio
async def test_process_fill_logic():
    # Setup mocks
    processor = SupplyFillProcessor()
    processor.dc_sg_repository = MagicMock()
    processor.supply_repository = MagicMock()
    processor.plant_repository = MagicMock()
    
    # Mock data
    today = datetime.now().date()
    plant_id = "PLANT_001"
    
    # Mock no existing records for today
    processor.dc_sg_repository.get_records_by_date.return_value = []
    processor.supply_repository.find_supply_serve_by_date.return_value = []
    
    # Mock plant master
    processor.plant_repository.collection.find.return_value = [{"plant_id": plant_id}]
    
    # Mock historical records
    hist_record = {
        "scheduled_at": datetime(2023, 12, 19, 10, 0),
        "time_block": 1,
        "dc_sg": "DC",
        "plant_id": plant_id,
        "volume": 100.0,
        "fc_price": 10.0,
        "px_price": 11.0,
        "vc_price": 9.0,
        "price_group": "A",
        "tm": 0.0,
        "multiplier": "1",
        "ramping": "0"
    }
    processor.dc_sg_repository.get_latest_records_per_timeblock.return_value = [hist_record]
    
    # Mock repository update/insert results
    processor.dc_sg_repository.update_or_insert_record.return_value = MagicMock(upserted_id="123", modified_count=0)
    processor.supply_repository.bulk_insert_supply_serve_with_deduplication.return_value = {"inserted_count": 1}
    
    # Mock SupplyProcessor for demand schedule update
    with patch("services.scheduler.app.processors.supply_processor.SupplyProcessor") as MockSP:
        success = await processor._process_fill({"correlation_id": "test"})
        
        assert success is True
        # Verify today's date was used
        processor.dc_sg_repository.update_or_insert_record.assert_called()
        entity = processor.dc_sg_repository.update_or_insert_record.call_args[0][0]
        assert isinstance(entity, DCSgEntity)
        assert entity.scheduled_at.date() == today
        assert entity.time_block == 1
        assert entity.volume == 100.0
        
        # Verify supply serve bulk insert
        processor.supply_repository.bulk_insert_supply_serve_with_deduplication.assert_called()
        entities = processor.supply_repository.bulk_insert_supply_serve_with_deduplication.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], SupplyServeEntity)
        assert entities[0].scheduled_at.date() == today

if __name__ == "__main__":
    asyncio.run(test_process_fill_logic())
