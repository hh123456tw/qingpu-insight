import hashlib
import os
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

import joblib
from pydantic import BaseModel, ConfigDict, Field

from qingpu_insight.valuation import ValuationBundle

REPORT_TYPES: dict[str, str] = {
    "resale-evaluation": "reports/resale-evaluation.json",
    "resale-model-card": "reports/resale-model-card.md",
    "presale-evaluation": "reports/presale-evaluation.json",
    "presale-model-card": "reports/presale-model-card.md",
    "manifest": "manifest.json",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DataSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_count: int = Field(ge=0)
    usable_counts: dict[Literal["resale", "presale"], int]
    excluded_counts: dict[Literal["resale", "presale"], int]
    station_counts: dict[Literal["A17", "A18", "A19"], int]
    min_date: date
    max_date: date


class TrainingProfileSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Literal["quick", "balanced", "thorough", "custom"]
    source: Literal["preset", "custom"]
    hgb_learning_rate: float = Field(ge=0.01, le=0.20)
    hgb_max_iter: int = Field(ge=100, le=1000)
    rf_n_estimators: int = Field(ge=100, le=1000)
    recency_half_life_months: int | None = Field(default=None, ge=12, le=84)


class ProfileTrainingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_name: Literal["quick", "balanced", "thorough", "custom"]
    parameters: dict[str, int | float | None]
    selection_metrics: dict[str, dict[str, object]]
    candidate_errors: dict[str, str] = Field(default_factory=dict)


class MarketTrainingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: Literal["resale", "presale"]
    selected_model: str
    recommended: bool
    reason_codes: list[str]
    selection_metrics: dict[str, dict[str, object]]
    final_test_metrics: dict[str, dict[str, object]]
    artifact_file: str
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_files: dict[str, str]
    report_sha256: dict[str, str]
    selected_profile: Literal["quick", "balanced", "thorough", "custom"] | None = None
    profile_results: list[ProfileTrainingResult] = Field(default_factory=list)
    test_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    average_interval_width_twd_per_ping: float | None = Field(default=None, ge=0.0)


class TrainingManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1, 2, 3] = 1
    run_id: UUID
    created_at: datetime
    markets: list[Literal["resale", "presale"]]
    source_commit: str
    source_dirty: bool
    runtime_versions: dict[str, str]
    data_snapshot: DataSnapshot
    results: list[MarketTrainingResult]
    tuning_plan_version: int | None = Field(default=None, ge=1)
    profiles: list[TrainingProfileSnapshot] = Field(default_factory=list)


class CandidateArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _normalize(self, run_id: str) -> str:
        return str(UUID(run_id))

    def begin(self, run_id: str) -> Path:
        try:
            normalized = self._normalize(run_id)
        except ValueError:
            raise ValueError(f"Invalid run_id: {run_id}") from None

        final = self._root / normalized
        if final.exists():
            raise FileExistsError(f"Run {normalized} already exists")

        stage = self._root / f".tmp-{normalized}"
        stage.mkdir(parents=True)
        return stage

    def commit(self, run_id: str, manifest: TrainingManifest) -> Path:
        normalized = self._normalize(run_id)
        if str(manifest.run_id) != normalized:
            raise ValueError(
                f"manifest.run_id {manifest.run_id} does not match run_id {run_id}"
            )

        stage = self._root / f".tmp-{normalized}"
        final = self._root / normalized

        if final.exists():
            raise FileExistsError(f"Run {normalized} already exists")

        manifest_path = stage / "manifest.json"
        with open(manifest_path, encoding="utf-8") as f:
            loaded = TrainingManifest.model_validate_json(f.read())

        if loaded != manifest:
            raise ValueError("Loaded manifest does not match supplied manifest")

        for result in manifest.results:
            artifact_path = stage / result.artifact_file
            if not artifact_path.exists():
                raise FileNotFoundError(f"Artifact {result.artifact_file} not found")
            actual_hash = sha256_file(artifact_path)
            if actual_hash != result.artifact_sha256:
                raise ValueError(f"Hash mismatch for {result.artifact_file}")

            if result.artifact_file.endswith(".joblib"):
                try:
                    bundle = joblib.load(artifact_path)
                except Exception:
                    raise ValueError(f"{result.artifact_file} is not a valid joblib file") from None
                if not isinstance(bundle, ValuationBundle):
                    raise TypeError(
                        f"{result.artifact_file} is not a ValuationBundle"
                    )
                if bundle.transaction_type != result.market:
                    raise ValueError(
                        f"Market mismatch for {result.artifact_file}: "
                        f"expected {result.market}, got {bundle.transaction_type}"
                    )

            for report_key, report_rel in result.report_files.items():
                report_path = stage / report_rel
                if not report_path.exists():
                    raise FileNotFoundError(f"Report {report_rel} not found")
                expected_hash = result.report_sha256[report_key]
                actual_hash = sha256_file(report_path)
                if actual_hash != expected_hash:
                    raise ValueError(
                        f"Hash mismatch for report {report_rel}"
                    )

        os.replace(str(stage), str(final))
        return final

    def discard_staging(self, run_id: str) -> None:
        try:
            normalized = self._normalize(run_id)
        except ValueError:
            raise ValueError(f"Invalid run_id: {run_id}") from None

        stage = (self._root / f".tmp-{normalized}").resolve()
        if not stage.exists():
            return
        if stage.parent != self._root:
            raise ValueError("Invalid staging directory location")
        shutil.rmtree(stage)

    def get(self, run_id: str) -> TrainingManifest | None:
        final = (self._root / self._normalize(run_id)).resolve()
        manifest_path = final / "manifest.json"
        if not manifest_path.exists():
            return None
        with open(manifest_path, encoding="utf-8") as f:
            return TrainingManifest.model_validate_json(f.read())

    def list_recent(self, limit: int = 20) -> list[TrainingManifest]:
        results: list[TrainingManifest] = []
        for entry in self._root.iterdir():
            if entry.name.startswith(".tmp-"):
                continue
            manifest_path = entry / "manifest.json"
            if manifest_path.exists():
                with open(manifest_path, encoding="utf-8") as f:
                    results.append(TrainingManifest.model_validate_json(f.read()))
        results.sort(key=lambda m: m.created_at, reverse=True)
        return results[:limit]

    def report_path(self, run_id: str, report_type: str) -> Path:
        if report_type not in REPORT_TYPES:
            raise ValueError(f"Unknown report_type: {report_type}")
        return (self._root / self._normalize(run_id) / REPORT_TYPES[report_type]).resolve()
