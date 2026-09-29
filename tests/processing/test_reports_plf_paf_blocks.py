"""
Tests for the block-wise PLF / PAF report.

The plant row is the average of that plant's blocks, so the block rows must use
the same formulas and must average back to it. The download mirrors the screen:
each plant's average row, then its blocks.
"""

import asyncio
import csv
import io
import uuid
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from libs.audit_queue.simple_logger import AuditLogger
from services.processing.app.core.dependencies import get_audit_logger
from services.processing.app.v2.api.router.reports_plf_paf_routes import router
from services.processing.app.v2.dependencies import get_user_context
from services.processing.app.v2.repository.reports_plf_paf_repository import (
    ReportsPlfPafRepository,
)
from services.processing.app.v2.services import reports_plf_paf_service as plf_module
from services.processing.app.v2.services.reports_plf_paf_service import (
    CSV_HEADERS,
    ReportsPlfPafService,
)

MASTER = {
    "ACME": {"plant_id": "ACME", "plant_name": "ACME Dhaulpur", "technology": "Solar", "ppa": "50"},
    "KKNP": {"plant_id": "KKNP", "plant_name": "Kudankulam", "technology": "Nuclear", "ppa": "40"},
    "PULKIT": {"plant_id": "PULKIT", "plant_name": "Pulkit Metals", "technology": "Solar"},
    "SECI": {"plant_id": "SECI", "plant_name": "SECI Wind", "technology": "Wind"},
    "OA_PX": {"plant_id": "OA_PX", "plant_name": "OA_PX", "technology": "Open Access"},
}

BLOCKS = [
    {"plant_id": "ACME", "date": "2026-09-28", "time_block": 1, "dc": 0.0, "sg": 0.0},
    {"plant_id": "ACME", "date": "2026-09-28", "time_block": 48, "dc": 40.0, "sg": 30.0},
    {"plant_id": "ACME", "date": "2026-09-29", "time_block": 48, "dc": 20.0, "sg": 22.0},
    {"plant_id": "KKNP", "date": "2026-09-28", "time_block": 1, "dc": 0.0, "sg": 35.0},
    {"plant_id": "PULKIT", "date": "2026-09-28", "time_block": 1, "dc": 8.0, "sg": 7.5},
    {"plant_id": "SECI", "date": "2026-09-28", "time_block": 1, "dc": None, "sg": 5.0},
    {"plant_id": "OA_PX", "date": "2026-09-28", "time_block": 1, "dc": None, "sg": -10.0},
]


def _averages(blocks):
    """What the summary pipeline returns for the same rows."""
    sums = {}
    for row in blocks:
        for dc_sg, value in (("DC", row["dc"]), ("SG", row["sg"])):
            if value is None:
                continue
            bucket = sums.setdefault((row["plant_id"], dc_sg), [0.0, 0])
            bucket[0] += value
            bucket[1] += 1
    return {
        key: {"average": total / count, "block_count": count}
        for key, (total, count) in sums.items()
    }


class FakeRepository:
    def __init__(self, blocks=BLOCKS, master=MASTER):
        self.blocks = blocks
        self.master = master
        self.block_calls = []

    async def find_plant_dc_sg_averages(self, from_date, to_date, from_block=1, to_block=96):
        return _averages(self.blocks)

    async def find_plant_blocks(self, from_date, to_date, from_block=1, to_block=96, plant_id=None):
        self.block_calls.append(
            {"from_date": from_date, "to_date": to_date, "from_block": from_block,
             "to_block": to_block, "plant_id": plant_id}
        )
        return [row for row in self.blocks if plant_id is None or row["plant_id"] == plant_id]

    async def find_plant_master(self):
        return self.master


def _service(repository=None):
    service = ReportsPlfPafService()
    service.repository = repository or FakeRepository()
    return service


