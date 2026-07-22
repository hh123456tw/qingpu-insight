"""Tests for life-circle assignment of normalized listings."""

import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from qingpu_insight.listing_location import assign_listing_life_circle

SNAPSHOT_AT = datetime(2026, 7, 21, 12, 0, 0)

# Station TWD97 -> WGS84 mappings (pre-computed via pyproj):
#   A17: TWD97 (271500, 2768000) -> WGS84 (25.019912, 121.213032)
#   A18: TWD97 (273000, 2770000) -> WGS84 (25.037947, 121.227928)
#   A19: TWD97 (272000, 2769000) -> WGS84 (25.028933, 121.218002)
#
# Key distances used below:
#   - (25.032, 121.225) -> A18 = 724m, A17 = 1806m, A19 = 783m
#   - (25.037947, 121.2478) -> A18 = 2000m, A17 = 4035m, A19 = 3163m
#   - (25.037947, 121.25)   -> A18 = 2038m, A17 = 4068m, A19 = 3199m
#   - (25.0, 121.0)         -> all > 20000m
#   - (25.021, 121.214)     -> A17 = 155m, A18 = 2350m, A19 = 1008m


@pytest.fixture
def station_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "station_code": ["A17", "A18", "A19"],
        "station_name": ["領航站", "高鐵桃園站", "桃園體育園區站"],
        "twd97_x": [271500.0, 273000.0, 272000.0],
        "twd97_y": [2768000.0, 2770000.0, 2769000.0],
    })


def test_missing_coordinates_are_retained_but_not_eligible(station_frame):
    df = pd.DataFrame({"latitude": [np.nan], "longitude": [np.nan]})
    result = assign_listing_life_circle(df, station_frame, 2_000)
    assert not result.loc[0, "location_eligible"]
    assert pd.isna(result.loc[0, "station_code"])


def test_valid_nearest_station(station_frame):
    df = pd.DataFrame({"latitude": [25.032], "longitude": [121.225]})
    result = assign_listing_life_circle(df, station_frame, 2_000)
    assert result.loc[0, "location_eligible"]
    assert result.loc[0, "station_code"] == "A18"
    assert 600 < result.loc[0, "station_distance_m"] < 800


def test_overlap_chooses_nearest(station_frame):
    df = pd.DataFrame({"latitude": [25.021], "longitude": [121.214]})
    result = assign_listing_life_circle(df, station_frame, 2_000)
    assert result.loc[0, "location_eligible"]
    assert result.loc[0, "station_code"] == "A17"


def test_exactly_radius_boundary(station_frame):
    # 1999m due east from A18, well beyond A17/A19 radius
    df = pd.DataFrame({"latitude": [25.037947], "longitude": [121.24777]})
    result = assign_listing_life_circle(df, station_frame, 2_000)
    assert result.loc[0, "location_eligible"]
    assert result.loc[0, "station_code"] == "A18"
    assert abs(result.loc[0, "station_distance_m"] - 1999) < 3


def test_outside_radius(station_frame):
    # 2007m east from A18 -> outside 2000m radius
    df = pd.DataFrame({"latitude": [25.037947], "longitude": [121.24785]})
    result = assign_listing_life_circle(df, station_frame, 2_000)
    assert not result.loc[0, "location_eligible"]
    assert result.loc[0, "station_code"] == "A18"
    assert result.loc[0, "station_distance_m"] > 2_000
    assert result.loc[0, "location_reason"] == "outside_service_radius"


def test_far_point_outside_radius(station_frame):
    df = pd.DataFrame({"latitude": [25.0], "longitude": [121.0]})
    result = assign_listing_life_circle(df, station_frame, 2_000)
    assert not result.loc[0, "location_eligible"]
    assert result.loc[0, "station_code"] == "A17"
    assert result.loc[0, "station_distance_m"] > 20_000


def test_invalid_coordinates_are_not_eligible(station_frame):
    df = pd.DataFrame({"latitude": [0.0], "longitude": [0.0]})
    result = assign_listing_life_circle(df, station_frame, 2_000)
    assert not result.loc[0, "location_eligible"]
    assert pd.isna(result.loc[0, "station_code"])


def test_multiple_listings_mixed_eligibility(station_frame):
    df = pd.DataFrame({
        "latitude": [25.032, 25.0],
        "longitude": [121.225, 121.0],
    })
    result = assign_listing_life_circle(df, station_frame, 2_000)
    assert result.loc[0, "location_eligible"]
    assert result.loc[0, "station_code"] == "A18"
    assert not result.loc[1, "location_eligible"]
    assert result.loc[1, "station_code"] == "A17"


