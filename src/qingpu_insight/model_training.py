import inspect
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from qingpu_insight.model_features import FEATURE_COLUMNS


def recency_weights(
    frame: pd.DataFrame,
    reference_date: pd.Timestamp | None = None,
    half_life_months: int = 24,
    minimum: float = 0.10,
) -> np.ndarray:
    latest = pd.Timestamp(reference_date or frame["transaction_date"].max()).normalize()
    dates = pd.to_datetime(frame["transaction_date"])
    ages = ((latest.year - dates.dt.year) * 12 + latest.month - dates.dt.month).clip(lower=0)
    return np.maximum(minimum, np.power(0.5, ages.to_numpy() / half_life_months))


@dataclass(frozen=True)
class TimeSplit:
    train: pd.DataFrame
    calibration: pd.DataFrame
    test: pd.DataFrame


def split_by_time(
    frame: pd.DataFrame, test_months: int = 12, calibration_months: int = 6
) -> TimeSplit:
    maximum = frame["transaction_date"].max().normalize()
    test_start = maximum - pd.DateOffset(months=test_months) + pd.Timedelta(days=1)
    calibration_start = test_start - pd.DateOffset(months=calibration_months)
    split = TimeSplit(
        train=frame.loc[frame.transaction_date < calibration_start].copy(),
        calibration=frame.loc[
            frame.transaction_date.between(calibration_start, test_start, inclusive="left")
        ].copy(),
        test=frame.loc[frame.transaction_date >= test_start].copy(),
    )
    if min(map(len, (split.train, split.calibration, split.test))) < 100:
        raise ValueError("train, calibration, and test must each contain at least 100 rows")
    return split


class RecentMedianBaseline(BaseEstimator):
    def __init__(self, months: int = 12):
        self.months = months
        self._group_medians: pd.Series | None = None
        self._group_counts: pd.Series | None = None
        self._station_medians: pd.Series | None = None
        self._station_counts: pd.Series | None = None
        self._global_median: float | None = None

    def fit(self, train_frame: pd.DataFrame) -> "RecentMedianBaseline":
        cutoff = train_frame["transaction_date"].max().normalize() - pd.DateOffset(
            months=self.months
        )
        recent = train_frame.loc[train_frame.transaction_date >= cutoff]

        groups = recent.groupby(["station_code", "building_type"])["target_unit_price_twd"]
        self._group_medians = groups.median()
        self._group_counts = groups.count()

        stations = recent.groupby("station_code")["target_unit_price_twd"]
        self._station_medians = stations.median()
        self._station_counts = stations.count()

        self._global_median = recent["target_unit_price_twd"].median()

        return self

    def predict(self, X) -> np.ndarray:
        if isinstance(X, np.ndarray):
            X = pd.DataFrame(X, columns=list(FEATURE_COLUMNS))
        result = []
        for _, row in X.iterrows():
            key = (row["station_code"], row["building_type"])
            if key in self._group_medians.index and self._group_counts.get(key, 0) >= 20:
                result.append(self._group_medians[key])
            elif (
                row["station_code"] in self._station_medians.index
                and self._station_counts.get(row["station_code"], 0) >= 20
            ):
                result.append(self._station_medians[row["station_code"]])
            else:
                result.append(self._global_median)
        return np.array(result)


def _compute_metrics(actual: np.ndarray, predicted: np.ndarray, mask: np.ndarray) -> dict:
    y = actual[mask]
    y_pred = predicted[mask]
    count = len(y)
    ae = np.abs(y - y_pred)
    se = (y - y_pred) ** 2
    mae = float(np.mean(ae))
    denom = np.maximum(np.abs(y), 100_000)
    mape = float(np.mean(ae / denom) * 100)
    rmse = float(np.sqrt(np.mean(se)))
    ss_res = float(np.sum(se))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {"mae": mae, "mape": mape, "rmse": rmse, "r2": r2, "count": count}


