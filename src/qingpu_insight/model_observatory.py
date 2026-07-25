from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from qingpu_insight.jobs import JobService
from qingpu_insight.model_artifacts import (
    CandidateArtifactStore,
    TrainingManifest,
)
from qingpu_insight.model_training_service import ModelTrainingService, build_data_snapshot
from qingpu_insight.valuation import ValuationBundle


class ModelObservatory:
    def __init__(
        self,
        artifact_dir: Path,
        candidate_store: CandidateArtifactStore,
        model_training_service: ModelTrainingService,
        job_service: JobService,
    ) -> None:
        self._artifact_dir = artifact_dir
        self._candidate_store = candidate_store
        self._model_training_service = model_training_service
        self._job_service = job_service
        self._cached_snapshot: dict[str, Any] | None = None
        self._cached_snapshot_key: tuple[str, int, float] | None = None

    def status(self) -> dict[str, Any]:
        official_models: dict[str, Any] = {}
        for market in ("resale", "presale"):
            path = self._artifact_dir / f"{market}.joblib"
            if not path.exists():
                official_models[market] = {
                    "available": False,
                    "role": "official",
                    "warning": f"{market}_model_unavailable",
                }
                continue
            try:
                bundle: ValuationBundle = joblib.load(path)
                official_models[market] = {
                    "available": True,
                    "name": bundle.model_name,
                    "version": bundle.model_version,
                    "role": "official",
                    "data_max_date": bundle.data_max_date,
                }
            except Exception:
                official_models[market] = {
                    "available": False,
                    "role": "official",
                    "warning": f"{market}_model_corrupt",
                }

        try:
            candidates = self._candidate_store.list_recent(limit=9999)
        except Exception:
            candidates = []

        result: dict[str, Any] = {
            "official_models": official_models,
            "candidate_count": len(candidates),
        }

        input_path: Path | None = getattr(
            self._model_training_service, "_input_path", None
        )
        if input_path is not None and input_path.exists():
            stat = input_path.stat()
            key = (str(input_path.resolve()), stat.st_size, stat.st_mtime_ns)
            if key != self._cached_snapshot_key:
                try:
                    frame = pd.read_parquet(input_path)
                    snapshot = build_data_snapshot(input_path, frame)
                    self._cached_snapshot = {
                        "sha256": snapshot.sha256,
                        "raw_count": snapshot.raw_count,
                        "usable_counts": dict(snapshot.usable_counts),
                        "excluded_counts": dict(snapshot.excluded_counts),
                        "station_counts": dict(snapshot.station_counts),
                        "min_date": snapshot.min_date.isoformat(),
                        "max_date": snapshot.max_date.isoformat(),
                    }
                    self._cached_snapshot_key = key
                except Exception:
                    pass
            if self._cached_snapshot is not None:
                result["data_status"] = dict(self._cached_snapshot)

        return result

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        jobs = self._job_service.list_recent(limit, job_type="model_training")
        manifests: dict[str, TrainingManifest] = {}
        for run in jobs:
            try:
                manifest = self._candidate_store.get(run.run_id)
                if manifest is not None:
                    manifests[run.run_id] = manifest
            except (FileNotFoundError, Exception):
                pass

        results: list[dict[str, Any]] = []
        for run in jobs:
            entry: dict[str, Any] = {
                "run_id": run.run_id,
                "status": run.status,
                "trigger": run.trigger,
                "started_at": (
                    run.started_at.isoformat() if run.started_at else None
                ),
                "finished_at": (
                    run.finished_at.isoformat() if run.finished_at else None
                ),
            }
            manifest = manifests.get(run.run_id)
            if manifest is not None:
                entry["markets"] = manifest.markets
                entry["created_at"] = manifest.created_at.isoformat()
            results.append(entry)

        return results

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self._job_service.get(run_id)
        if run is None:
            return None

        try:
            manifest = self._candidate_store.get(run_id)
        except (FileNotFoundError, Exception):
            manifest = None

        result: dict[str, Any] = {
            "run_id": run.run_id,
            "status": run.status,
            "trigger": run.trigger,
            "started_at": (
                run.started_at.isoformat() if run.started_at else None
            ),
            "finished_at": (
                run.finished_at.isoformat() if run.finished_at else None
            ),
        }

        if manifest is not None:
            result["manifest"] = {
                "markets": manifest.markets,
                "created_at": manifest.created_at.isoformat(),
                "source_commit": manifest.source_commit,
                "source_dirty": manifest.source_dirty,
                "data_snapshot": manifest.data_snapshot.model_dump(mode="json"),
                "results": [
                    r.model_dump(mode="json") for r in manifest.results
                ],
            }

        return result
