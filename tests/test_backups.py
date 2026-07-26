from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from qingpu_insight.backups import (
    BackupJobService,
    BackupRecord,
    BackupService,
    RecordingRunner,
    RepositoryError,
    UnsafeRestoreTarget,
    build_restore_database,
    hash_file,
    resolve_backup_path,
    validate_restore_database,
)
from qingpu_insight.jobs import JobSubmission

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
        self.restore_status: str | None = None
        self.fail_mark_restore = False

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
        self.restore_status = status
        if self.fail_mark_restore:
            raise RepositoryError("Simulated mark_restore failure")


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
    # 1 CREATE attempt only — no DROP since database was never created
    assert len(runner.calls) == 1


def test_create_failure_never_drops_database(tmp_path: Path) -> None:
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

    assert len(runner.calls) == 1


def test_import_and_drop_failure_records_cleanup_failed(tmp_path: Path) -> None:
    from qingpu_insight.backups import RestoreCleanupFailed

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

    runner = RecordingRunner(returncodes=[0, 1, 1])
    service = BackupService(
        config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path,
    )

    with pytest.raises(RestoreCleanupFailed):
        service.restore_drill(backup_id)

    assert repo.restore_status == "cleanup_failed"


def test_metadata_failure_does_not_skip_drop(tmp_path: Path) -> None:
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
    repo.fail_mark_restore = True

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

    with pytest.raises(RepositoryError):
        service.restore_drill(backup_id)

    assert runner.calls[-1][-1].startswith("DROP DATABASE")


# === BackupJobService tests ===


class RecordingJobRepository:
    def __init__(self) -> None:
        self._runs: dict[str, Any] = {}

    def create_or_get(self, run) -> tuple[Any, bool]:
        active = self.find_active_by_key(run.idempotency_key)
        if active is not None:
            return active, False
        self._runs[run.run_id] = run
        return run, True

    def get(self, run_id: str) -> Any | None:
        return self._runs.get(run_id)

    def find_active_by_key(self, idempotency_key: str) -> Any | None:
        for run in self._runs.values():
            if run.idempotency_key == idempotency_key and run.status in (
                "pending", "running", "retry_wait",
            ):
                return run
        return None

    def list_recent(self, limit: int = 20, job_type: str | None = None) -> list:
        all_runs = reversed(list(self._runs.values()))
        if job_type is not None:
            all_runs = (r for r in all_runs if r.job_type == job_type)
        return list(all_runs)[:limit]

    def list_active(self, job_type: str) -> list:
        return [
            r for r in self._runs.values()
            if r.job_type == job_type and r.status in ("pending", "running", "retry_wait")
        ]

    def transition(
        self, run_id, current_status, target_status, *,
        output_version=None, summary=None, error_code=None, error_message=None,
    ) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.status != current_status:
            return False
        from dataclasses import replace
        started_at = run.started_at
        if target_status == "running" and started_at is None:
            started_at = datetime.now(UTC)
        finished_at = run.finished_at
        if target_status in {"succeeded", "failed", "skipped"}:
            finished_at = datetime.now(UTC)
        self._runs[run_id] = replace(
            run,
            status=target_status,
            started_at=started_at,
            finished_at=finished_at,
            output_version=output_version or run.output_version,
            summary=summary if summary is not None else run.summary,
            error_code=error_code or run.error_code,
            error_message=error_message or run.error_message,
        )
        return True

    def update_summary(self, run_id, expected_status, summary) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.status != expected_status:
            return False
        from dataclasses import replace
        self._runs[run_id] = replace(run, summary=summary)
        return True


