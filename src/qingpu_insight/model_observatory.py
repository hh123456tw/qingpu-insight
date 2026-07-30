from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from qingpu_insight.automl_outputs import AutoMLRunOutputStore
from qingpu_insight.jobs import JobService
from qingpu_insight.model_artifacts import (
    CandidateArtifactStore,
    TrainingManifest,
    sha256_file,
)
from qingpu_insight.model_release import (
    OfficialModelStore,
    source_provenance_blocker,
)
from qingpu_insight.model_training_service import ModelTrainingService, build_data_snapshot
from qingpu_insight.valuation import ValuationBundle

_PUBLIC_MARKET = "resale"


def _project_result(r: Any) -> dict[str, Any]:
    data = r.model_dump(mode="json")
    fe = data.get("feature_experiments", [])
    bt = data.get("backtests", [])
    rc = data.get("release_checks", {})
    data["analysis_available"] = bool(fe or bt or rc)
    return data


def _public_number(value: Any, *, integer: bool = False) -> int | float | None:
    if value is None:
        return None
    try:
        return int(value) if integer else float(value)
    except (TypeError, ValueError):
        return None


def _official_model_report(bundle: ValuationBundle) -> dict[str, Any]:
    metrics = bundle.metrics if isinstance(bundle.metrics, dict) else {}
    overall = metrics.get("overall", {})
    if not isinstance(overall, dict):
        overall = {}

    stations: dict[str, dict[str, int | float | None]] = {}
    for station in ("A17", "A18", "A19"):
        row = metrics.get(f"station:{station}", {})
        if not isinstance(row, dict):
            row = {}
        stations[station] = {
            "mae": _public_number(row.get("mae")),
            "mape": _public_number(row.get("mape")),
            "count": _public_number(row.get("count"), integer=True),
        }

    top_features: list[dict[str, str | float]] = []
    importance = bundle.global_importance
    if isinstance(importance, list):
        for item in importance[:5]:
            if not isinstance(item, dict) or not isinstance(item.get("feature"), str):
                continue
            score = _public_number(item.get("importance"))
            if score is None:
                continue
            top_features.append({"feature": item["feature"], "importance": score})

    report = {
        "evaluation_split": bundle.metrics_split,
        "data_min_date": str(bundle.data_min_date),
        "data_max_date": str(bundle.data_max_date),
        "test_count": _public_number(overall.get("count"), integer=True),
        "overall": {
            "mae": _public_number(overall.get("mae")),
            "mape": _public_number(overall.get("mape")),
            "rmse": _public_number(overall.get("rmse")),
            "r2": _public_number(overall.get("r2")),
        },
        "stations": stations,
        "top_features": top_features,
        "diagnostics": (
            bundle.diagnostics if isinstance(bundle.diagnostics, dict) else {}
        ),
    }
    policy = bundle.parking_price_policy
    if policy is not None:
        report["parking_policy"] = {
            "version": policy.version,
            "by_type": {
                k: {"price_twd": v.price_twd, "sample_size": v.sample_size}
                for k, v in policy.by_type.items()
            },
            "market_fallback": {
                "price_twd": policy.market_fallback.price_twd,
                "sample_size": policy.market_fallback.sample_size,
            }
            if policy.market_fallback
            else None,
        }
    return report


def _project_snapshot(snapshot: Any) -> dict[str, Any]:
    data = snapshot.model_dump(mode="json")
    usable_counts = data.get("usable_counts", {})
    excluded_counts = data.get("excluded_counts", {})
    resale_usable = int(usable_counts.get(_PUBLIC_MARKET, 0))
    resale_excluded = int(excluded_counts.get(_PUBLIC_MARKET, 0))
    data["raw_count"] = resale_usable + resale_excluded
    data["usable_counts"] = {_PUBLIC_MARKET: resale_usable}
    data["excluded_counts"] = {_PUBLIC_MARKET: resale_excluded}
    for unsafe_key in ("station_counts", "min_date", "max_date"):
        data.pop(unsafe_key, None)
    return data


