from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import joblib
import pandas as pd

from qingpu_insight.automl_control import AutoMLControlRegistry
from qingpu_insight.automl_outputs import AutoMLRunOutputStore
from qingpu_insight.automl_search import run_automl_search
from qingpu_insight.community_features import (
    add_historical_community_features,
    build_community_feature_snapshot,
)
from qingpu_insight.community_registry import CommunityRegistry
from qingpu_insight.job_executor import LocalJobExecutor
from qingpu_insight.jobs import JobService, JobSubmission
from qingpu_insight.model_analysis import (
    build_resale_diagnostics,
    evaluate_release_checks,
    release_reason_codes,
    run_annual_backtests,
    run_feature_experiments,
    run_shared_feature_experiments,
)
from qingpu_insight.model_artifacts import (
    AutoMLMarketSearchSnapshot,
    AutoMLRunSnapshot,
    AutoMLTrialSnapshot,
    CandidateArtifactStore,
    DataSnapshot,
    MarketTrainingResult,
    ProfileTrainingResult,
    TrainingManifest,
    TrainingProfileSnapshot,
    sha256_file,
)
from qingpu_insight.model_features import (
    BASE_FEATURE_COLUMNS,
    COMMUNITY_FEATURE_COLUMNS,
    FEATURE_COLUMNS,
    build_model_frame,
)
from qingpu_insight.model_training import (
    BaselineEvaluationError,
    ModelFitSpec,
    ProfileEvaluationError,
    evaluate_fit_spec,
    leakage_audit,
    run_tuned_model_experiment,
    split_by_time,
)
from qingpu_insight.model_tuning import (
    AutoMLTuningPlan,
    TrainingPlan,
    parse_tuning_plan,
)
from qingpu_insight.valuation import ValuationBundle, train_artifact
from qingpu_insight.valuation_reporting import (
    compute_interval_summary,
    write_evaluation,
    write_model_card,
)


class ModelTrainingError(Exception):
    def __init__(self, error_code: str, safe_message: str) -> None:
        self.error_code = error_code
        self.safe_message = safe_message
        super().__init__(safe_message)


STABLE_ERRORS = {
    "training_data_missing",
    "training_data_invalid",
    "training_data_insufficient",
    "baseline_failed",
    "candidate_write_failed",
    "candidate_validation_failed",
}


@dataclass(frozen=True)
class SourceVersionProvider:
    commit: str
    dirty: bool

    def read(self) -> SourceVersionProvider:
        return self


def runtime_versions() -> dict[str, str]:
    import numpy as np
    import pandas as pd
    import sklearn

    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scikit_learn": sklearn.__version__,
    }


def build_data_snapshot(input_path: Path, frame: pd.DataFrame) -> DataSnapshot:
    sha = sha256_file(input_path)
    raw_count = len(frame)
    usable = frame[frame["analysis_eligible"]]
    usable_counts: dict[str, int] = {}
    excluded_counts: dict[str, int] = {}
    for market in ("resale", "presale"):
        market_frame = frame[frame["transaction_type"] == market]
        usable_market = market_frame[market_frame["analysis_eligible"]]
        usable_counts[market] = len(usable_market)
        excluded_counts[market] = len(market_frame) - len(usable_market)
    station_counts: dict[str, int] = usable["station_code"].value_counts().to_dict()
    for s in ("A17", "A18", "A19"):
        station_counts.setdefault(s, 0)
    min_date = usable["transaction_date"].min().date()
    max_date = usable["transaction_date"].max().date()
    return DataSnapshot(
        sha256=sha,
        raw_count=raw_count,
        usable_counts=usable_counts,
        excluded_counts=excluded_counts,
        station_counts=station_counts,
        min_date=min_date,
        max_date=max_date,
    )


def market_result_from_files(
    market: Literal["resale", "presale"],
    bundle: ValuationBundle,
    experiment: Any,
    artifact_path: Path,
    evaluation_path: Path,
    card_path: Path,
    stage: Path,
    selected_profile: str | None = None,
    profile_results: list[ProfileTrainingResult] | None = None,
    diagnostics: dict[str, object] | None = None,
    feature_experiments: list[dict[str, object]] | None = None,
    backtests: list[dict[str, object]] | None = None,
    release_checks: dict[str, bool] | None = None,
    feature_columns: list[str] | None = None,
    feature_contract_version: int = 0,
    test_coverage: float | None = None,
    average_interval_width_twd_per_ping: float | None = None,
    parking_policy: dict[str, object] | None = None,
    model_name: str | None = None,
) -> MarketTrainingResult:
    selection_metrics: dict[str, dict[str, object]] = {}
    if hasattr(experiment, "profile_results"):
        for profile_eval in experiment.profile_results:
            for candidate in profile_eval.candidates:
                selection_metrics[f"{profile_eval.profile.name}:{candidate.evaluation.name}"] = (
                    candidate.evaluation.metrics.to_dict(orient="index")
                )

    final_test_metrics: dict[str, dict[str, object]] = {}
    for name, c in experiment.final_test_results.items():
        final_test_metrics[name] = c.metrics.to_dict(orient="index")

    report_files: dict[str, str] = {}
    report_sha256: dict[str, str] = {}
    for report_type, rel in (
        (f"{market}-evaluation", str(evaluation_path.relative_to(stage))),
        (f"{market}-model-card", str(card_path.relative_to(stage))),
    ):
        report_files[report_type] = rel
        report_sha256[report_type] = sha256_file(stage / rel)

    selected_model_name = (
        model_name
        or getattr(experiment, "selected_model", None)
        or getattr(experiment, "selected_name", bundle.model_name)
    )

    return MarketTrainingResult(
        market=market,
        selected_model=selected_model_name,
        recommended=(
            release_checks.get("recommended", experiment.recommended)
            if release_checks
            else experiment.recommended
        ),
        reason_codes=(
            release_reason_codes(release_checks)
            if release_checks
            else list(experiment.reason_codes)
        ),
        selection_metrics=selection_metrics,
        final_test_metrics=final_test_metrics,
        artifact_file=artifact_path.name,
        artifact_sha256=sha256_file(artifact_path),
        report_files=report_files,
        report_sha256=report_sha256,
        selected_profile=selected_profile,
        profile_results=profile_results or [],
        test_coverage=test_coverage,
        average_interval_width_twd_per_ping=average_interval_width_twd_per_ping,
        feature_contract_version=feature_contract_version,
        feature_columns=feature_columns or [],
        diagnostics=diagnostics or {},
        feature_experiments=feature_experiments or [],
        backtests=backtests or [],
        release_checks=release_checks or {},
        parking_policy=parking_policy,
    )


