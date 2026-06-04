"""
Cron Orchestrator Service
Triggers all registered cron jobs across ingestion and scheduler services,
waiting for every trigger call to complete before returning.
"""

import asyncio
import logging
from typing import Dict, Any, List
from urllib.parse import urlparse

import httpx

from ...core.config import settings

logger = logging.getLogger(__name__)

INGESTION_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
SCHEDULER_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


def _ingestion_base_url() -> str:
    """
    Return just scheme+host+port from INGESTION_SERVICE_URL.
    The cron endpoints (/cron/status, /cron/trigger/...) are mounted at the
    ingestion app root, not under /api/v1, so we must strip any path suffix
    that may be present in the config value (e.g. http://host:8002/api/v1).
    """
    parsed = urlparse(settings.INGESTION_SERVICE_URL)
    return f"{parsed.scheme}://{parsed.netloc}"


class CronOrchestratorService:

    async def _get_ingestion_jobs(self, client: httpx.AsyncClient) -> List[str]:
        """Fetch registered job IDs from the ingestion service."""
        try:
            resp = await client.get(
                f"{_ingestion_base_url()}/cron/status",
                timeout=INGESTION_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()
            jobs = data.get("jobs", {})
            if isinstance(jobs, dict):
                return list(jobs.keys())
            if isinstance(jobs, list):
                return [j.get("job_id") or j.get("id") for j in jobs if j.get("job_id") or j.get("id")]
            return []
        except Exception as e:
            logger.error(f"Failed to fetch ingestion jobs: {e}")
            return []

    async def _trigger_ingestion_job(self, client: httpx.AsyncClient, job_id: str) -> Dict[str, Any]:
        """Trigger a single ingestion job."""
        try:
            resp = await client.post(
                f"{_ingestion_base_url()}/cron/trigger/{job_id}",
                timeout=INGESTION_TIMEOUT
            )
            resp.raise_for_status()
            return {"job_id": job_id, "success": True, "detail": resp.json()}
        except Exception as e:
            logger.error(f"Failed to trigger ingestion job '{job_id}': {e}")
            return {"job_id": job_id, "success": False, "detail": str(e)}

    async def _get_scheduler_jobs(self, client: httpx.AsyncClient) -> List[str]:
        """Fetch registered job IDs from the scheduler service."""
        try:
            resp = await client.get(
                f"{settings.SCHEDULER_SERVICE_URL}/api/v1/scheduler/scheduler/jobs/all",
                timeout=SCHEDULER_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()
            jobs = data.get("jobs", [])
            return [j.get("job_id") for j in jobs if j.get("job_id")]
        except Exception as e:
            logger.error(f"Failed to fetch scheduler jobs: {e}")
            return []

    async def _trigger_scheduler_job(self, client: httpx.AsyncClient, job_id: str) -> Dict[str, Any]:
        """Trigger a single scheduler job."""
        try:
            resp = await client.post(
                f"{settings.SCHEDULER_SERVICE_URL}/api/v1/scheduler/scheduler/jobs/{job_id}/trigger",
                timeout=SCHEDULER_TIMEOUT
            )
            resp.raise_for_status()
            return {"job_id": job_id, "success": True, "detail": resp.json()}
        except Exception as e:
            logger.error(f"Failed to trigger scheduler job '{job_id}': {e}")
            return {"job_id": job_id, "success": False, "detail": str(e)}

    async def trigger_all(self) -> Dict[str, Any]:
        """
        Trigger all crons in ingestion then scheduler, wait for every call to finish.
        Returns a summary of what was triggered and whether each succeeded.
        """
        async with httpx.AsyncClient() as client:
            # ── 1. Ingestion ──────────────────────────────────────────────
            ingestion_job_ids = await self._get_ingestion_jobs(client)
            logger.info(f"Triggering {len(ingestion_job_ids)} ingestion jobs: {ingestion_job_ids}")

            ingestion_results = await asyncio.gather(
                *[self._trigger_ingestion_job(client, jid) for jid in ingestion_job_ids],
                return_exceptions=False
            )

            # ── 2. Scheduler ──────────────────────────────────────────────
            scheduler_job_ids = await self._get_scheduler_jobs(client)
            logger.info(f"Triggering {len(scheduler_job_ids)} scheduler jobs: {scheduler_job_ids}")

            scheduler_results = await asyncio.gather(
                *[self._trigger_scheduler_job(client, jid) for jid in scheduler_job_ids],
                return_exceptions=False
            )

        ingestion_triggered = sum(1 for r in ingestion_results if r.get("success"))
        scheduler_triggered = sum(1 for r in scheduler_results if r.get("success"))

        return {
            "ingestion": {
                "total": len(ingestion_job_ids),
                "triggered": ingestion_triggered,
                "results": list(ingestion_results)
            },
            "scheduler": {
                "total": len(scheduler_job_ids),
                "triggered": scheduler_triggered,
                "results": list(scheduler_results)
            },
            "summary": {
                "total_jobs": len(ingestion_job_ids) + len(scheduler_job_ids),
                "total_triggered": ingestion_triggered + scheduler_triggered
            }
        }


cron_orchestrator_service = CronOrchestratorService()
