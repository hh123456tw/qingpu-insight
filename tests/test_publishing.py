from __future__ import annotations

from typing import Any

import pytest

from qingpu_insight.publishing import DatasetVersion, MySQLVersionPublisher


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
def mysql_publisher() -> MySQLVersionPublisher:
    conn = FakeConnection()
    return MySQLVersionPublisher(conn, dataset_key="listings")


class TestMySQLVersionPublisher:
    def test_stage_creates_version(self, mysql_publisher: MySQLVersionPublisher) -> None:
        mysql_publisher.stage(DatasetVersion("v1", "run-1", "ready", {"rows": 10}))
        executed = mysql_publisher._connection.cursor_instance.executed
        assert any("dataset_versions" in sql for sql, _ in executed)
        assert mysql_publisher._connection.committed

    def test_publish_updates_pointer(self, mysql_publisher: MySQLVersionPublisher) -> None:
        conn: FakeConnection = mysql_publisher._connection  # type: ignore[assignment]
        conn.cursor_instance._fetch_result = [
            {"version": "v1", "dataset_key": "listings", "run_id": "run-1",
             "status": "ready", "summary": '{"rows": 10}',
             "artifact_hash": None, "artifact_row_count": None},
        ]
        mysql_publisher.publish("v1")
        executed = conn.cursor_instance.executed
        assert any("published_datasets" in sql for sql, _ in executed)

    def test_publish_rejects_non_ready(self, mysql_publisher: MySQLVersionPublisher) -> None:
        conn: FakeConnection = mysql_publisher._connection  # type: ignore[assignment]
        conn.cursor_instance._fetch_result = [
            {"version": "v1", "dataset_key": "listings", "run_id": "run-1",
             "status": "building", "summary": '{"rows": 3}',
             "artifact_hash": None, "artifact_row_count": None},
        ]
        with pytest.raises(ValueError, match="expected 'ready'"):
            mysql_publisher.publish("v1")

    def test_current_after_no_publish_is_none(
        self, mysql_publisher: MySQLVersionPublisher,
    ) -> None:
        assert mysql_publisher.current() is None

    def test_current_returns_published_version(
        self, mysql_publisher: MySQLVersionPublisher,
    ) -> None:
        conn: FakeConnection = mysql_publisher._connection  # type: ignore[assignment]
        conn.cursor_instance._fetch_result = [
            {"version": "v1", "dataset_key": "listings", "run_id": "run-1",
             "status": "ready", "summary": '{"rows": 10}',
             "artifact_hash": "abc123", "artifact_row_count": 10},
        ]
        current = mysql_publisher.current()
        assert current is not None
        assert current.version == "v1"
        assert current.artifact_hash == "abc123"
