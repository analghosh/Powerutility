"""
Reading pending raw data must never scan a whole raw collection.

The *_raw_data collections are time-series collections. A measurement's _id is
not indexed there, so find({"_id": {"$in": ids}}) and a $lookup on _id unpack
every bucket ever written. Nothing deletes raw data, so on PROD (29-09-2026) the
scan passed the 20 s socketTimeoutMS: every GRID_INDIA_SUPPLY revision fetched
after 04:23 was stored but never read ("The read operation timed out"), and the
run logged "No pending data" instead.

FakeTimeSeries raises that same timeout for any read that does not bound
created_at (the time field), which is what lets MongoDB skip whole buckets.
"""

import random
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from bson import ObjectId
from pymongo.errors import NetworkTimeout

from libs.repository.raw_data_repository import RawDataRepository
from services.scheduler.app.processors.base_processor import BaseProcessor
from services.scheduler.app.processors.supply_processor import SupplyProcessor
from services.scheduler.app.processors.weather_processor import WeatherProcessor

IST = timedelta(hours=5, minutes=30)
NOW_UTC = datetime(2026, 9, 29, 4, 0, 0)
TIMEOUT = ("epmsptc-shard-00-01.pdpu1d.mongodb.net:27017: The read operation timed out "
           "(configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 10000.0ms)")


def _oid_at(utc_naive):
    """A unique ObjectId whose timestamp is utc_naive (the driver sets _id at insert)."""
    head = ObjectId.from_datetime(utc_naive.replace(tzinfo=timezone.utc)).binary[:4]
    return ObjectId(head + ObjectId().binary[4:])


def _raw_doc(utc_naive):
    # created_at is the ingestion host's naive local clock (IST), _id is UTC.
    return {"_id": _oid_at(utc_naive), "created_at": utc_naive + IST, "raw_data": [{"v": 1}]}


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        if isinstance(key, list):
            key, direction = key[0]
        # Missing fields sort as equal, like null in MongoDB (stable, so input order stays).
        self.docs.sort(key=lambda d: (d.get(key) is not None, d.get(key)), reverse=direction == -1)
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    async def to_list(self, length=None):
        return self.docs[:length] if length else list(self.docs)


class FakeTimeSeries:
    """A *_raw_data collection: any read without a created_at bound times out."""

    name = "supply_raw_data"

    def __init__(self, docs):
        self.docs = list(docs)
        self.ranges = []

    def find(self, filter, *args, **kwargs):
        bound = filter.get("created_at")
        if not isinstance(bound, dict) or not ({"$gte", "$gt"} & bound.keys()) \
                or not ({"$lte", "$lt"} & bound.keys()):
            raise NetworkTimeout(TIMEOUT)
        lo = bound.get("$gte", bound.get("$gt"))
        hi = bound.get("$lte", bound.get("$lt"))
        self.ranges.append((lo, hi))
        ids = set(filter.get("_id", {}).get("$in", []))
        return FakeCursor(d for d in self.docs if d["_id"] in ids and lo <= d["created_at"] <= hi)

    def aggregate(self, pipeline, *args, **kwargs):
        raise NetworkTimeout(TIMEOUT)


class FakeStatusCollection:
    """{data_type}_raw_data_processing_status rows. They are stored with no created_at."""

    def __init__(self, rows):
        self.rows = list(rows)

    def find(self, filter, *args, **kwargs):
        statuses = set(filter.get("status", {}).get("$in", []))
        return FakeCursor(r for r in self.rows
                          if r["provider_id"] == filter.get("provider_id") and r["status"] in statuses)

    def aggregate(self, pipeline, *args, **kwargs):
        # The old pipeline $lookup-s the raw collection on _id: a full scan.
        raise NetworkTimeout(TIMEOUT)


class FakeStatusRepo:
    rows = []

    def __init__(self, *args, **kwargs):
        self.collection = FakeStatusCollection(FakeStatusRepo.rows)


