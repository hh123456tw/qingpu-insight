import calendar
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pandas as pd
from sklearn.base import clone

from qingpu_insight.model_features import BASE_FEATURE_COLUMNS, FEATURE_COLUMNS
from qingpu_insight.model_training import (
    RecentMedianBaseline,
    TimeSplit,
    candidate_estimators,
    evaluate_candidate,
    evaluate_fitted_candidate,
    run_model_experiment,
    split_by_time,
)


@dataclass(frozen=True)
class FeatureExperiment:
    name: str
    feature_columns: tuple[str, ...]
    selected_model: str | None
    metrics: dict[str, Any]
    candidate_errors: dict[str, str]


ABLATIONS = {
    "without_transaction_trend": ("transaction_month_index",),
    "without_station_building_type": ("station_building_type",),
    "without_age_band": ("building_age_band",),
    "without_area_band": ("area_band",),
    "without_floor_band": ("floor_band",),
}


def run_feature_experiments(split: TimeSplit) -> tuple[FeatureExperiment, ...]:
    experiments: list[FeatureExperiment] = []

    base_result = run_model_experiment(split, feature_columns=BASE_FEATURE_COLUMNS)
    base_selected = next(
        c for c in base_result.selection_results if c.name == base_result.selected_name
    )
    experiments.append(
        FeatureExperiment(
            name="base",
            feature_columns=BASE_FEATURE_COLUMNS,
            selected_model=base_result.selected_name,
            metrics=base_selected.metrics.to_dict(orient="index"),
            candidate_errors=base_result.candidate_errors,
        )
    )

    all_candidates = candidate_estimators(feature_columns=FEATURE_COLUMNS)
    enhanced_result = run_model_experiment(
        split,
        feature_columns=FEATURE_COLUMNS,
        estimators=all_candidates,
    )
    learned_results = [
        result for result in enhanced_result.selection_results if result.name != "baseline"
    ]
    if not learned_results:
        raise ValueError("all enhanced learned candidates failed")
    enhanced_selected = min(learned_results, key=lambda result: result.overall_mae)
    enhanced_winner_name = enhanced_selected.name
    experiments.append(
        FeatureExperiment(
            name="enhanced",
            feature_columns=FEATURE_COLUMNS,
            selected_model=enhanced_winner_name,
            metrics=enhanced_selected.metrics.to_dict(orient="index"),
            candidate_errors=enhanced_result.candidate_errors,
        )
    )

    for ab_name, removed in ABLATIONS.items():
        ablation_features = tuple(feature for feature in FEATURE_COLUMNS if feature not in removed)
        ablation_estimator = candidate_estimators(feature_columns=ablation_features)[
            enhanced_winner_name
        ]
        ablation_result = run_model_experiment(
            split,
            feature_columns=ablation_features,
            estimators={enhanced_winner_name: clone(ablation_estimator)},
            locked_candidate_name=enhanced_winner_name,
        )
        ablation_selected = next(
            candidate
            for candidate in ablation_result.selection_results
            if candidate.name == enhanced_winner_name
        )
        experiments.append(
            FeatureExperiment(
                name=ab_name,
                feature_columns=ablation_features,
                selected_model=enhanced_winner_name,
                metrics=ablation_selected.metrics.to_dict(orient="index"),
                candidate_errors=ablation_result.candidate_errors,
            )
        )

    return tuple(experiments)


