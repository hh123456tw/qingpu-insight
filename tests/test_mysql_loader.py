from typing import Any

import pandas as pd
import pytest

from qingpu_insight.mysql_loader import _UPSERT_SQL, INSERT_COLUMNS, load_market_rows


class FakeCursor:
    def __init__(self) -> None:
        self.executemany_calls: list[tuple[str, list[tuple[Any, ...]]]] = []
        self.close_called = False

    def executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> int:
        self.executemany_calls.append((sql, list(rows)))
        return len(rows)

    def close(self) -> None:
        self.close_called = True

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()
        self.committed = False
        self.rolled_back = False
        self.close_called = False

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.close_called = True


@pytest.fixture
def fake_connection() -> FakeConnection:
    return FakeConnection()


@pytest.fixture
def market_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "transaction_key": [f"key_{i:03d}" for i in range(12)],
        "transaction_type": ["resale"] * 12,
        "record_id": [f"R{i}" for i in range(12)],
        "station_code": ["A17"] * 12,
        "transaction_date": pd.to_datetime(["2026-01-15"] * 12),
        "building_area_sqm": [99.17355] * 12,
        "building_area_ping": [30.0] * 12,
        "unit_price_sqm_twd": [181500] * 12,
        "unit_price_per_ping_twd": [600000.0] * 12,
        "total_price_twd": [18000000] * 12,
        "building_type": ["住宅大樓"] * 12,
        "bedrooms": [3] * 12,
        "living_rooms": [2] * 12,
        "bathrooms": [2] * 12,
        "building_age_years": [6.04] * 12,
        "station_distance_m": [500.0] * 12,
        "longitude": [121.21] * 12,
        "latitude": [25.01] * 12,
        "match_quality": ["exact"] * 12,
        "source_file": ["a.csv"] * 12,
    })


def test_loader_uses_upsert_and_returns_loaded_count(
    fake_connection: FakeConnection, market_frame: pd.DataFrame
) -> None:
    count = load_market_rows(fake_connection, market_frame, batch_size=5)
    sql, rows = fake_connection.cursor_instance.executemany_calls[0]
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert fake_connection.cursor_instance.executemany_calls[0][0] == _UPSERT_SQL
    assert count == len(market_frame)
    assert len(rows) == 5
    assert fake_connection.committed


def test_loader_batches_remainder_correctly(
    fake_connection: FakeConnection, market_frame: pd.DataFrame
) -> None:
    count = load_market_rows(fake_connection, market_frame, batch_size=5)
    assert count == 12
    calls = fake_connection.cursor_instance.executemany_calls
    assert len(calls) == 3
    assert len(calls[0][1]) == 5
    assert len(calls[1][1]) == 5
    assert len(calls[2][1]) == 2


def test_loader_converts_nan_to_none(fake_connection: FakeConnection) -> None:
    df = pd.DataFrame({
        "transaction_key": ["k_nan"],
        "transaction_type": ["resale"],
        "record_id": [None],
        "station_code": ["A17"],
        "transaction_date": [pd.Timestamp("2026-01-15")],
        "building_area_sqm": [99.17355],
        "building_area_ping": [30.0],
        "unit_price_sqm_twd": [181500],
        "unit_price_per_ping_twd": [600000.0],
        "total_price_twd": [18000000],
        "building_type": [None],
        "bedrooms": [None],
        "living_rooms": [None],
        "bathrooms": [None],
        "building_age_years": [float("nan")],
        "station_distance_m": [500.0],
        "longitude": [121.21],
        "latitude": [25.01],
        "match_quality": ["exact"],
        "source_file": ["a.csv"],
    })
    df.loc[0, "building_type"] = float("nan")
    df.loc[0, "bedrooms"] = float("nan")

    count = load_market_rows(fake_connection, df, batch_size=5)
    assert count == 1
    sql, rows = fake_connection.cursor_instance.executemany_calls[0]
    row = rows[0]
    assert row[10] is None  # building_type
    assert row[11] is None  # bedrooms
    assert row[14] is None  # building_age_years (float column)
    assert fake_connection.committed


def test_loader_rollback_on_error(fake_connection: FakeConnection) -> None:
    bad_cursor = FakeCursor()
    original_executemany = bad_cursor.executemany

    call_count = 0

    def failing_executemany(sql: str, rows: list[tuple]) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            msg = "Deadlock found"
            raise RuntimeError(msg)
        return original_executemany(sql, rows)

    bad_cursor.executemany = failing_executemany

    class FailingConnection(FakeConnection):
        def cursor(self) -> FakeCursor:
            return bad_cursor

    fc = FailingConnection()
    df = pd.DataFrame({
        "transaction_key": [f"k{i}" for i in range(15)],
        "transaction_type": ["resale"] * 15,
        "record_id": [f"R{i}" for i in range(15)],
        "station_code": ["A17"] * 15,
        "transaction_date": pd.to_datetime(["2026-01-15"] * 15),
        "building_area_sqm": [99.17355] * 15,
        "building_area_ping": [30.0] * 15,
        "unit_price_sqm_twd": [181500] * 15,
        "unit_price_per_ping_twd": [600000.0] * 15,
        "total_price_twd": [18000000] * 15,
        "building_type": ["住宅大樓"] * 15,
        "bedrooms": [3] * 15,
        "living_rooms": [2] * 15,
        "bathrooms": [2] * 15,
        "building_age_years": [6.04] * 15,
        "station_distance_m": [500.0] * 15,
        "longitude": [121.21] * 15,
        "latitude": [25.01] * 15,
        "match_quality": ["exact"] * 15,
        "source_file": ["a.csv"] * 15,
    })

    with pytest.raises(RuntimeError, match="Deadlock found"):
        load_market_rows(fc, df, batch_size=10)
    assert fc.rolled_back
    assert not fc.committed
    assert len(bad_cursor.executemany_calls) == 1


def test_upsert_refreshes_all_mutable_market_fields() -> None:
    for column in INSERT_COLUMNS[1:]:
        assert f"{column}=VALUES({column})" in _UPSERT_SQL
