from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd

from qingpu_insight.evidence_repository import MySQLEvidenceRepository


class FakeCursor:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.result: list[Sequence[Any]] = [("v1",)]
        self.rowcount = 1

    def execute(self, sql: str, params: Any = None) -> int:
        self.executed.append(sql)
        return self.rowcount

    def fetchone(self) -> Sequence[Any] | None:
        return self.result[0] if self.result else None

    def fetchall(self) -> list[Sequence[Any]]:
        return self.result

    def close(self) -> None:
        pass

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        pass


class FakeConnection:
    def __init__(self) -> None:
        self._cursor = FakeCursor()
        self.closed = False

    def cursor(self, cursor_class: Any = None) -> FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def test_current_dataset_version_returns_version() -> None:
    conn = FakeConnection()

    def factory() -> FakeConnection:
        return conn

    repo = MySQLEvidenceRepository(factory)
    version = repo.current_dataset_version()
    assert version == "v1"

    executed = conn._cursor.executed
    assert len(executed) == 1
    sql = " ".join(executed[0].lower().split())
    assert "select version" in sql
    assert "from published_datasets" in sql
    assert "dataset_key = %s" in sql


def test_current_dataset_version_returns_unknown_when_no_row() -> None:
    conn = FakeConnection()
    conn._cursor.result = []

    def factory() -> FakeConnection:
        return conn

    repo = MySQLEvidenceRepository(factory)
    assert repo.current_dataset_version() == "unknown"


def test_load_candidates_has_correct_sql_shape(monkeypatch) -> None:
    conn = FakeConnection()
    recorded_sqls: list[str] = []

    def fake_read_sql(sql, con, params=None):
        recorded_sqls.append(sql)
        return pd.DataFrame()

    monkeypatch.setattr("pandas.read_sql", fake_read_sql)

    def factory() -> FakeConnection:
        return conn

    repo = MySQLEvidenceRepository(factory)
    repo.load_candidates(["c1"])
    assert len(recorded_sqls) == 1
    sql = " ".join(recorded_sqls[0].lower().split())
    assert "source_listing_id as listing_id" in sql
    assert "asking_price_twd as price" in sql
    assert "snapshot_at as observed_at" in sql
    assert "active = true" in sql
    assert "select *" not in sql


def test_load_market_evidence_has_correct_sql_shape(monkeypatch) -> None:
    conn = FakeConnection()
    conn._cursor.result = [("A18",)]
    recorded_sqls: list[str] = []

    def fake_read_sql(sql, con, params=None):
        recorded_sqls.append(sql)
        return pd.DataFrame()

    monkeypatch.setattr("pandas.read_sql", fake_read_sql)

    def factory() -> FakeConnection:
        return conn

    repo = MySQLEvidenceRepository(factory)
    repo.load_market_evidence(["c1"])
    assert len(recorded_sqls) == 1
    station_sql = " ".join(conn._cursor.executed[0].lower().split())
    assert "select distinct station_code" in station_sql
    assert "from listing_current" in station_sql

    market_sql = " ".join(recorded_sqls[0].lower().split())
    assert "total_price_twd as transaction_price" in market_sql
    assert "analysis_eligible = true" in market_sql
    assert "limit 500" in market_sql


def test_load_market_returns_empty_when_no_stations() -> None:
    conn = FakeConnection()
    conn._cursor.result = []

    def factory() -> FakeConnection:
        return conn

    repo = MySQLEvidenceRepository(factory)
    df = repo.load_market_evidence(["nonexistent"])
    assert len(df) == 0
