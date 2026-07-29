import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyRegressor

from qingpu_insight.model_features import FEATURE_COLUMNS, ValuationInput
from qingpu_insight.parking_valuation import ParkingPricePolicy, ParkingPriceStat
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
        parking_price_policy=ParkingPricePolicy(
            version=1,
            minimum_type_samples=20,
            by_type={
                "坡道平面": ParkingPriceStat(1_700_000, 40),
                "坡道機械": ParkingPriceStat(800_000, 15),
            },
            market_fallback=ParkingPriceStat(1_200_000, 60),
        ),
    )


@pytest.fixture
def market() -> pd.DataFrame:
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2023-06-01", "2026-06-01", periods=n)
    return pd.DataFrame(
        {
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
        }
    )


class FakeRegistry:
    def __init__(self, bundle: ValuationBundle):
        self._bundle = bundle

    def get(self, transaction_type: str) -> ValuationBundle:
        if self._bundle.transaction_type != transaction_type:
            raise ModelUnavailableError(f"{transaction_type} model artifact not found")
        return self._bundle


class UnavailableRegistry:
    def get(self, transaction_type: str) -> ValuationBundle:
        raise ModelUnavailableError(f"{transaction_type} model artifact not found")


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


def test_valuation_has_ordered_interval_and_five_or_fewer_comparables(
    bundle, market, valid_resale_input
):
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
        transaction_type="resale",
        model_name="test",
        model_version="v1",
        pipeline=dummy,
        interval_abs_residual_twd_per_ping=10000,
        feature_ranges={},
        feature_hard_ranges={},
        feature_medians={},
        global_importance=[],
        reference_rows=pd.DataFrame(),
        data_min_date="",
        data_max_date="",
        metrics={},
    )
    low, high = prediction_interval(bundle, 500_000)
    assert low == 490_000
    assert high == 510_000


def test_prediction_interval_low_floor():
    dummy = DummyRegressor()
    dummy.fit(np.zeros((5, 5)), np.ones(5))
    bundle = ValuationBundle(
        transaction_type="resale",
        model_name="test",
        model_version="v1",
        pipeline=dummy,
        interval_abs_residual_twd_per_ping=50000,
        feature_ranges={},
        feature_hard_ranges={},
        feature_medians={},
        global_importance=[],
        reference_rows=pd.DataFrame(),
        data_min_date="",
        data_max_date="",
        metrics={},
    )
    low, high = prediction_interval(bundle, 10_000)
    assert low == 0.0
    assert high == 60_000


def test_confidence_high_when_all_conditions_met(bundle):
    row = pd.DataFrame(
        {
            "building_area_ping": [30],
            "station_distance_m": [500],
            "bedrooms": [3],
            "living_rooms": [2],
            "bathrooms": [2],
            "building_age_years": [6],
            "floor": [12],
            "total_floors": [15],
            "parking_area_ping": [10],
        }
    )
    result = confidence_assessment(
        bundle,
        row,
        500_000,
        (425_000, 575_000),
        [{"similarity_score": 0.7}, {"similarity_score": 0.8}, {"similarity_score": 0.9}],
    )
    assert result["confidence"] == "high"


def test_confidence_compares_interval_in_unit_price_units(bundle):
    row = pd.DataFrame(
        {
            "building_area_ping": [30],
            "station_distance_m": [500],
            "bedrooms": [3],
            "living_rooms": [2],
            "bathrooms": [2],
            "building_age_years": [6],
            "floor": [12],
            "total_floors": [15],
            "parking_area_ping": [10],
        }
    )
    result = confidence_assessment(
        bundle,
        row,
        500_000,
        (299_999, 700_001),
        [{"similarity_score": 0.7}, {"similarity_score": 0.8}, {"similarity_score": 0.9}],
    )
    assert result["confidence"] == "medium"
    assert "估價誤差半徑超過估計單價 40%" in result["confidence_reasons"]


