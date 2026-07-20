from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from qingpu_insight.model_features import (
    FEATURE_COLUMNS,
    ValuationInput,
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
    assert parse_floor("地下二層") == -2
    assert parse_floor("全") is None


def test_build_model_frame_isolates_type_and_adjusts_parking(fixture_frame):
    resale = build_model_frame(fixture_frame, "resale")
    assert set(resale["transaction_type"]) == {"resale"}
    assert resale.loc[resale.record_id.eq("R1"), "target_unit_price_twd"].item() == pytest.approx(
        800_000
    )


def test_input_frame_matches_training_feature_columns(valid_resale_input):
    online = input_frame(valid_resale_input, pd.Timestamp("2026-06-12"))
    assert list(online.columns) == list(FEATURE_COLUMNS)


def test_presale_input_rejects_age_and_floor_above_total(valid_presale_input):
    with pytest.raises(ValueError, match="building_age_years must be between 0 and 100"):
        replace(valid_presale_input, building_age_years=-1.0)
    with pytest.raises(ValueError, match="floor must not exceed total_floors"):
        replace(valid_presale_input, floor=21, total_floors=20)