def metric_rows(actual: np.ndarray, predicted: np.ndarray, frame: pd.DataFrame) -> pd.DataFrame:
    n = len(actual)
    rows = {}
    rows["overall"] = _compute_metrics(actual, predicted, np.ones(n, dtype=bool))
    for station in frame["station_code"].unique():
        mask = (frame["station_code"] == station).values
        if mask.sum() >= 30:
            rows[f"station:{station}"] = _compute_metrics(actual, predicted, mask)
    for bt in frame["building_type"].unique():
        mask = (frame["building_type"] == bt).values
        if mask.sum() >= 30:
            rows[f"building_type:{bt}"] = _compute_metrics(actual, predicted, mask)
    return pd.DataFrame(rows).T


def leakage_audit(split: TimeSplit) -> dict[str, object]:
    road_train = set(split.train.get("road_key", pd.Series(dtype=object)).dropna())
    road_test = set(split.test.get("road_key", pd.Series(dtype=object)).dropna())
    tx_train = set(split.train.get("transaction_key", pd.Series(dtype=object)))
    tx_test = set(split.test.get("transaction_key", pd.Series(dtype=object)))
    return {
        "target_in_features": "target_unit_price_twd" in FEATURE_COLUMNS,
        "transaction_key_overlap": bool(tx_train & tx_test),
        "road_group_overlap_count": len(road_train & road_test),
    }


@dataclass(frozen=True)
class CandidateEvaluation:
    name: str
    estimator: Any
    overall_mae: float
    station_mape: dict[str, float]
    metrics: pd.DataFrame


@dataclass(frozen=True)
class ModelExperiment:
    selection_results: tuple[CandidateEvaluation, ...]
    selected_name: str
    selected_estimator: Any
    final_test_results: dict[str, CandidateEvaluation]
    candidate_errors: dict[str, str]
    recommended: bool
    reason_codes: tuple[str, ...]


class BaselineEvaluationError(Exception):
    pass


def _fit_candidate(est, X, y, sample_weight=None):
    if sample_weight is None:
        est.fit(X, y)
    elif isinstance(est, Pipeline):
        est.fit(X, y, model__sample_weight=sample_weight)
    elif "sample_weight" in inspect.signature(est.fit).parameters:
        est.fit(X, y, sample_weight=sample_weight)
    else:
        est.fit(X, y)


def evaluate_candidate(
    name: str,
    estimator: Any,
    train_frame: pd.DataFrame,
    evaluation_frame: pd.DataFrame,
    feature_columns=FEATURE_COLUMNS,
) -> CandidateEvaluation:
    estimator.fit(
        train_frame[list(feature_columns)],
        train_frame["target_unit_price_twd"],
    )
    return evaluate_fitted_candidate(name, estimator, evaluation_frame, feature_columns=feature_columns)


def evaluate_fitted_candidate(
    name: str,
    estimator: Any,
    evaluation_frame: pd.DataFrame,
    feature_columns=FEATURE_COLUMNS,
) -> CandidateEvaluation:
    predicted = estimator.predict(evaluation_frame[list(feature_columns)])
    actual = evaluation_frame["target_unit_price_twd"].to_numpy()
    metrics = metric_rows(actual, predicted, evaluation_frame)
    return CandidateEvaluation(
        name=name,
        estimator=estimator,
        overall_mae=float(metrics.loc["overall", "mae"]),
        station_mape={
            index.split(":", 1)[1]: float(row["mape"])
            for index, row in metrics.iterrows()
            if index.startswith("station:")
        },
        metrics=metrics,
    )


NUMERIC_FEATURES = [
    "station_distance_m",
    "building_area_ping",
    "bedrooms",
    "living_rooms",
    "bathrooms",
    "building_age_years",
    "floor",
    "total_floors",
    "floor_ratio",
    "parking_area_ping",
    "transaction_year",
    "transaction_month",
]
CATEGORICAL_FEATURES = ["station_code", "building_type", "parking_type"]


def make_preprocessor(feature_columns=FEATURE_COLUMNS) -> ColumnTransformer:
    num = [c for c in NUMERIC_FEATURES if c in feature_columns]
    cat = [c for c in CATEGORICAL_FEATURES if c in feature_columns]
    return ColumnTransformer(
        [
            (
                "numeric",
                Pipeline(
                    [
                        (
                            "impute",
                            SimpleImputer(
                                strategy="median",
                                add_indicator=True,
                                keep_empty_features=True,
                            ),
                        ),
                        ("scale", StandardScaler()),
                    ]
                ),
                num,
            ),
            (
                "categorical",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
                    ]
                ),
                cat,
            ),
        ]
    )


