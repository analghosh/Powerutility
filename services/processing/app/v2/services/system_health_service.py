"""
System Health: one answer to "is EPMS working right now?"

Checks each backend service and the database and cache it depends on, then
rates every scheduled job with the rules in ``helpers.job_health``. The page
polls this, so the result is cached for a few seconds and only one check runs
at a time however many screens are open.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from libs.health.checks import check_redis_health
from libs.health.health_checker import HealthStatus

from ...core.config import settings
from ..helpers import job_health as jh
from ..repository.system_health_repository import system_health_repository

logger = logging.getLogger(__name__)

CACHE_SECONDS = 10.0
PROBE_TIMEOUT_SECONDS = 3.0
# Parallel job lookups; each is a few indexed find_one calls.
JOB_LOOKUP_CONCURRENCY = 16
ERROR_MESSAGE_LIMIT = 300

STATUS_UP = "up"
STATUS_DEGRADED = "degraded"
STATUS_DOWN = "down"

OVERALL_OK = "ok"
OVERALL_ATTENTION = "attention"
OVERALL_DOWN = "down"

# key, display name, what it does for the user. Order is the order on the page.
SERVICES: Tuple[Tuple[str, str, str], ...] = (
    ("auth", "Login & Access", "Sign-in, user accounts and permissions"),
    ("ingestion", "Data Collection", "Brings in exchange, grid and forecast data"),
    ("processing", "Processing & Bidding", "Forecasts, schedules, bids and reports"),
    ("scheduler", "Job Scheduler", "Starts the automatic jobs on time"),
    ("database", "Database", "Stores all EPMS data"),
    ("cache", "Cache", "Keeps screens fast and sessions active"),
)


def health_url(service_url: str) -> str:
    """
    ``/health`` sits at the service root, in front of any ``/api/...`` path:
    ``http://localhost:8001/api/v1/auth`` -> ``http://localhost:8001/health``.
    A proxy prefix before ``/api`` (``https://host/auth/api/v1``) is kept.
    """
    root = service_url.split("/api/", 1)[0] if "/api/" in service_url else service_url
    return root.rstrip("/") + "/health"


def overall_status(services: List[Dict[str, Any]], jobs: List[Dict[str, Any]]) -> str:
    if any(service["status"] != STATUS_UP for service in services):
        return OVERALL_DOWN
    if any(job["state"] in jh.ATTENTION_STATES for job in jobs):
        return OVERALL_ATTENTION
    return OVERALL_OK


def _iso(value: Optional[datetime]) -> Optional[str]:
    value = jh.as_utc(value)
    return value.isoformat() if value else None


def _trim(message: Optional[str]) -> Optional[str]:
    if not message:
        return None
    message = str(message).strip()
    if len(message) > ERROR_MESSAGE_LIMIT:
        return message[: ERROR_MESSAGE_LIMIT - 1].rstrip() + "…"
    return message


def _redis_url() -> str:
    auth = f":{settings.REDIS_PASSWORD}@" if settings.REDIS_PASSWORD else ""
    return f"redis://{auth}{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"


class SystemHealthService:
    def __init__(self):
        self.repository = system_health_repository
        self._cached: Optional[Dict[str, Any]] = None
        self._cached_until = 0.0
        self._lock = asyncio.Lock()

    async def get_system_health(self) -> Dict[str, Any]:
        if self._cached and time.monotonic() < self._cached_until:
            return self._cached
        async with self._lock:
            if self._cached and time.monotonic() < self._cached_until:
                return self._cached
            result = await self._check()
            self._cached = result
            self._cached_until = time.monotonic() + CACHE_SECONDS
            return result

    async def _check(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        probes, jobs = await asyncio.gather(self._check_services(), self._check_jobs(now))
        services = [
            {"key": key, "name": name, "description": description, **probes[key]}
            for key, name, description in SERVICES
        ]
        return {
            "checked_at": now.isoformat(),
            "overall": overall_status(services, jobs),
            "services": services,
            "jobs": jobs,
        }

    # ---- services ------------------------------------------------------

    async def _check_services(self) -> Dict[str, Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            auth, ingestion, scheduler, database, cache = await asyncio.gather(
                self._probe_http(client, settings.AUTH_SERVICE_URL),
                self._probe_http(client, settings.INGESTION_SERVICE_URL),
                self._probe_http(client, settings.SCHEDULER_SERVICE_URL, scheduler=True),
                self._probe_database(),
                self._probe_cache(),
            )
        return {
            "auth": auth,
            "ingestion": ingestion,
            # This request is being answered by processing, so it is up.
            "processing": {"status": STATUS_UP, "response_ms": None, "message": None},
            "scheduler": scheduler,
            "database": database,
            "cache": cache,
        }

    async def _probe_http(
        self, client: httpx.AsyncClient, service_url: str, scheduler: bool = False
    ) -> Dict[str, Any]:
        url = health_url(service_url)
        started = time.perf_counter()
        try:
            response = await client.get(url)
            elapsed = round((time.perf_counter() - started) * 1000)
            body = response.json() if response.status_code == 200 else {}
        except Exception as exc:
            logger.warning("System health: %s unreachable: %s", url, exc)
            return {"status": STATUS_DOWN, "response_ms": None, "message": "Not responding"}

        if response.status_code != 200 or str(body.get("status", "")).lower() != "healthy":
            return {
                "status": STATUS_DOWN,
                "response_ms": elapsed,
                "message": f"Health check answered {response.status_code}",
            }
        if scheduler and body.get("scheduler_running") is False:
            return {
                "status": STATUS_DEGRADED,
                "response_ms": elapsed,
                "message": "Running, but not starting any jobs",
            }
        return {"status": STATUS_UP, "response_ms": elapsed, "message": None}

    async def _probe_database(self) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            await asyncio.wait_for(self.repository.ping(), timeout=PROBE_TIMEOUT_SECONDS)
        except Exception as exc:
            logger.warning("System health: MongoDB ping failed: %s", exc)
            return {"status": STATUS_DOWN, "response_ms": None, "message": "Not responding"}
        elapsed = round((time.perf_counter() - started) * 1000)
        return {"status": STATUS_UP, "response_ms": elapsed, "message": None}

    async def _probe_cache(self) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                check_redis_health(_redis_url()), timeout=PROBE_TIMEOUT_SECONDS
            )
        except Exception as exc:
            logger.warning("System health: Redis check failed: %s", exc)
            return {"status": STATUS_DOWN, "response_ms": None, "message": "Not responding"}
        elapsed = round((time.perf_counter() - started) * 1000)
        if result.status != HealthStatus.HEALTHY:
            logger.warning("System health: Redis unhealthy: %s", result.message)
            return {"status": STATUS_DOWN, "response_ms": elapsed, "message": "Not responding"}
        return {"status": STATUS_UP, "response_ms": elapsed, "message": None}

    # ---- jobs ----------------------------------------------------------

    async def _check_jobs(self, now: datetime) -> List[Dict[str, Any]]:
        try:
            jobs = await self.repository.get_all_jobs()
        except Exception as exc:
            logger.error("System health: could not read job definitions: %s", exc)
            return []

        gate = asyncio.Semaphore(JOB_LOOKUP_CONCURRENCY)

        async def rate(job: Dict[str, Any]) -> Dict[str, Any]:
            async with gate:
                return await self._rate_job(job, now)

        rated = await asyncio.gather(*(rate(job) for job in jobs))
        group_rank = {group: index for index, group in enumerate(jh.GROUP_ORDER)}
        rated.sort(key=lambda job: (group_rank.get(job["group"], 99), job["name"].lower()))
        return rated

    async def _rate_job(self, job: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        job_id = str(job["_id"])
        summary = {
            "job_type": job.get("job_type"),
            "name": job.get("name") or job.get("job_type") or job_id,
            "description": job.get("description"),
            "service": job.get("service"),
            "group": jh.job_group(job.get("job_type"), job.get("service")),
            "schedule": job.get("schedule"),
            "timezone": job.get("timezone"),
            "state": jh.STATE_PAUSED,
            "last_run_at": None,
            "last_result": None,
            "last_success_at": None,
            "missed_since": None,
            "message": None,
        }
        if not job.get("enabled", False):
            return summary

        try:
            last_row, last_finished = await asyncio.gather(
                self.repository.latest_run(job_id),
                self.repository.latest_run(job_id, jh.FINISHED_STATUSES),
            )
            last_success = last_finished
            if not last_finished or last_finished.get("status") not in jh.SUCCESS_STATUSES:
                last_success = await self.repository.latest_run(job_id, jh.SUCCESS_STATUSES)
        except Exception as exc:
            logger.error("System health: run lookup failed for %s: %s", job_id, exc)
            summary.update(state=jh.STATE_FAILED, message="Could not read this job's run history")
            return summary

        verdict = jh.classify_job(
            job, (last_row or {}).get("created_at"), last_finished, now
        )
        summary.update(
            state=verdict["state"],
            last_run_at=_iso((last_row or {}).get("created_at")),
            last_result=(last_finished or {}).get("status"),
            last_success_at=_iso((last_success or {}).get("created_at")),
            missed_since=_iso(verdict["due_at"]) if verdict["state"] == jh.STATE_MISSED else None,
        )
        if verdict["state"] in (jh.STATE_FAILED, jh.STATE_PARTIAL):
            summary["message"] = _trim((last_finished or {}).get("error_message"))
        return summary


system_health_service = SystemHealthService()
