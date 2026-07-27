import warnings

import numpy as np
import pandas as pd
import pytest
from sklearn.base import BaseEstimator

from qingpu_insight.model_features import FEATURE_COLUMNS
import qingpu_insight.model_training as model_training
from qingpu_insight.model_training import (
    BaselineEvaluationError,
    CandidateEvaluation,
    ProfileEvaluationError,
    RecentMedianBaseline,
    TimeSplit,
    TunedModelExperiment,
    candidate_estimators,
    evaluate_candidate,
    leakage_audit,
    make_preprocessor,
    metric_rows,
    run_model_experiment,
    run_tuned_model_experiment,
    select_release_candidate,
    split_by_time,
)
from qingpu_insight.model_tuning import TrainingProfile, PRESET_PROFILES


def _build_synthetic_frame(n_rows: int = 800) -> pd.DataFrame:
    np.random.seed(42)
    base = pd.Timestamp("2023-01-01")
    total_days = 1095
    dates = [base + pd.DateOffset(days=int(i * total_days / n_rows)) for i in range(n_rows)]

    stations = ["A17", "A18", "A19"]
    types = ["住宅大樓", "華廈"]
    ptypes = ["坡道平面", "坡道機械", ""]

    rows = []
    for i in range(n_rows):
        s = stations[i % 3]
        t = types[i % 2]
        pt = ptypes[i % 3]

        base_price = {"A17": 600_000, "A18": 500_000, "A19": 550_000}[s]
        type_mult = {"住宅大樓": 1.0, "華廈": 0.85}[t]
        target = base_price * type_mult + np.random.uniform(-50_000, 50_000)

        building_age = float(np.random.uniform(0, 30))
        fl = int(np.random.randint(1, 15))
        tfl = int(np.random.randint(5, 25))

        rows.append(
            {
                "transaction_date": dates[i],
                "station_code": s,
                "station_distance_m": float(np.random.randint(100, 1500)),
                "building_area_ping": float(np.random.uniform(15, 60)),
                "building_type": t,
                "bedrooms": int(np.random.randint(1, 5)),
                "living_rooms": int(np.random.randint(1, 3)),
                "bathrooms": int(np.random.randint(1, 3)),
                "building_age_years": building_age,
                "floor": fl,
                "total_floors": tfl,
                "floor_ratio": fl / tfl,
                "parking_type": pt,
                "parking_area_ping": float(np.random.uniform(0, 15) if pt else 0),
                "transaction_year": dates[i].year,
                "transaction_month": dates[i].month,
                "target_unit_price_twd": target,
                "transaction_key": f"T{i}",
                "road_key": f"R{i % 10}",
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture
def model_frame() -> pd.DataFrame:
    return _build_synthetic_frame(800)


def test_split_by_time_rejects_small_frame():
    small = pd.DataFrame({"transaction_date": [pd.Timestamp("2024-01-01")] * 10, "x": range(10)})
    with pytest.raises(ValueError, match="at least 100 rows"):
        split_by_time(small)


def test_time_split_never_trains_on_future_rows(model_frame):
    split = split_by_time(model_frame, test_months=12, calibration_months=6)
    assert split.train.transaction_date.max() < split.calibration.transaction_date.min()
    assert split.calibration.transaction_date.max() < split.test.transaction_date.min()


@pytest.fixture
def fallback_frame() -> pd.DataFrame:
    np.random.seed(42)
    rows = []
    for i in range(25):
        rows.append(
            {
                "transaction_date": pd.Timestamp("2024-01-01") + pd.DateOffset(days=i),
                "station_code": "A17",
                "building_type": "住宅大樓",
                "target_unit_price_twd": 600_000 + i * 1000,
            }
        )
    for i in range(5):
        rows.append(
            {
                "transaction_date": pd.Timestamp("2024-01-01") + pd.DateOffset(days=i),
                "station_code": "A17",
                "building_type": "華廈",
                "target_unit_price_twd": 500_000 + i * 1000,
            }
        )
    for i in range(30):
        rows.append(
            {
                "transaction_date": pd.Timestamp("2024-01-01") + pd.DateOffset(days=i),
                "station_code": "A18",
                "building_type": "住宅大樓",
                "target_unit_price_twd": 700_000 + i * 500,
            }
        )
    for i in range(30):
        rows.append(
            {
                "transaction_date": pd.Timestamp("2024-01-01") + pd.DateOffset(days=i),
                "station_code": "A18",
                "building_type": "華廈",
                "target_unit_price_twd": 450_000 + i * 500,
            }
        )
    return pd.DataFrame(rows)


def test_recent_median_falls_back_from_group_to_station_then_global(fallback_frame):
    baseline = RecentMedianBaseline(months=24).fit(fallback_frame)
    a17_median = fallback_frame.loc[
        fallback_frame.station_code.eq("A17"), "target_unit_price_twd"
    ].median()
    global_median = fallback_frame["target_unit_price_twd"].median()
    group_median = fallback_frame.loc[
        (fallback_frame.station_code == "A17") & (fallback_frame.building_type == "住宅大樓"),
        "target_unit_price_twd",
    ].median()

    test_unseen_type = pd.DataFrame({"station_code": ["A17"], "building_type": ["辦公大樓"]})
    assert baseline.predict(test_unseen_type)[0] == pytest.approx(a17_median)

    test_unknown_station = pd.DataFrame({"station_code": ["A99"], "building_type": ["住宅大樓"]})
    assert baseline.predict(test_unknown_station)[0] == pytest.approx(global_median)

    test_known_group = pd.DataFrame({"station_code": ["A17"], "building_type": ["住宅大樓"]})
    assert baseline.predict(test_known_group)[0] == pytest.approx(group_median)


def test_metrics_include_overall_station_and_building_type_rows():
    np.random.seed(42)
    n = 100
    frame = pd.DataFrame(
        {
            "station_code": ["A17"] * 40 + ["A18"] * 35 + ["A19"] * 25,
            "building_type": ["住宅大樓"] * 50 + ["華廈"] * 50,
        }
    )
    actual = np.random.uniform(400_000, 800_000, n)
    predicted = actual + np.random.normal(0, 30_000, n)

    rows = metric_rows(actual, predicted, frame)
    assert {"overall", "station:A17", "building_type:住宅大樓"} <= set(rows.index)
    assert {"mae", "mape", "rmse", "r2", "count"} <= set(rows.columns)


def test_metrics_excludes_small_groups():
    frame = pd.DataFrame(
        {
            "station_code": ["A99"] * 20,
            "building_type": ["透天厝"] * 20,
        }
    )
    actual = np.random.uniform(400_000, 800_000, 20)
    predicted = actual + np.random.normal(0, 30_000, 20)
    rows = metric_rows(actual, predicted, frame)
    assert "station:A99" not in rows.index
    assert "building_type:透天厝" not in rows.index


def test_leakage_audit_detects_no_overlap():
    train = pd.DataFrame(
        {
            "transaction_key": ["a", "b"],
            "road_key": ["r1", "r2"],
            "target_unit_price_twd": [1.0, 2.0],
        }
    )
    test = pd.DataFrame(
        {
            "transaction_key": ["c", "d"],
            "road_key": ["r3", "r4"],
            "target_unit_price_twd": [3.0, 4.0],
        }
    )
    cal = pd.DataFrame(
        {"transaction_key": ["e"], "road_key": ["r5"], "target_unit_price_twd": [5.0]}
    )
    split = TimeSplit(train=train, calibration=cal, test=test)
    audit = leakage_audit(split)
    assert audit["target_in_features"] == ("target_unit_price_twd" in FEATURE_COLUMNS)
    assert not audit["transaction_key_overlap"]
    assert audit["road_group_overlap_count"] == 0


def test_leakage_audit_detects_overlaps():
    train = pd.DataFrame(
        {
            "transaction_key": ["a", "b"],
            "road_key": ["r1", "r2"],
            "target_unit_price_twd": [1.0, 2.0],
        }
    )
    test = pd.DataFrame(
        {
            "transaction_key": ["a", "c"],
            "road_key": ["r1", "r3"],
            "target_unit_price_twd": [3.0, 4.0],
        }
    )
    cal = pd.DataFrame(
        {"transaction_key": ["d"], "road_key": ["r4"], "target_unit_price_twd": [5.0]}
    )
    split = TimeSplit(train=train, calibration=cal, test=test)
    audit = leakage_audit(split)
    assert audit["transaction_key_overlap"]
    assert audit["road_group_overlap_count"] == 1


def test_evaluate_candidate_returns_evaluation(model_frame):
    from sklearn.dummy import DummyRegressor

    split = split_by_time(model_frame, test_months=12, calibration_months=6)
    estimator = DummyRegressor(strategy="mean")
    result = evaluate_candidate("dummy_mean", estimator, split.train, split.test)
    assert result.name == "dummy_mean"
    assert result.estimator is estimator
    assert isinstance(result.overall_mae, float)
    assert isinstance(result.station_mape, dict)
    assert isinstance(result.metrics, pd.DataFrame)
    assert "overall" in result.metrics.index


def test_candidate_inventory_contains_required_models():
    estimators = candidate_estimators(seed=42)
    assert set(estimators) == {"ridge", "random_forest", "hist_gradient_boosting"}


def test_preprocessor_keeps_intentionally_empty_presale_age(model_frame):
    frame = model_frame.copy()
    frame["building_age_years"] = np.nan
    with warnings.catch_warnings(record=True) as caught:
        make_preprocessor().fit(frame[list(FEATURE_COLUMNS)])
    assert not any("Skipping features" in str(item.message) for item in caught)


def test_release_gate_requires_overall_improvement_and_station_stability():
    baseline = CandidateEvaluation(
        name="baseline",
        estimator=None,
        overall_mae=100_000.0,
        station_mape={"A17": 5.0, "A18": 6.0, "A19": 7.0},
        metrics=pd.DataFrame(),
    )
    ridge = CandidateEvaluation(
        name="ridge",
        estimator=None,
        overall_mae=90_000.0,
        station_mape={"A17": 4.5, "A18": 5.5, "A19": 6.5},
        metrics=pd.DataFrame(),
    )
    rf = CandidateEvaluation(
        name="random_forest",
        estimator=None,
        overall_mae=95_000.0,
        station_mape={"A17": 5.0, "A18": 5.8, "A19": 6.8},
        metrics=pd.DataFrame(),
    )
    hgb = CandidateEvaluation(
        name="hist_gradient_boosting",
        estimator=None,
        overall_mae=92_000.0,
        station_mape={"A17": 4.8, "A18": 5.9, "A19": 6.9},
        metrics=pd.DataFrame(),
    )
    results_with_ridge_win = [baseline, ridge, rf, hgb]

    ridge_bad = CandidateEvaluation(
        name="ridge",
        estimator=None,
        overall_mae=97_000.0,
        station_mape={"A17": 4.5, "A18": 6.7, "A19": 6.5},
        metrics=pd.DataFrame(),
    )
    rf_bad = CandidateEvaluation(
        name="random_forest",
        estimator=None,
        overall_mae=85_000.0,
        station_mape={"A17": 4.0, "A18": 6.8, "A19": 6.0},
        metrics=pd.DataFrame(),
    )
    hgb_bad = CandidateEvaluation(
        name="hist_gradient_boosting",
        estimator=None,
        overall_mae=99_000.0,
        station_mape={"A17": 4.9, "A18": 6.5, "A19": 6.9},
        metrics=pd.DataFrame(),
    )
    results_with_station_regression = [baseline, ridge_bad, rf_bad, hgb_bad]

    assert select_release_candidate(results_with_ridge_win).name == "ridge"
    assert select_release_candidate(results_with_station_regression).name == "baseline"


def test_release_gate_ignores_unpublished_small_station_metrics():
    baseline = CandidateEvaluation(
        name="baseline",
        estimator=None,
        overall_mae=100_000.0,
        station_mape={"A17": 5.0, "A19": 7.0},
        metrics=pd.DataFrame(),
    )
    candidate = CandidateEvaluation(
        name="ridge",
        estimator=None,
        overall_mae=90_000.0,
        station_mape={"A17": 4.5, "A19": 6.5},
        metrics=pd.DataFrame(),
    )
    assert select_release_candidate([baseline, candidate]).name == "ridge"


class ConstantEstimator:
    def __init__(self, value: float) -> None:
        self.value = value

    def fit(self, X, y, **kwargs):
        return self

    def predict(self, X):
        return np.full(len(X), self.value)


class FailingRegressor(BaseEstimator):
    def fit(self, X, y=None):
        raise RuntimeError("fit failed")

    def predict(self, X):
        return np.zeros(len(X))


def test_experiment_selects_on_calibration_before_reading_final_test(
    model_frame: pd.DataFrame,
) -> None:
    from sklearn.dummy import DummyRegressor

    split = split_by_time(model_frame)
    estimators = {
        "low_quantile": DummyRegressor(strategy="quantile", quantile=0.25),
        "high_quantile": DummyRegressor(strategy="quantile", quantile=0.75),
    }
    first = run_model_experiment(split, estimators)

    hostile_test = split.test.copy()
    hostile_test["target_unit_price_twd"] = 2_000_000.0
    second = run_model_experiment(
        TimeSplit(split.train, split.calibration, hostile_test),
        {
            "low_quantile": DummyRegressor(strategy="quantile", quantile=0.25),
            "high_quantile": DummyRegressor(strategy="quantile", quantile=0.75),
        },
    )

    assert second.selected_name == first.selected_name
    assert set(second.final_test_results) == {"baseline", first.selected_name}


def test_experiment_candidate_failure_is_isolated(model_frame):
    from sklearn.dummy import DummyRegressor

    split = split_by_time(model_frame)
    estimators = {
        "broken": FailingRegressor(),
        "ok": DummyRegressor(strategy="mean"),
    }
    result = run_model_experiment(split, estimators)
    assert result.selected_name != "broken"
    assert result.candidate_errors == {"broken": "candidate_failed"}
    assert "broken" not in result.final_test_results


def test_experiment_baseline_failure_raises_error():
    from sklearn.dummy import DummyRegressor

    n = 150
    train = pd.DataFrame(
        {
            "transaction_date": [pd.Timestamp("2024-01-01")] * n,
            "station_code": ["A17"] * n,
            "building_type": ["住宅大樓"] * n,
            "target_unit_price_twd": [600_000.0] * n,
        }
    )
    split = TimeSplit(train=train, calibration=train, test=train)
    with pytest.raises(BaselineEvaluationError):
        run_model_experiment(split, {"dummy": DummyRegressor()})


def test_experiment_final_gate_returns_recommended_false_when_candidate_fails():
    np.random.seed(42)
    base = pd.Timestamp("2022-01-01")
    total_days = 1460
    n = 800
    dates = [base + pd.DateOffset(days=int(i * total_days / n)) for i in range(n)]

    rows = []
    for i in range(n):
        frac = i / n
        if frac < 0.6:
            target = 600_000.0
        elif frac < 0.75:
            target = 550_000.0
        else:
            target = 2_000_000.0
        dt = dates[i]
        rows.append(
            {
                "transaction_date": dt,
                "station_code": "A17",
                "station_distance_m": 500.0,
                "building_area_ping": 35.0,
                "building_type": "住宅大樓",
                "bedrooms": 3,
                "living_rooms": 2,
                "bathrooms": 2,
                "building_age_years": 10.0,
                "floor": 8,
                "total_floors": 15,
                "floor_ratio": 0.53,
                "parking_type": "",
                "parking_area_ping": 0.0,
                "transaction_year": dt.year,
                "transaction_month": dt.month,
                "target_unit_price_twd": target,
                "transaction_key": f"T{i}",
                "road_key": f"R{i % 10}",
            }
        )
    frame = pd.DataFrame(rows)
    split = split_by_time(frame)

    from sklearn.dummy import DummyRegressor

    estimators = {
        "cheater": DummyRegressor(strategy="constant", constant=550_000),
    }
    result = run_model_experiment(split, estimators)
    assert result.selected_name == "cheater"
    assert not result.recommended
    assert "final_gate_failed" in result.reason_codes


def test_experiment_no_candidate_beats_baseline_returns_baseline_selected(model_frame):
    from sklearn.dummy import DummyRegressor

    split = split_by_time(model_frame)
    estimators = {
        "terrible": DummyRegressor(strategy="constant", constant=1),
    }
    result = run_model_experiment(split, estimators)
    assert result.selected_name == "baseline"
    assert not result.recommended
    assert "baseline_selected" in result.reason_codes
    assert set(result.final_test_results) == {"baseline"}


def test_candidate_estimators_use_profile_parameters() -> None:
    profile = TrainingProfile("custom", "custom", 0.03, 444, 555, 30)
    estimators = candidate_estimators(seed=42, profile=profile)
    hgb = estimators["hist_gradient_boosting"].named_steps["model"]
    forest = estimators["random_forest"].named_steps["model"]
    assert hgb.learning_rate == 0.03
    assert hgb.max_iter == 444
    assert forest.n_estimators == 555
    assert len({
        id(pipeline.named_steps["features"])
        for pipeline in estimators.values()
    }) == 3


def test_evaluate_candidate_uses_requested_half_life(
    model_frame, monkeypatch,
) -> None:
    captured = {}
    original = model_training.recency_weights

    def capture(frame, reference_date=None, half_life_months=48, minimum=0.10):
        captured["half_life"] = half_life_months
        return original(frame, reference_date, half_life_months, minimum)

    monkeypatch.setattr(model_training, "recency_weights", capture)
    split = split_by_time(model_frame)
    evaluate_candidate(
        "ridge",
        candidate_estimators()["ridge"],
        split.train,
        split.calibration,
        use_recency_weights=True,
        recency_half_life_months=30,
    )
    assert captured["half_life"] == 30


def test_tuned_model_experiment_selects_on_calibration(monkeypatch) -> None:
    n = 800
    np.random.seed(42)
    base = pd.Timestamp("2022-01-01")
    dates = [base + pd.DateOffset(days=int(i * 1095 / n)) for i in range(n)]
    day_offsets = [int(i * 1095 / n) for i in range(n)]

    targets = []
    for d in day_offsets:
        if d < 546:
            targets.append(600_000.0)
        elif d < 730:
            targets.append(610_000.0)
        else:
            targets.append(590_000.0)

    frame = pd.DataFrame({
        "transaction_date": dates,
        "station_code": ["A17"] * n,
        "station_distance_m": 500.0,
        "building_area_ping": 35.0,
        "building_type": ["住宅大樓"] * n,
        "bedrooms": 3,
        "living_rooms": 2,
        "bathrooms": 2,
        "building_age_years": 10.0,
        "floor": 8,
        "total_floors": 15,
        "floor_ratio": 0.53,
        "parking_type": [""] * n,
        "parking_area_ping": 0.0,
        "transaction_year": [d.year for d in dates],
        "transaction_month": [d.month for d in dates],
        "target_unit_price_twd": targets,
        "transaction_key": [f"T{i}" for i in range(n)],
        "road_key": [f"R{i % 10}" for i in range(n)],
    })
    split = split_by_time(frame, test_months=12, calibration_months=6)

    profile_specific = {
        "quick": {
            "ridge": ConstantEstimator(610_000.0),
            "random_forest": ConstantEstimator(500_000.0),
            "hist_gradient_boosting": ConstantEstimator(550_000.0),
        },
        "thorough": {
            "ridge": ConstantEstimator(600_000.0),
            "random_forest": ConstantEstimator(500_000.0),
            "hist_gradient_boosting": ConstantEstimator(550_000.0),
        },
    }

    def mock_candidate_estimators(seed=42, profile=None):
        return profile_specific[profile.name]

    monkeypatch.setattr(model_training, "candidate_estimators", mock_candidate_estimators)

    experiment = run_tuned_model_experiment(
        split,
        profiles=(PRESET_PROFILES[0], PRESET_PROFILES[2]),
        feature_columns=("building_area_ping",),
        use_recency_weights=False,
    )
    assert experiment.selected_profile == "quick"
    assert experiment.selected_model == "ridge"
    assert set(experiment.final_test_results) == {"baseline", "ridge"}


def test_tuned_model_tie_breaking(monkeypatch) -> None:
    n = 800
    np.random.seed(42)
    base = pd.Timestamp("2022-01-01")
    dates = [base + pd.DateOffset(days=int(i * 1095 / n)) for i in range(n)]
    targets = [600_000.0 if i < n // 2 else 400_000.0 for i in range(n)]

    frame = pd.DataFrame({
        "transaction_date": dates,
        "station_code": ["A17"] * n,
        "station_distance_m": 500.0,
        "building_area_ping": 35.0,
        "building_type": ["住宅大樓"] * n,
        "bedrooms": 3,
        "living_rooms": 2,
        "bathrooms": 2,
        "building_age_years": 10.0,
        "floor": 8,
        "total_floors": 15,
        "floor_ratio": 0.53,
        "parking_type": [""] * n,
        "parking_area_ping": 0.0,
        "transaction_year": [d.year for d in dates],
        "transaction_month": [d.month for d in dates],
        "target_unit_price_twd": targets,
        "transaction_key": [f"T{i}" for i in range(n)],
        "road_key": [f"R{i % 10}" for i in range(n)],
    })
    split = split_by_time(frame)

    profile_specific = {
        "quick": {
            "ridge": ConstantEstimator(500_000.0),
            "random_forest": ConstantEstimator(600_000.0),
        },
        "balanced": {
            "ridge": ConstantEstimator(500_000.0),
        },
    }

    def mock_candidate_estimators(seed=42, profile=None):
        return profile_specific[profile.name]

    monkeypatch.setattr(model_training, "candidate_estimators", mock_candidate_estimators)

    experiment = run_tuned_model_experiment(
        split,
        profiles=(PRESET_PROFILES[0], PRESET_PROFILES[1]),
        feature_columns=("building_area_ping",),
        use_recency_weights=False,
    )
    assert experiment.selected_profile == "quick"
    assert experiment.selected_model == "ridge"


def test_tuned_model_profile_failure(monkeypatch) -> None:
    n = 800
    np.random.seed(42)
    base = pd.Timestamp("2022-01-01")
    dates = [base + pd.DateOffset(days=int(i * 1095 / n)) for i in range(n)]
    targets = [600_000.0] * n

    frame = pd.DataFrame({
        "transaction_date": dates,
        "station_code": ["A17"] * n,
        "station_distance_m": 500.0,
        "building_area_ping": 35.0,
        "building_type": ["住宅大樓"] * n,
        "bedrooms": 3,
        "living_rooms": 2,
        "bathrooms": 2,
        "building_age_years": 10.0,
        "floor": 8,
        "total_floors": 15,
        "floor_ratio": 0.53,
        "parking_type": [""] * n,
        "parking_area_ping": 0.0,
        "transaction_year": [d.year for d in dates],
        "transaction_month": [d.month for d in dates],
        "target_unit_price_twd": targets,
        "transaction_key": [f"T{i}" for i in range(n)],
        "road_key": [f"R{i % 10}" for i in range(n)],
    })
    split = split_by_time(frame)

    profile_specific = {
        "quick": {
            "ridge": ConstantEstimator(600_000.0),
        },
    }

    def mock_candidate_estimators(seed=42, profile=None):
        if profile.name == "balanced":
            return {
                "broken": FailingRegressor(),
                "also_broken": FailingRegressor(),
            }
        return profile_specific[profile.name]

    monkeypatch.setattr(model_training, "candidate_estimators", mock_candidate_estimators)

    with pytest.raises(ProfileEvaluationError) as exc:
        run_tuned_model_experiment(
            split,
            profiles=(PRESET_PROFILES[0], PRESET_PROFILES[1]),
            feature_columns=("building_area_ping",),
            use_recency_weights=False,
        )
    assert exc.value.profile_name == "balanced"
