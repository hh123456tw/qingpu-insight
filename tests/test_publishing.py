from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pymysql
import pytest

from qingpu_insight import publishing
from qingpu_insight.listing_sources import CaptureBatch
from qingpu_insight.publishing import (
    DatasetVersion,
    ImmutableDatasetVersionError,
    MySQLVersionPublisher,
    compute_artifact_hash,
)


class FakeDatabase:
    def __init__(self) -> None:
        self.versions: dict[tuple[str, str], dict[str, Any]] = {}
        self.batches: dict[tuple[str, str, str], str] = {}
        self.rows: dict[tuple[str, str, int], tuple[str, str]] = {}
        self.events: dict[tuple[str, str, str], str] = {}
        self.locks: set[str] = set()
        self.pointer: dict[str, str | None] = {}
        self.runtime_batches: dict[str, dict[str, Any]] = {}
        self.runtime_snapshots: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.runtime_current: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.runtime_events: dict[str, dict[str, Any]] = {}
        self.fail_on: str | None = None
        self.concurrent_snapshot: dict[str, Any] | None = None
        self.concurrent_error: pymysql.err.IntegrityError | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            name: copy.deepcopy(getattr(self, name))
            for name in (
                "versions", "batches", "rows", "events", "locks", "pointer",
                "runtime_batches", "runtime_snapshots", "runtime_current",
                "runtime_events",
            )
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        for name, value in snapshot.items():
            setattr(self, name, value)


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.result: list[dict[str, Any]] = []
        self.rowcount = 0

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> int:
        normalized = " ".join(sql.lower().split())
        self.connection.executions.append((normalized, params))
        self.result = []
        self.rowcount = 0
        db = self.connection.database

        if normalized.startswith(("create table", "alter table", "drop procedure")):
            return 0
        if normalized.startswith(("delimiter", "call ", "create procedure")):
            return 0
        if "information_schema" in normalized:
            self.result = [{"present": 1, "IS_NULLABLE": "NO"}]
            return 0

        if normalized.startswith("select") and "from dataset_versions" in normalized:
            key = (params[0], params[1])
            row = db.versions.get(key)
            self.result = [copy.deepcopy(row)] if row else []
        elif normalized.startswith("insert into dataset_versions"):
            key = (params["dataset_key"], params["version"])
            if key in db.versions:
                raise AssertionError("production attempted to rewrite immutable metadata")
            db.versions[key] = copy.deepcopy(params)
            self.rowcount = 1
        elif "from dataset_version_batches" in normalized:
            dataset_key, version = params
            self.result = [
                {"batch_id": key[2], "payload": payload}
                for key, payload in sorted(db.batches.items())
                if key[:2] == (dataset_key, version)
            ]
        elif normalized.startswith("insert into dataset_version_batches"):
            key = (params["dataset_key"], params["version"], params["batch_id"])
            db.batches[key] = params["payload"]
            self.rowcount = 1
        elif "from dataset_version_rows" in normalized:
            dataset_key, version = params
            self.result = [
                {"row_number": key[2], "payload": payload, "row_hash": row_hash}
                for key, (payload, row_hash) in sorted(db.rows.items())
                if key[:2] == (dataset_key, version)
            ]
        elif normalized.startswith("insert into dataset_version_rows"):
            key = (params["dataset_key"], params["version"], params["row_number"])
            db.rows[key] = (params["payload"], params["row_hash"])
            self.rowcount = 1
        elif "from dataset_version_events" in normalized:
            dataset_key, version = params
            self.result = [
                {"event_key": key[2], "payload": payload}
                for key, payload in sorted(db.events.items())
                if key[:2] == (dataset_key, version)
            ]
        elif normalized.startswith("insert into dataset_version_events"):
            key = (params["dataset_key"], params["version"], params["event_key"])
            db.events[key] = params["payload"]
            self.rowcount = 1
        elif normalized.startswith("insert ignore into dataset_publish_locks"):
            db.locks.add(params[0])
        elif "from dataset_publish_locks" in normalized:
            self.result = [{"dataset_key": params[0]}] if params[0] in db.locks else []
        elif normalized.startswith("select") and "from published_datasets" in normalized:
            version = db.pointer.get(params[0])
            self.result = [{"version": version}] if params[0] in db.pointer else []
        elif normalized.startswith("insert into published_datasets"):
            self._maybe_fail("published_datasets")
            db.pointer[params["dataset_key"]] = params["version"]
            self.rowcount = 1
        elif normalized.startswith("update dataset_versions"):
            key = (params["dataset_key"], params["version"])
            db.versions[key]["status"] = "abandoned"
            db.versions[key]["summary"] = params["summary"]
            self.rowcount = 1
        elif normalized.startswith("insert ignore into listing_batches"):
            self._maybe_fail("listing_batches")
            db.runtime_batches.setdefault(params["batch_id"], copy.deepcopy(params))
        elif normalized.startswith("insert ignore into listing_snapshots"):
            self._maybe_fail("listing_snapshots")
            key = (
                params["batch_id"], params["source"], params["listing_type"],
                params["source_listing_id"],
            )
            db.runtime_snapshots.setdefault(key, copy.deepcopy(params))
        elif normalized.startswith("insert into listing_current"):
            self._maybe_fail("listing_current")
            key = (params["source"], params["listing_type"], params["source_listing_id"])
            db.runtime_current[key] = copy.deepcopy(params)
        elif normalized.startswith("insert ignore into listing_events"):
            self._maybe_fail("listing_events")
            db.runtime_events.setdefault(params["event_key"], copy.deepcopy(params))
        else:
            raise AssertionError(f"unhandled SQL: {normalized}")
        return self.rowcount

    def _maybe_fail(self, table: str) -> None:
        if self.connection.database.fail_on == table:
            raise RuntimeError(f"injected {table} failure")

    def fetchone(self) -> dict[str, Any] | None:
        return self.result[0] if self.result else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self.result