def _status_row(raw, status="pending", provider_id="P"):
    utc = raw["_id"].generation_time.replace(tzinfo=None)
    return {"_id": _oid_at(utc + timedelta(milliseconds=5)), "raw_data_id": str(raw["_id"]),
            "provider_id": provider_id, "status": status}


def _processor(cls, raw_docs):
    processor = object.__new__(cls)
    repo = object.__new__(RawDataRepository)
    repo.collection = FakeTimeSeries(raw_docs)
    repo.collection_name = repo.collection.name
    processor.raw_data_repository = repo
    return processor


class SupplyPendingLookupTest(unittest.IsolatedAsyncioTestCase):

    async def test_reads_pending_revisions_without_scanning_the_collection(self):
        # Months of already-processed revisions plus four pending ones this morning.
        history = [_raw_doc(NOW_UTC - timedelta(days=d)) for d in range(1, 200)]
        pending = [_raw_doc(NOW_UTC + timedelta(minutes=15 * i)) for i in range(4)]
        rows = [_status_row(r) for r in pending]
        random.Random(7).shuffle(rows)
        FakeStatusRepo.rows = rows
        processor = _processor(SupplyProcessor, history + pending)

        with patch("libs.repository.raw_data_processing_status_repository.RawDataProcessingStatusRepository",
                   FakeStatusRepo):
            docs = await processor._get_pending_raw_data("P", "supply")

        # The three oldest pending revisions, oldest first.
        self.assertEqual([d["_id"] for d in docs], [d["_id"] for d in pending[:3]])

    async def test_each_read_covers_days_not_the_whole_history(self):
        pending = [_raw_doc(NOW_UTC), _raw_doc(NOW_UTC - timedelta(days=60))]
        FakeStatusRepo.rows = [_status_row(r) for r in pending]
        processor = _processor(SupplyProcessor, pending)

        with patch("libs.repository.raw_data_processing_status_repository.RawDataProcessingStatusRepository",
                   FakeStatusRepo):
            docs = await processor._get_pending_raw_data("P", "supply")

        self.assertEqual({d["_id"] for d in docs}, {d["_id"] for d in pending})
        widest = max(hi - lo for lo, hi in processor.raw_data_repository.collection.ranges)
        self.assertLessEqual(widest, timedelta(days=3))


class BasePendingLookupTest(unittest.IsolatedAsyncioTestCase):

    async def test_reads_pending_and_failed_rows_oldest_first(self):
        history = [_raw_doc(NOW_UTC - timedelta(days=d)) for d in range(1, 200)]
        pending = [_raw_doc(NOW_UTC + timedelta(minutes=15 * i)) for i in range(5)]
        rows = [_status_row(r, "failed" if i == 1 else "pending") for i, r in enumerate(pending)]
        random.Random(3).shuffle(rows)
        FakeStatusRepo.rows = rows
        processor = _processor(WeatherProcessor, history + pending)

        with patch("libs.repository.raw_data_processing_status_repository.RawDataProcessingStatusRepository",
                   FakeStatusRepo):
            docs = await BaseProcessor._get_pending_raw_data(processor, "P", "weather")

        self.assertEqual([d["_id"] for d in docs], [d["_id"] for d in pending])

    async def test_skips_status_rows_whose_raw_document_is_gone(self):
        kept = _raw_doc(NOW_UTC)
        gone = _raw_doc(NOW_UTC - timedelta(minutes=15))
        FakeStatusRepo.rows = [_status_row(gone), _status_row(kept)]
        processor = _processor(WeatherProcessor, [kept])

        with patch("libs.repository.raw_data_processing_status_repository.RawDataProcessingStatusRepository",
                   FakeStatusRepo):
            docs = await BaseProcessor._get_pending_raw_data(processor, "P", "weather")

        self.assertEqual([d["_id"] for d in docs], [kept["_id"]])


if __name__ == "__main__":
    unittest.main()
