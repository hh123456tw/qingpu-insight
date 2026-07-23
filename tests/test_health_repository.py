from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from qingpu_insight.health import HealthItem, HealthSummary
from qingpu_insight.health_repository import MySQLHealthRepository

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


def test_repository_save_commits_and_closes() -> None:
    connections: list[FakeConnection] = []

    def factory() -> FakeConnection:
        conn = FakeConnection()
        connections.append(conn)
        return conn

    repo = MySQLHealthRepository(factory)
    connections.clear()

    summary = HealthSummary(status="healthy", checked_at=NOW, items=(
        HealthItem("mysql", "healthy", NOW, "ok", 1, "boolean"),
        HealthItem("disk_free", "healthy", NOW, "ok", 100 * 1024 ** 3, "bytes"),
    ))
    repo.save(summary)

    assert len(connections) == 1
    save_conn = connections[0]
    assert save_conn.commits >= 1
    assert save_conn.rollbacks == 0
    assert save_conn.closed is True


def test_repository_save_and_latest_roundtrip() -> None:
    call_count: list[int] = [0]
    save_conn: FakeConnection = FakeConnection()

    def factory() -> FakeConnection:
        idx = call_count[0]
        call_count[0] += 1
        if idx == 0:
            return save_conn
        # Simulate existing data in DB for latest() call
        latest_conn = FakeConnection()
        health_run_row: dict[str, Any] = {
            "run_id": "test-uuid",
            "status": "warning",
            "checked_at": NOW,
        }
        health_item_rows: list[dict[str, Any]] = [
            {"run_id": "test-uuid", "code": "mysql", "status": "healthy",
             "observed_at": NOW, "summary": "ok", "value": 1, "unit": "boolean"},
            {"run_id": "test-uuid", "code": "disk_free", "status": "warning",
             "observed_at": NOW, "summary": "low disk", "value": 5 * 1024 ** 3, "unit": "bytes"},
        ]
        latest_conn.cursor_instance.fetch_rows = [health_run_row] + health_item_rows
        return latest_conn

    repo = MySQLHealthRepository(factory)
    assert call_count[0] >= 1
    call_count[0] = 0

    summary = HealthSummary(status="warning", checked_at=NOW, items=(
        HealthItem("mysql", "healthy", NOW, "ok", 1, "boolean"),
        HealthItem("disk_free", "warning", NOW, "low disk", 5 * 1024 ** 3, "bytes"),
    ))
    repo.save(summary)
    assert call_count[0] == 1

    result = repo.latest()
    assert result is not None
    assert result.status == "warning"
    assert len(result.items) == 2


def test_repository_latest_returns_none_when_empty() -> None:
    def factory() -> FakeConnection:
        conn = FakeConnection()
        conn.cursor_instance.fetch_rows = [None]
        return conn

    repo = MySQLHealthRepository(factory)
    result = repo.latest()
    assert result is None


def test_repository_save_produces_insert_statements() -> None:
    connections: list[FakeConnection] = []

    def factory() -> FakeConnection:
        conn = FakeConnection()
        connections.append(conn)
        return conn

    repo = MySQLHealthRepository(factory)
    connections.clear()

    summary = HealthSummary(status="critical", checked_at=NOW, items=(
        HealthItem("mysql", "critical", NOW, "unreachable", 0, "boolean"),
    ))
    repo.save(summary)

    assert len(connections) == 1
    executed = connections[0].cursor_instance.executed
    assert len(executed) >= 1
    sql0 = executed[0][0]
    assert "INSERT INTO health_runs" in sql0 or "insert into health_runs" in sql0
