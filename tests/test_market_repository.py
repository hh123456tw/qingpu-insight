from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import pytest

from qingpu_insight.market_metrics import MarketFilters, market_trends
from qingpu_insight.market_repository import (
    ALLOWLISTED_COLUMNS,
    MySQLMarketDataSource,
    ParquetMarketDataSource,
    _build_filter_sql,
    repository_from_env,
)
from qingpu_insight.model_features import build_model_frame


def test_repository_defaults_to_parquet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QINGPU_DATABASE_URL", raising=False)
    repo = repository_from_env(tmp_path)
    assert isinstance(repo, ParquetMarketDataSource)


def test_repository_uses_mysql_when_url_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QINGPU_DATABASE_URL", "mysql+pymysql://user:pass@localhost/qingpu_insight")
    repo = repository_from_env(tmp_path)
    assert isinstance(repo, MySQLMarketDataSource)


class FakeCursor:
    def __init__(self) -> None:
        self.executed_sql: str | None = None
        self.executed_params: dict | None = None
        self.fetchall_result: list[tuple] = []
        self.close_called = False

    def execute(self, sql: str, params: dict | None = None) -> int:
        self.executed_sql = sql
        self.executed_params = params
        return 0

    def fetchall(self) -> list[tuple]:
        return self.fetchall_result

    def close(self) -> None:
        self.close_called = True

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()
        self.close_called = False

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def close(self) -> None:
        self.close_called = True


def test_mysql_source_generates_parameterized_sql() -> None:
    fake = FakeConnection()
    cursor = fake.cursor_instance
    row = {
        "transaction_key": "k1",
        "transaction_type": "resale",
        "record_id": "R1",
        "station_code": "A18",
        "transaction_date": date(2026, 1, 15),
        "building_area_sqm": 99.17355,
        "building_area_ping": 30.0,
        "unit_price_sqm_twd": 181500,
        "unit_price_per_ping_twd": 600000.0,
        "total_price_twd": 18000000,
        "building_type": "住宅大樓",
        "bedrooms": 3,
        "living_rooms": 2,
        "bathrooms": 2,
        "building_age_years": 6.04,
        "station_distance_m": 500.0,
        "longitude": 121.21,
        "latitude": 25.01,
        "match_quality": "exact",
        "source_file": "a.csv",
        "floor": "五層",
        "total_floors": "十五層",
        "parking_type": "坡道平面",
        "parking_area_sqm": 33.0,
        "parking_price_twd": 2_000_000,
        "analysis_eligible": True,
    }
    cursor.fetchall_result = [tuple(row[column] for column in ALLOWLISTED_COLUMNS)]

    parsed = urlparse("mysql://user:pass@localhost/qingpu_insight")
    source = MySQLMarketDataSource(parsed, _test_connection=fake)
    filters = MarketFilters(
        transaction_type="resale",
        station_codes=("A18", "A19"),
        date_from=pd.Timestamp("2026-01-01"),
        date_to=pd.Timestamp("2026-02-01"),
        area_ping_min=20.0,
        area_ping_max=100.0,
        building_types=("住宅大樓",),
        bedrooms=(3, 4),
    )
    df = source.load(filters)

    assert cursor.executed_sql is not None
    assert "%(transaction_type)s" in cursor.executed_sql
    assert "%(date_from)s" in cursor.executed_sql
    assert "%(sc_0)s" in cursor.executed_sql or "station_code IN" in cursor.executed_sql
    assert cursor.executed_params is not None
    assert cursor.executed_params["transaction_type"] == "resale"

    assert list(df.columns) == list(ALLOWLISTED_COLUMNS)
    assert len(df) == 1
    assert df.iloc[0]["transaction_key"] == "k1"
    assert pd.api.types.is_datetime64_any_dtype(df["transaction_date"])
    trend_rows = pd.concat([df] * 10, ignore_index=True)
    trends = market_trends(trend_rows, filters)
    assert trends[0]["month"] == "2026-01"
    assert trends[0]["record_count"] == 10
    assert len(build_model_frame(df, "resale")) == 1


def test_parquet_source_filters_by_transaction_type(tmp_path: Path) -> None:
    df = pd.DataFrame(
        {
            "transaction_type": ["resale", "presale", "resale"],
            "station_code": ["A17", "A17", "A18"],
            "transaction_date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            "building_area_ping": [30.0, 25.0, 40.0],
            "building_type": ["住宅大樓", "住宅大樓", "華廈"],
            "bedrooms": [3, 2, 4],
            "unit_price_per_ping_twd": [600000.0, 800000.0, 500000.0],
            "total_price_twd": [18000000, 20000000, 20000000],
            "longitude": [121.21, 121.22, 121.20],
            "latitude": [25.01, 25.02, 25.00],
            "record_id": ["R1", "P1", "R2"],
            "building_area_sqm": [99.17355, 82.644625, 132.2314],
            "unit_price_sqm_twd": [181500, 242000, 151250],
            "station_distance_m": [500.0, 600.0, 300.0],
            "match_quality": ["exact", "exact", "exact"],
            "source_file": ["a.csv", "b.csv", "a.csv"],
        }
    )
    path = tmp_path / "market_transactions.parquet"
    df.to_parquet(path, index=False)

    source = ParquetMarketDataSource(path)
    filters = MarketFilters(transaction_type="resale")
    result = source.load(filters)
    assert list(result["transaction_type"]) == ["resale", "resale"]


def test_build_filter_sql_uses_named_placeholders() -> None:
    filters = MarketFilters(
        transaction_type="presale",
        station_codes=("A17",),
        date_from=pd.Timestamp("2026-01-01"),
        building_types=("住宅大樓",),
        bedrooms=(2, 3),
    )
    where, params = _build_filter_sql(filters)
    assert "%(transaction_type)s" in where
    assert "%(sc_0)s" in where
    assert "%(date_from)s" in where
    assert "%(bt_0)s" in where
    assert "%(br_0)s" in where
    assert params["transaction_type"] == "presale"
    assert params["sc_0"] == "A17"
    assert params["br_0"] == 2
    assert params["br_1"] == 3


def test_build_filter_sql_selects_all_stations_when_all_three() -> None:
    filters = MarketFilters(transaction_type="resale")
    where, params = _build_filter_sql(filters)
    assert "station_code" not in where
    assert where == "transaction_type = %(transaction_type)s"
    assert params == {"transaction_type": "resale"}
