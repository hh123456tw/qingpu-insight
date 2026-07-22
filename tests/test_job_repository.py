from __future__ import annotations

from typing import Any

import pytest

from qingpu_insight.job_repository import MySQLJobRepository
from qingpu_insight.jobs import JobRun


class FakeCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self._fetch_result: list[dict[str, Any]] | None = None
        self.rowcount: int = 0
        self._closed = False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        self.executed.append((sql, params))
        return self.rowcount

    def fetchone(self) -> dict[str, Any] | None:
        if self._fetch_result:
            return self._fetch_result[0]
        return None

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self, cursor_class=None) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


@pytest.fixture
def fake_conn() -> FakeConnection:
    return FakeConnection()


def test_create_and_get(fake_conn: FakeConnection) -> None:
    repo = MySQLJobRepository(fake_conn)
    run = JobRun(
        run_id="test-uuid",
        job_type="listing_update",
        trigger="manual",
        idempotency_key="ik-1",
        status="pending",
        started_at=None,
        finished_at=None,
        attempt=1,
        input_version=None,
        output_version=None,
        summary={},
        error_code=None,
        error_message=None,
    )
    repo.create(run)
    ddl = [
        sql for sql, _ in fake_conn.cursor_instance.executed
        if sql.strip().upper().startswith("CREATE")
    ]
    assert len(ddl) >= 1
    insert = [
        (sql, params) for sql, params in fake_conn.cursor_instance.executed
        if sql.strip().upper().startswith("INSERT")
    ]
    assert len(insert) == 1
    assert fake_conn.committed
