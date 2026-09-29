"""
Obligation Report screen: the uploaded PDF, back out exactly as it came in.

Until now an upload kept only the PDF's SHA-256. The screen lists every
obligation report by exchange and market and offers the original file, so the
upload keeps the bytes (keyed by the report's own id, replaced with it), a list
row says whether its file is there, and the download route returns the same
bytes. A report uploaded before this change has no file, and says so rather
than failing obscurely.
"""

import asyncio
import hashlib
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from libs.audit_queue.simple_logger import AuditLogger
from services.processing.app.core.dependencies import get_audit_logger
from services.processing.app.v2.api.router import exchange_report_routes
from services.processing.app.v2.dependencies import get_user_context
from services.processing.app.v2.exchange_reports import registry
from services.processing.app.v2.repository import exchange_report_repository as repo_module
from services.processing.app.v2.repository.exchange_report_repository import (
    COLLECTION, FILE_COLLECTION, ExchangeReportRepository)
from services.processing.app.v2.services import exchange_report_service as service_module
from services.processing.app.v2.services.exchange_report_service import ExchangeReportService

SAMPLE = registry.CATEGORIES[registry.OBLIGATION_REPORT].sample_path
ACTOR = {"user_id": "u-1", "username": "localdev"}


def _mongo_available():
    try:
        from pymongo.mongo_client import MongoClient
        MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=500).admin.command("ping")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mongo_available(), reason="needs a local MongoDB")


def _run(check):
    """Run `check(repository)` against a throwaway database, then drop it."""

    async def wrapper():
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient("mongodb://localhost:27017")
        db = client[f"obligation_files_test_{uuid.uuid4().hex[:8]}"]
        repository = ExchangeReportRepository()
        repository._db = db
        try:
            return await check(repository, db)
        finally:
            await client.drop_database(db.name)
            client.close()

    return asyncio.run(wrapper())


def _document(**over):
    document = {
        "report_category": registry.OBLIGATION_REPORT,
        "exchange": "IEX",
        "market_type": "DAM",
        "delivery_date_iso": "2026-09-19",
        "portfolio_code": "S2PY0PTC0136",
        "source_filename": "report.pdf",
        "uploaded_on_ist_date": "2026-09-19",
    }
    document.update(over)
    return document


# ---------------------------------------------------------------- repository


def test_an_upload_keeps_its_file_under_the_report_s_own_id():
    async def check(repository, db):
        result = await repository.save(_document(), {}, content=b"%PDF-one",
                                       content_type="application/pdf")
        report_id = result["document"]["_id"]
        stored = await repository.find_file(str(report_id))
        return stored, await db[FILE_COLLECTION].count_documents({})

    stored, files = _run(check)
    assert bytes(stored["content"]) == b"%PDF-one"
    assert stored["content_type"] == "application/pdf"
    assert stored["source_filename"] == "report.pdf"
    assert files == 1


def test_a_replacing_upload_replaces_the_file_too():
    async def check(repository, db):
        first = await repository.save(_document(), {}, content=b"%PDF-old",
                                      content_type="application/pdf")
        second = await repository.save(_document(source_filename="reissued.pdf"), {},
                                       content=b"%PDF-new", content_type="application/pdf")
        stored = await repository.find_file(str(second["document"]["_id"]))
        return first, second, stored, await db[FILE_COLLECTION].count_documents({})

    first, second, stored, files = _run(check)
    assert first["document"]["_id"] == second["document"]["_id"]
    assert second["replaced"] is True
    assert bytes(stored["content"]) == b"%PDF-new"
    assert stored["source_filename"] == "reissued.pdf"
    assert files == 1


def test_the_list_knows_which_reports_have_their_file():
    async def check(repository, db):
        kept = await repository.save(_document(), {}, content=b"%PDF",
                                     content_type="application/pdf")
        # A report stored before files were kept: metadata only.
        await db[COLLECTION].insert_one(_document(market_type="RTM"))
        old = await db[COLLECTION].find_one({"market_type": "RTM"})
        ids = [str(kept["document"]["_id"]), str(old["_id"])]
        return ids, await repository.ids_with_files(ids)

    ids, with_files = _run(check)
    assert with_files == {ids[0]}


def test_an_unknown_or_malformed_id_has_no_file():
    async def check(repository, db):
        return await repository.find_file("not-an-id"), await repository.find_file(
            "0123456789abcdef01234567")

    assert _run(check) == (None, None)


