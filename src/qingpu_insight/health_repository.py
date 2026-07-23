from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pymysql

from qingpu_insight.health import HealthItem, HealthSummary

ConnectionFactory = Callable[[], pymysql.Connection]


class MySQLHealthRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory
        self._ensure_schema()

    @contextmanager
    def _connection(self) -> Iterator[pymysql.Connection]:
        conn = self._connection_factory()
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connection() as conn:
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS health_runs (
                            run_id VARCHAR(36) NOT NULL PRIMARY KEY,
                            status VARCHAR(16) NOT NULL,
                            checked_at DATETIME(3) NOT NULL,
                            summary JSON NOT NULL,
                            created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """)
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS health_items (
                            run_id VARCHAR(36) NOT NULL,
                            code VARCHAR(64) NOT NULL,
                            status VARCHAR(16) NOT NULL,
                            observed_at DATETIME(3) NOT NULL,
                            summary VARCHAR(255) NOT NULL DEFAULT '',
                            value DOUBLE NULL,
                            unit VARCHAR(32) NULL,
                            PRIMARY KEY (run_id, code)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """)
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS backup_records (
                            path VARCHAR(1024) NOT NULL PRIMARY KEY,
                            size_bytes BIGINT UNSIGNED NOT NULL,
                            checksum VARCHAR(64) NOT NULL,
                            backup_type VARCHAR(32) NOT NULL,
                            created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def save(self, summary: HealthSummary) -> HealthSummary:
        run_id = str(uuid.uuid4())
        with self._connection() as conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """INSERT INTO health_runs (run_id, status, checked_at, summary)
                           VALUES (%s, %s, %s, %s)""",
                        (
                            run_id,
                            summary.status,
                            summary.checked_at,
                            json.dumps(
                                {
                                    "status": summary.status,
                                    "checked_at": summary.checked_at.isoformat(),
                                    "item_count": len(summary.items),
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    )
                    for item in summary.items:
                        cursor.execute(
                            """INSERT INTO health_items
                               (run_id, code, status, observed_at, summary, value, unit)
                               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                            (
                                run_id,
                                item.code,
                                item.status,
                                item.observed_at,
                                item.summary,
                                item.value,
                                item.unit,
                            ),
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return summary

    def latest(self) -> HealthSummary | None:
        with self._connection() as conn:
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM health_runs"
                        " ORDER BY checked_at DESC, created_at DESC LIMIT 1"
                    )
                    row = cursor.fetchone()
                    if row is None:
                        return None
                    cursor.execute(
                        "SELECT * FROM health_items WHERE run_id = %s ORDER BY code",
                        (row["run_id"],),
                    )
                    item_rows = cursor.fetchall()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        items = tuple(
            HealthItem(
                code=str(r["code"]),
                status=str(r["status"]),
                observed_at=r["observed_at"],
                summary=str(r["summary"]),
                value=float(r["value"]) if r["value"] is not None else None,
                unit=str(r["unit"]) if r["unit"] is not None else None,
            )
            for r in item_rows
        )
        return HealthSummary(
            status=str(row["status"]),
            checked_at=row["checked_at"],
            items=items,
        )
