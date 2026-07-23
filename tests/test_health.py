from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from qingpu_insight.health import (
    DEFAULT_THRESHOLDS,
    HealthItem,
    HealthService,
    HealthSummary,
    summarize_health,
)

NOW = datetime.now(UTC)


def item(result: HealthSummary, code: str) -> HealthItem | None:
    for i in result.items:
        if i.code == code:
            return i
    return None


class FakeProbes:
    def __init__(self, current_listing: Any = None) -> None:
        self._current_listing = current_listing

    def mysql(self) -> HealthItem:
        return HealthItem("mysql", "healthy", NOW, "ok", 1, "boolean")

    def market_dataset(self) -> HealthItem:
        return HealthItem("market_dataset", "healthy", NOW, "ok", 1, "boolean")

    def listing_dataset(self, listing_type: str) -> HealthItem:
        code = f"listing_{listing_type}"
        if self._current_listing is None:
            return HealthItem(code, "critical", NOW, "no listing data", None, None)
        return HealthItem(code, "healthy", NOW, "ok", 1, "boolean")

    def latest_listing_job(self) -> HealthItem:
        return HealthItem("latest_listing_job", "healthy", NOW, "ok", None, None)

    def latest_backup(self) -> HealthItem:
        return HealthItem("latest_backup", "critical", NOW, "no backup", None, None)

    def disk_free(self) -> HealthItem:
        return HealthItem("disk_free", "healthy", NOW, "ok", 100 * 1024 ** 3, "bytes")


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
        if cursor_class is not None:
            self.cursor_instance._dict_mode = True
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def test_summary_uses_worst_item_status() -> None:
    summary = summarize_health([
        HealthItem("mysql", "healthy", NOW, "ok", 1, "boolean"),
        HealthItem("listing_sale_freshness", "warning", NOW, "stale", 30, "hours"),
    ], checked_at=NOW)
    assert summary.status == "warning"


def test_summary_critical_dominates_warning() -> None:
    summary = summarize_health([
        HealthItem("mysql", "healthy", NOW, "ok", 1, "boolean"),
        HealthItem("disk_free", "warning", NOW, "low", 9 * 1024 ** 3, "bytes"),
        HealthItem("backup", "critical", NOW, "missing", None, None),
    ], checked_at=NOW)
    assert summary.status == "critical"


def test_summary_healthy_when_all_healthy() -> None:
    summary = summarize_health([
        HealthItem("mysql", "healthy", NOW, "ok", 1, "boolean"),
    ], checked_at=NOW)
    assert summary.status == "healthy"


def test_summary_empty_items_is_healthy() -> None:
    summary = summarize_health([], checked_at=NOW)
    assert summary.status == "healthy"


def test_no_successful_listing_version_is_critical() -> None:
    service = HealthService(probes=FakeProbes(current_listing=None))
    result = service.run()
    assert item(result, "listing_sale").status == "critical"


def test_service_run_returns_summary_with_all_item_codes() -> None:
    service = HealthService(probes=FakeProbes(current_listing="v1"))
    result = service.run()
    codes = {i.code for i in result.items}
    expected = {
        "mysql", "market_dataset",
        "listing_sale", "listing_newhouse", "listing_rental",
        "latest_listing_job", "latest_backup", "disk_free",
    }
    assert codes == expected


def test_health_service_aggregates_worst_overall_status() -> None:
    service = HealthService(probes=FakeProbes(current_listing=None))
    result = service.run()
    assert result.status == "critical"


def test_default_thresholds_are_defined() -> None:
    assert "market_freshness_warning_hours" in DEFAULT_THRESHOLDS
    assert "listing_freshness_warning_hours" in DEFAULT_THRESHOLDS
    assert "disk_warning_bytes" in DEFAULT_THRESHOLDS
    assert "disk_critical_bytes" in DEFAULT_THRESHOLDS
    assert DEFAULT_THRESHOLDS["market_freshness_warning_hours"] == 24 * 45
    assert DEFAULT_THRESHOLDS["listing_freshness_warning_hours"] == 24 * 7
    assert DEFAULT_THRESHOLDS["disk_warning_bytes"] == 10 * 1024 ** 3
    assert DEFAULT_THRESHOLDS["disk_critical_bytes"] == 2 * 1024 ** 3


def test_health_item_immutability() -> None:
    item = HealthItem("mysql", "healthy", NOW, "ok", 1, "boolean")
    with pytest.raises(AttributeError):
        item.code = "changed"  # type: ignore[misc]


def test_health_summary_immutability() -> None:
    s = HealthSummary("healthy", NOW, ())
    with pytest.raises(AttributeError):
        s.status = "critical"  # type: ignore[misc]