def build_resale_diagnostics(
    frame: pd.DataFrame,
    split: TimeSplit,
    candidate: Any | None = None,
    feature_columns: tuple[str, ...] = FEATURE_COLUMNS,
) -> dict[str, object]:
    station_counts = frame["station_code"].value_counts().to_dict()
    missing_rates = {
        col: round(float(frame[col].isna().mean()), 6)
        for col in FEATURE_COLUMNS
        if col in frame.columns
    }
    monthly = (
        frame.groupby([frame["transaction_date"].dt.to_period("M"), "station_code"])
        .agg(
            count=("target_unit_price_twd", "count"),
            median_target=("target_unit_price_twd", "median"),
        )
        .reset_index()
    )
    monthly["period"] = monthly["transaction_date"].astype(str)
    monthly = monthly.sort_values(["period", "station_code"])
    monthly_summary = [
        {
            "period": row["period"],
            "station_code": row["station_code"],
            "count": int(row["count"]),
            "median_target": float(row["median_target"]),
        }
        for _, row in monthly.iterrows()
    ]
    bt = (
        frame.groupby(["station_code", "building_type"])
        .agg(
            count=("target_unit_price_twd", "count"),
            median_target=("target_unit_price_twd", "median"),
        )
        .reset_index()
        .sort_values(["station_code", "building_type"])
    )
    building_type_summary = [
        {
            "station_code": row["station_code"],
            "building_type": row["building_type"],
            "count": int(row["count"]),
            "median_target": float(row["median_target"]),
        }
        for _, row in bt.iterrows()
    ]

    def _summarize(df: pd.DataFrame) -> dict[str, Any]:
        n = len(df)
        if n == 0:
            return {
                "row_count": 0,
                "date_min": "",
                "date_max": "",
                "median_target": 0.0,
                "station_proportions": {},
                "building_type_proportions": {},
            }
        return {
            "row_count": n,
            "date_min": str(df["transaction_date"].min().date()),
            "date_max": str(df["transaction_date"].max().date()),
            "median_target": float(df["target_unit_price_twd"].median()),
            "station_proportions": {
                k: float(v)
                for k, v in sorted((df["station_code"].value_counts() / n).to_dict().items())
            },
            "building_type_proportions": {
                k: float(v)
                for k, v in sorted((df["building_type"].value_counts() / n).to_dict().items())
            },
        }

    split_summary = {
        "train": _summarize(split.train),
        "calibration": _summarize(split.calibration),
        "test": _summarize(split.test),
    }
    extreme_masks = {
        "station_distance_m": frame["station_distance_m"].lt(0)
        | frame["station_distance_m"].gt(2_000),
        "building_area_ping": frame["building_area_ping"].lt(5)
        | frame["building_area_ping"].gt(200),
        "building_age_years": frame["building_age_years"].lt(0)
        | frame["building_age_years"].gt(100),
        "floor_ratio": frame["floor_ratio"].lt(0) | frame["floor_ratio"].gt(1),
    }
    feature_extreme_rates = {
        name: round(float(mask.fillna(False).mean()), 6) for name, mask in extreme_masks.items()
    }

    evaluation_error_summary: list[dict[str, object]] = []
    if candidate is not None:
        evaluation = split.test.copy()
        predictions = candidate.estimator.predict(evaluation[list(feature_columns)])
        evaluation["_absolute_error"] = evaluation["target_unit_price_twd"].to_numpy() - predictions
        evaluation["_absolute_error"] = evaluation["_absolute_error"].abs()
        grouped_errors = (
            evaluation.groupby(["station_code", "building_type"])
            .agg(
                count=("_absolute_error", "count"),
                mae=("_absolute_error", "mean"),
            )
            .reset_index()
            .sort_values(["station_code", "building_type"])
        )
        evaluation_error_summary = [
            {
                "station_code": row["station_code"],
                "building_type": row["building_type"],
                "count": int(row["count"]),
                "mae": float(row["mae"]),
            }
            for _, row in grouped_errors.iterrows()
        ]

    return {
        "station_counts": {k: int(v) for k, v in sorted(station_counts.items())},
        "missing_rates": dict(sorted(missing_rates.items())),
        "monthly_summary": monthly_summary,
        "building_type_summary": building_type_summary,
        "feature_extreme_rates": feature_extreme_rates,
        "evaluation_error_summary": evaluation_error_summary,
        "split_summary": split_summary,
    }


