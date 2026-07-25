import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from qingpu_insight.model_features import FEATURE_COLUMNS, ValuationInput, input_frame


@dataclass
class ValuationBundle:
    transaction_type: str
    model_name: str
    model_version: str
    pipeline: Any
    interval_abs_residual_twd_per_ping: float
    feature_ranges: dict[str, tuple[float, float]]
    feature_hard_ranges: dict[str, tuple[float, float]]
    feature_medians: dict[str, float]
    global_importance: list[dict[str, float | str]]
    reference_rows: pd.DataFrame
    data_min_date: str
    data_max_date: str
    metrics: dict[str, Any]


class ModelUnavailableError(Exception):
    pass


class ModelRegistry:
    def __init__(self, artifact_dir: Path):
        self._artifact_dir = artifact_dir
        self._bundles: dict[str, ValuationBundle] = {}

    def get(self, transaction_type: str) -> ValuationBundle:
        if transaction_type not in self._bundles:
            path = self._artifact_dir / f"{transaction_type}.joblib"
            if not path.exists():
                raise ModelUnavailableError(f"{transaction_type} model artifact not found")
            bundle: ValuationBundle = joblib.load(path)
            if bundle.transaction_type != transaction_type:
                raise ModelUnavailableError(
                    f"loaded bundle is for {bundle.transaction_type}, not {transaction_type}"
                )
            self._bundles[transaction_type] = bundle
        return self._bundles[transaction_type]


def prediction_interval(bundle: ValuationBundle, unit_price: float) -> tuple[float, float]:
    radius = bundle.interval_abs_residual_twd_per_ping
    return max(0.0, unit_price - radius), unit_price + radius


def local_factors(bundle: ValuationBundle, row: pd.DataFrame) -> list[dict[str, object]]:
    base = float(bundle.pipeline.predict(row)[0])
    factors = []
    for feature, median in bundle.feature_medians.items():
        changed = row.copy()
        changed.loc[0, feature] = median
        delta = base - float(bundle.pipeline.predict(changed)[0])
        factors.append(
            {
                "feature": feature,
                "impact_twd_per_ping": round(delta),
                "direction": "positive" if delta >= 0 else "negative",
            }
        )
    return sorted(factors, key=lambda item: abs(item["impact_twd_per_ping"]), reverse=True)[:5]


def similar_transactions(
    bundle: ValuationBundle, input_row: pd.DataFrame, market: pd.DataFrame
) -> dict[str, Any]:
    input_station = str(input_row.at[0, "station_code"])
    max_date = pd.Timestamp(bundle.data_max_date)
    cutoff = max_date - pd.DateOffset(months=36)

    candidates = market.loc[
        (market["transaction_type"] == bundle.transaction_type)
        & (market["transaction_date"] >= cutoff)
    ].copy()

    if len(candidates) < 3:
        return {"comparables": [], "comparable_scope": "insufficient_data"}

    same_station = candidates.loc[candidates["station_code"] == input_station].copy()
    expanded = len(same_station) < 3
    pool = same_station if not expanded else candidates
    scope = "same_station" if not expanded else "expanded_station"

    ref_area = float(input_row.at[0, "building_area_ping"])
    ref_dist = float(input_row.at[0, "station_distance_m"])
    ref_bed = float(input_row.at[0, "bedrooms"])
    ref_age = (
        float(input_row.at[0, "building_age_years"])
        if pd.notna(input_row.at[0, "building_age_years"])
        else 0
    )
    ref_ratio = float(input_row.at[0, "floor_ratio"])
    ref_type = str(input_row.at[0, "building_type"])

    scores = []
    for _, row in pool.iterrows():
        age = float(row["building_age_years"]) if pd.notna(row.get("building_age_years")) else 0
        dist = (
            abs(float(row["building_area_ping"]) - ref_area)
            + abs(float(row["station_distance_m"]) - ref_dist) / 100
            + abs(float(row["bedrooms"]) - ref_bed)
            + abs(age - ref_age) / 10
            + abs(float(row.get("floor_ratio", 0)) - ref_ratio)
            + abs((max_date - pd.Timestamp(row["transaction_date"])).days) / 30
        )
        penalty = 0.5 if str(row.get("building_type", "")) != ref_type else 0
        scores.append((dist + penalty, row))

    scores.sort(key=lambda item: item[0])
    top = scores[:5]

    max_dist = max(item[0] for item in top) if top else 1
    if max_dist == 0:
        max_dist = 1

    comparables_list = []
    for dist_score, row in top:
        similarity = max(0.0, 1.0 - dist_score / max_dist)
        comparables_list.append(
            {
                "record_id": str(row["record_id"]),
                "transaction_type": str(row["transaction_type"]),
                "transaction_date": str(pd.Timestamp(row["transaction_date"]).date()),
                "station_code": str(row["station_code"]),
                "building_type": str(row["building_type"]),
                "building_area_ping": round(float(row["building_area_ping"]), 2),
                "unit_price_per_ping_twd": round(float(row["unit_price_per_ping_twd"])),
                "total_price_twd": round(float(row["total_price_twd"])),
                "floor_ratio": round(float(row.get("floor_ratio", 0)), 4),
                "longitude": round(float(row.get("longitude", 0)), 4),
                "latitude": round(float(row.get("latitude", 0)), 4),
                "similarity_score": round(similarity, 4),
            }
        )

    return {"comparables": comparables_list, "comparable_scope": scope}


