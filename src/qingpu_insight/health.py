from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

import pymysql

from qingpu_insight.jobs import JobService
from qingpu_insight.publishing import MySQLVersionPublisher

HealthStatus = Literal["healthy", "warning", "critical"]


@dataclass(frozen=True)
class HealthItem:
    code: str
    status: HealthStatus
    observed_at: datetime
    summary: str
    value: float | None
    unit: str | None


@dataclass(frozen=True)
class HealthSummary:
    status: HealthStatus
    checked_at: datetime
    items: tuple[HealthItem, ...] = ()


def summarize_health(items: Sequence[HealthItem], checked_at: datetime) -> HealthSummary:
    worst: HealthStatus = "healthy"
    for item in items:
        if item.status == "critical":
            worst = "critical"
            break
        if item.status == "warning":
            worst = "warning"
    return HealthSummary(status=worst, checked_at=checked_at, items=tuple(items))


DEFAULT_THRESHOLDS = {
    "market_freshness_warning_hours": 24 * 45,
    "listing_freshness_warning_hours": 24 * 7,
    "disk_warning_bytes": 10 * 1024 ** 3,
    "disk_critical_bytes": 2 * 1024 ** 3,
}

LISTING_TYPES = ("sale", "newhouse", "rental")


class HealthProbes(Protocol):
    def mysql(self) -> HealthItem: ...
    def market_dataset(self) -> HealthItem: ...
    def listing_dataset(self, listing_type: str) -> HealthItem: ...
    def latest_listing_job(self) -> HealthItem: ...
    def latest_backup(self) -> HealthItem: ...
    def disk_free(self) -> HealthItem: ...


class HealthService:
    def __init__(self, probes: HealthProbes) -> None:
        self._probes = probes

    def run(self) -> HealthSummary:
        checked_at = datetime.now(UTC)
        items = [
            self._probes.mysql(),
            self._probes.market_dataset(),
            self._probes.listing_dataset("sale"),
            self._probes.listing_dataset("newhouse"),
            self._probes.listing_dataset("rental"),
            self._probes.latest_listing_job(),
            self._probes.latest_backup(),
            self._probes.disk_free(),
        ]
        return summarize_health(items, checked_at)


class ProductionHealthProbes:
    def __init__(
        self,
        connection_factory: Callable[[], pymysql.Connection],
        job_service: JobService | None = None,
        version_publisher: MySQLVersionPublisher | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._job_service = job_service
        self._version_publisher = version_publisher

    def mysql(self) -> HealthItem:
        observed_at = datetime.now(UTC)
        try:
            conn = self._connection_factory()
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
            finally:
                conn.close()
            return HealthItem("mysql", "healthy", observed_at, "ok", 1, "boolean")
        except Exception:
            return HealthItem("mysql", "critical", observed_at, "unreachable", 0, "boolean")

    def market_dataset(self) -> HealthItem:
        observed_at = datetime.now(UTC)
        try:
            if self._version_publisher is None:
                return HealthItem("market_dataset", "critical", observed_at,
                                   "no publisher", None, None)
            current = self._version_publisher.current()
            if current is None:
                msg = "no published version"
                return HealthItem("market_dataset", "critical", observed_at, msg, None, None)
            ver = f"v{current.version}"
            return HealthItem("market_dataset", "healthy", observed_at, ver, 1, "boolean")
        except Exception:
            return HealthItem("market_dataset", "critical", observed_at, "check failed", None, None)

    def listing_dataset(self, listing_type: str) -> HealthItem:
        observed_at = datetime.now(UTC)
        code = f"listing_{listing_type}"
        try:
            conn = self._connection_factory()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT COUNT(*) FROM listing_current"
                        " WHERE listing_type = %s AND active = TRUE",
                        (listing_type,),
                    )
                    row = cursor.fetchone()
                    count = int(row[0]) if row else 0
            finally:
                conn.close()
            if count == 0:
                return HealthItem(code, "critical", observed_at, "no active listings", 0, "count")
            return HealthItem(code, "healthy", observed_at,
                               f"{count} listings", float(count), "count")
        except Exception:
            return HealthItem(code, "critical", observed_at,
                               "check failed", None, None)

    def latest_listing_job(self) -> HealthItem:
        observed_at = datetime.now(UTC)
        jc = "latest_listing_job"
        try:
            if self._job_service is None:
                return HealthItem(jc, "critical", observed_at, "no job service", None, None)
            recent = self._job_service.list_recent(limit=1)
            if not recent:
                return HealthItem(jc, "critical", observed_at, "no jobs", None, None)
            last = recent[0]
            if last.status == "succeeded":
                msg = f"succeeded: {last.run_id[:8]}"
                return HealthItem(jc, "healthy", observed_at, msg, None, None)
            if last.status == "failed":
                return HealthItem(jc, "critical", observed_at, "last job failed", None, None)
            msg = f"last job {last.status}"
            return HealthItem(jc, "warning", observed_at, msg, None, None)
        except Exception:
            return HealthItem(jc, "critical", observed_at, "check failed", None, None)

    def latest_backup(self) -> HealthItem:
        observed_at = datetime.now(UTC)
        try:
            conn = self._connection_factory()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT created_at FROM backup_records ORDER BY created_at DESC LIMIT 1"
                    )
                    row = cursor.fetchone()
            finally:
                conn.close()
            if row is None:
                return HealthItem("latest_backup", "critical", observed_at, "no backup", None, None)
            return HealthItem("latest_backup", "healthy", observed_at, "backup exists", None, None)
        except Exception:
            return HealthItem("latest_backup", "critical", observed_at, "check failed", None, None)

    def disk_free(self) -> HealthItem:
        observed_at = datetime.now(UTC)
        try:
            usage = shutil.disk_usage(".")
            free_bytes = usage.free
            df = "disk_free"
            crit = DEFAULT_THRESHOLDS["disk_critical_bytes"]
            warn = DEFAULT_THRESHOLDS["disk_warning_bytes"]
            if free_bytes < crit:
                return HealthItem(df, "critical", observed_at, "critically low",
                                   float(free_bytes), "bytes")
            if free_bytes < warn:
                return HealthItem(df, "warning", observed_at, "low",
                                   float(free_bytes), "bytes")
            return HealthItem(df, "healthy", observed_at, "ok", float(free_bytes), "bytes")
        except Exception:
            return HealthItem("disk_free", "warning", observed_at, "check failed", None, None)
