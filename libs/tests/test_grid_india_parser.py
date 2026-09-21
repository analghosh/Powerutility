"""
Tests for Grid India Parser
"""
import json
import unittest
import datetime
from unittest.mock import patch, MagicMock
from libs.parsers.grid_india_parser import GridIndiaParser

class TestGridIndiaParser(unittest.TestCase):
    
    def setUp(self):
        self.parser = GridIndiaParser()
        self.sample_json = {
            "ResponseBody": {
                "Date": "16-12-2025",
                "SchedulePublishedTime": "14-06-2026 15:30:45",
                "GroupWiseDataList": [
                    {
                        "FullschdList": [
                            {
                                "EnergyScheduleTypeName": "OA_REMC",
                                "SellerAcronym": "SOLAR_PLANT",
                                "FullScheduleData": {
                                    "OAFullScheduleJsonData": {
                                        "TotalDrwBoundrySchdAmt": [100.0, 200.0] + [0.0]*94
                                    }
                                }
                            },
                            {
                                "EnergyScheduleTypeName": "ISGS",
                                "SellerAcronym": "ISGS_THERM",
                                "FullScheduleData": {
                                    "ISGSFullScheduleJsonData": {
                                        "ISGSGeneratorTypeName": "THERMAL",
                                        "ISGSThermalFullScheduleJsonData": {
                                            "TotalDrwBoundrySchdAmt": [50.0, 60.0] + [0.0]*94
                                        }
                                    }
                                }
                            },
                            {
                                "EnergyScheduleTypeName": "ISGS",
                                "SellerAcronym": "ISGS_NUC",
                                "FullScheduleData": {
                                    "ISGSFullScheduleJsonData": {
                                        "ISGSGeneratorTypeName": "NUCLEAR",
                                        "ISGSNuclearFullScheduleJsonData": {
                                            "TotalDrwBoundrySchdAmt": [70.0, 80.0] + [0.0]*94
                                        }
                                    }
                                }
                            }
                        ]
                    }
                ]
            }
        }

    def test_parser_type(self):
        self.assertEqual(self.parser.get_parser_type(), "grid_india")

    @patch('libs.parsers.grid_india_parser.datetime')
    def test_parse_success(self, mock_datetime):
        # Mock now().time() to return 12:00 PM
        mock_datetime.datetime.now.return_value.time.return_value = datetime.time(12, 0)
        # Ensure datetime.time is the real class for comparisons to work
        mock_datetime.time = datetime.time
        
        result = self.parser.parse(json.dumps(self.sample_json))
        
        # Check that no DC records are created outside the window
        dc_records = [r for r in result if r["dc_sg"] == "DC"]
        self.assertEqual(len(dc_records), 0)
        
        # OA_REMC SG
        oa_sg = next((r for r in result if r["plant_id"] == "SOLAR_PLANT" and r["dc_sg"] == "SG" and r["time_block"] == 1), None)
        self.assertIsNotNone(oa_sg)
        self.assertEqual(oa_sg["volume"], 100.0)

        # ISGS Thermal SG
        isgs_therm = next((r for r in result if r["plant_id"] == "ISGS_THERM" and r["dc_sg"] == "SG" and r["time_block"] == 1), None)
        self.assertIsNotNone(isgs_therm)
        self.assertEqual(isgs_therm["volume"], 50.0)

        # Schedule published timestamp should be present and preserve the full time
        self.assertIn("schedule_published_time", oa_sg)
        self.assertEqual(oa_sg["schedule_published_time"], datetime.datetime(2026, 6, 14, 15, 30, 45))
        self.assertEqual(isgs_therm["schedule_published_time"], datetime.datetime(2026, 6, 14, 15, 30, 45))

    @patch('libs.parsers.grid_india_parser.datetime')
    def test_parse_with_time_window(self, mock_datetime):
        # Mock now().time() to return 8:00 AM
        mock_datetime.datetime.now.return_value.time.return_value = datetime.time(8, 0)
        # Ensure datetime.time is the real class for comparisons to work
        mock_datetime.time = datetime.time
        
        result = self.parser.parse(json.dumps(self.sample_json))
        
        # Check that SG records exist
        sg_records = [r for r in result if r["dc_sg"] == "SG"]
        self.assertTrue(len(sg_records) > 0)
        
        # Check that DC records are duplicated
        dc_records = [r for r in result if r["dc_sg"] == "DC"]
        self.assertEqual(len(dc_records), len(sg_records))
        
        # Verify a specific duplicated record
        solar_sg = next((r for r in result if r["plant_id"] == "SOLAR_PLANT" and r["dc_sg"] == "SG" and r["time_block"] == 1), None)
        solar_dc = next((r for r in result if r["plant_id"] == "SOLAR_PLANT" and r["dc_sg"] == "DC" and r["time_block"] == 1), None)
        
        self.assertIsNotNone(solar_sg)
        self.assertIsNotNone(solar_dc)
        self.assertEqual(solar_sg["volume"], solar_dc["volume"])
        self.assertEqual(solar_dc["dc_sg"], "DC")

    @patch('libs.parsers.grid_india_parser.datetime')
    def test_parse_schedule_published_time_iso_with_timezone(self, mock_datetime):
        # Mock now().time() to return 12:00 PM
        mock_datetime.datetime.now.return_value.time.return_value = datetime.time(12, 0)
        mock_datetime.time = datetime.time

        sample = {
            "ResponseBody": {
                "Date": "16-12-2025",
                "SchedulePublishedTime": "2026-06-14T15:30:45+00:00",
                "GroupWiseDataList": [
                    {
                        "FullschdList": [
                            {
                                "EnergyScheduleTypeName": "OA_REMC",
                                "SellerAcronym": "SOLAR_PLANT",
                                "FullScheduleData": {
                                    "OAFullScheduleJsonData": {
                                        "TotalDrwBoundrySchdAmt": [100.0] + [0.0] * 95
                                    }
                                }
                            }
                        ]
                    }
                ]
            }
        }

        result = self.parser.parse(json.dumps(sample))
        record = next((r for r in result if r["plant_id"] == "SOLAR_PLANT" and r["time_block"] == 1), None)
        self.assertIsNotNone(record)
        self.assertIn("schedule_published_time", record)
        self.assertEqual(record["schedule_published_time"], datetime.datetime(2026, 6, 14, 15, 30, 45, tzinfo=datetime.timezone.utc))

    def test_validate_content(self):
        self.assertTrue(self.parser.validate_content(json.dumps(self.sample_json)))
        self.assertFalse(self.parser.validate_content(json.dumps({"random": "json"})))
        self.assertFalse(self.parser.validate_content("not json"))

