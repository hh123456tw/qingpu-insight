from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest

from qingpu_insight.evidence import EvidenceBuilder, UnknownCandidateError
from qingpu_insight.evidence_repository import MySQLEvidenceRepository, empty_market_frame
from qingpu_insight.report_contracts import ReportRequest


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
    assert "snapshot_at" in sql
    assert "as observed_at" not in sql
    assert "price_per_ping" in sql
    assert "acquisition_representation as location_method" in sql
    assert "model_evidence" in sql
    assert "title" not in sql
    assert "active = true" in sql
    assert "select *" not in sql


def test_load_market_evidence_has_correct_sql_shape(monkeypatch) -> None:
    conn = FakeConnection()
    conn._cursor.result = [("sale-001", "A18", 30.0)]
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
    cursor_sql = " ".join(conn._cursor.executed[0].lower().split())
    assert "select source_listing_id" in cursor_sql
    assert "station_code" in cursor_sql
    assert "building_area_ping" in cursor_sql
    assert "from listing_current" in cursor_sql
    assert "where source_listing_id in" in cursor_sql

    market_sql = " ".join(recorded_sqls[0].lower().split())
    assert "total_price_twd" in market_sql
    assert "as transaction_price" not in market_sql
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


# ---------------------------------------------------------------------------
# fake_mysql fixture — returns DataFrames with real DB column names
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC).isoformat()


@pytest.fixture
def fake_mysql(monkeypatch):
    class CaptureCursor:
        def __init__(self) -> None:
            self.executed: list[str] = []
            self.rowcount = 1

        def execute(self, sql: str, params: Any = None) -> int:
            self.executed.append(sql)
            return self.rowcount

        def fetchall(self) -> list[Sequence[Any]]:
            return [("sale-001", "A18", 30.0)]

        def fetchone(self) -> dict[str, Any] | None:
            return {"version": "v1"}

        def fetchmany(self, size: int = 1) -> list[Sequence[Any]]:
            return [("sale-001", "A18", 30.0)]

        def close(self) -> None:
            pass

        def __enter__(self) -> CaptureCursor:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    class FakeConn:
        def __init__(self) -> None:
            self._cursor = CaptureCursor()
            self.closed = False

        def cursor(self, cursor_class: Any = None) -> CaptureCursor:
            return self._cursor

        def close(self) -> None:
            self.closed = True

    def factory() -> FakeConn:
        return FakeConn()

    def fake_read_sql(sql: str, con: Any, params: Any = None) -> pd.DataFrame:
        if "listing_current" in sql and "WHERE" in sql.upper():
            return pd.DataFrame([{
                "source_listing_id": "sale-001",
                "listing_type": "sale",
                "asking_price_twd": 15_000_000,
                "asking_unit_price_low_twd_per_ping": 500_000,
                "asking_unit_price_high_twd_per_ping": 550_000,
                "building_area_ping": 30.0,
                "station_code": "A18",
                "station_distance_m": 300.0,
                "building_age_years": 5.0,
                "snapshot_at": _NOW,
                "acquisition_representation": "structured_address",
                "model_evidence": None,
            }])
        elif "market_transactions" in sql:
            return pd.DataFrame([{
                "transaction_key": "txn-001",
                "station_code": "A18",
                "transaction_type": "sale",
                "transaction_date": "2025-06-01",
                "total_price_twd": 14_500_000,
                "unit_price_per_ping_twd": 483_333,
                "building_area_ping": 30.0,
            }])
        return pd.DataFrame()

    monkeypatch.setattr("pandas.read_sql", fake_read_sql)

    class FakeMySQL:
        def __init__(self) -> None:
            self.factory = factory

    return FakeMySQL()


# ---------------------------------------------------------------------------
# Integration: MySQL repository output builds an EvidencePack
# ---------------------------------------------------------------------------


def test_mysql_repository_output_builds_evidence_pack(fake_mysql) -> None:
    repository = MySQLEvidenceRepository(fake_mysql.factory)
    pack = EvidenceBuilder(repository).build(
        ReportRequest(
            candidate_ids=("sale-001",),
            intended_use="self_use",
            provider="rule",
        )
    )
    assert pack.candidates[0].candidate_id == "sale-001"
    assert {fact.kind for fact in pack.facts} >= {
        "asking_price",
        "unit_price",
        "data_freshness",
        "nearby_transactions_summary",
    }