class TestBackupJobService:
    def test_submit_create_creates_backup_create_job(self) -> None:
        from qingpu_insight.jobs import JobService

        job_repo = RecordingJobRepository()
        job_service = JobService(job_repo)
        runner = RecordingRunner()
        repo = RecordingRepository()
        svc = BackupService(config=CONFIG, runner=runner, repository=repo, backup_dir=Path("/tmp"))
        bjs = BackupJobService(job_service=job_service, backup_service=svc)

        submission = bjs.submit_create()

        assert submission.created is True
        assert submission.run.job_type == "backup_create"
        assert submission.run.idempotency_key == "backup_create:active"
        assert submission.run.status == "pending"

    def test_submit_create_returns_existing_when_active(self) -> None:
        from qingpu_insight.jobs import JobService

        job_repo = RecordingJobRepository()
        job_service = JobService(job_repo)
        runner = RecordingRunner()
        repo = RecordingRepository()
        svc = BackupService(config=CONFIG, runner=runner, repository=repo, backup_dir=Path("/tmp"))
        bjs = BackupJobService(job_service=job_service, backup_service=svc)

        first = bjs.submit_create()
        second = bjs.submit_create()

        assert second.created is False
        assert second.run.run_id == first.run.run_id

    def test_submit_restore_drill_creates_restore_drill_job(self) -> None:
        from qingpu_insight.jobs import JobService

        job_repo = RecordingJobRepository()
        job_service = JobService(job_repo)
        runner = RecordingRunner()
        repo = RecordingRepository()
        svc = BackupService(config=CONFIG, runner=runner, repository=repo, backup_dir=Path("/tmp"))
        bjs = BackupJobService(job_service=job_service, backup_service=svc)

        backup_id = str(uuid.uuid4())
        submission = bjs.submit_restore_drill(backup_id)

        assert submission.created is True
        assert submission.run.job_type == "restore_drill"
        assert submission.run.idempotency_key == f"restore_drill:{backup_id}"
        assert submission.run.status == "pending"

    def test_submit_restore_drill_returns_existing_when_active(self) -> None:
        from qingpu_insight.jobs import JobService

        job_repo = RecordingJobRepository()
        job_service = JobService(job_repo)
        runner = RecordingRunner()
        repo = RecordingRepository()
        svc = BackupService(config=CONFIG, runner=runner, repository=repo, backup_dir=Path("/tmp"))
        bjs = BackupJobService(job_service=job_service, backup_service=svc)

        backup_id = str(uuid.uuid4())
        first = bjs.submit_restore_drill(backup_id)
        second = bjs.submit_restore_drill(backup_id)

        assert second.created is False
        assert second.run.run_id == first.run.run_id

    def test_execute_create_success(self, tmp_path: Path) -> None:
        from qingpu_insight.jobs import JobService

        content = b"known dump content\n"
        runner = RecordingRunner(dump_content=content)
        repo = RecordingRepository()
        svc = BackupService(config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path)
        job_repo = RecordingJobRepository()
        job_service = JobService(job_repo)
        bjs = BackupJobService(job_service=job_service, backup_service=svc)

        submission = bjs.submit_create()
        record = bjs.execute_create(submission.run.run_id)

        assert record.status == "completed"
        assert record.sha256 == hashlib.sha256(content).hexdigest()
        final_run = job_service.get(submission.run.run_id)
        assert final_run is not None
        assert final_run.status == "succeeded"
        assert final_run.output_version == record.sha256
        assert final_run.summary == {
            "backup_id": record.backup_id,
            "size_bytes": record.size_bytes,
            "sha256": record.sha256,
        }

    def test_execute_create_dump_failed(self, tmp_path: Path) -> None:
        from qingpu_insight.jobs import JobService

        runner = RecordingRunner(returncode=1)
        repo = RecordingRepository()
        svc = BackupService(config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path)
        job_repo = RecordingJobRepository()
        job_service = JobService(job_repo)
        bjs = BackupJobService(job_service=job_service, backup_service=svc)

        submission = bjs.submit_create()
        record = bjs.execute_create(submission.run.run_id)

        assert record.status == "dump_failed"
        final_run = job_service.get(submission.run.run_id)
        assert final_run is not None
        assert final_run.status == "failed"

    def test_execute_restore_drill_success(self, tmp_path: Path) -> None:
        from qingpu_insight.jobs import JobService

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
            (5,), (10,), (3,), (20,), ("ds-001", "v2"),
        ]

        def connect(**kwargs: Any) -> FakeDrillConnection:
            return drill_conn

        svc = BackupService(
            config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path,
            pymysql_connect=connect,
        )
        job_repo = RecordingJobRepository()
        job_service = JobService(job_repo)
        bjs = BackupJobService(job_service=job_service, backup_service=svc)

        submission = bjs.submit_restore_drill(backup_id)
        evidence = bjs.execute_restore_drill(submission.run.run_id, backup_id)

        assert evidence.database_name.startswith("qingpu_restore_drill_")
        assert evidence.row_counts == [5, 10, 3, 20]
        final_run = job_service.get(submission.run.run_id)
        assert final_run is not None
        assert final_run.status == "succeeded"
        assert final_run.summary["database_name"] == evidence.database_name
        assert final_run.summary["row_counts"] == [5, 10, 3, 20]

    def test_execute_restore_drill_checksum_mismatch(self, tmp_path: Path) -> None:
        from qingpu_insight.jobs import JobService

        backup_id = str(uuid.uuid4())
        content = b"mysql dump content\n"
        (tmp_path / f"{backup_id}.sql").write_bytes(b"different content\n")

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
        svc = BackupService(
            config=CONFIG, runner=runner, repository=repo, backup_dir=tmp_path,
        )
        job_repo = RecordingJobRepository()
        job_service = JobService(job_repo)
        bjs = BackupJobService(job_service=job_service, backup_service=svc)

        submission = bjs.submit_restore_drill(backup_id)
        with pytest.raises(ValueError, match="Checksum mismatch"):
            bjs.execute_restore_drill(submission.run.run_id, backup_id)

        final_run = job_service.get(submission.run.run_id)
        assert final_run is not None
        assert final_run.status == "failed"


