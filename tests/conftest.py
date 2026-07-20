from pathlib import Path

import pandas as pd
import pytest

from qingpu_insight.model_features import ValuationInput, build_model_frame


@pytest.fixture
def market_frame() -> pd.DataFrame:
    path = Path(__file__).parent / "fixtures" / "market_transactions.csv"
    df = pd.read_csv(path)
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df["completion_date"] = pd.to_datetime(df["completion_date"])
    df["coordinate_eligible"] = df["coordinate_eligible"].astype(bool)
    df["analysis_eligible"] = df["analysis_eligible"].astype(bool)
    return df


@pytest.fixture
def model_frame() -> pd.DataFrame:
    path = Path(__file__).parent / "fixtures" / "model_transactions.csv"
    df = pd.read_csv(path)
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df["analysis_eligible"] = df["analysis_eligible"].astype(bool)
    return build_model_frame(df, "resale")


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
        asking_total_price_twd=18000000,
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