class FakeConnection:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database
        self.executions: list[tuple[str, Any]] = []
        self.cursor_classes: list[object] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self._before = database.snapshot()

    def cursor(self, cursor_class: object = None) -> FakeCursor:
        self.cursor_classes.append(cursor_class)
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1
        self._before = self.database.snapshot()

    def rollback(self) -> None:
        self.rollbacks += 1
        self.database.restore(self._before)

    def close(self) -> None:
        self.closed = True


class ConcurrentStageCursor(FakeCursor):
    def execute(self, sql: str, params: Any = None) -> int:
        normalized = " ".join(sql.lower().split())
        database = self.connection.database
        if (
            normalized.startswith("insert into dataset_versions")
            and database.concurrent_snapshot is not None
        ):
            winner = database.concurrent_snapshot
            error = database.concurrent_error
            database.concurrent_snapshot = None
            database.restore(winner)
            self.connection.preserve_concurrent_commit = True
            assert error is not None
            raise error
        return super().execute(sql, params)


class ConcurrentStageConnection(FakeConnection):
    def __init__(self, database: FakeDatabase) -> None:
        super().__init__(database)
        self.preserve_concurrent_commit = False

    def cursor(self, cursor_class: object = None) -> FakeCursor:
        self.cursor_classes.append(cursor_class)
        return ConcurrentStageCursor(self)

    def rollback(self) -> None:
        self.rollbacks += 1
        if not self.preserve_concurrent_commit:
            self.database.restore(self._before)
        self.preserve_concurrent_commit = False


class FakeConnectionFactory:
    def __init__(
        self,
        database: FakeDatabase,
        connection_type: type[FakeConnection] = FakeConnection,
    ) -> None:
        self.database = database
        self.connection_type = connection_type
        self.connections: list[FakeConnection] = []

    def __call__(self) -> FakeConnection:
        connection = self.connection_type(self.database)
        self.connections.append(connection)
        return connection


@pytest.fixture
def listing_rows() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "listing_type": "sale",
            "source_listing_id": "sale-1",
            "snapshot_at": pd.Timestamp("2026-07-22 10:00:00"),
            "source_url": "https://sale.591.com.tw/home/1",
            "title": "青埔住宅",
            "asking_price_twd": 18_000_000,
            "building_area_ping": 30.5,
            "raw_hash": "a" * 64,
            "active": True,
            "consecutive_absences": 0,
            "last_seen_batch_id": "batch-1",
        }
    ])


@pytest.fixture
def listing_events() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "event_key": "event-1",
            "source": "591",
            "listing_type": "sale",
            "source_listing_id": "sale-1",
            "event_type": "new_listing",
            "event_data": '{"price":18000000}',
            "occurred_at": pd.Timestamp("2026-07-22 10:00:00"),
        }
    ])


