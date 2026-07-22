from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pymysql

from qingpu_insight.jobs import JobRun, JobStatus


class MySQLJobRepository:
    def __init__(self, connection: pymysql.Connection) -> None:
        self._connection = connection
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS job_runs (
                    run_id VARCHAR(36) NOT NULL PRIMARY KEY,
                    job_type VARCHAR(64) NOT NULL,
                    trigger VARCHAR(32) NOT NULL,
                    idempotency_key VARCHAR(255) NOT NULL,
                    status VARCHAR(32) NOT NULL,
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
                        DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
                    INDEX idx_idempotency_key (idempotency_key),
                    INDEX idx_status (status)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
        self._connection.commit()

    @staticmethod
    def _row_to_run(row: dict[str, Any]) -> JobRun:
        return JobRun(
            run_id=str(row["run_id"]),
            job_type=str(row["job_type"]),
            trigger=str(row["trigger"]),
            idempotency_key=str(row["idempotency_key"]),
            status=str(row["status"]),
            started_at=row["started_at"] if row.get("started_at") else None,
            finished_at=row["finished_at"] if row.get("finished_at") else None,
            attempt=int(row["attempt"]),
            input_version=row.get("input_version"),
            output_version=row.get("output_version"),
            summary=json.loads(row["summary"]) if isinstance(row.get("summary"), str) else {},
            error_code=row.get("error_code"),
            error_message=row.get("error_message"),
        )

    def create(self, run: JobRun) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO job_runs
                   (run_id, job_type, trigger, idempotency_key, status, attempt,
                    input_version, output_version, summary, error_code, error_message,
                    started_at, finished_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    run.run_id, run.job_type, run.trigger, run.idempotency_key,
                    run.status, run.attempt, run.input_version, run.output_version,
                    json.dumps(run.summary, ensure_ascii=False),
                    run.error_code, run.error_message,
                    run.started_at, run.finished_at,
                ),
            )
        self._connection.commit()

    def get(self, run_id: str) -> JobRun | None:
        with self._connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT * FROM job_runs WHERE run_id = %s", (run_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_run(row)

    def find_active_by_key(self, idempotency_key: str) -> JobRun | None:
        with self._connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                "SELECT * FROM job_runs WHERE idempotency_key = %s"
                " AND status IN ('pending', 'running', 'retry_wait') FOR UPDATE",
                (idempotency_key,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_run(row)

    def transition(
        self, run_id: str, current_status: JobStatus, target_status: JobStatus,
    ) -> bool:
        with self._connection.cursor() as cursor:
            now = datetime.now(UTC)
            if target_status in ("pending", "retry_wait", "running"):
                cursor.execute(
                    """UPDATE job_runs
                       SET status = %s, started_at = COALESCE(started_at, %s), updated_at = %s
                       WHERE run_id = %s AND status = %s""",
                    (target_status, now, now, run_id, current_status),
                )
            else:
                cursor.execute(
                    """UPDATE job_runs SET status = %s, finished_at = %s, updated_at = %s
                       WHERE run_id = %s AND status = %s""",
                    (target_status, now, now, run_id, current_status),
                )
            affected = cursor.rowcount
        self._connection.commit()
        return affected == 1
