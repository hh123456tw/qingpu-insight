from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import joblib
import pandas as pd
import pytest
from sklearn.dummy import DummyRegressor

from qingpu_insight.jobs import JobRun, JobService
from qingpu_insight.model_artifacts import (
    DataSnapshot,
    MarketTrainingResult,
    TrainingManifest,
    sha256_file,
)
from qingpu_insight.model_release import ModelReleaseService, OfficialModelStore
from qingpu_insight.model_release_repository import (
    InMemoryModelReleaseRepository,
)
from qingpu_insight.operation_previews import (
    InMemoryOperationPreviewRepository,
    OperationPreviewService,
    PreviewAlreadyConsumed,
    PreviewConfirmationMismatch,
)
from qingpu_insight.parking_valuation import ParkingPricePolicy, ParkingPriceStat
from qingpu_insight.valuation import ValuationBundle


def _make_bundle(market: str, model_version: str = "1.0") -> ValuationBundle:
    dummy = DummyRegressor(strategy="constant", constant=500_000)
    dummy.fit(
        pd.DataFrame(
            {c: [0.0] for c in ["building_area_ping", "station_distance_m", "bedrooms",
                                 "living_rooms", "bathrooms", "building_age_years",
                                 "floor", "total_floors"]}
        ),
        pd.Series([500_000.0]),
    )
    return ValuationBundle(
        transaction_type=market,
        model_name="ridge",
        model_version=model_version,
        pipeline=dummy,
        interval_abs_residual_twd_per_ping=1000.0,
        feature_ranges={},
        feature_hard_ranges={},
        feature_medians={},
        global_importance=[],
        reference_rows=pd.DataFrame(),
        data_min_date="2024-01-01",
        data_max_date="2024-12-31",
        metrics={},
    )


