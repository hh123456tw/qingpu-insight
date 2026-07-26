import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from qingpu_insight.model_features import FEATURE_COLUMNS
from qingpu_insight.model_training import (
    RecentMedianBaseline,
    TimeSplit,
    candidate_estimators,
    evaluate_candidate,
    evaluate_fitted_candidate,
    passes_release_gate,
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
    from qingpu_insight.model_features import BASE_FEATURE_COLUMNS
    from qingpu_insight.model_training import candidate_estimators
    experiments: list[FeatureExperiment] = []

    base_result = run_model_experiment(split, feature_columns=BASE_FEATURE_COLUMNS)
    base_selected = next(
        c for c in base_result.selection_results if c.name == base_result.selected_name
    )
    experiments.append(FeatureExperiment(
        name="base",
        feature_columns=BASE_FEATURE_COLUMNS,
        selected_model=base_result.selected_name,
        metrics=base_selected.metrics.to_dict(orient="index"),
        candidate_errors=base_result.candidate_errors,
    ))

    all_candidates = candidate_estimators(feature_columns=FEATURE_COLUMNS)
    enhanced_result = run_model_experiment(split, feature_columns=FEATURE_COLUMNS, estimators=all_candidates)
    enhanced_winner_name = enhanced_result.selected_name
    enhanced_selected = next(
        c for c in enhanced_result.selection_results if c.name == enhanced_result.selected_name
    )
    experiments.append(FeatureExperiment(
        name="enhanced",
        feature_columns=FEATURE_COLUMNS,
        selected_model=enhanced_result.selected_name,
        metrics=enhanced_selected.metrics.to_dict(orient="index"),
        candidate_errors=enhanced_result.candidate_errors,
    ))

    for ab_name, removed in ABLATIONS.items():
            ablation_features = tuple(f for f in FEATURE_COLUMNS if f not in removed)
            ablation_result = run_model_experiment(
                split,
                feature_columns=ablation_features,
                estimators={enhanced_winner_name: all_candidates[enhanced_winner_name]},
            )
            ablation_selected = next(
                c for c in ablation_result.selection_results if c.name == ablation_result.selected_name
            )
            experiments.append(FeatureExperiment(
                name=ab_name,
                feature_columns=ablation_features,
                selected_model=ablation_result.selected_name,
                metrics=ablation_selected.metrics.to_dict(orient="index"),
                candidate_errors=ablation_result.candidate_errors,
            ))

    return tuple(experiments)


def build_resale_diagnostics(
    frame: pd.DataFrame, split: TimeSplit
) -> dict[str, object]:
    station_counts = frame["station_code"].value_counts().to_dict()
    missing_rates = {
        col: round(float(frame[col].isna().mean()), 6)
        for col in FEATURE_COLUMNS
        if col in frame.columns
    }
    monthly = (
        frame.groupby(
            [frame["transaction_date"].dt.to_period("M"), "station_code"]
        )
        .agg(count=("target_unit_price_twd", "count"), median_target=("target_unit_price_twd", "median"))
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
        .agg(count=("target_unit_price_twd", "count"), median_target=("target_unit_price_twd", "median"))
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
                for k, v in sorted(
                    (df["station_code"].value_counts() / n).to_dict().items()
                )
            },
            "building_type_proportions": {
                k: float(v)
                for k, v in sorted(
                    (df["building_type"].value_counts() / n).to_dict().items()
                )
            },
        }

    split_summary = {
        "train": _summarize(split.train),
        "calibration": _summarize(split.calibration),
        "test": _summarize(split.test),
    }

    return {
        "station_counts": {
            k: int(v) for k, v in sorted(station_counts.items())
        },
        "missing_rates": dict(sorted(missing_rates.items())),
        "monthly_summary": monthly_summary,
        "building_type_summary": building_type_summary,
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
            selected_model_name, candidate_est, split.train, split.calibration,
            feature_columns=feature_columns,
        )

        baseline = RecentMedianBaseline()
        baseline.fit(split.train)
        baseline_result = evaluate_fitted_candidate(
            "baseline", baseline, split.calibration, feature_columns=feature_columns,
        )

        passed = passes_release_gate(candidate_result, baseline_result)

        stations_within_limit = all(
            candidate_result.station_mape.get(station, float("inf"))
            <= baseline_result.station_mape[station] * 1.10
            for station in baseline_result.station_mape
        )

        results.append({
            "cutoff_date": cutoff,
            "train_max_date": split.train["transaction_date"].max(),
            "test_min_date": split.test["transaction_date"].min(),
            "source_max_date": filtered["transaction_date"].max(),
            "passed": passed,
            "stations_within_limit": stations_within_limit,
            "candidate_metrics": candidate_result.metrics.to_dict(orient="index"),
            "baseline_metrics": baseline_result.metrics.to_dict(orient="index"),
        })

    return results


def evaluate_release_checks(candidate_metrics, baseline_metrics, backtests, data_max_date, latest_official_date):
    overall_mae_improved = (
        candidate_metrics["overall"]["mae"] <= baseline_metrics["overall"]["mae"] * 0.98
    )

    stations_within_limit = all(
        candidate_metrics.get(metric, {}).get("mape", float("inf"))
        <= baseline_metrics[metric]["mape"] * 1.10
        for metric in baseline_metrics
        if metric.startswith("station:")
    )

    a18_baseline = baseline_metrics.get("station:A18", {}).get("mape", float("inf"))
    a18_candidate = candidate_metrics.get("station:A18", {}).get("mape", float("inf"))
    a18_improved = a18_candidate < a18_baseline

    backtests_passed = sum(1 for b in backtests if b.get("passed")) >= 2

    backtest_stations_within_limit = all(b.get("stations_within_limit") for b in backtests)

    candidate_fresh = data_max_date >= latest_official_date - timedelta(days=180)

    recommended = all((
        overall_mae_improved,
        stations_within_limit,
        a18_improved,
        backtests_passed,
        backtest_stations_within_limit,
        candidate_fresh,
    ))

    return {
        "overall_mae_improved": overall_mae_improved,
        "stations_within_limit": stations_within_limit,
        "a18_improved": a18_improved,
        "backtests_passed": backtests_passed,
        "backtest_stations_within_limit": backtest_stations_within_limit,
        "candidate_fresh": candidate_fresh,
        "recommended": recommended,
    }
