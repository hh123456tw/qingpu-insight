from __future__ import annotations

from typing import Any

import pymysql
import pytest

from qingpu_insight.job_repository import MySQLJobRepository
from qingpu_insight.jobs import JobRun


def pending_run() -> JobRun:
    return JobRun(
        run_id="test-uuid", job_type="listing_update", trigger="manual",
        idempotency_key="ik-1", status="pending", started_at=None, finished_at=None,
        attempt=1, input_version=None, output_version=None, summary={}, error_code=None,
        error_message=None,
    )


def row_for(run: JobRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id, "job_type": run.job_type, "trigger": run.trigger,
        "idempotency_key": run.idempotency_key, "status": run.status,
        "started_at": run.started_at, "finished_at": run.finished_at, "attempt": run.attempt,
        "input_version": run.input_version, "output_version": run.output_version,
        "summary": "{}", "error_code": run.error_code, "error_message": run.error_message,
    }


class FakeCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_rows: list[dict[str, Any] | None] = []
        self.rowcount = 1
        self.insert_error: Exception | None = None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        self.executed.append((sql, params))
        if sql.lstrip().upper().startswith("INSERT") and self.insert_error:
            raise self.insert_error
        return self.rowcount

    def fetchone(self) -> dict[str, Any] | None:
        return self.fetch_rows.pop(0) if self.fetch_rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        rows = [row for row in self.fetch_rows if row is not None]
        self.fetch_rows.clear()
        return rows

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, cursor_class: object = None) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture
def fake_conn() -> FakeConnection:
    return FakeConnection()


def test_schema_enforces_one_active_idempotency_key(fake_conn: FakeConnection) -> None:
    MySQLJobRepository(fake_conn)
    ddl = fake_conn.cursor_instance.executed[0][0]
    assert "active_idempotency_key" in ddl
    assert "GENERATED ALWAYS AS" in ddl
    assert "UNIQUE INDEX uq_job_runs_active_key" in ddl


def test_create_or_get_returns_created_run(fake_conn: FakeConnection) -> None:
    repo = MySQLJobRepository(fake_conn)
    run, created = repo.create_or_get(pending_run())
    assert (run, created) == (pending_run(), True)
    assert fake_conn.commits == 2


def test_create_or_get_rolls_back_duplicate_then_reloads_active_run(
    fake_conn: FakeConnection,
) -> None:
    repo = MySQLJobRepository(fake_conn)
    existing = pending_run()
    fake_conn.cursor_instance.insert_error = pymysql.err.IntegrityError(1062, "duplicate")
    fake_conn.cursor_instance.fetch_rows = [row_for(existing)]
    run, created = repo.create_or_get(existing)
    assert (run, created) == (existing, False)
    assert fake_conn.rollbacks == 1
    assert fake_conn.commits == 2


@pytest.mark.parametrize("row", [row_for(pending_run()), None])
def test_find_active_by_key_closes_lock_transaction_on_every_branch(
    fake_conn: FakeConnection, row: dict[str, Any] | None,
) -> None:
    repo = MySQLJobRepository(fake_conn)
    fake_conn.cursor_instance.fetch_rows = [row]
    result = repo.find_active_by_key("ik-1")
    assert (result is None) is (row is None)
    assert fake_conn.commits == 2


def test_transition_persists_terminal_metadata_atomically(fake_conn: FakeConnection) -> None:
    repo = MySQLJobRepository(fake_conn)
    assert repo.transition(
        "test-uuid", "running", "succeeded", output_version="v1", summary={"rows": 2},
    )
    sql, params = fake_conn.cursor_instance.executed[-1]
    assert "output_version" in sql and "summary" in sql
    assert "v1" in params and '{"rows": 2}' in params


def test_repository_uses_connection_factory_for_each_operation() -> None:
    connections: list[FakeConnection] = []

    def factory() -> FakeConnection:
        connection = FakeConnection()
        connections.append(connection)
        return connection

    repo = MySQLJobRepository(factory)
    repo.get("missing")
    repo.list_recent()
    assert len(connections) == 3