def run_annual_backtests(frame, selected_model_name, feature_columns):
    max_date = frame["transaction_date"].max()
    max_year = max_date.year
    max_month = max_date.month

    results = []
    for offset in range(3):
        year = max_year - offset
        month = max_month
        _, last_day = calendar.monthrange(year, month)
        cutoff = pd.Timestamp(year, month, last_day)

        filtered = frame[frame["transaction_date"] <= cutoff].copy()

        if len(filtered) < 300:
            continue

        try:
            split = split_by_time(filtered)
        except ValueError:
            continue

        estimators = candidate_estimators(feature_columns=feature_columns)
        candidate_est = estimators[selected_model_name]

        candidate_result = evaluate_candidate(
            selected_model_name,
            candidate_est,
            split.train,
            split.test,
            feature_columns=feature_columns,
            use_recency_weights=True,
        )

        baseline = RecentMedianBaseline(months=12)
        baseline.fit(split.train)
        baseline_result = evaluate_fitted_candidate(
            "baseline",
            baseline,
            split.test,
            feature_columns=feature_columns,
        )

        passed = candidate_result.overall_mae < baseline_result.overall_mae

        required_stations = ("A17", "A18", "A19")
        stations_within_limit = all(
            station in baseline_result.station_mape
            and station in candidate_result.station_mape
            and candidate_result.station_mape[station]
            <= baseline_result.station_mape[station] * 1.10
            for station in required_stations
        )

        results.append(
            {
                "cutoff_date": cutoff,
                "train_max_date": split.train["transaction_date"].max(),
                "test_min_date": split.test["transaction_date"].min(),
                "source_max_date": filtered["transaction_date"].max(),
                "passed": passed,
                "stations_within_limit": stations_within_limit,
                "candidate_metrics": candidate_result.metrics.to_dict(orient="index"),
                "baseline_metrics": baseline_result.metrics.to_dict(orient="index"),
            }
        )

    return results


def evaluate_release_checks(
    candidate_metrics,
    baseline_metrics,
    backtests,
    data_max_date,
    latest_official_date,
):
    overall_mae_improved = (
        candidate_metrics["overall"]["mae"] <= baseline_metrics["overall"]["mae"] * 0.98
    )

    required_station_metrics = tuple(f"station:{station}" for station in ("A17", "A18", "A19"))
    stations_within_limit = all(
        metric in baseline_metrics
        and metric in candidate_metrics
        and candidate_metrics[metric].get("mape", float("inf"))
        <= baseline_metrics[metric].get("mape", float("-inf")) * 1.10
        for metric in required_station_metrics
    )

    a18_baseline = baseline_metrics.get("station:A18", {}).get("mape", float("inf"))
    a18_candidate = candidate_metrics.get("station:A18", {}).get("mape", float("inf"))
    a18_improved = a18_candidate < a18_baseline

    has_three_backtests = len(backtests) == 3
    backtests_passed = (
        has_three_backtests and sum(1 for backtest in backtests if backtest.get("passed")) >= 2
    )

    backtest_stations_within_limit = has_three_backtests and all(
        backtest.get("stations_within_limit") for backtest in backtests
    )

    candidate_fresh = data_max_date >= latest_official_date - timedelta(days=180)

    recommended = all(
        (
            overall_mae_improved,
            stations_within_limit,
            a18_improved,
            backtests_passed,
            backtest_stations_within_limit,
            candidate_fresh,
        )
    )

    return {
        "overall_mae_improved": overall_mae_improved,
        "stations_within_limit": stations_within_limit,
        "a18_improved": a18_improved,
        "backtests_passed": backtests_passed,
        "backtest_stations_within_limit": backtest_stations_within_limit,
        "candidate_fresh": candidate_fresh,
        "recommended": recommended,
    }


RELEASE_REASON_CODES = {
    "overall_mae_improved": "overall_mae_not_improved",
    "stations_within_limit": "station_regression",
    "a18_improved": "a18_not_improved",
    "backtests_passed": "backtest_insufficient",
    "backtest_stations_within_limit": "backtest_station_regression",
    "candidate_fresh": "candidate_stale",
}


def release_reason_codes(checks: dict[str, bool]) -> list[str]:
    return [
        reason for check, reason in RELEASE_REASON_CODES.items() if not checks.get(check, False)
    ]
