import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from qingpu_insight.model_analysis import build_resale_diagnostics
from qingpu_insight.model_training import split_by_time


@pytest.fixture
def large_model_frame():
    np.random.seed(42)
    stations = ["A17", "A18", "A19"]
    bt_types = ["住宅大樓", "華廈", "公寓"]
    rows = []

    # Ensure >=100 rows per split: train (dates < 2024-12-16),
    # calibration (2024-12-16 <= dates < 2025-06-16),
    # test (dates >= 2025-06-16)
    periods = [
        (350, "train", datetime(2021, 1, 1), datetime(2024, 12, 15)),
        (150, "calibration", datetime(2024, 12, 16), datetime(2025, 6, 15)),
        (150, "test", datetime(2025, 6, 16), datetime(2026, 6, 15)),
    ]

    for count, _, start, end in periods:
        span = (end - start).days
        for _ in range(count):
            d = start + timedelta(days=int(np.random.uniform(0, span)))
            rows.append(
                {
                    "station_code": np.random.choice(stations),
                    "station_distance_m": float(np.random.uniform(100, 1500)),
                    "building_area_ping": float(np.random.uniform(20, 80)),
                    "building_type": np.random.choice(bt_types),
                    "bedrooms": int(np.random.choice([2, 3, 4])),
                    "living_rooms": int(np.random.choice([1, 2])),
                    "bathrooms": int(np.random.choice([1, 2])),
                    "building_age_years": float(np.random.uniform(0, 40)),
                    "floor": int(np.random.randint(1, 15)),
                    "total_floors": int(np.random.randint(5, 20)),
                    "floor_ratio": float(np.random.uniform(0.1, 0.9)),
                    "parking_type": np.random.choice(
                        ["坡道平面", "坡道機械", None], p=[0.4, 0.3, 0.3]
                    ),
                    "parking_area_ping": float(np.random.uniform(0, 15)),
                    "transaction_year": d.year,
                    "transaction_month": d.month,
                    "transaction_date": d,
                    "target_unit_price_twd": float(
                        np.random.uniform(200000, 800000)
                    ),
                }
            )
    df = pd.DataFrame(rows)
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    return df


def test_resale_diagnostics_exposes_a18_drift(large_model_frame):
    split = split_by_time(large_model_frame)
    diagnostics = build_resale_diagnostics(large_model_frame, split)
    assert set(diagnostics["station_counts"]) == {"A17", "A18", "A19"}
    assert "building_age_years" in diagnostics["missing_rates"]
    assert diagnostics["monthly_summary"]
    assert any(
        row["station_code"] == "A18"
        for row in diagnostics["building_type_summary"]
    )
    assert set(diagnostics["split_summary"]) == {
        "train",
        "calibration",
        "test",
    }


def test_diagnostics_are_json_serializable(large_model_frame):
    payload = build_resale_diagnostics(
        large_model_frame, split_by_time(large_model_frame)
    )
    json.dumps(payload)