def _setup_candidate(
    base: Path, market: str, recommended: bool = True
) -> tuple[Path, TrainingManifest, MarketTrainingResult]:
    candidate_root = base / f"candidate-{market}"
    candidate_root.mkdir(parents=True)

    bundle = _make_bundle(market)
    bundle.parking_price_policy = ParkingPricePolicy(
        version=1,
        minimum_type_samples=20,
        by_type={
            "\u5761\u9053\u5e73\u9762": ParkingPriceStat(price_twd=1_500_000, sample_size=50),
        },
        market_fallback=ParkingPriceStat(price_twd=1_200_000, sample_size=100),
    )
    artifact_file = f"{market}.joblib"
    joblib.dump(bundle, candidate_root / artifact_file)
    artifact_hash = sha256_file(candidate_root / artifact_file)

    result = MarketTrainingResult(
        market=market,
        selected_model="ridge",
        recommended=recommended,
        reason_codes=["best_cv_score"],
        selection_metrics={"cv": {"mae": 1000.0}},
        final_test_metrics={"test": {"mae": 1200.0}},
        artifact_file=artifact_file,
        artifact_sha256=artifact_hash,
        report_files={},
        report_sha256={},
    )

    manifest = TrainingManifest(
        run_id=uuid4(),
        created_at=datetime.now(UTC),
        markets=[market],
        source_commit="abc123",
        source_dirty=False,
        runtime_versions={"python": "3.11"},
        data_snapshot=DataSnapshot(
            sha256="a" * 64,
            raw_count=100,
            usable_counts={"resale": 80, "presale": 0}
            if market == "resale"
            else {"resale": 0, "presale": 80},
            excluded_counts={"resale": 20, "presale": 0}
            if market == "resale"
            else {"resale": 0, "presale": 20},
            station_counts={"A17": 50, "A18": 30, "A19": 20},
            min_date=date(2024, 1, 1),
            max_date=date(2024, 12, 31),
        ),
        results=[result],
    )

    (candidate_root / "manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )

    return candidate_root, manifest, result


@pytest.fixture
def bundle_with_policy() -> ValuationBundle:
    bundle = _make_bundle("resale", "2.0")
    bundle.parking_price_policy = ParkingPricePolicy(
        version=1,
        minimum_type_samples=20,
        by_type={
            "\u5761\u9053\u5e73\u9762": ParkingPriceStat(price_twd=1_500_000, sample_size=50),
            "\u5761\u9053\u6a5f\u68b0": ParkingPriceStat(price_twd=800_000, sample_size=30),
        },
        market_fallback=ParkingPriceStat(price_twd=1_200_000, sample_size=100),
    )
    return bundle


class TestReleaseSmoke:
    def test_release_smoke_rejects_missing_or_nonpositive_parking_policy(self):
        bundle = _make_bundle("resale")
        bundle.parking_price_policy = ParkingPricePolicy(
            version=1,
            minimum_type_samples=20,
            by_type={},
            market_fallback=None,
        )
        with pytest.raises(ValueError, match="parking price policy"):
            ModelReleaseService._smoke_test("resale", bundle)

    def test_release_smoke_accepts_additive_parking(self, bundle_with_policy):
        ModelReleaseService._smoke_test("resale", bundle_with_policy)


class FakeJobRepository:
    def __init__(self) -> None:
        self._runs: dict[str, JobRun] = {}
        self._keys: dict[str, str] = {}

    def create_or_get(self, run: JobRun):
        key = run.idempotency_key
        if key in self._keys:
            existing = self._runs[self._keys[key]]
            return existing, False
        self._runs[run.run_id] = run
        self._keys[key] = run.run_id
        return run, True

    def get(self, run_id: str) -> JobRun | None:
        return self._runs.get(run_id)

    def find_active_by_key(self, idempotency_key):
        run_id = self._keys.get(idempotency_key)
        if run_id is None:
            return None
        run = self._runs.get(run_id)
        if run is not None and run.status in ("pending", "running", "retry_wait"):
            return run
        return None

    def list_recent(self, limit=20, job_type=None):
        return list(self._runs.values())[:limit]

    def list_active(self, job_type):
        return []

    def update_summary(self, run_id, expected_status, summary):
        run = self._runs.get(run_id)
        if run is not None and run.status == expected_status:
            return True
        return False

    def transition(self, run_id, current_status, target_status, **kwargs):
        run = self._runs.get(run_id)
        if run is None or run.status != current_status:
            return False
        from dataclasses import replace
        from datetime import datetime
        now = datetime.now(UTC)
        started = run.started_at or (
            now if target_status == "running" else run.started_at
        )
        finished = (
            now if target_status in ("succeeded", "failed") else run.finished_at
        )
        new_run = replace(
            run,
            status=target_status,
            started_at=started,
            finished_at=finished,
            output_version=kwargs.get("output_version", run.output_version),
            summary=kwargs.get("summary", run.summary),
            error_code=kwargs.get("error_code", run.error_code),
            error_message=kwargs.get("error_message", run.error_message),
        )
        self._runs[run_id] = new_run
        return True


class TestModelReleaseService:

    def _create_service(
        self, tmp_path: Path, market: str = "resale",
    ) -> tuple[ModelReleaseService, OfficialModelStore, Path]:
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()

        official_store = OfficialModelStore(artifact_dir)

        candidate_dir = tmp_path / "candidates"
        candidate_dir.mkdir()

        from qingpu_insight.model_artifacts import CandidateArtifactStore
        candidate_store = CandidateArtifactStore(candidate_dir)

        release_repo = InMemoryModelReleaseRepository()
        preview_repo = InMemoryOperationPreviewRepository()
        preview_service = OperationPreviewService(
            repository=preview_repo,
            clock=lambda: datetime.now(UTC),
            make_uuid=lambda: "test-preview-id",
        )
        job_repo = FakeJobRepository()
        job_service = JobService(job_repo)

        service = ModelReleaseService(
            official_store=official_store,
            release_repository=release_repo,
            preview_service=preview_service,
            job_service=job_service,
            candidate_store=candidate_store,
            artifact_dir=artifact_dir,
        )
        return service, official_store, candidate_dir

    def test_preview_publish_creates_preview_with_correct_text(
        self, tmp_path: Path
    ) -> None:
        service, _, candidate_dir = self._create_service(tmp_path)
        candidate_root, manifest, _ = _setup_candidate(tmp_path / "src", "resale", recommended=True)

        run_id = str(manifest.run_id)
        dest = candidate_dir / run_id
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        candidate_root.rename(dest)

        preview = service.preview_publish(run_id, "resale")
        assert preview.operation == "model_publish"
        assert preview.payload["run_id"] == run_id
        assert preview.payload["market"] == "resale"
        assert preview.confirmation_text == f"發布 resale {run_id}"

    def test_preview_publish_rejects_not_recommended(
        self, tmp_path: Path
    ) -> None:
        service, _, candidate_dir = self._create_service(tmp_path)
        candidate_root, manifest, _ = _setup_candidate(
            tmp_path / "src", "resale", recommended=False
        )

        run_id = str(manifest.run_id)
        dest = candidate_dir / run_id
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        candidate_root.rename(dest)

        with pytest.raises(ValueError, match="not recommended"):
            service.preview_publish(run_id, "resale")

    def test_preview_publish_rejects_nonexistent_run(
        self, tmp_path: Path
    ) -> None:
        service, _, _ = self._create_service(tmp_path)
        with pytest.raises(ValueError):
            service.preview_publish("nonexistent-run", "resale")

    def test_preview_publish_rejects_presale_before_candidate_lookup(
        self,
        tmp_path: Path,
    ) -> None:
        service, _, _ = self._create_service(tmp_path)
        with pytest.raises(ValueError, match="resale"):
            service.preview_publish("historic-presale-run", "presale")

    def test_preview_rollback_creates_preview(
        self, tmp_path: Path
    ) -> None:
        service, store, candidate_dir = self._create_service(tmp_path)

        candidate_root, manifest, _ = _setup_candidate(tmp_path / "src", "resale", recommended=True)
        run_id = str(manifest.run_id)
        dest = candidate_dir / run_id
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        candidate_root.rename(dest)

        record = store.import_candidate(dest, manifest, "resale")
        store.activate("resale", record.version_id)

        preview = service.preview_rollback("resale", record.version_id)
        assert preview.operation == "model_rollback"
        assert preview.confirmation_text == f"回滾 resale {record.version_id}"

    def test_preview_rollback_rejects_nonexistent_version(
        self, tmp_path: Path
    ) -> None:
        service, _, _ = self._create_service(tmp_path)
        with pytest.raises((ValueError, FileNotFoundError)):
            service.preview_rollback("resale", "nonexistent")

    def test_preview_rollback_rejects_presale_before_store_lookup(
        self,
        tmp_path: Path,
    ) -> None:
        service, _, _ = self._create_service(tmp_path)
        with pytest.raises(ValueError, match="resale"):
            service.preview_rollback("presale", "historic-version")

    def test_submit_consumes_preview_and_creates_job(
        self, tmp_path: Path
    ) -> None:
        service, _, candidate_dir = self._create_service(tmp_path)
        candidate_root, manifest, _ = _setup_candidate(tmp_path / "src", "resale", recommended=True)
        run_id = str(manifest.run_id)
        dest = candidate_dir / run_id
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        candidate_root.rename(dest)

        preview = service.preview_publish(run_id, "resale")
        confirm_text = preview.confirmation_text

        submission = service.submit(preview.preview_id, confirm_text)
        assert submission.created
        assert submission.run.job_type == "model_release"
        assert submission.run.status == "pending"

    def test_submit_rejects_mismatched_confirmation(
        self, tmp_path: Path
    ) -> None:
        service, _, candidate_dir = self._create_service(tmp_path)
        candidate_root, manifest, _ = _setup_candidate(tmp_path / "src", "resale", recommended=True)
        run_id = str(manifest.run_id)
        dest = candidate_dir / run_id
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        candidate_root.rename(dest)

        preview = service.preview_publish(run_id, "resale")
        with pytest.raises(PreviewConfirmationMismatch):
            service.submit(preview.preview_id, "wrong text")

    def test_submit_rejects_consumed_preview(
        self, tmp_path: Path
    ) -> None:
        service, _, candidate_dir = self._create_service(tmp_path)
        candidate_root, manifest, _ = _setup_candidate(tmp_path / "src", "resale", recommended=True)
        run_id = str(manifest.run_id)
        dest = candidate_dir / run_id
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        candidate_root.rename(dest)

        preview = service.preview_publish(run_id, "resale")
        confirm_text = preview.confirmation_text
        service.submit(preview.preview_id, confirm_text)
        with pytest.raises(PreviewAlreadyConsumed):
            service.submit(preview.preview_id, confirm_text)

    def test_submit_rejects_legacy_presale_preview(
        self,
        tmp_path: Path,
    ) -> None:
        service, _, _ = self._create_service(tmp_path)
        preview = service._preview_service.create_for(
            "model_publish",
            {
                "operation": "publish",
                "market": "presale",
                "run_id": "historic-presale-run",
            },
            "legacy confirmation",
        )

        with pytest.raises(ValueError, match="resale"):
            service.submit(preview.preview_id, preview.confirmation_text)
        stored = service._preview_service.get(preview.preview_id)
        assert stored.consumed_at is None

    def _start_job(self, service: ModelReleaseService) -> str:
        submission = service._job_service.create(
            job_type="model_release",
            idempotency_key="test:key",
            trigger="manual",
        )
        service._job_service.start(submission.run.run_id)
        return submission.run.run_id

    def test_execute_publish_full_flow(
        self, tmp_path: Path
    ) -> None:
        service, store, candidate_dir = self._create_service(tmp_path)
        candidate_root, manifest, _ = _setup_candidate(tmp_path / "src", "resale", recommended=True)

        run_id = str(manifest.run_id)
        dest = candidate_dir / run_id
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        candidate_root.rename(dest)

        preview = service.preview_publish(run_id, "resale")
        job_run_id = self._start_job(service)

        result = service.execute(job_run_id, preview)
        assert result.market == "resale"
        assert result.source_run_id == run_id

        current_file = store.current("resale")
        assert current_file is not None
        assert current_file.version_id == result.version_id

    def test_execute_rollback_full_flow(
        self, tmp_path: Path
    ) -> None:
        service, store, candidate_dir = self._create_service(tmp_path)

        candidate_root, manifest, _ = _setup_candidate(tmp_path / "src", "resale", recommended=True)
        run_id = str(manifest.run_id)
        dest = candidate_dir / run_id
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        candidate_root.rename(dest)

        v1 = store.import_candidate(dest, manifest, "resale")
        store.activate("resale", v1.version_id)

        candidate_root2, manifest2, _ = _setup_candidate(
            tmp_path / "src2", "resale", recommended=True
        )
        run_id2 = str(manifest2.run_id)
        dest2 = candidate_dir / run_id2
        if dest2.exists():
            import shutil
            shutil.rmtree(dest2)
        candidate_root2.rename(dest2)

        v2 = store.import_candidate(dest2, manifest2, "resale")
        store.activate("resale", v2.version_id)

        preview = service.preview_rollback("resale", v1.version_id)
        job_run_id = self._start_job(service)

        result = service.execute(job_run_id, preview)
        assert result.market == "resale"

        current_file = store.current("resale")
        assert current_file is not None
        assert current_file.version_id == v1.version_id

    def test_execute_rejects_legacy_presale_preview(
        self,
        tmp_path: Path,
    ) -> None:
        service, _, _ = self._create_service(tmp_path)
        preview = service._preview_service.create_for(
            "model_rollback",
            {
                "operation": "rollback",
                "market": "presale",
                "version_id": "historic-version",
            },
            "legacy confirmation",
        )
        job_run_id = self._start_job(service)

        with pytest.raises(ValueError, match="resale"):
            service.execute(job_run_id, preview)

    def test_execute_publish_failure_restores_file_pointer(
        self, tmp_path: Path
    ) -> None:
        artifact_dir = tmp_path / "artifacts"
        artifact_dir.mkdir()
        official_store = OfficialModelStore(artifact_dir)

        candidate_dir = tmp_path / "candidates"
        candidate_dir.mkdir()
        from qingpu_insight.model_artifacts import CandidateArtifactStore
        candidate_store = CandidateArtifactStore(candidate_dir)

        preview_repo = InMemoryOperationPreviewRepository()
        preview_service = OperationPreviewService(
            repository=preview_repo,
            clock=lambda: datetime.now(UTC),
            make_uuid=lambda: "test-preview-id",
        )
        job_repo = FakeJobRepository()
        job_service = JobService(job_repo)

        class FailingRepo(InMemoryModelReleaseRepository):
            def register_version(self, record):
                raise RuntimeError("MySQL failure")

        failing_repo = FailingRepo()

        candidate_root, manifest, _ = _setup_candidate(
            tmp_path / "src", "resale", recommended=True
        )
        run_id = str(manifest.run_id)
        dest = candidate_dir / run_id
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        candidate_root.rename(dest)

        v1 = official_store.import_candidate(dest, manifest, "resale")
        official_store.activate("resale", v1.version_id)

        service = ModelReleaseService(
            official_store=official_store,
            release_repository=failing_repo,
            preview_service=preview_service,
            job_service=job_service,
            candidate_store=candidate_store,
            artifact_dir=artifact_dir,
        )


        job_submission = service._job_service.create(
            job_type="model_release",
            idempotency_key="test:failure:key",
            trigger="manual",
        )
        service._job_service.start(job_submission.run.run_id)
        failing_job_run_id = job_submission.run.run_id

        with pytest.raises(RuntimeError, match="MySQL failure"):
            service.execute(failing_job_run_id, preview_service.create_for(
                "model_publish",
                {"operation": "publish", "market": "resale", "run_id": run_id},
                "irrelevant",
            ))

        current_file = official_store.current("resale")
        assert current_file is not None
        assert current_file.version_id == v1.version_id


class TestOfficialModelStore:
    def test_import_candidate_verifies_hash_market_and_gate(self, tmp_path: Path) -> None:
        store = OfficialModelStore(tmp_path / "artifacts")
        candidate_dir, approved_manifest, approved_result = _setup_candidate(
            tmp_path, "resale", recommended=True
        )
        record = store.import_candidate(candidate_dir, approved_manifest, "resale")

        assert record.market == "resale"
        assert record.artifact_sha256 == approved_result.artifact_sha256
        assert joblib.load(
            tmp_path / "artifacts" / record.artifact_path
        ).transaction_type == "resale"

    def test_import_rejects_missing_manifest(self, tmp_path: Path) -> None:
        store = OfficialModelStore(tmp_path / "artifacts")
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        manifest = TrainingManifest(
            run_id=uuid4(),
            created_at=datetime.now(UTC),
            markets=["resale"],
            source_commit="x",
            source_dirty=False,
            runtime_versions={"py": "3.11"},
            data_snapshot=DataSnapshot(
                sha256="a" * 64,
                raw_count=0,
                usable_counts={"resale": 0, "presale": 0},
                excluded_counts={"resale": 0, "presale": 0},
                station_counts={"A17": 0, "A18": 0, "A19": 0},
                min_date=date(2024, 1, 1),
                max_date=date(2024, 12, 31),
            ),
            results=[],
        )
        with pytest.raises(FileNotFoundError, match="manifest.json"):
            store.import_candidate(empty_dir, manifest, "resale")

    def test_import_rejects_not_recommended(self, tmp_path: Path) -> None:
        store = OfficialModelStore(tmp_path / "artifacts")
        candidate_dir, manifest, _ = _setup_candidate(
            tmp_path, "resale", recommended=False
        )
        with pytest.raises(ValueError, match="not recommended"):
            store.import_candidate(candidate_dir, manifest, "resale")

    def test_import_rejects_hash_mismatch(self, tmp_path: Path) -> None:
        store = OfficialModelStore(tmp_path / "artifacts")
        candidate_dir, manifest, _ = _setup_candidate(
            tmp_path, "resale", recommended=True
        )
        artifact_path = candidate_dir / "resale.joblib"
        artifact_path.write_bytes(b"tampered-data")
        with pytest.raises(ValueError, match="SHA256 mismatch"):
            store.import_candidate(candidate_dir, manifest, "resale")

    def test_import_rejects_wrong_market(self, tmp_path: Path) -> None:
        store = OfficialModelStore(tmp_path / "artifacts")
        candidate_dir, manifest, _ = _setup_candidate(
            tmp_path, "resale", recommended=True
        )
        bundle = joblib.load(candidate_dir / "resale.joblib")
        bundle.transaction_type = "presale"
        joblib.dump(bundle, candidate_dir / "resale.joblib")
        new_hash = sha256_file(candidate_dir / "resale.joblib")
        manifest.results[0].artifact_sha256 = new_hash
        (candidate_dir / "manifest.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="market mismatch"):
            store.import_candidate(candidate_dir, manifest, "resale")

    def test_activate_resale_does_not_touch_presale_pointer(self, tmp_path: Path) -> None:
        store = OfficialModelStore(tmp_path / "artifacts")

        resale_dir, resale_manifest, _ = _setup_candidate(
            tmp_path / "candidates", "resale", recommended=True
        )
        resale_record = store.import_candidate(resale_dir, resale_manifest, "resale")

        presale_dir, presale_manifest, _ = _setup_candidate(
            tmp_path / "candidates2", "presale", recommended=True
        )
        presale_record = store.import_candidate(
            presale_dir, presale_manifest, "presale"
        )
        store.activate("presale", presale_record.version_id)

        presale_before = store.current("presale")
        store.activate("resale", resale_record.version_id)
        assert store.current("presale") == presale_before

    def test_current_returns_none_before_activation(self, tmp_path: Path) -> None:
        store = OfficialModelStore(tmp_path / "artifacts")
        assert store.current("resale") is None

    def test_current_returns_manifest_after_activation(self, tmp_path: Path) -> None:
        store = OfficialModelStore(tmp_path / "artifacts")
        candidate_dir, manifest, _ = _setup_candidate(
            tmp_path, "resale", recommended=True
        )
        record = store.import_candidate(candidate_dir, manifest, "resale")
        activated = store.activate("resale", record.version_id)

        current = store.current("resale")
        assert current is not None
        assert current == activated

    def test_load_specific_version(self, tmp_path: Path) -> None:
        store = OfficialModelStore(tmp_path / "artifacts")
        candidate_dir, manifest, _ = _setup_candidate(
            tmp_path, "resale", recommended=True
        )
        record = store.import_candidate(candidate_dir, manifest, "resale")

        bundle = store.load("resale", record.version_id)
        assert isinstance(bundle, ValuationBundle)
        assert bundle.transaction_type == "resale"

    def test_load_rejects_path_traversal(self, tmp_path: Path) -> None:
        store = OfficialModelStore(tmp_path / "artifacts")
        with pytest.raises((ValueError, FileNotFoundError)):
            store.load("resale", "../etc/passwd")

    def test_activate_fails_for_nonexistent_version(self, tmp_path: Path) -> None:
        store = OfficialModelStore(tmp_path / "artifacts")
        with pytest.raises(ValueError, match="not found"):
            store.activate("resale", "nonexistent")
