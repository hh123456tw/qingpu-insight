from dataclasses import replace

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyRegressor

from qingpu_insight.model_features import FEATURE_COLUMNS
from qingpu_insight.valuation import (
    ModelRegistry,
    ModelUnavailableError,
    ValuationBundle,
    confidence_assessment,
    prediction_interval,
    similar_transactions,
    train_artifact,
    valuate,
)


@pytest.fixture
def bundle() -> ValuationBundle:
    dummy = DummyRegressor(strategy="constant", constant=500_000)
    dummy.fit(np.zeros((5, 5)), np.ones(5))
    return ValuationBundle(
        transaction_type="resale",
        model_name="ridge",
        model_version="resale-2026-06-01-a1b2c3d4",
        pipeline=dummy,
        interval_abs_residual_twd_per_ping=50_000,
        feature_ranges={
            "building_area_ping": (20, 80),
            "station_distance_m": (100, 1500),
            "bedrooms": (1, 5),
            "living_rooms": (1, 4),
            "bathrooms": (1, 4),
            "building_age_years": (0, 30),
            "floor": (1, 20),
            "total_floors": (5, 25),
            "parking_area_ping": (0, 20),
        },
        feature_hard_ranges={
            "building_area_ping": (15, 90),
            "station_distance_m": (50, 1800),
            "bedrooms": (1, 5),
            "living_rooms": (1, 4),
            "bathrooms": (1, 4),
            "building_age_years": (0, 40),
            "floor": (1, 22),
            "total_floors": (3, 28),
            "parking_area_ping": (0, 25),
        },
        feature_medians={
            "building_area_ping": 35.0,
            "station_distance_m": 600.0,
            "bedrooms": 3.0,
            "living_rooms": 2.0,
            "bathrooms": 2.0,
            "building_age_years": 10.0,
            "floor": 8.0,
            "total_floors": 15.0,
            "parking_area_ping": 8.0,
        },
        global_importance=[
            {"feature": "station_distance_m", "importance": 0.15},
            {"feature": "building_area_ping", "importance": 0.12},
        ],
        reference_rows=pd.DataFrame({"dummy": [1]}),
        data_min_date="2024-01-01",
        data_max_date="2026-06-01",
        metrics={"overall": {"mae": 45000}},
    )


@pytest.fixture
def market() -> pd.DataFrame:
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2023-06-01", "2026-06-01", periods=n)
    return pd.DataFrame({
        "record_id": [f"R{i}" for i in range(n)],
        "transaction_type": ["resale"] * n,
        "transaction_date": dates,
        "station_code": np.random.choice(["A17", "A18", "A19"], n),
        "building_type": np.random.choice(["住宅大樓", "華廈"], n),
        "building_area_ping": np.random.uniform(15, 80, n),
        "unit_price_per_ping_twd": np.random.uniform(200_000, 800_000, n).astype(int),
        "total_price_twd": np.random.uniform(5_000_000, 40_000_000, n).astype(int),
        "station_distance_m": np.random.uniform(100, 1500, n),
        "bedrooms": np.random.randint(1, 5, n),
        "floor_ratio": np.random.uniform(0.1, 0.9, n),
        "longitude": np.random.uniform(121.20, 121.25, n),
        "latitude": np.random.uniform(25.00, 25.05, n),
        "building_age_years": np.where(
            np.random.random(n) > 0.2,
            np.random.uniform(0, 30, n),
            np.nan,
        ),
    })


class FakeRegistry:
    def __init__(self, bundle: ValuationBundle):
        self._bundle = bundle

    def get(self, transaction_type: str) -> ValuationBundle:
        if self._bundle.transaction_type != transaction_type:
            raise ModelUnavailableError(
                f"{transaction_type} model artifact not found"
            )
        return self._bundle


def test_registry_never_serves_other_transaction_type(tmp_path):
    dummy = DummyRegressor()
    dummy.fit(np.zeros((5, 5)), np.ones(5))
    resale_bundle = ValuationBundle(
        transaction_type="resale",
        model_name="ridge",
        model_version="v1",
        pipeline=dummy,
        interval_abs_residual_twd_per_ping=50000,
        feature_ranges={},
        feature_hard_ranges={},
        feature_medians={},
        global_importance=[],
        reference_rows=pd.DataFrame(),
        data_min_date="2024-01-01",
        data_max_date="2026-06-01",
        metrics={},
    )
    joblib.dump(resale_bundle, tmp_path / "resale.joblib")
    registry = ModelRegistry(tmp_path)
    with pytest.raises(ModelUnavailableError, match="presale"):
        registry.get("presale")


def test_valuation_has_ordered_interval_and_five_or_fewer_comparables(bundle, market, valid_resale_input):
    result = valuate(valid_resale_input, FakeRegistry(bundle), market)
    low, high = result["interval_total_price_twd"]
    assert low <= result["estimated_total_price_twd"] <= high
    assert len(result["comparables"]) <= 5


def test_comparables_are_same_type_public_and_recent(bundle, market, valid_resale_input):
    result = valuate(valid_resale_input, FakeRegistry(bundle), market)
    for row in result["comparables"]:
        assert row["transaction_type"] == "resale"
    if result["comparables"]:
        keys = set(result["comparables"][0].keys())
        assert "address" not in keys
        assert "transaction_key" not in keys


def test_out_of_range_input_is_low_confidence(bundle, market, valid_resale_input):
    result = valuate(
        replace(valid_resale_input, building_area_ping=199),
        FakeRegistry(bundle),
        market,
    )
    assert result["confidence"] == "low"
    assert "部分輸入數值超出主要訓練範圍" in result["confidence_reasons"]


