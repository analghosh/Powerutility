"""Unit tests for DemandServePipelineProcessor."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.scheduler.app.processors.demand_serve_pipeline_processor import (
    DemandServePipelineProcessor,
    PIPELINE_STEPS,
)


@pytest.fixture
def mock_processors():
    idf = MagicMock()
    demand = MagicMock()
    dsm = MagicMock()
    return idf, demand, dsm


@pytest.mark.asyncio
async def test_pipeline_runs_steps_in_order(mock_processors):
    idf, demand, dsm = mock_processors
    call_order: list[str] = []

    async def run_idf():
        call_order.append("idf")
        return {"status": "success", "message": "idf ok", "execution_time": 0.1}

    async def run_demand():
        call_order.append("demand")
        return {"status": "success", "message": "demand ok", "execution_time": 0.2}

    async def run_dsm():
        call_order.append("dsm")
        return {"status": "success", "message": "dsm ok", "execution_time": 0.3}

    idf.execute_cron_job = AsyncMock(side_effect=run_idf)
    demand.execute_cron_job = AsyncMock(side_effect=run_demand)
    dsm.execute_cron_job = AsyncMock(side_effect=run_dsm)

    proc = DemandServePipelineProcessor(
        idf_processor=idf,
        demand_processor=demand,
        dsm_processor=dsm,
    )

    with patch.object(proc, "_record_job_start", AsyncMock(return_value="exec-1")), patch.object(
        proc, "_record_job_completion", AsyncMock()
    ):
        result = await proc.execute_cron_job()

    assert result["status"] == "success"
    assert len(result["steps"]) == len(PIPELINE_STEPS)
    assert [s["step"] for s in result["steps"]] == [t[0] for t in PIPELINE_STEPS]
    assert call_order == ["idf", "demand", "dsm"]


@pytest.mark.asyncio
async def test_pipeline_stops_after_demand_failure(mock_processors):
    idf, demand, dsm = mock_processors
    idf.execute_cron_job = AsyncMock(
        return_value={"status": "success", "message": "idf ok", "execution_time": 0.1}
    )
    demand.execute_cron_job = AsyncMock(
        return_value={"status": "error", "message": "demand failed", "execution_time": 0.1}
    )

    proc = DemandServePipelineProcessor(
        idf_processor=idf,
        demand_processor=demand,
        dsm_processor=dsm,
    )

    with patch.object(proc, "_record_job_start", AsyncMock(return_value="exec-2")), patch.object(
        proc, "_record_job_completion", AsyncMock()
    ):
        result = await proc.execute_cron_job()

    assert result["status"] == "error"
    idf.execute_cron_job.assert_awaited_once()
    demand.execute_cron_job.assert_awaited_once()
    dsm.execute_cron_job.assert_not_awaited()
    assert result["steps"][2]["status"] == "skipped"


@pytest.mark.asyncio
async def test_pipeline_continues_on_idf_partial(mock_processors):
    idf, demand, dsm = mock_processors
    idf.execute_cron_job = AsyncMock(
        return_value={"status": "partial", "message": "idf partial", "execution_time": 0.1}
    )
    demand.execute_cron_job = AsyncMock(
        return_value={"status": "success", "message": "demand ok", "execution_time": 0.2}
    )
    dsm.execute_cron_job = AsyncMock(
        return_value={"status": "success", "message": "dsm ok", "execution_time": 0.3}
    )

    proc = DemandServePipelineProcessor(
        idf_processor=idf,
        demand_processor=demand,
        dsm_processor=dsm,
    )

    with patch.object(proc, "_record_job_start", AsyncMock(return_value="exec-3")), patch.object(
        proc, "_record_job_completion", AsyncMock()
    ):
        result = await proc.execute_cron_job()

    assert result["status"] == "partial"
    demand.execute_cron_job.assert_awaited_once()
    dsm.execute_cron_job.assert_awaited_once()
