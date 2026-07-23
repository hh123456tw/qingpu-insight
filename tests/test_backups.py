from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from qingpu_insight.backups import BackupRecord, BackupService, RecordingRunner

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

    def create(self, record: BackupRecord) -> None:
        self.created.append(record)
        self.records.append(record)

    def latest(self) -> BackupRecord | None:
        return self.records[-1] if self.records else None

    def list_recent(self, limit: int) -> list[BackupRecord]:
        return self.records[-limit:]

    def mark_restore(self, backup_id: str, status: str, checked_at: datetime) -> None:
        pass


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