def candidate_estimators(feature_columns=FEATURE_COLUMNS, seed: int = 42) -> dict[str, Pipeline]:
    prep = make_preprocessor(feature_columns)
    return {
        "ridge": Pipeline([("features", prep), ("model", Ridge(alpha=10.0))]),
        "random_forest": Pipeline(
            [
                ("features", prep),
                (
                    "model",
                    RandomForestRegressor(
                        n_estimators=400,
                        min_samples_leaf=5,
                        max_features=0.8,
                        random_state=seed,
                        n_jobs=-1,
                    ),
                ),
            ]
        ),
        "hist_gradient_boosting": Pipeline(
            [
                ("features", prep),
                (
                    "model",
                    HistGradientBoostingRegressor(
                        learning_rate=0.06,
                        max_iter=350,
                        max_leaf_nodes=31,
                        l2_regularization=1.0,
                        random_state=seed,
                    ),
                ),
            ]
        ),
    }


def passes_release_gate(
    candidate: CandidateEvaluation,
    baseline: CandidateEvaluation,
) -> bool:
    published_stations = set(baseline.station_mape)
    return (
        candidate.name != "baseline"
        and candidate.overall_mae <= baseline.overall_mae * 0.98
        and published_stations <= set(candidate.station_mape)
        and all(
            candidate.station_mape[station] <= baseline.station_mape[station] * 1.10
            for station in published_stations
        )
    )


def select_release_candidate(results: list[CandidateEvaluation]) -> CandidateEvaluation:
    baseline = next(result for result in results if result.name == "baseline")
    eligible = [r for r in results if passes_release_gate(r, baseline)]
    return min(eligible, key=lambda r: r.overall_mae, default=baseline)


def run_model_experiment(
    split: TimeSplit,
    estimators: dict[str, Any] | None = None,
    feature_columns=FEATURE_COLUMNS,
    use_recency_weights: bool = True,
) -> ModelExperiment:
    if estimators is None:
        estimators = candidate_estimators(feature_columns=feature_columns)

    baseline = RecentMedianBaseline()

    try:
        baseline.fit(split.train)
        baseline_cal = evaluate_fitted_candidate("baseline", baseline, split.calibration, feature_columns=feature_columns)
    except Exception as exc:
        raise BaselineEvaluationError("baseline evaluation failed") from exc

    calibration_results: list[CandidateEvaluation] = [baseline_cal]
    candidate_errors: dict[str, str] = {}

    for name, est in estimators.items():
        try:
            w = recency_weights(split.train) if use_recency_weights else None
            _fit_candidate(
                est,
                split.train[list(feature_columns)],
                split.train["target_unit_price_twd"],
                sample_weight=w,
            )
            result = evaluate_fitted_candidate(name, est, split.calibration, feature_columns=feature_columns)
            calibration_results.append(result)
        except Exception:
            candidate_errors[name] = "candidate_failed"

    selected = select_release_candidate(calibration_results)
    selected_name = selected.name
    selected_estimator = selected.estimator

    final_test_results: dict[str, CandidateEvaluation] = {}
    final_baseline = evaluate_fitted_candidate("baseline", baseline, split.test, feature_columns=feature_columns)
    final_test_results["baseline"] = final_baseline

    if selected_name != "baseline":
        final_selected = evaluate_fitted_candidate(selected_name, selected_estimator, split.test, feature_columns=feature_columns)
        final_test_results[selected_name] = final_selected
    else:
        final_selected = final_baseline

    if selected_name == "baseline":
        recommended = False
        reason_codes: tuple[str, ...] = ("baseline_selected",)
    elif passes_release_gate(final_selected, final_baseline):
        recommended = True
        reason_codes = ()
    else:
        recommended = False
        reason_codes = ("final_gate_failed",)

    return ModelExperiment(
        selection_results=tuple(calibration_results),
        selected_name=selected_name,
        selected_estimator=selected_estimator,
        final_test_results=final_test_results,
        candidate_errors=candidate_errors,
        recommended=recommended,
        reason_codes=reason_codes,
    )
