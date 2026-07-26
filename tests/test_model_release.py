from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import joblib
import pandas as pd
import pytest
from sklearn.dummy import DummyRegressor

from qingpu_insight.model_artifacts import (
    DataSnapshot,
    MarketTrainingResult,
    TrainingManifest,
    sha256_file,
)
from qingpu_insight.model_release import OfficialModelStore
from qingpu_insight.valuation import ValuationBundle


def _make_bundle(market: str, model_version: str = "1.0") -> ValuationBundle:
    dummy = DummyRegressor(strategy="constant", constant=500_000)
    dummy.fit(  # noqa: E501
        pd.DataFrame(
            {c: [0.0] for c in ["building_area_ping", "station_distance_m", "bedrooms",
                                 "living_rooms", "bathrooms", "building_age_years",
                                 "floor", "total_floors", "parking_area_ping"]}
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
        manifest = TrainingManifest(  # dummy, won't matter
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
        # Tamper the artifact so its hash no longer matches the manifest
        artifact_path = candidate_dir / "resale.joblib"
        artifact_path.write_bytes(b"tampered-data")
        with pytest.raises(ValueError, match="SHA256 mismatch"):
            store.import_candidate(candidate_dir, manifest, "resale")

    def test_import_rejects_wrong_market(self, tmp_path: Path) -> None:
        store = OfficialModelStore(tmp_path / "artifacts")
        candidate_dir, manifest, _ = _setup_candidate(
            tmp_path, "resale", recommended=True
        )
        # Swap the bundle's transaction type after creation
        bundle = joblib.load(candidate_dir / "resale.joblib")
        bundle.transaction_type = "presale"
        joblib.dump(bundle, candidate_dir / "resale.joblib")
        # Recompute hash for the swapped bundle and update manifest
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