@pytest.fixture
def batch() -> CaptureBatch:
    return CaptureBatch(
        batch_id="batch-1",
        source="591",
        listing_type="sale",
        started_at=datetime(2026, 7, 22, 10, 0),
        reached_terminal_page=True,
    )


def make_version(
    path: Path,
    rows: pd.DataFrame,
    version: str = "v1",
    run_id: str = "run-1",
) -> DatasetVersion:
    rows.to_parquet(path, index=False)
    return DatasetVersion(
        version=version,
        run_id=run_id,
        status="ready",
        summary={"rows": len(rows)},
        artifact_path=str(path),
        artifact_hash=compute_artifact_hash(path),
        artifact_row_count=len(rows),
        rows_hash=publishing.compute_rows_hash(rows),
    )


def make_publisher() -> tuple[FakeDatabase, FakeConnectionFactory, MySQLVersionPublisher]:
    database = FakeDatabase()
    factory = FakeConnectionFactory(database)
    publisher = MySQLVersionPublisher(factory, dataset_key="listings")
    return database, factory, publisher


def make_concurrent_publisher(
    winner: FakeDatabase,
    error: pymysql.err.IntegrityError,
) -> tuple[FakeDatabase, FakeConnectionFactory, MySQLVersionPublisher]:
    database = FakeDatabase()
    database.concurrent_snapshot = winner.snapshot()
    database.concurrent_error = error
    factory = FakeConnectionFactory(database, ConcurrentStageConnection)
    return database, factory, MySQLVersionPublisher(factory, dataset_key="listings")


def test_rows_hash_normalizes_only_finite_integral_floats() -> None:
    integer = pd.DataFrame({"value": [1]})
    integral_float = pd.DataFrame({"value": [1.0]})
    fractional_float = pd.DataFrame({"value": [1.5]})
    boolean = pd.DataFrame({"value": [True]})

    assert publishing.compute_rows_hash(integer) == publishing.compute_rows_hash(
        integral_float
    )
    assert publishing.compute_rows_hash(integer) != publishing.compute_rows_hash(
        fractional_float
    )
    assert publishing.compute_rows_hash(integer) != publishing.compute_rows_hash(boolean)


def test_rows_hash_preserves_null_equivalence_and_parquet_round_trip(
    tmp_path: Path,
) -> None:
    nan_frame = pd.DataFrame({"value": [float("nan")]})
    null_frame = pd.DataFrame({"value": [None]})
    rows = pd.DataFrame(
        {
            "asking_price_twd": pd.Series([18_800_000, None], dtype=object),
            "monthly_rent_twd": pd.Series([None, 25_000], dtype=object),
        }
    )
    path = tmp_path / "nullable-numeric.parquet"
    rows.to_parquet(path, index=False)

    assert publishing.compute_rows_hash(nan_frame) == publishing.compute_rows_hash(
        null_frame
    )
    assert publishing.compute_rows_hash(rows) == publishing.compute_rows_hash(
        pd.read_parquet(path)
    )


def test_stage_writes_only_immutable_version_tables(
    tmp_path: Path, batch: CaptureBatch, listing_rows: pd.DataFrame,
    listing_events: pd.DataFrame,
) -> None:
    database, _, publisher = make_publisher()
    version = make_version(tmp_path / "v1.parquet", listing_rows)

    publisher.stage(version, [batch], listing_rows, listing_events)

    assert ("listings", "v1") in database.versions
    assert len(database.batches) == 1
    assert len(database.rows) == 1
    assert len(database.events) == 1
    assert database.pointer == {}
    assert database.runtime_batches == {}
    assert database.runtime_snapshots == {}
    assert database.runtime_current == {}
    assert database.runtime_events == {}


