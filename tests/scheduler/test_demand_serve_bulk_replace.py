"""
Rewriting demand_serve rows must never lose a row.

``bulk_insert_demand_serve_with_deduplication`` used to send every DeleteOne and
then every InsertOne in one ordered bulk_write. PROD has a unique index on
(type, scheduled_at, time_block), so when another job re-created one of the rows
between our delete and our insert (the IDF merge runs in the same second), the
first insert failed with E11000, ordered=True stopped the batch, and every row
already deleted stayed deleted. That is how 22-09-2026 22:15:02 dropped the
23rd's IDF rows 35-96 ("Error in bulk_delete_then_insert_optimized ... E11000").

The fake collection below keeps that unique key, runs ops in order, and lets a
"concurrent writer" re-create a key the moment our first delete lands.
"""

import unittest
from datetime import datetime
from unittest.mock import patch

from pymongo import DeleteOne, InsertOne, ReplaceOne
from pymongo.errors import BulkWriteError

from libs.models import DemandServeEntity
from services.scheduler.app.repository.demand_serve_repository import DemandServeRepository

DAY = datetime(2026, 9, 23)


def _key(doc):
    return (doc["type"], doc["scheduled_at"], doc["time_block"])


class FakeUniqueCollection:
    """Enough of a Motor collection for bulk_write against a unique key."""

    def __init__(self, docs, racer=None):
        self.docs = {_key(d): dict(d) for d in docs}
        self.racer = racer  # called once, right after the first delete

    def _race(self):
        if self.racer:
            racer, self.racer = self.racer, None
            racer(self.docs)

    async def bulk_write(self, ops, ordered=True):
        errors, deleted, inserted, matched, upserted = [], 0, 0, 0, 0
        for i, op in enumerate(ops):
            if isinstance(op, DeleteOne):
                if self.docs.pop(_key(op._filter), None) is not None:
                    deleted += 1
                self._race()
            elif isinstance(op, InsertOne):
                k = _key(op._doc)
                if k in self.docs:
                    errors.append({"index": i, "code": 11000, "errmsg": f"E11000 dup key {k}"})
                    if ordered:
                        break
                    continue
                self.docs[k] = dict(op._doc)
                inserted += 1
            elif isinstance(op, ReplaceOne):
                k = _key(op._filter)
                if k in self.docs:
                    matched += 1
                elif op._upsert:
                    upserted += 1
                else:
                    continue
                self.docs[k] = {**op._filter, **op._doc}
        if errors:
            raise BulkWriteError({"writeErrors": errors, "nInserted": inserted,
                                  "nRemoved": deleted, "nMatched": matched,
                                  "nUpserted": upserted})

        class Result:
            deleted_count = deleted
            inserted_count = inserted
            matched_count = matched
            modified_count = matched
            upserted_count = upserted
        return Result()


def _row(tb, schedule):
    return {"type": "IDF", "scheduled_at": DAY, "time_block": tb,
            "forecast": 400.0 + tb, "schedule": schedule}


def _repo(collection):
    with patch.object(DemandServeRepository, "__init__", lambda self: None):
        repo = DemandServeRepository()
    repo.collection = collection
    repo.serve_collection = collection
    repo.collection_name = "demand_serve"
    return repo


def _entities(blocks, schedule):
    return [DemandServeEntity(**_row(tb, schedule)) for tb in blocks]


class BulkRewriteTests(unittest.IsolatedAsyncioTestCase):
    BLOCKS = range(35, 97)  # the 62 rows the schedule update rewrote

    def _merge_recreates_block_35(self, docs):
        docs[("IDF", DAY, 35)] = _row(35, None)

    async def test_a_concurrent_recreate_loses_no_rows(self):
        coll = FakeUniqueCollection([_row(tb, None) for tb in self.BLOCKS],
                                    racer=self._merge_recreates_block_35)

        await _repo(coll).bulk_insert_demand_serve_with_deduplication(
            _entities(self.BLOCKS, schedule=341.62))

        self.assertEqual(sorted(k[2] for k in coll.docs), list(self.BLOCKS))
        self.assertTrue(all(d["schedule"] == 341.62 for d in coll.docs.values()))

    async def test_rows_keep_their_identity_and_gain_no_duplicates(self):
        coll = FakeUniqueCollection([_row(tb, None) for tb in self.BLOCKS])

        result = await _repo(coll).bulk_insert_demand_serve_with_deduplication(
            _entities(self.BLOCKS, schedule=1.0))

        self.assertEqual(len(coll.docs), len(self.BLOCKS))
        self.assertTrue(all("_id" not in d for d in coll.docs.values()))
        self.assertEqual(result["error_count"], 0)

    async def test_new_rows_are_created(self):
        coll = FakeUniqueCollection([])

        await _repo(coll).bulk_insert_demand_serve_with_deduplication(
            _entities(range(1, 5), schedule=1.0))

        self.assertEqual(sorted(k[2] for k in coll.docs), [1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()
