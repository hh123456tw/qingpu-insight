from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pymysql
import pymysql.err
import pytest

from qingpu_insight.backup_repository import MySQLBackupRepository
from qingpu_insight.backups import BackupRecord

NOW = datetime.now(UTC)


class FakeCursor:
    def __init__(self, dict_mode: bool = False) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_rows: list[dict[str, Any] | None] = []
        self.rowcount = 1
        self._dict_mode = dict_mode

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        self.executed.append((sql, params))
        return self.rowcount

    def fetchone(self) -> dict[str, Any] | None:
        return self.fetch_rows.pop(0) if self.fetch_rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        rows = [r for r in self.fetch_rows if r is not None]
        self.fetch_rows.clear()
        return rows

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()
        self.cursor_classes: list[object] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, cursor_class: object = None) -> FakeCursor:
        self.cursor_classes.append(cursor_class)
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


RECORD = BackupRecord(
    backup_id="test-001",
    status="completed",
    path="test-001.sql",
    sha256="abc123",
    size_bytes=1024,
    created_at=NOW,
)


def test_repository_save_roundtrip() -> None:
    conn = FakeConnection()
    latest_conn = FakeConnection()
    latest_conn.cursor_instance.fetch_rows = [
        {
            "backup_id": "test-001",
            "status": "completed",
            "path": "test-001.sql",
            "sha256": "abc123",
            "size_bytes": 1024,
            "created_at": NOW,
            "restore_status": None,
            "restore_checked_at": None,
        },
    ]
    call_count: list[int] = [0]

    def factory() -> FakeConnection:
        idx = call_count[0]
        call_count[0] += 1
        if idx == 0:
            return conn
        return latest_conn

    repo = MySQLBackupRepository(factory)
    call_count[0] = 0

    repo.create(RECORD)
    assert conn.commits >= 1
    assert conn.rollbacks == 0
    assert conn.closed is True

    result = repo.latest()
    assert result is not None
    assert result.backup_id == "test-001"
    assert result.status == "completed"
    assert result.sha256 == "abc123"
    assert result.size_bytes == 1024


def test_duplicate_backup_id_rejected() -> None:
    inserted: set[str] = set()

    class DupeAwareCursor(FakeCursor):
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
            self.executed.append((sql, params))
            if sql.strip().upper().startswith("INSERT"):
                bid = params[0] if params else ""
                if bid in inserted:
                    raise pymysql.err.IntegrityError(
                        f"Duplicate entry '{bid}' for key 'PRIMARY'",
                    )
                inserted.add(bid)
            return 1

    conn = FakeConnection()
    conn.cursor_instance = DupeAwareCursor()

    def factory() -> FakeConnection:
        return conn

    repo = MySQLBackupRepository(factory)
    repo.create(RECORD)
    with pytest.raises(pymysql.err.IntegrityError):
        repo.create(RECORD)


def test_mark_restore_updates_record() -> None:
    conn = FakeConnection()

    def factory() -> FakeConnection:
        return conn

    repo = MySQLBackupRepository(factory)
    checked_at = datetime.now(UTC)
    repo.mark_restore("test-001", "verified", checked_at)

    assert conn.commits >= 1
    executed = conn.cursor_instance.executed
    update_sql = executed[-1][0] if executed else ""
    assert "UPDATE" in update_sql.upper() or "update" in update_sql
    assert "restore_status" in update_sql


def test_list_recent_returns_ordered_records() -> None:
    conn = FakeConnection()
    conn.cursor_instance.fetch_rows = [
        {
            "backup_id": "test-003",
            "status": "completed",
            "path": "test-003.sql",
            "sha256": "def456",
            "size_bytes": 2048,
            "created_at": NOW,
            "restore_status": None,
            "restore_checked_at": None,
        },
    ]

    def factory() -> FakeConnection:
        return conn

    repo = MySQLBackupRepository(factory)
    results = repo.list_recent(5)
    assert len(results) == 1
    assert results[0].backup_id == "test-003"


def test_get_returns_record() -> None:
    conn = FakeConnection()
    conn.cursor_instance.fetch_rows = [
        {
            "backup_id": "test-001",
            "status": "completed",
            "path": "test-001.sql",
            "sha256": "abc123",
            "size_bytes": 1024,
            "created_at": NOW,
            "restore_status": None,
            "restore_checked_at": None,
        },
    ]

    def factory() -> FakeConnection:
        return conn

    repo = MySQLBackupRepository(factory)
    result = repo.get("test-001")
    assert result is not None
    assert result.backup_id == "test-001"
    assert result.sha256 == "abc123"


def test_get_returns_none_when_missing() -> None:
    conn = FakeConnection()

    def factory() -> FakeConnection:
        return conn

    repo = MySQLBackupRepository(factory)
    result = repo.get("nonexistent")
    assert result is None


def test_get_rolls_back_when_select_fails() -> None:
    class FailingCursor(FakeCursor):
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
            self.executed.append((sql, params))
            if sql.strip().upper().startswith("SELECT"):
                raise Exception("DB error")
            return self.rowcount

    conn = FakeConnection()
    conn.cursor_instance = FailingCursor()

    def factory() -> FakeConnection:
        return conn

    repo = MySQLBackupRepository(factory)
    with pytest.raises(Exception, match="DB error"):
        repo.get("fail-001")
    assert conn.rollbacks > 0