def test_confidence_low_when_degraded():
    dummy = DummyRegressor()
    dummy.fit(np.zeros((5, 5)), np.ones(5))
    bundle = ValuationBundle(
        transaction_type="resale",
        model_name="test",
        model_version="v1",
        pipeline=dummy,
        interval_abs_residual_twd_per_ping=50000,
        feature_ranges={},
        feature_hard_ranges={},
        feature_medians={},
        global_importance=[],
        reference_rows=pd.DataFrame(),
        data_min_date="",
        data_max_date="",
        metrics={},
    )
    row = pd.DataFrame(
        {
            "building_area_ping": [30],
            "station_distance_m": [500],
            "bedrooms": [3],
            "living_rooms": [2],
            "bathrooms": [2],
            "building_age_years": None,
            "floor": [12],
            "total_floors": [15],
            "parking_area_ping": [10],
        }
    )
    result = confidence_assessment(
        bundle,
        row,
        500_000,
        (400_000, 600_000),
        [{"similarity_score": 0.7}],
        degraded=True,
    )
    assert result["confidence"] == "low"


def test_model_age_days_returns_non_negative(bundle):
    from qingpu_insight.valuation import model_age_days

    age = model_age_days(bundle, pd.Timestamp("2026-06-12"))
    assert age > 0


def test_stale_model_uses_recent_baseline(bundle, market, valid_resale_input):
    from dataclasses import replace

    stale_bundle = replace(bundle, data_max_date="2024-12-12")
    result = valuate(
        valid_resale_input,
        FakeRegistry(stale_bundle),
        market,
        latest_data_date=pd.Timestamp("2026-06-12"),
        stale_after_days=180,
    )
    assert result["degraded"] is True
    assert result["degraded_reason"] == "stale_model"
    assert result["model"]["name"] == "recent_median_baseline"
    assert "正式模型資料過舊" in result["confidence_reasons"]


def test_fresh_model_remains_official(bundle, market, valid_resale_input):
    from dataclasses import replace

    fresh_bundle = replace(bundle, data_max_date="2024-12-12")
    result = valuate(
        valid_resale_input,
        FakeRegistry(fresh_bundle),
        market,
        latest_data_date=pd.Timestamp("2025-01-01"),
        stale_after_days=180,
    )
    assert result["degraded"] is False
    assert result["degraded_reason"] is None


def test_presale_does_not_use_resale_staleness_rule(bundle, market):
    presale_bundle = replace(
        bundle,
        transaction_type="presale",
        data_max_date="2024-01-01",
    )
    presale_market = market.assign(transaction_type="presale")
    input_ = ValuationInput(
        transaction_type="presale",
        station_code="A18",
        station_distance_m=500,
        building_area_ping=30,
        building_type="住宅大樓",
        bedrooms=3,
        living_rooms=2,
        bathrooms=2,
        building_age_years=None,
        floor=5,
        total_floors=15,
    )
    result = valuate(
        input_,
        FakeRegistry(presale_bundle),
        presale_market,
        latest_data_date=pd.Timestamp("2026-06-12"),
        stale_after_days=180,
    )
    assert result["degraded"] is False
    assert result["model"]["name"] == "ridge"


def test_degraded_valuation_uses_recent_same_station_and_building_type(valid_resale_input):
    market = pd.DataFrame(
        {
            "transaction_type": ["resale"] * 5,
            "transaction_date": pd.to_datetime(
                [
                    "2023-01-01",
                    "2025-01-01",
                    "2025-02-01",
                    "2025-03-01",
                    "2025-04-01",
                ]
            ),
            "station_code": ["A17", "A17", "A17", "A18", "A17"],
            "building_type": ["住宅大樓", "住宅大樓", "住宅大樓", "住宅大樓", "華廈"],
            "unit_price_per_ping_twd": [100_000, 500_000, 700_000, 900_000, 300_000],
        }
    )
    value = replace(valid_resale_input, building_type="住宅大樓")
    result = valuate(value, UnavailableRegistry(), market)
    assert result["estimated_unit_price_per_ping_twd"] == 600_000
    assert result["data_date"] == "2025-04-01"


def test_degraded_valuation_refuses_to_invent_price_without_market_data(valid_resale_input):
    empty = pd.DataFrame(
        columns=[
            "transaction_type",
            "transaction_date",
            "station_code",
            "building_type",
            "unit_price_per_ping_twd",
        ]
    )
    with pytest.raises(ModelUnavailableError, match="market data"):
        valuate(valid_resale_input, UnavailableRegistry(), empty)