def public_training_summary(manifest: TrainingManifest) -> dict[str, object]:
    return {
        "run_id": str(manifest.run_id),
        "markets": manifest.markets,
        "data_snapshot": {
            "raw_count": manifest.data_snapshot.raw_count,
            "usable_counts": manifest.data_snapshot.usable_counts,
        },
        "results": [
            {
                "market": r.market,
                "selected_model": r.selected_model,
                "recommended": r.recommended,
                "reason_codes": r.reason_codes,
                "selected_profile": r.selected_profile,
            }
            for r in manifest.results
        ],
    }


class ModelTrainingRequest:
    SUPPORTED = frozenset({"resale", "presale"})

    def __init__(
        self,
        markets: tuple[Literal["resale", "presale"], ...],
        trigger: str = "web",
        tuning_plan: TrainingPlan | None = None,
    ) -> None:
        if not markets:
            raise ValueError("markets must not be empty")
        seen = set()
        for m in markets:
            if m not in self.SUPPORTED:
                raise ValueError(f"unsupported market: {m}")
            if m in seen:
                raise ValueError(f"duplicate market: {m}")
            seen.add(m)
        # canonicalise
        ordered = [m for m in ("resale", "presale") if m in seen]
        self._markets = tuple(ordered)
        self.trigger = trigger
        self.tuning_plan = tuning_plan or parse_tuning_plan(self._markets, None)

    @property
    def markets(self) -> tuple[Literal["resale", "presale"], ...]:
        return self._markets

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ModelTrainingRequest):
            return NotImplemented
        return (
            self.markets == other.markets
            and self.trigger == other.trigger
            and self.tuning_plan == other.tuning_plan
        )

    def __repr__(self) -> str:
        return (
            f"ModelTrainingRequest(markets={self.markets}, "
            f"trigger={self.trigger!r}, tuning_plan={self.tuning_plan})"
        )