def test_candidate_without_market_comparables_keeps_listing_facts(
    fake_mysql, monkeypatch
) -> None:
    def empty_market_sql(sql, con, params=None):
        if "listing_current" in sql and "WHERE" in sql.upper():
            return pd.DataFrame([{
                "source_listing_id": "sale-001",
                "listing_type": "sale",
                "asking_price_twd": 15_000_000,
                "asking_unit_price_low_twd_per_ping": 500_000,
                "asking_unit_price_high_twd_per_ping": 550_000,
                "building_area_ping": 30.0,
                "station_code": "A18",
                "station_distance_m": 300.0,
                "building_age_years": 5.0,
                "snapshot_at": _NOW,
                "acquisition_representation": "structured_address",
                "model_evidence": None,
            }])
        if "market_transactions" in sql:
            return pd.DataFrame(
                columns=["transaction_key", "station_code", "transaction_type",
                         "transaction_date", "total_price_twd",
                         "unit_price_per_ping_twd", "building_area_ping"]
            )
        return pd.DataFrame()

    monkeypatch.setattr("pandas.read_sql", empty_market_sql)

    repository = MySQLEvidenceRepository(fake_mysql.factory)
    pack = EvidenceBuilder(repository).build(
        ReportRequest(
            candidate_ids=("sale-001",),
            intended_use="self_use",
            provider="rule",
        )
    )
    assert pack.candidates[0].candidate_id == "sale-001"
    kinds = {fact.kind for fact in pack.facts}
    assert "nearby_transactions_summary" not in kinds
    assert "asking_price" in kinds
    assert "unit_price" in kinds


def test_unknown_candidate_raises_domain_error(fake_mysql, monkeypatch) -> None:
    def no_data_sql(sql, con, params=None):
        return pd.DataFrame()

    monkeypatch.setattr("pandas.read_sql", no_data_sql)

    repository = MySQLEvidenceRepository(fake_mysql.factory)
    with pytest.raises(UnknownCandidateError) as exc:
        EvidenceBuilder(repository).build(
            ReportRequest(
                candidate_ids=("unknown",),
                intended_use="self_use",
                provider="rule",
            )
        )
    assert "unknown" in str(exc.value)


def test_market_comparables_are_station_and_area_scoped(fake_mysql, monkeypatch) -> None:
    def scoped_read_sql(sql, con, params=None):
        if "listing_current" in sql and "WHERE" in sql.upper():
            return pd.DataFrame([{
                "source_listing_id": "sale-001",
                "listing_type": "sale",
                "asking_price_twd": 15_000_000,
                "asking_unit_price_low_twd_per_ping": 500_000,
                "asking_unit_price_high_twd_per_ping": 550_000,
                "building_area_ping": 30.0,
                "station_code": "A18",
                "station_distance_m": 300.0,
                "building_age_years": 5.0,
                "snapshot_at": _NOW,
                "acquisition_representation": "structured_address",
                "model_evidence": None,
            }])
        if "market_transactions" in sql:
            return pd.DataFrame([
                {
                    "transaction_key": "txn-001",
                    "station_code": "A18",
                    "transaction_type": "sale",
                    "transaction_date": "2025-06-01",
                    "total_price_twd": 14_500_000,
                    "unit_price_per_ping_twd": 483_333,
                    "building_area_ping": 30.0,
                },
                {
                    "transaction_key": "txn-002",
                    "station_code": "A18",
                    "transaction_type": "sale",
                    "transaction_date": "2025-05-15",
                    "total_price_twd": 12_000_000,
                    "unit_price_per_ping_twd": 300_000,
                    "building_area_ping": 40.0,
                },
                {
                    "transaction_key": "txn-003",
                    "station_code": "A19",
                    "transaction_type": "sale",
                    "transaction_date": "2025-06-10",
                    "total_price_twd": 16_000_000,
                    "unit_price_per_ping_twd": 500_000,
                    "building_area_ping": 32.0,
                },
            ])
        return pd.DataFrame()

    monkeypatch.setattr("pandas.read_sql", scoped_read_sql)

    repository = MySQLEvidenceRepository(fake_mysql.factory)
    pack = EvidenceBuilder(repository).build(
        ReportRequest(
            candidate_ids=("sale-001",),
            intended_use="self_use",
            provider="rule",
        )
    )
    # Should have exactly one nearby_transactions_summary fact
    txn_facts = [f for f in pack.facts if f.kind == "nearby_transactions_summary"]
    assert len(txn_facts) == 1
    # The value should include "1 transaction" because only txn-001 is A18 + area-scoped
    # txn-002 is same station but 40 ping (outside 80-120% of 30 = 24-36)
    # txn-003 is A19 (different station)
    assert "1 transactions" in txn_facts[0].value


def test_empty_market_frame_has_correct_columns() -> None:
    df = empty_market_frame()
    expected = {"listing_id", "transaction_key", "station_code", "transaction_type",
                "transaction_date", "transaction_price", "unit_price_per_ping_twd",
                "building_area_ping"}
    assert set(df.columns) == expected
    assert len(df) == 0