def test_prediction_interval_symmetric():
    dummy = DummyRegressor()
    dummy.fit(np.zeros((5, 5)), np.ones(5))
    bundle = ValuationBundle(
        transaction_type="resale", model_name="test", model_version="v1",
        pipeline=dummy, interval_abs_residual_twd_per_ping=10000,
        feature_ranges={}, feature_hard_ranges={}, feature_medians={},
        global_importance=[], reference_rows=pd.DataFrame(),
        data_min_date="", data_max_date="", metrics={},
    )
    low, high = prediction_interval(bundle, 500_000)
    assert low == 490_000
    assert high == 510_000


def test_prediction_interval_low_floor():
    dummy = DummyRegressor()
    dummy.fit(np.zeros((5, 5)), np.ones(5))
    bundle = ValuationBundle(
        transaction_type="resale", model_name="test", model_version="v1",
        pipeline=dummy, interval_abs_residual_twd_per_ping=50000,
        feature_ranges={}, feature_hard_ranges={}, feature_medians={},
        global_importance=[], reference_rows=pd.DataFrame(),
        data_min_date="", data_max_date="", metrics={},
    )
    low, high = prediction_interval(bundle, 10_000)
    assert low == 0.0
    assert high == 60_000


def test_confidence_high_when_all_conditions_met(bundle):
    row = pd.DataFrame({
        "building_area_ping": [30], "station_distance_m": [500],
        "bedrooms": [3], "living_rooms": [2], "bathrooms": [2],
        "building_age_years": [6], "floor": [12], "total_floors": [15],
        "parking_area_ping": [10],
    })
    result = confidence_assessment(
        bundle, row, 500_000, (400_000, 600_000),
        [{"similarity_score": 0.7}, {"similarity_score": 0.8}, {"similarity_score": 0.9}],
    )
    assert result["confidence"] == "high"


def test_confidence_low_when_degraded():
    dummy = DummyRegressor()
    dummy.fit(np.zeros((5, 5)), np.ones(5))
    bundle = ValuationBundle(
        transaction_type="resale", model_name="test", model_version="v1",
        pipeline=dummy, interval_abs_residual_twd_per_ping=50000,
        feature_ranges={}, feature_hard_ranges={}, feature_medians={},
        global_importance=[], reference_rows=pd.DataFrame(),
        data_min_date="", data_max_date="", metrics={},
    )
    row = pd.DataFrame({
        "building_area_ping": [30], "station_distance_m": [500],
        "bedrooms": [3], "living_rooms": [2], "bathrooms": [2],
        "building_age_years": None, "floor": [12], "total_floors": [15],
        "parking_area_ping": [10],
    })
    result = confidence_assessment(
        bundle, row, 500_000, (400_000, 600_000),
        [{"similarity_score": 0.7}], degraded=True,
    )
    assert result["confidence"] == "low"


def test_similar_transactions_insufficient_data(bundle):
    row = pd.DataFrame({
        "station_code": ["A17"], "building_area_ping": [30],
        "station_distance_m": [500], "bedrooms": [3],
        "building_age_years": [6], "floor_ratio": [0.8],
        "building_type": ["住宅大樓"],
    })
    empty_market = pd.DataFrame(columns=[
        "transaction_type", "transaction_date", "station_code",
        "building_type", "building_area_ping", "unit_price_per_ping_twd",
        "total_price_twd", "floor_ratio", "longitude", "latitude",
        "building_age_years", "record_id", "bedrooms", "station_distance_m",
    ])
    result = similar_transactions(bundle, row, empty_market)
    assert result["comparable_scope"] == "insufficient_data"
    assert len(result["comparables"]) == 0


def test_train_artifact_round_trip(tmp_path):
    np.random.seed(42)
    train = pd.DataFrame({
        "transaction_date": pd.date_range("2024-01-01", periods=200, freq="D"),
        "target_unit_price_twd": np.random.uniform(200_000, 800_000, 200),
    })
    for col in FEATURE_COLUMNS:
        if col in ("station_code", "building_type", "parking_type"):
            train[col] = "A17"
        else:
            train[col] = np.random.randn(200)
    train["transaction_year"] = 2025
    train["transaction_month"] = 6
    for col in FEATURE_COLUMNS:
        if col not in train.columns:
            train[col] = 0

    calibration = train.iloc[:50].copy()
    test = train.iloc[50:100].copy()

    from dataclasses import dataclass

    @dataclass
    class FakeSplit:
        train: pd.DataFrame
        calibration: pd.DataFrame
        test: pd.DataFrame

    split = FakeSplit(train=train, calibration=calibration, test=test)

    @dataclass
    class FakeSelected:
        name: str
        estimator: object
        metrics: pd.DataFrame

    estimator = DummyRegressor(strategy="mean")
    estimator.fit(train[list(FEATURE_COLUMNS)], train["target_unit_price_twd"])
    metrics = pd.DataFrame({"overall": {"mae": 50000, "mape": 10, "rmse": 60000, "r2": 0.5, "count": 50}}).T

    bundle = ValuationBundle(
        transaction_type="resale", model_name="baseline", model_version="v1",
        pipeline=estimator, interval_abs_residual_twd_per_ping=50000,
        feature_ranges={}, feature_hard_ranges={}, feature_medians={},
        global_importance=[], reference_rows=train,
        data_min_date="2024-01-01", data_max_date="2026-06-01",
        metrics={},
    )
    selected = FakeSelected(name="baseline", estimator=estimator, metrics=metrics)

    result_path = train_artifact("resale", selected, split, bundle, tmp_path)
    assert result_path.exists()
    assert result_path.name == "resale.joblib"

    loaded: ValuationBundle = joblib.load(result_path)
    assert loaded.transaction_type == "resale"
    assert loaded.model_name == "baseline"
