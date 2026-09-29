"""
Raw ingestion data is kept for 7 days.

Nothing ever removed *_raw_data or their *_raw_data_processing_status rows, and
the pending-data read slowed down with every day kept until it timed out on PROD
(29-09-2026). Time-series raw collections get a 7-day TTL, like the vendor's
*_integration_apilog collections; a delete on their time field unpacks every
bucket, so that is only the fallback when the TTL cannot be set. Plain raw
collections and status rows (stored without created_at) go by _id time.
"""

import unittest
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from pymongo.errors import OperationFailure

from services.scheduler.app.services.raw_data_retention import enforce_raw_data_retention

SEVEN_DAYS = 7 * 24 * 3600


def _oid_days_ago(days):
    return ObjectId.from_datetime(datetime.now(timezone.utc) - timedelta(days=days))


class FakeCursor:
    def __init__(self, items):
        self.items = items

    async def to_list(self, length=None):
        return list(self.items)


class FakeCollection:
    def __init__(self, docs):
        self.docs = list(docs)
        self.deletes = []

    async def delete_many(self, filter):
        self.deletes.append(filter)
        (field, cond), = filter.items()
        keep = [d for d in self.docs if not d[field] < cond["$lt"]]

        class Result:
            deleted_count = len(self.docs) - len(keep)
        self.docs = keep
        return Result()


class FakeDB:
    def __init__(self, collections, refuse_collmod=False):
        # name -> (type, options, FakeCollection)
        self.collections = collections
        self.refuse_collmod = refuse_collmod
        self.collmods = []

    async def list_collections(self):
        return FakeCursor({"name": n, "type": t, "options": o} for n, (t, o, _) in self.collections.items())

    def __getitem__(self, name):
        return self.collections[name][2]

    async def command(self, name, value, **kwargs):
        assert name == "collMod"
        if self.refuse_collmod:
            raise OperationFailure("not authorized on epms to execute command { collMod: ... }", code=13)
        self.collmods.append((value, kwargs))


def _ts_docs():
    now = datetime.now()
    return [{"_id": ObjectId(), "created_at": now - timedelta(days=d)} for d in (30, 8, 6, 0)]


def _id_docs(**extra):
    return [dict({"_id": _oid_days_ago(d)}, **extra) for d in (30, 8, 6, 0)]


class RawDataRetentionTest(unittest.IsolatedAsyncioTestCase):

    async def test_time_series_raw_collections_get_a_seven_day_ttl(self):
        db = FakeDB({
            "supply_raw_data": ("timeseries", {"timeField": "created_at"}, FakeCollection(_ts_docs())),
            "weather_raw_data": ("timeseries", {"timeField": "created_at"}, FakeCollection(_ts_docs())),
            "supply_serve": ("timeseries", {"timeField": "scheduled_at"}, FakeCollection(_ts_docs())),
            "system.buckets.supply_raw_data": ("collection", {}, FakeCollection([])),
        })

        await enforce_raw_data_retention(db, days=7)

        self.assertEqual(sorted(db.collmods), [("supply_raw_data", {"expireAfterSeconds": SEVEN_DAYS}),
                                               ("weather_raw_data", {"expireAfterSeconds": SEVEN_DAYS})])
        self.assertEqual(db["supply_serve"].deletes, [])

    async def test_a_collection_already_on_seven_days_is_left_alone(self):
        db = FakeDB({"supply_raw_data": ("timeseries", {"expireAfterSeconds": SEVEN_DAYS},
                                         FakeCollection(_ts_docs()))})

        await enforce_raw_data_retention(db, days=7)

        self.assertEqual(db.collmods, [])
        self.assertEqual(db["supply_raw_data"].deletes, [])

    async def test_when_the_ttl_is_refused_old_raw_data_is_deleted(self):
        db = FakeDB({"supply_raw_data": ("timeseries", {}, FakeCollection(_ts_docs()))}, refuse_collmod=True)

        result = await enforce_raw_data_retention(db, days=7)

        ages = sorted((datetime.now() - d["created_at"]).days for d in db["supply_raw_data"].docs)
        self.assertEqual(ages, [0, 6])
        self.assertIn("not authorized", result["supply_raw_data"]["ttl_error"])
        self.assertEqual(result["supply_raw_data"]["deleted"], 2)

    async def test_status_rows_and_plain_raw_collections_go_by_id_time(self):
        db = FakeDB({
            "supply_raw_data_processing_status": ("collection", {}, FakeCollection(_id_docs(status="pending"))),
            "exchange_raw_data": ("collection", {}, FakeCollection(_id_docs())),
            "supply_serve_historical": ("collection", {}, FakeCollection(_id_docs())),
        })

        await enforce_raw_data_retention(db, days=7)

        for name in ("supply_raw_data_processing_status", "exchange_raw_data"):
            ages = sorted((datetime.now(timezone.utc) - d["_id"].generation_time).days for d in db[name].docs)
            self.assertEqual(ages, [0, 6], name)
        self.assertEqual(len(db["supply_serve_historical"].docs), 4)

    async def test_one_failing_collection_does_not_stop_the_rest(self):
        broken = FakeCollection(_id_docs())

        async def refuse(filter):
            raise OperationFailure("operation exceeded time limit", code=50)
        broken.delete_many = refuse
        db = FakeDB({
            "bid_raw_data": ("collection", {}, broken),
            "exchange_raw_data": ("collection", {}, FakeCollection(_id_docs())),
        })

        result = await enforce_raw_data_retention(db, days=7)

        self.assertIn("exceeded time limit", result["bid_raw_data"]["error"])
        self.assertEqual(result["exchange_raw_data"]["deleted"], 2)



class SystemCleanupJobTest(unittest.TestCase):
    """The daily system_cleanup job (02:00) is what applies the retention."""

    def _manager(self, retention):
        from services.scheduler.app.services.cron_manager import CronManager

        manager = object.__new__(CronManager)

        async def apilog():
            return {"status": "success", "message": "Cleaned 0 integration apilog docs"}

        async def raw():
            return retention
        manager._run_integration_apilog_cleanup = apilog
        manager._run_raw_data_retention = raw
        manager._safe_run_async = lambda coro: __import__("asyncio").run(coro)
        return manager

    def test_cleanup_applies_raw_data_retention(self):
        result = self._manager({"supply_raw_data": {"ttl_days": 7, "ttl_set": True}})._execute_system_cleanup_job()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["raw_data_retention"]["supply_raw_data"]["ttl_days"], 7)

    def test_cleanup_reports_a_failed_collection(self):
        result = self._manager({"bid_raw_data": {"error": "operation exceeded time limit"}})._execute_system_cleanup_job()

        self.assertEqual(result["status"], "error")
        self.assertIn("bid_raw_data", result["error"])


if __name__ == "__main__":
    unittest.main()
