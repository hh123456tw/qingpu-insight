from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

import pymysql

from qingpu_insight.job_repository import ConnectionFactory


@dataclass(frozen=True)
class ModelVersionRecord:
    version_id: str
    market: Literal["resale", "presale"]
    source_run_id: str
    model_name: str
    model_version: str
    artifact_path: str
    artifact_sha256: str
    metadata: dict[str, object]
    created_at: datetime


class ModelReleaseRepository(Protocol):
    def register_version(self, record: ModelVersionRecord) -> None: ...
    def current(self, market: str) -> ModelVersionRecord | None: ...
    def activate(self, market: str, version_id: str, job_run_id: str, action: str) -> None: ...
    def list_versions(self, market: str, limit: int) -> list[ModelVersionRecord]: ...


class MySQLModelReleaseRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    @contextmanager
    def _connection(self) -> Iterator[pymysql.Connection]:
        conn = self._connection_factory()
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> ModelVersionRecord:
        raw_metadata = row.get("metadata")
        if isinstance(raw_metadata, str):
            metadata = json.loads(raw_metadata)
        elif isinstance(raw_metadata, dict):
            metadata = raw_metadata
        else:
            metadata = {}
        return ModelVersionRecord(
            version_id=str(row["version_id"]),
            market=str(row["market"]),
            source_run_id=str(row["source_run_id"]),
            model_name=str(row["model_name"]),
            model_version=str(row["model_version"]),
            artifact_path=str(row["artifact_path"]),
            artifact_sha256=str(row["artifact_sha256"]),
            metadata=metadata,
            created_at=row["created_at"],
        )

    def register_version(self, record: ModelVersionRecord) -> None:
        with self._connection() as conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """INSERT INTO model_versions
                           (version_id, market, source_run_id, model_name,
                            model_version, artifact_path, artifact_sha256,
                            metadata, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            record.version_id,
                            record.market,
                            record.source_run_id,
                            record.model_name,
                            record.model_version,
                            record.artifact_path,
                            record.artifact_sha256,
                            json.dumps(record.metadata),
                            record.created_at,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def current(self, market: str) -> ModelVersionRecord | None:
        with self._connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(
                    """SELECT mv.*
                       FROM model_versions mv
                       INNER JOIN published_models pm
                          ON pm.market = mv.market
                         AND pm.version_id = mv.version_id
                       WHERE mv.market = %s""",
                    (market,),
                )
                row = cursor.fetchone()
            conn.commit()
        if row is None:
            return None
        return self._row_to_record(row)

    def activate(self, market: str, version_id: str, job_run_id: str, action: str) -> None:
        with self._connection() as conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT 1 FROM model_versions"
                        " WHERE market = %s AND version_id = %s",
                        (market, version_id),
                    )
                    if cursor.fetchone() is None:
                        raise ValueError(
                            f"Version {version_id!r} for market {market!r} not registered"
                        )
                    cursor.execute(
                        "SELECT version_id FROM published_models WHERE market = %s",
                        (market,),
                    )
                    prev_row = cursor.fetchone()
                    previous_version_id = prev_row[0] if prev_row else None
                    cursor.execute(
                        """INSERT INTO published_models
                           (market, version_id, job_run_id, action)
                           VALUES (%s, %s, %s, %s)
                           ON DUPLICATE KEY UPDATE
                               version_id = VALUES(version_id),
                               job_run_id = VALUES(job_run_id),
                               action = VALUES(action),
                               activated_at = CURRENT_TIMESTAMP(3)""",
                        (market, version_id, job_run_id, action),
                    )
                    cursor.execute(
                        """INSERT INTO model_release_events
                           (market, version_id, job_run_id, action, previous_version_id)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (market, version_id, job_run_id, action, previous_version_id),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def list_versions(self, market: str, limit: int) -> list[ModelVersionRecord]:
        with self._connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(
                    "SELECT * FROM model_versions"
                    " WHERE market = %s"
                    " ORDER BY created_at DESC, version_id DESC"
                    " LIMIT %s",
                    (market, limit),
                )
                rows = cursor.fetchall()
            conn.commit()
        return [self._row_to_record(row) for row in rows]


class InMemoryModelReleaseRepository:
    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], ModelVersionRecord] = {}
        self._current: dict[str, ModelVersionRecord] = {}

    def register_version(self, record: ModelVersionRecord) -> None:
        key = (record.market, record.version_id)
        if key not in self._versions:
            self._versions[key] = record

    def current(self, market: str) -> ModelVersionRecord | None:
        return self._current.get(market)

    def activate(self, market: str, version_id: str, job_run_id: str, action: str) -> None:
        key = (market, version_id)
        record = self._versions.get(key)
        if record is None:
            raise ValueError(
                f"Version {version_id!r} for market {market!r} not registered"
            )
        self._current[market] = record

    def list_versions(self, market: str, limit: int) -> list[ModelVersionRecord]:
        versions = [v for (m, _), v in self._versions.items() if m == market]
        versions.sort(key=lambda v: v.created_at, reverse=True)
        return versions[:limit]
