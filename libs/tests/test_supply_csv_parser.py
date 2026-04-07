import pytest
from libs.parsers.supply_csv_parser import SupplyCSVParser

def test_supply_csv_parser_basic():
    parser = SupplyCSVParser()
    content = """date,time_block,type,plant1,plant2
2023-10-01,1,DC,100,200
2023-10-01,2,DC,110,210
"""
    result = parser.parse(content)
    
    assert len(result) == 4
    
    # Check first row, first plant
    assert result[0] == {
        "scheduled_at": "2023-10-01",
        "time_block": "1",
        "dc_sg": "DC",
        "plant_id": "plant1",
        "volume": "100"
    }
    
    # Check first row, second plant
    assert result[1] == {
        "scheduled_at": "2023-10-01",
        "time_block": "1",
        "dc_sg": "DC",
        "plant_id": "plant2",
        "volume": "200"
    }

def test_supply_csv_parser_alt_headers():
    parser = SupplyCSVParser()
    content = """scheduled_at,timeBlock,dc_sg,P1,P2
2023-10-01,1,SG,50,60
"""
    result = parser.parse(content)
    
    assert len(result) == 2
    assert result[0]["plant_id"] == "P1"
    assert result[0]["volume"] == "50"
    assert result[0]["dc_sg"] == "SG"
    assert result[1]["plant_id"] == "P2"
    assert result[1]["volume"] == "60"

def test_supply_csv_parser_missing_values():
    parser = SupplyCSVParser()
    content = """date,time_block,type,plant1,plant2
2023-10-01,1,DC,100,
"""
    result = parser.parse(content)
    
    assert len(result) == 2
    assert result[0]["volume"] == "100"
    assert result[1]["volume"] == ""

def test_validate_content():
    parser = SupplyCSVParser()
    valid_content = "date,time_block,type,p1\n2023-01-01,1,DC,100"
    invalid_content = "some,other,data\n1,2,3"
    
    assert parser.validate_content(valid_content) is True
    assert parser.validate_content(invalid_content) is False
