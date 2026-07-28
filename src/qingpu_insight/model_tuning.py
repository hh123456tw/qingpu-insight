from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

MarketName = Literal["resale", "presale"]
ProfileName = Literal["quick", "balanced", "thorough", "custom"]
ProfileSource = Literal["preset", "custom"]
PROFILE_ORDER: tuple[ProfileName, ...] = (
    "quick", "balanced", "thorough", "custom",
)


class TuningValidationError(ValueError):
    def __init__(self, fields: dict[str, str]) -> None:
        self.fields = fields
        super().__init__("invalid tuning plan")


@dataclass(frozen=True)
class TrainingProfile:
    name: ProfileName
    source: ProfileSource
    hgb_learning_rate: float
    hgb_max_iter: int
    rf_n_estimators: int
    recency_half_life_months: int | None

    def snapshot(self) -> dict[str, object]:
        return {
            "name": self.name,
            "source": self.source,
            "hgb_learning_rate": self.hgb_learning_rate,
            "hgb_max_iter": self.hgb_max_iter,
            "rf_n_estimators": self.rf_n_estimators,
            "recency_half_life_months": self.recency_half_life_months,
        }


@dataclass(frozen=True)
class TrainingTuningPlan:
    version: int
    mode: Literal["preset_comparison"]
    profiles: tuple[TrainingProfile, ...]

    @property
    def include_custom(self) -> bool:
        return any(profile.name == "custom" for profile in self.profiles)


AutoMLBudgetName = Literal["quick", "standard", "deep"]


@dataclass(frozen=True)
class AutoMLBudget:
    name: AutoMLBudgetName
    seconds: int
    max_trials: int


@dataclass(frozen=True)
class AutoMLTuningPlan:
    version: Literal[2]
    mode: Literal["automl"]
    budget: AutoMLBudget
    seed: Literal[42] = 42


TrainingPlan = TrainingTuningPlan | AutoMLTuningPlan

AUTOML_BUDGETS = {
    "quick": AutoMLBudget("quick", 300, 12),
    "standard": AutoMLBudget("standard", 900, 35),
    "deep": AutoMLBudget("deep", 1800, 70),
}

PRESET_PROFILES = (
    TrainingProfile("quick", "preset", 0.08, 180, 160, 48),
    TrainingProfile("balanced", "preset", 0.06, 350, 400, 48),
    TrainingProfile("thorough", "preset", 0.04, 600, 700, 48),
)
BALANCED_PROFILE = PRESET_PROFILES[1]


def _parse_automl_plan(payload: Mapping[str, Any]) -> AutoMLTuningPlan:
    fields: dict[str, str] = {}
    allowed = frozenset({"mode", "budget"})
    for key in sorted(set(payload) - allowed):
        fields[key] = "not_allowed"

    budget_name = payload.get("budget")
    if budget_name not in AUTOML_BUDGETS:
        fields["budget"] = "required"

    if fields:
        raise TuningValidationError(fields)

    assert isinstance(budget_name, str)
    return AutoMLTuningPlan(2, "automl", AUTOML_BUDGETS[budget_name])


def _finite_float(value: Any, low: float, high: float) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and low <= float(value) <= high
    )


def _bounded_int(value: Any, low: int, high: int) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and low <= value <= high
    )


def parse_tuning_plan(
    markets: tuple[MarketName, ...],
    payload: Mapping[str, Any] | None,
) -> TrainingPlan:
    if payload is None:
        return TrainingTuningPlan(1, "preset_comparison", PRESET_PROFILES)
    if not isinstance(payload, Mapping):
        raise TuningValidationError({"body": "object"})

    mode = payload.get("mode")
    if mode == "automl":
        return _parse_automl_plan(payload)

    fields: dict[str, str] = {}
    allowed = {"mode", "include_custom", "custom"}
    for key in sorted(set(payload) - allowed):
        fields[key] = "not_allowed"

    if mode != "preset_comparison":
        fields["mode"] = "preset_comparison"
    include_custom = payload.get("include_custom")
    if not isinstance(include_custom, bool):
        fields["include_custom"] = "boolean"

    raw_custom = payload.get("custom")
    if include_custom is False and raw_custom is not None:
        fields["custom"] = "not_allowed"
    if include_custom is True and not isinstance(raw_custom, Mapping):
        fields["custom"] = "object"
    if fields:
        raise TuningValidationError(fields)
    if include_custom is False:
        return TrainingTuningPlan(1, "preset_comparison", PRESET_PROFILES)

    assert isinstance(raw_custom, Mapping)
    custom_allowed = {
        "hgb_learning_rate",
        "hgb_max_iter",
        "rf_n_estimators",
        "recency_half_life_months",
    }
    for key in sorted(set(raw_custom) - custom_allowed):
        fields[f"custom.{key}"] = "not_allowed"

    if not _finite_float(raw_custom.get("hgb_learning_rate"), 0.01, 0.20):
        fields["custom.hgb_learning_rate"] = "number_0_01_to_0_20"
    if not _bounded_int(raw_custom.get("hgb_max_iter"), 100, 1000):
        fields["custom.hgb_max_iter"] = "integer_100_to_1000"
    if not _bounded_int(raw_custom.get("rf_n_estimators"), 100, 1000):
        fields["custom.rf_n_estimators"] = "integer_100_to_1000"

    resale_requested = "resale" in markets
    half_life = raw_custom.get("recency_half_life_months")
    if resale_requested:
        if not _bounded_int(half_life, 12, 84):
            fields[
                "custom.recency_half_life_months"
            ] = "integer_12_to_84"
    elif "recency_half_life_months" in raw_custom:
        fields["custom.recency_half_life_months"] = "not_applicable"

    if fields:
        raise TuningValidationError(fields)

    custom = TrainingProfile(
        name="custom",
        source="custom",
        hgb_learning_rate=float(raw_custom["hgb_learning_rate"]),
        hgb_max_iter=int(raw_custom["hgb_max_iter"]),
        rf_n_estimators=int(raw_custom["rf_n_estimators"]),
        recency_half_life_months=int(half_life) if resale_requested else None,
    )
    return TrainingTuningPlan(
        1,
        "preset_comparison",
        PRESET_PROFILES + (custom,),
    )
