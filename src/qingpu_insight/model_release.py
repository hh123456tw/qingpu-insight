from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import joblib

from qingpu_insight.jobs import JobService
from qingpu_insight.model_artifacts import (
    CandidateArtifactStore,
    TrainingManifest,
    sha256_file,
)
from qingpu_insight.model_features import PARKING_FEATURE_COLUMNS, ValuationInput
from qingpu_insight.model_release_repository import (
    ModelReleaseRepository,
    ModelVersionRecord,
)
from qingpu_insight.operation_previews import (
    OperationPreview,
    OperationPreviewService,
)
from qingpu_insight.valuation import ValuationBundle


@dataclass(frozen=True)
class OfficialModelManifest:
    schema_version: Literal[1]
    market: Literal["resale", "presale"]
    version_id: str
    source_run_id: str
    artifact_file: str
    artifact_sha256: str
    activated_at: datetime


class OfficialModelStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _official_dir(self, market: str) -> Path:
        return self._root / "official" / market

    def _versions_dir(self, market: str) -> Path:
        return self._official_dir(market) / "versions"

    def _version_dir(self, market: str, version_id: str) -> Path:
        return self._versions_dir(market) / version_id

    def _current_json(self, market: str) -> Path:
        return self._official_dir(market) / "current.json"

    def import_candidate(
        self, candidate_root: Path, training: TrainingManifest, market: str
    ) -> ModelVersionRecord:
        candidate_root = Path(candidate_root).resolve()

        manifest_path = candidate_root / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"manifest.json not found at {candidate_root}")
        context = manifest_path.read_text(encoding="utf-8")
        disk_manifest = TrainingManifest.model_validate_json(context)
        if disk_manifest != training:
            raise ValueError(
                "manifest on disk does not match supplied training manifest"
            )

        result = None
        for r in training.results:
            if r.market == market:
                result = r
                break
        if result is None:
            raise ValueError(f"no MarketTrainingResult for market {market!r}")
        if not result.recommended:
            raise ValueError(
                f"MarketTrainingResult for {market!r} is not recommended"
            )

        artifact_path = (candidate_root / result.artifact_file).resolve()
        if not artifact_path.exists():
            raise FileNotFoundError(f"artifact {result.artifact_file} not found")
        actual_hash = sha256_file(artifact_path)
        if actual_hash != result.artifact_sha256:
            raise ValueError(f"SHA256 mismatch for {result.artifact_file}")

        try:
            bundle: Any = joblib.load(str(artifact_path))
        except Exception:
            raise ValueError(f"{result.artifact_file} is not a valid joblib file") from None
        if not isinstance(bundle, ValuationBundle):
            raise TypeError(f"{result.artifact_file} is not a ValuationBundle")
        if bundle.transaction_type != market:
            raise ValueError(
                f"market mismatch: expected {market}, got {bundle.transaction_type}"
            )

        version_id = uuid4().hex[:8]
        now = datetime.now(UTC)
        artifact_rel = f"official/{market}/versions/{version_id}/model.joblib"

        versions_dir = self._versions_dir(market)
        staging = versions_dir / f".tmp-{version_id}"
        staging.mkdir(parents=True)

        try:
            dest_artifact = staging / "model.joblib"
            shutil.copy2(str(artifact_path), str(dest_artifact))

            manifest = OfficialModelManifest(
                schema_version=1,
                market=market,  # type: ignore[arg-type]
                version_id=version_id,
                source_run_id=str(training.run_id),
                artifact_file=artifact_rel,
                artifact_sha256=actual_hash,
                activated_at=now,
            )

            manifest_json = staging / "manifest.json"
            manifest_json.write_text(
                json.dumps(
                    {
                        "schema_version": manifest.schema_version,
                        "market": manifest.market,
                        "version_id": manifest.version_id,
                        "source_run_id": manifest.source_run_id,
                        "artifact_file": manifest.artifact_file,
                        "artifact_sha256": manifest.artifact_sha256,
                        "activated_at": manifest.activated_at.isoformat(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            loaded = json.loads(manifest_json.read_text(encoding="utf-8"))
            re_read = OfficialModelManifest(
                schema_version=loaded["schema_version"],
                market=loaded["market"],
                version_id=loaded["version_id"],
                source_run_id=loaded["source_run_id"],
                artifact_file=loaded["artifact_file"],
                artifact_sha256=loaded["artifact_sha256"],
                activated_at=datetime.fromisoformat(loaded["activated_at"]),
            )
            if re_read != manifest:
                raise ValueError("manifest re-read validation failed")

            final_dir = versions_dir / version_id
            os.replace(str(staging), str(final_dir))
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise

        return ModelVersionRecord(
            version_id=version_id,
            market=market,
            source_run_id=str(training.run_id),
            model_name=bundle.model_name,
            model_version=bundle.model_version,
            artifact_path=artifact_rel,
            artifact_sha256=actual_hash,
            metadata={},
            created_at=now,
        )

    def activate(self, market: str, version_id: str) -> OfficialModelManifest:
        version_dir = self._version_dir(market, version_id)
        if not version_dir.exists():
            raise ValueError(f"version {version_id!r} for {market!r} not found")

        manifest_path = version_dir / "manifest.json"
        if not manifest_path.exists():
            raise ValueError(f"manifest for version {version_id!r} not found")

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = OfficialModelManifest(
            schema_version=data["schema_version"],
            market=data["market"],
            version_id=data["version_id"],
            source_run_id=data["source_run_id"],
            artifact_file=data["artifact_file"],
            artifact_sha256=data["artifact_sha256"],
            activated_at=datetime.fromisoformat(data["activated_at"]),
        )

        artifact_path = (self._root / manifest.artifact_file).resolve()
        if not str(artifact_path).startswith(str(self._root)):
            raise ValueError("artifact path is outside store root")
        if not artifact_path.exists():
            raise ValueError(f"artifact {manifest.artifact_file} not found")

        try:
            bundle: Any = joblib.load(str(artifact_path))
        except Exception:
            raise ValueError(f"artifact {manifest.artifact_file} is not valid") from None
        if not isinstance(bundle, ValuationBundle):
            raise TypeError("artifact is not a ValuationBundle")
        if bundle.transaction_type != market:
            raise ValueError(
                f"market mismatch: expected {market}, got {bundle.transaction_type}"
            )

        current_json = self._current_json(market)
        tmp = current_json.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")

        try:
            bundle2: Any = joblib.load(str(artifact_path))
            if not isinstance(bundle2, ValuationBundle):
                raise TypeError("re-loaded artifact is not a ValuationBundle")
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

        os.replace(str(tmp), str(current_json))
        return manifest

    def current(self, market: str) -> OfficialModelManifest | None:
        current_json = self._current_json(market)
        if not current_json.exists():
            return None

        data = json.loads(current_json.read_text(encoding="utf-8"))
        return OfficialModelManifest(
            schema_version=data["schema_version"],
            market=data["market"],
            version_id=data["version_id"],
            source_run_id=data["source_run_id"],
            artifact_file=data["artifact_file"],
            artifact_sha256=data["artifact_sha256"],
            activated_at=datetime.fromisoformat(data["activated_at"]),
        )

    def load(self, market: str, version_id: str) -> ValuationBundle:
        version_dir = self._version_dir(market, version_id)
        artifact_path = (version_dir / "model.joblib").resolve()

        if not str(artifact_path).startswith(str(self._root)):
            raise ValueError("artifact path is outside store root")
        if not artifact_path.exists():
            raise FileNotFoundError(
                f"model.joblib for {market}/{version_id} not found"
            )

        bundle: Any = joblib.load(str(artifact_path))
        if not isinstance(bundle, ValuationBundle):
            raise TypeError("loaded object is not a ValuationBundle")
        if bundle.transaction_type != market:
            raise ValueError(
                f"market mismatch: expected {market}, got {bundle.transaction_type}"
            )
        return bundle


STAGES = (
    "validating_candidate",
    "backing_up_pointer",
    "importing_version",
    "smoke_testing",
    "activating",
    "recording_release",
)


_SMOKE_INPUTS: dict[str, ValuationInput] = {
    "resale": ValuationInput(
        transaction_type="resale",
        station_code="A17",
        station_distance_m=500.0,
        building_area_ping=30.0,
        building_type="公寓",
        bedrooms=3,
        living_rooms=2,
        bathrooms=2,
        building_age_years=20.0,
        floor=3,
        total_floors=5,
    ),
    "presale": ValuationInput(
        transaction_type="presale",
        station_code="A17",
        station_distance_m=500.0,
        building_area_ping=30.0,
        building_type="大樓",
        bedrooms=3,
        living_rooms=2,
        bathrooms=2,
        floor=3,
        total_floors=15,
    ),
}


class ModelReleaseService:
    def __init__(
        self,
        official_store: OfficialModelStore,
        release_repository: ModelReleaseRepository,
        preview_service: OperationPreviewService,
        job_service: JobService,
        candidate_store: CandidateArtifactStore,
        artifact_dir: Path,
    ) -> None:
        self._official_store = official_store
        self._release_repository = release_repository
        self._preview_service = preview_service
        self._job_service = job_service
        self._candidate_store = candidate_store
        self._artifact_dir = artifact_dir

    def preview_publish(self, run_id: str, market: str) -> OperationPreview:
        manifest = self._candidate_store.get(run_id)
        if manifest is None:
            raise ValueError(f"candidate run {run_id!r} not found")

        result = None
        for r in manifest.results:
            if r.market == market:
                result = r
                break
        if result is None:
            raise ValueError(f"no result for market {market!r} in run {run_id!r}")

        if not result.recommended:
            raise ValueError(
                f"market {market!r} result is not recommended"
            )

        candidate_dir = self._candidate_store._root / str(manifest.run_id)
        artifact_path = (candidate_dir / result.artifact_file).resolve()
        if not artifact_path.exists():
            raise FileNotFoundError(f"artifact {result.artifact_file} not found")
        actual_hash = sha256_file(artifact_path)
        if actual_hash != result.artifact_sha256:
            raise ValueError(f"SHA256 mismatch for {result.artifact_file}")

        try:
            bundle: object = joblib.load(str(artifact_path))
        except Exception:
            raise ValueError(f"{result.artifact_file} is not a valid joblib file") from None
        if not isinstance(bundle, ValuationBundle):
            raise TypeError(f"{result.artifact_file} is not a ValuationBundle")
        if bundle.transaction_type != market:
            raise ValueError(
                f"market mismatch: expected {market}, got {bundle.transaction_type}"
            )

        return self._preview_service.create_for(
            operation="model_publish",
            payload={"operation": "publish", "market": market, "run_id": run_id},
            confirmation_text=f"發布 {market} {run_id}",
        )

    def preview_rollback(self, market: str, version_id: str) -> OperationPreview:
        self._official_store.load(market, version_id)

        return self._preview_service.create_for(
            operation="model_rollback",
            payload={
                "operation": "rollback", "market": market, "version_id": version_id,
            },
            confirmation_text=f"回滾 {market} {version_id}",
        )

    def submit(self, preview_id: str, confirmation_text: str) -> object:

        preview = self._preview_service.consume(preview_id, confirmation_text)
        market = str(preview.payload["market"])
        idempotency_key = f"model_release:{market}:active"
        submission = self._job_service.create(
            job_type="model_release",
            idempotency_key=idempotency_key,
            trigger="manual",
        )
        return submission

    def execute(
        self, run_id: str, preview: OperationPreview
    ) -> ModelVersionRecord:
        op = preview.payload.get("operation")
        if op == "publish":
            return self._execute_publish(run_id, preview)
        elif op == "rollback":
            return self._execute_rollback(run_id, preview)
        else:
            raise ValueError(f"unknown operation {op!r}")

    def handoff(self, submission, preview, executor) -> None:
        executor.submit(
            submission.run.run_id,
            lambda: self.execute(submission.run.run_id, preview),
        )

    def _progress(self, run_id: str, stage: str) -> None:
        self._job_service.progress(
            run_id, {"stage": stage, "timestamp": datetime.now(UTC).isoformat()}
        )

    def _execute_publish(
        self, run_id: str, preview: OperationPreview
    ) -> ModelVersionRecord:
        market = str(preview.payload["market"])
        source_run_id = str(preview.payload["run_id"])
        self._progress(run_id, "validating_candidate")

        manifest = self._candidate_store.get(source_run_id)
        if manifest is None:
            raise ValueError(f"candidate run {source_run_id!r} not found")

        prev_file = self._official_store.current(market)
        self._progress(run_id, "backing_up_pointer")

        candidate_root = self._candidate_store._root / str(manifest.run_id)
        version_record = self._official_store.import_candidate(
            candidate_root, manifest, market
        )
        self._progress(run_id, "importing_version")

        bundle = self._official_store.load(market, version_record.version_id)
        self._smoke_test(market, bundle)
        self._progress(run_id, "smoke_testing")

        prev_file_version = prev_file.version_id if prev_file else None
        try:
            self._official_store.activate(market, version_record.version_id)
            self._progress(run_id, "activating")

            current = self._official_store.current(market)
            if current is None or current.version_id != version_record.version_id:
                raise ValueError("file pointer activation verification failed")

            self._release_repository.register_version(version_record)
            self._release_repository.activate(
                market, version_record.version_id, run_id, "publish"
            )
            self._progress(run_id, "recording_release")
        except Exception:
            self._restore_file_pointer(market, prev_file_version)
            raise

        return version_record

    def _execute_rollback(
        self, run_id: str, preview: OperationPreview
    ) -> ModelVersionRecord:
        market = str(preview.payload["market"])
        version_id = str(preview.payload["version_id"])
        self._progress(run_id, "validating_candidate")

        bundle = self._official_store.load(market, version_id)

        prev_file = self._official_store.current(market)
        self._progress(run_id, "backing_up_pointer")

        prev_file_version = prev_file.version_id if prev_file else None
        try:
            activated = self._official_store.activate(market, version_id)
            self._progress(run_id, "activating")

            current = self._official_store.current(market)
            if current is None or current.version_id != version_id:
                raise ValueError("file pointer activation verification failed")

            existing_versions = self._release_repository.list_versions(market, 100)
            version_exists = any(
                v.version_id == version_id for v in existing_versions
            )
            if not version_exists:
                record = ModelVersionRecord(
                    version_id=activated.version_id,
                    market=activated.market,
                    source_run_id=activated.source_run_id,
                    model_name=bundle.model_name,
                    model_version=bundle.model_version,
                    artifact_path=activated.artifact_file,
                    artifact_sha256=activated.artifact_sha256,
                    metadata={},
                    created_at=activated.activated_at,
                )
                self._release_repository.register_version(record)
            else:
                record = next(v for v in existing_versions if v.version_id == version_id)

            self._release_repository.activate(
                market, version_id, run_id, "rollback"
            )
            self._progress(run_id, "recording_release")
        except Exception:
            self._restore_file_pointer(market, prev_file_version)
            raise

        return record

    def _restore_file_pointer(
        self, market: str, previous_version_id: str | None
    ) -> None:
        if previous_version_id is not None:
            try:
                self._official_store.activate(market, previous_version_id)
            except Exception:
                raise RuntimeError(
                    f"CRITICAL: file pointer for {market} may be inconsistent"
                ) from None

    @staticmethod
    def _smoke_test(market: str, bundle: ValuationBundle) -> None:
        from qingpu_insight.parking_valuation import estimate_parking_price
        from qingpu_insight.valuation import compose_total_price

        # Verify no parking features in bundle
        for col in PARKING_FEATURE_COLUMNS:
            if col in bundle.feature_columns:
                raise ValueError(f"parking feature {col} must not be in feature_columns")

        # Verify policy exists and has positive fallback
        policy = bundle.parking_price_policy
        if policy is None or policy.market_fallback is None or policy.market_fallback.price_twd <= 0:
            raise ValueError("parking price policy must have positive market fallback")

        test_input = _SMOKE_INPUTS.get(market)
        if test_input is None:
            raise ValueError(f"no smoke test input for market {market!r}")

        import pandas as pd

        from qingpu_insight.model_features import input_frame
        data_date = pd.Timestamp(bundle.data_max_date)
        row = input_frame(test_input, data_date)
        bundle.pipeline.predict(row)

        # Verify parking consistency
        unit_price = float(bundle.pipeline.predict(row)[0])

        # No parking case
        no_parking_est = estimate_parking_price(policy, "")
        _, _, no_parking_total = compose_total_price(unit_price, 30.0, no_parking_est)

        # Slope flat
        flat_est = estimate_parking_price(policy, "\u5761\u9053\u5e73\u9762")
        _, _, flat_total = compose_total_price(unit_price, 30.0, flat_est)

        # Slope mechanical
        mechanical_est = estimate_parking_price(policy, "\u5761\u9053\u6a5f\u68b0")
        _, _, mechanical_total = compose_total_price(unit_price, 30.0, mechanical_est)

        if not (flat_total > no_parking_total):
            raise ValueError("parking consistency: slope flat total must exceed no-parking total")
        if not (mechanical_total > no_parking_total):
            raise ValueError("parking consistency: slope mechanical total must exceed no-parking total")