def test_parking_is_additive_and_does_not_change_building_estimate(
    bundle, market, valid_resale_input
):
    from dataclasses import replace

    no_parking = valuate(
        replace(valid_resale_input, parking_type="", parking_area_ping=0),
        FakeRegistry(bundle),
        market,
    )
    flat = valuate(
        replace(valid_resale_input, parking_type="坡道平面", parking_area_ping=8),
        FakeRegistry(bundle),
        market,
    )

    assert flat["estimated_building_price_twd"] == no_parking["estimated_building_price_twd"]
    assert (
        flat["estimated_total_price_twd"]
        == flat["estimated_building_price_twd"] + flat["estimated_parking_price_twd"]
    )
    assert flat["estimated_total_price_twd"] > no_parking["estimated_total_price_twd"]


def test_legacy_bundle_no_parking_policy(bundle, market, valid_resale_input):
    bundle.__dict__.pop("parking_price_policy", None)
    result = valuate(valid_resale_input, FakeRegistry(bundle), market)
    assert result["estimated_parking_price_twd"] is None
    assert result["estimated_building_price_twd"] == result["estimated_total_price_twd"]


def test_legacy_parking_feature_contract_remains_servable(bundle, market, valid_resale_input):
    class LegacyParkingPipeline:
        def predict(self, frame):
            raise AssertionError("legacy parking model must not be used")

    bundle.pipeline = LegacyParkingPipeline()
    bundle.feature_columns = FEATURE_COLUMNS + (
        "parking_type",
        "parking_area_ping",
    )
    bundle.parking_price_policy = None
    market = market.copy()
    market["parking_type"] = "坡道平面"
    market["parking_price_twd"] = 1_500_000
    no_parking_input = replace(
        valid_resale_input,
        parking_type="",
        parking_area_ping=0,
    )

    no_parking = valuate(no_parking_input, FakeRegistry(bundle), market)
    with_parking = valuate(valid_resale_input, FakeRegistry(bundle), market)

    assert (
        with_parking["estimated_building_price_twd"] == (no_parking["estimated_building_price_twd"])
    )
    assert with_parking["estimated_total_price_twd"] > (no_parking["estimated_total_price_twd"])
    assert with_parking["degraded_reason"] == "legacy_parking_contract"
    json.dumps(with_parking, ensure_ascii=False)


# --- Task 3: Parking policy ---


