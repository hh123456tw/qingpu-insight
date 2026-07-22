from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pymysql


@dataclass(frozen=True)
class DatasetVersion:
    version: str
    run_id: str
    status: str
    summary: dict[str, object] = field(default_factory=dict)
    artifact_hash: str | None = None
    artifact_row_count: int | None = None


class MySQLVersionPublisher:
    def __init__(self, connection: pymysql.Connection, dataset_key: str = "listings") -> None:
        self._connection = connection
        self._dataset_key = dataset_key
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS dataset_versions (
                    version VARCHAR(64) NOT NULL PRIMARY KEY,
                    dataset_key VARCHAR(64) NOT NULL,
                    run_id VARCHAR(36) NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    summary JSON NOT NULL,
                    artifact_hash VARCHAR(64) NULL,
                    artifact_row_count INT NULL,
                    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                    INDEX idx_dataset_key (dataset_key)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS published_datasets (
                    dataset_key VARCHAR(64) NOT NULL PRIMARY KEY,
                    version VARCHAR(64) NOT NULL,
                    published_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
        self._connection.commit()

    def stage(self, version: DatasetVersion) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO dataset_versions
                   (version, dataset_key, run_id, status, summary,
                    artifact_hash, artifact_row_count)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                   status = VALUES(status), summary = VALUES(summary),
                   artifact_hash = VALUES(artifact_hash),
                   artifact_row_count = VALUES(artifact_row_count)""",
                (
                    version.version, self._dataset_key, version.run_id,
                    version.status, json.dumps(version.summary, ensure_ascii=False),
                    version.artifact_hash, version.artifact_row_count,
                ),
            )
        self._connection.commit()

    def publish(self, version: str) -> None:
        with self._connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                "SELECT * FROM dataset_versions WHERE version = %s AND dataset_key = %s",
                (version, self._dataset_key),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"version {version} not found for dataset {self._dataset_key}")
            if row["status"] != "ready":
                raise ValueError(
                    f"version {version} status is {row['status']}, expected 'ready'"
                )

            cursor.execute(
                """INSERT INTO published_datasets (dataset_key, version, published_at)
                   VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE version = VALUES(version),
                   published_at = VALUES(published_at)""",
                (self._dataset_key, version, datetime.now(UTC)),
            )
        self._connection.commit()

    def current(self) -> DatasetVersion | None:
        with self._connection.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(
                """SELECT v.* FROM dataset_versions v
                   INNER JOIN published_datasets p ON v.version = p.version
                   WHERE p.dataset_key = %s""",
                (self._dataset_key,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return DatasetVersion(
            version=str(row["version"]),
            run_id=str(row["run_id"]),
            status=str(row["status"]),
            summary=json.loads(row["summary"]) if isinstance(row.get("summary"), str) else {},
            artifact_hash=row.get("artifact_hash"),
            artifact_row_count=(
                int(row["artifact_row_count"]) if row.get("artifact_row_count") else None
            ),
        )


def compute_artifact_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()
