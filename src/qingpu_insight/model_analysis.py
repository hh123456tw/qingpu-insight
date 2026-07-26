from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from qingpu_insight.model_features import FEATURE_COLUMNS
from qingpu_insight.model_training import TimeSplit, run_model_experiment


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