def test_train_artifact_persists_parking_policy(tmp_path):
    np.random.seed(42)
    n = 200
    train = pd.DataFrame(
        {
            "transaction_date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "target_unit_price_twd": np.random.uniform(200_000, 800_000, n),
            "parking_type": np.random.choice(["坡道平面", "坡道機械", ""], n, p=[0.5, 0.3, 0.2]),
            "parking_price_twd": np.where(
                np.random.random(n) > 0.2, np.random.uniform(500_000, 2_000_000, n), 0
            ),
        }
    )
    for col in FEATURE_COLUMNS:
        if col in ("station_code", "building_type", "parking_type"):
            train[col] = "A17"
        else:
            train[col] = np.random.randn(n)
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
    metrics = pd.DataFrame(
        {"overall": {"mae": 50000, "mape": 10, "rmse": 60000, "r2": 0.5, "count": 50}}
    ).T

    seed_bundle = ValuationBundle(
        transaction_type="resale",
        model_name="baseline",
        model_version="v1",
        pipeline=estimator,
        interval_abs_residual_twd_per_ping=50000,
        feature_ranges={},
        feature_hard_ranges={},
        feature_medians={},
        global_importance=[],
        reference_rows=train,
        data_min_date="2024-01-01",
        data_max_date="2026-06-01",
        metrics={},
    )
    selected = FakeSelected(name="baseline", estimator=estimator, metrics=metrics)

    import sklearn.inspection

    original_pi = sklearn.inspection.permutation_importance

    def fake_pi(estimator, X, y, **kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(importances_mean=np.zeros(len(FEATURE_COLUMNS)))

    sklearn.inspection.permutation_importance = fake_pi

    try:
        path = train_artifact(
            "resale",
            selected,
            split,
            seed_bundle,
            tmp_path,
            feature_columns=FEATURE_COLUMNS,
            training_frame=train,
        )
    finally:
        sklearn.inspection.permutation_importance = original_pi

    loaded = joblib.load(path)
    assert loaded.parking_price_policy.version == 1
    assert loaded.parking_price_policy.by_type["坡道平面"].price_twd > 0
    assert "parking_type" not in loaded.feature_columns


def test_old_bundle_pickle_gets_no_parking_policy(bundle):
    del bundle.__dict__["parking_price_policy"]
    assert bundle.parking_price_policy is None


# --- Task 2 helpers ---


def _candidate(
    area=31,
    distance=520,
    bedrooms=3,
    months_old=6,
    age=6.0,
    floor_ratio=0.5,
    building_type="住宅大樓",
    record_id="C0",
) -> dict:
    """Build a candidate dict for a market DataFrame row."""
    return {
        "record_id": record_id,
        "transaction_type": "resale",
        "transaction_date": pd.Timestamp("2026-06-01") - pd.DateOffset(months=months_old),
        "station_code": "A17",
        "building_type": building_type,
        "building_area_ping": area,
        "unit_price_per_ping_twd": 500000,
        "total_price_twd": 15000000,
        "station_distance_m": distance,
        "bedrooms": bedrooms,
        "floor_ratio": floor_ratio,
        "longitude": 121.23,
        "latitude": 25.03,
        "building_age_years": age,
    }


def _market(candidates: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(candidates)


def test_comparable_similarity_is_absolute_not_batch_relative(bundle):
    input_row = pd.DataFrame(
        {
            "station_code": ["A17"],
            "building_area_ping": [30],
            "station_distance_m": [500],
            "bedrooms": [3],
            "building_age_years": [6.0],
            "floor_ratio": [0.5],
            "building_type": ["住宅大樓"],
        }
    )
    close = _candidate(area=31, distance=520, bedrooms=3, months_old=6)
    far = _candidate(area=80, distance=1900, bedrooms=1, months_old=35)
    close_rows = [close] * 3
    base = similar_transactions(bundle, input_row, _market(close_rows))
    with_outlier = similar_transactions(bundle, input_row, _market(close_rows + [far]))
    assert (
        base["comparables"][0]["similarity_score"]
        == (with_outlier["comparables"][0]["similarity_score"])
    )


def test_similar_transactions_exposes_dwelling_unit_price(bundle):
    input_row = pd.DataFrame(
        {
            "station_code": ["A17"],
            "building_area_ping": [30],
            "station_distance_m": [500],
            "bedrooms": [3],
            "building_age_years": [6.0],
            "floor_ratio": [0.5],
            "building_type": ["住宅大樓"],
        }
    )
    candidates = [
        {
            **_candidate(record_id=f"C{index}"),
            "target_unit_price_twd": 320_000 + index * 10_000,
        }
        for index in range(3)
    ]

    result = similar_transactions(bundle, input_row, _market(candidates))

    first = result["comparables"][0]
    assert first["dwelling_unit_price_per_ping_twd"] in {
        320_000,
        330_000,
        340_000,
    }
    assert first["rank"] == 1
    assert first["price_twd"] == first["total_price_twd"]


def test_missing_optional_age_renormalizes_weights():
    candidate = _candidate(age=float("nan"))
    input_row_without_age = pd.Series(
        {
            "station_code": "A17",
            "building_area_ping": 30,
            "station_distance_m": 500,
            "bedrooms": 3,
            "floor_ratio": 0.5,
            "building_type": "住宅大樓",
        }
    )
    max_date = pd.Timestamp("2026-06-01")
    from qingpu_insight.valuation import _comparable_similarity

    score = _comparable_similarity(input_row_without_age, candidate, max_date)
    assert 0 <= score <= 1


def test_layout_similarity_averages_rooms_living_rooms_and_bathrooms():
    from qingpu_insight.valuation import _layout_similarity

    input_row = pd.Series({"bedrooms": 3, "living_rooms": 2, "bathrooms": 2})
    candidate = pd.Series({"bedrooms": 3, "living_rooms": 1, "bathrooms": 1})

    assert _layout_similarity(input_row, candidate) == pytest.approx(2 / 3)


def test_future_candidate_similarity_stays_bounded():
    from qingpu_insight.valuation import _comparable_similarity

    input_row = pd.Series(
        {
            "building_area_ping": 30,
            "station_distance_m": 500,
            "bedrooms": 3,
            "living_rooms": 2,
            "bathrooms": 2,
            "building_age_years": 6,
            "floor_ratio": 0.5,
            "building_type": "住宅大樓",
        }
    )
    candidate = input_row.copy()
    candidate["transaction_date"] = pd.Timestamp("2026-07-01")

    assert _comparable_similarity(input_row, candidate, pd.Timestamp("2026-06-01")) <= 1


def test_typical_close_cases_clear_point_six(bundle):
    input_row = pd.DataFrame(
        {
            "station_code": ["A17"],
            "building_area_ping": [30],
            "station_distance_m": [500],
            "bedrooms": [3],
            "building_age_years": [6.0],
            "floor_ratio": [0.5],
            "building_type": ["住宅大樓"],
        }
    )
    close_candidates = [
        _candidate(area=31, distance=520, bedrooms=3, months_old=6, record_id="C1"),
        _candidate(area=28, distance=480, bedrooms=3, months_old=4, record_id="C2"),
        _candidate(area=32, distance=510, bedrooms=2, months_old=8, record_id="C3"),
        _candidate(
            area=55,
            distance=1200,
            bedrooms=4,
            months_old=15,
            building_type="華廈",
            record_id="C4",
        ),
        _candidate(area=70, distance=1800, bedrooms=1, months_old=30, record_id="C5"),
    ]
    result = similar_transactions(bundle, input_row, _market(close_candidates))
    assert sum(c["similarity_score"] >= 0.60 for c in result["comparables"]) >= 3


def test_confidence_not_low_solely_from_comparable_insufficiency_when_three_good(
    bundle, market, valid_resale_input
):
    result = valuate(valid_resale_input, FakeRegistry(bundle), market)
    good = sum(1 for c in result["comparables"] if c["similarity_score"] >= 0.60)
    if good >= 3:
        assert not any("高相似度成交案例不足" in r for r in result["confidence_reasons"])


def test_dissimilar_comparables_include_insufficient_evidence_reason(bundle, valid_resale_input):
    small_market = pd.DataFrame(
        {
            "record_id": ["D1", "D2", "D3"],
            "transaction_type": ["resale", "resale", "resale"],
            "transaction_date": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"]),
            "station_code": ["A17", "A17", "A17"],
            "building_type": ["公寓", "公寓", "公寓"],
            "building_area_ping": [80, 90, 100],
            "unit_price_per_ping_twd": [400000, 500000, 600000],
            "total_price_twd": [32000000, 45000000, 60000000],
            "station_distance_m": [2000, 2500, 3000],
            "bedrooms": [1, 1, 1],
            "floor_ratio": [0.1, 0.1, 0.1],
            "longitude": [121.20, 121.21, 121.22],
            "latitude": [25.00, 25.01, 25.02],
            "building_age_years": [30.0, 35.0, 40.0],
        }
    )
    result = valuate(valid_resale_input, FakeRegistry(bundle), small_market)
    assert any("高相似度成交案例不足" in r for r in result["confidence_reasons"])


def test_similar_transactions_insufficient_data(bundle):
    row = pd.DataFrame(
        {
            "station_code": ["A17"],
            "building_area_ping": [30],
            "station_distance_m": [500],
            "bedrooms": [3],
            "building_age_years": [6],
            "floor_ratio": [0.8],
            "building_type": ["住宅大樓"],
        }
    )
    empty_market = pd.DataFrame(
        columns=[
            "transaction_type",
            "transaction_date",
            "station_code",
            "building_type",
            "building_area_ping",
            "unit_price_per_ping_twd",
            "total_price_twd",
            "floor_ratio",
            "longitude",
            "latitude",
            "building_age_years",
            "record_id",
            "bedrooms",
            "station_distance_m",
        ]
    )
    result = similar_transactions(bundle, row, empty_market)
    assert result["comparable_scope"] == "insufficient_data"
    assert len(result["comparables"]) == 0


class FitRecordingEstimator:
    def __init__(self):
        self.fit_called = False

    def fit(self, X, y=None):
        self.fit_called = True
        return self

    def predict(self, X):
        return np.full(len(X), 500_000)


def test_train_artifact_uses_calibration_and_does_not_refit(tmp_path):
    np.random.seed(42)
    n = 200
    train = pd.DataFrame(
        {
            "transaction_date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "target_unit_price_twd": np.random.uniform(200_000, 800_000, n),
        }
    )
    for col in FEATURE_COLUMNS:
        if col in ("station_code", "building_type", "parking_type"):
            train[col] = "A17"
        else:
            train[col] = np.random.randn(n)
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

    est = FitRecordingEstimator()

    @dataclass
    class FakeSelected:
        name: str
        estimator: Any
        metrics: pd.DataFrame

    metrics = pd.DataFrame(
        {"overall": {"mae": 50000, "mape": 10, "rmse": 60000, "r2": 0.5, "count": 50}}
    ).T

    bundle = ValuationBundle(
        transaction_type="resale",
        model_name="baseline",
        model_version="v1",
        pipeline=est,
        interval_abs_residual_twd_per_ping=50000,
        feature_ranges={},
        feature_hard_ranges={},
        feature_medians={},
        global_importance=[],
        reference_rows=train,
        data_min_date="2024-01-01",
        data_max_date="2026-06-01",
        metrics={},
    )
    selected = FakeSelected(name="baseline", estimator=est, metrics=metrics)

    import sklearn.inspection

    original_pi = sklearn.inspection.permutation_importance
    pi_X_captured = {}

    def fake_pi(estimator, X, y, **kwargs):
        pi_X_captured["X"] = X
        from types import SimpleNamespace

        return SimpleNamespace(importances_mean=np.zeros(len(FEATURE_COLUMNS)))

    sklearn.inspection.permutation_importance = fake_pi

    try:
        result_path = train_artifact("resale", selected, split, bundle, tmp_path)
    finally:
        sklearn.inspection.permutation_importance = original_pi

    assert not est.fit_called, "train_artifact must not call fit()"

    captured_X = pi_X_captured["X"]
    assert len(captured_X) == len(calibration)
    assert list(captured_X.columns) == list(FEATURE_COLUMNS)

    loaded: ValuationBundle = joblib.load(result_path)
    assert loaded.transaction_type == "resale"
    assert loaded.model_name == "baseline"


def test_train_artifact_round_trip(tmp_path):
    np.random.seed(42)
    train = pd.DataFrame(
        {
            "transaction_date": pd.date_range("2024-01-01", periods=200, freq="D"),
            "target_unit_price_twd": np.random.uniform(200_000, 800_000, 200),
        }
    )
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
    metrics = pd.DataFrame(
        {"overall": {"mae": 50000, "mape": 10, "rmse": 60000, "r2": 0.5, "count": 50}}
    ).T

    bundle = ValuationBundle(
        transaction_type="resale",
        model_name="baseline",
        model_version="v1",
        pipeline=estimator,
        interval_abs_residual_twd_per_ping=50000,
        feature_ranges={},
        feature_hard_ranges={},
        feature_medians={},
        global_importance=[],
        reference_rows=train,
        data_min_date="2024-01-01",
        data_max_date="2026-06-01",
        metrics={},
    )
    selected = FakeSelected(name="baseline", estimator=estimator, metrics=metrics)

    result_path = train_artifact("resale", selected, split, bundle, tmp_path)
    assert result_path.exists()
    assert result_path.name == "resale.joblib"

    loaded: ValuationBundle = joblib.load(result_path)
    assert loaded.transaction_type == "resale"
    assert loaded.model_name == "baseline"
    assert loaded.metrics["overall"]["mae"] == 50000


def test_train_artifact_half_life(tmp_path, monkeypatch):
    import qingpu_insight.valuation as valuation_mod

    np.random.seed(42)
    n = 200
    train = pd.DataFrame(
        {
            "transaction_date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "target_unit_price_twd": np.random.uniform(200_000, 800_000, n),
        }
    )
    for col in FEATURE_COLUMNS:
        if col in ("station_code", "building_type", "parking_type"):
            train[col] = "A17"
        else:
            train[col] = np.random.randn(n)
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

    est = FitRecordingEstimator()

    @dataclass
    class FakeSelected:
        name: str
        estimator: Any
        metrics: pd.DataFrame

    metrics = pd.DataFrame(
        {"overall": {"mae": 50000, "mape": 10, "rmse": 60000, "r2": 0.5, "count": 50}}
    ).T

    bundle = ValuationBundle(
        transaction_type="resale",
        model_name="baseline",
        model_version="v1",
        pipeline=est,
        interval_abs_residual_twd_per_ping=50000,
        feature_ranges={},
        feature_hard_ranges={},
        feature_medians={},
        global_importance=[],
        reference_rows=train,
        data_min_date="2024-01-01",
        data_max_date="2026-06-01",
        metrics={},
    )
    selected = FakeSelected(name="baseline", estimator=est, metrics=metrics)

    captured = {}
    original = valuation_mod.recency_weights

    def capture(frame, reference_date=None, half_life_months=48, minimum=0.10):
        captured["half_life"] = half_life_months
        return original(frame, reference_date, half_life_months, minimum)

    monkeypatch.setattr(valuation_mod, "recency_weights", capture)

    import sklearn.inspection

    original_pi = sklearn.inspection.permutation_importance

    def fake_pi(estimator, X, y, **kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(importances_mean=np.zeros(len(FEATURE_COLUMNS)))

    sklearn.inspection.permutation_importance = fake_pi

    try:
        train_artifact(
            "resale",
            selected,
            split,
            bundle,
            tmp_path,
            use_recency_weights=True,
            recency_half_life_months=30,
        )
    finally:
        sklearn.inspection.permutation_importance = original_pi

    assert captured["half_life"] == 30


class TestModelRegistryOfficialPreference:
    def test_registry_prefers_official_manifest_over_legacy_file(
        self,
        tmp_path: Path,
        market: pd.DataFrame,
        valid_resale_input: ValuationInput,
    ) -> None:
        from datetime import UTC, date, datetime
        from uuid import uuid4

        from qingpu_insight.model_artifacts import (
            DataSnapshot,
            MarketTrainingResult,
            TrainingManifest,
            sha256_file,
        )
        from qingpu_insight.model_release import OfficialModelStore

        # Write legacy bundle
        def _make_bundle(market: str, model_version: str = "1.0") -> ValuationBundle:
            dummy = DummyRegressor(strategy="constant", constant=500_000)
            dummy.fit(np.zeros((5, 5)), np.ones(5))
            return ValuationBundle(
                transaction_type=market,
                model_name="ridge",
                model_version=model_version,
                pipeline=dummy,
                interval_abs_residual_twd_per_ping=1000.0,
                feature_ranges={},
                feature_hard_ranges={},
                feature_medians={},
                global_importance=[],
                reference_rows=pd.DataFrame(),
                data_min_date="2024-01-01",
                data_max_date="2024-12-31",
                metrics={},
            )

        legacy_bundle = _make_bundle("resale", model_version="legacy")
        joblib.dump(legacy_bundle, tmp_path / "resale.joblib")

        # Write official version
        candidate_dir = tmp_path / "candidate"
        candidate_dir.mkdir()
        official_bundle = _make_bundle("resale", model_version="official-v2")
        joblib.dump(official_bundle, candidate_dir / "resale.joblib")
        artifact_hash = sha256_file(candidate_dir / "resale.joblib")

        manifest = TrainingManifest(
            run_id=uuid4(),
            created_at=datetime.now(UTC),
            markets=["resale"],
            source_commit="test",
            source_dirty=False,
            runtime_versions={"python": "3.11"},
            data_snapshot=DataSnapshot(
                sha256="a" * 64,
                raw_count=100,
                usable_counts={"resale": 80, "presale": 0},
                excluded_counts={"resale": 20, "presale": 0},
                station_counts={"A17": 50, "A18": 30, "A19": 20},
                min_date=date(2024, 1, 1),
                max_date=date(2024, 12, 31),
            ),
            results=[
                MarketTrainingResult(
                    market="resale",
                    selected_model="ridge",
                    recommended=True,
                    reason_codes=["best"],
                    selection_metrics={"cv": {"mae": 1000.0}},
                    final_test_metrics={"test": {"mae": 1200.0}},
                    artifact_file="resale.joblib",
                    artifact_sha256=artifact_hash,
                    report_files={},
                    report_sha256={},
                )
            ],
        )
        (candidate_dir / "manifest.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )

        store = OfficialModelStore(tmp_path)
        record = store.import_candidate(candidate_dir, manifest, "resale")
        store.activate("resale", record.version_id)

        bundle = ModelRegistry(tmp_path).load("resale")
        assert bundle.model_version == "official-v2"

        (tmp_path / "resale.joblib").unlink()
        result = valuate(
            valid_resale_input,
            ModelRegistry(tmp_path),
            market,
            latest_data_date=pd.Timestamp("2024-12-31"),
        )
        assert result["model"]["version"] == "official-v2"
        assert result["model"]["version"] != "fallback"
