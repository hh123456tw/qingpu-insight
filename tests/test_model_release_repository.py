from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qingpu_insight.model_release_repository import (
    InMemoryModelReleaseRepository,
    ModelVersionRecord,
)


def _record(
    version_id: str = "v1",
    market: str = "resale",
    created_at: datetime | None = None,
) -> ModelVersionRecord:
    return ModelVersionRecord(
        version_id=version_id,
        market=market,
        source_run_id="run-1",
        model_name="test_model",
        model_version="1.0",
        artifact_path="/models/test.parquet",
        artifact_sha256="a" * 64,
        metadata={"metric": 0.95},
        created_at=created_at or datetime.now(UTC),
    )


class TestMigrationSQL:
    def test_migration_has_required_tables(self) -> None:
        sql_path = (
            Path(__file__).parents[1]
            / "database"
            / "007_frontend_operations_schema.sql"
        )
        sql = sql_path.read_text("utf-8")
        assert "CREATE TABLE IF NOT EXISTS model_versions" in sql
        assert "CREATE TABLE IF NOT EXISTS published_models" in sql
        assert "PRIMARY KEY (market)" in sql
        assert "CREATE TABLE IF NOT EXISTS model_release_events" in sql
        assert "CREATE TABLE IF NOT EXISTS operation_previews" in sql


class TestInMemoryModelReleaseRepository:
    @pytest.fixture
    def repo(self) -> InMemoryModelReleaseRepository:
        return InMemoryModelReleaseRepository()

    def test_register_version_stores_record(self, repo: InMemoryModelReleaseRepository) -> None:
        rec = _record()
        repo.register_version(rec)
        versions = repo.list_versions("resale", 10)
        assert len(versions) == 1
        assert versions[0].version_id == "v1"

    def test_register_version_is_idempotent(
        self, repo: InMemoryModelReleaseRepository,
    ) -> None:
        rec = _record()
        repo.register_version(rec)
        repo.register_version(rec)
        versions = repo.list_versions("resale", 10)
        assert len(versions) == 1

    def test_activate_changes_only_requested_market(
        self, repo: InMemoryModelReleaseRepository,
    ) -> None:
        repo.register_version(_record(version_id="v-resale", market="resale"))
        repo.register_version(_record(version_id="v-presale", market="presale"))
        repo.activate("resale", "v-resale", "job-1", "publish")
        assert repo.current("resale") is not None
        assert repo.current("resale").version_id == "v-resale"
        assert repo.current("presale") is None

    def test_current_returns_none_for_unknown_market(
        self, repo: InMemoryModelReleaseRepository,
    ) -> None:
        assert repo.current("resale") is None

    def test_current_returns_none_when_version_not_activated(
        self, repo: InMemoryModelReleaseRepository,
    ) -> None:
        repo.register_version(_record(version_id="v1", market="resale"))
        assert repo.current("resale") is None

    def test_list_versions_respects_limit_and_ordering(
        self, repo: InMemoryModelReleaseRepository,
    ) -> None:
        now = datetime.now(UTC)
        for i in range(3):
            repo.register_version(_record(
                version_id=f"v{i}",
                market="resale",
                created_at=now + timedelta(hours=i),
            ))
        versions = repo.list_versions("resale", 2)
        assert len(versions) == 2
        assert [v.version_id for v in versions] == ["v2", "v1"]

    def test_list_versions_filters_by_market(
        self, repo: InMemoryModelReleaseRepository,
    ) -> None:
        repo.register_version(_record(version_id="v1", market="resale"))
        repo.register_version(_record(version_id="v2", market="presale"))
        versions = repo.list_versions("resale", 10)
        assert all(v.market == "resale" for v in versions)
        assert len(versions) == 1

    def test_activate_raises_error_for_nonexistent_version(
        self, repo: InMemoryModelReleaseRepository,
    ) -> None:
        with pytest.raises(ValueError, match="not registered"):
            repo.activate("resale", "nonexistent", "job-1", "publish")
