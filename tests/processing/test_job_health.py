"""
Tests for the job rules behind the System Health page.

The clock is fixed at 11:47 IST (06:17 UTC) on 28 Sep 2026. Every timestamp a
job reports is its UTC ``created_at``, the way the run logs store it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from services.processing.app.v2.helpers import job_health as jh

NOW = datetime(2026, 9, 28, 6, 17, tzinfo=timezone.utc)  # 11:47 IST


def _job(schedule="*/15 * * * *", enabled=True, created_at=None, **extra):
    job = {
        "job_type": "supply_data_processing",
        "schedule": schedule,
        "timezone": "Asia/Kolkata",
        "enabled": enabled,
        "created_at": created_at or datetime(2026, 1, 1),
    }
    job.update(extra)
    return job


def _utc(hh, mm):
    return datetime(2026, 9, 28, hh, mm, tzinfo=timezone.utc)


def test_a_disabled_job_is_paused_whatever_its_last_run_said():
    result = jh.classify_job(_job(enabled=False), None, {"status": "error"}, NOW)
    assert result["state"] == jh.STATE_PAUSED


def test_a_job_that_ran_in_its_last_slot_and_succeeded_is_ok():
    # */15 IST: the slot 15 minutes before 11:47 is 11:30 IST = 06:00 UTC.
    started = _utc(6, 0)
    result = jh.classify_job(_job(), started, {"status": "success"}, NOW)
    assert result["state"] == jh.STATE_OK


def test_a_failed_last_run_marks_the_job_failed():
    result = jh.classify_job(_job(), _utc(6, 0), {"status": "error"}, NOW)
    assert result["state"] == jh.STATE_FAILED


def test_partial_success_is_its_own_state():
    result = jh.classify_job(_job(), _utc(6, 0), {"status": "partial_success"}, NOW)
    assert result["state"] == jh.STATE_PARTIAL


def test_status_case_does_not_matter():
    result = jh.classify_job(_job(), _utc(6, 0), {"status": "ERROR"}, NOW)
    assert result["state"] == jh.STATE_FAILED


def test_a_job_that_skipped_its_last_slot_is_missed():
    # Last start 05:30 UTC (11:00 IST); the 11:30 IST slot came and went.
    result = jh.classify_job(_job(), _utc(5, 30), {"status": "success"}, NOW)
    assert result["state"] == jh.STATE_MISSED
    assert result["due_at"] == _utc(6, 0)


def test_missed_outranks_a_stale_failure():
    result = jh.classify_job(_job(), _utc(5, 30), {"status": "error"}, NOW)
    assert result["state"] == jh.STATE_MISSED


def test_a_slot_inside_the_grace_period_is_not_yet_missed():
    # Every minute: the 11:46 IST slot is only a minute old, so the rule looks
    # at 11:31 IST (the last slot before now - 15 min) and a start at 11:40
    # covers it.
    every_minute = _job(schedule="* * * * *")
    result = jh.classify_job(every_minute, _utc(6, 10), {"status": "success"}, NOW)
    assert result["state"] == jh.STATE_OK


def test_an_every_minute_job_silent_for_twenty_minutes_is_missed():
    every_minute = _job(schedule="* * * * *")
    result = jh.classify_job(every_minute, _utc(5, 57), {"status": "success"}, NOW)
    assert result["state"] == jh.STATE_MISSED


def test_a_run_a_few_seconds_before_its_slot_still_counts():
    started = _utc(6, 0) - timedelta(seconds=20)
    result = jh.classify_job(_job(), started, {"status": "success"}, NOW)
    assert result["state"] == jh.STATE_OK


def test_an_enabled_job_that_has_never_run_is_missed():
    result = jh.classify_job(_job(), None, None, NOW)
    assert result["state"] == jh.STATE_MISSED


def test_a_job_created_after_its_last_slot_is_not_missed():
    brand_new = _job(schedule="0 2 * * *", created_at=_utc(6, 10).replace(tzinfo=None))
    result = jh.classify_job(brand_new, None, None, NOW)
    assert result["state"] == jh.STATE_OK


def test_a_daily_job_that_ran_this_morning_is_ok():
    # 02:00 IST = 20:30 UTC the previous day.
    daily = _job(schedule="0 2 * * *")
    started = datetime(2026, 9, 27, 20, 30, 1, tzinfo=timezone.utc)
    result = jh.classify_job(daily, started, {"status": "success"}, NOW)
    assert result["state"] == jh.STATE_OK


def test_a_daily_job_that_did_not_run_this_morning_is_missed():
    daily = _job(schedule="0 2 * * *")
    yesterday = datetime(2026, 9, 26, 20, 30, 1, tzinfo=timezone.utc)
    result = jh.classify_job(daily, yesterday, {"status": "success"}, NOW)
    assert result["state"] == jh.STATE_MISSED


def test_naive_created_at_from_mongo_is_read_as_utc():
    naive_start = _utc(6, 0).replace(tzinfo=None)
    result = jh.classify_job(_job(), naive_start, {"status": "success"}, NOW)
    assert result["state"] == jh.STATE_OK


@pytest.mark.parametrize("schedule", ["", "not a cron", "0 0 31 2 *"])
def test_a_schedule_that_never_fires_or_cannot_be_read_skips_the_missed_check(schedule):
    job = _job(schedule=schedule)
    assert jh.classify_job(job, None, {"status": "success"}, NOW)["state"] == jh.STATE_OK


def test_step_syntax_with_a_start_minute_is_understood():
    half_hourly = _job(schedule="0/30 * * * *")
    assert jh.last_due_time("0/30 * * * *", "Asia/Kolkata", NOW) == _utc(6, 0)
    result = jh.classify_job(half_hourly, _utc(6, 0), {"status": "success"}, NOW)
    assert result["state"] == jh.STATE_OK


@pytest.mark.parametrize(
    "job_type, service, group",
    [
        ("api_raw_data_processor", "ingestion", jh.GROUP_DATA_COLLECTION),
        ("supply_data_processing", "scheduler", jh.GROUP_DEMAND_SUPPLY),
        ("weather_data_processing", "scheduler", jh.GROUP_DEMAND_SUPPLY),
        ("dam_single_bid_book_sync", "scheduler", jh.GROUP_BIDDING),
        ("limit_values_approval_processing", "scheduler", jh.GROUP_BIDDING),
        ("exchange_dsm_processing", "scheduler", jh.GROUP_SETTLEMENT),
        ("urs_backfill", "scheduler", jh.GROUP_SETTLEMENT),
        ("bid_serve_archival", "scheduler", jh.GROUP_HOUSEKEEPING),
        ("job_logs_cleanup", "scheduler", jh.GROUP_HOUSEKEEPING),
        ("retry_failed_jobs", "scheduler", jh.GROUP_HOUSEKEEPING),
    ],
)
def test_jobs_fall_into_the_area_an_operator_would_expect(job_type, service, group):
    assert jh.job_group(job_type, service) == group