def confidence_assessment(
    bundle: ValuationBundle,
    input_row: pd.DataFrame,
    unit_price: float,
    interval: tuple[float, float],
    comparables: list[dict[str, Any]],
    degraded: bool = False,
) -> dict[str, Any]:
    reasons = []

    if degraded:
        return {"confidence": "low", "confidence_reasons": ["使用降級模型（近期中位數基準）"]}

    interval_width = interval[1] - interval[0]
    width_ratio = interval_width / unit_price if unit_price > 0 else 1

    numeric_fields = [
        "building_area_ping",
        "station_distance_m",
        "bedrooms",
        "living_rooms",
        "bathrooms",
        "floor",
        "total_floors",
        "parking_area_ping",
    ]
    if pd.notna(input_row.at[0, "building_age_years"]):
        numeric_fields.append("building_age_years")

    p5_fails = 0
    p1_fails = 0
    for field in numeric_fields:
        val = float(input_row.at[0, field])
        if field in bundle.feature_ranges:
            p5_low, p5_high = bundle.feature_ranges[field]
            if val < p5_low or val > p5_high:
                p5_fails += 1
        if field in bundle.feature_hard_ranges:
            h_low, h_high = bundle.feature_hard_ranges[field]
            if val < h_low or val > h_high:
                p1_fails += 1

    good_comparables = sum(1 for c in comparables if c.get("similarity_score", 0) >= 0.60)

    fails = 0
    if p5_fails > 0:
        fails += 1
        reasons.append("部分輸入數值超出主要訓練範圍")
    if width_ratio > 0.30:
        fails += 1
        reasons.append("估價區間寬度超過估計總價 30%")
    if good_comparables < 3:
        fails += 1
        reasons.append("高相似度成交案例不足（少於 3 筆 ≥ 0.60）")

    if p1_fails > 0:
        reasons.append("部分輸入數值超出全部訓練範圍")
        return {"confidence": "low", "confidence_reasons": reasons}
    if fails >= 2:
        return {"confidence": "low", "confidence_reasons": reasons}
    if fails == 1:
        return {"confidence": "medium", "confidence_reasons": reasons}
    return {"confidence": "high", "confidence_reasons": []}