def test_stage_is_idempotent_but_immutable(
    tmp_path: Path, batch: CaptureBatch, listing_rows: pd.DataFrame,
    listing_events: pd.DataFrame,
) -> None:
    _, _, publisher = make_publisher()
    version = make_version(tmp_path / "v1.parquet", listing_rows)
    publisher.stage(version, [batch], listing_rows, listing_events)
    publisher.stage(version, [batch], listing_rows, listing_events)

    with pytest.raises(ValueError, match="immutable conflict"):
        publisher.stage(
            DatasetVersion(**{**version.__dict__, "run_id": "run-other"}),
            [batch], listing_rows, listing_events,
        )
    changed = listing_rows.copy()
    changed.loc[0, "title"] = "changed"
    changed_version = DatasetVersion(
        **{**version.__dict__, "rows_hash": publishing.compute_rows_hash(changed)}
    )
    with pytest.raises(ValueError, match="immutable conflict"):
        publisher.stage(changed_version, [batch], changed, listing_events)


def test_concurrent_identical_stage_recovers_as_idempotent_retry(
    tmp_path: Path, batch: CaptureBatch, listing_rows: pd.DataFrame,
    listing_events: pd.DataFrame,
) -> None:
    version = make_version(tmp_path / "v1.parquet", listing_rows)
    winner, _, winner_publisher = make_publisher()
    winner_publisher.stage(version, [batch], listing_rows, listing_events)
    database, factory, publisher = make_concurrent_publisher(
        winner,
        pymysql.err.IntegrityError(
            1062,
            "Duplicate entry 'listings-v1' for key 'dataset_versions.PRIMARY'",
        ),
    )

    publisher.stage(version, [batch], listing_rows, listing_events)

    assert database.versions[("listings", "v1")]["run_id"] == "run-1"
    assert len(factory.connections) == 3
    assert all(connection.closed for connection in factory.connections)


def test_concurrent_conflicting_stage_raises_controlled_immutable_error(
    tmp_path: Path, batch: CaptureBatch, listing_rows: pd.DataFrame,
    listing_events: pd.DataFrame,
) -> None:
    candidate = make_version(tmp_path / "v1.parquet", listing_rows)
    winner_version = DatasetVersion(**{**candidate.__dict__, "run_id": "other-run"})
    winner, _, winner_publisher = make_publisher()
    winner_publisher.stage(winner_version, [batch], listing_rows, listing_events)
    _, _, publisher = make_concurrent_publisher(
        winner,
        pymysql.err.IntegrityError(
            1062,
            "Duplicate entry 'listings-v1' for key 'PRIMARY'",
        ),
    )

    with pytest.raises(ImmutableDatasetVersionError) as raised:
        publisher.stage(candidate, [batch], listing_rows, listing_events)

    assert "immutable conflict" in str(raised.value)


def test_concurrent_stage_does_not_swallow_unrelated_duplicate(
    tmp_path: Path, batch: CaptureBatch, listing_rows: pd.DataFrame,
    listing_events: pd.DataFrame,
) -> None:
    version = make_version(tmp_path / "v1.parquet", listing_rows)
    winner, _, winner_publisher = make_publisher()
    winner_publisher.stage(version, [batch], listing_rows, listing_events)
    unrelated = pymysql.err.IntegrityError(
        1062,
        "Duplicate entry 'run-1' for key 'dataset_versions.uq_run_id'",
    )
    _, _, publisher = make_concurrent_publisher(winner, unrelated)

    with pytest.raises(pymysql.err.IntegrityError) as raised:
        publisher.stage(version, [batch], listing_rows, listing_events)

    assert raised.value is unrelated


@pytest.mark.parametrize(
    "replacement",
    [
        {"artifact_row_count": 2},
        {"rows_hash": "f" * 64},
    ],
)
def test_stage_rejects_ready_metadata_that_does_not_describe_rows(
    tmp_path: Path, batch: CaptureBatch, listing_rows: pd.DataFrame,
    listing_events: pd.DataFrame, replacement: dict[str, object],
) -> None:
    _, _, publisher = make_publisher()
    version = make_version(tmp_path / "v1.parquet", listing_rows)
    invalid = DatasetVersion(**{**version.__dict__, **replacement})

    with pytest.raises(ValueError, match="staged rows"):
        publisher.stage(invalid, [batch], listing_rows, listing_events)


