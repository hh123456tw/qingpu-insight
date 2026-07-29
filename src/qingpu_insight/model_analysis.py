import calendar
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import clone

from qingpu_insight.market_cleaning import (
    MARKET_TRANSACTION_SUBJECTS,
    SPECIAL_RELATIONSHIP_PATTERN,
)
from qingpu_insight.model_features import BASE_FEATURE_COLUMNS, FEATURE_COLUMNS, RESALE_FEATURE_SETS
from qingpu_insight.model_training import (
    CandidateEvaluation,
    ModelFitSpec,
    RecentMedianBaseline,
    TimeSplit,
    build_estimator,
    candidate_estimators,
    evaluate_candidate,
    evaluate_fitted_candidate,
    run_model_experiment,
    split_by_time,
)
from qingpu_insight.model_tuning import BALANCED_PROFILE, TrainingProfile


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


@dataclass(frozen=True)
class SharedFeatureExperimentResult:
    calibration_experiments: tuple[FeatureExperiment, ...]
    locked_feature_set_name: str
    locked_feature_columns: tuple[str, ...]
    selection_reason: str


_RESALE_LOCKABLE = frozenset(
    {"baseline_v3", "common_area", "community", "common_area_community"}
)


def run_shared_feature_experiments(split: TimeSplit) -> SharedFeatureExperimentResult:
    experiments: list[FeatureExperiment] = []

    for name, feature_columns in RESALE_FEATURE_SETS.items():
        all_candidates = candidate_estimators(feature_columns=feature_columns)
        candidate_results: list[CandidateEvaluation] = []
        candidate_errors: dict[str, str] = {}

        for model_name, est in all_candidates.items():
            try:
                result = evaluate_candidate(
                    model_name,
                    est,
                    split.train,
                    split.calibration,
                    feature_columns=feature_columns,
                )
                candidate_results.append(result)
            except Exception:
                candidate_errors[model_name] = "candidate_failed"

        if candidate_results:
            best = min(candidate_results, key=lambda r: r.overall_mae)
            experiments.append(
                FeatureExperiment(
                    name=name,
                    feature_columns=feature_columns,
                    selected_model=best.name,
                    metrics=best.metrics.to_dict(orient="index"),
                    candidate_errors=candidate_errors,
                )
            )
        else:
            experiments.append(
                FeatureExperiment(
                    name=name,
                    feature_columns=feature_columns,
                    selected_model=None,
                    metrics={},
                    candidate_errors=candidate_errors,
                )
            )

    lockable = [
        e for e in experiments if e.name in _RESALE_LOCKABLE and e.selected_model is not None
    ]
    if not lockable:
        raise ValueError("no lockable feature set experiment completed")

    def _sort_key(e: FeatureExperiment) -> tuple:
        overall = e.metrics.get("overall", {})
        mae = overall.get("mae", float("inf"))
        mape = overall.get("mape", float("inf"))
        n_features = len(e.feature_columns)
        return (mae, mape, n_features)

    best = min(lockable, key=_sort_key)
    best_mae = best.metrics.get("overall", {}).get("mae", float("inf"))
    best_mape = best.metrics.get("overall", {}).get("mape", float("inf"))
    selection_reason = (
        f"locked {best.name} (MAE={best_mae:.1f}, MAPE={best_mape:.2f}, "
        f"features={len(best.feature_columns)})"
    )

    return SharedFeatureExperimentResult(
        calibration_experiments=tuple(experiments),
        locked_feature_set_name=best.name,
        locked_feature_columns=best.feature_columns,
        selection_reason=selection_reason,
    )


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
    source_frame: pd.DataFrame | None = None,
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
    top_residuals: list[dict[str, object]] = []
    if candidate is not None:
        evaluation = split.test.copy()
        predictions = candidate.estimator.predict(evaluation[list(feature_columns)])
        actual = evaluation["target_unit_price_twd"].to_numpy(dtype=float)
        evaluation["_predicted"] = predictions
        evaluation["_absolute_error"] = np.abs(actual - predictions)
        evaluation["_absolute_percentage_error"] = (
            evaluation["_absolute_error"].to_numpy()
            / np.maximum(np.abs(actual), 100_000)
            * 100
        )
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
        high_price_threshold = float(evaluation["target_unit_price_twd"].quantile(0.95))
        largest = evaluation.nlargest(20, "_absolute_error")
        for index, row in largest.iterrows():
            flags: list[str] = []
            if float(row["target_unit_price_twd"]) >= high_price_threshold:
                flags.append("高價尾端")
            if float(row["_absolute_percentage_error"]) >= 30:
                flags.append("高相對誤差")
            top_residuals.append(
                {
                    "record_id": str(row.get("record_id", index)),
                    "transaction_date": str(pd.Timestamp(row["transaction_date"]).date()),
                    "station_code": str(row.get("station_code", "")),
                    "road_key": str(row.get("road_key", "") or ""),
                    "building_type": str(row.get("building_type", "") or ""),
                    "actual_twd_per_ping": float(row["target_unit_price_twd"]),
                    "predicted_twd_per_ping": float(row["_predicted"]),
                    "absolute_error_twd_per_ping": float(row["_absolute_error"]),
                    "absolute_percentage_error": float(
                        row["_absolute_percentage_error"]
                    ),
                    "flags": flags,
                }
            )

    quality_source = source_frame if source_frame is not None else frame
    subjects = quality_source.get(
        "transaction_subject",
        pd.Series("", index=quality_source.index, dtype="string"),
    ).fillna("")
    remarks = quality_source.get(
        "remarks",
        pd.Series("", index=quality_source.index, dtype="string"),
    ).fillna("")
    special_relationship = remarks.str.contains(SPECIAL_RELATIONSHIP_PATTERN)
    non_market_subject = subjects.ne("") & ~subjects.isin(MARKET_TRANSACTION_SUBJECTS)
    ambiguous_registration = remarks.str.contains(
        "預售屋、或土地及建物分件登記案件",
        regex=False,
    )

    return {
        "station_counts": {k: int(v) for k, v in sorted(station_counts.items())},
        "missing_rates": dict(sorted(missing_rates.items())),
        "monthly_summary": monthly_summary,
        "building_type_summary": building_type_summary,
        "feature_extreme_rates": feature_extreme_rates,
        "evaluation_error_summary": evaluation_error_summary,
        "top_residuals": top_residuals,
        "data_quality": {
            "special_relationship_excluded": int(special_relationship.sum()),
            "non_market_subject_excluded": int(non_market_subject.sum()),
            "ambiguous_registration_note_count": int(ambiguous_registration.sum()),
        },
        "split_summary": split_summary,
    }


