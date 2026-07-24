from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from qingpu_insight.backups import (
    BackupRecord,
    BackupService,
    RecordingRunner,
    build_restore_database,
    validate_restore_database,
)
from qingpu_insight.health import (
    HealthItem,
    HealthSummary,
    summarize_health,
)

NOW = datetime.now(UTC)


def _hex_only_id() -> str:
    import uuid
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# 1. Health aggregation distinguishes healthy / warning / critical
# ---------------------------------------------------------------------------


def test_health_aggregation_healthy() -> None:
    s = summarize_health(
        [HealthItem("mysql", "healthy", NOW, "ok", 1, "boolean")], NOW
    )
    assert s.status == "healthy"


def test_health_aggregation_warning() -> None:
    s = summarize_health(
        [
            HealthItem("mysql", "healthy", NOW, "ok", 1, "boolean"),
            HealthItem("disk_free", "warning", NOW, "low", 9 * 1024 ** 3, "bytes"),
        ],
        NOW,
    )
    assert s.status == "warning"


def test_health_aggregation_critical_dominates_warning() -> None:
    s = summarize_health(
        [
            HealthItem("mysql", "healthy", NOW, "ok", 1, "boolean"),
            HealthItem("disk_free", "warning", NOW, "low", 9 * 1024 ** 3, "bytes"),
            HealthItem("latest_backup", "critical", NOW, "no backup", None, None),
        ],
        NOW,
    )
    assert s.status == "critical"


# ---------------------------------------------------------------------------
# 2. Dump produces non-empty file with correct checksum
# ---------------------------------------------------------------------------


class FakeConfig:
    mysql_host = "localhost"
    mysql_port = 3306
    mysql_user = "root"
    mysql_password = "secret"
    mysql_database = "qingpu"


class RecordingRepository:
    def __init__(self) -> None:
        self.records: list[BackupRecord] = []
        self.created: list[BackupRecord] = []
        self.restore_calls: list[tuple[str, str, datetime]] = []

    def create(self, record: BackupRecord) -> None:
        self.created.append(record)
        self.records.append(record)

    def get(self, backup_id: str) -> BackupRecord | None:
        for r in self.records:
            if r.backup_id == backup_id:
                return r
        return None

    def latest(self) -> BackupRecord | None:
        return self.records[-1] if self.records else None

    def list_recent(self, limit: int) -> list[BackupRecord]:
        return self.records[-limit:]

    def mark_restore(
        self, backup_id: str, status: str, checked_at: datetime
    ) -> None:
        self.restore_calls.append((backup_id, status, checked_at))


def test_dump_produces_non_empty_file_with_correct_checksum(tmp_path: Path) -> None:
    dump_content = b"-- MySQL dump snapshot\nCREATE TABLE foo;\n"
    runner = RecordingRunner(dump_content=dump_content)
    repo = RecordingRepository()
    service = BackupService(FakeConfig(), runner, repo, tmp_path)

    record = service.create()

    assert record.status == "completed"
    assert record.size_bytes > 0
    expected_sha = hashlib.sha256(dump_content).hexdigest()
    assert record.sha256 == expected_sha
    backup_file = tmp_path / record.path
    assert backup_file.exists()
    assert backup_file.read_bytes() == dump_content


# ---------------------------------------------------------------------------
# 3. Restore drill verifies core tables and cleans up
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self) -> None:
        self._fetch_index = 0
        self.results: list[tuple | None] = []

    def execute(self, sql: str, params: tuple = ()) -> int:
        del sql, params
        return 0

    def fetchone(self) -> tuple | None:
        if self._fetch_index < len(self.results):
            val = self.results[self._fetch_index]
            self._fetch_index += 1
            return val
        return None

    def fetchall(self) -> list[tuple]:
        remaining = self.results[self._fetch_index:]
        self._fetch_index = len(self.results)
        return remaining

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        pass


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False
        self._cursor = FakeCursor()

    def cursor(self, cursor_class: Any = None) -> FakeCursor:
        del cursor_class
        return self._cursor

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def _make_connect(results: list[tuple]) -> Any:
    def connect(**kwargs):
        del kwargs
        conn = FakeConnection()
        conn._cursor.results = list(results)
        return conn

    return connect