def test_hash_file_reads_in_bounded_chunks(tmp_path: Path) -> None:
    content = b"x" * (1024 * 1024 + 1)
    path = tmp_path / "large.sql"
    path.write_bytes(content)

    result = hash_file(path, chunk_size=1024)
    assert result == hashlib.sha256(content).hexdigest()


# === Production Restore Service tests ===


RESTORABLE_TABLES: tuple[str, ...] = (
    "data_refreshes", "market_transactions", "listing_batches",
    "listing_snapshots", "listing_current", "listing_events",
    "listing_valuations", "dataset_versions", "dataset_version_batches",
    "dataset_version_rows", "dataset_version_events", "published_datasets",
    "buyer_reports",
)


def test_restorable_tables_whitelist() -> None:
    from qingpu_insight.backups import RESTORABLE_TABLES as RT
    assert RT == RESTORABLE_TABLES


class FakeProductionRestoreHealthService:
    def __init__(self, status: str = "healthy") -> None:
        self._status = status
        self.call_count = 0

    def run(self):
        from qingpu_insight.health import HealthSummary
        self.call_count += 1
        return HealthSummary(status=self._status, checked_at=NOW)


class FakeProductionRestoreBackupService:
    def __init__(self, fail: bool = False) -> None:
        self._fail = fail
        self.calls: list[BackupRecord] = []

    def create(self) -> BackupRecord:
        if self._fail:
            record = BackupRecord(
                backup_id=str(uuid.uuid4()),
                status="completed",
                path="",
                sha256="",
                size_bytes=0,
                created_at=NOW,
            )
        else:
            record = BackupRecord(
                backup_id=str(uuid.uuid4()),
                status="completed",
                path="",
                sha256="abc" * 10,
                size_bytes=100,
                created_at=NOW,
            )
        self.calls.append(record)
        return record


class FakeStageConnection:
    def __init__(self, row_values: list[int] | None = None) -> None:
        self._row_values = row_values or [5] * len(RESTORABLE_TABLES)
        self.closed = False

    def cursor(self):
        return FakeStageCursor(self._row_values)

    def close(self) -> None:
        self.closed = True


class FakeStageCursor:
    def __init__(self, row_values: list[int]) -> None:
        self._row_values = row_values
        self._index = 0
        self.executed: list[str] = []

    def execute(self, sql: str, params: tuple = ()) -> int:
        self.executed.append(sql)
        return 1

    def fetchone(self):
        if self._index < len(self._row_values):
            val = self._row_values[self._index]
            self._index += 1
            return (val,)
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeProductionRestoreJobService:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.succeeded: list[tuple[str, str, dict]] = []
        self.failed: list[tuple[str, str, str]] = []
        self._active: dict[str, list] = {}

    def start(self, run_id: str) -> None:
        self.started.append(run_id)

    def succeed(self, run_id: str, output: str, summary: dict) -> None:
        self.succeeded.append((run_id, output, summary))

    def fail(self, run_id: str, code: str, msg: str) -> None:
        self.failed.append((run_id, code, msg))

    def create(self, job_type, idempotency_key, trigger, input_version=None):
        from qingpu_insight.jobs import JobRun
        run = JobRun(
            run_id=str(uuid.uuid4()),
            job_type=job_type,
            trigger=trigger,
            idempotency_key=idempotency_key,
            status="pending",
            started_at=None,
            finished_at=None,
            attempt=1,
            input_version=input_version,
            output_version=None,
            summary={},
            error_code=None,
            error_message=None,
        )
        return JobSubmission(run=run, created=True)

    def list_active(self, job_type: str) -> list:
        return self._active.get(job_type, [])


