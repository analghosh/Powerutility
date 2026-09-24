"""
An IDF row must never invent a DAF forecast.

``process_raw_data_to_serve`` moves the IDF row's forecast into forecast_serve
and pops it before ``_sync_idf_to_daf`` copies the row as DAF. When no DAF row
existed yet, that copy was saved with ``forecast=0``, which the Demand Forecast
chart drew as a 0 MW Day Ahead line and which counts as 100% MAPE. It is how
blocks 43-96 of 23-09-2026 got their zeros.
"""

import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from services.scheduler.app.processors.demand_processor import DemandProcessor


class SyncIdfToDafTests(unittest.IsolatedAsyncioTestCase):
    def _processor(self, existing_daf):
        with patch.object(DemandProcessor, "__init__", lambda self: None):
            proc = DemandProcessor()
        proc.demand_repository = MagicMock()
        proc.demand_repository.find_by_unique_key = AsyncMock(return_value=existing_daf)
        proc._create_demand_entity = AsyncMock(return_value=MagicMock(name="new_daf"))
        proc._create_demand_entity_with_preserved_timestamps = AsyncMock(
            return_value=MagicMock(name="merged_daf")
        )
        return proc

    def _idf_row(self):
        # forecast already popped, as process_raw_data_to_serve leaves it
        return {"scheduled_at": datetime(2026, 9, 23), "time_block": 43, "type": "IDF",
                "drawal": 332.06, "actual": 346.86, "rostering": 60.0}

    async def test_no_daf_row_is_created_when_none_exists(self):
        proc = self._processor(existing_daf=None)

        result = await proc._sync_idf_to_daf(self._idf_row(), {}, {})

        self.assertIsNone(result)
        proc._create_demand_entity.assert_not_awaited()

    async def test_an_existing_daf_row_keeps_its_forecast_and_takes_the_actuals(self):
        existing = {"scheduled_at": datetime(2026, 9, 23), "time_block": 43,
                    "type": "DAF", "forecast": 391.0, "actual": 0.0}
        proc = self._processor(existing_daf=existing)

        result = await proc._sync_idf_to_daf(self._idf_row(), {}, {})

        self.assertIsNotNone(result)
        merged = proc._create_demand_entity_with_preserved_timestamps.await_args.args[0]
        self.assertEqual(merged["type"], "DAF")
        self.assertEqual(merged["forecast"], 391.0)
        self.assertEqual(merged["actual"], 346.86)


if __name__ == "__main__":
    unittest.main()
