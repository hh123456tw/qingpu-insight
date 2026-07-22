from __future__ import annotations

from pathlib import Path
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
        self.insert_errors: list[Exception | None] = []
        self.schema_column_exists = True
        self.schema_index_exists = True
        self.duplicate_active_row: dict[str, Any] | None = None
        self._schema_fetch_pending = False
        self._schema_fetch_row: dict[str, Any] | None = None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        self.executed.append((sql, params))
        normalized = " ".join(sql.upper().split())
        if "INFORMATION_SCHEMA.COLUMNS" in normalized:
            self._set_schema_fetch({"present": 1} if self.schema_column_exists else None)
        elif "INFORMATION_SCHEMA.STATISTICS" in normalized:
            self._set_schema_fetch({"present": 1} if self.schema_index_exists else None)
        elif "HAVING COUNT(*) > 1" in normalized:
            self._set_schema_fetch(self.duplicate_active_row)
        elif "ADD COLUMN ACTIVE_IDEMPOTENCY_KEY" in normalized:
            self.schema_column_exists = True
        elif "ADD UNIQUE INDEX UQ_JOB_RUNS_ACTIVE_KEY" in normalized:
            self.schema_index_exists = True
        if normalized.startswith("INSERT") and self.insert_errors:
            error = self.insert_errors.pop(0)
            if error is not None:
                raise error
        return self.rowcount

    def _set_schema_fetch(self, row: dict[str, Any] | None) -> None:
        self._schema_fetch_pending = True
        self._schema_fetch_row = row

    def fetchone(self) -> dict[str, Any] | None:
        if self._schema_fetch_pending:
            self._schema_fetch_pending = False
            return self._schema_fetch_row
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
        self.cursor_classes: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, cursor_class: object = None) -> FakeCursor:
        self.cursor_classes.append(cursor_class)
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


def test_schema_upgrades_existing_table_missing_active_column_and_index() -> None:
    connection = FakeConnection()
    connection.cursor_instance.schema_column_exists = False
    connection.cursor_instance.schema_index_exists = False
    MySQLJobRepository(connection)
    sql = "\n".join(statement for statement, _ in connection.cursor_instance.executed)
    assert "ADD COLUMN active_idempotency_key" in sql
    assert "ADD UNIQUE INDEX uq_job_runs_active_key" in sql


def test_schema_refuses_duplicate_active_rows_without_mutating_history() -> None:
    connection = FakeConnection()
    connection.cursor_instance.schema_index_exists = False
    connection.cursor_instance.duplicate_active_row = {
        "idempotency_key": "duplicate-key",
        "active_count": 2,
    }
    with pytest.raises(RuntimeError, match="duplicate-key"):
        MySQLJobRepository(connection)
    assert connection.cursor_classes[0] is pymysql.cursors.DictCursor
    statements = [sql.strip().upper() for sql, _ in connection.cursor_instance.executed[1:]]
    assert not any(sql.startswith(("DELETE", "UPDATE")) for sql in statements)
    assert not any("ADD UNIQUE INDEX" in sql for sql in statements)


def test_schema_migration_is_idempotent_and_signals_active_duplicates() -> None:
    migration = (
        Path(__file__).parents[1] / "database" / "004_m4_jobs_publishing_schema.sql"
    ).read_text(encoding="utf-8")
    assert "information_schema.COLUMNS" in migration
    assert "information_schema.STATISTICS" in migration
    assert "SIGNAL SQLSTATE '45000'" in migration
    assert "ADD UNIQUE INDEX uq_job_runs_active_key" in migration


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
    fake_conn.cursor_instance.insert_errors = [
        pymysql.err.IntegrityError(
            1062,
            "Duplicate entry 'ik-1' for key 'job_runs.uq_job_runs_active_key'",
        ),
    ]
    fake_conn.cursor_instance.fetch_rows = [row_for(existing)]
    run, created = repo.create_or_get(existing)
    assert (run, created) == (existing, False)
    assert fake_conn.rollbacks == 1
    assert fake_conn.commits == 2


def test_create_or_get_reinserts_when_conflicting_run_becomes_terminal(
    fake_conn: FakeConnection,
) -> None:
    repo = MySQLJobRepository(fake_conn)
    fake_conn.cursor_instance.insert_errors = [
        pymysql.err.IntegrityError(
            1062,
            "Duplicate entry 'ik-1' for key 'job_runs.uq_job_runs_active_key'",
        ),
        None,
    ]
    fake_conn.cursor_instance.fetch_rows = [None]
    run, created = repo.create_or_get(pending_run())
    assert (run, created) == (pending_run(), True)
    inserts = [
        sql for sql, _ in fake_conn.cursor_instance.executed
        if sql.lstrip().upper().startswith("INSERT")
    ]
    assert len(inserts) == 2


def test_create_or_get_does_not_recover_unrelated_duplicate_key(
    fake_conn: FakeConnection,
) -> None:
    repo = MySQLJobRepository(fake_conn)
    unrelated = pymysql.err.IntegrityError(1062, "Duplicate entry 'test-uuid' for key 'PRIMARY'")
    fake_conn.cursor_instance.insert_errors = [unrelated]
    with pytest.raises(pymysql.err.IntegrityError) as raised:
        repo.create_or_get(pending_run())
    assert raised.value is unrelated
    assert not any("FOR UPDATE" in sql for sql, _ in fake_conn.cursor_instance.executed)


def test_create_or_get_bounds_stale_conflict_retries(fake_conn: FakeConnection) -> None:
    repo = MySQLJobRepository(fake_conn)
    conflicts = [
        pymysql.err.IntegrityError(
            1062,
            "Duplicate entry 'ik-1' for key 'job_runs.uq_job_runs_active_key'",
        )
        for _ in range(3)
    ]
    fake_conn.cursor_instance.insert_errors = conflicts
    fake_conn.cursor_instance.fetch_rows = [None, None, None]
    with pytest.raises(pymysql.err.IntegrityError):
        repo.create_or_get(pending_run())
    inserts = [
        sql for sql, _ in fake_conn.cursor_instance.executed
        if sql.lstrip().upper().startswith("INSERT")
    ]
    assert len(inserts) == 3


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
