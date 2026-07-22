from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, cast

import pandas as pd
import pymysql

from qingpu_insight.listing_repository import publish_listing_payloads_in_transaction
from qingpu_insight.listing_sources import CaptureBatch

DatasetStatus = Literal["staging", "ready", "abandoned"]


class DatasetVersionMigrationConflict(RuntimeError):
    """Raised when legacy metadata cannot be made immutable without invention."""


class ImmutableDatasetVersionError(ValueError):
    """Raised when a dataset version key is already owned by another payload."""


@dataclass(frozen=True)
class DatasetVersion:
    version: str
    run_id: str
    status: DatasetStatus
    summary: dict[str, object]
    artifact_path: str
    artifact_hash: str
    artifact_row_count: int
    rows_hash: str


_CREATE_DATASET_VERSIONS_SQL = """
CREATE TABLE IF NOT EXISTS dataset_versions (
  dataset_key VARCHAR(64) NOT NULL,
  version VARCHAR(64) NOT NULL,
  run_id VARCHAR(36) NOT NULL,
  status VARCHAR(32) NOT NULL,
  summary JSON NOT NULL,
  artifact_path VARCHAR(1024) NOT NULL,
  artifact_hash CHAR(64) NOT NULL,
  artifact_row_count BIGINT UNSIGNED NOT NULL,
  rows_hash CHAR(64) NOT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  PRIMARY KEY (dataset_key, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_CREATE_VERSION_BATCHES_SQL = """