def _project_summary(summary: dict[str, object]) -> dict[str, object]:
    projected = dict(summary)
    for key in ("markets", "completed_markets"):
        value = projected.get(key)
        if isinstance(value, list):
            projected[key] = [_PUBLIC_MARKET] if _PUBLIC_MARKET in value else []
    if projected.get("market") != _PUBLIC_MARKET:
        projected.pop("market", None)
    results = projected.get("results")
    if isinstance(results, list):
        projected["results"] = [
            item
            for item in results
            if isinstance(item, dict) and item.get("market") == _PUBLIC_MARKET
        ]
    data_snapshot = projected.get("data_snapshot")
    if isinstance(data_snapshot, dict):
        public_snapshot = dict(data_snapshot)
        usable_counts = public_snapshot.get("usable_counts", {})
        excluded_counts = public_snapshot.get("excluded_counts", {})
        if isinstance(usable_counts, dict):
            resale_usable = int(usable_counts.get(_PUBLIC_MARKET, 0))
            public_snapshot["usable_counts"] = {_PUBLIC_MARKET: resale_usable}
        else:
            resale_usable = None
            public_snapshot.pop("usable_counts", None)
        if isinstance(excluded_counts, dict):
            resale_excluded = int(excluded_counts.get(_PUBLIC_MARKET, 0))
            public_snapshot["excluded_counts"] = {
                _PUBLIC_MARKET: resale_excluded
            }
        else:
            resale_excluded = None
            public_snapshot.pop("excluded_counts", None)
        if resale_usable is not None and resale_excluded is not None:
            public_snapshot["raw_count"] = resale_usable + resale_excluded
        else:
            public_snapshot.pop("raw_count", None)
        for unsafe_key in ("station_counts", "min_date", "max_date"):
            public_snapshot.pop(unsafe_key, None)
        projected["data_snapshot"] = public_snapshot
    stage = projected.get("stage")
    if isinstance(stage, str) and "presale" in stage:
        projected["stage"] = "processing_model_training"
    return projected