class ModelTrainingService:
    def __init__(
        self,
        jobs: JobService,
        store: CandidateArtifactStore,
        input_path: Path,
        source_version_provider: SourceVersionProvider,
        clock: Any | None = None,
        automl_registry: AutoMLControlRegistry | None = None,
        automl_output_store: AutoMLRunOutputStore | None = None,
    ) -> None:
        self._jobs = jobs
        self._store = store
        self._input_path = input_path
        self._source_version_provider = source_version_provider
        self._clock = clock or (lambda: datetime.now())
        self._automl_registry = automl_registry or AutoMLControlRegistry()
        self._automl_output_store = automl_output_store

    def _merge_market_quality_diagnostics(
        self,
        diagnostics: dict[str, object],
    ) -> dict[str, object]:
        quality_path = next(
            (
                parent / "outputs" / "reports" / "m1-market-quality.json"
                for parent in self._input_path.resolve().parents
                if (parent / "outputs" / "reports" / "m1-market-quality.json").exists()
            ),
            None,
        )
        if quality_path is None:
            return diagnostics
        try:
            payload = json.loads(quality_path.read_text(encoding="utf-8"))
            exclusions = payload.get("exclusion_reasons", {})
        except (OSError, json.JSONDecodeError, AttributeError):
            return diagnostics
        data_quality = diagnostics.get("data_quality")
        if not isinstance(data_quality, dict) or not isinstance(exclusions, dict):
            return diagnostics
        data_quality["special_relationship_excluded"] = int(
            exclusions.get("special_relationship", 0)
        )
        data_quality["non_market_subject_excluded"] = int(
            exclusions.get("non_market_subject", 0)
        )
        return diagnostics

    def _evaluate_shared_feature_release_gate(
        self,
        candidate_metrics: dict[str, object],
        baseline_metrics: dict[str, object],
        backtests: list[dict[str, object]],
        has_shared_features: bool,
        test_coverage: float | None = None,
        validation_evidence: dict[str, object] | None = None,
        registry_version: str | None = None,
    ) -> dict[str, bool]:
        checks: dict[str, bool] = {}
        if not has_shared_features:
            checks["shared_feature_gate_passed"] = True
            return checks

        overall_mae_improved_2pct = (
            candidate_metrics.get("overall", {}).get("mae", float("inf"))
            <= baseline_metrics.get("overall", {}).get("mae", float("inf")) * 0.98
        )
        mape_not_worsened = (
            candidate_metrics.get("overall", {}).get("mape", float("inf"))
            <= baseline_metrics.get("overall", {}).get("mape", float("inf"))
        )
        stations_ok = all(
            candidate_metrics.get(f"station:{s}", {}).get("mape", float("inf"))
            <= baseline_metrics.get(f"station:{s}", {}).get("mape", float("inf")) + 1.0
            for s in ("A17", "A18", "A19")
        )
        backtests_ok = len(backtests) >= 2 and sum(
            1 for bt in backtests if bt.get("passed")
        ) >= 2

        pi_coverage_ok = test_coverage is None or test_coverage >= 0.90

        validation_ok = True
        if validation_evidence is not None:
            labeled_pages = validation_evidence.get("labeled_pages", 0)
            validation_ok = labeled_pages >= 20
            parsing_success = validation_evidence.get("public_area_parsing_success", 0.0)
            validation_ok = validation_ok and parsing_success >= 0.70
            community_recognition = validation_evidence.get("known_community_recognition", 0.0)
            validation_ok = validation_ok and community_recognition >= 0.80
            if registry_version is not None:
                evidence_digest = validation_evidence.get("registry_digest")
                if (evidence_digest is not None and registry_version is not None
                        and evidence_digest != registry_version):
                    validation_ok = False
        else:
            validation_ok = False

        passed = all((
            overall_mae_improved_2pct,
            mape_not_worsened,
            stations_ok,
            backtests_ok,
            pi_coverage_ok,
            validation_ok,
        ))

        checks["shared_mae_improved_2pct"] = overall_mae_improved_2pct
        checks["shared_mape_not_worsened"] = mape_not_worsened
        checks["shared_stations_within_limit"] = stations_ok
        checks["shared_backtests_passed"] = backtests_ok
        checks["shared_pi_coverage_ok"] = pi_coverage_ok
        checks["shared_validation_ok"] = validation_ok
        checks["shared_feature_gate_passed"] = passed
        return checks

    def _finalize_candidate(
        self,
        run_id: str,
        market: str,
        stage: Path,
        locked_eval: Any,
        model_name: str,
        split: Any,
        experiment: Any,
        seed_bundle: ValuationBundle,
        enhanced_features: tuple[str, ...],
        model_frame: pd.DataFrame,
        is_resale: bool,
        recency_half_life_months: int,
        diagnostics: dict[str, object] | None = None,
        analysis_experiments: list[dict[str, object]] | None = None,
        selected_profile: Any | None = None,
        profile_results: list[ProfileTrainingResult] | None = None,
        automl_info: dict[str, object] | None = None,
        fit_spec: ModelFitSpec | None = None,
        feature_contract_version: int = 0,
        fallback_profiles: tuple | None = None,
        validation_evidence: dict[str, object] | None = None,
    ) -> MarketTrainingResult:
        diagnostics = diagnostics or {}
        analysis_experiments = analysis_experiments or []
        final_evaluation = experiment.final_test_results[model_name]

        try:
            artifact_path = train_artifact(
                market,
                locked_eval,
                split,
                seed_bundle,
                stage,
                feature_columns=enhanced_features,
                training_frame=model_frame if is_resale else None,
                use_recency_weights=is_resale,
                recency_half_life_months=recency_half_life_months,
                reporting_metrics=final_evaluation.metrics.to_dict(orient="index"),
                reporting_diagnostics=diagnostics if is_resale else None,
            )
            bundle: ValuationBundle = joblib.load(artifact_path)
            bundle.community_registry_version = seed_bundle.community_registry_version
            bundle.community_registry_rows = seed_bundle.community_registry_rows
            bundle.community_feature_snapshot = seed_bundle.community_feature_snapshot
            shared_exp = None
            if analysis_experiments:
                for exp in analysis_experiments:
                    if isinstance(exp, dict) and exp.get("name") == "shared_feature_experiment":
                        shared_exp = exp
                        break
            bundle.shared_feature_experiment = shared_exp
            joblib.dump(bundle, artifact_path)
            parking_policy_dict = None
            if bundle.parking_price_policy is not None:
                pp = bundle.parking_price_policy
                parking_policy_dict = {
                    "version": pp.version,
                    "minimum_type_samples": pp.minimum_type_samples,
                    "by_type": {
                        k: {
                            "price_twd": int(v.price_twd),
                            "sample_size": int(v.sample_size),
                        }
                        for k, v in pp.by_type.items()
                    },
                    "market_fallback": (
                        {
                            "price_twd": int(pp.market_fallback.price_twd),
                            "sample_size": int(pp.market_fallback.sample_size),
                        }
                        if pp.market_fallback
                        else None
                    ),
                }
        except Exception as exc:
            raise ModelTrainingError("candidate_write_failed", str(exc)) from exc

        interval_summary = compute_interval_summary(
            bundle,
            experiment.final_test_results[model_name],
            split,
        )
        test_coverage = interval_summary["test_coverage"]

        serialized_backtests: list[dict[str, object]] = []
        release_checks: dict[str, bool] = {}
        if is_resale:
            try:
                raw_backtests = run_annual_backtests(
                    model_frame,
                    model_name,
                    enhanced_features,
                    fit_spec=fit_spec,
                )
                serialized_backtests = []
                for bt in raw_backtests:
                    bt_copy = dict(bt)
                    for key in (
                        "cutoff_date",
                        "train_max_date",
                        "test_min_date",
                        "source_max_date",
                    ):
                        if key in bt_copy and isinstance(bt_copy[key], pd.Timestamp):
                            bt_copy[key] = str(bt_copy[key].date())
                    serialized_backtests.append(bt_copy)

                baseline_metrics = experiment.final_test_results["baseline"].metrics.to_dict(
                    orient="index"
                )
                candidate_metrics = experiment.final_test_results[model_name].metrics.to_dict(
                    orient="index"
                )
                data_max_ts = pd.Timestamp(bundle.data_max_date)
                latest_official_ts = pd.Timestamp(model_frame["transaction_date"].max())
                release_checks = evaluate_release_checks(
                    candidate_metrics,
                    baseline_metrics,
                    serialized_backtests,
                    data_max_ts,
                    latest_official_ts,
                )
                has_shared = bool(
                    any(col in enhanced_features for col in COMMUNITY_FEATURE_COLUMNS)
                )
                shared_checks = self._evaluate_shared_feature_release_gate(
                    candidate_metrics,
                    baseline_metrics,
                    serialized_backtests,
                    has_shared,
                    test_coverage=test_coverage,
                    validation_evidence=validation_evidence,
                    registry_version=seed_bundle.community_registry_version,
                )
                release_checks.update(shared_checks)

                shared_gate_passed = shared_checks.get("shared_feature_gate_passed", True)
                if (is_resale and has_shared and not shared_gate_passed
                        and fallback_profiles is not None):
                    from qingpu_insight.model_training import run_tuned_model_experiment

                    fallback_exp = run_tuned_model_experiment(
                        split,
                        profiles=fallback_profiles,
                        feature_columns=FEATURE_COLUMNS,
                        use_recency_weights=True,
                        baseline_months=12,
                    )

                    model_name = fallback_exp.selected_model
                    locked_eval = fallback_exp.selected_evaluation
                    enhanced_features = FEATURE_COLUMNS
                    feature_contract_version = 3
                    release_checks["shared_feature_fallback"] = True
                    release_checks["shared_feature_fallback_reason"] = (
                        "shared_feature_gate_failed_fallback_to_baseline_v3"
                    )

                    fallback_bundle = ValuationBundle(
                        transaction_type=market,
                        model_name="",
                        model_version="",
                        pipeline=None,
                        interval_abs_residual_twd_per_ping=0,
                        feature_ranges={},
                        feature_hard_ranges={},
                        feature_medians={},
                        global_importance=[],
                        reference_rows=pd.DataFrame(),
                        data_min_date=str(split.train["transaction_date"].min().date()),
                        data_max_date=str(split.train["transaction_date"].max().date()),
                        metrics={},
                        feature_columns=enhanced_features,
                        community_registry_version=seed_bundle.community_registry_version,
                        community_registry_rows=seed_bundle.community_registry_rows,
                        community_feature_snapshot=seed_bundle.community_feature_snapshot,
                    )

                    reporting_metrics = (
                        fallback_exp.final_test_results[model_name].metrics.to_dict(orient="index")
                    )
                    new_artifact_path = train_artifact(
                        market,
                        locked_eval,
                        split,
                        fallback_bundle,
                        stage,
                        feature_columns=enhanced_features,
                        training_frame=model_frame,
                        use_recency_weights=True,
                        recency_half_life_months=recency_half_life_months,
                        reporting_metrics=reporting_metrics,
                        reporting_diagnostics=diagnostics,
                    )
                    bundle = joblib.load(new_artifact_path)
                    bundle.community_registry_version = seed_bundle.community_registry_version
                    bundle.community_registry_rows = seed_bundle.community_registry_rows
                    bundle.community_feature_snapshot = seed_bundle.community_feature_snapshot
                    shared_exp = None
                    if analysis_experiments:
                        for exp in analysis_experiments:
                            if (
                                isinstance(exp, dict)
                                and exp.get("name") == "shared_feature_experiment"
                            ):
                                shared_exp = exp
                                break
                    bundle.shared_feature_experiment = shared_exp
                    joblib.dump(bundle, new_artifact_path)

                    raw_backtests = run_annual_backtests(
                        model_frame,
                        model_name,
                        enhanced_features,
                        fit_spec=fit_spec,
                    )
                    serialized_backtests = []
                    for bt in raw_backtests:
                        bt_copy = dict(bt)
                        for key in (
                            "cutoff_date",
                            "train_max_date",
                            "test_min_date",
                            "source_max_date",
                        ):
                            if key in bt_copy and isinstance(bt_copy[key], pd.Timestamp):
                                bt_copy[key] = str(bt_copy[key].date())
                        serialized_backtests.append(bt_copy)

                    baseline_metrics = (
                        fallback_exp.final_test_results["baseline"].metrics.to_dict(orient="index")
                    )
                    candidate_metrics = (
                        fallback_exp.final_test_results[model_name].metrics.to_dict(orient="index")
                    )
                    release_checks = evaluate_release_checks(
                        candidate_metrics,
                        baseline_metrics,
                        serialized_backtests,
                        data_max_ts,
                        latest_official_ts,
                    )
                    artifact_path = new_artifact_path
                    experiment = fallback_exp
                    interval_summary = compute_interval_summary(
                        bundle,
                        experiment.final_test_results[model_name],
                        split,
                    )
                    test_coverage = interval_summary["test_coverage"]
            except Exception as exc:
                raise ModelTrainingError("candidate_write_failed", str(exc)) from exc

        parking_policy = bundle.parking_price_policy
        parking_consistent = bool(
            "parking_type" not in bundle.feature_columns
            and "parking_area_ping" not in bundle.feature_columns
            and parking_policy is not None
            and parking_policy.market_fallback is not None
            and parking_policy.market_fallback.price_twd > 0
        )
        release_checks["parking_price_consistency"] = parking_consistent
        shared_gate = release_checks.get("shared_feature_gate_passed", True)
        release_checks["recommended"] = bool(
            release_checks.get("recommended", experiment.recommended)
            and parking_consistent
            and shared_gate
        )

        self._jobs.progress(
            run_id,
            {
                "stage": f"evaluating_{market}",
                "completed_markets": [],
            },
        )

        try:
            report_dir = stage / "reports"
            evaluation_path = write_evaluation(
                bundle,
                experiment,
                split,
                report_dir,
                selected_profile=selected_profile,
                diagnostics=diagnostics if is_resale else None,
                feature_experiments=(analysis_experiments if is_resale else None),
                backtests=(serialized_backtests if is_resale else None),
                release_checks=release_checks,
                reason_codes=release_reason_codes(release_checks),
                automl_info=automl_info,
            )
            card_path = write_model_card(
                bundle,
                experiment,
                leakage_audit(split),
                report_dir,
                selected_profile=selected_profile,
                feature_experiments=(analysis_experiments if is_resale else None),
                backtests=(serialized_backtests if is_resale else None),
                release_checks=release_checks,
                reason_codes=release_reason_codes(release_checks),
                automl_info=automl_info,
            )
            interval_summary = compute_interval_summary(
                bundle,
                experiment.final_test_results[model_name],
                split,
            )
        except Exception as exc:
            raise ModelTrainingError("candidate_write_failed", str(exc)) from exc

        try:
            return market_result_from_files(
                market=market,
                bundle=bundle,
                experiment=experiment,
                artifact_path=artifact_path,
                evaluation_path=evaluation_path,
                card_path=card_path,
                stage=stage,
                selected_profile=(selected_profile.name if selected_profile is not None else None),
                profile_results=profile_results or [],
                diagnostics=diagnostics if is_resale else None,
                feature_experiments=(analysis_experiments if is_resale else None),
                backtests=(serialized_backtests if is_resale else None),
                release_checks=release_checks,
                feature_columns=list(enhanced_features),
                feature_contract_version=feature_contract_version,
                test_coverage=interval_summary["test_coverage"],
                average_interval_width_twd_per_ping=(
                    interval_summary["average_interval_width_twd_per_ping"]
                ),
                parking_policy=parking_policy_dict,
                model_name=model_name,
            )
        except Exception as exc:
            raise ModelTrainingError("candidate_validation_failed", str(exc)) from exc

    def _execute_guided_market(
        self,
        run_id: str,
        market: str,
        frame: pd.DataFrame,
        stage: Path,
        plan: TrainingPlan,
    ) -> MarketTrainingResult:
        is_resale = market == "resale"
        model_frame = build_model_frame(frame, market)

        registry: CommunityRegistry | None = None
        if is_resale:
            registry_path = Path("data/reference/qingpu_communities.csv")
            if registry_path.exists():
                registry = CommunityRegistry.from_csv(registry_path)
                model_frame = add_historical_community_features(model_frame, registry)

        split = split_by_time(model_frame)

        diagnostics: dict[str, object] = {}
        analysis_experiments: list[dict[str, object]] = []
        enhanced_features: tuple[str, ...] = BASE_FEATURE_COLUMNS
        feature_contract_ver = 0

        try:
            if is_resale:
                exp_list = run_feature_experiments(split)
                analysis_experiments = [
                    {
                        "name": fe.name,
                        "feature_columns": list(fe.feature_columns),
                        "selected_model": fe.selected_model,
                        "metrics": fe.metrics,
                        "candidate_errors": fe.candidate_errors,
                    }
                    for fe in exp_list
                ]

                shared_result = run_shared_feature_experiments(split)
                locked_features = shared_result.locked_feature_columns
                has_shared_features = any(
                    col in locked_features for col in COMMUNITY_FEATURE_COLUMNS
                )
                enhanced_features = locked_features
                analysis_experiments.append({
                    "name": "shared_feature_experiment",
                    "locked_feature_set_name": shared_result.locked_feature_set_name,
                    "locked_feature_columns": list(locked_features),
                    "selection_reason": shared_result.selection_reason,
                    "calibration_experiments": [
                        {"name": e.name, "selected_model": e.selected_model}
                        for e in shared_result.calibration_experiments
                    ],
                })
                feature_contract_ver = 4 if has_shared_features else 3

            experiment = run_tuned_model_experiment(
                split,
                profiles=plan.profiles,
                feature_columns=(enhanced_features if is_resale else BASE_FEATURE_COLUMNS),
                use_recency_weights=is_resale,
                baseline_months=12 if is_resale else 24,
                on_profile_start=lambda pn, _m=market: self._jobs.progress(
                    run_id,
                    {
                        "stage": f"training_{_m}",
                        "profile": pn,
                        "completed_markets": [],
                    },
                ),
            )
        except ProfileEvaluationError as exc:
            raise ModelTrainingError(
                "profile_failed",
                f"{market} 設定檔 {exc.profile_name} 無法完成",
            ) from exc
        except BaselineEvaluationError as exc:
            raise ModelTrainingError("baseline_failed", str(exc)) from exc

        locked = experiment.selected_evaluation
        winning_profile = next(p for p in plan.profiles if p.name == experiment.selected_profile)

        if is_resale:
            diagnostics = build_resale_diagnostics(
                model_frame,
                split,
                candidate=experiment.final_test_results[experiment.selected_model],
                feature_columns=enhanced_features,
                source_frame=frame,
            )
            diagnostics = self._merge_market_quality_diagnostics(diagnostics)

        selected_profile_obj = winning_profile
        profile_results = [
            ProfileTrainingResult(
                profile_name=pe.profile.name,
                parameters={
                    "hgb_learning_rate": pe.profile.hgb_learning_rate,
                    "hgb_max_iter": pe.profile.hgb_max_iter,
                    "rf_n_estimators": pe.profile.rf_n_estimators,
                    "recency_half_life_months": pe.profile.recency_half_life_months,
                },
                selection_metrics={
                    c.model_name: c.evaluation.metrics.to_dict(orient="index")
                    for c in pe.candidates
                },
                candidate_errors=pe.candidate_errors,
            )
            for pe in experiment.profile_results
        ]

        registry_version: str | None = None
        registry_rows: tuple[dict[str, object], ...] = ()
        if is_resale and registry is not None:
            registry_version = registry.version
            registry_rows = tuple(registry._data.to_dict(orient="records"))

        data_max_date = str(
            (model_frame if is_resale else split.train)["transaction_date"].max().date()
        )
        community_snapshot = (
            build_community_feature_snapshot(
                model_frame, cutoff=pd.Timestamp(data_max_date)
            )
            if is_resale and registry is not None
            else None
        )

        seed_bundle = ValuationBundle(
            transaction_type=market,
            model_name="",
            model_version="",
            pipeline=None,
            interval_abs_residual_twd_per_ping=0,
            feature_ranges={},
            feature_hard_ranges={},
            feature_medians={},
            global_importance=[],
            reference_rows=pd.DataFrame(),
            data_min_date="",
            data_max_date=data_max_date,
            metrics={},
            feature_columns=enhanced_features,
            community_registry_version=registry_version,
            community_registry_rows=registry_rows,
            community_feature_snapshot=community_snapshot,
        )

        return self._finalize_candidate(
            run_id=run_id,
            market=market,
            stage=stage,
            locked_eval=locked,
            model_name=experiment.selected_model,
            split=split,
            experiment=experiment,
            seed_bundle=seed_bundle,
            enhanced_features=enhanced_features,
            model_frame=model_frame,
            is_resale=is_resale,
            recency_half_life_months=(winning_profile.recency_half_life_months or 48),
            diagnostics=diagnostics,
            analysis_experiments=analysis_experiments,
            selected_profile=selected_profile_obj,
            profile_results=profile_results,
            feature_contract_version=feature_contract_ver,
            fallback_profiles=plan.profiles if is_resale and has_shared_features else None,
        )

    def _execute_automl_market(
        self,
        run_id: str,
        market: str,
        frame: pd.DataFrame,
        stage: Path,
        plan: AutoMLTuningPlan,
    ) -> tuple[MarketTrainingResult | None, AutoMLMarketSearchSnapshot]:
        is_resale = market == "resale"
        model_frame = build_model_frame(frame, market)

        registry: CommunityRegistry | None = None
        if is_resale:
            registry_path = Path("data/reference/qingpu_communities.csv")
            if registry_path.exists():
                registry = CommunityRegistry.from_csv(registry_path)
                model_frame = add_historical_community_features(model_frame, registry)

        split = split_by_time(model_frame)
        diagnostics: dict[str, object] = {}
        analysis_experiments: list[dict[str, object]] = []
        enhanced_features: tuple[str, ...] = BASE_FEATURE_COLUMNS
        if is_resale:
            exp_list = run_feature_experiments(split)
            analysis_experiments = [
                {
                    "name": fe.name,
                    "feature_columns": list(fe.feature_columns),
                    "selected_model": fe.selected_model,
                    "metrics": fe.metrics,
                    "candidate_errors": fe.candidate_errors,
                }
                for fe in exp_list
            ]
            shared_result = run_shared_feature_experiments(split)
            locked_features = shared_result.locked_feature_columns
            enhanced_features = locked_features
            analysis_experiments.append({
                "name": "shared_feature_experiment",
                "locked_feature_set_name": shared_result.locked_feature_set_name,
                "locked_feature_columns": list(locked_features),
                "selection_reason": shared_result.selection_reason,
                "calibration_experiments": [
                    {"name": e.name, "selected_model": e.selected_model}
                    for e in shared_result.calibration_experiments
                ],
            })
        feature_columns = list(enhanced_features) if is_resale else list(BASE_FEATURE_COLUMNS)
        self._jobs.progress(
            run_id,
            {
                "mode": "automl",
                "stage": f"automl_search_{market}",
                "market": market,
            },
        )
        search_result = run_automl_search(
            split,
            plan,
            feature_columns,
            use_recency_weights=is_resale,
            baseline_months=12 if is_resale else 24,
            should_stop=lambda: self._automl_registry.should_stop(run_id),
            on_progress=lambda p: self._jobs.progress(
                run_id,
                {
                    "mode": "automl",
                    "stage": f"automl_trial_{market}",
                    "market": market,
                    **p,
                },
            ),
        )
        if self._automl_output_store is not None:
            snapshot_dict = {
                "budget_name": search_result.budget_name,
                "budget_seconds": search_result.budget_seconds,
                "max_trials": search_result.max_trials,
                "completed_trials": search_result.completed_trials,
                "failed_trials": search_result.failed_trials,
                "seed": search_result.seed,
                "stopped": search_result.stopped,
                "elapsed_seconds": search_result.elapsed_seconds,
                "trials": [t.snapshot() for t in search_result.trials],
                "ranked_trials": [t.snapshot() for t in search_result.ranked_trials],
                "shortlisted_trials": [t.snapshot() for t in search_result.shortlisted_trials],
            }
            self._automl_output_store.write(run_id, market, snapshot_dict)
        if search_result.stopped or self._automl_registry.should_stop(run_id):
            market_snapshot = self._build_market_snapshot(
                search_result,
                [],
                None,
                [],
                stopped=True,
                trial_file="",
                trial_sha256="",
            )
            return (None, market_snapshot)
        if search_result.completed_trials == 0:
            raise ModelTrainingError(
                "automl_all_trials_failed",
                f"{market} 的 AutoML 試驗全部失敗",
            )
        shortlisted = list(search_result.shortlisted_trials)
        self._jobs.progress(
            run_id,
            {
                "mode": "automl",
                "stage": f"validating_shortlist_{market}",
                "market": market,
                "shortlist_count": len(shortlisted),
                "shortlisted_trial_numbers": [t.trial_number for t in shortlisted],
            },
        )
        release_blockers: list[str] = []
        for trial in shortlisted:
            if self._automl_registry.should_stop(run_id):
                return (
                    None,
                    self._build_market_snapshot(
                        search_result,
                        shortlisted,
                        None,
                        release_blockers,
                        stopped=True,
                        trial_file="",
                        trial_sha256="",
                    ),
                )
            if trial.fit_spec is None:
                continue

            trial_experiment = evaluate_fit_spec(
                split,
                trial.fit_spec,
                feature_columns,
                baseline_months=12 if is_resale else 24,
            )
            locked_eval = trial_experiment.selection_results[0]
            model_name = trial_experiment.selected_name
            if is_resale:
                diagnostics = build_resale_diagnostics(
                    model_frame,
                    split,
                    candidate=trial_experiment.final_test_results[model_name],
                    feature_columns=tuple(feature_columns),
                    source_frame=frame,
                )
                diagnostics = self._merge_market_quality_diagnostics(diagnostics)
            registry_version: str | None = None
            registry_rows: tuple[dict[str, object], ...] = ()
            if is_resale and registry is not None:
                registry_version = registry.version
                registry_rows = tuple(registry._data.to_dict(orient="records"))

            data_max_date = str(
                (model_frame if is_resale else split.train)["transaction_date"].max().date()
            )
            community_snapshot = (
                build_community_feature_snapshot(
                    model_frame, cutoff=pd.Timestamp(data_max_date)
                )
                if is_resale and registry is not None
                else None
            )

            seed_bundle = ValuationBundle(
                transaction_type=market,
                model_name="",
                model_version="",
                pipeline=None,
                interval_abs_residual_twd_per_ping=0,
                feature_ranges={},
                feature_hard_ranges={},
                feature_medians={},
                global_importance=[],
                reference_rows=pd.DataFrame(),
                data_min_date="",
                data_max_date=data_max_date,
                metrics={},
                feature_columns=tuple(feature_columns),
                community_registry_version=registry_version,
                community_registry_rows=registry_rows,
                community_feature_snapshot=community_snapshot,
            )
            recency_half_life = trial.fit_spec.recency_half_life_months or 48
            has_shared = is_resale and any(
                col in enhanced_features for col in COMMUNITY_FEATURE_COLUMNS
            )
            fcv = 4 if has_shared else (3 if is_resale else 0)
            result = self._finalize_candidate(
                run_id=run_id,
                market=market,
                stage=stage,
                locked_eval=locked_eval,
                model_name=model_name,
                split=split,
                experiment=trial_experiment,
                seed_bundle=seed_bundle,
                enhanced_features=tuple(feature_columns),
                model_frame=model_frame,
                is_resale=is_resale,
                recency_half_life_months=recency_half_life,
                diagnostics=diagnostics,
                analysis_experiments=analysis_experiments,
                automl_info={
                    "mode": "automl",
                    "budget_name": search_result.budget_name,
                    "budget_seconds": search_result.budget_seconds,
                    "completed_trials": search_result.completed_trials,
                    "selected_trial_number": trial.trial_number,
                    "fit_spec": trial.fit_spec.snapshot(),
                    "release_blockers": [],
                },
                fit_spec=trial.fit_spec,
                feature_contract_version=fcv,
            )
            if not result.recommended:
                release_blockers.extend(result.reason_codes)
                continue
            trial_file_rel, trial_file_sha = "", ""
            if self._automl_output_store is not None:
                trial_file_rel, trial_file_sha = self._automl_output_store.copy_trials_to(
                    run_id, market, stage
                )
            market_snapshot = self._build_market_snapshot(
                search_result,
                shortlisted,
                trial.trial_number,
                [],
                stopped=False,
                trial_file=trial_file_rel,
                trial_sha256=trial_file_sha,
            )
            return (result, market_snapshot)
        trial_file_rel, trial_file_sha = "", ""
        if self._automl_output_store is not None:
            trial_file_rel, trial_file_sha = self._automl_output_store.copy_trials_to(
                run_id, market, stage
            )
        market_snapshot = self._build_market_snapshot(
            search_result,
            shortlisted,
            None,
            sorted(set(release_blockers)),
            stopped=False,
            trial_file=trial_file_rel,
            trial_sha256=trial_file_sha,
        )
        if self._automl_output_store is not None:
            final_snapshot = self._automl_output_store.get(run_id, market) or {}
            final_snapshot.update(
                {
                    "release_blockers": market_snapshot.release_blockers,
                    "shortlisted_trial_numbers": (market_snapshot.shortlisted_trial_numbers),
                    "selected_trial_number": None,
                    "candidate_available": False,
                }
            )
            self._automl_output_store.write(run_id, market, final_snapshot)
        return (None, market_snapshot)

    def _build_market_snapshot(
        self,
        search_result: Any,
        shortlisted: list[Any],
        selected_trial_number: int | None,
        release_blockers: list[str],
        stopped: bool,
        trial_file: str,
        trial_sha256: str,
    ) -> AutoMLMarketSearchSnapshot:
        top_trials = [
            AutoMLTrialSnapshot(
                trial_number=t.trial_number,
                state=t.state,
                fit_spec=t.fit_spec.snapshot() if t.fit_spec else None,
                metrics=t.metrics,
                overall_mae=t.overall_mae,
                overall_mape=t.overall_mape,
                station_mape=dict(t.station_mape),
                calibration_passed=t.calibration_passed,
                reason_codes=list(t.reason_codes),
                duration_seconds=t.duration_seconds,
            )
            for t in (
                search_result.ranked_trials[:10] if hasattr(search_result, "ranked_trials") else []
            )
        ]

        file_sha = (
            trial_sha256
            if trial_sha256
            else "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

        return AutoMLMarketSearchSnapshot(
            budget_name=search_result.budget_name,
            budget_seconds=search_result.budget_seconds,
            max_trials=search_result.max_trials,
            completed_trials=search_result.completed_trials,
            failed_trials=search_result.failed_trials,
            seed=search_result.seed,
            stopped=stopped or search_result.stopped,
            top_trials=top_trials,
            trial_file=trial_file,
            trial_sha256=file_sha,
            shortlisted_trial_numbers=[t.trial_number for t in shortlisted],
            selected_trial_number=selected_trial_number,
            release_blockers=release_blockers,
        )

    def request_stop(self, run_id: str) -> bool:
        return self._automl_registry.request_stop(run_id)

    def start_run(self, run_id: str) -> Any:
        return self._jobs.start(run_id)

    def submit(self, request: ModelTrainingRequest) -> JobSubmission:
        return self._jobs.create(
            job_type="model_training",
            idempotency_key="model_training:active",
            trigger=request.trigger,
        )

    def handoff(
        self,
        submission: JobSubmission,
        request: ModelTrainingRequest,
        executor: LocalJobExecutor,
    ) -> Any:
        run_id = submission.run.run_id
        return executor.submit(
            run_id,
            lambda: self.execute(run_id, request),
        )

    def execute(self, run_id: str, request: ModelTrainingRequest) -> TrainingManifest | None:
        markets = list(request.markets)
        is_automl = isinstance(request.tuning_plan, AutoMLTuningPlan)

        self._jobs.progress(
            run_id,
            {
                "mode": "automl" if is_automl else "guided",
                "stage": "validating_data",
                "markets": markets,
            },
        )

        if not self._input_path.exists():
            raise ModelTrainingError("training_data_missing", "training data file not found")

        frame = pd.read_parquet(self._input_path)
        if frame.empty:
            raise ModelTrainingError("training_data_invalid", "training data is empty")

        snapshot = build_data_snapshot(self._input_path, frame)

        for market in markets:
            market_frame = build_model_frame(frame, market)
            if len(market_frame) < 300:
                raise ModelTrainingError(
                    "training_data_insufficient",
                    f"insufficient data for {market}: {len(market_frame)} rows",
                )

        source_version = self._source_version_provider.read()
        stage = self._store.begin(run_id)

        plan = request.tuning_plan

        try:
            if is_automl:
                return self._execute_automl(
                    run_id,
                    frame,
                    stage,
                    plan,
                    markets,
                    snapshot,
                    source_version,
                )
            else:
                return self._execute_guided(
                    run_id,
                    frame,
                    stage,
                    plan,
                    markets,
                    snapshot,
                    source_version,
                )
        except ModelTrainingError as error:
            self._store.discard_staging(run_id)
            self._jobs.fail(run_id, error.error_code, error.safe_message)
            raise
        except Exception:
            self._store.discard_staging(run_id)
            self._jobs.fail(run_id, "training_failed", "模型訓練意外失敗")
            raise

    def _execute_guided(
        self,
        run_id: str,
        frame: pd.DataFrame,
        stage: Path,
        plan: TrainingPlan,
        markets: list[str],
        snapshot: DataSnapshot,
        source_version: SourceVersionProvider,
    ) -> TrainingManifest:
        results: list[MarketTrainingResult] = []
        completed: list[str] = []

        for market in markets:
            result = self._execute_guided_market(run_id, market, frame, stage, plan)
            results.append(result)
            completed.append(market)
            self._jobs.progress(
                run_id,
                {"stage": f"evaluating_{market}", "completed_markets": list(completed)},
            )

        self._jobs.progress(
            run_id,
            {"stage": "writing_artifacts", "completed_markets": list(completed)},
        )
        manifest = TrainingManifest(
            schema_version=3,
            tuning_plan_version=plan.version,
            profiles=[
                TrainingProfileSnapshot.model_validate(profile.snapshot())
                for profile in plan.profiles
            ],
            run_id=UUID(run_id),
            created_at=self._clock(),
            markets=list(markets),
            source_commit=source_version.commit,
            source_dirty=source_version.dirty,
            runtime_versions=runtime_versions(),
            data_snapshot=snapshot,
            results=results,
        )
        (stage / "manifest.json").write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )
        self._store.commit(run_id, manifest)
        self._jobs.succeed(run_id, run_id, public_training_summary(manifest))
        return manifest

    def _execute_automl(
        self,
        run_id: str,
        frame: pd.DataFrame,
        stage: Path,
        plan: AutoMLTuningPlan,
        markets: list[str],
        snapshot: DataSnapshot,
        source_version: SourceVersionProvider,
    ) -> TrainingManifest | None:
        results: list[MarketTrainingResult] = []
        automl_market_snapshots: dict[str, AutoMLMarketSearchSnapshot] = {}
        any_candidate = False
        any_stopped = False

        self._automl_registry.register(run_id)
        try:
            for market in markets:
                if self._automl_registry.should_stop(run_id):
                    any_stopped = True
                    break
                mresult, m_snapshot = self._execute_automl_market(
                    run_id,
                    market,
                    frame,
                    stage,
                    plan,
                )
                automl_market_snapshots[market] = m_snapshot
                if m_snapshot.stopped:
                    any_stopped = True
                    break
                if mresult is not None:
                    results.append(mresult)
                    any_candidate = True
        finally:
            self._automl_registry.unregister(run_id)

        if any_stopped:
            self._store.discard_staging(run_id)
            self._jobs.skip(
                run_id,
                {"mode": "automl", "markets": markets, "stopped": True},
            )
            return None

        if not any_candidate:
            self._store.discard_staging(run_id)
            self._jobs.succeed(
                run_id,
                run_id,
                {
                    "mode": "automl",
                    "candidate_available": False,
                    "markets": markets,
                },
            )
            return None

        self._jobs.progress(
            run_id,
            {
                "mode": "automl",
                "stage": "writing_artifacts",
                "completed_markets": [r.market for r in results],
            },
        )
        manifest = TrainingManifest(
            schema_version=4,
            tuning_plan_version=2,
            profiles=[],
            run_id=UUID(run_id),
            created_at=self._clock(),
            markets=list(markets),
            source_commit=source_version.commit,
            source_dirty=source_version.dirty,
            runtime_versions=runtime_versions(),
            data_snapshot=snapshot,
            results=results,
            automl=AutoMLRunSnapshot(
                mode="automl",
                markets=automl_market_snapshots,
            ),
        )
        (stage / "manifest.json").write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )
        self._store.commit(run_id, manifest)
        self._jobs.succeed(run_id, run_id, public_training_summary(manifest))
        return manifest