def _blocks(plant_id, **kwargs):
    kwargs.setdefault("from_date", "2026-09-28")
    kwargs.setdefault("to_date", "2026-09-29")
    return asyncio.run(_service().get_plant_blocks(plant_id=plant_id, **kwargs))


# ---------------------------------------------------------------- block rows


def test_a_block_row_carries_its_own_dc_sg_plf_and_paf():
    noon = _blocks("ACME")["blocks"][1]
    assert noon == {
        "date": "28-09-2026",
        "time_block": 48,
        "time_label": "11:45-12:00",
        "dc": 40.0,
        "sg": 30.0,
        "plf": 75.0,
        "paf": 80.0,
    }


def test_blocks_run_date_by_date_then_block_by_block():
    rows = _blocks("ACME")["blocks"]
    assert [(r["date"], r["time_block"]) for r in rows] == [
        ("28-09-2026", 1),
        ("28-09-2026", 48),
        ("29-09-2026", 48),
    ]


def test_the_last_block_of_the_day_ends_at_midnight():
    repo = FakeRepository(blocks=[
        {"plant_id": "ACME", "date": "2026-09-28", "time_block": 96, "dc": 1.0, "sg": 1.0},
    ])
    result = asyncio.run(_service(repo).get_plant_blocks(
        plant_id="ACME", from_date="2026-09-28", to_date="2026-09-28"))
    assert result["blocks"][0]["time_label"] == "23:45-00:00"


def test_a_block_declared_at_zero_capacity_has_zero_plf_like_the_plant_row():
    block = _blocks("KKNP")["blocks"][0]
    assert (block["dc"], block["sg"], block["plf"], block["paf"]) == (0.0, 35.0, 0.0, 0.0)


def test_a_block_with_no_dc_row_has_no_plf_or_paf():
    block = _blocks("SECI")["blocks"][0]
    assert (block["dc"], block["sg"], block["plf"], block["paf"]) == (None, 5.0, None, None)


def test_a_block_paf_is_null_when_the_master_has_no_contracted_capacity():
    block = _blocks("PULKIT")["blocks"][0]
    assert (block["plf"], block["paf"]) == (93.75, None)


def test_the_blocks_response_names_the_plant():
    assert _blocks("ACME")["plant"] == {
        "plant_id": "ACME",
        "plant_name": "ACME Dhaulpur",
        "technology": "Solar",
        "contracted_capacity": 50.0,
    }


def test_the_blocks_query_reads_only_the_one_plant():
    repo = FakeRepository()
    asyncio.run(_service(repo).get_plant_blocks(
        plant_id="ACME", from_date="2026-09-28", to_date="2026-09-29",
        from_block=5, to_block=20))
    assert repo.block_calls == [{
        "from_date": "2026-09-28", "to_date": "2026-09-29",
        "from_block": 5, "to_block": 20, "plant_id": "ACME",
    }]


def test_the_blocks_query_accepts_a_single_schedule_date():
    result = _blocks("ACME", from_date=None, to_date=None, schedule_date="2026-09-28")
    assert result["query"]["from_date"] == result["query"]["to_date"] == "2026-09-28"


def test_the_blocks_query_refuses_more_than_ninety_days():
    with pytest.raises(ValueError):
        _blocks("ACME", from_date="2026-01-01", to_date="2026-09-28")


def test_the_screen_table_does_not_read_blocks():
    repo = FakeRepository()
    asyncio.run(_service(repo).get_reports_plf_paf(from_date="2026-09-28", to_date="2026-09-29"))
    assert repo.block_calls == []


# ---------------------------------------------------------------- download


def _csv_rows(response_format="csv"):
    result = asyncio.run(_service().get_reports_plf_paf(
        from_date="2026-09-28", to_date="2026-09-29", response_format=response_format))
    return list(csv.reader(io.StringIO(result["csv_export"]["content"])))


