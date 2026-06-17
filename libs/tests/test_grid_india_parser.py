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

if __name__ == '__main__':
    unittest.main()