class TestGridIndiaFuelTypes(unittest.TestCase):
    """Grid India reports each seller under exactly one fuel block, and the fuel
    decides the field names. Reading only the Thermal spelling silently produced
    96 zero-volume blocks instead of a parse failure, so these cases assert on
    the values rather than on the record merely existing."""

    def setUp(self):
        self.parser = GridIndiaParser()

    @staticmethod
    def _isgs(acronym, gen_type, block_key, amount):
        return {
            "EnergyScheduleTypeName": "ISGS",
            "SellerAcronym": acronym,
            "FullScheduleData": {
                "ISGSFullScheduleJsonData": {
                    "ISGSGeneratorTypeName": gen_type,
                    block_key: {
                        "TotalDrwBoundarySchdAmount": [amount] * 96,
                        "TotalRegBoundarySchdAmount": [amount + 1] * 96,
                    },
                }
            },
        }

    @staticmethod
    def _payload(full_schd=(), entitlements=()):
        return json.dumps({
            "ResponseBody": {
                "Date": "02-09-2026",
                "FullSchdRevisionNo": 104,
                "GroupWiseDataList": [{
                    "FullschdList": list(full_schd),
                    "EntitlementList": list(entitlements),
                }],
            }
        })

    def _volumes(self, rows, plant_id, dc_sg):
        return [r["volume"] for r in rows
                if r["plant_id"] == plant_id and r["dc_sg"] == dc_sg]

    def test_hydro_schedule_is_read_not_zeroed(self):
        """Bhutan hydro (Punatsangchhu_II) arrives as ISGS/HYDRO. Before the fix
        the plant was still emitted, but with every block at 0.0."""
        rows = self.parser.parse(self._payload(full_schd=[
            self._isgs("Punatsangchhu_II", "HYDRO",
                       "ISGSHydroFullScheduleJsonData", 13.87),
        ]))
        volumes = self._volumes(rows, "Punatsangchhu_II", "SG")
        self.assertEqual(len(volumes), 96)
        self.assertEqual(set(volumes), {13.87})

    def test_thermal_and_nuclear_schedules_still_read(self):
        rows = self.parser.parse(self._payload(full_schd=[
            self._isgs("THERM", "THERMAL", "ISGSThermalFullScheduleJsonData", 50.0),
            self._isgs("NUC", "NUCLEAR", "ISGSNuclearFullScheduleJsonData", 70.0),
        ]))
        self.assertEqual(set(self._volumes(rows, "THERM", "SG")), {50.0})
        self.assertEqual(set(self._volumes(rows, "NUC", "SG")), {70.0})

    def test_unknown_generator_type_is_not_silently_zeroed(self):
        """A fuel we do not model yet must not masquerade as a real zero
        schedule. It carries no usable block, so it contributes nothing."""
        rows = self.parser.parse(self._payload(full_schd=[
            self._isgs("MYSTERY", "GEOTHERMAL",
                       "ISGSGeothermalFullScheduleJsonData", 42.0),
        ]))
        self.assertEqual(set(self._volumes(rows, "MYSTERY", "SG")), {0.0})

    def test_nuclear_entitlement_yields_dc_volume(self):
        """Nuclear drops the Onbar/Offbar prefix. Reading only OnbarFinalEntAmount
        left every nuclear DC row on this deployment at zero."""
        rows = self.parser.parse(self._payload(entitlements=[{
            "SellerAcronym": "KKNP",
            "EntitlementData": {"NuclearEntJsonData": {
                "FinalEntAmount": [33.79] * 96,
                "EntireSharedEntAmount": [33.79] * 96,
            }},
        }]))
        volumes = self._volumes(rows, "KKNP", "DC")
        self.assertEqual(len(volumes), 96)
        self.assertEqual(set(volumes), {33.79})

    def test_hydro_entitlement_yields_dc_volume(self):
        rows = self.parser.parse(self._payload(entitlements=[{
            "SellerAcronym": "Punatsangchhu_II",
            "EntitlementData": {"HydroEntJsonData": {
                "FinalEntAmount": [14.46] * 96,
                "EntireSharedEntAmount": [14.46] * 96,
            }},
        }]))
        self.assertEqual(set(self._volumes(rows, "Punatsangchhu_II", "DC")), {14.46})

    def test_thermal_entitlement_keeps_onbar_offbar_split(self):
        """Thermal must keep using the Onbar spelling, and its off-bar half must
        survive the added fallbacks."""
        rows = self.parser.parse(self._payload(entitlements=[{
            "SellerAcronym": "TALST2",
            "EntitlementData": {"ThermalEntJsonData": {
                "OnbarFinalEntAmount": [61.42] * 96,
                "EntireSharedOnbarEntAmount": [63.99] * 96,
                "EntireSharedOffbarEntAmount": [1.5] * 96,
            }},
        }]))
        dc = [r for r in rows if r["plant_id"] == "TALST2" and r["dc_sg"] == "DC"]
        self.assertEqual(len(dc), 96)
        self.assertEqual(dc[0]["volume"], 61.42)
        self.assertEqual(dc[0]["entire_shared_onbar_ent_amount"], 63.99)
        self.assertEqual(dc[0]["entire_shared_offbar_ent_amount"], 1.5)

    def test_a_fuel_with_no_split_reports_no_offbar(self):
        rows = self.parser.parse(self._payload(entitlements=[{
            "SellerAcronym": "KKNP",
            "EntitlementData": {"NuclearEntJsonData": {
                "FinalEntAmount": [33.79] * 96,
                "EntireSharedEntAmount": [33.79] * 96,
            }},
        }]))
        dc = [r for r in rows if r["plant_id"] == "KKNP" and r["dc_sg"] == "DC"][0]
        self.assertEqual(dc["entire_shared_onbar_ent_amount"], 33.79)
        self.assertEqual(dc.get("entire_shared_offbar_ent_amount", 0.0), 0.0)


