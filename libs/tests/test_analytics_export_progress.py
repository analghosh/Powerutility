"""A background export must report progress while it is writing, not at the end.

This is the half of "don't block large exports" that cannot be proved from the
outside on this machine: local Mongo holds ~43 days, so a real export finishes in
about a second and the bar never has to move. These drive the writer directly with
a response of PROD shape — a year of individual blocks — and watch the updates land.

The defect they were written to catch: `_write_file` runs on a worker thread via
`asyncio.to_thread`, and it read the event loop with `asyncio.get_running_loop()`
*inside* that thread, where there is none. Every progress update was therefore
dropped and the bar sat at 0% until the file was finished — which looks exactly
like an export that has hung.
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.helpers import analytics_layout  # noqa: E402
from services.processing.app.v2.services.analytics_export_service import (  # noqa: E402
    PROGRESS_EVERY_ROWS, AnalyticsExportService)

DAYS, BLOCKS, SERIES = 60, 96, 6          # 5,760 rows — big enough to tick, quick to run

FILTERS = {
    "from_date": "2026-01-01", "to_date": "2026-03-01", "from_block": 1,
    "to_block": 96, "date_grouping": "BLOCK_DATE_WISE",
    "block_grouping": "INDIVIDUAL", "datasets": ["D0"], "format": "xlsx",
}


def _response(days=DAYS, blocks=BLOCKS, series=SERIES):
    dates = [f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}" for i in range(days)]
    matrix = [[float(b * 1000 + d) for d in range(days)] for b in range(blocks)]
    return {
        "axes": {"dates": dates, "blocks": list(range(1, blocks + 1))},
        "series": [{"dataset": f"D{i}", "label": f"Dataset {i}", "unit": "MW",
                    "entity": None, "values": matrix} for i in range(series)],
        "meta": {"generated_at": "2026-09-20T00:00:00+00:00",
                 "date_grouping": "BLOCK_DATE_WISE"},
    }


class RecordingJobs:
    """Captures every progress write the service makes."""

    def __init__(self):
        self.updates = []

    async def set_progress(self, job_id, rows_done, rows_total=None):
        self.updates.append((job_id, rows_done, rows_total))
        return True


async def _write(tmp_path, fmt="xlsx", response=None):
    jobs = RecordingJobs()
    service = AnalyticsExportService(jobs=jobs)
    response = response or _response()
    total = analytics_layout.row_count(response)
    path = os.path.join(str(tmp_path), f"export.{fmt}")
    written = await asyncio.to_thread(
        service._write_file, response, dict(FILTERS, format=fmt), path, fmt,
        "job-1", total, asyncio.get_running_loop())
    # `run_coroutine_threadsafe` schedules onto this loop; give it a turn to drain.
    await asyncio.sleep(0.3)
    return jobs, written, total


@pytest.mark.asyncio
async def test_progress_lands_while_the_file_is_being_written(tmp_path):
    jobs, _, total = await _write(tmp_path)
    counts = [u[1] for u in jobs.updates]

    assert counts, "no progress was reported at all"
    assert len(counts) > 1, (
        f"progress landed once, at the end: {counts}. The loop was probably read "
        f"from inside the worker thread again.")
    assert counts[0] < total, f"the first update was already complete: {counts[0]}"


@pytest.mark.asyncio
async def test_progress_only_moves_forward_and_finishes_exactly(tmp_path):
    jobs, written, total = await _write(tmp_path)
    counts = [u[1] for u in jobs.updates]

    assert counts == sorted(counts), f"progress went backwards: {counts}"
    assert counts[-1] == total, f"final progress {counts[-1]} != {total} rows"
    assert written["row_count"] == total


@pytest.mark.asyncio
async def test_progress_is_throttled_rather_than_written_per_row(tmp_path):
    """A Mongo round trip per row would cost more than the export itself."""
    jobs, _, total = await _write(tmp_path)
    counts = [u[1] for u in jobs.updates]

    assert len(counts) < total / 10, (
        f"{len(counts)} writes for {total} rows — the throttle is not working")
    # Every update except the last lands on a multiple of the interval.
    for value in counts[:-1]:
        assert value % PROGRESS_EVERY_ROWS == 0, counts


@pytest.mark.asyncio
async def test_a_csv_background_export_reports_progress_too(tmp_path):
    """CSV takes the same path when it is built in the background."""
    jobs, written, total = await _write(tmp_path, fmt="csv")
    counts = [u[1] for u in jobs.updates]

    assert len(counts) > 1
    assert counts[-1] == total
    # The `#` filter block and the heading row are not data rows.
    assert written["row_count"] == total


@pytest.mark.asyncio
async def test_a_dropped_progress_update_never_fails_the_write(tmp_path):
    """Losing a counter must not cost the user their file."""
    class BrokenJobs(RecordingJobs):
        async def set_progress(self, *a, **kw):
            raise RuntimeError("mongo is down")

    service = AnalyticsExportService(jobs=BrokenJobs())
    response = _response(days=10)
    path = os.path.join(str(tmp_path), "export.xlsx")
    written = await asyncio.to_thread(
        service._write_file, response, FILTERS, path, "xlsx", "job-1",
        analytics_layout.row_count(response), asyncio.get_running_loop())
    await asyncio.sleep(0.2)

    assert written["row_count"] == analytics_layout.row_count(response)
    assert os.path.getsize(written["path"]) > 0


@pytest.mark.asyncio
async def test_no_loop_means_no_progress_but_still_a_file(tmp_path):
    """The writer is usable outside an event loop — the tests for it are."""
    service = AnalyticsExportService(jobs=RecordingJobs())
    response = _response(days=5)
    path = os.path.join(str(tmp_path), "export.xlsx")
    written = service._write_file(response, FILTERS, path, "xlsx", "job-1",
                                  analytics_layout.row_count(response), None)
    assert written["row_count"] == analytics_layout.row_count(response)
