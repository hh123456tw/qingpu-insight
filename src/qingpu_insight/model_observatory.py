from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from qingpu_insight.jobs import JobService
from qingpu_insight.model_artifacts import (
    CandidateArtifactStore,
    TrainingManifest,
    sha256_file,
)
from qingpu_insight.model_release import OfficialModelStore
from qingpu_insight.model_training_service import ModelTrainingService, build_data_snapshot
from qingpu_insight.valuation import ValuationBundle


def _project_result(r: Any) -> dict[str, Any]:
    data = r.model_dump(mode="json")
    fe = data.get("feature_experiments", [])
    bt = data.get("backtests", [])
    rc = data.get("release_checks", {})
    data["analysis_available"] = bool(fe or bt or rc)
    return data


class ModelObservatory:
    def __init__(
        self,
        artifact_dir: Path,
        candidate_store: CandidateArtifactStore,
        model_training_service: ModelTrainingService,
        job_service: JobService,
        input_path: Path | None = None,
        official_store: OfficialModelStore | None = None,
    ) -> None:
        self._artifact_dir = artifact_dir
        self._candidate_store = candidate_store
        self._model_training_service = model_training_service
        self._job_service = job_service
        self._input_path = input_path
        self._cached_snapshot: dict[str, Any] | None = None
        self._cached_snapshot_key: tuple[str, int, float] | None = None
        self._official_store = official_store

    def _legacy_model_status(self, market: str) -> dict[str, Any] | None:
        path = self._artifact_dir / f"{market}.joblib"
        if not path.exists():
            return None
        try:
            bundle: ValuationBundle = joblib.load(path)
            if bundle.transaction_type != market:
                return None
        except Exception:
            return None
        return {
            "available": True,
            "name": bundle.model_name,
            "version": bundle.model_version,
            "role": "legacy_fallback",
            "data_max_date": bundle.data_max_date,
            "warning": "official_manifest_missing",
        }

    def status(self, latest_data_date: pd.Timestamp | None = None) -> dict[str, Any]:
        official_models: dict[str, Any] = {}
        for market in ("resale", "presale"):
            if self._official_store is not None:
                current = self._official_store.current(market)
                if current is not None:
                    try:
                        bundle = self._official_store.load(market, current.version_id)
                        official_models[market] = {
                            "available": True,
                            "name": bundle.model_name,
                            "version": bundle.model_version,
                            "role": "official",
                            "data_max_date": bundle.data_max_date,
                            "version_id": current.version_id,
                        }
                    except Exception:
                        official_models[market] = {
                            "available": False,
                            "role": "official",
                            "warning": f"{market}_model_corrupt",
                        }
                else:
                    official_models[market] = self._legacy_model_status(market) or {
                        "available": False,
                        "role": "official",
                        "warning": f"{market}_model_unavailable",
                    }
            else:
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

        if self._input_path is not None and self._input_path.exists():
            stat = self._input_path.stat()
            key = (str(self._input_path.resolve()), stat.st_size, stat.st_mtime_ns)
            if key != self._cached_snapshot_key:
                try:
                    frame = pd.read_parquet(self._input_path)
                    snapshot = build_data_snapshot(self._input_path, frame)
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

        ref_date = latest_data_date
        if ref_date is None and self._cached_snapshot is not None:
            ref_date = pd.Timestamp(self._cached_snapshot["max_date"])

        for market_info in official_models.values():
            if (
                market_info.get("available")
                and market_info.get("data_max_date") is not None
                and ref_date is not None
            ):
                data_max = pd.Timestamp(market_info["data_max_date"])
                age = (ref_date - data_max).days
                market_info["age_days"] = age
                market_info["stale"] = age > 180
            else:
                market_info["age_days"] = None
                market_info["stale"] = False
            market_info["stale_after_days"] = 180

        return result

    def report_path(self, run_id: str, report_type: str) -> Path:
        return self._candidate_store.report_path(run_id, report_type)

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
                "started_at": (run.started_at.isoformat() if run.started_at else None),
                "finished_at": (run.finished_at.isoformat() if run.finished_at else None),
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
            "started_at": (run.started_at.isoformat() if run.started_at else None),
            "finished_at": (run.finished_at.isoformat() if run.finished_at else None),
        }

        if manifest is not None:
            result["manifest"] = {
                "schema_version": manifest.schema_version,
                "markets": manifest.markets,
                "created_at": manifest.created_at.isoformat(),
                "source_commit": manifest.source_commit,
                "source_dirty": manifest.source_dirty,
                "data_snapshot": manifest.data_snapshot.model_dump(mode="json"),
                "results": [_project_result(r) for r in manifest.results],
                "tuning_plan_version": manifest.tuning_plan_version,
                "profiles": [profile.model_dump(mode="json") for profile in manifest.profiles],
                "legacy_tuning_record": manifest.schema_version < 3,
            }

            markets_info: dict[str, dict[str, Any]] = {}
            for m_result in manifest.results:
                m = m_result.market
                blockers: list[str] = []
                publishable = True

                if not m_result.recommended:
                    blockers.append("not_recommended")
                    publishable = False

                candidate_dir = self._candidate_store._root / str(manifest.run_id)
                artifact_path = candidate_dir / m_result.artifact_file
                if not artifact_path.exists():
                    blockers.append("artifact_missing")
                    publishable = False
                else:
                    actual_hash = sha256_file(artifact_path)
                    if actual_hash != m_result.artifact_sha256:
                        blockers.append("sha256_mismatch")
                        publishable = False
                    else:
                        try:
                            bundle: object = joblib.load(str(artifact_path))
                            if not isinstance(bundle, ValuationBundle):
                                blockers.append("not_a_valuation_bundle")
                                publishable = False
                            elif bundle.transaction_type != m:
                                blockers.append("market_mismatch")
                                publishable = False
                        except Exception:
                            blockers.append("corrupt_artifact")
                            publishable = False

                current_version_id: str | None = None
                is_current_official = False
                if self._official_store is not None:
                    current = self._official_store.current(m)
                    if current is not None:
                        current_version_id = current.version_id
                        is_current_official = (
                            current.source_run_id == str(manifest.run_id)
                        )

                markets_info[m] = {
                    "publishable": publishable,
                    "release_blockers": blockers,
                    "current_official_version_id": current_version_id,
                    "is_current_official": is_current_official,
                }

            result["markets"] = markets_info

        return result