def test_production_restore_safety_backup_fails(tmp_path: Path) -> None:
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

    runner = RecordingRunner(returncodes=[0])
    fake_backup = FakeProductionRestoreBackupService(fail=True)
    fake_jobs = FakeProductionRestoreJobService()

    from qingpu_insight.backups import ProductionRestoreService

    service = ProductionRestoreService(
        config=CONFIG,
        runner=runner,
        repository=repo,
        backup_dir=tmp_path,
        backup_service=fake_backup,
        job_service=fake_jobs,
        preview_service=object(),
    )

    with pytest.raises(RuntimeError, match="Safety backup not completed"):
        service._execute_inner("run-1", backup_id)

    assert fake_backup.calls
    assert not fake_jobs.succeeded


def test_production_restore_health_fails(tmp_path: Path) -> None:
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

    table_count = len(RESTORABLE_TABLES)
    # Total calls: create_stage(1) + import_stage(1) + create_rollback(1)
    # + mysqldump(table_count) + mysql_import_rollback(table_count)
    # + rename(1) + drop_stage(1) + drop_rollback(1)
    total_calls = 1 + 1 + 1 + table_count + table_count + 1 + 1 + 1
    runner = RecordingRunner(returncodes=[0] * total_calls)
    runner._stdout = "-- Dump content\n"

    fake_backup = FakeProductionRestoreBackupService(fail=False)
    fake_jobs = FakeProductionRestoreJobService()
    fake_health = FakeProductionRestoreHealthService(status="critical")
    stage_conn = FakeStageConnection()

    def connect(**kwargs):
        return stage_conn

    from qingpu_insight.backups import ProductionRestoreService

    service = ProductionRestoreService(
        config=CONFIG,
        runner=runner,
        repository=repo,
        backup_dir=tmp_path,
        backup_service=fake_backup,
        job_service=fake_jobs,
        preview_service=object(),
        pymysql_connect=connect,
        health_service=fake_health,
    )

    result = service._execute_inner("run-1", backup_id)

    assert result.backup_id == backup_id
    assert result.safety_backup_id != ""
    assert result.rollback_database != ""
    assert result.rollback_database.startswith("qingpu_restore_rollback_")
    assert len(result.table_names) == table_count
    assert len(result.row_counts) == table_count
    assert all(c == 5 for c in result.row_counts)

    assert len(fake_jobs.failed) == 1
    assert fake_jobs.failed[0][1] == "health_check_failed"
    assert not fake_jobs.succeeded

    assert fake_health.call_count > 0


def test_production_restore_happy_path(tmp_path: Path) -> None:
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

    table_count = len(RESTORABLE_TABLES)
    total_calls = 1 + 1 + 1 + table_count + table_count + 1 + 1 + 1
    runner = RecordingRunner(returncodes=[0] * total_calls)
    runner._stdout = "-- Dump content\n"

    fake_backup = FakeProductionRestoreBackupService(fail=False)
    fake_jobs = FakeProductionRestoreJobService()
    fake_health = FakeProductionRestoreHealthService(status="healthy")
    stage_conn = FakeStageConnection()

    def connect(**kwargs):
        return stage_conn

    from qingpu_insight.backups import ProductionRestoreService

    service = ProductionRestoreService(
        config=CONFIG,
        runner=runner,
        repository=repo,
        backup_dir=tmp_path,
        backup_service=fake_backup,
        job_service=fake_jobs,
        preview_service=object(),
        pymysql_connect=connect,
        health_service=fake_health,
    )

    result = service._execute_inner("run-1", backup_id)

    assert result.backup_id == backup_id
    assert result.safety_backup_id != ""
    assert result.rollback_database.startswith("qingpu_restore_rollback_")
    assert len(result.table_names) == table_count
    assert result.table_names == list(RESTORABLE_TABLES)
    assert len(result.row_counts) == table_count
    assert all(c == 5 for c in result.row_counts)

    assert len(fake_jobs.succeeded) == 1
    assert fake_jobs.succeeded[0][0] == "run-1"
    assert not fake_jobs.failed

    create_stage_calls = [c for c in runner.calls if "CREATE DATABASE" in c[-1]]
    drop_stage_calls = [c for c in runner.calls if "DROP DATABASE" in c[-1] and "stage" in c[-1]]
    drop_rollback_calls = [
        c for c in runner.calls
        if "DROP DATABASE" in c[-1] and "rollback" in c[-1]
    ]

    assert len(create_stage_calls) >= 1
    assert len(drop_stage_calls) == 1
    assert len(drop_rollback_calls) == 1
    assert fake_health.call_count > 0


