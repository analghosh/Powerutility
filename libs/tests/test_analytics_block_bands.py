"""Block grouping bands.

PTC's peak/off-peak definitions do not exist in EPMS, mplive, or the parameters
collection — the five named bands ship inactive and are activated by supplying
ranges, not by changing code. Only INDIVIDUAL and CUSTOM work at launch.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.processing.app.v2.analytics import block_bands as bb  # noqa: E402


def test_individual_and_custom_are_active_at_launch():
    assert bb.DEFAULT_BANDS["INDIVIDUAL"].active is True
    assert bb.DEFAULT_BANDS["CUSTOM"].active is True


@pytest.mark.parametrize("code", ["NIGHT_OFF_PEAK", "DAY_OFF_PEAK", "DAY_PEAK",
                                  "EVENING_PEAK", "PEAK"])
def test_named_bands_ship_inactive_with_no_ranges(code):
    band = bb.DEFAULT_BANDS[code]
    assert band.active is False
    assert band.ranges == ()


def test_individual_bucket_is_the_block_itself():
    assert bb.bucket_for(42, bb.DEFAULT_BANDS["INDIVIDUAL"]) == "42"


def test_bucket_for_returns_the_band_code_when_block_is_in_range():
    band = bb.Band(code="EVENING_PEAK", label="Evening Peak",
                   ranges=((69, 84),), active=True, effective_from=None)
    assert bb.bucket_for(70, band) == "EVENING_PEAK"


def test_bucket_for_returns_none_outside_the_band():
    band = bb.Band(code="EVENING_PEAK", label="Evening Peak",
                   ranges=((69, 84),), active=True, effective_from=None)
    assert bb.bucket_for(10, band) is None


def test_band_boundaries_are_inclusive():
    band = bb.Band(code="X", label="X", ranges=((69, 84),), active=True, effective_from=None)
    assert bb.bucket_for(69, band) == "X"
    assert bb.bucket_for(84, band) == "X"
    assert bb.bucket_for(85, band) is None


def test_individual_bucket_count_is_the_selected_span():
    assert bb.bucket_count(bb.DEFAULT_BANDS["INDIVIDUAL"], 1, 96, None) == 96
    assert bb.bucket_count(bb.DEFAULT_BANDS["INDIVIDUAL"], 10, 20, None) == 11


def test_named_band_collapses_to_one_bucket():
    band = bb.Band(code="PEAK", label="Peak", ranges=((69, 84),), active=True, effective_from=None)
    assert bb.bucket_count(band, 1, 96, None) == 1


def test_from_block_greater_than_to_block_is_rejected():
    with pytest.raises(ValueError):
        bb.validate_block_range(50, 10)


@pytest.mark.parametrize("bad", [0, 97, -1, 1000])
def test_blocks_outside_1_to_96_are_rejected(bad):
    with pytest.raises(ValueError):
        bb.validate_block_range(bad, 96)


def test_valid_full_day_range_is_accepted():
    bb.validate_block_range(1, 96)  # must not raise


# ------------------------------------------------------- parametrised bands


@pytest.mark.parametrize("text,expected", [
    ("1-24, 89-96", ((1, 24), (89, 96))),
    ("1-24;89-96", ((1, 24), (89, 96))),
    ("1–24 89-96", ((1, 24), (89, 96))),        # en dash, whitespace separator
    ("5", ((5, 5),)),
    ("", ()),
    ("   ", ()),
    (None, ()),
    ([[1, 4], [9, 12]], ((1, 4), (9, 12))),     # already-structured (a job row replay)
])
def test_parse_ranges_accepts_what_an_admin_would_type(text, expected):
    assert bb.parse_ranges(text) == expected


@pytest.mark.parametrize("text", ["0-5", "10-3", "1-97", "abc", "1-24-30", "1--5"])
def test_parse_ranges_names_the_bad_piece(text):
    with pytest.raises(ValueError) as exc:
        bb.parse_ranges(text)
    assert text.split(",")[0].strip() in str(exc.value) or "not valid" in str(exc.value)


def test_bands_from_parameters_activates_only_the_bands_with_ranges():
    bands = bb.bands_from_parameters({
        "analytics_band_peak": "65-88",
        "analytics_band_night_off_peak": "",
        "analytics_band_day_peak": "garbage",     # a typo must not take the screen down
    })
    assert bands["PEAK"].active and bands["PEAK"].ranges == ((65, 88),)
    assert not bands["NIGHT_OFF_PEAK"].active
    assert not bands["DAY_PEAK"].active
    assert bands["INDIVIDUAL"].active and bands["CUSTOM"].active
    assert bb.parameter_errors({"analytics_band_day_peak": "garbage"}) == {
        "DAY_PEAK": "Block range 'garbage' is not valid; expected e.g. 1-24, 89-96"}


def test_every_named_band_has_a_parameter_key_and_a_description():
    named = [c for c in bb.DEFAULT_BANDS if c not in (bb.BAND_INDIVIDUAL, bb.BAND_CUSTOM)]
    assert set(named) == set(bb.PARAMETER_KEYS)
    for code in named:
        assert bb.PARAMETER_KEYS[code].startswith("analytics_band_")
        assert bb.DEFAULT_BANDS[code].label in bb.PARAMETER_DESCRIPTIONS[code]