def test_output_columns_preserve_input(station_frame):
    df = pd.DataFrame({
        "latitude": [25.032],
        "longitude": [121.225],
        "source_listing_id": ["test-001"],
    })
    result = assign_listing_life_circle(df, station_frame, 2_000)
    assert result.loc[0, "source_listing_id"] == "test-001"
    assert result.loc[0, "latitude"] == 25.032


def test_half_the_listings_eligible(station_frame):
    df = pd.DataFrame({
        "latitude": [25.032, 25.037947],
        "longitude": [121.225, 121.24785],
    })
    result = assign_listing_life_circle(df, station_frame, 2_000)
    assert result["location_eligible"].sum() == 1


@pytest.mark.parametrize(
    ("method", "expected_reason"),
    [
        ("source_coordinates", "eligible_source_coordinates"),
        ("structured_address", "eligible_structured_address"),
        ("manual", "eligible_manual"),
        ("unknown", "unknown_location_method"),
    ],
)
def test_eligible_reason_depends_on_location_method(
    station_frame, method, expected_reason
):
    result = assign_listing_life_circle(
        pd.DataFrame(
            [{"latitude": 25.032, "longitude": 121.225, "location_method": method}]
        ),
        station_frame,
        2_000,
    )
    assert bool(result.loc[0, "location_eligible"]) is (method != "unknown")
    assert result.loc[0, "location_reason"] == expected_reason


def test_missing_or_invalid_coordinates_have_no_station_and_missing_reason(station_frame):
    result = assign_listing_life_circle(
        pd.DataFrame(
            [
                {"latitude": np.nan, "longitude": np.nan, "location_method": "unknown"},
                {"latitude": 0, "longitude": 0, "location_method": "source_coordinates"},
            ]
        ),
        station_frame,
        2_000,
    )
    assert result["location_reason"].tolist() == [
        "missing_coordinates",
        "missing_coordinates",
    ]
    assert result["station_code"].isna().all()


def test_station_input_requires_at_least_one_valid_station(station_frame):
    for invalid in (
        station_frame.iloc[0:0],
        station_frame.assign(twd97_x=np.nan),
        station_frame.assign(station_code=""),
    ):
        with pytest.raises(ValueError, match="valid station"):
            assign_listing_life_circle(
                pd.DataFrame([{"latitude": 25.032, "longitude": 121.225}]),
                invalid,
                2_000,
            )


def test_station_input_ignores_invalid_rows_but_uses_valid_station(station_frame):
    stations = pd.concat(
        [
            station_frame.iloc[[1]],
            pd.DataFrame(
                [{"station_code": "", "twd97_x": np.nan, "twd97_y": np.nan}]
            ),
        ],
        ignore_index=True,
    )
    result = assign_listing_life_circle(
        pd.DataFrame([{"latitude": 25.032, "longitude": 121.225}]), stations, 2_000
    )
    assert result.loc[0, "station_code"] == "A18"


def test_station_transform_filters_invalid_wgs84_results_without_runtime_warning(
    station_frame,
):
    stations = pd.concat(
        [
            station_frame.iloc[[1]],
            pd.DataFrame(
                [{"station_code": "BAD", "twd97_x": 1e10, "twd97_y": 1e10}]
            ),
        ],
        ignore_index=True,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = assign_listing_life_circle(
            pd.DataFrame([{"latitude": 25.032, "longitude": 121.225}]),
            stations,
            2_000,
        )
    assert result.loc[0, "station_code"] == "A18"
    assert not [warning for warning in caught if issubclass(warning.category, RuntimeWarning)]


def test_all_invalid_transformed_stations_raise_clear_value_error(station_frame):
    invalid = station_frame.iloc[[0]].assign(twd97_x=1e10, twd97_y=1e10)
    with pytest.raises(ValueError, match="valid station"):
        assign_listing_life_circle(
            pd.DataFrame([{"latitude": 25.032, "longitude": 121.225}]),
            invalid,
            2_000,
        )


@pytest.mark.parametrize(
    "reason",
    [
        "geocoder_unavailable",
        "address_not_resolved",
        "invalid_geocoder_coordinates",
        "missing_structured_address",
        "detail_address_missing",
    ],
)
def test_unknown_missing_coordinate_keeps_specific_resolution_diagnostic(
    station_frame, reason
):
    result = assign_listing_life_circle(
        pd.DataFrame(
            [
                {
                    "latitude": np.nan,
                    "longitude": np.nan,
                    "location_method": "unknown",
                    "location_reason": reason,
                }
            ]
        ),
        station_frame,
        2_000,
    )
    assert result.loc[0, "location_reason"] == reason
    assert not result.loc[0, "location_eligible"]