def valuate(
    input_: ValuationInput,
    registry: ModelRegistry,
    market: pd.DataFrame,
) -> dict[str, Any]:
    degraded = False
    try:
        bundle = registry.get(input_.transaction_type)
    except ModelUnavailableError:
        bundle = None
        degraded = True

    if degraded or bundle is None:
        recent = market.loc[(market["transaction_type"] == input_.transaction_type)].copy()
        recent = recent.dropna(subset=["transaction_date", "unit_price_per_ping_twd"])
        if recent.empty:
            raise ModelUnavailableError("model artifact and market data are unavailable")
        data_date = pd.Timestamp(recent["transaction_date"].max())
        recent = recent.loc[recent["transaction_date"] >= data_date - pd.DateOffset(months=24)]
        same_station = recent.loc[recent["station_code"] == input_.station_code]
        same_cohort = same_station.loc[same_station["building_type"] == input_.building_type]
        cohort = same_cohort if not same_cohort.empty else same_station
        if cohort.empty:
            cohort = recent
        median_price = float(cohort["unit_price_per_ping_twd"].median())
        deviations = (cohort["unit_price_per_ping_twd"] - median_price).abs()
        interval_radius = float(deviations.quantile(0.90))
        total_price = median_price * input_.building_area_ping
        return {
            "transaction_type": input_.transaction_type,
            "estimated_unit_price_per_ping_twd": round(median_price),
            "estimated_total_price_twd": round(total_price),
            "interval_total_price_twd": (
                round(max(0, median_price - interval_radius) * input_.building_area_ping),
                round((median_price + interval_radius) * input_.building_area_ping),
            ),
            "confidence": "low",
            "confidence_reasons": ["模型 artifact 不可用，使用近期中位數降級估價"],
            "factors": [],
            "comparables": [],
            "comparable_scope": "degraded",
            "data_date": str(data_date.date()),
            "degraded": True,
            "asking_price_assessment": None,
            "model": {
                "name": "recent_median_baseline",
                "version": "fallback",
                "transaction_type": input_.transaction_type,
            },
        }

    data_date = pd.Timestamp(bundle.data_max_date)
    row = input_frame(input_, data_date)

    unit_price = float(bundle.pipeline.predict(row)[0])
    total_price = unit_price * input_.building_area_ping
    interval = prediction_interval(bundle, unit_price)

    factors = local_factors(bundle, row)
    comparables_result = similar_transactions(bundle, row, market)
    comparables_list = comparables_result["comparables"]
    comparable_scope = comparables_result["comparable_scope"]

    assessing = confidence_assessment(bundle, row, unit_price, interval, comparables_list)

    result: dict[str, Any] = {
        "transaction_type": input_.transaction_type,
        "estimated_unit_price_per_ping_twd": round(unit_price),
        "estimated_total_price_twd": round(total_price),
        "interval_total_price_twd": (
            round(interval[0] * input_.building_area_ping),
            round(interval[1] * input_.building_area_ping),
        ),
        "confidence": assessing["confidence"],
        "confidence_reasons": assessing["confidence_reasons"],
        "factors": factors,
        "comparables": comparables_list,
        "comparable_scope": comparable_scope,
        "data_date": bundle.data_max_date,
        "degraded": False,
        "model": {
            "name": bundle.model_name,
            "version": bundle.model_version,
            "transaction_type": bundle.transaction_type,
        },
    }

    if input_.asking_total_price_twd is not None and input_.asking_total_price_twd > 0:
        low, high = result["interval_total_price_twd"]
        if input_.asking_total_price_twd < low:
            assessment = "偏低"
        elif input_.asking_total_price_twd <= high:
            assessment = "合理區間"
        else:
            assessment = "偏高"
        result["asking_price_assessment"] = assessment
    else:
        result["asking_price_assessment"] = None

    return result


def _model_version(transaction_type: str, max_date: str, contract_hash: str) -> str:
    return f"{transaction_type}-{max_date}-{contract_hash[:8]}"


def train_artifact(
    transaction_type: str,
    selected: Any,
    split: Any,
    bundle: ValuationBundle,
    artifact_dir: Path,
) -> Path:
    from sklearn.inspection import permutation_importance

    calibration_pred = selected.estimator.predict(split.calibration[list(FEATURE_COLUMNS)])
    radius = float(
        np.quantile(
            np.abs(split.calibration["target_unit_price_twd"].values - calibration_pred),
            0.90,
        )
    )

    imp = permutation_importance(
        selected.estimator,
        split.calibration[list(FEATURE_COLUMNS)],
        split.calibration["target_unit_price_twd"],
        scoring="neg_mean_absolute_error",
        n_repeats=5,
        random_state=42,
    )
    importance_list = [
        {"feature": name, "importance": float(imp.importances_mean[i])}
        for i, name in enumerate(FEATURE_COLUMNS)
    ]
    importance_list.sort(key=lambda x: x["importance"], reverse=True)

    contract_str = json.dumps(list(FEATURE_COLUMNS), sort_keys=True)
    contract_hash = hashlib.sha256(contract_str.encode()).hexdigest()
    version = _model_version(transaction_type, bundle.data_max_date, contract_hash)

    train_frame = split.train
    feature_ranges: dict[str, tuple[float, float]] = {}
    feature_hard_ranges: dict[str, tuple[float, float]] = {}
    feature_medians: dict[str, float] = {}
    for col in FEATURE_COLUMNS:
        if col in ("station_code", "building_type", "parking_type"):
            continue
        values = train_frame[col].dropna()
        if len(values) > 0:
            feature_ranges[col] = (float(values.quantile(0.05)), float(values.quantile(0.95)))
            feature_hard_ranges[col] = (float(values.quantile(0.01)), float(values.quantile(0.99)))
            feature_medians[col] = float(values.median())

    result_bundle = ValuationBundle(
        transaction_type=transaction_type,
        model_name=selected.name,
        model_version=version,
        pipeline=selected.estimator,
        interval_abs_residual_twd_per_ping=radius,
        feature_ranges=feature_ranges,
        feature_hard_ranges=feature_hard_ranges,
        feature_medians=feature_medians,
        global_importance=importance_list,
        reference_rows=split.train,
        data_min_date=str(split.train["transaction_date"].min().date()),
        data_max_date=str(split.train["transaction_date"].max().date()),
        metrics=selected.metrics.to_dict(orient="index"),
    )

    artifact_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = artifact_dir / f"{transaction_type}.tmp"
    final_path = artifact_dir / f"{transaction_type}.joblib"
    joblib.dump(result_bundle, tmp_path)
    tmp_path.replace(final_path)
    return final_path