def test_the_download_has_plant_date_block_and_time_columns():
    assert CSV_HEADERS == [
        "Plant", "Technology", "Contracted Capacity (MW)", "Date", "Block", "Time",
        "DC (MW)", "SG (MW)", "PLF (%)", "PAF (%)",
    ]
    assert _csv_rows()[0] == CSV_HEADERS


def test_the_download_puts_each_plants_average_before_its_blocks():
    acme = [row for row in _csv_rows()[1:] if row[0] == "ACME Dhaulpur"]
    assert acme == [
        ["ACME Dhaulpur", "Solar", "50.0", "All", "All", "Average", "20.0", "17.33", "86.67", "40.0"],
        ["ACME Dhaulpur", "Solar", "50.0", "28-09-2026", "1", "00:00-00:15", "0.0", "0.0", "0.0", "0.0"],
        ["ACME Dhaulpur", "Solar", "50.0", "28-09-2026", "48", "11:45-12:00", "40.0", "30.0", "75.0", "80.0"],
        ["ACME Dhaulpur", "Solar", "50.0", "29-09-2026", "48", "11:45-12:00", "20.0", "22.0", "110.0", "40.0"],
    ]


def test_the_download_leaves_blank_what_the_screen_shows_as_a_dash():
    seci = [row for row in _csv_rows()[1:] if row[0] == "SECI Wind"]
    assert seci[1] == ["SECI Wind", "Wind", "", "28-09-2026", "1", "00:00-00:15", "", "5.0", "", ""]


def test_the_download_leaves_open_access_out_like_the_screen():
    assert not [row for row in _csv_rows()[1:] if row[0] == "OA_PX"]


def test_the_download_lists_plants_in_the_screen_order():
    plants = []
    for row in _csv_rows()[1:]:
        if row[0] not in plants:
            plants.append(row[0])
    assert plants == ["ACME Dhaulpur", "Kudankulam", "Pulkit Metals", "SECI Wind"]


def test_format_both_embeds_the_same_block_wise_file():
    assert _csv_rows("both") == _csv_rows("csv")


# ---------------------------------------------------------------- routes


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(plf_module.reports_plf_paf_service, "repository", FakeRepository())

    app = FastAPI()
    app.include_router(router)
    audit = MagicMock(spec=AuditLogger)
    app.dependency_overrides[get_user_context] = lambda: {
        "user_id": "test-user", "username": "tester", "correlation_id": "corr-1",
    }
    app.dependency_overrides[get_audit_logger] = lambda: audit
    with TestClient(app) as test_client:
        yield test_client


def test_the_blocks_route_returns_one_plants_blocks(client):
    response = client.get("/reports-plf-paf/blocks", params={
        "plant_id": "ACME", "from_date": "2026-09-28", "to_date": "2026-09-29"})
    assert response.status_code == 200
    body = response.json()
    assert body["plant"]["plant_name"] == "ACME Dhaulpur"
    assert [b["time_block"] for b in body["blocks"]] == [1, 48, 48]


def test_the_blocks_route_needs_a_plant(client):
    response = client.get("/reports-plf-paf/blocks", params={
        "from_date": "2026-09-28", "to_date": "2026-09-29"})
    assert response.status_code == 422


def test_both_download_routes_serve_the_block_wise_file(client):
    params = {"from_date": "2026-09-28", "to_date": "2026-09-29"}
    download = client.get("/reports-plf-paf/download", params=params)
    as_csv = client.get("/reports-plf-paf/", params={**params, "format": "csv"})
    assert download.status_code == as_csv.status_code == 200
    assert download.text == as_csv.text
    assert download.text.splitlines()[0] == ",".join(CSV_HEADERS)
    assert len(download.text.splitlines()) == 1 + 4 + 6  # header, 4 plant rows, 6 blocks


# ---------------------------------------------------------------- real Mongo


def _mongo_available():
    try:
        from pymongo.mongo_client import MongoClient as Direct
        Direct("mongodb://localhost:27017", serverSelectionTimeoutMS=500).admin.command("ping")
        return True
    except Exception:
        return False


