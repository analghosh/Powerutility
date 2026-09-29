"""
A backlog of pending supply documents clears in one run.

Supply reads only 3 pending documents per run (each Grid India document holds
~4700 rows). After the 29-09-2026 stall ~30 fetches were queued; at 3 per
15-minute run, less the new fetch each run adds, reaching the current RLDC
revision would have taken 3-4 hours. A run now keeps taking batches of 3,
oldest first, until the queue is empty, a batch fails, or a batch makes no
progress - capped at PENDING_BATCHES_PER_RUN.
"""

import unittest

from services.scheduler.app.processors.supply_processor import SupplyProcessor
from services.scheduler.app.processors.weather_processor import WeatherProcessor

PROVIDER = {"_id": "P", "code": "GRID_INDIA_SUPPLY"}


class FakeQueue:
    """Pending raw documents; processing a document completes it unless told not to."""

    def __init__(self, count, fail_id=None, stick=False):
        self.pending = [{"_id": f"doc{i:03d}", "raw_data": [{}] * 10} for i in range(count)]
        self.processed = []
        self.fail_id = fail_id
        self.stick = stick  # status updates silently fail: documents stay pending

    def install(self, processor):
        async def get_pending(provider_id, data_type=None):
            return self.pending[:processor.PENDING_BATCH_SIZE]

        async def process(raw_data_items, metadata, provider_config, provider_code):
            done = 0
            for i, item in enumerate(raw_data_items):
                if item["_id"] == self.fail_id:
                    self.pending.remove(item)
                    return {"success": False, "processed_documents": done, "total_documents": len(raw_data_items),
                            "total_records_processed": done * 10, "failed_at_index": i,
                            "failed_document_id": item["_id"], "remaining_documents": len(raw_data_items) - done}
                self.processed.append(item["_id"])
                if not self.stick:
                    self.pending.remove(item)
                done += 1
            return {"success": True, "processed_documents": done, "total_documents": len(raw_data_items),
                    "total_records_processed": done * 10, "failed_at_index": -1,
                    "failed_document_id": None, "remaining_documents": 0}

        processor._get_pending_raw_data = get_pending
        processor._process_raw_data_documents = process
        return processor


class PendingCatchUpTest(unittest.IsolatedAsyncioTestCase):

    async def test_supply_backlog_clears_in_one_run(self):
        queue = FakeQueue(31)
        processor = queue.install(object.__new__(SupplyProcessor))

        result = await processor._process_provider_data(PROVIDER, {"data_type": "supply"})

        self.assertTrue(result["success"])
        self.assertEqual(queue.pending, [])
        self.assertEqual(queue.processed, [f"doc{i:03d}" for i in range(31)])
        self.assertEqual(result["processed_documents"], 31)
        self.assertEqual(result["total_records_processed"], 310)

    async def test_one_run_is_capped(self):
        queue = FakeQueue(100)
        processor = queue.install(object.__new__(SupplyProcessor))

        result = await processor._process_provider_data(PROVIDER, {"data_type": "supply"})

        cap = SupplyProcessor.PENDING_BATCHES_PER_RUN * SupplyProcessor.PENDING_BATCH_SIZE
        self.assertEqual(result["processed_documents"], cap)
        self.assertEqual(len(queue.pending), 100 - cap)

    async def test_a_failed_document_stops_the_run(self):
        queue = FakeQueue(12, fail_id="doc004")
        processor = queue.install(object.__new__(SupplyProcessor))

        result = await processor._process_provider_data(PROVIDER, {"data_type": "supply"})

        self.assertFalse(result["success"])
        self.assertEqual(result["failed_document_id"], "doc004")
        self.assertEqual(queue.processed, ["doc000", "doc001", "doc002", "doc003"])

    async def test_documents_left_pending_are_not_reprocessed(self):
        queue = FakeQueue(9, stick=True)
        processor = queue.install(object.__new__(SupplyProcessor))

        await processor._process_provider_data(PROVIDER, {"data_type": "supply"})

        self.assertEqual(queue.processed, ["doc000", "doc001", "doc002"])

    async def test_other_data_types_still_take_one_batch_per_run(self):
        queue = FakeQueue(120)
        processor = queue.install(object.__new__(WeatherProcessor))

        result = await processor._process_provider_data(PROVIDER, {"data_type": "weather"})

        self.assertEqual(result["processed_documents"], 50)
        self.assertEqual(len(queue.pending), 70)


if __name__ == "__main__":
    unittest.main()
