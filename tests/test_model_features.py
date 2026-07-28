from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qingpu_insight.model_features import (
    FEATURE_COLUMNS,
    PARKING_FEATURE_COLUMNS,
    ValuationInput,
    add_derived_features,
    build_model_frame,
    input_frame,
    parse_floor,
)


@pytest.fixture
def fixture_frame() -> pd.DataFrame:
    path = Path(__file__).parent / "fixtures" / "model_transactions.csv"
    df = pd.read_csv(path)
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df["analysis_eligible"] = df["analysis_eligible"].astype(bool)
    return df


@pytest.fixture
def valid_resale_input() -> ValuationInput:
    return ValuationInput(
        transaction_type="resale",
        station_code="A17",
        station_distance_m=500,
        building_area_ping=30,
        building_type="住宅大樓",
        bedrooms=3,
        living_rooms=2,
        bathrooms=2,
        building_age_years=6.0,
        floor=12,
        total_floors=15,
        parking_type="坡道平面",
        parking_area_ping=10,
    )


@pytest.fixture
def valid_presale_input() -> ValuationInput:
    return ValuationInput(
        transaction_type="presale",
        station_code="A18",
        station_distance_m=700,
        building_area_ping=35,
        building_type="住宅大樓",
        bedrooms=3,
        living_rooms=2,
        bathrooms=2,
        building_age_years=None,
        floor=12,
        total_floors=20,
        parking_type="坡道平面",
        parking_area_ping=10,
    )


def test_parse_floor_handles_chinese_and_rejects_impossible_values():
    assert parse_floor("十層") == 10
    assert parse_floor("二十一層") == 21
    assert parse_floor("15") == 15
    assert parse_floor("地下二層") == -2
    assert parse_floor("全") is None


def test_build_model_frame_isolates_type_and_adjusts_parking(fixture_frame):
    resale = build_model_frame(fixture_frame, "resale")
    assert set(resale["transaction_type"]) == {"resale"}
    assert resale.loc[resale.record_id.eq("R1"), "target_unit_price_twd"].item() == pytest.approx(
        800_000
    )


def test_build_model_frame_parses_chinese_total_floors(fixture_frame):
    fixture_frame["total_floors"] = fixture_frame["total_floors"].astype(object)
    fixture_frame.loc[fixture_frame["transaction_type"].eq("resale"), "total_floors"] = "二十一層"
    resale = build_model_frame(fixture_frame, "resale")
    assert resale["total_floors"].eq(21).all()
    assert resale["floor_ratio"].notna().all()


def test_input_frame_matches_training_feature_columns(valid_resale_input):
    online = input_frame(valid_resale_input, pd.Timestamp("2026-06-12"))
    assert list(online.columns) == list(FEATURE_COLUMNS)


def test_presale_input_rejects_age_and_floor_above_total(valid_presale_input):
    with pytest.raises(ValueError, match="building_age_years must be omitted"):
        replace(valid_presale_input, building_age_years=1.0)
    with pytest.raises(ValueError, match="floor must not exceed total_floors"):
        replace(valid_presale_input, floor=21, total_floors=20)


@pytest.mark.parametrize("station_code", ["Z99", "", "A16"])
def test_valuation_input_rejects_unknown_station(valid_resale_input, station_code):
    with pytest.raises(ValueError, match="station_code"):
        replace(valid_resale_input, station_code=station_code)


def test_valuation_input_rejects_zero_total_floors(valid_resale_input):
    with pytest.raises(ValueError, match="total_floors"):
        replace(valid_resale_input, floor=0, total_floors=0)


def test_resale_input_requires_building_age(valid_resale_input):
    with pytest.raises(ValueError, match="building_age_years is required"):
        replace(valid_resale_input, building_age_years=None)


def test_derived_features_are_identical_for_training_and_inference():
    value = ValuationInput(
        transaction_type="resale",
        station_code="A18",
        station_distance_m=500.0,
        building_area_ping=20.0,
        building_type="住宅大樓",
        bedrooms=3,
        living_rooms=2,
        bathrooms=2,
        building_age_years=5.0,
        floor=15,
        total_floors=30,
        parking_type="",
        parking_area_ping=0.0,
    )
    raw = pd.DataFrame(
        [
            {
                "analysis_eligible": True,
                "transaction_type": "resale",
                "transaction_date": pd.Timestamp("2026-06-12"),
                "station_code": "A18",
                "station_distance_m": 500.0,
                "building_area_ping": 20.0,
                "building_type": "住宅大樓",
                "bedrooms": 3,
                "living_rooms": 2,
                "bathrooms": 2,
                "building_age_years": 5.0,
                "floor": "15層",
                "total_floors": "30層",
                "parking_type": "",
                "parking_area_sqm": 0.0,
                "parking_price_twd": 0.0,
                "total_price_twd": 12_000_000,
                "unit_price_per_ping_twd": 600_000.0,
            }
        ]
    )
    trained = build_model_frame(raw, "resale").iloc[0]
    inferred = input_frame(value, pd.Timestamp("2026-06-12")).iloc[0]

    assert trained["transaction_month_index"] == inferred["transaction_month_index"]
    assert trained["station_building_type"] == inferred["station_building_type"]
    assert trained["building_age_band"] == inferred["building_age_band"] == "5_10"
    assert trained["area_band"] == inferred["area_band"] == "small"
    assert trained["floor_band"] == inferred["floor_band"] == "middle"


def test_house_feature_contract_excludes_parking():
    assert "parking_type" not in FEATURE_COLUMNS
    assert "parking_area_ping" not in FEATURE_COLUMNS
    assert "parking_type" in PARKING_FEATURE_COLUMNS
    assert "parking_area_ping" in PARKING_FEATURE_COLUMNS


def test_no_parking_normalizes_stale_area(valid_resale_input):
    value = replace(valid_resale_input, parking_type="", parking_area_ping=8)
    assert value.parking_area_ping == 0


def test_selected_parking_requires_positive_area(valid_resale_input):
    with pytest.raises(ValueError, match="parking_area_ping must be greater than 0"):
        replace(valid_resale_input, parking_type="坡道平面", parking_area_ping=0)


def test_derived_feature_boundaries_and_missing_values():
    frame = pd.DataFrame(
        {
            "transaction_date": pd.to_datetime(["2026-01-01"] * 4),
            "station_code": ["A18"] * 4,
            "building_type": ["住宅大樓"] * 4,
            "building_age_years": [0.0, 5.0, 20.0, np.nan],
            "building_area_ping": [20.0, 20.01, 50.0, 50.01],
            "floor_ratio": [0.33, 0.34, 0.67, np.nan],
        }
    )
    result = add_derived_features(frame)
    assert result["building_age_band"].tolist() == ["0_5", "5_10", "20_plus", "missing"]
    assert result["area_band"].tolist() == ["small", "standard", "standard", "large"]
    assert result["floor_band"].tolist() == ["low", "middle", "middle", "unknown"]
