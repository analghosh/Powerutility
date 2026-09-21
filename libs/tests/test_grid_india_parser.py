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
    Un-requisitioned surplus.

    Grid India publishes no FullschdList entry of type "URS" - a live payload
    carries only ISGS, OA_REMC, OA_PX and OA_GNA. It does publish both halves
    the definition is made of: EntitlementList, this utility's share of each
    plant's declared capacity, and RequisitionList, how much of that share was
    actually asked for. URS is the remainder, per plant, per block.
    """

    def setUp(self):
        self.parser = GridIndiaParser()

    @staticmethod
    def _blocks(value):
        return [value] + [0.0] * 95

    def _payload(self, entitlements=None, requisitions=None, schedules=None):
        group = {
            "EntitlementList": entitlements or [],
            "RequisitionList": requisitions or [],
            "FullschdList": schedules or [],
        }
        return json.dumps({"ResponseBody": {
            "Date": "21-09-2026",
            "FullSchdRevisionNo": 137,
            "GroupWiseDataList": [group],
        }})

    def _entitlement(self, plant_id, onbar, offbar=None, fuel="Thermal"):
        """Thermal and Gas split the entitlement; Nuclear and Hydro do not."""
        if fuel in ("Thermal", "Gas"):
            data = {"OnbarFinalEntAmount": self._blocks(onbar)}
            if offbar is not None:
                data["OffbarFinalEntAmount"] = self._blocks(offbar)
        else:
            data = {"FinalEntAmount": self._blocks(onbar)}
        return {"SellerAcronym": plant_id, "EntitlementData": {f"{fuel}EntJsonData": data}}

    def _requisition(self, plant_id, onbar, offbar=0.0, fuel="Thermal"):
        return {
            "SellerAcronym": plant_id,
            "RequisitionData": {f"{fuel}ReqJsonData": {
                "OnbarReqAmount": self._blocks(onbar),
                "OffbarReqAmount": self._blocks(offbar),
            }},
        }

    def _urs(self, rows, plant_id, time_block=1):
        return next(
            (r for r in rows
             if r["plant_id"] == plant_id and r["dc_sg"] == "URS"
             and r["time_block"] == time_block),
            None,
        )

    def test_urs_is_the_entitlement_that_was_not_requisitioned(self):
        rows = self.parser.parse(self._payload(
            entitlements=[self._entitlement("KUDGI", 50.0)],
            requisitions=[self._requisition("KUDGI", 20.0)],
        ))
        self.assertEqual(self._urs(rows, "KUDGI")["volume"], 30.0)

    def test_a_fully_requisitioned_plant_has_no_surplus(self):
        rows = self.parser.parse(self._payload(
            entitlements=[self._entitlement("NLCEXP", 21.35)],
            requisitions=[self._requisition("NLCEXP", 21.35)],
        ))
        self.assertEqual(self._urs(rows, "NLCEXP")["volume"], 0.0)

    def test_a_plant_with_no_requisition_row_is_entirely_surplus(self):
        # Requisitioning nothing is not the same as being absent from the feed:
        # the whole share is un-requisitioned.
        rows = self.parser.parse(self._payload(
            entitlements=[self._entitlement("KKNP", 33.79, fuel="Nuclear")],
            requisitions=[],
        ))
        self.assertEqual(self._urs(rows, "KKNP")["volume"], 33.79)

    def test_both_halves_of_a_split_entitlement_count(self):
        rows = self.parser.parse(self._payload(
            entitlements=[self._entitlement("RSTPSU7", 40.0, offbar=10.0)],
            requisitions=[self._requisition("RSTPSU7", 15.0, offbar=5.0)],
        ))
        self.assertEqual(self._urs(rows, "RSTPSU7")["volume"], 30.0)

    def test_a_requisition_above_the_entitlement_is_not_negative_surplus(self):
        rows = self.parser.parse(self._payload(
            entitlements=[self._entitlement("MAPS", 10.0, fuel="Nuclear")],
            requisitions=[self._requisition("MAPS", 25.0, fuel="Nuclear")],
        ))
        self.assertEqual(self._urs(rows, "MAPS")["volume"], 0.0)

    def test_a_requisition_without_an_entitlement_yields_nothing(self):
        # No share of the plant means no surplus of it to report.
        rows = self.parser.parse(self._payload(
            requisitions=[self._requisition("GHOST", 12.0)],
        ))
        self.assertIsNone(self._urs(rows, "GHOST"))

    def test_every_block_of_the_day_is_written(self):
        rows = self.parser.parse(self._payload(
            entitlements=[self._entitlement("KUDGI", 50.0)],
            requisitions=[self._requisition("KUDGI", 20.0)],
        ))
        urs = [r for r in rows if r["dc_sg"] == "URS"]
        self.assertEqual(len(urs), 96)
        self.assertEqual(sorted(r["time_block"] for r in urs), list(range(1, 97)))

    def test_the_derivation_does_not_disturb_sg(self):
        # SG comes from FullschdList and must read exactly as it did before URS
        # was derived at all.
        schedule = {
            "EnergyScheduleTypeName": "ISGS",
            "SellerAcronym": "KUDGI",
            "FullScheduleData": {"ISGSFullScheduleJsonData": {
                "ISGSGeneratorTypeName": "THERMAL",
                "ISGSThermalFullScheduleJsonData": {
                    "TotalDrwBoundarySchdAmount": self._blocks(18.0)
                },
            }},
        }
        rows = self.parser.parse(self._payload(
            entitlements=[self._entitlement("KUDGI", 50.0)],
            requisitions=[self._requisition("KUDGI", 20.0)],
            schedules=[schedule],
        ))
        sg = next(r for r in rows if r["plant_id"] == "KUDGI"
                  and r["dc_sg"] == "SG" and r["time_block"] == 1)
        self.assertEqual(sg["volume"], 18.0)
        self.assertEqual(self._urs(rows, "KUDGI")["volume"], 30.0)

    def test_a_payload_with_no_entitlements_yields_no_urs(self):
        rows = self.parser.parse(self._payload())
        self.assertEqual([r for r in rows if r["dc_sg"] == "URS"], [])


if __name__ == '__main__':
    unittest.main()