requires_mongo = pytest.mark.skipif(not _mongo_available(), reason="needs a local MongoDB")


def _supply(plant_id, day, block, dc_sg, volume):
    return {
        "plant_id": plant_id,
        "scheduled_at": datetime(2026, 9, day),
        "time_block": block,
        "dc_sg": dc_sg,
        "volume": volume,
    }


LIVE = [
    _supply("A", 28, 1, "DC", 10.0), _supply("A", 28, 1, "SG", 9.0),
    _supply("A", 28, 2, "DC", 20.0), _supply("A", 28, 2, "SG", 15.0),
    _supply("A", 28, 3, "DC", 30.0), _supply("A", 28, 3, "SG", 30.0),
    _supply("B", 28, 1, "SG", 4.0),
]
ARCHIVE = [
    _supply("A", 27, 1, "DC", 50.0), _supply("A", 27, 1, "SG", 25.0),
    _supply("A", 27, 2, "DC", 60.0), _supply("A", 27, 2, "SG", 30.0),
    # The roll-boundary overlap: the live copy of the 28th must win.
    _supply("A", 28, 1, "DC", 999.0), _supply("A", 28, 1, "SG", 999.0),
]


async def _with_repository(check):
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient("mongodb://localhost:27017")
    db = client[f"plfpaf_test_{uuid.uuid4().hex[:8]}"]
    try:
        repo = ReportsPlfPafRepository()
        await db[repo.collection_name].insert_many([dict(r) for r in LIVE])
        await db[repo.historical_collection_name].insert_many([dict(r) for r in ARCHIVE])
        repo.repository._db = db
        return await check(repo)
    finally:
        await client.drop_database(db.name)
        client.close()


@requires_mongo
def test_blocks_pair_dc_with_sg_and_take_each_date_from_one_collection():
    async def check(repo):
        return await repo.find_plant_blocks("2026-09-27", "2026-09-28")

    rows = asyncio.run(_with_repository(check))
    assert rows == [
        {"plant_id": "A", "date": "2026-09-27", "time_block": 1, "dc": 50.0, "sg": 25.0},
        {"plant_id": "A", "date": "2026-09-27", "time_block": 2, "dc": 60.0, "sg": 30.0},
        {"plant_id": "A", "date": "2026-09-28", "time_block": 1, "dc": 10.0, "sg": 9.0},
        {"plant_id": "A", "date": "2026-09-28", "time_block": 2, "dc": 20.0, "sg": 15.0},
        {"plant_id": "A", "date": "2026-09-28", "time_block": 3, "dc": 30.0, "sg": 30.0},
        {"plant_id": "B", "date": "2026-09-28", "time_block": 1, "dc": None, "sg": 4.0},
    ]


@requires_mongo
def test_blocks_honour_the_plant_and_block_filters():
    async def check(repo):
        return await repo.find_plant_blocks("2026-09-27", "2026-09-28", 2, 3, plant_id="A")

    rows = asyncio.run(_with_repository(check))
    assert [(r["date"], r["time_block"]) for r in rows] == [
        ("2026-09-27", 2), ("2026-09-28", 2), ("2026-09-28", 3),
    ]


@requires_mongo
def test_blocks_average_back_to_the_plant_row():
    async def check(repo):
        return (
            await repo.find_plant_blocks("2026-09-27", "2026-09-28"),
            await repo.find_plant_dc_sg_averages("2026-09-27", "2026-09-28"),
        )

    blocks, averages = asyncio.run(_with_repository(check))
    for (plant_id, dc_sg), bucket in averages.items():
        values = [b[dc_sg.lower()] for b in blocks
                  if b["plant_id"] == plant_id and b[dc_sg.lower()] is not None]
        assert len(values) == bucket["block_count"]
        assert sum(values) / len(values) == pytest.approx(bucket["average"])
