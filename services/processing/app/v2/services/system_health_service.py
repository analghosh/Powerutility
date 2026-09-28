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
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from libs.health.checks import check_redis_health
from libs.health.health_checker import HealthStatus
from libs.utils.failure_reasons import safe_reason

from ...core.config import settings
from ..helpers import job_health as jh
from ..repository.system_health_repository import RUN_LOG_BY_SERVICE, system_health_repository

logger = logging.getLogger(__name__)

CACHE_SECONDS = 10.0
ACCESS_CACHE_SECONDS = 60.0
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


# Who may open the page. Mirrors the menu: the super-admin roles and wildcard
# holders that see every menu, plus any role granted the System Health item.
SUPER_ADMIN_ROLES = frozenset({"admin", "superadmin", "super_admin"})
PAGE_PERMISSIONS = frozenset({"*", "admin:*", "system_health:read", "system_health:*"})


def has_standing_access(roles: Iterable[str], permissions: Iterable[str]) -> bool:
    """Access that needs no lookup: a super-admin role or a covering permission."""
    if any(str(role).lower() in SUPER_ADMIN_ROLES for role in roles or ()):
        return True
    return any(permission in PAGE_PERMISSIONS for permission in permissions or ())


def health_url(service_url: str) -> str:
    """
    ``/health`` sits at the service root, in front of any ``/api/...`` path:
    ``http://localhost:8001/api/v1/auth`` -> ``http://localhost:8001/health``.
    A proxy prefix before ``/api`` (``https://host/auth/api/v1``) is kept.
    """
    root = service_url.split("/api/", 1)[0] if "/api/" in service_url else service_url
    return root.rstrip("/") + "/health"


def cron_status_url(service_url: str) -> str:
    """Each scheduler lists what it registered at /cron/status, beside /health."""
    return health_url(service_url)[: -len("/health")] + "/cron/status"


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
    """One line, secrets masked, short enough for the page."""
    return safe_reason(message, ERROR_MESSAGE_LIMIT)


def _redis_url() -> str:
    auth = f":{settings.REDIS_PASSWORD}@" if settings.REDIS_PASSWORD else ""
    return f"redis://{auth}{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"