# ------------------------------------------------------------------- service


class _Parser:
    def parse_pdf(self, data, filename=None):
        return {
            "market_type": "DAM", "exchange": "IEX", "delivery_date_iso": "2026-09-19",
            "portfolio_code": "S2PY0PTC0136", "trade_blocks": [], "sell_blocks": [],
            "validation": {"passed": True, "checks": []},
        }


def _with_service(monkeypatch, check):
    def run(repository, db):
        monkeypatch.setattr(service_module, "exchange_report_repository", repository)
        service = ExchangeReportService()
        service._obligation_parser = _Parser()
        return check(service, repository, db)

    return _run(run)


def test_the_uploaded_pdf_is_kept_byte_for_byte(monkeypatch):
    data = SAMPLE.read_bytes()

    async def check(service, repository, db):
        result = await service.upload(
            data=data, filename=SAMPLE.name, report_category=registry.OBLIGATION_REPORT,
            exchange="IEX", market_type="DAM", delivery_date=None, actor=ACTOR)
        return result, await service.get_file(result["id"])

    result, stored = _with_service(monkeypatch, check)
    assert stored["content"] == data
    assert hashlib.sha256(stored["content"]).hexdigest() == hashlib.sha256(data).hexdigest()
    assert stored["content_type"] == "application/pdf"
    assert stored["filename"] == SAMPLE.name


def test_list_rows_say_whether_the_file_can_be_downloaded(monkeypatch):
    async def check(service, repository, db):
        await service.upload(
            data=b"%PDF-new", filename="new.pdf", report_category=registry.OBLIGATION_REPORT,
            exchange="IEX", market_type="DAM", delivery_date=None, actor=ACTOR)
        await db[COLLECTION].insert_one(_document(market_type="RTM", file_size_bytes=10))
        return await service.list_reports(report_category=registry.OBLIGATION_REPORT)

    listing = _with_service(monkeypatch, check)
    by_market = {row["market_type"]: row for row in listing["items"]}
    assert by_market["DAM"]["has_file"] is True
    assert by_market["DAM"]["file_size_bytes"] == len(b"%PDF-new")
    assert by_market["RTM"]["has_file"] is False


def test_a_report_without_its_file_is_told_apart_from_no_report(monkeypatch):
    async def check(service, repository, db):
        await db[COLLECTION].insert_one(_document(market_type="RTM"))
        old = await db[COLLECTION].find_one({"market_type": "RTM"})
        missing = await service.get_file("0123456789abcdef01234567")
        with pytest.raises(service_module.FileNotKept):
            await service.get_file(str(old["_id"]))
        return missing

    assert _with_service(monkeypatch, check) is None


# -------------------------------------------------------------------- routes


def _client():
    app = FastAPI()
    app.include_router(exchange_report_routes.router)
    app.dependency_overrides[get_user_context] = lambda: {"user_id": "u-1", "username": "t"}
    app.dependency_overrides[get_audit_logger] = lambda: MagicMock(spec=AuditLogger)
    return TestClient(app)


class _FileService:
    def __init__(self, result=None, raises=None):
        self.result, self.raises = result, raises

    async def get_file(self, report_id):
        if self.raises:
            raise self.raises
        return self.result


def test_the_download_route_returns_the_pdf_as_an_attachment(monkeypatch):
    data = SAMPLE.read_bytes()
    monkeypatch.setattr(exchange_report_routes, "exchange_report_service", _FileService(
        {"content": data, "content_type": "application/pdf", "filename": SAMPLE.name}))
    response = _client().get("/exchange-reports/0123456789abcdef01234567/file")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == f'attachment; filename="{SAMPLE.name}"'
    assert response.content == data


def test_the_download_route_404s_for_no_such_report(monkeypatch):
    monkeypatch.setattr(exchange_report_routes, "exchange_report_service", _FileService(None))
    response = _client().get("/exchange-reports/0123456789abcdef01234567/file")
    assert response.status_code == 404
    assert response.json()["detail"] == "No such report"


def test_the_download_route_says_when_the_file_was_never_kept(monkeypatch):
    monkeypatch.setattr(exchange_report_routes, "exchange_report_service",
                        _FileService(raises=service_module.FileNotKept("old.pdf")))
    response = _client().get("/exchange-reports/0123456789abcdef01234567/file")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["reason"] == "file_not_kept"
    assert "upload it again" in detail["message"].lower()