def test_restore_drill_verifies_tables_and_cleans_up(tmp_path: Path) -> None:
    dump_content = b"-- MySQL dump\n"
    runner = RecordingRunner(dump_content=dump_content)
    repo = RecordingRepository()
    results = [
        (5,),       # COUNT job_runs
        (10,),      # COUNT dataset_versions
        (15,),      # COUNT published_datasets
        (20,),      # COUNT listing_current
        ("listings", "v2"),  # published_datasets SELECT
    ]
    service = BackupService(
        FakeConfig(), runner, repo, tmp_path,
        pymysql_connect=_make_connect(results),
    )

    backup_id = _hex_only_id()
    # Manually create the backup record and file in the repo
    backup_file = tmp_path / f"{backup_id}.sql"
    backup_file.write_bytes(dump_content)
    sha256 = hashlib.sha256(dump_content).hexdigest()
    record = BackupRecord(
        backup_id=backup_id,
        status="completed",
        path=f"{backup_id}.sql",
        sha256=sha256,
        size_bytes=len(dump_content),
        created_at=datetime.now(UTC),
    )
    repo.create(record)

    evidence = service.restore_drill(backup_id)

    assert evidence.database_name.startswith("qingpu_restore_drill_")
    validate_restore_database(evidence.database_name)
    assert "job_runs" in evidence.table_names
    assert isinstance(evidence.row_counts, list)
    assert evidence.checked_at is not None

    # Verify cleanup: DROP DATABASE should have been called
    drop_calls = [
        call for call in runner.calls if "DROP DATABASE" in " ".join(call)
    ]
    assert len(drop_calls) >= 1

    # Repository should be marked succeeded
    assert repo.restore_calls[-1][1] == "succeeded"


# ---------------------------------------------------------------------------
# 4. Import / smoke query failure preserves backup metadata and cleans up
# ---------------------------------------------------------------------------


def test_import_failure_preserves_backup_metadata(tmp_path: Path) -> None:
    """Import failure still retains the original backup record and attempts cleanup."""
    dump_content = b"-- MySQL dump\n"
    runner = RecordingRunner(dump_content=dump_content, returncodes=[0, 1])
    repo = RecordingRepository()
    service = BackupService(
        FakeConfig(), runner, repo, tmp_path,
        pymysql_connect=_make_connect([]),
    )

    backup_id = _hex_only_id()
    backup_file = tmp_path / f"{backup_id}.sql"
    backup_file.write_bytes(dump_content)
    sha256 = hashlib.sha256(dump_content).hexdigest()
    record = BackupRecord(
        backup_id=backup_id,
        status="completed",
        path=f"{backup_id}.sql",
        sha256=sha256,
        size_bytes=len(dump_content),
        created_at=datetime.now(UTC),
    )
    repo.create(record)

    with pytest.raises(RuntimeError, match="Import failed"):
        service.restore_drill(backup_id)

    # Original backup record is preserved
    stored = repo.get(backup_id)
    assert stored is not None
    assert stored.status == "completed"
    assert stored.sha256 != ""

    # Cleanup should have been attempted
    drop_calls = [
        call for call in runner.calls if "DROP DATABASE" in " ".join(call)
    ]
    assert len(drop_calls) >= 1

    # Repository should be marked failed
    assert repo.restore_calls[-1][1] == "failed"


# ---------------------------------------------------------------------------
# 5. Web has no restore mutation routes
# ---------------------------------------------------------------------------


class InMemoryMarketDataSource:
    def load(self, filters):
        return __import__("pandas").DataFrame()


@pytest.fixture
def ops_app():
    from qingpu_insight.web import OpsServices, create_app

    class FakeHealthService:
        def run(self) -> HealthSummary:
            return HealthSummary(
                "healthy",
                NOW,
                (HealthItem("mysql", "healthy", NOW, "ok", 1, "boolean"),),
            )

    class FakeBackupRepo:
        def list_recent(self, limit):
            return []

    ops = OpsServices(
        health_service=FakeHealthService(),
        health_repository=None,
        backup_repository=FakeBackupRepo(),
    )
    app = create_app(
        data_source=InMemoryMarketDataSource(),
        ops_services=ops,
    )
    with app.test_client() as client:
        yield client


def test_no_restore_mutation_routes(ops_app) -> None:
    assert ops_app.post("/api/ops/backups").status_code == 405
    assert ops_app.post("/api/ops/restore").status_code == 404
    assert ops_app.get("/api/ops/restore").status_code == 404
    assert ops_app.put("/api/ops/restore").status_code == 404
    assert ops_app.delete("/api/ops/restore").status_code == 404
