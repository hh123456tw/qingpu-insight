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

from qingpu_insight.model_artifacts import TrainingManifest, sha256_file
from qingpu_insight.model_release_repository import ModelVersionRecord
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