@pytest.mark.parametrize(
    ("corrupt", "message"),
    [
        ("status", "status"),
        ("missing", "artifact does not exist"),
        ("bytes", "artifact hash"),
        ("artifact_count", "artifact row count"),
        ("artifact_rows_hash", "canonical rows hash"),
        ("staged_count", "staged row count"),
        ("staged_hash", "staged rows hash"),
        ("malformed_json", "malformed staged row JSON"),
    ],
)
def test_publish_rejects_invalid_version_or_artifact(
    tmp_path: Path, batch: CaptureBatch, listing_rows: pd.DataFrame,
    listing_events: pd.DataFrame, corrupt: str, message: str,
) -> None:
    database, _, publisher = make_publisher()
    path = tmp_path / "v1.parquet"
    version = make_version(path, listing_rows)
    publisher.stage(version, [batch], listing_rows, listing_events)
    key = ("listings", "v1")

    if corrupt == "status":
        database.versions[key]["status"] = "staging"
    elif corrupt == "missing":
        path.unlink()
    elif corrupt == "bytes":
        path.write_bytes(b"not parquet")
    elif corrupt == "artifact_count":
        database.versions[key]["artifact_row_count"] = 2
    elif corrupt == "artifact_rows_hash":
        different = listing_rows.copy()
        different.loc[0, "title"] = "artifact changed"
        different.to_parquet(path, index=False)
        database.versions[key]["artifact_hash"] = compute_artifact_hash(path)
    elif corrupt == "staged_count":
        database.rows.pop(("listings", "v1", 0))
    elif corrupt == "staged_hash":
        payload, _ = database.rows[("listings", "v1", 0)]
        database.rows[("listings", "v1", 0)] = (payload, "f" * 64)
    elif corrupt == "malformed_json":
        _, row_hash = database.rows[("listings", "v1", 0)]
        database.rows[("listings", "v1", 0)] = ("{", row_hash)

    with pytest.raises(ValueError, match=message):
        publisher.publish("v1", expected_current_version=None)
    assert database.pointer == {}
    assert database.runtime_current == {}


@pytest.mark.parametrize(
    "failure_table",
    [
        "listing_batches", "listing_snapshots", "listing_current",
        "listing_events", "published_datasets",
    ],
)
def test_publish_failure_rolls_back_runtime_and_pointer(
    tmp_path: Path, batch: CaptureBatch, listing_rows: pd.DataFrame,
    listing_events: pd.DataFrame, failure_table: str,
) -> None:
    database, factory, publisher = make_publisher()
    v1 = make_version(tmp_path / "v1.parquet", listing_rows)
    publisher.stage(v1, [batch], listing_rows, listing_events)
    publisher.publish("v1", expected_current_version=None)
    committed_runtime = copy.deepcopy(database.runtime_current)

    v2_rows = listing_rows.copy()
    v2_rows.loc[0, "title"] = "v2"
    v2 = make_version(tmp_path / "v2.parquet", v2_rows, "v2", "run-2")
    publisher.stage(v2, [batch], v2_rows, listing_events)
    database.fail_on = failure_table

    with pytest.raises(RuntimeError, match="injected"):
        publisher.publish("v2", expected_current_version="v1")

    assert factory.connections[-1].rollbacks == 1
    assert database.pointer["listings"] == "v1"
    assert database.runtime_current == committed_runtime


def test_publish_rejects_stale_expected_current_version(
    tmp_path: Path, batch: CaptureBatch, listing_rows: pd.DataFrame,
    listing_events: pd.DataFrame,
) -> None:
    database, _, publisher = make_publisher()
    for name in ("v1", "v2"):
        version = make_version(tmp_path / f"{name}.parquet", listing_rows, name, f"run-{name}")
        publisher.stage(version, [batch], listing_rows, listing_events)
    publisher.publish("v1", expected_current_version=None)

    with pytest.raises(ValueError, match="stale publish candidate"):
        publisher.publish("v2", expected_current_version=None)
    assert database.pointer["listings"] == "v1"


def test_publish_applies_every_payload_then_pointer_in_one_commit(
    tmp_path: Path, batch: CaptureBatch, listing_rows: pd.DataFrame,
    listing_events: pd.DataFrame,
) -> None:
    database, factory, publisher = make_publisher()
    version = make_version(tmp_path / "v1.parquet", listing_rows)
    publisher.stage(version, [batch], listing_rows, listing_events)

    published = publisher.publish("v1", expected_current_version=None)

    connection = factory.connections[-1]
    writes = [sql for sql, _ in connection.executions if sql.startswith("insert")]
    positions = {
        table: next(i for i, sql in enumerate(writes) if table in sql)
        for table in (
            "listing_batches", "listing_snapshots", "listing_current",
            "listing_events", "published_datasets",
        )
    }
    assert positions == dict(sorted(positions.items(), key=lambda item: item[1]))
    assert connection.commits == 1
    assert database.pointer["listings"] == "v1"
    assert published == version


