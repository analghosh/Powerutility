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

    KEYS = ("URS_BACKFILL_FROM", "URS_BACKFILL_TO")

    def setUp(self):
        self._clear()
        self.addCleanup(self._clear)

    @classmethod
    def _clear(cls):
        for key in cls.KEYS:
            os.environ.pop(key, None)

    def test_the_default_window_is_pinned_not_relative(self):
        """
        A relative window would make a late or repeated deployment fetch a
        different fortnight than the one this exists to recover.
        """
        window = urs_backfill.resolve_window()
        self.assertEqual(window[0], urs_backfill.DEFAULT_WINDOW_FROM)
        self.assertEqual(window[-1], urs_backfill.DEFAULT_WINDOW_TO)
        self.assertTrue(all(day < date.today() for day in window))

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

    def test_an_oversized_window_is_refused_not_truncated(self):
        os.environ["URS_BACKFILL_FROM"] = "2020-01-01"
        os.environ["URS_BACKFILL_TO"] = str(date.today() - timedelta(days=1))
        self.assertEqual(urs_backfill.resolve_window(), [])

    def test_a_malformed_explicit_window_yields_nothing(self):
        os.environ["URS_BACKFILL_FROM"] = "08/09/2026"
        os.environ["URS_BACKFILL_TO"] = "21/09/2026"
        self.assertEqual(urs_backfill.resolve_window(), [])


class FlagTests(unittest.TestCase):
    """
    On by default, because the operators cannot set an environment variable on
    this host - a redeploy is their only lever. Running once is what makes that
    safe, not the flag.
    """

    def setUp(self):
        self.addCleanup(os.environ.pop, "URS_BACKFILL_ON_START", None)

    def test_it_is_on_with_nothing_configured(self):
        os.environ.pop("URS_BACKFILL_ON_START", None)
        self.assertTrue(urs_backfill.backfill_enabled())

    def test_it_can_be_switched_off(self):
        for value in ("0", "false", "FALSE", "no", "off"):
            os.environ["URS_BACKFILL_ON_START"] = value
            self.assertFalse(urs_backfill.backfill_enabled(), value)

    def test_anything_else_leaves_it_on(self):
        for value in ("1", "true", "yes", "on", ""):
            os.environ["URS_BACKFILL_ON_START"] = value
            self.assertTrue(urs_backfill.backfill_enabled(), value)


class PendingDaysTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_unattempted_days_are_returned(self):
        """
        A restart must not refetch. Every date already recorded in
        urs_backfill_state drops out, so a second run has nothing to do.
        """
        window = urs_backfill.resolve_window()
        done = {day.isoformat() for day in window[:-1]}

        with patch.object(urs_backfill, "_already_done", new=AsyncMock(
            side_effect=lambda day: day.isoformat() in done
        )):
            self.assertEqual(await urs_backfill.pending_days(), [window[-1]])

            done.add(window[-1].isoformat())
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
