import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from qingpu_insight.model_features import (
    BASE_FEATURE_COLUMNS,
    FEATURE_COLUMNS,
    ValuationInput,
    input_frame,
    parking_adjusted_target,
)
from qingpu_insight.model_training import recency_weights
from qingpu_insight.parking_valuation import (
    ParkingPriceEstimate,
    ParkingPricePolicy,
    build_parking_price_policy,
    estimate_parking_price,
)


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
    feature_columns: tuple[str, ...] = BASE_FEATURE_COLUMNS
    parking_price_policy: ParkingPricePolicy | None = None

    def __getattr__(self, name):
        if name == "feature_columns":
            return BASE_FEATURE_COLUMNS
        if name == "parking_price_policy":
            return None
        raise AttributeError(f"ValuationBundle has no attribute {name!r}")


class ModelUnavailableError(Exception):
    pass


class ModelRegistry:
    def __init__(self, artifact_dir: Path):
        self._artifact_dir = artifact_dir
        self._bundles: dict[str, ValuationBundle] = {}

    def load(self, transaction_type: str) -> ValuationBundle:
        official_dir = self._artifact_dir / "official" / transaction_type
        current_json = official_dir / "current.json"

        if current_json.exists():
            try:
                data = json.loads(current_json.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raise ModelUnavailableError(
                    f"corrupt current manifest for {transaction_type}"
                ) from None

            required = [
                "schema_version",
                "market",
                "version_id",
                "source_run_id",
                "artifact_file",
                "artifact_sha256",
                "activated_at",
            ]
            if not all(k in data for k in required):
                raise ModelUnavailableError(
                    f"corrupt current manifest for {transaction_type}: missing fields"
                )

            artifact_path = (self._artifact_dir / data["artifact_file"]).resolve()
            root = self._artifact_dir.resolve()
            if not str(artifact_path).startswith(str(root)):
                raise ModelUnavailableError(
                    f"artifact path {data['artifact_file']} is outside artifact directory"
                )

            if not artifact_path.exists():
                raise ModelUnavailableError(f"official artifact not found: {data['artifact_file']}")

            actual_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            if actual_hash != data["artifact_sha256"]:
                raise ModelUnavailableError(
                    f"SHA256 mismatch for official artifact {data['artifact_file']}"
                )

            bundle: ValuationBundle = joblib.load(str(artifact_path))
            if bundle.transaction_type != transaction_type:
                raise ModelUnavailableError(
                    f"loaded bundle is for {bundle.transaction_type}, not {transaction_type}"
                )

            self._bundles[transaction_type] = bundle
            return bundle

        return self.get(transaction_type)

    def get(self, transaction_type: str) -> ValuationBundle:
        if transaction_type not in self._bundles:
            current_json = self._artifact_dir / "official" / transaction_type / "current.json"
            if current_json.exists():
                return self.load(transaction_type)
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


def model_age_days(bundle: ValuationBundle, latest_data_date: pd.Timestamp) -> int:
    data_date = pd.Timestamp(bundle.data_max_date).normalize()
    return (latest_data_date.normalize() - data_date).days


def prediction_interval(bundle: ValuationBundle, unit_price: float) -> tuple[float, float]:
    radius = bundle.interval_abs_residual_twd_per_ping
    return max(0.0, unit_price - radius), unit_price + radius


def compose_total_price(
    building_unit_price_twd: float,
    building_area_ping: float,
    parking_estimate: ParkingPriceEstimate | None,
) -> tuple[int, int | None, int]:
    building = round(building_unit_price_twd * building_area_ping)
    if parking_estimate is None:
        return building, None, building
    parking = parking_estimate.price_twd
    return building, parking, building + parking


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


def _component_similarity(left: object, right: object, tolerance: float) -> float | None:
    try:
        l_val = float(left)  # type: ignore[arg-type]
        r_val = float(right)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if np.isnan(l_val) or np.isnan(r_val):
        return None
    if tolerance <= 0:
        return None
    return min(1.0, max(0.0, 1.0 - abs(l_val - r_val) / tolerance))


def _layout_similarity(input_row: pd.Series, candidate: pd.Series) -> float | None:
    scores = [
        _component_similarity(input_row.get(field), candidate.get(field), 2)
        for field in ("bedrooms", "living_rooms", "bathrooms")
    ]
    available = [score for score in scores if score is not None]
    if not available:
        return None
    return sum(available) / len(available)


def _comparable_similarity(
    input_row: pd.Series, candidate: pd.Series, max_date: pd.Timestamp
) -> float:
    ref_area = float(input_row["building_area_ping"])
    area = float(candidate["building_area_ping"])
    ref_distance = float(input_row["station_distance_m"])
    distance = float(candidate["station_distance_m"])
    ref_age = input_row.get("building_age_years", float("nan"))
    age = candidate.get("building_age_years", float("nan"))
    ref_floor_ratio = float(input_row["floor_ratio"])
    floor_ratio = float(candidate["floor_ratio"])
    ref_type = str(input_row["building_type"])
    candidate_type = str(candidate["building_type"])
    months_old = (max_date - pd.Timestamp(candidate["transaction_date"])).days / 30.0

    components = [
        (0.25, _component_similarity(ref_area, area, 20)),
        (0.20, _component_similarity(ref_distance, distance, 1000)),
        (0.15, _layout_similarity(input_row, candidate)),
        (0.10, _component_similarity(ref_age, age, 20)),
        (0.10, _component_similarity(ref_floor_ratio, floor_ratio, 0.5)),
        (0.10, 1.0 if ref_type == candidate_type else 0.0),
        (0.10, min(1.0, max(0.0, 1.0 - months_old / 60))),
    ]
    available = [(weight, score) for weight, score in components if score is not None]
    if not available:
        return 0.0
    return sum(weight * score for weight, score in available) / sum(
        weight for weight, _ in available
    )


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

    input_series = input_row.iloc[0]

    scores = []
    for _, row in pool.iterrows():
        similarity = _comparable_similarity(input_series, row, max_date)
        scores.append((similarity, row))

    scores.sort(key=lambda item: (-item[0], -item[1]["transaction_date"].timestamp()))
    top = scores[:5]

    comparables_list = []
    for similarity, row in top:
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
    latest_data_date: pd.Timestamp | None = None,
    stale_after_days: int = 180,
) -> dict[str, Any]:
    from qingpu_insight.model_training import RecentMedianBaseline

    degraded = False
    degraded_reason: str | None = None
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
        market_policy = (
            build_parking_price_policy(recent)
            if "parking_type" in recent.columns and "parking_price_twd" in recent.columns
            else None
        )
        parking_estimate = estimate_parking_price(market_policy, input_.parking_type)
        building_price, parking_price, total_price = compose_total_price(
            median_price, input_.building_area_ping, parking_estimate
        )
        low_unit = max(0, median_price - interval_radius)
        high_unit = median_price + interval_radius
        building_low = round(low_unit * input_.building_area_ping)
        building_high = round(high_unit * input_.building_area_ping)
        interval_total = (
            (building_low + parking_price, building_high + parking_price)
            if parking_price is not None
            else (building_low, building_high)
        )
        return {
            "transaction_type": input_.transaction_type,
            "estimated_unit_price_per_ping_twd": round(median_price),
            "estimated_total_price_twd": round(total_price),
            "estimated_building_price_twd": building_price,
            "estimated_parking_price_twd": parking_price,
            "parking_price_policy": (
                {
                    "parking_type": parking_estimate.parking_type,
                    "sample_size": parking_estimate.sample_size,
                    "source": parking_estimate.source,
                }
                if parking_estimate and parking_estimate.source != "none"
                else None
            ),
            "interval_total_price_twd": interval_total,
            "confidence": "low",
            "confidence_reasons": ["模型 artifact 不可用，使用近期中位數降級估價"],
            "factors": [],
            "comparables": [],
            "comparable_scope": "degraded",
            "data_date": str(data_date.date()),
            "degraded": True,
            "degraded_reason": "artifact_unavailable",
            "asking_price_assessment": None,
            "model": {
                "name": "recent_median_baseline",
                "version": "fallback",
                "transaction_type": input_.transaction_type,
            },
        }

    # Staleness check
    stale = False
    if input_.transaction_type == "resale" and latest_data_date is not None:
        age = model_age_days(bundle, latest_data_date)
        if age > stale_after_days:
            stale = True
            degraded = True
            degraded_reason = "stale_model"

    if stale:
        recent = market.loc[market["transaction_type"] == input_.transaction_type].copy()
        recent = recent.dropna(subset=["transaction_date", "unit_price_per_ping_twd"])
        if recent.empty:
            raise ModelUnavailableError("model artifact and market data are unavailable")
        cutoff = latest_data_date.normalize() - pd.DateOffset(months=12)
        recent = recent.loc[recent["transaction_date"] >= cutoff].copy()
        if {
            "building_area_ping",
            "parking_area_sqm",
            "parking_price_twd",
            "total_price_twd",
        } <= set(recent.columns):
            recent["target_unit_price_twd"] = recent.apply(
                lambda item: parking_adjusted_target(item)[0],
                axis=1,
            )
        else:
            recent["target_unit_price_twd"] = recent["unit_price_per_ping_twd"]
        baseline = RecentMedianBaseline(months=12).fit(recent)
        row = input_frame(input_, latest_data_date)
        unit_price = float(baseline.predict(row)[0])
        parking_estimate = estimate_parking_price(bundle.parking_price_policy, input_.parking_type)
        building_price, parking_price, total_price = compose_total_price(
            unit_price, input_.building_area_ping, parking_estimate
        )
        fallback_predictions = baseline.predict(recent)
        interval_radius = float(
            np.quantile(
                np.abs(recent["target_unit_price_twd"].to_numpy() - fallback_predictions),
                0.90,
            )
        )
        interval = (
            max(0.0, unit_price - interval_radius),
            unit_price + interval_radius,
        )
        factors = []
        comparables_list: list[dict[str, Any]] = []
        comparable_scope = "degraded"
        confidence_reasons = [
            "正式模型資料過舊",
            "使用最新官方資料的近期中位數降級估價",
        ]
        if bundle.parking_price_policy is None:
            confidence_reasons.append("legacy_parking")
        low, high = interval
        building_low = round(low * input_.building_area_ping)
        building_high = round(high * input_.building_area_ping)
        interval_total = (
            (building_low + parking_price, building_high + parking_price)
            if parking_price is not None
            else (building_low, building_high)
        )
        result: dict[str, Any] = {
            "transaction_type": input_.transaction_type,
            "estimated_unit_price_per_ping_twd": round(unit_price),
            "estimated_total_price_twd": round(total_price),
            "estimated_building_price_twd": building_price,
            "estimated_parking_price_twd": parking_price,
            "parking_price_policy": (
                {
                    "parking_type": parking_estimate.parking_type,
                    "sample_size": parking_estimate.sample_size,
                    "source": parking_estimate.source,
                }
                if parking_estimate and parking_estimate.source != "none"
                else None
            ),
            "interval_total_price_twd": interval_total,
            "confidence": "low",
            "confidence_reasons": confidence_reasons,
            "factors": factors,
            "comparables": comparables_list,
            "comparable_scope": comparable_scope,
            "data_date": str(latest_data_date.normalize().date()),
            "degraded": True,
            "degraded_reason": degraded_reason,
            "model": {
                "name": "recent_median_baseline",
                "version": "fallback",
                "transaction_type": input_.transaction_type,
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

    data_date = pd.Timestamp(bundle.data_max_date)
    row = input_frame(input_, data_date)

    unit_price = float(bundle.pipeline.predict(row)[0])
    parking_estimate = estimate_parking_price(bundle.parking_price_policy, input_.parking_type)
    building_price, parking_price, total_price = compose_total_price(
        unit_price, input_.building_area_ping, parking_estimate
    )
    interval = prediction_interval(bundle, unit_price)

    factors = local_factors(bundle, row)
    comparables_result = similar_transactions(bundle, row, market)
    comparables_list = comparables_result["comparables"]
    comparable_scope = comparables_result["comparable_scope"]

    assessing = confidence_assessment(bundle, row, unit_price, interval, comparables_list)
    if bundle.parking_price_policy is None:
        assessing["confidence_reasons"].append("legacy_parking")

    low, high = interval
    building_low = round(low * input_.building_area_ping)
    building_high = round(high * input_.building_area_ping)
    interval_total = (
        (building_low + parking_price, building_high + parking_price)
        if parking_price is not None
        else (building_low, building_high)
    )

    result: dict[str, Any] = {
        "transaction_type": input_.transaction_type,
        "estimated_unit_price_per_ping_twd": round(unit_price),
        "estimated_total_price_twd": round(total_price),
        "estimated_building_price_twd": building_price,
        "estimated_parking_price_twd": parking_price,
        "parking_price_policy": (
            {
                "parking_type": parking_estimate.parking_type,
                "sample_size": parking_estimate.sample_size,
                "source": parking_estimate.source,
            }
            if parking_estimate and parking_estimate.source != "none"
            else None
        ),
        "interval_total_price_twd": interval_total,
        "confidence": assessing["confidence"],
        "confidence_reasons": assessing["confidence_reasons"],
        "factors": factors,
        "comparables": comparables_list,
        "comparable_scope": comparable_scope,
        "data_date": bundle.data_max_date,
        "degraded": False,
        "degraded_reason": None,
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
    feature_columns: tuple[str, ...] = FEATURE_COLUMNS,
    training_frame: pd.DataFrame | None = None,
    use_recency_weights: bool = False,
    recency_half_life_months: int = 48,
) -> Path:
    from sklearn.base import clone
    from sklearn.inspection import permutation_importance

    from qingpu_insight.model_training import (
        RecentMedianBaseline,
        fit_candidate,
    )

    calibration_pred = selected.estimator.predict(split.calibration[list(feature_columns)])
    radius = float(
        np.quantile(
            np.abs(split.calibration["target_unit_price_twd"].values - calibration_pred),
            0.90,
        )
    )

    imp = permutation_importance(
        selected.estimator,
        split.calibration[list(feature_columns)],
        split.calibration["target_unit_price_twd"],
        scoring="neg_mean_absolute_error",
        n_repeats=5,
        random_state=42,
    )
    importance_list = [
        {"feature": name, "importance": float(imp.importances_mean[i])}
        for i, name in enumerate(feature_columns)
    ]
    importance_list.sort(key=lambda x: x["importance"], reverse=True)

    contract_str = json.dumps(list(feature_columns), sort_keys=True)
    contract_hash = hashlib.sha256(contract_str.encode()).hexdigest()
    version = _model_version(transaction_type, bundle.data_max_date, contract_hash)

    train_frame = training_frame if training_frame is not None else split.train
    deployed_estimator = selected.estimator
    if training_frame is not None or use_recency_weights:
        try:
            deployed_estimator = clone(selected.estimator)
        except TypeError:
            deployed_estimator = deepcopy(selected.estimator)
        if isinstance(deployed_estimator, RecentMedianBaseline):
            deployed_estimator.fit(train_frame)
        else:
            weights = (
                recency_weights(
                    train_frame,
                    half_life_months=recency_half_life_months,
                )
                if use_recency_weights
                else None
            )
            fit_candidate(
                deployed_estimator,
                train_frame[list(feature_columns)],
                train_frame["target_unit_price_twd"],
                sample_weight=weights,
            )
    parking_policy = (
        build_parking_price_policy(train_frame) if "parking_type" in train_frame.columns else None
    )

    feature_ranges: dict[str, tuple[float, float]] = {}
    feature_hard_ranges: dict[str, tuple[float, float]] = {}
    feature_medians: dict[str, float] = {}
    for col in feature_columns:
        if col in (
            "station_code",
            "building_type",
            "parking_type",
            "station_building_type",
            "building_age_band",
            "area_band",
            "floor_band",
        ):
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
        pipeline=deployed_estimator,
        interval_abs_residual_twd_per_ping=radius,
        feature_ranges=feature_ranges,
        feature_hard_ranges=feature_hard_ranges,
        feature_medians=feature_medians,
        global_importance=importance_list,
        reference_rows=train_frame,
        data_min_date=str(train_frame["transaction_date"].min().date()),
        data_max_date=str(train_frame["transaction_date"].max().date()),
        metrics=selected.metrics.to_dict(orient="index"),
        feature_columns=feature_columns,
        parking_price_policy=parking_policy,
    )

    artifact_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = artifact_dir / f"{transaction_type}.tmp"
    final_path = artifact_dir / f"{transaction_type}.joblib"
    joblib.dump(result_bundle, tmp_path)
    tmp_path.replace(final_path)
    return final_path
