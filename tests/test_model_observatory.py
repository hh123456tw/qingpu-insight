from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import joblib
import pandas as pd
import pytest

from qingpu_insight.jobs import JobRun, JobService
from qingpu_insight.model_artifacts import (
    CandidateArtifactStore,
    DataSnapshot,
    MarketTrainingResult,
    TrainingManifest,
)
from qingpu_insight.model_observatory import ModelObservatory
from qingpu_insight.model_training_service import ModelTrainingService
from qingpu_insight.valuation import ValuationBundle


def bundle_fixture(
    model_name: str = "ridge", transaction_type: str = "resale"
) -> ValuationBundle:
    return ValuationBundle(
        transaction_type=transaction_type,
        model_name=model_name,
        model_version="v1",
        pipeline=None,
        interval_abs_residual_twd_per_ping=0,
        feature_ranges={},
        feature_hard_ranges={},
        feature_medians={},
        global_importance=[],
        reference_rows=pd.DataFrame(),
        data_min_date="2023-01-01",
        data_max_date="2024-01-01",
        metrics={},
    )


def manifest_fixture(
    selected_model: str = "hist_gradient_boosting",
    markets: list[str] | None = None,
) -> TrainingManifest:
    if markets is None:
        markets = ["resale", "presale"]
    run_id = uuid4()
    ds = DataSnapshot(
        sha256="a" * 64,
        raw_count=1000,
        usable_counts={"resale": 500, "presale": 300},
        excluded_counts={"resale": 100, "presale": 100},
        station_counts={"A17": 200, "A18": 300, "A19": 200},
        min_date=date(2023, 1, 1),
        max_date=date(2024, 1, 1),
    )
    results = []
    for m in markets:
        results.append(
            MarketTrainingResult(
                market=m,
                selected_model=selected_model,
                recommended=True,
                reason_codes=[],
                selection_metrics={"overall": {"mae": {"overall": 10.0}}},
                final_test_metrics={"overall": {"mae": {"overall": 10.0}}},
                artifact_file=f"{m}.joblib",
                artifact_sha256="b" * 64,
                report_files={f"{m}-evaluation": f"reports/{m}-evaluation.json"},
                report_sha256={f"{m}-evaluation": "c" * 64},
            )
        )
    return TrainingManifest(
        schema_version=1,
        run_id=run_id,
        created_at=datetime.now(UTC),
        markets=markets,
        source_commit="abc123",
        source_dirty=False,
        runtime_versions={"python": "3.12"},
        data_snapshot=ds,
        results=results,
    )


def observatory_fixture(
    tmp_path: Path,
    official_models: dict[str, ValuationBundle] | None = None,
    candidate_runs: list[TrainingManifest] | None = None,
) -> ModelObservatory:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    if official_models:
        for market, bundle in official_models.items():
            joblib.dump(bundle, artifact_dir / f"{market}.joblib")

    candidate_store_dir = tmp_path / "candidates"
    candidate_store_dir.mkdir(parents=True, exist_ok=True)
    candidate_store = CandidateArtifactStore(candidate_store_dir)

    job_runs: dict[str, JobRun] = {}
    now = datetime.now(UTC)

    if candidate_runs:
        for manifest in candidate_runs:
            rid = str(manifest.run_id)
            run_dir = candidate_store_dir / rid
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                manifest.model_dump_json(indent=2), encoding="utf-8"
            )
            job_runs[rid] = JobRun(
                run_id=rid,
                job_type="model_training",
                trigger="manual",
                idempotency_key="",
                status="succeeded",
                started_at=now,
                finished_at=now,
                attempt=1,
                input_version=None,
                output_version=rid,
                summary={},
                error_code=None,
                error_message=None,
            )

    class FakeJobService(JobService):
        def __init__(self) -> None:
            self._runs = job_runs

        def list_recent(
            self, limit: int = 20, job_type: str | None = None
        ) -> list[JobRun]:
            return list(self._runs.values())[:limit]

        def get(self, run_id: str) -> JobRun | None:
            return self._runs.get(run_id)

    input_path = tmp_path / "data" / "processed" / "market_transactions.parquet"
    input_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for ttype in ("resale", "presale"):
        for i in range(100):
            rows.append(
                {
                    "transaction_type": ttype,
                    "analysis_eligible": True,
                    "transaction_date": pd.Timestamp("2023-01-01")
                    + pd.DateOffset(days=i),
                    "station_code": ["A17", "A18", "A19"][i % 3],
                }
            )
    pd.DataFrame(rows).to_parquet(input_path, index=False)

    # We need a minimal ModelTrainingService that exposes _input_path
    class FakeModelTrainingService:
        _input_path = input_path

    return ModelObservatory(
        artifact_dir=artifact_dir,
        candidate_store=candidate_store,
        model_training_service=FakeModelTrainingService(),  # type: ignore
        job_service=FakeJobService(),
    )


class TestModelObservatoryStatus:
    def test_official_model_separated_from_candidate_runs(
        self, tmp_path: Path
    ) -> None:
        observatory = observatory_fixture(
            tmp_path,
            official_models={"resale": bundle_fixture(model_name="ridge")},
            candidate_runs=[
                manifest_fixture(selected_model="hist_gradient_boosting")
            ],
        )
        status = observatory.status()

        assert status["official_models"]["resale"]["name"] == "ridge"
        assert status["official_models"]["resale"]["role"] == "official"
        assert status["candidate_count"] == 1

    def test_missing_official_artifact_is_safe_warning(
        self, tmp_path: Path
    ) -> None:
        status = observatory_fixture(tmp_path).status()
        assert status["official_models"]["resale"] == {
            "available": False,
            "role": "official",
            "warning": "resale_model_unavailable",
        }

    def test_data_status_includes_cached_snapshot(self, tmp_path: Path) -> None:
        observatory = observatory_fixture(tmp_path)
        status = observatory.status()
        assert "data_status" in status
        assert status["data_status"]["sha256"] is not None
        assert status["data_status"]["raw_count"] == 200
        assert status["data_status"]["usable_counts"]["resale"] == 100

    def test_list_runs_merges_job_history_with_manifests(
        self, tmp_path: Path
    ) -> None:
        manifest = manifest_fixture()
        observatory = observatory_fixture(
            tmp_path, candidate_runs=[manifest]
        )
        runs = observatory.list_runs()
        assert len(runs) == 1
        assert runs[0]["run_id"] == str(manifest.run_id)
        assert runs[0]["markets"] == manifest.markets

    def test_get_run_returns_detailed_info(self, tmp_path: Path) -> None:
        manifest = manifest_fixture()
        observatory = observatory_fixture(
            tmp_path, candidate_runs=[manifest]
        )
        result = observatory.get_run(str(manifest.run_id))
        assert result is not None
        assert result["run_id"] == str(manifest.run_id)
        assert "manifest" in result
        assert result["manifest"]["markets"] == manifest.markets

    def test_get_run_returns_none_for_missing(self, tmp_path: Path) -> None:
        observatory = observatory_fixture(tmp_path)
        assert observatory.get_run("nonexistent-run-id") is None
