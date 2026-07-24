from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from qingpu_insight.backups import (
    BackupRecord,
    BackupService,
    RecordingRunner,
    UnsafeRestoreTarget,
    build_restore_database,
    hash_file,
    resolve_backup_path,
    validate_restore_database,
)

CONFIG = SimpleNamespace(
    mysql_host="localhost",
    mysql_port=3306,
    mysql_user="root",
    mysql_password="secret",
    mysql_database="qingpu",
)

NOW = datetime.now(UTC)


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

    def mark_restore(self, backup_id: str, status: str, checked_at: datetime) -> None:
        self.restore_calls.append((backup_id, status, checked_at))


REPO = RecordingRepository()


def test_backup_uses_env_password_and_argument_array(tmp_path: Path) -> None:
    runner = RecordingRunner()
    service = BackupService(config=CONFIG, runner=runner, repository=REPO, backup_dir=tmp_path)
    record = service.create()
    assert runner.args[0] == "mysqldump"
    assert "--result-file" in runner.args
    assert not any(CONFIG.mysql_password in arg for arg in runner.args)
    assert runner.env["MYSQL_PWD"] == "secret"
    assert "secret" not in repr(record)


def test_backup_sha256_calculated(tmp_path: Path) -> None:
    content = b"known dump content for sha256 test\n"
    runner = RecordingRunner(dump_content=content)
    repo = RecordingRepository()
    service = BackupService(config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path)
    record = service.create()
    assert record.status == "completed"
    expected = hashlib.sha256(content).hexdigest()
    assert record.sha256 == expected
    assert record.size_bytes == len(content)
    final_path = tmp_path / f"{record.backup_id}.sql"
    assert final_path.exists()
    assert final_path.read_bytes() == content


def test_partial_file_cleanup_on_failure(tmp_path: Path) -> None:
    runner = RecordingRunner(returncode=1)
    repo = RecordingRepository()
    service = BackupService(config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path)
    record = service.create()
    assert record.status == "dump_failed"
    partials = list(tmp_path.glob("*.partial"))
    finals = list(tmp_path.glob("*.sql"))
    assert len(partials) == 0
    assert len(finals) == 0


def test_empty_dump_rejected(tmp_path: Path) -> None:
    runner = RecordingRunner(dump_content=b"")
    repo = RecordingRepository()
    service = BackupService(config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path)
    record = service.create()
    assert record.status == "checksum_failed"
    partials = list(tmp_path.glob("*.partial"))
    finals = list(tmp_path.glob("*.sql"))
    assert len(partials) == 0
    assert len(finals) == 0


def test_process_failure_doesnt_leave_ready_backup(tmp_path: Path) -> None:
    runner = RecordingRunner(returncode=1, dump_content=b"some content")
    repo = RecordingRepository()
    service = BackupService(config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path)
    record = service.create()
    assert record.status == "dump_failed"
    partials = list(tmp_path.glob("*.partial"))
    finals = list(tmp_path.glob("*.sql"))
    assert len(partials) == 0
    assert len(finals) == 0


# === Restore drill tests ===


class FakeDrillCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.results: list[Any] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        self.executed.append((sql, params))
        return 1

    def fetchone(self) -> Any:
        if self.results:
            r = self.results.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return None

    def fetchall(self) -> list[Any]:
        rows: list[Any] = []
        while self.results:
            r = self.results.pop(0)
            if isinstance(r, Exception):
                raise r
            rows.append(r)
        return rows

    def __enter__(self) -> FakeDrillCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeDrillConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeDrillCursor()
        self.closed = False

    def cursor(self) -> FakeDrillCursor:
        return self.cursor_instance

    def close(self) -> None:
        self.closed = True


def test_restore_rejects_non_drill_database() -> None:
    with pytest.raises(UnsafeRestoreTarget):
        validate_restore_database("qingpu_insight")


def test_restore_database_name_accepts_generated_backup_uuid() -> None:
    backup_id = str(uuid.uuid4())
    attempt_id = uuid.uuid4().hex
    name = build_restore_database(backup_id, attempt_id)
    validate_restore_database(name)
    assert "-" not in name.removeprefix("qingpu_restore_drill_")