CREATE TABLE IF NOT EXISTS dataset_version_batches (
  dataset_key VARCHAR(64) NOT NULL,
  version VARCHAR(64) NOT NULL,
  batch_id VARCHAR(64) NOT NULL,
  payload JSON NOT NULL,
  PRIMARY KEY (dataset_key, version, batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_CREATE_VERSION_ROWS_SQL = """
CREATE TABLE IF NOT EXISTS dataset_version_rows (
  dataset_key VARCHAR(64) NOT NULL,
  version VARCHAR(64) NOT NULL,
  `row_number` BIGINT UNSIGNED NOT NULL,
  payload JSON NOT NULL,
  row_hash CHAR(64) NOT NULL,
  PRIMARY KEY (dataset_key, version, `row_number`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_CREATE_VERSION_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS dataset_version_events (
  dataset_key VARCHAR(64) NOT NULL,
  version VARCHAR(64) NOT NULL,
  event_key VARCHAR(64) NOT NULL,
  payload JSON NOT NULL,
  PRIMARY KEY (dataset_key, version, event_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_CREATE_PUBLISH_LOCKS_SQL = """
CREATE TABLE IF NOT EXISTS dataset_publish_locks (
  dataset_key VARCHAR(64) NOT NULL PRIMARY KEY,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_CREATE_PUBLISHED_DATASETS_SQL = """
CREATE TABLE IF NOT EXISTS published_datasets (
  dataset_key VARCHAR(64) NOT NULL PRIMARY KEY,
  version VARCHAR(64) NULL,
  published_at DATETIME(3) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_SCHEMA_STATEMENTS = (
    _CREATE_DATASET_VERSIONS_SQL,
    _CREATE_VERSION_BATCHES_SQL,
    _CREATE_VERSION_ROWS_SQL,
    _CREATE_VERSION_EVENTS_SQL,
    _CREATE_PUBLISH_LOCKS_SQL,
    _CREATE_PUBLISHED_DATASETS_SQL,
)


def _canonical_value(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value.isoformat()
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _dataframe_payloads(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {str(column): _canonical_value(value) for column, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def compute_rows_hash(rows: pd.DataFrame) -> str:
    return sha256(_canonical_json(_dataframe_payloads(rows)).encode("utf-8")).hexdigest()


def compute_artifact_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


class MySQLVersionPublisher:
    def __init__(
        self,
        connection_factory: Callable[[], pymysql.Connection] | pymysql.Connection,
        dataset_key: str = "listings",
    ) -> None:
        self._dataset_key = dataset_key
        self._caller_connection = (
            connection_factory if hasattr(connection_factory, "cursor") else None
        )
        self._connection_factory = (
            None if self._caller_connection is not None else connection_factory
        )
        # Retained only for existing direct-connection test fakes.
        self._connection = self._caller_connection
        with self._operation_connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    for statement in _SCHEMA_STATEMENTS:
                        cursor.execute(statement)
                    self._upgrade_existing_schema(cursor)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _upgrade_existing_schema(cursor) -> None:
        needs_completeness_check = False
        definitions = {
            "artifact_path": "VARCHAR(1024)",
            "artifact_hash": "CHAR(64)",
            "artifact_row_count": "BIGINT UNSIGNED",
            "rows_hash": "CHAR(64)",
        }
        for column, definition in definitions.items():
            cursor.execute(
                """SELECT 1 AS present, IS_NULLABLE
                   FROM information_schema.COLUMNS
                   WHERE TABLE_SCHEMA = DATABASE()
                     AND TABLE_NAME = %s AND COLUMN_NAME = %s
                   LIMIT 1""",
                ("dataset_versions", column),
            )
            metadata = cursor.fetchone()
            if metadata is None:
                cursor.execute(
                    f"ALTER TABLE dataset_versions ADD COLUMN `{column}` {definition} NULL"
                )
                needs_completeness_check = True
            elif str(metadata.get("IS_NULLABLE", "YES")).upper() != "NO":
                needs_completeness_check = True

        if needs_completeness_check:
            cursor.execute(
                """SELECT version FROM dataset_versions
                   WHERE artifact_path IS NULL OR artifact_path = ''
                      OR artifact_hash IS NULL OR artifact_hash = ''
                      OR artifact_row_count IS NULL
                      OR rows_hash IS NULL OR rows_hash = ''
                   LIMIT 1"""
            )
            incomplete = cursor.fetchone()
            if incomplete is not None:
                raise DatasetVersionMigrationConflict(
                    "legacy dataset version lacks immutable artifact metadata: "
                    f"{incomplete.get('version')!r}; rows were preserved"
                )
            cursor.execute(
                """ALTER TABLE dataset_versions
                   MODIFY COLUMN artifact_path VARCHAR(1024) NOT NULL,
                   MODIFY COLUMN artifact_hash CHAR(64) NOT NULL,
                   MODIFY COLUMN artifact_row_count BIGINT UNSIGNED NOT NULL,
                   MODIFY COLUMN rows_hash CHAR(64) NOT NULL"""
            )

        cursor.execute(
            """SELECT 1 AS present
               FROM information_schema.STATISTICS
               WHERE TABLE_SCHEMA = DATABASE()
                 AND TABLE_NAME = %s AND INDEX_NAME = 'PRIMARY'
               GROUP BY INDEX_NAME
               HAVING GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) = %s
               LIMIT 1""",
            ("dataset_versions", "dataset_key,version"),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                """ALTER TABLE dataset_versions DROP PRIMARY KEY,
                   ADD PRIMARY KEY (dataset_key, version)"""
            )

        cursor.execute(
            """SELECT 1 AS present, IS_NULLABLE
               FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = DATABASE()
                 AND TABLE_NAME = %s AND COLUMN_NAME = %s
               LIMIT 1""",
            ("published_datasets", "version"),
        )
        pointer_version = cursor.fetchone()
        if (
            pointer_version is not None
            and str(pointer_version.get("IS_NULLABLE", "NO")).upper() != "YES"
        ):
            cursor.execute(
                "ALTER TABLE published_datasets MODIFY COLUMN version VARCHAR(64) NULL"
            )

    @contextmanager
    def _operation_connection(self) -> Iterator[pymysql.Connection]:
        if self._caller_connection is not None:
            yield self._caller_connection
            return
        assert callable(self._connection_factory)
        connection = self._connection_factory()
        try:
            yield connection
        finally:
            connection.close()

    def stage(
        self,
        version: DatasetVersion,
        batches: Sequence[CaptureBatch],
        rows: pd.DataFrame,
        events: pd.DataFrame,
    ) -> None:
        self._validate_version_metadata(version)
        if (
            version.artifact_row_count != len(rows)
            or version.rows_hash != compute_rows_hash(rows)
        ):
            raise ValueError("dataset metadata does not describe staged rows")
        batch_payloads = [
            (batch.batch_id, _canonical_json(asdict(batch))) for batch in batches
        ]
        row_payloads = [
            (_canonical_json(payload), sha256(_canonical_json(payload).encode()).hexdigest())
            for payload in _dataframe_payloads(rows)
        ]
        event_payloads = [
            (str(payload.get("event_key")), _canonical_json(payload))
            for payload in _dataframe_payloads(events)
        ]
        if len({event_key for event_key, _ in event_payloads}) != len(event_payloads):
            raise ValueError("duplicate event_key in staged payload")

        with self._operation_connection() as connection:
            inserting_version_metadata = False
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    existing = self._select_version(cursor, version.version)
                    if existing is not None:
                        if not self._matches_existing_stage(
                            cursor, existing, version, batch_payloads, row_payloads,
                            event_payloads,
                        ):
                            raise ImmutableDatasetVersionError(
                                f"immutable conflict for {self._dataset_key}/{version.version}"
                            )
                        connection.commit()
                        return

                    inserting_version_metadata = True
                    cursor.execute(
                        """INSERT INTO dataset_versions
                           (dataset_key, version, run_id, status, summary, artifact_path,
                            artifact_hash, artifact_row_count, rows_hash)
                           VALUES (%(dataset_key)s, %(version)s, %(run_id)s, %(status)s,
                                   %(summary)s, %(artifact_path)s, %(artifact_hash)s,
                                   %(artifact_row_count)s, %(rows_hash)s)""",
                        {
                            "dataset_key": self._dataset_key,
                            "version": version.version,
                            "run_id": version.run_id,
                            "status": version.status,
                            "summary": _canonical_json(version.summary),
                            "artifact_path": version.artifact_path,
                            "artifact_hash": version.artifact_hash,
                            "artifact_row_count": version.artifact_row_count,
                            "rows_hash": version.rows_hash,
                        },
                    )
                    inserting_version_metadata = False
                    for batch_id, payload in batch_payloads:
                        cursor.execute(
                            """INSERT INTO dataset_version_batches
                               (dataset_key, version, batch_id, payload)
                               VALUES (%(dataset_key)s, %(version)s, %(batch_id)s, %(payload)s)""",
                            {
                                "dataset_key": self._dataset_key,
                                "version": version.version,
                                "batch_id": batch_id,
                                "payload": payload,
                            },
                        )
                    for row_number, (payload, row_hash) in enumerate(row_payloads):
                        cursor.execute(
                            """INSERT INTO dataset_version_rows
                               (dataset_key, version, `row_number`, payload, row_hash)
                               VALUES (%(dataset_key)s, %(version)s, %(row_number)s,
                                       %(payload)s, %(row_hash)s)""",
                            {
                                "dataset_key": self._dataset_key,
                                "version": version.version,
                                "row_number": row_number,
                                "payload": payload,
                                "row_hash": row_hash,
                            },
                        )
                    for event_key, payload in event_payloads:
                        cursor.execute(
                            """INSERT INTO dataset_version_events
                               (dataset_key, version, event_key, payload)
                               VALUES (%(dataset_key)s, %(version)s, %(event_key)s, %(payload)s)""",
                            {
                                "dataset_key": self._dataset_key,
                                "version": version.version,
                                "event_key": event_key,
                                "payload": payload,
                            },
                        )
                connection.commit()
            except pymysql.err.IntegrityError as error:
                connection.rollback()
                if not (
                    inserting_version_metadata
                    and self._is_dataset_version_primary_duplicate(error)
                ):
                    raise
                self._recover_concurrent_stage(
                    version, batch_payloads, row_payloads, event_payloads
                )
            except Exception:
                connection.rollback()
                raise

    def publish(
        self, version: str, expected_current_version: str | None
    ) -> DatasetVersion:
        with self._operation_connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    row = self._select_version(cursor, version)
                    if row is None:
                        raise ValueError(
                            f"version {version} not found for dataset {self._dataset_key}"
                        )
                    dataset_version = self._version_from_row(row)
                    if dataset_version.status != "ready":
                        raise ValueError(
                            f"version {version} status is {dataset_version.status}, "
                            "expected 'ready'"
                        )
                    artifact_rows = self._validate_artifact(dataset_version)
                    batches, staged_rows, events = self._load_and_validate_staging(
                        cursor, dataset_version
                    )
                    if compute_rows_hash(artifact_rows) != compute_rows_hash(staged_rows):
                        raise ValueError("artifact and staged canonical rows hash differ")

                    cursor.execute(
                        "INSERT IGNORE INTO dataset_publish_locks (dataset_key) VALUES (%s)",
                        (self._dataset_key,),
                    )
                    cursor.execute(
                        "SELECT dataset_key FROM dataset_publish_locks "
                        "WHERE dataset_key = %s FOR UPDATE",
                        (self._dataset_key,),
                    )
                    if cursor.fetchone() is None:
                        raise RuntimeError("dataset publish lock sentinel is missing")
                    cursor.execute(
                        "SELECT version FROM published_datasets "
                        "WHERE dataset_key = %s FOR UPDATE",
                        (self._dataset_key,),
                    )
                    pointer = cursor.fetchone()
                    current_version = pointer.get("version") if pointer else None
                    if current_version != expected_current_version:
                        raise ValueError(
                            "stale publish candidate: expected current version "
                            f"{expected_current_version!r}, found {current_version!r}"
                        )

                    publish_listing_payloads_in_transaction(
                        connection, batches, staged_rows, events
                    )
                    cursor.execute(
                        """INSERT INTO published_datasets
                           (dataset_key, version, published_at)
                           VALUES (%(dataset_key)s, %(version)s, %(published_at)s)
                           ON DUPLICATE KEY UPDATE version = VALUES(version),
                           published_at = VALUES(published_at)""",
                        {
                            "dataset_key": self._dataset_key,
                            "version": version,
                            "published_at": datetime.now(UTC),
                        },
                    )
                connection.commit()
                return dataset_version
            except Exception:
                connection.rollback()
                raise

    def current(self) -> DatasetVersion | None:
        with self._operation_connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    cursor.execute(
                        "SELECT version FROM published_datasets WHERE dataset_key = %s",
                        (self._dataset_key,),
                    )
                    pointer = cursor.fetchone()
                    if pointer is None or pointer.get("version") is None:
                        return None
                    row = self._select_version(cursor, str(pointer["version"]))
                    return self._version_from_row(row) if row is not None else None
            except Exception:
                connection.rollback()
                raise

    def abandon(self, version: str, reason: str) -> None:
        with self._operation_connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    row = self._select_version(cursor, version)
                    if row is None:
                        raise ValueError(
                            f"version {version} not found for dataset {self._dataset_key}"
                        )
                    if row["status"] == "ready":
                        raise ValueError("ready dataset version cannot be abandoned")
                    summary = self._decode_summary(row.get("summary"))
                    summary["abandoned_reason"] = reason
                    cursor.execute(
                        """UPDATE dataset_versions SET status = 'abandoned', summary = %(summary)s
                           WHERE dataset_key = %(dataset_key)s AND version = %(version)s""",
                        {
                            "summary": _canonical_json(summary),
                            "dataset_key": self._dataset_key,
                            "version": version,
                        },
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _select_version(self, cursor, version: str) -> dict[str, Any] | None:
        cursor.execute(
            "SELECT * FROM dataset_versions WHERE dataset_key = %s AND version = %s",
            (self._dataset_key, version),
        )
        return cursor.fetchone()

    @staticmethod
    def _is_dataset_version_primary_duplicate(
        error: pymysql.err.IntegrityError,
    ) -> bool:
        if not error.args or error.args[0] != 1062:
            return False
        message = str(error).lower()
        return (
            "dataset_versions.primary" in message
            or "for key 'primary'" in message
            or "for key `primary`" in message
        )

    def _recover_concurrent_stage(
        self,
        version: DatasetVersion,
        batches: list[tuple[str, str]],
        rows: list[tuple[str, str]],
        events: list[tuple[str, str]],
    ) -> None:
        with self._operation_connection() as connection:
            try:
                with connection.cursor(pymysql.cursors.DictCursor) as cursor:
                    existing = self._select_version(cursor, version.version)
                    if existing is None or not self._matches_existing_stage(
                        cursor, existing, version, batches, rows, events
                    ):
                        raise ImmutableDatasetVersionError(
                            f"immutable conflict for {self._dataset_key}/{version.version}"
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _matches_existing_stage(
        self,
        cursor,
        existing: dict[str, Any],
        version: DatasetVersion,
        batches: list[tuple[str, str]],
        rows: list[tuple[str, str]],
        events: list[tuple[str, str]],
    ) -> bool:
        metadata_matches = self._version_from_row(existing) == version
        cursor.execute(
            "SELECT batch_id, payload FROM dataset_version_batches "
            "WHERE dataset_key = %s AND version = %s ORDER BY batch_id",
            (self._dataset_key, version.version),
        )
        existing_batches = [
            (str(row["batch_id"]), self._json_text(row["payload"]))
            for row in cursor.fetchall()
        ]
        cursor.execute(
            "SELECT `row_number`, payload, row_hash FROM dataset_version_rows "
            "WHERE dataset_key = %s AND version = %s ORDER BY `row_number`",
            (self._dataset_key, version.version),
        )
        existing_rows = [
            (self._json_text(row["payload"]), str(row["row_hash"]))
            for row in cursor.fetchall()
        ]
        cursor.execute(
            "SELECT event_key, payload FROM dataset_version_events "
            "WHERE dataset_key = %s AND version = %s ORDER BY event_key",
            (self._dataset_key, version.version),
        )
        existing_events = [
            (str(row["event_key"]), self._json_text(row["payload"]))
            for row in cursor.fetchall()
        ]
        return (
            metadata_matches
            and existing_batches == sorted(batches)
            and existing_rows == rows
            and existing_events == sorted(events)
        )

    def _validate_artifact(self, version: DatasetVersion) -> pd.DataFrame:
        path = Path(version.artifact_path)
        if not path.is_file():
            raise ValueError(f"artifact does not exist: {path}")
        if compute_artifact_hash(path) != version.artifact_hash:
            raise ValueError("artifact hash does not match staged metadata")
        artifact_rows = pd.read_parquet(path)
        if len(artifact_rows) != version.artifact_row_count:
            raise ValueError("artifact row count does not match staged metadata")
        if compute_rows_hash(artifact_rows) != version.rows_hash:
            raise ValueError("artifact canonical rows hash does not match staged metadata")
        return artifact_rows

    def _load_and_validate_staging(
        self, cursor, version: DatasetVersion
    ) -> tuple[list[CaptureBatch], pd.DataFrame, pd.DataFrame]:
        cursor.execute(
            "SELECT batch_id, payload FROM dataset_version_batches "
            "WHERE dataset_key = %s AND version = %s ORDER BY batch_id",
            (self._dataset_key, version.version),
        )
        batches = [
            self._batch_from_json(row["payload"], str(row["batch_id"]))
            for row in cursor.fetchall()
        ]

        cursor.execute(
            "SELECT `row_number`, payload, row_hash FROM dataset_version_rows "
            "WHERE dataset_key = %s AND version = %s ORDER BY `row_number`",
            (self._dataset_key, version.version),
        )
        stored_rows = cursor.fetchall()
        if len(stored_rows) != version.artifact_row_count:
            raise ValueError("staged row count does not match artifact row count")
        payloads: list[dict[str, Any]] = []
        for stored in stored_rows:
            payload = self._decode_object(stored["payload"], "malformed staged row JSON")
            payload_json = _canonical_json(payload)
            payload_hash = sha256(payload_json.encode("utf-8")).hexdigest()
            if payload_hash != str(stored["row_hash"]):
                raise ValueError("staged rows hash does not match row payload")
            payloads.append(payload)
        staged_rows = pd.DataFrame(payloads)
        if sha256(_canonical_json(payloads).encode("utf-8")).hexdigest() != version.rows_hash:
            raise ValueError("staged rows hash does not match version metadata")

        cursor.execute(
            "SELECT event_key, payload FROM dataset_version_events "
            "WHERE dataset_key = %s AND version = %s ORDER BY event_key",
            (self._dataset_key, version.version),
        )
        event_payloads = [
            self._decode_object(row["payload"], "malformed staged event JSON")
            for row in cursor.fetchall()
        ]
        return batches, staged_rows, pd.DataFrame(event_payloads)

    def _batch_from_json(self, raw: Any, expected_batch_id: str) -> CaptureBatch:
        payload = self._decode_object(raw, "malformed staged batch JSON")
        if payload.get("batch_id") != expected_batch_id:
            raise ValueError("staged batch payload key mismatch")
        started_at = datetime.fromisoformat(str(payload["started_at"]))
        return CaptureBatch(
            batch_id=expected_batch_id,
            source=str(payload["source"]),
            listing_type=cast(Any, payload["listing_type"]),
            started_at=started_at,
            errors=[object() for _ in payload.get("errors", [])],
            reached_terminal_page=bool(payload.get("reached_terminal_page")),
        )

    def _validate_version_metadata(self, version: DatasetVersion) -> None:
        if version.status not in ("staging", "ready", "abandoned"):
            raise ValueError(f"unsupported dataset status {version.status!r}")
        if version.status == "ready" and (
            not version.artifact_path
            or not version.artifact_hash
            or version.artifact_row_count is None
            or not version.rows_hash
        ):
            raise ValueError("ready dataset version requires complete artifact metadata")
        if version.artifact_row_count < 0:
            raise ValueError("artifact_row_count cannot be negative")

    def _version_from_row(self, row: dict[str, Any]) -> DatasetVersion:
        return DatasetVersion(
            version=str(row["version"]),
            run_id=str(row["run_id"]),
            status=row["status"],
            summary=self._decode_summary(row.get("summary")),
            artifact_path=str(row["artifact_path"]),
            artifact_hash=str(row["artifact_hash"]),
            artifact_row_count=int(row["artifact_row_count"]),
            rows_hash=str(row["rows_hash"]),
        )

    @staticmethod
    def _decode_summary(raw: Any) -> dict[str, object]:
        if isinstance(raw, dict):
            return dict(raw)
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError("dataset summary must be a JSON object")
        return decoded

    @staticmethod
    def _decode_object(raw: Any, message: str) -> dict[str, Any]:
        try:
            decoded = raw if isinstance(raw, dict) else json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(message) from exc
        if not isinstance(decoded, dict):
            raise ValueError(message)
        return decoded

    @staticmethod
    def _json_text(raw: Any) -> str:
        return _canonical_json(raw if isinstance(raw, (dict, list)) else json.loads(raw))
