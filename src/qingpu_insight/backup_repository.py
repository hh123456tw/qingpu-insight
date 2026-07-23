from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime

import pymysql

from qingpu_insight.backups import BackupRecord

ConnectionFactory = Callable[[], pymysql.Connection]


class MySQLBackupRepository:
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
                with conn.cursor() as cursor:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS backup_records (
                            backup_id VARCHAR(36) NOT NULL PRIMARY KEY,
                            status VARCHAR(32) NOT NULL,
                            path VARCHAR(1024) NOT NULL,
                            sha256 VARCHAR(64) NOT NULL,
                            size_bytes BIGINT UNSIGNED NOT NULL,
                            created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                            restore_status VARCHAR(32) NULL,
                            restore_checked_at DATETIME(3) NULL
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def create(self, record: BackupRecord) -> None:
        with self._connection() as conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """INSERT INTO backup_records
                           (backup_id, status, path, sha256, size_bytes, created_at,
                            restore_status, restore_checked_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            record.backup_id,
                            record.status,
                            record.path,
                            record.sha256,
                            record.size_bytes,
                            record.created_at,
                            record.restore_status,
                            record.restore_checked_at,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def mark_restore(
        self, backup_id: str, status: str, checked_at: datetime,
    ) -> None:
        with self._connection() as conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """UPDATE backup_records
                           SET restore_status = %s, restore_checked_at = %s
                           WHERE backup_id = %s""",
                        (status, checked_at, backup_id),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def latest(self) -> BackupRecord | None:
        with self._connection() as conn:
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM backup_records"
                        " ORDER BY created_at DESC LIMIT 1",
                    )
                    row = cursor.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        if row is None:
            return None
        return BackupRecord(
            backup_id=str(row["backup_id"]),
            status=str(row["status"]),
            path=str(row["path"]),
            sha256=str(row["sha256"]),
            size_bytes=int(row["size_bytes"]),
            created_at=row["created_at"],
            restore_status=row.get("restore_status"),
            restore_checked_at=row.get("restore_checked_at"),
        )

    def list_recent(self, limit: int) -> list[BackupRecord]:
        with self._connection() as conn:
            try:
                with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM backup_records"
                        " ORDER BY created_at DESC LIMIT %s",
                        (limit,),
                    )
                    rows = cursor.fetchall()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return [
            BackupRecord(
                backup_id=str(r["backup_id"]),
                status=str(r["status"]),
                path=str(r["path"]),
                sha256=str(r["sha256"]),
                size_bytes=int(r["size_bytes"]),
                created_at=r["created_at"],
                restore_status=r.get("restore_status"),
                restore_checked_at=r.get("restore_checked_at"),
            )
            for r in rows
        ]