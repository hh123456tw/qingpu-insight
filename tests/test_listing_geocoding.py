from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest

from qingpu_insight.listing_geocoding import (
    DoorplateListingGeocoder,
    GeocoderUnavailable,
    GeocodingService,
    MySQLGeocodeCache,
)
from qingpu_insight.location_evidence import LocationEvidence


@dataclass
class FakeGeocoder:
    result: tuple[float, float] | None = (25.014, 121.211)
    version: str = "fake-v1"
    calls: int = 0

    def resolve(self, normalized_address: str) -> tuple[float, float] | None:
        self.calls += 1
        return self.result


class FailingGeocoder:
    version = "fake-v1"

    def resolve(self, normalized_address: str) -> tuple[float, float] | None:
        raise GeocoderUnavailable("provider is down")


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, LocationEvidence] = {}
        self.put_calls = 0

    def get(self, normalized_address: str) -> LocationEvidence | None:
        return self.values.get(normalized_address)

    def put(self, normalized_address: str, evidence: LocationEvidence) -> None:
        self.put_calls += 1
        self.values[normalized_address] = evidence


@pytest.fixture
def mysql_cache() -> MemoryCache:
    return MemoryCache()


@pytest.fixture
def fake_geocoder() -> FakeGeocoder:
    return FakeGeocoder()


@pytest.fixture
def failing_geocoder() -> FailingGeocoder:
    return FailingGeocoder()


def test_service_reuses_normalized_address_cache(
    mysql_cache: MemoryCache, fake_geocoder: FakeGeocoder
) -> None:
    def clock() -> datetime:
        return datetime(2026, 7, 22, 8, 30, tzinfo=UTC)
    service = GeocodingService(fake_geocoder, mysql_cache, clock=clock)

    first = service.resolve("桃園市 中壢區 高鐵南路一段 1 號")
    second = service.resolve("桃園市中壢區高鐵南路一段1號")

    assert first == second
    assert fake_geocoder.calls == 1
    assert first.geocoded_at == datetime(2026, 7, 22, 8, 30, tzinfo=UTC)
    assert first.confidence == "medium"
    assert first.geocoder_version == "fake-v1"


def test_provider_error_returns_unknown_without_cache_poison(
    mysql_cache: MemoryCache, failing_geocoder: FailingGeocoder
) -> None:
    service = GeocodingService(failing_geocoder, mysql_cache)

    result = service.resolve("桃園市中壢區高鐵南路一段1號")

    assert result.method == "unknown"
    assert result.reason == "geocoder_unavailable"
    assert mysql_cache.values == {}


def test_unresolved_or_invalid_provider_results_are_unknown_and_not_cached(
    mysql_cache: MemoryCache,
) -> None:
    unresolved = GeocodingService(FakeGeocoder(result=None), mysql_cache).resolve("高鐵南路一段1號")
    invalid = GeocodingService(FakeGeocoder(result=(51.5, -0.1)), mysql_cache).resolve(
        "高鐵南路一段2號"
    )

    assert unresolved.reason == "address_not_resolved"
    assert invalid.reason == "invalid_geocoder_coordinates"
    assert mysql_cache.values == {}


def test_doorplate_geocoder_resolves_only_an_unambiguous_exact_address() -> None:
    geocoder = DoorplateListingGeocoder(
        pd.DataFrame(
            {
                "地址": ["桃園市中壢區高鐵南路一段1號", "高鐵南路一段2號", "高鐵南路一段2號"],
                "緯度": [25.014, 25.015, 25.016],
                "經度": [121.211, 121.212, 121.213],
            }
        )
    )

    assert geocoder.resolve("高鐵南路一段1號") == (25.014, 121.211)
    assert geocoder.resolve("高鐵南路一段3號") is None
    assert geocoder.resolve("高鐵南路一段2號") is None


class FakeCursor:
    def __init__(self, row: dict[str, Any] | None = None, fail_insert: bool = False) -> None:
        self.row = row
        self.fail_insert = fail_insert
        self.executions: list[tuple[str, dict[str, Any] | None]] = []
        self.closed = False

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> int:
        self.executions.append((sql, params))
        if self.fail_insert and "INSERT INTO geocode_cache" in sql:
            raise RuntimeError("database write failed")
        return 1

    def fetchone(self) -> dict[str, Any] | None:
        return self.row

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.cursor_instance = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def test_mysql_cache_schema_and_upsert_use_one_committed_transaction() -> None:
    schema_connection = FakeConnection(FakeCursor())
    put_connection = FakeConnection(FakeCursor())
    connections = iter((schema_connection, put_connection))
    cache = MySQLGeocodeCache(lambda: next(connections))

    cache.ensure_schema()
    cache.put(
        "高鐵南路一段1號",
        LocationEvidence(
            25.014,
            121.211,
            "structured_address",
            "medium",
            "address_resolved",
            datetime(2026, 7, 22, 8, 30, tzinfo=UTC),
            "doorplate-v1",
        ),
    )

    schema_sql = schema_connection.cursor_instance.executions[0][0]
    upsert_sql, upsert_params = put_connection.cursor_instance.executions[0]
    assert "CREATE TABLE IF NOT EXISTS geocode_cache" in schema_sql
    assert "DATETIME(6)" in schema_sql
    assert "ON DUPLICATE KEY UPDATE" in upsert_sql
    assert upsert_params is not None
    assert upsert_params["normalized_address"] == "高鐵南路一段1號"
    assert upsert_params["latitude"] == 25.014
    assert upsert_params["longitude"] == 121.211
    assert upsert_params["method"] == "structured_address"
    assert upsert_params["geocoded_at"].tzinfo is None
    assert schema_connection.commits == 1
    assert put_connection.commits == 1
    assert schema_connection.closed and put_connection.closed


def test_mysql_cache_roundtrip_restores_aware_utc_and_rejects_untrusted_rows() -> None:
    row = {
        "latitude": 25.014,
        "longitude": 121.211,
        "method": "structured_address",
        "confidence": "medium",
        "reason": "address_resolved",
        "geocoded_at": datetime(2026, 7, 22, 8, 30),
        "geocoder_version": "doorplate-v1",
    }
    valid_connection = FakeConnection(FakeCursor(row))
    invalid_connections = [
        FakeConnection(FakeCursor({**row, "method": "invented"})),
        FakeConnection(FakeCursor({**row, "latitude": 51.5})),
        FakeConnection(FakeCursor({key: value for key, value in row.items() if key != "reason"})),
    ]
    connections = iter((valid_connection, *invalid_connections))
    cache = MySQLGeocodeCache(lambda: next(connections))

    evidence = cache.get("高鐵南路一段1號")
    untrusted = [cache.get(f"高鐵南路一段{number}號") for number in (2, 3, 4)]

    assert evidence is not None
    assert evidence.geocoded_at == datetime(2026, 7, 22, 8, 30, tzinfo=UTC)
    assert untrusted == [None, None, None]
    assert valid_connection.closed
    assert all(connection.closed for connection in invalid_connections)


def test_mysql_cache_rolls_back_and_propagates_write_errors() -> None:
    connection = FakeConnection(FakeCursor(fail_insert=True))
    cache = MySQLGeocodeCache(lambda: connection)
    evidence = LocationEvidence(
        25.014,
        121.211,
        "structured_address",
        "medium",
        "address_resolved",
        datetime(2026, 7, 22, 8, 30, tzinfo=UTC),
        "doorplate-v1",
    )

    with pytest.raises(RuntimeError, match="database write failed"):
        cache.put("高鐵南路一段1號", evidence)

    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed
