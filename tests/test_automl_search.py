import numpy as np
import pandas as pd
import pytest
from sklearn.base import BaseEstimator

import qingpu_insight.model_analysis as model_analysis
from qingpu_insight.automl_search import (
    AutoMLTrialResult,
    json_safe,
    rank_trials,
    run_automl_search,
    shortlist_trials,
)
from qingpu_insight.model_analysis import run_annual_backtests
from qingpu_insight.model_features import FEATURE_COLUMNS, add_derived_features
from qingpu_insight.model_training import (
    CandidateEvaluation,
    ModelExperiment,
    ModelFitSpec,
    build_estimator,
    evaluate_fit_spec,
    split_by_time,
)
from qingpu_insight.model_tuning import AutoMLBudget, AutoMLTuningPlan


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


def test_evaluate_fit_spec_returns_experiment_with_correct_selected_name(resale_frame) -> None:
    split = split_by_time(resale_frame)
    spec = ModelFitSpec(
        model_name="random_forest",
        parameters={"n_estimators": 50, "min_samples_leaf": 10, "max_features": 0.8},
        recency_half_life_months=None,
    )
    experiment = evaluate_fit_spec(split, spec)
    assert isinstance(experiment, ModelExperiment)
    assert experiment.selected_name == "random_forest"


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


# ---------------------------------------------------------------------------
# Task 3 — Optuna Search, Ranking, and Progress
# ---------------------------------------------------------------------------


def _eval_metrics(mae: float, mape: float) -> pd.DataFrame:
    return pd.DataFrame(
        {"mae": [mae], "mape": [mape], "rmse": [mae], "r2": [0.5], "count": [100]},
        index=["overall"],
    )


def _passing_evaluation(spec: ModelFitSpec) -> ModelExperiment:
    cal_eval = CandidateEvaluation(
        name=spec.model_name,
        estimator=None,
        overall_mae=40_000.0,
        station_mape={"A17": 8.0},
        metrics=_eval_metrics(40_000.0, 8.0),
    )
    return ModelExperiment(
        selection_results=(cal_eval,),
        selected_name=spec.model_name,
        selected_estimator=None,
        final_test_results={},
        candidate_errors={},
        recommended=True,
        reason_codes=(),
    )


def trial_result(
    trial_number: int,
    mae: float | None = None,
    calibration_passed: bool = True,
    mape: float | None = 5.0,
    state: str = "completed",
    fit_spec: ModelFitSpec | None = None,
) -> AutoMLTrialResult:
    if fit_spec is None:
        fit_spec = ModelFitSpec(
            "random_forest",
            {"n_estimators": 100, "min_samples_leaf": 5, "max_features": 0.8},
            None,
        )
    station_mape_val: dict[str, float] = {"A17": mape} if mape is not None else {}
    return AutoMLTrialResult(
        trial_number=trial_number,
        state=state,
        fit_spec=fit_spec,
        estimator=None,
        metrics={},
        overall_mae=mae,
        overall_mape=mape,
        station_mape=station_mape_val,
        calibration_passed=calibration_passed,
        reason_codes=(),
        duration_seconds=1.0,
    )


@pytest.fixture
def fake_split():
    cols = list(FEATURE_COLUMNS)
    dates = pd.date_range("2020-01-01", periods=1500, freq="D")
    df = pd.DataFrame(index=range(1500))
    df["transaction_date"] = dates[:1500]
    df["target_unit_price_twd"] = 500_000.0
    for col in cols:
        df[col] = 0
    df["station_code"] = "A17"
    df["building_type"] = "住宅大樓"
    return split_by_time(df)


def quick_plan() -> AutoMLTuningPlan:
    return AutoMLTuningPlan(
        version=2, mode="automl", budget=AutoMLBudget("quick", 60, 12)
    )


def test_json_safe_converts_numpy_scalars() -> None:
    assert json_safe(np.int64(42)) == 42
    assert json_safe(np.float64(3.14)) == 3.14
    assert json_safe(np.bool_(True)) is True
    assert json_safe(np.array([1, 2, 3])) == [1, 2, 3]
    nested = {"a": np.float64(1.5), "b": [np.int64(10)]}
    assert json_safe(nested) == {"a": 1.5, "b": [10]}


def test_ranked_trials_put_gate_passes_before_lower_mae_failures() -> None:
    failed = trial_result(1, mae=40_000, calibration_passed=False)
    passed = trial_result(2, mae=45_000, calibration_passed=True)
    result = rank_trials((failed, passed))
    assert [row.trial_number for row in result] == [2, 1]


def test_failed_trial_does_not_abort_study(fake_split) -> None:
    calls = 0

    def evaluator(spec):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic failure")
        return _passing_evaluation(spec)

    result = run_automl_search(
        fake_split,
        quick_plan(),
        FEATURE_COLUMNS,
        True,
        12,
        should_stop=lambda: False,
        on_progress=lambda _: None,
        trial_evaluator=evaluator,
    )
    assert result.failed_trials == 1
    assert result.completed_trials >= 1


def test_shortlist_max_three_distinct() -> None:
    spec_a = ModelFitSpec("random_forest", {"n_estimators": 100}, None)
    spec_b = ModelFitSpec("random_forest", {"n_estimators": 200}, None)
    spec_c = ModelFitSpec("random_forest", {"n_estimators": 300}, None)
    spec_d = ModelFitSpec("random_forest", {"n_estimators": 400}, None)
    trials = [
        trial_result(1, mae=50_000, fit_spec=spec_a),
        trial_result(2, mae=51_000, fit_spec=spec_b),
        trial_result(3, mae=52_000, fit_spec=spec_c),
        trial_result(4, mae=53_000, fit_spec=spec_d),
    ]
    ranked = rank_trials(tuple(trials))
    shortlisted = shortlist_trials(ranked)
    assert len(shortlisted) == 3
    assert shortlisted[0].trial_number == 1
    assert shortlisted[1].trial_number == 2
    assert shortlisted[2].trial_number == 3


def test_search_respects_max_trials(fake_split) -> None:
    called = 0

    def evaluator(spec):
        nonlocal called
        called += 1
        return _passing_evaluation(spec)

    limited = AutoMLTuningPlan(
        version=2, mode="automl", budget=AutoMLBudget("quick", 60, 3)
    )
    result = run_automl_search(
        fake_split,
        limited,
        FEATURE_COLUMNS,
        True,
        12,
        should_stop=lambda: False,
        on_progress=lambda _: None,
        trial_evaluator=evaluator,
    )
    assert len(result.trials) == 3
    assert result.completed_trials == 3


def test_snapshot_omits_estimator() -> None:
    result = AutoMLTrialResult(
        trial_number=1,
        state="completed",
        fit_spec=None,
        estimator=DummyEstimator(),
        metrics={},
        overall_mae=40_000.0,
        overall_mape=5.0,
        station_mape={"A17": 5.0},
        calibration_passed=True,
        reason_codes=(),
        duration_seconds=1.5,
    )
    snap = result.snapshot()
    assert "estimator" not in snap
    assert snap["trial_number"] == 1
    assert snap["state"] == "completed"
    assert snap["calibration_passed"] is True