def test_restore_drill_success(tmp_path: Path) -> None:
    backup_id = str(uuid.uuid4())
    content = b"mysql dump content\n"
    sha256 = hashlib.sha256(content).hexdigest()
    (tmp_path / f"{backup_id}.sql").write_bytes(content)

    repo = RecordingRepository()
    repo.create(BackupRecord(
        backup_id=backup_id,
        status="completed",
        path=f"{backup_id}.sql",
        sha256=sha256,
        size_bytes=len(content),
        created_at=NOW,
    ))

    runner = RecordingRunner()
    drill_conn = FakeDrillConnection()
    drill_conn.cursor_instance.results = [
        (5,), (10,), (3,), (20,),
        ("ds-001", "v2"),
    ]

    def connect(**kwargs: Any) -> FakeDrillConnection:
        return drill_conn

    service = BackupService(
        config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path,
        pymysql_connect=connect,
    )

    evidence = service.restore_drill(backup_id)

    assert evidence.database_name.startswith("qingpu_restore_drill_")
    assert evidence.table_names == [
        "job_runs", "dataset_versions", "published_datasets", "listing_current",
    ]
    assert evidence.row_counts == [5, 10, 3, 20]
    assert evidence.published_versions == {"ds-001": "v2"}

    assert len(runner.calls) == 3
    assert "CREATE DATABASE" in runner.calls[0][-1]
    assert runner.calls[1][0] == "mysql"
    assert "DROP DATABASE" in runner.calls[2][-1]

    assert len(repo.restore_calls) >= 1
    assert repo.restore_calls[-1][1] == "succeeded"


def test_restore_drill_checksum_mismatch(tmp_path: Path) -> None:
    backup_id = str(uuid.uuid4())
    content = b"mysql dump content\n"
    other_content = b"different content\n"
    (tmp_path / f"{backup_id}.sql").write_bytes(other_content)

    repo = RecordingRepository()
    repo.create(BackupRecord(
        backup_id=backup_id,
        status="completed",
        path=f"{backup_id}.sql",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        created_at=NOW,
    ))

    runner = RecordingRunner()
    service = BackupService(
        config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path,
    )

    with pytest.raises(ValueError, match="Checksum mismatch"):
        service.restore_drill(backup_id)

    assert len(repo.restore_calls) >= 1
    assert repo.restore_calls[-1][1] == "failed"


def test_restore_drill_import_failure(tmp_path: Path) -> None:
    backup_id = str(uuid.uuid4())
    content = b"mysql dump content\n"
    sha256 = hashlib.sha256(content).hexdigest()
    (tmp_path / f"{backup_id}.sql").write_bytes(content)

    repo = RecordingRepository()
    repo.create(BackupRecord(
        backup_id=backup_id,
        status="completed",
        path=f"{backup_id}.sql",
        sha256=sha256,
        size_bytes=len(content),
        created_at=NOW,
    ))

    runner = RecordingRunner(returncodes=[0, 1, 0])

    def connect(**kwargs: Any) -> FakeDrillConnection:
        return FakeDrillConnection()

    service = BackupService(
        config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path,
        pymysql_connect=connect,
    )

    with pytest.raises(RuntimeError, match="Import failed"):
        service.restore_drill(backup_id)

    assert len(runner.calls) == 3
    assert repo.restore_calls[-1][1] == "failed"


def test_restore_drill_missing_table(tmp_path: Path) -> None:
    backup_id = str(uuid.uuid4())
    content = b"mysql dump content\n"
    sha256 = hashlib.sha256(content).hexdigest()
    (tmp_path / f"{backup_id}.sql").write_bytes(content)

    repo = RecordingRepository()
    repo.create(BackupRecord(
        backup_id=backup_id,
        status="completed",
        path=f"{backup_id}.sql",
        sha256=sha256,
        size_bytes=len(content),
        created_at=NOW,
    ))

    runner = RecordingRunner()
    drill_conn = FakeDrillConnection()
    drill_conn.cursor_instance.results = [(5,), None]

    def connect(**kwargs: Any) -> FakeDrillConnection:
        return drill_conn

    service = BackupService(
        config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path,
        pymysql_connect=connect,
    )

    with pytest.raises(RuntimeError, match="not found or empty"):
        service.restore_drill(backup_id)

    assert repo.restore_calls[-1][1] == "failed"


def test_restore_drill_pointer_query_failure(tmp_path: Path) -> None:
    backup_id = str(uuid.uuid4())
    content = b"mysql dump content\n"
    sha256 = hashlib.sha256(content).hexdigest()
    (tmp_path / f"{backup_id}.sql").write_bytes(content)

    repo = RecordingRepository()
    repo.create(BackupRecord(
        backup_id=backup_id,
        status="completed",
        path=f"{backup_id}.sql",
        sha256=sha256,
        size_bytes=len(content),
        created_at=NOW,
    ))

    runner = RecordingRunner()
    drill_conn = FakeDrillConnection()
    drill_conn.cursor_instance.results = [
        (5,), (10,), (3,), (20,), RuntimeError("DB error"),
    ]

    def connect(**kwargs: Any) -> FakeDrillConnection:
        return drill_conn

    service = BackupService(
        config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path,
        pymysql_connect=connect,
    )

    with pytest.raises(RuntimeError):
        service.restore_drill(backup_id)

    assert repo.restore_calls[-1][1] == "failed"


