from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.base import BaseEstimator as _SkBaseEst

from qingpu_insight.automl_control import AutoMLControlRegistry
from qingpu_insight.automl_outputs import AutoMLRunOutputStore
from qingpu_insight.jobs import ACTIVE_STATUSES, JobRun, JobService, JobStatus
from qingpu_insight.model_artifacts import CandidateArtifactStore, sha256_file
from qingpu_insight.model_features import BASE_FEATURE_COLUMNS
from qingpu_insight.model_training import ModelFitSpec, ProfileEvaluationError
from qingpu_insight.model_training_service import (
    ModelTrainingError,
    ModelTrainingRequest,
    ModelTrainingService,
    SourceVersionProvider,
)
from qingpu_insight.model_tuning import (
    AutoMLBudget,
    AutoMLTuningPlan,
    parse_tuning_plan,
)


class FakeJobRepository:
    def __init__(self) -> None:
        self._runs: dict[str, JobRun] = {}

    def create_or_get(self, run: JobRun) -> tuple[JobRun, bool]:
        existing = self.find_active_by_key(run.idempotency_key)
        if existing:
            return existing, False
        self._runs[run.run_id] = run
        return run, True

    def get(self, run_id: str) -> JobRun | None:
        return self._runs.get(run_id)

    def find_active_by_key(self, idempotency_key: str) -> JobRun | None:
        return next(
            (
                run
                for run in self._runs.values()
                if run.idempotency_key == idempotency_key and run.status in ACTIVE_STATUSES
            ),
            None,
        )

    def update_summary(
        self,
        run_id: str,
        expected_status: JobStatus,
        summary: dict[str, object],
    ) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.status != expected_status:
            return False
        self._runs[run_id] = replace(run, summary=summary)
        return True

    def transition(
        self,
        run_id: str,
        current_status: JobStatus,
        target_status: JobStatus,
        *,
        output_version: str | None = None,
        summary: dict[str, object] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.status != current_status:
            return False
        now = datetime.now(UTC)
        self._runs[run_id] = replace(
            run,
            status=target_status,
            started_at=(run.started_at or now if target_status == "running" else run.started_at),
            finished_at=(
                now if target_status in ("succeeded", "failed", "skipped") else run.finished_at
            ),
            attempt=run.attempt
            + (1 if (run.status == "retry_wait" and target_status == "running") else 0),
            output_version=(output_version if output_version is not None else run.output_version),
            summary=summary if summary is not None else run.summary,
            error_code=error_code if error_code is not None else run.error_code,
            error_message=(error_message if error_message is not None else run.error_message),
        )
        return True

    def list_recent(self, limit: int = 20, job_type: str | None = None) -> list[JobRun]:
        return list(self._runs.values())[-limit:][::-1]

    def list_active(self, job_type: str) -> list[JobRun]:
        return [
            run
            for run in self._runs.values()
            if run.job_type == job_type and run.status in ACTIVE_STATUSES
        ]


def service_fixture(
    tmp_path: Path, input_path: Path | None = None
) -> tuple[ModelTrainingService, JobService]:
    repo = FakeJobRepository()
    jobs = JobService(repo)
    store = CandidateArtifactStore(tmp_path / "candidates")

    if input_path is None:
        input_path = tmp_path / "data.parquet"
        pd.DataFrame().to_parquet(input_path)

    service = ModelTrainingService(
        jobs=jobs,
        store=store,
        input_path=input_path,
        source_version_provider=SourceVersionProvider(commit="test-hash", dirty=False),
    )
    return service, jobs


@pytest.fixture
def market_parquet(tmp_path: Path) -> Path:
    np.random.seed(42)
    n_per_market = 3000
    total = n_per_market * 2
    base = pd.Timestamp("2017-01-01")
    total_days = 2922

    stations = ["A17", "A18", "A19"]
    types = ["住宅大樓", "華廈"]
    ptypes = ["坡道平面", "坡道機械", ""]

    rows = []
    for i in range(total):
        market = "resale" if i < n_per_market else "presale"
        j = i if i < n_per_market else i - n_per_market
        s = stations[j % 3]
        t = types[j % 2]
        pt_repr = ptypes[j % 3]

        base_price = {"A17": 600_000, "A18": 500_000, "A19": 550_000}[s]
        type_mult = {"住宅大樓": 1.0, "華廈": 0.85}[t]
        target = base_price * type_mult + np.random.uniform(-50_000, 50_000)

        building_age = float(np.random.uniform(0, 30))
        fl = int(np.random.randint(1, 15))
        tfl = int(np.random.randint(5, 25))
        area = float(np.random.uniform(15, 60))
        parking_sqm = float(np.random.uniform(0, 15) if pt_repr else 0)

        dt = base + pd.DateOffset(days=int(i * total_days / total))

        rows.append(
            {
                "transaction_type": market,
                "transaction_date": dt,
                "station_code": s,
                "station_distance_m": float(np.random.randint(100, 1500)),
                "building_area_ping": area,
                "building_type": t,
                "bedrooms": int(np.random.randint(1, 5)),
                "living_rooms": int(np.random.randint(1, 3)),
                "bathrooms": int(np.random.randint(1, 3)),
                "building_age_years": building_age,
                "floor": fl,
                "total_floors": tfl,
                "parking_type": pt_repr,
                "parking_area_sqm": parking_sqm,
                "parking_price_twd": (
                    float(np.random.uniform(500_000, 2_000_000)) if pt_repr else 0
                ),
                "total_price_twd": target * area,
                "unit_price_per_ping_twd": target,
                "transaction_key": f"T{i}",
                "road_key": f"R{i % 10}",
                "analysis_eligible": True,
            }
        )

    df = pd.DataFrame(rows)
    path = tmp_path / "market.parquet"
    df.to_parquet(path, index=False)
    return path


@pytest.mark.parametrize(
    "markets",
    [
        (),
        ("resale", "resale"),
        ("sale",),
        ("presale",),
        ("resale", "presale"),
    ],
)
def test_training_request_rejects_nonfixed_markets(markets) -> None:
    with pytest.raises(ValueError):
        ModelTrainingRequest(markets=markets)


def test_submit_returns_the_existing_active_model_job(tmp_path: Path) -> None:
    service, jobs = service_fixture(tmp_path)
    first = service.submit(ModelTrainingRequest(("resale",)))
    second = service.submit(ModelTrainingRequest(("resale",)))
    assert first.created is True
    assert second.created is False
    assert second.run.run_id == first.run.run_id
    assert jobs.get(first.run.run_id).idempotency_key == "model_training:active"


def test_submit_persists_pending_resale_market_evidence(tmp_path: Path) -> None:
    service, jobs = service_fixture(tmp_path)

    submission = service.submit(ModelTrainingRequest(("resale",)))

    assert submission.run.summary == {"markets": ["resale"]}
    assert jobs.get(submission.run.run_id).summary == {"markets": ["resale"]}


def test_execute_writes_traceable_candidate_without_touching_official_models(
    tmp_path: Path,
    market_parquet: Path,
) -> None:
    service, jobs = service_fixture(tmp_path, input_path=market_parquet)
    official = tmp_path / "artifacts" / "resale.joblib"
    official.parent.mkdir(parents=True)
    official.write_bytes(b"official-model")
    before = sha256_file(official)
    run = service.submit(ModelTrainingRequest(("resale",))).run
    jobs.start(run.run_id)

    manifest = service.execute(run.run_id, ModelTrainingRequest(("resale",)))

    assert manifest.run_id == UUID(run.run_id)
    assert manifest.markets == ["resale"]
    assert manifest.data_snapshot.raw_count == 6_000
    assert manifest.data_snapshot.usable_counts == {
        "resale": 3000,
        "presale": 3000,
    }
    assert manifest.data_snapshot.station_counts == {
        "A17": 2000,
        "A18": 2000,
        "A19": 2000,
    }
    assert sha256_file(official) == before
    assert jobs.get(run.run_id).status == "succeeded"


def test_service_rejects_forged_mixed_request_without_touching_artifacts(
    tmp_path: Path,
    market_parquet: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, jobs = service_fixture(tmp_path, input_path=market_parquet)
    official_resale = tmp_path / "artifacts" / "resale.joblib"
    official_presale = tmp_path / "artifacts" / "presale.joblib"
    official_resale.parent.mkdir(parents=True)
    official_resale.write_bytes(b"official-resale")
    official_presale.write_bytes(b"official-presale")
    before_resale = sha256_file(official_resale)
    before_presale = sha256_file(official_presale)

    run = service.submit(ModelTrainingRequest(("resale",))).run
    jobs.start(run.run_id)
    request = object.__new__(ModelTrainingRequest)
    request._markets = ("resale", "presale")
    request.trigger = "manual"
    request.tuning_plan = parse_tuning_plan(("resale",), None)

    with pytest.raises(ValueError, match="resale"):
        service.submit(request)
    with pytest.raises(ValueError, match="resale"):
        service.execute(run.run_id, request)

    candidate_root = tmp_path / "candidates"
    assert jobs.get(run.run_id).status == "running"
    assert not (candidate_root / run.run_id).exists()
    assert sha256_file(official_resale) == before_resale
    assert sha256_file(official_presale) == before_presale


def test_training_request_keeps_tuning_plan() -> None:
    plan = parse_tuning_plan(("resale",), None)
    request = ModelTrainingRequest(("resale",), tuning_plan=plan)
    assert request.tuning_plan == plan
    assert request.tuning_plan.profiles[1].name == "balanced"


def test_training_request_defaults_to_three_profiles() -> None:
    request = ModelTrainingRequest(("resale",))
    assert len(request.tuning_plan.profiles) == 3
    for profile in request.tuning_plan.profiles:
        assert profile.source == "preset"


def test_training_request_custom_four_profiles() -> None:
    custom_plan = parse_tuning_plan(
        ("resale",),
        {
            "mode": "preset_comparison",
            "include_custom": True,
            "custom": {
                "hgb_learning_rate": 0.10,
                "hgb_max_iter": 200,
                "rf_n_estimators": 300,
                "recency_half_life_months": 24,
            },
        },
    )
    request = ModelTrainingRequest(("resale",), tuning_plan=custom_plan)
    assert len(request.tuning_plan.profiles) == 4
    assert request.tuning_plan.profiles[-1].name == "custom"


def test_profile_failure_discards_candidate_and_fails_job(
    tmp_path: Path,
    market_parquet: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qingpu_insight.model_training_service as mts

    service, jobs = service_fixture(tmp_path, input_path=market_parquet)
    plan = parse_tuning_plan(("resale",), None)
    request = ModelTrainingRequest(("resale",), tuning_plan=plan)
    run = service.submit(request).run
    jobs.start(run.run_id)

    monkeypatch.setattr(
        mts,
        "run_tuned_model_experiment",
        lambda *args, **kwargs: (_ for _ in ()).throw(ProfileEvaluationError("thorough")),
    )
    with pytest.raises(ModelTrainingError) as caught:
        service.execute(run.run_id, request)
    assert caught.value.error_code == "profile_failed"
    assert jobs.get(run.run_id).status == "failed"
    assert not (tmp_path / "candidates" / run.run_id).exists()


def test_execute_runs_tuned_model_experiment_with_profiles_and_recency_weighting(
    tmp_path, market_parquet, monkeypatch
):
    import qingpu_insight.model_training_service as mts

    captured = []

    def spy(
        split,
        *,
        profiles,
        feature_columns,
        use_recency_weights,
        baseline_months,
        on_profile_start=None,
    ):
        captured.append(
            {
                "profile_count": len(profiles),
                "use_recency_weights": use_recency_weights,
                "profile_names": [p.name for p in profiles],
            }
        )
        from qingpu_insight.model_training import (
            ProfileEvaluation,
            TunedModelExperiment,
            candidate_estimators,
            evaluate_candidate,
        )
        from qingpu_insight.model_training import (
            run_model_experiment as orig,
        )

        result = orig(
            split,
            feature_columns=feature_columns,
            use_recency_weights=use_recency_weights,
            baseline_months=baseline_months,
        )
        if "ridge" not in result.final_test_results:
            ridge_est = candidate_estimators(feature_columns=feature_columns)["ridge"]
            ridge_eval = evaluate_candidate(
                "ridge",
                ridge_est,
                split.train,
                split.test,
                feature_columns=feature_columns,
            )
            result.final_test_results["ridge"] = ridge_eval
        profiles_tuple = tuple(
            ProfileEvaluation(profile=p, candidates=(), candidate_errors={}) for p in profiles
        )
        return TunedModelExperiment(
            profile_results=profiles_tuple,
            selected_profile="balanced",
            selected_model="ridge",
            selected_evaluation=result.final_test_results["ridge"],
            selected_estimator=result.final_test_results["ridge"].estimator,
            final_test_results=result.final_test_results,
            recommended=result.recommended,
            reason_codes=result.reason_codes,
        )

    monkeypatch.setattr(mts, "run_tuned_model_experiment", spy)

    service, jobs = service_fixture(tmp_path, input_path=market_parquet)
    run = service.submit(ModelTrainingRequest(("resale",))).run
    jobs.start(run.run_id)
    manifest = service.execute(run.run_id, ModelTrainingRequest(("resale",)))

    assert len(captured) == 1
    assert captured[0]["use_recency_weights"] is True
    for cap in captured:
        assert cap["profile_count"] == 3
        assert cap["profile_names"] == ["quick", "balanced", "thorough"]
    assert manifest.results[0].selected_profile == "balanced"


def test_resale_training_writes_schema_v2_analysis(tmp_path, market_parquet):
    service, jobs = service_fixture(tmp_path, input_path=market_parquet)
    run = service.submit(ModelTrainingRequest(("resale",))).run
    jobs.start(run.run_id)
    manifest = service.execute(run.run_id, ModelTrainingRequest(("resale",)))
    result = manifest.results[0]
    assert manifest.schema_version == 3
    assert result.market == "resale"
    assert result.feature_contract_version == 3
    assert result.diagnostics["station_counts"]["A18"] > 0
    assert len(result.feature_experiments) == 7
    assert len(result.backtests) == 3
    assert result.selected_profile in {"quick", "balanced", "thorough"}
    assert result.profile_results
    assert result.test_coverage is not None
    assert result.average_interval_width_twd_per_ping is not None
    assert "a18_improved" in result.release_checks
    assert result.recommended is result.release_checks["recommended"]
    expected_reasons = {
        "overall_mae_improved": "overall_mae_not_improved",
        "stations_within_limit": "station_regression",
        "a18_improved": "a18_not_improved",
        "backtests_passed": "backtest_insufficient",
        "backtest_stations_within_limit": "backtest_station_regression",
        "candidate_fresh": "candidate_stale",
    }
    assert set(result.reason_codes) == {
        reason for check, reason in expected_reasons.items() if not result.release_checks[check]
    }
    artifact = joblib.load(tmp_path / "candidates" / run.run_id / result.artifact_file)
    source = pd.read_parquet(market_parquet)
    resale_max = source.loc[source["transaction_type"].eq("resale"), "transaction_date"].max()
    assert artifact.data_max_date == str(resale_max.date())


def test_training_diagnostics_merge_market_quality_exclusions(tmp_path: Path) -> None:
    input_path = tmp_path / "data" / "processed" / "market_transactions.parquet"
    input_path.parent.mkdir(parents=True)
    report_path = tmp_path / "outputs" / "reports" / "m1-market-quality.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(
            {
                "input_records": 90_322,
                "output_by_type": {"resale": 5_280, "presale": 2_811},
                "exclusion_reasons": {
                    "special_relationship": 446,
                    "non_market_subject": 18,
                    "missing_completion_date": 3,
                    "future_completion_transfer": 10_648,
                }
            }
        ),
        encoding="utf-8",
    )
    service = object.__new__(ModelTrainingService)
    service._input_path = input_path
    diagnostics = {
        "data_quality": {
            "special_relationship_excluded": 0,
            "non_market_subject_excluded": 0,
            "ambiguous_registration_note_count": 10_672,
        }
    }

    merged = service._merge_market_quality_diagnostics(diagnostics)

    assert merged["data_quality"] == {
        "special_relationship_excluded": 446,
        "non_market_subject_excluded": 18,
        "ambiguous_registration_note_count": 10_672,
        "raw_count": 90_322,
        "usable_count": 5_280,
        "missing_completion_date": 3,
        "future_completion_transfer": 10_648,
    }


def test_presale_training_request_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported market: presale"):
        ModelTrainingRequest(("presale",))


@pytest.fixture
def automl_plan() -> AutoMLTuningPlan:
    return AutoMLTuningPlan(2, "automl", AutoMLBudget("quick", 300, 12))


@pytest.fixture
def automl_service_fixture(
    tmp_path: Path,
    market_parquet: Path,
) -> tuple[ModelTrainingService, JobService, AutoMLControlRegistry, AutoMLRunOutputStore]:
    repo = FakeJobRepository()
    jobs = JobService(repo)
    store = CandidateArtifactStore(tmp_path / "candidates")
    registry = AutoMLControlRegistry()
    output_store = AutoMLRunOutputStore(tmp_path / "automl_outputs")
    service = ModelTrainingService(
        jobs=jobs,
        store=store,
        input_path=market_parquet,
        source_version_provider=SourceVersionProvider(commit="test-hash", dirty=False),
        automl_registry=registry,
        automl_output_store=output_store,
    )
    return service, jobs, registry, output_store


DUMMY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class _FakeEstimator(_SkBaseEst):
    def predict(self, X):
        import numpy as np

        return np.full(len(X), 500000.0)

    def fit(self, X, y, sample_weight=None):
        return self


class TestAutoMLOrchestration:
    def _make_trial(
        self,
        trial_number: int,
        fit_spec: Any,
        metrics: dict,
        overall_mae: float,
        station_mape: dict,
    ) -> Any:
        from qingpu_insight.automl_search import AutoMLTrialResult

        return AutoMLTrialResult(
            trial_number=trial_number,
            state="completed",
            fit_spec=fit_spec,
            estimator=None,
            metrics=metrics,
            overall_mae=overall_mae,
            overall_mape=float(metrics.get("overall", {}).get("mape", 0)),
            station_mape=dict(station_mape),
            calibration_passed=True,
            reason_codes=(),
            duration_seconds=1.0,
        )

    def _make_search_result(
        self,
        market: str,
        trials: list,
        stopped: bool = False,
    ) -> Any:
        from qingpu_insight.automl_search import AutoMLSearchResult, rank_trials, shortlist_trials

        all_trials = tuple(trials)
        ranked = rank_trials(all_trials)
        shortlisted = shortlist_trials(ranked)
        return AutoMLSearchResult(
            market=market,
            budget_name="quick",
            budget_seconds=300,
            max_trials=12,
            seed=42,
            elapsed_seconds=10.0,
            stopped=stopped,
            trials=all_trials,
            ranked_trials=ranked,
            shortlisted_trials=shortlisted,
        )

    def _patch_automl_pipeline(
        self,
        monkeypatch: pytest.MonkeyPatch,
        passing: bool = True,
        n_trials: int = 1,
        market: str = "resale",
    ) -> None:
        import qingpu_insight.model_training_service as mts

        fit_spec = ModelFitSpec(
            model_name="hist_gradient_boosting",
            parameters={
                "learning_rate": 0.1,
                "max_iter": 200,
                "max_leaf_nodes": 31,
                "l2_regularization": 1.0,
            },
            recency_half_life_months=48 if market == "resale" else None,
        )

        dummy_metrics = {
            "overall": {"mae": 45000, "mape": 8.5, "rmse": 55000, "r2": 0.72, "count": 200},
            "station:A17": {"mae": 42000, "mape": 7.8, "rmse": 51000, "r2": 0.75, "count": 80},
            "station:A18": {"mae": 43000, "mape": 8.0, "rmse": 52000, "r2": 0.74, "count": 70},
            "station:A19": {"mae": 44000, "mape": 8.2, "rmse": 53000, "r2": 0.73, "count": 50},
        }
        station_mape = {"A17": 7.8, "A18": 8.0, "A19": 8.2}

        trials = [
            self._make_trial(i, fit_spec, dummy_metrics, 45000 + i * 1000, station_mape)
            for i in range(n_trials)
        ]
        search_result = self._make_search_result(market, trials)

        monkeypatch.setattr(mts, "run_automl_search", lambda *a, **kw: search_result)
        monkeypatch.setattr(
            mts,
            "run_feature_experiments",
            lambda split: (
                type(
                    "FE",
                    (),
                    {
                        "name": "base",
                        "feature_columns": list(BASE_FEATURE_COLUMNS),
                        "selected_model": "ridge",
                        "metrics": {},
                        "candidate_errors": {},
                    },
                )(),
                type(
                    "FE",
                    (),
                    {
                        "name": "enhanced",
                        "feature_columns": list(BASE_FEATURE_COLUMNS),
                        "selected_model": "ridge",
                        "metrics": {},
                        "candidate_errors": {},
                    },
                )(),
            ),
        )

        import joblib as _jl

        from qingpu_insight.parking_valuation import (
            ParkingPricePolicy,
            ParkingPriceStat,
        )
        from qingpu_insight.valuation import ValuationBundle

        def _fake_train_artifact(transaction_type, selected, split, bundle, artifact_dir, **kw):
            result_bundle = ValuationBundle(
                transaction_type=transaction_type,
                model_name=selected.name,
                model_version="test-v1",
                pipeline=selected.estimator,
                interval_abs_residual_twd_per_ping=50000,
                feature_ranges={},
                feature_hard_ranges={},
                feature_medians={},
                global_importance=[],
                reference_rows=split.calibration,
                data_min_date=str(split.calibration["transaction_date"].min().date()),
                data_max_date=str(split.calibration["transaction_date"].max().date()),
                metrics={"overall": {"mae": 45000, "count": 100}},
                feature_columns=tuple(bundle.feature_columns),
                parking_price_policy=ParkingPricePolicy(
                    version=1,
                    minimum_type_samples=20,
                    by_type={},
                    market_fallback=ParkingPriceStat(2_000_000, 50),
                ),
            )
            artifact_dir.mkdir(parents=True, exist_ok=True)
            p = artifact_dir / f"{transaction_type}.joblib"
            _jl.dump(result_bundle, p)
            return p

        monkeypatch.setattr(mts, "train_artifact", _fake_train_artifact)

        def _fake_backtests(*a, **kw):
            return [
                {
                    "cutoff_date": "2025-01-01",
                    "train_max_date": "2024-06-01",
                    "test_min_date": "2025-01-01",
                    "source_max_date": "2025-12-31",
                    "passed": passing,
                    "stations_within_limit": passing,
                    "candidate_metrics": {"overall": {"mae": 45000}},
                    "baseline_metrics": {"overall": {"mae": 50000}},
                }
            ]

        monkeypatch.setattr(mts, "run_annual_backtests", _fake_backtests)

        class MockExp:
            def __init__(self):
                self.selected_name = "hist_gradient_boosting"
                self.selection_results = [
                    self._make_candidate_eval("hist_gradient_boosting", _FakeEstimator())
                ]
                self.selected_estimator = _FakeEstimator()
                self.final_test_results = {
                    "hist_gradient_boosting": self._make_candidate_eval(
                        "hist_gradient_boosting", _FakeEstimator()
                    ),
                    "baseline": self._make_candidate_eval("baseline", _FakeEstimator()),
                }
                self.recommended = passing
                self.reason_codes = ()
                self.candidate_errors = {}

            def _make_candidate_eval(self, name, estimator):
                import pandas as pd

                from qingpu_insight.model_training import CandidateEvaluation

                return CandidateEvaluation(
                    name=name,
                    estimator=estimator,
                    overall_mae=45000,
                    station_mape=station_mape,
                    metrics=pd.DataFrame(dummy_metrics).T,
                )

        monkeypatch.setattr(mts, "evaluate_fit_spec", lambda *a, **kw: MockExp())

        if market == "resale":
            fake_checks = {
                "overall_mae_improved": passing,
                "stations_within_limit": passing,
                "a18_improved": passing,
                "backtests_passed": passing,
                "backtest_stations_within_limit": passing,
                "candidate_fresh": passing,
                "recommended": passing,
            }
            monkeypatch.setattr(mts, "evaluate_release_checks", lambda *a, **kw: fake_checks)

    def test_automl_executes_search_and_returns_candidate(
        self,
        tmp_path: Path,
        market_parquet: Path,
        automl_plan: AutoMLTuningPlan,
        automl_service_fixture: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:

        service, jobs, registry, output_store = automl_service_fixture
        self._patch_automl_pipeline(monkeypatch, passing=True, market="resale")

        request = ModelTrainingRequest(("resale",), tuning_plan=automl_plan)
        run = service.submit(request).run
        jobs.start(run.run_id)
        manifest = service.execute(run.run_id, request)

        status = jobs.get(run.run_id)
        if manifest is None:
            import json

            print(f"\nJob status: {status.status if status else 'N/A'}")
            summary_text = json.dumps(status.summary, indent=2, default=str) if status else "N/A"
            print(f"Job summary: {summary_text}")

        assert manifest is not None
        assert manifest.automl is not None
        assert manifest.automl.mode == "automl"
        assert "resale" in manifest.automl.markets
        resale_snap = manifest.automl.markets["resale"]
        assert resale_snap.selected_trial_number is not None
        assert resale_snap.completed_trials > 0
        assert not resale_snap.stopped

        assert len(manifest.results) == 1
        result = manifest.results[0]
        assert result.market == "resale"
        assert result.artifact_sha256 is not None

    def test_automl_stop_returns_none_and_skips_job(
        self,
        tmp_path: Path,
        market_parquet: Path,
        automl_plan: AutoMLTuningPlan,
        automl_service_fixture: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:

        import qingpu_insight.model_training_service as mts

        service, jobs, registry, output_store = automl_service_fixture

        # Build a raw search result with stopped=True
        fit_spec = ModelFitSpec(
            model_name="hist_gradient_boosting",
            parameters={
                "learning_rate": 0.1,
                "max_iter": 200,
                "max_leaf_nodes": 31,
                "l2_regularization": 1.0,
            },
            recency_half_life_months=48,
        )
        trial = self._make_trial(0, fit_spec, {}, 45000, {})
        search_result = self._make_search_result("resale", [trial], stopped=True)
        monkeypatch.setattr(mts, "run_automl_search", lambda *a, **kw: search_result)

        request = ModelTrainingRequest(("resale",), tuning_plan=automl_plan)
        run = service.submit(request).run
        jobs.start(run.run_id)

        manifest = service.execute(run.run_id, request)

        assert manifest is None
        status = jobs.get(run.run_id)
        assert status is not None
        assert status.status == "skipped"

    def test_automl_no_pass_returns_none(
        self,
        tmp_path: Path,
        market_parquet: Path,
        automl_plan: AutoMLTuningPlan,
        automl_service_fixture: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:

        service, jobs, registry, output_store = automl_service_fixture
        self._patch_automl_pipeline(monkeypatch, passing=False, market="resale", n_trials=3)

        request = ModelTrainingRequest(("resale",), tuning_plan=automl_plan)
        run = service.submit(request).run
        jobs.start(run.run_id)

        manifest = service.execute(run.run_id, request)

        assert manifest is None
        status = jobs.get(run.run_id)
        assert status is not None
        assert status.status == "succeeded"
        assert not status.summary.get("candidate_available", True)
        raw_output = output_store.get(run.run_id, "resale")
        assert raw_output is not None
        assert raw_output["candidate_available"] is False
        assert raw_output["release_blockers"]

    def test_automl_mixed_market_partial_pass(
        self,
        tmp_path: Path,
        market_parquet: Path,
        automl_plan: AutoMLTuningPlan,
        automl_service_fixture: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import joblib as _jl

        import qingpu_insight.model_training_service as mts
        from qingpu_insight.parking_valuation import (
            ParkingPricePolicy,
            ParkingPriceStat,
        )
        from qingpu_insight.valuation import ValuationBundle

        service, jobs, registry, output_store = automl_service_fixture

        fit_spec = ModelFitSpec(
            model_name="hist_gradient_boosting",
            parameters={
                "learning_rate": 0.1,
                "max_iter": 200,
                "max_leaf_nodes": 31,
                "l2_regularization": 1.0,
            },
            recency_half_life_months=48,
        )
        dummy_metrics = {
            "overall": {"mae": 45000, "mape": 8.5, "rmse": 55000, "r2": 0.72, "count": 200},
            "station:A17": {"mae": 42000, "mape": 7.8, "rmse": 51000, "r2": 0.75, "count": 80},
        }
        station_mape = {"A17": 7.8, "A18": 8.0, "A19": 8.2}

        passing_trial = self._make_trial(0, fit_spec, dummy_metrics, 45000, station_mape)
        failing_trial = self._make_trial(0, fit_spec, dummy_metrics, 45000, station_mape)
        passing_result = self._make_search_result("resale", [passing_trial])
        failing_result = self._make_search_result("presale", [failing_trial])

        call_count = [0]

        def side_effect_search(
            split,
            plan,
            feature_columns,
            use_recency_weights,
            baseline_months,
            should_stop,
            on_progress,
            **kw,
        ):
            call_count[0] += 1
            return passing_result if call_count[0] == 1 else failing_result

        monkeypatch.setattr(mts, "run_automl_search", side_effect_search)
        monkeypatch.setattr(
            mts,
            "run_feature_experiments",
            lambda split: (
                type(
                    "FE",
                    (),
                    {
                        "name": "base",
                        "feature_columns": list(BASE_FEATURE_COLUMNS),
                        "selected_model": "ridge",
                        "metrics": {},
                        "candidate_errors": {},
                    },
                )(),
                type(
                    "FE",
                    (),
                    {
                        "name": "enhanced",
                        "feature_columns": list(BASE_FEATURE_COLUMNS),
                        "selected_model": "ridge",
                        "metrics": {},
                        "candidate_errors": {},
                    },
                )(),
            ),
        )

        def _fake_train_artifact(transaction_type, selected, split, bundle, artifact_dir, **kw):
            result_bundle = ValuationBundle(
                transaction_type=transaction_type,
                model_name=selected.name,
                model_version="test-v1",
                pipeline=selected.estimator,
                interval_abs_residual_twd_per_ping=50000,
                feature_ranges={},
                feature_hard_ranges={},
                feature_medians={},
                global_importance=[],
                reference_rows=split.calibration,
                data_min_date=str(split.calibration["transaction_date"].min().date()),
                data_max_date=str(split.calibration["transaction_date"].max().date()),
                metrics={"overall": {"mae": 45000, "count": 100}},
                feature_columns=tuple(bundle.feature_columns),
                parking_price_policy=ParkingPricePolicy(
                    version=1,
                    minimum_type_samples=20,
                    by_type={},
                    market_fallback=ParkingPriceStat(2_000_000, 50),
                ),
            )
            artifact_dir.mkdir(parents=True, exist_ok=True)
            p = artifact_dir / f"{transaction_type}.joblib"
            _jl.dump(result_bundle, p)
            return p

        monkeypatch.setattr(mts, "train_artifact", _fake_train_artifact)
        monkeypatch.setattr(mts, "run_annual_backtests", lambda *a, **kw: [])

        import pandas as pd

        from qingpu_insight.model_training import CandidateEvaluation

        passing_checks = {
            "overall_mae_improved": True,
            "stations_within_limit": True,
            "a18_improved": True,
            "backtests_passed": True,
            "backtest_stations_within_limit": True,
            "candidate_fresh": True,
            "recommended": True,
        }
        monkeypatch.setattr(mts, "evaluate_release_checks", lambda *a, **kw: passing_checks)

        def _make_exp(recommended=True):
            est = _FakeEstimator()
            metrics_df = pd.DataFrame(dummy_metrics).T
            ce = CandidateEvaluation(
                name="hist_gradient_boosting",
                estimator=est,
                overall_mae=45000,
                station_mape=station_mape,
                metrics=metrics_df,
            )
            exp = type(
                "MockExp",
                (),
                {
                    "selected_name": "hist_gradient_boosting",
                    "selection_results": [ce],
                    "selected_estimator": est,
                    "final_test_results": {
                        "hist_gradient_boosting": ce,
                        "baseline": ce,
                    },
                    "recommended": recommended,
                    "reason_codes": (),
                    "candidate_errors": {},
                },
            )()
            return exp

        exp_call_count = [0]

        def side_effect_evaluate(*a, **kw):
            exp_call_count[0] += 1
            return _make_exp(recommended=(exp_call_count[0] == 1))

        monkeypatch.setattr(mts, "evaluate_fit_spec", side_effect_evaluate)

        request = ModelTrainingRequest(("resale",), tuning_plan=automl_plan)
        run = service.submit(request).run
        jobs.start(run.run_id)

        manifest = service.execute(run.run_id, request)

        assert manifest is not None
        assert len(manifest.results) == 1
        assert manifest.results[0].market == "resale"

    def test_guided_path_unchanged(self, tmp_path: Path, market_parquet: Path) -> None:
        service, jobs = service_fixture(tmp_path, input_path=market_parquet)
        run = service.submit(ModelTrainingRequest(("resale",))).run
        jobs.start(run.run_id)
        manifest = service.execute(run.run_id, ModelTrainingRequest(("resale",)))
        result = manifest.results[0]
        assert manifest.schema_version == 3
        assert result.selected_profile is not None
        assert len(result.profile_results) == 3

    def test_automl_presale_request_is_rejected(
        self,
        tmp_path: Path,
        market_parquet: Path,
        automl_plan: AutoMLTuningPlan,
        automl_service_fixture: tuple,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with pytest.raises(ValueError, match="unsupported market: presale"):
            ModelTrainingRequest(("presale",), tuning_plan=automl_plan)
