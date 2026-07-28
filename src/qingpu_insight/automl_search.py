from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import optuna
from optuna.samplers import TPESampler

from qingpu_insight.model_training import (
    ModelExperiment,
    ModelFitSpec,
    RecentMedianBaseline,
    evaluate_fit_spec,
    evaluate_fitted_candidate,
    passes_release_gate,
)

TrialStateLit = Literal["completed", "rejected", "failed"]


def json_safe(value: object) -> object:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.ndarray):
        return [json_safe(v) for v in value.tolist()]
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


@dataclass(frozen=True)
class AutoMLTrialResult:
    trial_number: int
    state: TrialStateLit
    fit_spec: ModelFitSpec | None
    estimator: Any | None
    metrics: dict[str, dict[str, int | float]]
    overall_mae: float | None
    overall_mape: float | None
    station_mape: dict[str, float]
    calibration_passed: bool
    reason_codes: tuple[str, ...]
    duration_seconds: float

    def snapshot(self) -> dict[str, object]:
        return {
            "trial_number": self.trial_number,
            "state": self.state,
            "fit_spec": self.fit_spec.snapshot() if self.fit_spec else None,
            "metrics": json_safe(self.metrics),
            "overall_mae": self.overall_mae,
            "overall_mape": self.overall_mape,
            "station_mape": dict(self.station_mape),
            "calibration_passed": self.calibration_passed,
            "reason_codes": list(self.reason_codes),
            "duration_seconds": self.duration_seconds,
        }


@dataclass(frozen=True)
class AutoMLSearchResult:
    market: Literal["resale", "presale"]
    budget_name: Literal["quick", "standard", "deep"]
    budget_seconds: int
    max_trials: int
    seed: int
    elapsed_seconds: float
    stopped: bool
    trials: tuple[AutoMLTrialResult, ...]
    ranked_trials: tuple[AutoMLTrialResult, ...]
    shortlisted_trials: tuple[AutoMLTrialResult, ...]

    @property
    def completed_trials(self) -> int:
        return sum(1 for t in self.trials if t.state == "completed")

    @property
    def failed_trials(self) -> int:
        return sum(1 for t in self.trials if t.state == "failed")

    @property
    def rejected_trials(self) -> int:
        return sum(1 for t in self.trials if t.state == "rejected")


def rank_trials(
    trials: tuple[AutoMLTrialResult, ...],
) -> tuple[AutoMLTrialResult, ...]:
    return tuple(
        sorted(
            trials,
            key=lambda t: (
                not t.calibration_passed,
                t.overall_mae if t.overall_mae is not None else math.inf,
                t.overall_mape if t.overall_mape is not None else math.inf,
                t.trial_number,
            ),
        )
    )