def test_restore_drill_drop_failure(tmp_path: Path) -> None:
    backup_id = str(uuid.uuid4())
    content = b"mysql dump content\n"
    sha256 = hashlib.sha256(content).hexdigest()
    (tmp_path / f"{backup_id}.sql").write_bytes(content)

    repo = RecordingRepository()
    repo.create(BackupRecord(
        backup_id=backup_id,
        status="completed",
        path=f"{backup_id}.sql",
        sha256=sha256,
        size_bytes=len(content),
        created_at=NOW,
    ))

    runner = RecordingRunner(returncodes=[0, 0, 1])
    drill_conn = FakeDrillConnection()
    drill_conn.cursor_instance.results = [
        (5,), (10,), (3,), (20,), ("ds-001", "v2"),
    ]

    def connect(**kwargs: Any) -> FakeDrillConnection:
        return drill_conn

    service = BackupService(
        config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path,
        pymysql_connect=connect,
    )

    from qingpu_insight.backups import RestoreCleanupFailed

    with pytest.raises(RestoreCleanupFailed):
        service.restore_drill(backup_id)

    assert repo.restore_calls[-1][1] == "cleanup_failed"


def test_restore_rejects_metadata_path_outside_backup_dir() -> None:
    backup_dir = Path("/safe/backups").resolve()
    with pytest.raises(ValueError):
        resolve_backup_path(backup_dir, "../etc/passwd", str(uuid.uuid4()))


def test_restore_cleanup_failure_raises_and_records_cleanup_failed(
    tmp_path: Path,
) -> None:
    backup_id = str(uuid.uuid4())
    content = b"mysql dump content\n"
    sha256 = hashlib.sha256(content).hexdigest()
    (tmp_path / f"{backup_id}.sql").write_bytes(content)

    repo = RecordingRepository()
    repo.create(BackupRecord(
        backup_id=backup_id,
        status="completed",
        path=f"{backup_id}.sql",
        sha256=sha256,
        size_bytes=len(content),
        created_at=NOW,
    ))

    runner = RecordingRunner(returncodes=[0, 0, 1])
    drill_conn = FakeDrillConnection()
    drill_conn.cursor_instance.results = [
        (5,), (10,), (3,), (20,), ("ds-001", "v2"),
    ]

    def connect(**kwargs: Any) -> FakeDrillConnection:
        return drill_conn

    service = BackupService(
        config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path,
        pymysql_connect=connect,
    )

    from qingpu_insight.backups import RestoreCleanupFailed

    with pytest.raises(RestoreCleanupFailed):
        service.restore_drill(backup_id)

    assert repo.restore_calls[-1][1] == "cleanup_failed"


def test_restore_create_failure_does_not_drop_another_attempt_database(
    tmp_path: Path,
) -> None:
    backup_id = str(uuid.uuid4())
    content = b"mysql dump content\n"
    sha256 = hashlib.sha256(content).hexdigest()
    (tmp_path / f"{backup_id}.sql").write_bytes(content)

    repo = RecordingRepository()
    repo.create(BackupRecord(
        backup_id=backup_id,
        status="completed",
        path=f"{backup_id}.sql",
        sha256=sha256,
        size_bytes=len(content),
        created_at=NOW,
    ))

    runner = RecordingRunner(returncodes=[1])
    service = BackupService(
        config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path,
    )

    with pytest.raises(RuntimeError, match="Failed to create database"):
        service.restore_drill(backup_id)

    assert repo.restore_calls[-1][1] == "failed"
    # 1 CREATE attempt + 1 safety DROP in except handler = 2
    assert len(runner.calls) == 2


def test_hash_file_reads_in_bounded_chunks(tmp_path: Path) -> None:
    content = b"x" * (1024 * 1024 + 1)
    path = tmp_path / "large.sql"
    path.write_bytes(content)

    result = hash_file(path, chunk_size=1024)
    assert result == hashlib.sha256(content).hexdigest()


def test_restore_runner_receives_file_stream_not_dump_text(tmp_path: Path) -> None:
    backup_id = str(uuid.uuid4())
    content = b"mysql dump content\n"
    sha256 = hashlib.sha256(content).hexdigest()
    (tmp_path / f"{backup_id}.sql").write_bytes(content)

    repo = RecordingRepository()
    repo.create(BackupRecord(
        backup_id=backup_id,
        status="completed",
        path=f"{backup_id}.sql",
        sha256=sha256,
        size_bytes=len(content),
        created_at=NOW,
    ))

    runner = RecordingRunner(returncodes=[0, 0, 0])
    drill_conn = FakeDrillConnection()
    drill_conn.cursor_instance.results = [
        (5,), (10,), (3,), (20,), ("ds-001", "v2"),
    ]

    def connect(**kwargs: Any) -> FakeDrillConnection:
        return drill_conn

    service = BackupService(
        config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path,
        pymysql_connect=connect,
    )

    evidence = service.restore_drill(backup_id)
    assert evidence is not None

    import_args = runner.calls[1]
    assert import_args[0] == "mysql"

    stdin_path = runner.stdin_paths[1]
    assert stdin_path is not None
    assert stdin_path.read_bytes() == content