class ModelObservatory:
    def __init__(
        self,
        artifact_dir: Path,
        candidate_store: CandidateArtifactStore,
        model_training_service: ModelTrainingService,
        job_service: JobService,
        input_path: Path | None = None,
        official_store: OfficialModelStore | None = None,
        automl_output_store: AutoMLRunOutputStore | None = None,
    ) -> None:
        self._artifact_dir = artifact_dir
        self._candidate_store = candidate_store
        self._model_training_service = model_training_service
        self._job_service = job_service
        self._input_path = input_path
        self._cached_snapshot: dict[str, Any] | None = None
        self._cached_snapshot_key: tuple[str, int, float] | None = None
        self._official_store = official_store
        self._automl_output_store = automl_output_store

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
            "report": _official_model_report(bundle),
            "warning": "official_manifest_missing",
        }

    def status(self, latest_data_date: pd.Timestamp | None = None) -> dict[str, Any]:
        official_models: dict[str, Any] = {}
        for market in (_PUBLIC_MARKET,):
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
                            "source_run_id": current.source_run_id,
                            "activated_at": current.activated_at.isoformat(),
                            "artifact_sha256": current.artifact_sha256,
                            "report": _official_model_report(bundle),
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
                        "report": _official_model_report(bundle),
                    }
                except Exception:
                    official_models[market] = {
                        "available": False,
                        "role": "official",
                        "warning": f"{market}_model_corrupt",
                    }

        try:
            candidates = [
                manifest
                for manifest in self._candidate_store.list_recent(limit=9999)
                if _PUBLIC_MARKET in manifest.markets
            ]
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
                    resale = frame[frame["transaction_type"] == _PUBLIC_MARKET]
                    usable_resale = resale[resale["analysis_eligible"]]
                    station_counts = (
                        usable_resale["station_code"].value_counts().to_dict()
                    )
                    for station in ("A17", "A18", "A19"):
                        station_counts.setdefault(station, 0)
                    min_date = usable_resale["transaction_date"].min()
                    max_date = usable_resale["transaction_date"].max()
                    self._cached_snapshot = {
                        "sha256": snapshot.sha256,
                        "raw_count": len(resale),
                        "usable_counts": {_PUBLIC_MARKET: len(usable_resale)},
                        "excluded_counts": {
                            _PUBLIC_MARKET: len(resale) - len(usable_resale)
                        },
                        "station_counts": station_counts,
                        "min_date": (
                            min_date.date().isoformat() if pd.notna(min_date) else None
                        ),
                        "max_date": (
                            max_date.date().isoformat() if pd.notna(max_date) else None
                        ),
                    }
                    self._cached_snapshot_key = key
                except Exception:
                    pass
            if self._cached_snapshot is not None:
                result["data_status"] = dict(self._cached_snapshot)

        ref_date = latest_data_date
        if (
            ref_date is None
            and self._cached_snapshot is not None
            and self._cached_snapshot["max_date"] is not None
        ):
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
        if not report_type.startswith("resale-"):
            raise ValueError("model observatory only exposes resale reports")
        return self._candidate_store.report_path(run_id, report_type)

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        jobs = self._job_service.list_recent(9999, job_type="model_training")
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
            manifest = manifests.get(run.run_id)
            if not self._supports_resale(run, manifest):
                continue
            entry: dict[str, Any] = {
                "run_id": run.run_id,
                "status": run.status,
                "trigger": run.trigger,
                "started_at": (run.started_at.isoformat() if run.started_at else None),
                "finished_at": (run.finished_at.isoformat() if run.finished_at else None),
            }
            if manifest is not None:
                entry["markets"] = [_PUBLIC_MARKET]
                entry["created_at"] = manifest.created_at.isoformat()
            else:
                entry["markets"] = [_PUBLIC_MARKET]
            results.append(entry)

        return results[:limit]

    def _supports_resale(
        self,
        run: Any,
        manifest: TrainingManifest | None,
    ) -> bool:
        if run.job_type != "model_training":
            return False
        if manifest is not None:
            return _PUBLIC_MARKET in manifest.markets
        for key in ("markets", "completed_markets"):
            markets = run.summary.get(key)
            if isinstance(markets, list) and markets:
                return _PUBLIC_MARKET in markets
        market = run.summary.get("market")
        if isinstance(market, str):
            return market == _PUBLIC_MARKET
        if self._automl_output_store is not None:
            if self._automl_output_store.get(run.run_id, _PUBLIC_MARKET) is not None:
                return True
            if self._automl_output_store.get(run.run_id, "presale") is not None:
                return False
        return False

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self._job_service.get(run_id)
        if run is None or run.job_type != "model_training":
            return None

        try:
            manifest = self._candidate_store.get(run_id)
        except (FileNotFoundError, Exception):
            manifest = None

        if not self._supports_resale(run, manifest):
            return None

        public_summary = _project_summary(dict(run.summary))
        if manifest is not None and "data_snapshot" in public_summary:
            public_summary["data_snapshot"] = _project_snapshot(
                manifest.data_snapshot
            )
        result: dict[str, Any] = {
            "run_id": run.run_id,
            "status": run.status,
            "trigger": run.trigger,
            "summary": public_summary,
            "started_at": (run.started_at.isoformat() if run.started_at else None),
            "finished_at": (run.finished_at.isoformat() if run.finished_at else None),
        }

        if manifest is not None:
            result["manifest"] = {
                "schema_version": manifest.schema_version,
                "markets": [_PUBLIC_MARKET],
                "created_at": manifest.created_at.isoformat(),
                "source_commit": manifest.source_commit,
                "source_dirty": manifest.source_dirty,
                "data_snapshot": _project_snapshot(manifest.data_snapshot),
                "results": [
                    _project_result(r)
                    for r in manifest.results
                    if r.market == _PUBLIC_MARKET
                ],
                "tuning_plan_version": manifest.tuning_plan_version,
                "profiles": [profile.model_dump(mode="json") for profile in manifest.profiles],
                "legacy_tuning_record": manifest.schema_version < 3,
            }

            markets_info: dict[str, dict[str, Any]] = {}
            for m_result in manifest.results:
                m = m_result.market
                if m != _PUBLIC_MARKET:
                    continue
                blockers: list[str] = []
                publishable = True

                provenance_blocker = source_provenance_blocker(manifest)
                if provenance_blocker is not None:
                    blockers.append(provenance_blocker)
                    publishable = False

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
                        is_current_official = current.source_run_id == str(manifest.run_id)

                markets_info[m] = {
                    "publishable": publishable,
                    "release_blockers": blockers,
                    "current_official_version_id": current_version_id,
                    "is_current_official": is_current_official,
                }

            result["markets"] = markets_info

            if manifest.automl is not None:
                automl = manifest.automl.model_dump(mode="json")
                resale_automl = automl.get("markets", {}).get(_PUBLIC_MARKET)
                automl["markets"] = (
                    {_PUBLIC_MARKET: resale_automl}
                    if resale_automl is not None
                    else {}
                )
                result["manifest"]["automl"] = automl

        if manifest is None and self._automl_output_store is not None:
            markets: dict[str, dict[str, Any]] = {}
            stopped = run.status == "skipped"
            automl_data = self._automl_output_store.get(run_id, _PUBLIC_MARKET)
            if automl_data is not None:
                markets[_PUBLIC_MARKET] = automl_data
                stopped = stopped or bool(automl_data.get("stopped"))
            if markets:
                result["automl"] = {
                    "candidate_available": False,
                    "markets": markets,
                    "stopped": stopped,
                }

        return result