def shortlist_trials(
    ranked_trials: tuple[AutoMLTrialResult, ...],
) -> tuple[AutoMLTrialResult, ...]:
    seen: set[str] = set()
    result: list[AutoMLTrialResult] = []
    for t in ranked_trials:
        if t.state != "completed" or not t.calibration_passed or t.fit_spec is None:
            continue
        key = json.dumps(t.fit_spec.snapshot(), sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        result.append(t)
        if len(result) >= 3:
            break
    return tuple(result)


def run_automl_search(
    split,
    plan,
    feature_columns,
    use_recency_weights: bool,
    baseline_months: int,
    *,
    should_stop: Callable[[], bool],
    on_progress: Callable[[dict], None],
    trial_evaluator: Callable[[ModelFitSpec], ModelExperiment] | None = None,
) -> AutoMLSearchResult:
    results: list[AutoMLTrialResult] = []
    start_time = time.monotonic()
    best_mae: float | None = None
    market: Literal["resale", "presale"] = "resale" if use_recency_weights else "presale"

    baseline_cal_eval = None
    if trial_evaluator is None:
        baseline = RecentMedianBaseline(months=baseline_months)
        baseline.fit(split.train)
        baseline_cal_eval = evaluate_fitted_candidate(
            "baseline", baseline, split.calibration, feature_columns=feature_columns
        )

    study = optuna.create_study(
        direction="minimize",
        sampler=TPESampler(seed=plan.seed),
    )

    def objective(trial: optuna.Trial) -> float:
        nonlocal best_mae

        if should_stop():
            raise optuna.TrialPruned()

        params: dict[str, Any] = {}
        spec: ModelFitSpec | None = None
        model_name = "unknown"
        trial_start = time.monotonic()

        try:
            model_name = trial.suggest_categorical(
                "model_name", ["random_forest", "hist_gradient_boosting"]
            )
            half_life = (
                trial.suggest_categorical(
                    "recency_half_life_months", [24, 36, 48, 60, 72]
                )
                if use_recency_weights
                else None
            )

            if model_name == "random_forest":
                params = {
                    "n_estimators": trial.suggest_int(
                        "rf_n_estimators", 100, 1000, step=50
                    ),
                    "min_samples_leaf": trial.suggest_int(
                        "rf_min_samples_leaf", 1, 20
                    ),
                    "max_features": trial.suggest_float(
                        "rf_max_features", 0.3, 1.0
                    ),
                    "criterion": trial.suggest_categorical(
                        "rf_criterion", ["squared_error", "absolute_error"]
                    ),
                }
            else:
                params = {
                    "learning_rate": trial.suggest_float(
                        "hgb_learning_rate", 0.01, 0.2, log=True
                    ),
                    "max_iter": trial.suggest_int(
                        "hgb_max_iter", 100, 1000, step=50
                    ),
                    "max_leaf_nodes": trial.suggest_int(
                        "hgb_max_leaf_nodes", 10, 255
                    ),
                    "l2_regularization": trial.suggest_float(
                        "hgb_l2_regularization", 1e-10, 10.0, log=True
                    ),
                }

            spec = ModelFitSpec(
                model_name=model_name,
                parameters=params,
                recency_half_life_months=half_life,
            )

            if trial_evaluator is not None:
                experiment = trial_evaluator(spec)
            else:
                experiment = evaluate_fit_spec(split, spec, feature_columns)

            duration = time.monotonic() - trial_start

            cal_eval = experiment.selection_results[0]
            overall_mae = cal_eval.overall_mae
            overall_mape = float(cal_eval.metrics.loc["overall", "mape"])
            metrics = {
                str(idx): row.to_dict()
                for idx, row in cal_eval.metrics.iterrows()
            }

            if baseline_cal_eval is not None:
                calibration_passed = passes_release_gate(
                    cal_eval, baseline_cal_eval
                )
            else:
                calibration_passed = experiment.recommended

            tr = AutoMLTrialResult(
                trial_number=trial.number,
                state="completed",
                fit_spec=spec,
                estimator=experiment.selected_estimator,
                metrics=metrics,
                overall_mae=overall_mae,
                overall_mape=overall_mape,
                station_mape=dict(cal_eval.station_mape),
                calibration_passed=calibration_passed,
                reason_codes=experiment.reason_codes,
                duration_seconds=duration,
            )
            results.append(tr)

            if best_mae is None or (
                overall_mae is not None and overall_mae < best_mae
            ):
                best_mae = overall_mae

            completed = sum(1 for r in results if r.state == "completed")
            failed = sum(1 for r in results if r.state == "failed")
            on_progress({
                "stage": "trial",
                "model_name": model_name,
                "completed_trials": completed,
                "failed_trials": failed,
                "elapsed_seconds": time.monotonic() - start_time,
                "best_mae": best_mae,
                "last_parameters": dict(params),
            })

            return overall_mae or 0.0

        except optuna.TrialPruned:
            raise
        except Exception:
            duration = time.monotonic() - trial_start
            tr = AutoMLTrialResult(
                trial_number=trial.number,
                state="failed",
                fit_spec=spec,
                estimator=None,
                metrics={},
                overall_mae=None,
                overall_mape=None,
                station_mape={},
                calibration_passed=False,
                reason_codes=(),
                duration_seconds=duration,
            )
            results.append(tr)

            completed = sum(1 for r in results if r.state == "completed")
            failed = sum(1 for r in results if r.state == "failed")
            on_progress({
                "stage": "trial",
                "model_name": model_name,
                "completed_trials": completed,
                "failed_trials": failed,
                "elapsed_seconds": time.monotonic() - start_time,
                "best_mae": best_mae,
                "last_parameters": dict(params) if params else {},
            })

            raise

    try:
        study.optimize(
            objective,
            timeout=plan.budget.seconds,
            n_trials=plan.budget.max_trials,
            n_jobs=1,
            catch=(Exception,),
        )
    except Exception:
        pass

    elapsed = time.monotonic() - start_time
    stopped = should_stop()

    all_trials = tuple(results)
    ranked = rank_trials(all_trials)
    shortlisted = shortlist_trials(ranked)

    return AutoMLSearchResult(
        market=market,
        budget_name=plan.budget.name,
        budget_seconds=plan.budget.seconds,
        max_trials=plan.budget.max_trials,
        seed=plan.seed,
        elapsed_seconds=elapsed,
        stopped=stopped,
        trials=all_trials,
        ranked_trials=ranked,
        shortlisted_trials=shortlisted,
    )