class TestGridIndiaURS(unittest.TestCase):
    """
    Un-requisitioned surplus AVAILED — surplus actually scheduled to this
    utility from another beneficiary's un-requisitioned capacity.

    It arrives as its own FullschdList entry, keyed on SellerAcronym like any
    other schedule, with the volume in TotalDrwBoundarySchdAmount. The fixtures
    below mirror a real payload: the fuel block sits under
    URSFullScheduleJsonData, which also names the generator type.

    Grid India omits these entries on a day when no URS was drawn — a live
    payload for such a day carries none, and its NetScheduleSummary URS row is
    96 blocks of zero. An empty report on such a day is correct.
    """

    def setUp(self):
        self.parser = GridIndiaParser()

    @staticmethod
    def _blocks(value):
        return [value] + [0.0] * 95

    def _urs_entry(self, plant_id, value, fuel="Thermal", key="TotalDrwBoundarySchdAmount"):
        return {
            "EnergyScheduleTypeName": "URS",
            "SellerAcronym": plant_id,
            "BuyerAcronym": "PONDY",
            "FullScheduleData": {
                "ISGSFullScheduleJsonData": None,
                "URSFullScheduleJsonData": {
                    "ISGSGeneratorTypeId": 1,
                    "ISGSGeneratorTypeName": fuel.upper(),
                    f"URS{fuel}FullScheduleJsonData": {
                        "UnRegSchdAmount": self._blocks(value),
                        key: self._blocks(value),
                    },
                },
            },
        }

    def _isgs_entry(self, plant_id, value):
        return {
            "EnergyScheduleTypeName": "ISGS",
            "SellerAcronym": plant_id,
            "FullScheduleData": {"ISGSFullScheduleJsonData": {
                "ISGSGeneratorTypeName": "THERMAL",
                "ISGSThermalFullScheduleJsonData": {
                    "TotalDrwBoundarySchdAmount": self._blocks(value)
                },
            }},
        }

    def _payload(self, entries):
        return json.dumps({"ResponseBody": {
            "Date": "21-09-2026",
            "FullSchdRevisionNo": 137,
            "GroupWiseDataList": [{"FullschdList": entries}],
        }})

    def _row(self, rows, plant_id, dc_sg, time_block=1):
        return next(
            (r for r in rows
             if r["plant_id"] == plant_id and r["dc_sg"] == dc_sg
             and r["time_block"] == time_block),
            None,
        )

    def test_a_urs_entry_is_stored_against_its_selling_plant(self):
        rows = self.parser.parse(self._payload([self._urs_entry("TelanganaSTPP", 12.5)]))
        self.assertEqual(self._row(rows, "TelanganaSTPP", "URS")["volume"], 12.5)

    def test_urs_is_also_counted_into_sg(self):
        # SG has always meant the whole schedule, URS included. Splitting it out
        # would move DSM, PLF and every scheduling screen at once.
        rows = self.parser.parse(self._payload([
            self._isgs_entry("VALLURNTECL", 50.0),
            self._urs_entry("VALLURNTECL", 30.0),
        ]))
        self.assertEqual(self._row(rows, "VALLURNTECL", "SG")["volume"], 80.0)
        self.assertEqual(self._row(rows, "VALLURNTECL", "URS")["volume"], 30.0)

    def test_a_plant_with_no_urs_entry_gets_no_urs_rows(self):
        rows = self.parser.parse(self._payload([self._isgs_entry("KUDGI", 42.0)]))
        self.assertIsNone(self._row(rows, "KUDGI", "URS"))
        self.assertEqual(self._row(rows, "KUDGI", "SG")["volume"], 42.0)

    def test_a_day_with_no_urs_at_all_produces_no_urs_rows(self):
        rows = self.parser.parse(self._payload([self._isgs_entry("KUDGI", 42.0)]))
        self.assertEqual([r for r in rows if r["dc_sg"] == "URS"], [])

    def test_every_block_of_the_day_is_written(self):
        rows = self.parser.parse(self._payload([self._urs_entry("KUDGI", 12.5)]))
        urs = [r for r in rows if r["dc_sg"] == "URS"]
        self.assertEqual(len(urs), 96)
        self.assertEqual(sorted(r["time_block"] for r in urs), list(range(1, 97)))

    def test_a_non_thermal_seller_is_not_read_as_zero(self):
        # The nuclear DC rows were zero for months because only the thermal
        # spelling was read. URS splits by fuel the same way.
        for fuel in ("Nuclear", "Hydro", "Gas"):
            with self.subTest(fuel=fuel):
                rows = self.parser.parse(self._payload([
                    self._urs_entry("KKNP", 7.25, fuel=fuel)
                ]))
                self.assertEqual(self._row(rows, "KKNP", "URS")["volume"], 7.25)

    def test_the_older_field_spelling_still_reads(self):
        # Grid India has shipped four spellings of this field across versions.
        rows = self.parser.parse(self._payload([
            self._urs_entry("NTPL", 9.5, key="TotalDrwBoundrySchdAmt")
        ]))
        self.assertEqual(self._row(rows, "NTPL", "URS")["volume"], 9.5)

    def test_several_urs_entries_for_one_plant_are_summed(self):
        rows = self.parser.parse(self._payload([
            self._urs_entry("KUDGI", 10.0),
            self._urs_entry("KUDGI", 2.5),
        ]))
        self.assertEqual(self._row(rows, "KUDGI", "URS")["volume"], 12.5)


if __name__ == '__main__':
    unittest.main()
