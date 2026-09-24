"""
The batch temp sync must only ever write the day it read.

``sync_all_blocks_up_to_current`` reads today's IDF/DAF rows, but each write is
keyed on the temp record's own ``scheduled_at``. ``temp_demand_serve`` is keyed
on ``time_block`` alone, so once it holds tomorrow's rows (loaded before
midnight) every run replaced tomorrow's DAF rows with copies of today's,
forecast and ``inserted_at`` included. That is how the day-ahead forecast of
24-09-2026 became the 23rd's, zeros and all.
"""

import unittest
from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from services.scheduler.app.processors.demand_temp_sync_processor import (
    DemandTempSyncProcessor,
)


def _cursor(rows):
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


class SyncAllBlocksDateTests(unittest.IsolatedAsyncioTestCase):
    BLOCKS = 3

    def setUp(self):
        self.today = datetime.combine(datetime.now().date(), time(0, 0, 0))
        self.tomorrow = self.today + timedelta(days=1)

    def _processor(self, temp_rows, daf_rows):
        with patch.object(DemandTempSyncProcessor, "__init__", lambda self: None):
            proc = DemandTempSyncProcessor()
        proc._temp_collection_name = "temp_demand_serve"

        serve = MagicMock()
        serve.find = MagicMock(
            side_effect=lambda q: _cursor(daf_rows if q.get("type") == "DAF" else [])
        )
        serve.bulk_write = AsyncMock()
        historical = MagicMock()
        historical.bulk_write = AsyncMock()
        temp = MagicMock()
        temp.find = MagicMock(return_value=_cursor(temp_rows))

        proc.demand_repository = MagicMock()
        proc.demand_repository.db = {"temp_demand_serve": temp}
        proc.demand_repository.serve_collection = serve
        proc.demand_repository.historical_collection = historical
        proc.supply_repository = MagicMock()
        proc.supply_repository.serve_collection.find = MagicMock(return_value=_cursor([]))
        proc.exchange_repository = MagicMock()
        proc.exchange_repository.serve_collection.find = MagicMock(return_value=_cursor([]))
        proc.master_plants_repository = MagicMock()
        proc.master_plants_repository.get_all_plants = AsyncMock(return_value=[])
        proc._get_current_time_block = lambda: self.BLOCKS
        return proc, serve

    def _temp(self, scheduled_at):
        return [
            {"time_block": b, "scheduled_at": scheduled_at,
             "drawal": 372.0, "rostering": 0.0, "frequency": 50.0}
            for b in range(1, self.BLOCKS + 1)
        ]

    def _daf_today(self):
        return [
            {"scheduled_at": self.today, "time_block": b, "type": "DAF",
             "forecast": 415.0, "inserted_at": datetime(2026, 9, 21, 7, 30)}
            for b in range(1, self.BLOCKS + 1)
        ]

    @staticmethod
    def _written_keys(serve):
        if not serve.bulk_write.await_count:
            return []
        ops = serve.bulk_write.await_args.args[0]
        return [op._filter for op in ops]

    async def test_temp_rows_dated_tomorrow_do_not_overwrite_tomorrow(self):
        proc, serve = self._processor(self._temp(self.tomorrow), self._daf_today())

        await proc.sync_all_blocks_up_to_current()

        written_days = {k["scheduled_at"] for k in self._written_keys(serve)}
        self.assertNotIn(self.tomorrow, written_days)

    async def test_stale_temp_rows_from_yesterday_do_not_overwrite_yesterday(self):
        yesterday = self.today - timedelta(days=1)
        proc, serve = self._processor(self._temp(yesterday), self._daf_today())

        synced, skipped, _ = await proc.sync_all_blocks_up_to_current()

        self.assertEqual((synced, skipped), (0, self.BLOCKS))
        self.assertNotIn(yesterday, {k["scheduled_at"] for k in self._written_keys(serve)})

    async def test_temp_rows_dated_today_still_sync_and_keep_the_forecast(self):
        proc, serve = self._processor(self._temp(self.today), self._daf_today())

        synced, skipped, failed = await proc.sync_all_blocks_up_to_current()

        self.assertEqual((synced, skipped, failed), (self.BLOCKS, 0, 0))
        ops = serve.bulk_write.await_args.args[0]
        daf = [op._doc for op in ops if op._filter["type"] == "DAF"]
        self.assertEqual(len(daf), self.BLOCKS)
        self.assertTrue(all(d["scheduled_at"] == self.today for d in daf))
        self.assertTrue(all(d["forecast"] == 415.0 for d in daf))


if __name__ == "__main__":
    unittest.main()
