from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast

import pymysql

from qingpu_insight.jobs import JobRun, JobStatus

ConnectionFactory = Callable[[], pymysql.Connection]
_CREATE_ATTEMPTS = 3


class DuplicateActiveJobRuns(RuntimeError):
    def __init__(self, idempotency_key: str, active_count: int) -> None:
        self.idempotency_key = idempotency_key
        self.active_count = active_count
        super().__init__(
            f"cannot enforce active job uniqueness: idempotency key {idempotency_key!r} "
            f"has {active_count} active rows"
        )


class MySQLJobRepository:
    def __init__(self, connection: pymysql.Connection | ConnectionFactory) -> None:
        if callable(connection):
            self._connection_factory = connection
            self._close_connections = True
        else:
            self._connection_factory = lambda: connection
            self._close_connections = False
        self._ensure_schema()

    @contextmanager
    def _connection(self) -> Iterator[pymysql.Connection]:
        connection = self._connection_factory()
        try:
            yield connection
        finally:
            if self._close_connections:
                close = getattr(connection, "close", None)
                if close is not None:
                    close()

    def _ensure_schema(self) -> None:
        with self._connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS job_runs (
                            run_id VARCHAR(36) NOT NULL PRIMARY KEY,
                            job_type VARCHAR(64) NOT NULL,
                            `trigger` VARCHAR(32) NOT NULL,
                            idempotency_key VARCHAR(255) NOT NULL,
                            status VARCHAR(32) NOT NULL,
                            active_idempotency_key VARCHAR(255)
                                GENERATED ALWAYS AS (
                                    CASE
                                        WHEN status IN ('pending', 'running', 'retry_wait')
                                        THEN idempotency_key
                                        ELSE NULL
                                    END
                                ) STORED,
                            started_at DATETIME(3) NULL,
                            finished_at DATETIME(3) NULL,
                            attempt INT NOT NULL DEFAULT 1,
                            input_version VARCHAR(64) NULL,
                            output_version VARCHAR(64) NULL,
                            summary JSON NOT NULL,
                            error_code VARCHAR(64) NULL,
                            error_message TEXT NULL,
                            created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                            updated_at DATETIME(3) NOT NULL
                                DEFAULT CURRENT_TIMESTAMP(3)
                                ON UPDATE CURRENT_TIMESTAMP(3),
                            UNIQUE INDEX uq_job_runs_active_key (active_idempotency_key),
                            INDEX idx_idempotency_key (idempotency_key),
                            INDEX idx_status (status)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """)
                    cursor.execute(
                        """SELECT 1 AS present
                           FROM information_schema.COLUMNS
                           WHERE TABLE_SCHEMA = DATABASE()
                             AND TABLE_NAME = 'job_runs'
                             AND COLUMN_NAME = 'active_idempotency_key'
                           LIMIT 1"""
                    )
                    if cursor.fetchone() is None:
                        cursor.execute(
                            """ALTER TABLE job_runs
                               ADD COLUMN active_idempotency_key VARCHAR(255)
                               GENERATED ALWAYS AS (
                                   CASE
                                       WHEN status IN ('pending', 'running', 'retry_wait')
                                       THEN idempotency_key
                                       ELSE NULL
                                   END
                               ) STORED"""
                        )
                    cursor.execute(
                        """SELECT 1 AS present
                           FROM information_schema.STATISTICS
                           WHERE TABLE_SCHEMA = DATABASE()
                             AND TABLE_NAME = 'job_runs'
                             AND INDEX_NAME = 'uq_job_runs_active_key'
                           LIMIT 1"""
                    )
                    if cursor.fetchone() is None:
                        cursor.execute(
                            """SELECT idempotency_key, COUNT(*) AS active_count
                               FROM job_runs
                               WHERE status IN ('pending', 'running', 'retry_wait')
                               GROUP BY idempotency_key
                               HAVING COUNT(*) > 1
                               ORDER BY idempotency_key
                               LIMIT 1"""
                        )
                        duplicate = cursor.fetchone()
                        if duplicate is not None:
                            raise DuplicateActiveJobRuns(
                                str(duplicate["idempotency_key"]),
                                int(duplicate["active_count"]),
                            )
                        cursor.execute(
                            """ALTER TABLE job_runs
                               ADD UNIQUE INDEX uq_job_runs_active_key
                               (active_idempotency_key)"""
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _row_to_run(row: dict[str, Any]) -> JobRun:
        raw_summary = row.get("summary")
        if isinstance(raw_summary, str):
            summary = json.loads(raw_summary)
        elif isinstance(raw_summary, dict):
            summary = raw_summary
        else:
            summary = {}
        return JobRun(
            run_id=str(row["run_id"]),
            job_type=str(row["job_type"]),
            trigger=str(row["trigger"]),
            idempotency_key=str(row["idempotency_key"]),
            status=cast(JobStatus, row["status"]),
            started_at=row["started_at"] if row.get("started_at") else None,
            finished_at=row["finished_at"] if row.get("finished_at") else None,
            attempt=int(row["attempt"]),
            input_version=row.get("input_version"),
            output_version=row.get("output_version"),
            summary=summary,
            error_code=row.get("error_code"),
            error_message=row.get("error_message"),
        )

    @staticmethod
    def _insert(cursor: Any, run: JobRun) -> None:
        cursor.execute(
            """INSERT INTO job_runs
               (run_id, job_type, `trigger`, idempotency_key, status, attempt,
                input_version, output_version, summary, error_code, error_message,
                started_at, finished_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                run.run_id,
                run.job_type,
                run.trigger,
                run.idempotency_key,
                run.status,
                run.attempt,
                run.input_version,
                run.output_version,
                json.dumps(run.summary, ensure_ascii=False),
                run.error_code,
                run.error_message,
                run.started_at,
                run.finished_at,
            ),
        )

    def create_or_get(self, run: JobRun) -> tuple[JobRun, bool]:
        with self._connection() as connection:
            for attempt in range(_CREATE_ATTEMPTS):
                try:
                    with connection.cursor() as cursor:
                        self._insert(cursor, run)
                    connection.commit()
                    return run, True
                except pymysql.err.IntegrityError as error:
                    connection.rollback()
                    if not self._is_active_key_duplicate(error):
                        raise
                    duplicate_error = error
                except Exception:
                    connection.rollback()
                    raise

                try:
                    with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                        cursor.execute(
                            "SELECT * FROM job_runs WHERE idempotency_key = %s"
                            " AND status IN ('pending', 'running', 'retry_wait') FOR UPDATE",
                            (run.idempotency_key,),
                        )
                        row = cursor.fetchone()
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                if row is None:
                    if attempt == _CREATE_ATTEMPTS - 1:
                        raise duplicate_error
                    continue
                return self._row_to_run(row), False
        raise AssertionError("unreachable")

    @staticmethod
    def _is_active_key_duplicate(error: pymysql.err.IntegrityError) -> bool:
        return (
            bool(error.args)
            and error.args[0] == 1062
            and "uq_job_runs_active_key" in str(error).lower()
        )

    def get(self, run_id: str) -> JobRun | None:
        with self._connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute("SELECT * FROM job_runs WHERE run_id = %s", (run_id,))
                    row = cursor.fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        if row is None:
            return None
        return self._row_to_run(row)

    def find_active_by_key(self, idempotency_key: str) -> JobRun | None:
        with self._connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM job_runs WHERE idempotency_key = %s"
                        " AND status IN ('pending', 'running', 'retry_wait') FOR UPDATE",
                        (idempotency_key,),
                    )
                    row = cursor.fetchone()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        if row is None:
            return None
        return self._row_to_run(row)

    def list_recent(self, limit: int = 20, job_type: str | None = None) -> list[JobRun]:
        with self._connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    if job_type is not None:
                        cursor.execute(
                            "SELECT * FROM job_runs WHERE job_type = %s"
                            " ORDER BY created_at DESC, run_id DESC LIMIT %s",
                            (job_type, limit),
                        )
                    else:
                        cursor.execute(
                            "SELECT * FROM job_runs"
                            " ORDER BY created_at DESC, run_id DESC LIMIT %s",
                            (limit,),
                        )
                    rows = cursor.fetchall()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return [self._row_to_run(row) for row in rows]

    def list_active(self, job_type: str) -> list[JobRun]:
        with self._connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT * FROM job_runs WHERE job_type = %s"
                        " AND status IN ('pending', 'running', 'retry_wait')"
                        " ORDER BY created_at ASC, run_id ASC",
                        (job_type,),
                    )
                    rows = cursor.fetchall()
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return [self._row_to_run(row) for row in rows]

    def update_summary(
        self,
        run_id: str,
        expected_status: JobStatus,
        summary: dict[str, object],
    ) -> bool:
        now = datetime.now(UTC)
        summary_json = json.dumps(summary, ensure_ascii=False)
        with self._connection() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE job_runs SET summary = %s, updated_at = %s"
                        " WHERE run_id = %s AND status = %s",
                        (summary_json, now, run_id, expected_status),
                    )
                    affected = cursor.rowcount
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return affected == 1

    def transition(
        self,
        run_id: str,
        current_status: JobStatus,
        target_status: JobStatus,
        *,
        output_version: str | None = None,
        summary: dict[str, object] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        now = datetime.now(UTC)
        summary_json = json.dumps(summary, ensure_ascii=False) if summary is not None else None
        with self._connection() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """UPDATE job_runs
                           SET status = %s,
                               started_at = CASE
                                   WHEN %s = 'running' THEN COALESCE(started_at, %s)
                                   ELSE started_at
                               END,
                               finished_at = CASE
                                   WHEN %s IN ('succeeded', 'failed', 'skipped',
                                               'needs_attention') THEN %s
                                   ELSE finished_at
                               END,
                               attempt = CASE
                                   WHEN %s = 'retry_wait' AND %s = 'running' THEN attempt + 1
                                   ELSE attempt
                               END,
                               output_version = COALESCE(%s, output_version),
                               summary = COALESCE(%s, summary),
                               error_code = COALESCE(%s, error_code),
                               error_message = COALESCE(%s, error_message),
                               updated_at = %s
                           WHERE run_id = %s AND status = %s""",
                        (
                            target_status,
                            target_status,
                            now,
                            target_status,
                            now,
                            current_status,
                            target_status,
                            output_version,
                            summary_json,
                            error_code,
                            error_message,
                            now,
                            run_id,
                            current_status,
                        ),
                    )
                    affected = cursor.rowcount
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return affected == 1
