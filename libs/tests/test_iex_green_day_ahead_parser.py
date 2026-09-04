"""
Tests for IEX Green Day Ahead (GDAM) Parser

Fixtures under fixtures/ are captured from real responses stored in
epms.exchange_integration_apilog, except the legacy 4-bucket table which is
hand-built from the column layout the parser originally targeted.
"""
import os
import unittest

from libs.parsers.iex_green_day_ahead_parser import IEXGreenDayAheadParser

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


class TestIEXGreenDayAheadParser(unittest.TestCase):

    def setUp(self):
        self.parser = IEXGreenDayAheadParser()

    def test_extracts_every_block_from_embedded_payload(self):
        rows = self.parser.parse(load_fixture("iex_gdam_flight_payload.html"))

        self.assertEqual(96, len(rows))
        self.assertEqual("00:00-00:15", rows[0]["time_block"])
        self.assertEqual("23:45-24:00", rows[-1]["time_block"])

    def test_maps_five_generation_buckets_from_embedded_payload(self):
        rows = self.parser.parse(load_fixture("iex_gdam_flight_payload.html"))

        self.assertEqual("2026-08-08", rows[0]["date"])
        self.assertEqual(3444.4, rows[0]["buy_bid"])
        self.assertEqual(
            {"total": 1718.7, "hydro": 0.0, "wind": 774.0, "other_re": 885.1, "dre": 59.6},
            rows[0]["sell_bid"],
        )
        self.assertEqual(
            {"total": 889.19, "hydro": 0.0, "wind": 244.6, "other_re": 591.59, "dre": 53.0},
            rows[0]["mcv"],
        )
        self.assertEqual(
            {"total": 889.19, "hydro": 0.0, "wind": 244.6, "other_re": 591.59, "dre": 53.0},
            rows[0]["final_scheduled_volume"],
        )
        self.assertEqual(4000.92, rows[0]["mcp"])

    def test_parses_five_bucket_table_when_payload_is_absent(self):
        rows = self.parser.parse(load_fixture("iex_gdam_table_5bucket.html"))

        self.assertEqual(10, len(rows))
        self.assertEqual(3444.4, rows[0]["buy_bid"])
        self.assertEqual(1718.7, rows[0]["sell_bid"]["total"])
        self.assertEqual(774.0, rows[0]["sell_bid"]["wind"])
        self.assertEqual(4000.92, rows[0]["mcp"])

    def test_table_rows_inherit_the_rowspanned_date(self):
        rows = self.parser.parse(load_fixture("iex_gdam_table_5bucket.html"))

        self.assertTrue(all(row["date"] == "2026-08-08" for row in rows))
        # Block 5 opens a new hour, so its row carries an extra leading cell.
        self.assertEqual("01:00-01:15", rows[4]["time_block"])
        self.assertEqual(3660.1, rows[4]["buy_bid"])
        self.assertEqual("02:15-02:30", rows[9]["time_block"])

    def test_parses_legacy_four_bucket_table(self):
        rows = self.parser.parse(load_fixture("iex_gdam_table_4bucket_legacy.html"))

        self.assertEqual(4, len(rows))
        self.assertEqual("2026-01-05", rows[0]["date"])
        self.assertEqual(3444.4, rows[0]["buy_bid"])
        self.assertEqual(
            {"total": 1718.7, "solar": 800.1, "non_solar": 844.6, "hydro": 74.0},
            rows[0]["sell_bid"],
        )
        self.assertEqual(4141.34, rows[3]["mcp"])
        self.assertEqual("00:30-00:45", rows[2]["time_block"])

    def test_returns_no_rows_when_results_are_not_published(self):
        self.assertEqual([], self.parser.parse(load_fixture("iex_gdam_no_data.html")))

    def test_returns_no_rows_for_unusable_content(self):
        self.assertEqual([], self.parser.parse(""))
        self.assertEqual([], self.parser.parse("<html><body>no market data</body></html>"))


if __name__ == "__main__":
    unittest.main()
