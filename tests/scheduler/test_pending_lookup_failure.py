"""
A failed read of pending raw data must fail the run, not pass as "No pending data".

On PROD (29-09-2026) every supply run from 04:38 logged "The read operation timed
out" for GRID_INDIA_SUPPLY, returned [] from _get_pending_raw_data, reported the
provider as {"success": true, "message": "No pending data"} and recorded the run
as success, so nothing anywhere showed that revisions had stopped.
"""

import unittest
from datetime import datetime

from pymongo.errors import NetworkTimeout

from libs.repository.raw_data_repository import RawDataRepository
from services.scheduler.app.processors.supply_processor import SupplyProcessor

TIMEOUT = "epmsptc-shard-00-01.pdpu1d.mongodb.net:27017: The read operation timed out"


class DownRawCollection:
    name = "supply_raw_data"

    def find(self, *args, **kwargs):
        raise NetworkTimeout(TIMEOUT)


class CapturingJobDetailRepo:
    def __init__(self):
        self.updates = []

    async def update_execution(self, execution_id, update_data):
        self.updates.append(update_data)
        return True


def _supply_processor():
    processor = object.__new__(SupplyProcessor)
    repo = object.__new__(RawDataRepository)
    repo.collection = DownRawCollection()
    repo.collection_name = repo.collection.name
    processor.raw_data_repository = repo
    processor.job_detail_repository = CapturingJobDetailRepo()
    return processor


async def _record(processor, trigger_details, status="success", success=True):
    await processor._record_job_completion(
        "supply_data_processing", "Supply Data Processing", "*/15 * * * *",
        datetime(2026, 9, 29, 4, 38), datetime(2026, 9, 29, 4, 39),
        "supply_data_processing_20260929_043804", status, success,
        result={"status": status, "trigger_details": trigger_details},
    )
    return processor.job_detail_repository.updates[-1]


LOOKUP_FAILED = {"success": False, "pending_lookup_failed": True,
                 "error": f"Could not read pending raw data: {TIMEOUT}"}


class PendingLookupFailureTest(unittest.IsolatedAsyncioTestCase):

    async def test_provider_reports_the_read_failure(self):
        processor = _supply_processor()

        class StatusRepo:
            def __init__(self, *args, **kwargs):
                class Collection:
                    def find(self, *a, **k):
                        class Cursor:
                            def sort(self, *a, **k): return self
                            def limit(self, *a, **k): return self
                            async def to_list(self, length=None):
                                return [{"raw_data_id": "66f8d2c0e4b0a1a2b3c4d5e6", "provider_id": "P",
                                         "status": "pending"}]
                        return Cursor()
                self.collection = Collection()

        from unittest.mock import patch
        with patch("libs.repository.raw_data_processing_status_repository.RawDataProcessingStatusRepository",
                   StatusRepo):
            result = await processor._process_provider_data(
                {"_id": "P", "code": "GRID_INDIA_SUPPLY"}, {"data_type": "supply"})

        self.assertFalse(result["success"])
        self.assertTrue(result["pending_lookup_failed"])
        self.assertIn("The read operation timed out", result["error"])

    async def test_run_with_an_unreadable_provider_is_partial(self):
        stored = await _record(_supply_processor(), {"success": True, "details": {
            "GRID_INDIA_SUPPLY": LOOKUP_FAILED,
            "CSV_SUPPLY": {"success": True, "message": "No pending data"},
        }})

        self.assertEqual(stored["status"], "partial_success")
        self.assertFalse(stored["success"])
        self.assertIn("GRID_INDIA_SUPPLY", stored["error_message"])
        self.assertIn("The read operation timed out", stored["error_message"])
        self.assertEqual(stored["result_data"]["status"], "partial_success")

    async def test_run_where_no_provider_could_be_read_is_an_error(self):
        stored = await _record(_supply_processor(), {"success": False, "details": {
            "GRID_INDIA_SUPPLY": LOOKUP_FAILED,
            "GRID_INDIA_SUPPLY_TOMMOROW": LOOKUP_FAILED,
        }})

        self.assertEqual(stored["status"], "error")
        self.assertFalse(stored["success"])

    async def test_other_provider_failures_keep_their_current_status(self):
        stored = await _record(_supply_processor(), {"success": True, "details": {
            "GRID_INDIA_SUPPLY": {"success": False, "error": "Document 2 failed"},
            "CSV_SUPPLY": {"success": True, "message": "No pending data"},
        }})

        self.assertEqual(stored["status"], "success")
        self.assertTrue(stored["success"])
        self.assertIsNone(stored["error_message"])


if __name__ == "__main__":
    unittest.main()