def test_production_restore_runner_receives_file_stream(tmp_path: Path) -> None:
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

    table_count = len(RESTORABLE_TABLES)
    total_calls = 1 + 1 + 1 + table_count + table_count + 1 + 1 + 1
    runner = RecordingRunner(returncodes=[0] * total_calls)
    runner._stdout = "-- Dump content\n"

    fake_backup = FakeProductionRestoreBackupService(fail=False)
    fake_jobs = FakeProductionRestoreJobService()
    fake_health = FakeProductionRestoreHealthService(status="healthy")
    stage_conn = FakeStageConnection()

    def connect(**kwargs):
        return stage_conn

    from qingpu_insight.backups import ProductionRestoreService

    service = ProductionRestoreService(
        config=CONFIG,
        runner=runner,
        repository=repo,
        backup_dir=tmp_path,
        backup_service=fake_backup,
        job_service=fake_jobs,
        preview_service=object(),
        pymysql_connect=connect,
        health_service=fake_health,
    )

    service._execute_inner("run-1", backup_id)

    import_call = runner.calls[1]
    assert import_call[0] == "mysql"
    stdin_path = runner.stdin_paths[1]
    assert stdin_path is not None
    assert stdin_path.read_bytes() == content


def test_production_restore_preview_and_submit_roundtrip() -> None:
    from qingpu_insight.operation_previews import (
        InMemoryOperationPreviewRepository,
        OperationPreviewService,
    )

    preview_repo = InMemoryOperationPreviewRepository()
    preview_service = OperationPreviewService(
        repository=preview_repo,
        clock=lambda: NOW,
        make_uuid=lambda: "test-preview-uuid",
    )

    from qingpu_insight.backups import ProductionRestoreService
    from qingpu_insight.jobs import JobService

    job_repo = RecordingJobRepository()
    job_service = JobService(job_repo)

    repo = RecordingRepository()
    backup_id = str(uuid.uuid4())
    repo.create(BackupRecord(
        backup_id=backup_id,
        status="completed",
        path=f"{backup_id}.sql",
        sha256="abc",
        size_bytes=100,
        created_at=NOW,
    ))

    service = ProductionRestoreService(
        config=CONFIG,
        runner=RecordingRunner(),
        repository=repo,
        backup_dir=Path("/tmp"),
        backup_service=FakeProductionRestoreBackupService(),
        job_service=job_service,
        preview_service=preview_service,
    )

    preview = service.preview(backup_id)
    assert preview.operation == "database_restore"
    assert preview.payload == {"backup_id": backup_id}
    assert preview.confirmation_text == f"還原資料庫 {backup_id[:8]}"

    submission = service.submit(preview.preview_id, preview.confirmation_text)
    assert submission.created is True
    assert submission.run.job_type == "database_restore"
    assert submission.run.input_version == backup_id


def test_production_restore_preview_raises_for_missing_backup() -> None:
    from qingpu_insight.operation_previews import (
        InMemoryOperationPreviewRepository,
        OperationPreviewService,
    )

    preview_repo = InMemoryOperationPreviewRepository()
    preview_service = OperationPreviewService(
        repository=preview_repo,
        clock=lambda: NOW,
    )

    from qingpu_insight.backups import ProductionRestoreService

    repo = RecordingRepository()

    service = ProductionRestoreService(
        config=CONFIG,
        runner=RecordingRunner(),
        repository=repo,
        backup_dir=Path("/tmp"),
        backup_service=FakeProductionRestoreBackupService(),
        job_service=object(),
        preview_service=preview_service,
    )

    with pytest.raises(ValueError, match="not found"):
        service.preview("nonexistent-backup")


def test_restorable_tables_match_existing_literals() -> None:
    from qingpu_insight.backups import RESTORABLE_TABLES as RT
    assert "data_refreshes" in RT
    assert "market_transactions" in RT
    assert "listing_current" in RT
    assert "published_datasets" in RT
    assert "buyer_reports" in RT
    assert len(RT) == 13


def test_production_restore_result_dataclass() -> None:
    from qingpu_insight.backups import ProductionRestoreResult
    result = ProductionRestoreResult(
        backup_id="b1",
        safety_backup_id="s1",
        rollback_database="qingpu_restore_rollback_abc123",
        table_names=["t1", "t2"],
        row_counts=[10, 20],
    )
    assert result.backup_id == "b1"
    assert result.safety_backup_id == "s1"
    assert result.rollback_database == "qingpu_restore_rollback_abc123"
    assert result.table_names == ["t1", "t2"]
    assert result.row_counts == [10, 20]