def run_annual_backtests(
    frame,
    selected_model_name,
    feature_columns=FEATURE_COLUMNS,
    profile: TrainingProfile = BALANCED_PROFILE,
    fit_spec: ModelFitSpec | None = None,
):
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

        if fit_spec is not None:
            candidate_est = build_estimator(
                fit_spec,
                feature_columns=feature_columns,
            )
            candidate_result = evaluate_candidate(
                selected_model_name,
                candidate_est,
                split.train,
                split.test,
                feature_columns=feature_columns,
                use_recency_weights=fit_spec.recency_half_life_months is not None,
                recency_half_life_months=fit_spec.recency_half_life_months or 48,
            )
        else:
            estimators = candidate_estimators(
                feature_columns=feature_columns,
                profile=profile,
            )
            candidate_est = estimators[selected_model_name]

            candidate_result = evaluate_candidate(
                selected_model_name,
                candidate_est,
                split.train,
                split.test,
                feature_columns=feature_columns,
                use_recency_weights=profile.recency_half_life_months is not None,
                recency_half_life_months=profile.recency_half_life_months or 48,
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

    backtest_stations_within_limit = (
        has_three_backtests
        and sum(
            1
            for backtest in backtests
            if backtest.get("stations_within_limit")
        )
        >= 2
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
