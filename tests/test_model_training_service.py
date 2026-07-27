from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import numpy as np
import pandas as pd
import pytest

from qingpu_insight.jobs import ACTIVE_STATUSES, JobRun, JobService, JobStatus
from qingpu_insight.model_artifacts import CandidateArtifactStore, sha256_file
from qingpu_insight.model_training import ProfileEvaluationError
from qingpu_insight.model_training_service import (
    ModelTrainingError,
    ModelTrainingRequest,
    ModelTrainingService,
    SourceVersionProvider,
)
from qingpu_insight.model_tuning import parse_tuning_plan


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
                if run.idempotency_key == idempotency_key
                and run.status in ACTIVE_STATUSES
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
            started_at=(
                run.started_at or now if target_status == "running" else run.started_at
            ),
            finished_at=(
                now
                if target_status in ("succeeded", "failed", "skipped")
                else run.finished_at
            ),
            attempt=run.attempt
            + (1 if (run.status == "retry_wait" and target_status == "running") else 0),
            output_version=(
                output_version if output_version is not None else run.output_version
            ),
            summary=summary if summary is not None else run.summary,
            error_code=error_code if error_code is not None else run.error_code,
            error_message=(
                error_message if error_message is not None else run.error_message
            ),
        )
        return True

    def list_recent(
        self, limit: int = 20, job_type: str | None = None
    ) -> list[JobRun]:
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
        source_version_provider=SourceVersionProvider(
            commit="test-hash", dirty=False
        ),
    )
    return service, jobs


@pytest.fixture
def market_parquet(tmp_path: Path) -> Path:
    np.random.seed(42)
    n_per_market = 800
    total = n_per_market * 2
    base = pd.Timestamp("2020-01-01")
    total_days = 1825

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
    [(), ("resale", "resale"), ("sale",)],
)
def test_training_request_rejects_nonfixed_markets(markets) -> None:
    with pytest.raises(ValueError):
        ModelTrainingRequest(markets=markets)


def test_submit_returns_the_existing_active_model_job(tmp_path: Path) -> None:
    service, jobs = service_fixture(tmp_path)
    first = service.submit(ModelTrainingRequest(("resale",)))
    second = service.submit(ModelTrainingRequest(("presale",)))
    assert first.created is True
    assert second.created is False
    assert second.run.run_id == first.run.run_id
    assert jobs.get(first.run.run_id).idempotency_key == "model_training:active"


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
    assert manifest.data_snapshot.raw_count == 1_600
    assert manifest.data_snapshot.usable_counts == {
        "resale": 800,
        "presale": 800,
    }
    assert manifest.data_snapshot.station_counts == {
        "A17": 534,
        "A18": 534,
        "A19": 532,
    }
    assert sha256_file(official) == before
    assert jobs.get(run.run_id).status == "succeeded"


def test_execute_fails_atomically_when_a_market_raises(
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

    import qingpu_insight.model_training_service as mts

    call_count = 0
    original_run = mts.run_tuned_model_experiment

    def failing_run(split, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise RuntimeError("presale experiment failed")
        return original_run(split, **kwargs)

    monkeypatch.setattr(mts, "run_tuned_model_experiment", failing_run)

    run = service.submit(ModelTrainingRequest(("resale", "presale"))).run
    jobs.start(run.run_id)

    with pytest.raises(RuntimeError, match="presale experiment failed"):
        service.execute(run.run_id, ModelTrainingRequest(("resale", "presale")))

    candidate_root = tmp_path / "candidates"
    assert jobs.get(run.run_id).status == "failed"
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
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ProfileEvaluationError("thorough")
        ),
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

    def spy(split, *, profiles, feature_columns,
            use_recency_weights, baseline_months, on_profile_start=None):
        captured.append({
            "profile_count": len(profiles),
            "use_recency_weights": use_recency_weights,
            "profile_names": [p.name for p in profiles],
        })
        from qingpu_insight.model_training import run_model_experiment as orig
        result = orig(split)
        from qingpu_insight.model_training import ProfileEvaluation, TunedModelExperiment
        profiles_tuple = tuple(
            ProfileEvaluation(profile=p, candidates=(), candidate_errors={})
            for p in profiles
        )
        return TunedModelExperiment(
            profile_results=profiles_tuple,
            selected_profile="balanced",
            selected_model="ridge",
            selected_evaluation=result.final_test_results.get(
                "ridge", result.final_test_results.get("baseline")),
            selected_estimator=result.selected_estimator,
            final_test_results=result.final_test_results,
            recommended=result.recommended,
            reason_codes=result.reason_codes,
        )

    monkeypatch.setattr(mts, "run_tuned_model_experiment", spy)

    service, jobs = service_fixture(tmp_path, input_path=market_parquet)
    run = service.submit(ModelTrainingRequest(("resale", "presale"))).run
    jobs.start(run.run_id)
    manifest = service.execute(run.run_id, ModelTrainingRequest(("resale", "presale")))

    assert len(captured) == 2
    assert captured[0]["use_recency_weights"] is True
    assert captured[1]["use_recency_weights"] is False
    for cap in captured:
        assert cap["profile_count"] == 3
        assert cap["profile_names"] == ["quick", "balanced", "thorough"]
    assert manifest.results[0].selected_profile == "balanced"
