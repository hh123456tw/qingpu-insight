import io
import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import joblib
import pandas as pd
import pytest

from qingpu_insight.model_artifacts import (
    REPORT_TYPES,
    CandidateArtifactStore,
    DataSnapshot,
    MarketTrainingResult,
    ProfileTrainingResult,
    TrainingManifest,
    TrainingProfileSnapshot,
    sha256_file,
)
from qingpu_insight.valuation import ValuationBundle


def _make_bundle(market: str) -> bytes:
    bundle = ValuationBundle(
        transaction_type=market,
        model_name="ridge",
        model_version="1.0",
        pipeline=None,
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
    buf = io.BytesIO()
    joblib.dump(bundle, buf)
    return buf.getvalue()


def manifest_fixture(run_id: str, artifact_hash: str, report_hash: str) -> TrainingManifest:
    return TrainingManifest(
        run_id=UUID(run_id),
        created_at=datetime.now(UTC),
        markets=["resale"],
        source_commit="abc123def456",
        source_dirty=False,
        runtime_versions={"python": "3.11"},
        data_snapshot=DataSnapshot(
            sha256="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            raw_count=100,
            usable_counts={"resale": 80, "presale": 0},
            excluded_counts={"resale": 20, "presale": 0},
            station_counts={"A17": 30, "A18": 25, "A19": 25},
            min_date=date(2024, 1, 1),
            max_date=date(2024, 12, 31),
        ),
        results=[
            MarketTrainingResult(
                market="resale",
                selected_model="ridge",
                recommended=True,
                reason_codes=["best_cv_score"],
                selection_metrics={"cv": {"mae": 1000.0}},
                final_test_metrics={"test": {"mae": 1200.0}},
                artifact_file="resale.joblib",
                artifact_sha256=artifact_hash,
                report_files={"resale-evaluation": "reports/resale-evaluation.json"},
                report_sha256={"resale-evaluation": report_hash},
            )
        ],
)

_TEST_SHA256 = "a" * 64


def committed_store_fixture(tmp_path: Path) -> tuple[CandidateArtifactStore, TrainingManifest]:
    store = CandidateArtifactStore(tmp_path / "candidates")
    stage = store.begin("00000000-0000-4000-8000-000000000001")

    artifact_bytes = _make_bundle("resale")
    artifact = stage / "resale.joblib"
    artifact.write_bytes(artifact_bytes)

    report = stage / "reports" / "resale-evaluation.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"selected_model":"ridge"}', encoding="utf-8")

    manifest = manifest_fixture(
        run_id="00000000-0000-4000-8000-000000000001",
        artifact_hash=sha256_file(artifact),
        report_hash=sha256_file(report),
    )
    (stage / "manifest.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )

    store.commit(str(manifest.run_id), manifest)
    return store, manifest


class TestCandidateArtifactStore:
    def test_commits_a_valid_run_atomically(self, tmp_path: Path) -> None:
        store = CandidateArtifactStore(tmp_path / "artifacts" / "candidates")
        stage = store.begin("00000000-0000-4000-8000-000000000001")

        artifact_bytes = _make_bundle("resale")
        artifact = stage / "resale.joblib"
        artifact.write_bytes(artifact_bytes)

        report = stage / "reports" / "resale-evaluation.json"
        report.parent.mkdir()
        report.write_text('{"selected_model":"ridge"}', encoding="utf-8")

        manifest = manifest_fixture(
            run_id="00000000-0000-4000-8000-000000000001",
            artifact_hash=sha256_file(artifact),
            report_hash=sha256_file(report),
        )
        (stage / "manifest.json").write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )

        committed = store.commit(str(manifest.run_id), manifest)

        assert committed.name == str(manifest.run_id)
        assert not stage.exists()
        assert store.get(str(manifest.run_id)) == manifest

    @pytest.mark.parametrize("run_id", ["../escape", "not-a-uuid", ""])
    def test_rejects_unsafe_run_ids(self, tmp_path: Path, run_id: str) -> None:
        store = CandidateArtifactStore(tmp_path / "candidates")
        with pytest.raises(ValueError, match="run_id"):
            store.begin(run_id)

    def test_never_overwrites_a_completed_run(self, tmp_path: Path) -> None:
        store, manifest = committed_store_fixture(tmp_path)
        with pytest.raises(FileExistsError):
            store.begin(str(manifest.run_id))

    def test_report_lookup_accepts_only_the_fixed_whitelist(self, tmp_path: Path) -> None:
        store, manifest = committed_store_fixture(tmp_path)
        with pytest.raises(ValueError, match="report_type"):
            store.report_path(str(manifest.run_id), "../../resale.joblib")


class TestManifestHelpers:
    def test_manifest_fixture_roundtrip(self) -> None:
        h = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        manifest = manifest_fixture(
            run_id="00000000-0000-4000-8000-000000000001",
            artifact_hash=h,
            report_hash=h,
        )
        assert manifest.schema_version == 1
        assert isinstance(manifest.run_id, UUID)

        loaded = TrainingManifest.model_validate_json(manifest.model_dump_json())
        assert loaded.tuning_plan_version is None
        assert loaded.profiles == []
        assert loaded.results[0].selected_profile is None
        assert loaded.results[0].profile_results == []
        assert loaded.results[0].test_coverage is None

    def test_schema_v3_round_trips_tuning_evidence(self) -> None:
        h = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        quick = TrainingProfileSnapshot(
            name="quick",
            source="preset",
            hgb_learning_rate=0.08,
            hgb_max_iter=180,
            rf_n_estimators=160,
            recency_half_life_months=48,
        )
        manifest_v1 = manifest_fixture(
            run_id="00000000-0000-4000-8000-000000000001",
            artifact_hash=h,
            report_hash=h,
        )
        result = manifest_v1.results[0].model_copy(update={
            "selected_profile": "quick",
            "profile_results": [
                ProfileTrainingResult(
                    profile_name="quick",
                    parameters={
                        "hgb_learning_rate": 0.08,
                        "hgb_max_iter": 180,
                        "rf_n_estimators": 160,
                        "recency_half_life_months": 48,
                    },
                    selection_metrics={"ridge": {"overall": {"mae": 50_000}}},
                    candidate_errors={},
                )
            ],
            "test_coverage": 0.9,
            "average_interval_width_twd_per_ping": 120_000,
        })
        manifest = manifest_v1.model_copy(update={
            "schema_version": 3,
            "tuning_plan_version": 1,
            "profiles": [quick],
            "results": [result],
        })
        loaded = TrainingManifest.model_validate_json(manifest.model_dump_json())
        assert loaded.schema_version == 3
        assert loaded.results[0].selected_profile == "quick"
        assert loaded.results[0].test_coverage == 0.9

        mismatched = manifest.model_dump()
        mismatched["results"][0]["profile_results"][0]["parameters"][
            "hgb_max_iter"
        ] = 999
        with pytest.raises(ValueError, match="parameters must match snapshot"):
            TrainingManifest.model_validate(mismatched)

    def test_schema_v3_rejects_incomplete_tuning_evidence(self) -> None:
        h = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        incomplete = manifest_fixture(
            run_id="00000000-0000-4000-8000-000000000001",
            artifact_hash=h,
            report_hash=h,
        ).model_copy(update={"schema_version": 3})

        with pytest.raises(ValueError, match="schema v3"):
            TrainingManifest.model_validate(incomplete.model_dump())

    def test_sha256_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello")
        h = sha256_file(f)
        assert h == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        assert len(h) == 64


class TestStoreSafety:
    def test_discard_staging_noop_when_absent(self, tmp_path: Path) -> None:
        store = CandidateArtifactStore(tmp_path / "candidates")
        store.discard_staging("00000000-0000-4000-8000-000000000001")

    def test_discard_staging_refuses_invalid_uuid(self, tmp_path: Path) -> None:
        store = CandidateArtifactStore(tmp_path / "candidates")
        with pytest.raises(ValueError, match="run_id"):
            store.discard_staging("../escape")

    def test_get_returns_none_for_incomplete_run(self, tmp_path: Path) -> None:
        store = CandidateArtifactStore(tmp_path / "candidates")
        store.begin("00000000-0000-4000-8000-000000000001")
        assert store.get("00000000-0000-4000-8000-000000000001") is None

    def test_list_recent_skips_tmp_dirs(self, tmp_path: Path) -> None:
        store = CandidateArtifactStore(tmp_path / "candidates")
        stage = store.begin("00000000-0000-4000-8000-000000000001")
        (stage / "manifest.json").write_text("{}", encoding="utf-8")
        assert len(store.list_recent()) == 0
        assert len(store.list_recent()) == 0

    def test_report_path_all_whitelisted_types(self, tmp_path: Path) -> None:
        store, manifest = committed_store_fixture(tmp_path)
        rid = str(manifest.run_id)
        for rtype in REPORT_TYPES:
            p = store.report_path(rid, rtype)
            assert p.is_relative_to(store._root / rid)


class TestCommitIntegrity:
    def test_rejects_mutated_artifact(self, tmp_path: Path) -> None:
        store, _ = committed_store_fixture(tmp_path)
        store2 = CandidateArtifactStore(tmp_path / "candidates2")

        rid = "11111111-1111-4111-8111-111111111111"
        stage2 = store2.begin(rid)

        src = tmp_path / "candidates" / "00000000-0000-4000-8000-000000000001"
        for f in src.rglob("*"):
            if f.is_file():
                rel = f.relative_to(src)
                dest = stage2 / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(f.read_bytes())

        artifact_path = stage2 / "resale.joblib"
        artifact_path.write_bytes(b"tampered-data")

        new_hash = sha256_file(artifact_path)
        manifest2 = manifest_fixture(
            run_id=rid,
            artifact_hash=new_hash,
            report_hash=sha256_file(stage2 / "reports" / "resale-evaluation.json"),
        )
        (stage2 / "manifest.json").write_text(manifest2.model_dump_json(indent=2), encoding="utf-8")

        with pytest.raises((ValueError, TypeError)):
            store2.commit(rid, manifest2)

    def test_rejects_report_hash_mismatch(self, tmp_path: Path) -> None:
        store = CandidateArtifactStore(tmp_path / "candidates")
        stage = store.begin("22222222-2222-4222-8222-222222222222")

        artifact_bytes = _make_bundle("resale")
        artifact = stage / "resale.joblib"
        artifact.write_bytes(artifact_bytes)

        report = stage / "reports" / "resale-evaluation.json"
        report.parent.mkdir(parents=True)
        report.write_text('{"selected_model":"ridge"}', encoding="utf-8")

        fake_hash = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        manifest = manifest_fixture(
            run_id="22222222-2222-4222-8222-222222222222",
            artifact_hash=sha256_file(artifact),
            report_hash=fake_hash,
        )
        (stage / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

        with pytest.raises(ValueError, match="Hash mismatch"):
            store.commit("22222222-2222-4222-8222-222222222222", manifest)


@pytest.fixture
def schema_v1_manifest_json():
    """A minimal schema-v1 manifest JSON string."""
    return json.dumps(
        {
            "schema_version": 1,
            "run_id": "33333333-3333-4333-8333-333333333333",
            "created_at": "2024-06-15T12:00:00Z",
            "markets": ["resale"],
            "source_commit": "abc123",
            "source_dirty": False,
            "runtime_versions": {"python": "3.11"},
            "data_snapshot": {
                "sha256": _TEST_SHA256,
                "raw_count": 100,
                "usable_counts": {"resale": 80, "presale": 0},
                "excluded_counts": {"resale": 20, "presale": 0},
                "station_counts": {"A17": 30, "A18": 25, "A19": 25},
                "min_date": "2024-01-01",
                "max_date": "2024-12-31",
            },
            "results": [
                {
                    "market": "resale",
                    "selected_model": "ridge",
                    "recommended": True,
                    "reason_codes": ["best_cv_score"],
                    "selection_metrics": {"cv": {"mae": 1000.0}},
                    "final_test_metrics": {"test": {"mae": 1200.0}},
                    "artifact_file": "resale.joblib",
                    "artifact_sha256": _TEST_SHA256,
                    "report_files": {"resale-evaluation": "reports/resale-evaluation.json"},
                    "report_sha256": {
                        "resale-evaluation": _TEST_SHA256
                    },
                }
            ],
        }
    )


@pytest.fixture
def manifest_v2():
    return TrainingManifest(
        schema_version=2,
        run_id=UUID("44444444-4444-4444-8444-444444444444"),
        created_at=datetime.now(UTC),
        markets=["resale"],
        source_commit="abc123",
        source_dirty=False,
        runtime_versions={"python": "3.11"},
        data_snapshot=DataSnapshot(
            sha256=_TEST_SHA256,
            raw_count=100,
            usable_counts={"resale": 80, "presale": 0},
            excluded_counts={"resale": 20, "presale": 0},
            station_counts={"A17": 30, "A18": 25, "A19": 25},
            min_date=date(2024, 1, 1),
            max_date=date(2024, 12, 31),
        ),
        results=[
            MarketTrainingResult(
                market="resale",
                selected_model="ridge",
                recommended=True,
                reason_codes=["best_cv_score"],
                selection_metrics={"cv": {"mae": 1000.0}},
                final_test_metrics={"test": {"mae": 1200.0}},
                artifact_file="resale.joblib",
                artifact_sha256=_TEST_SHA256,
                report_files={"resale-evaluation": "reports/resale-evaluation.json"},
                report_sha256={
                    "resale-evaluation": _TEST_SHA256
                },
                feature_contract_version=2,
                feature_columns=["station_code", "station_distance_m"],
                diagnostics={"station_counts": {"A18": 25}},
                feature_experiments=[
                    {"name": "enhanced", "selected_model": "hist_gradient_boosting"}
                ],
                backtests=[{"cutoff_date": "2026-06-12", "passed": True}],
                release_checks={"a18_improved": True, "recommended": True},
            )
        ],
    )


class TestSchemaV2:
    def test_schema_v1_manifest_loads_with_empty_analysis_fields(self, schema_v1_manifest_json):
        manifest = TrainingManifest.model_validate_json(schema_v1_manifest_json)
        result = manifest.results[0]
        assert manifest.schema_version == 1
        assert result.feature_columns == []
        assert result.diagnostics == {}
        assert result.feature_experiments == []
        assert result.backtests == []
        assert result.release_checks == {}

    def test_schema_v2_manifest_round_trips_analysis(self, manifest_v2):
        loaded = TrainingManifest.model_validate_json(manifest_v2.model_dump_json())
        assert loaded.schema_version == 2
        assert loaded.results[0].feature_contract_version == 2
        assert loaded.results[0].release_checks["a18_improved"] is True
