import numpy as np
import pandas as pd
import pytest
from sklearn.base import BaseEstimator

import qingpu_insight.model_analysis as model_analysis
from qingpu_insight.model_analysis import run_annual_backtests
from qingpu_insight.model_features import FEATURE_COLUMNS, add_derived_features
from qingpu_insight.model_training import ModelFitSpec, build_estimator


class DummyEstimator(BaseEstimator):
    def fit(self, X, y, **kwargs):
        return self

    def predict(self, X):
        return np.zeros(len(X))


@pytest.fixture
def resale_frame():
    np.random.seed(42)
    stations = ["A17", "A18", "A19"]
    bt_types = ["住宅大樓", "華廈"]
    rows = []
    dates = pd.date_range("2021-01-01", "2026-06-30", periods=2000)
    for d in dates:
        age = float(np.random.uniform(0, 40))
        beds = int(np.random.choice([2, 3, 4]))
        station = np.random.choice(stations)
        signal = age * 8000 + beds * 20000 + (800000 if station == "A17" else 0)
        rows.append(
            {
                "station_code": station,
                "station_distance_m": float(np.random.uniform(100, 1500)),
                "building_area_ping": float(np.random.uniform(20, 80)),
                "building_type": np.random.choice(bt_types),
                "bedrooms": beds,
                "living_rooms": int(np.random.choice([1, 2])),
                "bathrooms": int(np.random.choice([1, 2])),
                "building_age_years": age,
                "floor": int(np.random.randint(1, 15)),
                "total_floors": int(np.random.randint(5, 20)),
                "floor_ratio": float(np.random.uniform(0.1, 0.9)),
                "parking_type": np.random.choice(["坡道平面", "坡道機械", ""]),
                "parking_area_ping": float(np.random.uniform(0, 15)),
                "transaction_year": d.year,
                "transaction_month": d.month,
                "transaction_date": d,
                "target_unit_price_twd": float(np.random.uniform(300000, 500000) + signal),
            }
        )
    df = pd.DataFrame(rows)
    return add_derived_features(df)


def test_build_hgb_from_exact_fit_spec() -> None:
    spec = ModelFitSpec(
        model_name="hist_gradient_boosting",
        parameters={
            "learning_rate": 0.07,
            "max_iter": 275,
            "max_leaf_nodes": 47,
            "l2_regularization": 2.5,
        },
        recency_half_life_months=36,
    )
    pipeline = build_estimator(spec, FEATURE_COLUMNS, seed=42)
    model = pipeline.named_steps["model"]
    assert model.learning_rate == 0.07
    assert model.max_iter == 275
    assert model.max_leaf_nodes == 47
    assert model.l2_regularization == 2.5


def test_build_rf_from_exact_fit_spec() -> None:
    spec = ModelFitSpec(
        model_name="random_forest",
        parameters={
            "n_estimators": 222,
            "min_samples_leaf": 7,
            "max_features": 0.65,
        },
        recency_half_life_months=60,
    )
    pipeline = build_estimator(spec, FEATURE_COLUMNS, seed=42)
    model = pipeline.named_steps["model"]
    assert model.n_estimators == 222
    assert model.min_samples_leaf == 7
    assert model.max_features == 0.65
    assert model.random_state == 42


def test_build_estimator_validates_model_name() -> None:
    with pytest.raises(ValueError, match="unknown model"):
        build_estimator(
            ModelFitSpec("unknown", {}, None),
            FEATURE_COLUMNS,
        )


def test_build_estimator_validates_parameters_not_empty() -> None:
    with pytest.raises(ValueError, match="empty parameters"):
        build_estimator(
            ModelFitSpec("random_forest", {}, None),
            FEATURE_COLUMNS,
        )


def test_annual_backtest_replays_automl_fit_spec(monkeypatch, resale_frame) -> None:
    seen: list[ModelFitSpec] = []
    spec = ModelFitSpec(
        "random_forest",
        {
            "n_estimators": 222,
            "min_samples_leaf": 7,
            "max_features": 0.65,
        },
        60,
    )
    monkeypatch.setattr(
        model_analysis,
        "build_estimator",
        lambda actual, **kwargs: seen.append(actual) or DummyEstimator(),
    )
    run_annual_backtests(resale_frame, "random_forest", fit_spec=spec)
    assert seen and all(item == spec for item in seen)
