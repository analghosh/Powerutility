"""
The renewable archival job failed on its own healthy path.

`archived_count_actual` was initialised inside the `else` branch -- the one taken only
when there ARE records to archive. On any run with nothing old enough, the `if not
archival_candidates` branch ran instead and the result dict at the end of the method read
a variable that had never been bound:

    Error: cannot access local variable 'archived_count_actual' where it is not
    associated with a value
    === RENEWABLE SERVE ARCHIVAL JOB FAILED ===

So the job reported failure precisely when there was nothing to do, which is most days.
Observed four times in a single local session before the fix.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from libs.database.mongo_client_factory import MongoClientFactory

with patch.object(MongoClientFactory, "get_client", return_value=MagicMock()):
    from services.scheduler.app.processors.renewable_processor import RenewableProcessor


def _processor(candidates):
    """A processor whose repository returns `candidates` and swallows everything else."""
    processor = RenewableProcessor.__new__(RenewableProcessor)

    serve_collection = SimpleNamespace(
        delete_many=AsyncMock(return_value=SimpleNamespace(deleted_count=0))
    )
    processor.renewable_repository = SimpleNamespace(
        find_many=AsyncMock(return_value=candidates),
        serve_collection=serve_collection,
    )

    processor._record_job_start = AsyncMock(return_value="exec-1")
    processor._record_job_completion = AsyncMock()
    processor._record_job_failure = AsyncMock()
    processor.set_correlation_id = MagicMock()
    return processor


class TestArchivalWithNothingToArchive:
    @pytest.mark.asyncio
    async def test_the_job_succeeds_when_there_is_nothing_to_archive(self):
        processor = _processor([])

        result = await processor.execute_archival_job()

        assert result["status"] == "success", result
        assert result["archived_count"] == 0

    @pytest.mark.asyncio
    async def test_it_reports_completion_rather_than_failure(self):
        processor = _processor([])

        await processor.execute_archival_job()

        assert processor._record_job_completion.await_count == 1
        assert processor._record_job_failure.await_count == 0