class SystemHealthService:
    def __init__(self):
        self.repository = system_health_repository
        self._cached: Optional[Dict[str, Any]] = None
        self._cached_until = 0.0
        self._lock = asyncio.Lock()
        self._access: Dict[frozenset, Tuple[float, bool]] = {}

    async def can_view(self, user_context: Dict[str, Any]) -> bool:
        """Whether this user's roles may see System Health (see PAGE_PERMISSIONS)."""
        roles = [str(role) for role in user_context.get("roles") or []]
        if has_standing_access(roles, user_context.get("permissions") or []):
            return True
        if not roles:
            return False
        key = frozenset(roles)
        cached = self._access.get(key)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
        try:
            allowed = await self.repository.roles_have_page(roles)
        except Exception as exc:
            logger.error("System health: page grant lookup failed: %s", exc)
            return False
        self._access[key] = (time.monotonic() + ACCESS_CACHE_SECONDS, allowed)
        return allowed

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
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            probes, jobs = await asyncio.gather(
                self._check_services(client), self._check_live_jobs(client, now)
            )
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

    async def _check_services(self, client: httpx.AsyncClient) -> Dict[str, Dict[str, Any]]:
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

    async def _registered(self, client: httpx.AsyncClient) -> Dict[str, Optional[Dict[str, str]]]:
        """
        What the scheduler and ingestion services actually registered, keyed
        by service. None for a service whose listing couldn't be read.
        """
        async def listing(service_url: str) -> Optional[Dict[str, str]]:
            url = cron_status_url(service_url)
            try:
                response = await client.get(url)
                if response.status_code != 200:
                    return None
                return jh.registered_jobs(response.json())
            except Exception as exc:
                logger.warning("System health: %s unreadable: %s", url, exc)
                return None

        scheduler, ingestion = await asyncio.gather(
            listing(settings.SCHEDULER_SERVICE_URL), listing(settings.INGESTION_SERVICE_URL)
        )
        return {"scheduler": scheduler, "ingestion": ingestion}

    async def _check_live_jobs(self, client: httpx.AsyncClient, now: datetime) -> List[Dict[str, Any]]:
        return await self._check_jobs(now, await self._registered(client))

    async def _check_jobs(
        self, now: datetime, registered: Optional[Dict[str, Optional[Dict[str, str]]]] = None
    ) -> List[Dict[str, Any]]:
        try:
            jobs = await self.repository.get_all_jobs()
        except Exception as exc:
            logger.error("System health: could not read job definitions: %s", exc)
            return []

        gate = asyncio.Semaphore(JOB_LOOKUP_CONCURRENCY)
        # (run log, slot) -> was that service logging anything then? Many jobs
        # share a slot (midnight), so each is asked once per check.
        activity: Dict[Tuple[str, datetime], "asyncio.Task[bool]"] = {}

        async def rate(job: Dict[str, Any]) -> Dict[str, Any]:
            async with gate:
                return await self._rate_job(job, now, activity, registered)

        rated = await asyncio.gather(*(rate(job) for job in jobs))
        group_rank = {group: index for index, group in enumerate(jh.GROUP_ORDER)}
        rated.sort(key=lambda job: (group_rank.get(job["group"], 99), job["name"].lower()))
        return rated

    async def _service_was_running(
        self, service: Optional[str], due_at: datetime, activity: Dict
    ) -> Optional[bool]:
        collection = RUN_LOG_BY_SERVICE.get(service or "")
        if not collection:
            return None
        key = (collection, due_at)
        if key not in activity:
            start, end = jh.activity_window(due_at)
            activity[key] = asyncio.ensure_future(
                self.repository.any_run_between(collection, start, end)
            )
        try:
            return await activity[key]
        except Exception as exc:
            logger.warning("System health: activity lookup failed for %s: %s", collection, exc)
            return None

    async def _rate_job(
        self,
        job: Dict[str, Any],
        now: datetime,
        activity: Optional[Dict] = None,
        registered: Optional[Dict[str, Optional[Dict[str, str]]]] = None,
    ) -> Dict[str, Any]:
        job_id = str(job["_id"])
        # Judge the job by what its scheduler registered, not by job_data,
        # when the schedulers could be asked.
        live = jh.live_schedule(job, registered or {})
        configured_schedule = job.get("schedule")
        not_scheduled = live["scheduled"] is False and bool(job.get("enabled", False))
        if live["scheduled"] is not None:
            job = {
                **job,
                "enabled": live["scheduled"],
                "schedule": live["schedule"] or configured_schedule,
            }
        runner = live["service"] or job.get("service")
        summary = {
            "job_type": job.get("job_type"),
            "name": job.get("name") or job.get("job_type") or job_id,
            "description": job.get("description"),
            "service": job.get("service"),
            "group": jh.job_group(job.get("job_type"), job.get("service")),
            "schedule": job.get("schedule"),
            "configured_schedule": configured_schedule,
            "schedule_source": "scheduler" if live["scheduled"] else "job_data",
            "timezone": job.get("timezone"),
            "state": jh.STATE_PAUSED,
            # Enabled in job_data but not registered with any scheduler: it
            # never runs, whatever job_data says.
            "paused_reason": "not_scheduled" if not_scheduled else None,
            "last_run_at": None,
            "last_result": None,
            "last_success_at": None,
            "missed_since": None,
            "missed_reason": None,
            "message": None,
            "failures": [],
            "failures_total": 0,
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
        if verdict["state"] == jh.STATE_MISSED and verdict["due_at"] is not None:
            running = await self._service_was_running(
                runner, verdict["due_at"], activity if activity is not None else {}
            )
            if running is not None:
                summary["missed_reason"] = jh.missed_reason(running)
        if verdict["state"] in (jh.STATE_FAILED, jh.STATE_PARTIAL):
            summary["message"] = _trim((last_finished or {}).get("error_message"))
            failures = jh.provider_failures((last_finished or {}).get("result_data"))
            summary["failures"] = failures[: jh.FAILURE_LIST_LIMIT]
            summary["failures_total"] = len(failures)
        return summary


system_health_service = SystemHealthService()
