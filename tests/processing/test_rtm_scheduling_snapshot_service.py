"""
Unit tests for RTM scheduling snapshot service (query shape, pagination, excel bounds).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException

from services.processing.app.v2.services.rtm_scheduling_snapshot_services import (
    RTMSchedulingSnapshotService,
)


class TestNormalizeScheduleDate:
    def test_iso_date_prefix(self):
        svc = RTMSchedulingSnapshotService()
        assert svc._normalize_schedule_date("2026-04-13") == "2026-04-13"
        assert svc._normalize_schedule_date("2026-04-13T17:00:00") == "2026-04-13"

    def test_invalid_raises(self):
        svc = RTMSchedulingSnapshotService()
        with pytest.raises(HTTPException) as exc:
            svc._normalize_schedule_date("13-04-2026")
        assert exc.value.status_code == 400


class TestBuildBaseMatch:
    def test_includes_schedule_date_or_and_source_in(self):
        svc = RTMSchedulingSnapshotService()
        m = svc._build_base_match("2026-04-13", None, None)
        assert "$and" in m
        assert m["$and"][0]["$or"][0]["schedule_date"] == "2026-04-13"
        assert "$gte" in m["$and"][0]["$or"][1]["schedule_date"]
        assert "$in" in m["$and"][1]["source_name"]
        assert set(m["$and"][1]["source_name"]["$in"]) == set(svc.RTM_SNAPSHOT_SOURCE_LIST)

    def test_session_and_run_id(self):
        svc = RTMSchedulingSnapshotService()
        m = svc._build_base_match("2026-04-13", "42", "run-abc")
        assert m["$and"][2]["session_id"] == {"$in": ["42", 42]}
        assert m["$and"][3]["run_id"] == "run-abc"


class TestExcelExportScope:
    def test_requires_session_or_run(self):
        svc = RTMSchedulingSnapshotService()
        with pytest.raises(HTTPException) as exc:
            svc.validate_excel_export_scope(None, None)
        assert exc.value.status_code == 422

    def test_accepts_session_id(self):
        svc = RTMSchedulingSnapshotService()
        svc.validate_excel_export_scope("1", None)

    def test_accepts_run_id(self):
        svc = RTMSchedulingSnapshotService()
        svc.validate_excel_export_scope(None, "run-x")


@pytest.mark.asyncio
class TestSnapshotPaginationPipeline:
    async def test_leading_match_and_skip_limit(self):
        svc = RTMSchedulingSnapshotService()
        mock_repo = MagicMock()
        mock_repo.count_snapshot_documents = AsyncMock(return_value=100)
        mock_repo.get_rtm_snapshot_rows = AsyncMock(return_value=[])
        svc.repository = mock_repo

        out = await svc.get_rtm_scheduling_snapshot_data(
            None, "2026-04-13", page=2, page_size=10, run_id=None
        )

        assert mock_repo.count_snapshot_documents.await_count == 1
        count_filter = mock_repo.count_snapshot_documents.await_args.args[0]
        assert count_filter["$and"][0]["$or"][0]["schedule_date"] == "2026-04-13"

        pipeline = mock_repo.get_rtm_snapshot_rows.await_args.args[0]
        assert pipeline[0]["$match"]["$and"][0]["$or"][0]["schedule_date"] == "2026-04-13"
        assert pipeline[-2] == {"$skip": 10}
        assert pipeline[-1] == {"$limit": 10}

        assert out["pagination"]["page"] == 2
        assert out["pagination"]["page_size"] == 10
        assert out["pagination"]["total_matched"] == 100
        assert out["pagination"]["has_next"] is True

    async def test_skip_pagination_no_skip_limit(self):
        svc = RTMSchedulingSnapshotService()
        mock_repo = MagicMock()
        mock_repo.count_snapshot_documents = AsyncMock(return_value=5)
        mock_repo.get_rtm_snapshot_rows = AsyncMock(return_value=[])
        svc.repository = mock_repo

        await svc.get_rtm_scheduling_snapshot_data(
            "1", "2026-04-13", skip_pagination=True, max_time_ms=60_000
        )

        pipeline = mock_repo.get_rtm_snapshot_rows.await_args.args[0]
        stage_types = [list(s.keys())[0] for s in pipeline]
        assert "$skip" not in stage_types
        assert "$limit" not in stage_types