def test_retry_does_not_duplicate_event_keys(
    tmp_path: Path, batch: CaptureBatch, listing_rows: pd.DataFrame,
    listing_events: pd.DataFrame,
) -> None:
    database, _, publisher = make_publisher()
    version = make_version(tmp_path / "v1.parquet", listing_rows)
    publisher.stage(version, [batch], listing_rows, listing_events)
    publisher.publish("v1", expected_current_version=None)
    publisher.publish("v1", expected_current_version="v1")
    assert list(database.runtime_events) == ["event-1"]


def test_current_preserves_zero_artifact_row_count(tmp_path: Path) -> None:
    database, _, publisher = make_publisher()
    rows = pd.DataFrame({"source_listing_id": pd.Series(dtype="str")})
    version = make_version(tmp_path / "empty.parquet", rows)
    database.versions[("listings", "v0")] = {
        **version.__dict__, "dataset_key": "listings", "version": "v0",
        "summary": json.dumps(version.summary),
    }
    database.pointer["listings"] = "v0"
    current = publisher.current()
    assert current is not None
    assert current.artifact_row_count == 0


def test_factory_connections_close_on_success_and_failure(
    tmp_path: Path, batch: CaptureBatch, listing_rows: pd.DataFrame,
    listing_events: pd.DataFrame,
) -> None:
    database, factory, publisher = make_publisher()
    version = make_version(tmp_path / "v1.parquet", listing_rows)
    publisher.stage(version, [batch], listing_rows, listing_events)
    database.fail_on = "listing_current"
    with pytest.raises(RuntimeError):
        publisher.publish("v1", expected_current_version=None)
    assert all(connection.closed for connection in factory.connections)

    direct = FakeConnection(FakeDatabase())
    direct_publisher = MySQLVersionPublisher(direct)
    assert direct_publisher.current() is None
    assert not direct.closed


def test_migration_defines_scoped_immutable_staging_and_collision_guard() -> None:
    sql = (
        Path(__file__).parents[1] / "database" / "004_m4_jobs_publishing_schema.sql"
    ).read_text(encoding="utf-8")
    assert "PRIMARY KEY (dataset_key, version)" in sql
    assert "dataset_version_batches" in sql
    assert "dataset_version_rows" in sql
    assert "dataset_version_events" in sql
    assert "dataset_publish_locks" in sql
    assert "information_schema" in sql
    assert "SIGNAL SQLSTATE '45000'" in sql
    assert "DROP TABLE dataset_versions" not in sql


def test_dataset_row_number_identifier_is_quoted_for_mysql_8() -> None:
    connection = FakeConnection(FakeDatabase())
    publisher = MySQLVersionPublisher(connection)
    rows = pd.DataFrame([{"source_listing_id": "one"}])
    version = DatasetVersion(
        version="v1",
        run_id="run-1",
        status="ready",
        summary={},
        artifact_path="unused.parquet",
        artifact_hash="a" * 64,
        artifact_row_count=1,
        rows_hash=publishing.compute_rows_hash(rows),
    )
    publisher.stage(version, [], rows, pd.DataFrame())
    publisher.stage(version, [], rows, pd.DataFrame())
    statements = [sql for sql, _ in connection.executions if "row_number" in sql]
    migration = (
        Path(__file__).parents[1] / "database" / "004_m4_jobs_publishing_schema.sql"
    ).read_text(encoding="utf-8")

    assert statements
    assert all("`row_number`" in sql for sql in statements)
    assert "`row_number` BIGINT UNSIGNED NOT NULL" in migration
    assert "PRIMARY KEY (dataset_key, version, `row_number`)" in migration


def test_runtime_schema_setup_inspects_existing_dataset_version_shape() -> None:
    connection = FakeConnection(FakeDatabase())

    MySQLVersionPublisher(connection)

    assert any(
        "information_schema.columns" in sql for sql, _ in connection.executions
    )
    assert any(
        "information_schema.statistics" in sql for sql, _ in connection.executions
    )
    assert connection.cursor_classes[0] is pymysql.cursors.DictCursor
