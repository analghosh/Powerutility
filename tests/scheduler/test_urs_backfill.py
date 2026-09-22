"""
Guards on the one-time URS backfill.

The two properties that matter to the operator are that it never reaches for a
date the feed has not settled yet, and that it cannot be made to run again by
restarting the service. The first is pure logic and is asserted here; the second
lives in the ``urs_backfill_state`` collection and is asserted in
``test_only_unattempted_days_are_returned``.
"""

import os
import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from services.scheduler.app.services import urs_backfill


class ResolveWindowTests(unittest.TestCase):
    """The window is always strictly behind today."""

    def setUp(self):
        for key in ("URS_BACKFILL_DAYS", "URS_BACKFILL_FROM", "URS_BACKFILL_TO"):
            os.environ.pop(key, None)
        self.addCleanup(self._clear)

    @staticmethod
    def _clear():
        for key in ("URS_BACKFILL_DAYS", "URS_BACKFILL_FROM", "URS_BACKFILL_TO"):
            os.environ.pop(key, None)

    def test_the_window_ends_yesterday(self):
        window = urs_backfill.resolve_window()
        self.assertEqual(window[-1], date.today() - timedelta(days=1))
        self.assertEqual(len(window), urs_backfill.DEFAULT_DAYS)

    def test_an_explicit_window_is_clamped_to_yesterday(self):
        """Asking for future days yields the past ones, not an error and not a guess."""
        os.environ["URS_BACKFILL_FROM"] = str(date.today() - timedelta(days=3))
        os.environ["URS_BACKFILL_TO"] = str(date.today() + timedelta(days=10))
        window = urs_backfill.resolve_window()
        self.assertEqual(window[-1], date.today() - timedelta(days=1))
        self.assertTrue(all(day < date.today() for day in window))

    def test_a_window_entirely_in_the_future_yields_nothing(self):
        os.environ["URS_BACKFILL_FROM"] = str(date.today() + timedelta(days=1))
        os.environ["URS_BACKFILL_TO"] = str(date.today() + timedelta(days=5))
        self.assertEqual(urs_backfill.resolve_window(), [])

    def test_an_oversized_request_is_capped_not_honoured(self):
        os.environ["URS_BACKFILL_DAYS"] = "3650"
        self.assertEqual(len(urs_backfill.resolve_window()), urs_backfill.MAX_DAYS)

    def test_a_junk_day_count_falls_back_to_the_default(self):
        os.environ["URS_BACKFILL_DAYS"] = "last week"
        self.assertEqual(len(urs_backfill.resolve_window()), urs_backfill.DEFAULT_DAYS)

    def test_a_malformed_explicit_window_yields_nothing(self):
        os.environ["URS_BACKFILL_FROM"] = "08/09/2026"
        os.environ["URS_BACKFILL_TO"] = "21/09/2026"
        self.assertEqual(urs_backfill.resolve_window(), [])


class FlagTests(unittest.TestCase):
    def test_the_backfill_is_off_unless_explicitly_switched_on(self):
        os.environ.pop("URS_BACKFILL_ON_START", None)
        self.assertFalse(urs_backfill._flag_is_set("URS_BACKFILL_ON_START"))
        for value in ("1", "true", "TRUE", "yes", "on"):
            os.environ["URS_BACKFILL_ON_START"] = value
            self.assertTrue(urs_backfill._flag_is_set("URS_BACKFILL_ON_START"), value)
        for value in ("0", "false", "no", ""):
            os.environ["URS_BACKFILL_ON_START"] = value
            self.assertFalse(urs_backfill._flag_is_set("URS_BACKFILL_ON_START"), value)
        os.environ.pop("URS_BACKFILL_ON_START", None)


class PendingDaysTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_unattempted_days_are_returned(self):
        """
        A restart must not refetch. Every date already recorded in
        urs_backfill_state drops out, so a second run has nothing to do.
        """
        os.environ["URS_BACKFILL_DAYS"] = "3"
        self.addCleanup(os.environ.pop, "URS_BACKFILL_DAYS", None)

        window = urs_backfill.resolve_window()
        done = {window[0].isoformat(), window[1].isoformat()}

        with patch.object(urs_backfill, "_already_done", new=AsyncMock(
            side_effect=lambda day: day.isoformat() in done
        )):
            self.assertEqual(await urs_backfill.pending_days(), [window[2]])

            done.add(window[2].isoformat())
            self.assertEqual(await urs_backfill.pending_days(), [])


class FeedTotalTests(unittest.TestCase):
    """The independent cross-check on the parser."""

    @staticmethod
    def _payload(rows):
        return {"ResponseBody": {"GroupWiseDataList": [
            {"NetScheduleSummary": {"NetSchdDataList": rows}}
        ]}}

    def test_the_urs_row_is_summed_across_its_blocks(self):
        payload = self._payload([
            {"EnergyScheduleTypeName": "ISGS", "NetSchdAmount": [99.0] * 96},
            {"EnergyScheduleTypeName": "URS", "NetSchdAmount": [1.5] * 96},
        ])
        self.assertAlmostEqual(urs_backfill.feed_urs_total(payload), 144.0)

    def test_a_quiet_day_totals_zero_rather_than_going_missing(self):
        payload = self._payload([{"EnergyScheduleTypeName": "URS", "NetSchdAmount": [0.0] * 96}])
        self.assertEqual(urs_backfill.feed_urs_total(payload), 0.0)

    def test_an_unreadable_payload_reports_no_total_rather_than_zero(self):
        # None means "could not check", which must not be confused with a real
        # zero - one is a quiet day, the other is a broken cross-check.
        self.assertIsNone(urs_backfill.feed_urs_total({"ResponseBody": {}}))


if __name__ == "__main__":
    unittest.main()
