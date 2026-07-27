from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import joblib
import pandas as pd

from qingpu_insight.job_executor import LocalJobExecutor
from qingpu_insight.jobs import JobService, JobSubmission
from qingpu_insight.model_analysis import (
    build_resale_diagnostics,
    evaluate_release_checks,
    release_reason_codes,
    run_annual_backtests,
    run_feature_experiments,
)
from qingpu_insight.model_artifacts import (
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
    build_model_frame,
)
from qingpu_insight.model_training import (
    BaselineEvaluationError,
    ProfileEvaluationError,
    TunedModelExperiment,
    leakage_audit,
    run_tuned_model_experiment,
    split_by_time,
)
from qingpu_insight.model_tuning import TrainingTuningPlan, parse_tuning_plan
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
    experiment: TunedModelExperiment,
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
) -> MarketTrainingResult:
    selection_metrics: dict[str, dict[str, object]] = {}
    for profile_eval in experiment.profile_results:
        for candidate in profile_eval.candidates:
            selection_metrics[
                f"{profile_eval.profile.name}:{candidate.evaluation.name}"
            ] = (
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

    return MarketTrainingResult(
        market=market,
        selected_model=experiment.selected_model,
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
        tuning_plan: TrainingTuningPlan | None = None,
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
    ) -> None:
        self._jobs = jobs
        self._store = store
        self._input_path = input_path
        self._source_version_provider = source_version_provider
        self._clock = clock or (lambda: datetime.now())

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

    def execute(self, run_id: str, request: ModelTrainingRequest) -> TrainingManifest:
        markets = list(request.markets)
        completed: list[str] = []

        self._jobs.progress(run_id, {"stage": "validating_data", "markets": markets})

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

        try:
            results: list[MarketTrainingResult] = []
            for market in markets:
                is_resale = market == "resale"
                model_frame = build_model_frame(frame, market)
                split = split_by_time(model_frame)

                diagnostics: dict[str, object] = {}
                analysis_experiments: list[dict[str, object]] = []
                serialized_backtests: list[dict[str, object]] = []
                release_checks: dict[str, bool] = {}
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
                        enhanced_features = exp_list[1].feature_columns
                        feature_contract_ver = 2

                    experiment = run_tuned_model_experiment(
                        split,
                        profiles=request.tuning_plan.profiles,
                        feature_columns=(
                            enhanced_features if is_resale else BASE_FEATURE_COLUMNS
                        ),
                        use_recency_weights=is_resale,
                        baseline_months=12 if is_resale else 24,
                        on_profile_start=lambda pn, _m=market: self._jobs.progress(
                            run_id,
                            {
                                "stage": f"training_{_m}",
                                "profile": pn,
                                "completed_markets": list(completed),
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
                winning_profile = next(
                    p
                    for p in request.tuning_plan.profiles
                    if p.name == experiment.selected_profile
                )
                if is_resale:
                    diagnostics = build_resale_diagnostics(
                        model_frame,
                        split,
                        candidate=experiment.final_test_results[
                            experiment.selected_model
                        ],
                        feature_columns=enhanced_features,
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
                    data_max_date=str(
                        (model_frame if is_resale else split.train)["transaction_date"].max().date()
                    ),
                    metrics={},
                    feature_columns=enhanced_features,
                )
                try:
                    artifact_path = train_artifact(
                        market,
                        locked,
                        split,
                        seed_bundle,
                        stage,
                        feature_columns=enhanced_features,
                        training_frame=model_frame if is_resale else None,
                        use_recency_weights=is_resale,
                        recency_half_life_months=(
                            winning_profile.recency_half_life_months or 48
                        ),
                    )
                    bundle: ValuationBundle = joblib.load(artifact_path)
                except Exception as exc:
                    raise ModelTrainingError("candidate_write_failed", str(exc)) from exc

                if is_resale:
                    try:
                        raw_backtests = run_annual_backtests(
                            model_frame,
                            experiment.selected_model,
                            enhanced_features,
                            profile=winning_profile,
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

                        baseline_metrics = experiment.final_test_results[
                            "baseline"
                        ].metrics.to_dict(orient="index")
                        candidate_metrics = experiment.final_test_results[
                            experiment.selected_model
                        ].metrics.to_dict(orient="index")
                        data_max_ts = pd.Timestamp(bundle.data_max_date)
                        latest_official_ts = pd.Timestamp(model_frame["transaction_date"].max())
                        release_checks = evaluate_release_checks(
                            candidate_metrics,
                            baseline_metrics,
                            serialized_backtests,
                            data_max_ts,
                            latest_official_ts,
                        )
                    except Exception as exc:
                        raise ModelTrainingError("candidate_write_failed", str(exc)) from exc

                self._jobs.progress(
                    run_id,
                    {
                        "stage": f"evaluating_{market}",
                        "completed_markets": list(completed),
                    },
                )

                try:
                    report_dir = stage / "reports"
                    selected_profile = next(
                        (
                            p
                            for p in request.tuning_plan.profiles
                            if p.name == experiment.selected_profile
                        ),
                        None,
                    )

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

                    evaluation_path = write_evaluation(
                        bundle,
                        experiment,
                        split,
                        report_dir,
                        selected_profile=selected_profile,
                        diagnostics=diagnostics if is_resale else None,
                        feature_experiments=(analysis_experiments if is_resale else None),
                        backtests=(serialized_backtests if is_resale else None),
                        release_checks=(release_checks if is_resale else None),
                        reason_codes=(release_reason_codes(release_checks) if is_resale else None),
                    )
                    card_path = write_model_card(
                        bundle,
                        experiment,
                        leakage_audit(split),
                        report_dir,
                        selected_profile=selected_profile,
                        feature_experiments=(analysis_experiments if is_resale else None),
                        backtests=(serialized_backtests if is_resale else None),
                        release_checks=(release_checks if is_resale else None),
                        reason_codes=(release_reason_codes(release_checks) if is_resale else None),
                    )
                    interval_summary = compute_interval_summary(
                        bundle,
                        experiment.final_test_results[experiment.selected_model],
                        split,
                    )
                except Exception as exc:
                    raise ModelTrainingError("candidate_write_failed", str(exc)) from exc

                try:
                    results.append(
                        market_result_from_files(
                            market=market,
                            bundle=bundle,
                            experiment=experiment,
                            artifact_path=artifact_path,
                            evaluation_path=evaluation_path,
                            card_path=card_path,
                            stage=stage,
                            selected_profile=experiment.selected_profile,
                            profile_results=profile_results,
                            diagnostics=diagnostics if is_resale else None,
                            feature_experiments=(analysis_experiments if is_resale else None),
                            backtests=(serialized_backtests if is_resale else None),
                            release_checks=(release_checks if is_resale else None),
                            feature_columns=list(enhanced_features),
                            feature_contract_version=feature_contract_ver,
                            test_coverage=interval_summary["test_coverage"],
                            average_interval_width_twd_per_ping=(
                                interval_summary[
                                    "average_interval_width_twd_per_ping"
                                ]
                            ),
                        )
                    )
                except Exception as exc:
                    raise ModelTrainingError("candidate_validation_failed", str(exc)) from exc
                completed.append(market)

            self._jobs.progress(
                run_id,
                {"stage": "writing_artifacts", "completed_markets": list(completed)},
            )
            manifest = TrainingManifest(
                schema_version=3,
                tuning_plan_version=request.tuning_plan.version,
                profiles=[
                    TrainingProfileSnapshot.model_validate(profile.snapshot())
                    for profile in request.tuning_plan.profiles
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
        except ModelTrainingError as error:
            self._store.discard_staging(run_id)
            self._jobs.fail(run_id, error.error_code, error.safe_message)
            raise
        except Exception:
            self._store.discard_staging(run_id)
            self._jobs.fail(run_id, "training_failed", "模型訓練意外失敗")
            raise
