from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from qingpu_insight.model_features import FEATURE_COLUMNS


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


class RecentMedianBaseline:
    def __init__(self, months: int = 24):
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

    def predict(self, test_frame: pd.DataFrame) -> np.ndarray:
        result = []
        for _, row in test_frame.iterrows():
            key = (row["station_code"], row["building_type"])
            if (
                key in self._group_medians.index
                and self._group_counts.get(key, 0) >= 20
            ):
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


def metric_rows(
    actual: np.ndarray, predicted: np.ndarray, frame: pd.DataFrame
) -> pd.DataFrame:
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


def evaluate_candidate(
    name: str, estimator: Any, split: TimeSplit
) -> CandidateEvaluation:
    estimator.fit(split.train[list(FEATURE_COLUMNS)], split.train["target_unit_price_twd"])
    predicted = estimator.predict(split.test[list(FEATURE_COLUMNS)])
    actual = split.test["target_unit_price_twd"].values
    metrics = metric_rows(actual, predicted, split.test)
    overall_mae = float(metrics.loc["overall", "mae"])
    station_mape = {
        idx.split(":", 1)[1]: float(row["mape"])
        for idx, row in metrics.iterrows()
        if idx.startswith("station:")
    }
    return CandidateEvaluation(
        name=name, estimator=estimator, overall_mae=overall_mae, station_mape=station_mape, metrics=metrics
    )


NUMERIC_FEATURES = [
    "station_distance_m", "building_area_ping", "bedrooms", "living_rooms",
    "bathrooms", "building_age_years", "floor", "total_floors", "floor_ratio",
    "parking_area_ping", "transaction_year", "transaction_month",
]
CATEGORICAL_FEATURES = ["station_code", "building_type", "parking_type"]


def make_preprocessor() -> ColumnTransformer:
    return ColumnTransformer([
        ("numeric", Pipeline([
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
        ]), NUMERIC_FEATURES),
        ("categorical", Pipeline([
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]), CATEGORICAL_FEATURES),
    ])


def candidate_estimators(seed: int = 42) -> dict[str, Pipeline]:
    return {
        "ridge": Pipeline([("features", make_preprocessor()), ("model", Ridge(alpha=10.0))]),
        "random_forest": Pipeline([("features", make_preprocessor()), ("model", RandomForestRegressor(
            n_estimators=400, min_samples_leaf=5, max_features=0.8,
            random_state=seed, n_jobs=-1,
        ))]),
        "hist_gradient_boosting": Pipeline([("features", make_preprocessor()), ("model", HistGradientBoostingRegressor(
            learning_rate=0.06, max_iter=350, max_leaf_nodes=31,
            l2_regularization=1.0, random_state=seed,
        ))]),
    }


def select_release_candidate(results: list[CandidateEvaluation]) -> CandidateEvaluation:
    baseline = next(result for result in results if result.name == "baseline")
    eligible = [result for result in results if result.name != "baseline"
        and result.overall_mae <= baseline.overall_mae * 0.98
        and all(result.station_mape[s] <= baseline.station_mape[s] * 1.10
                for s in ("A17", "A18", "A19"))]
    return min(eligible, key=lambda result: result.overall_mae, default=baseline)
