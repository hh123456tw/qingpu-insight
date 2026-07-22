from __future__ import annotations

import inspect
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pandas as pd
import pymysql
import pytest
from test_publishing import FakeConnection, FakeCursor, FakeDatabase

from qingpu_insight.job_executor import LocalJobExecutor
from qingpu_insight.listing_sources import CaptureBatch, CapturedPage
from qingpu_insight.listing_update import AtomicParquetArtifactWriter
from qingpu_insight.publishing import DatasetVersion, MySQLVersionPublisher
from qingpu_insight.web import create_app


def test_production_composer_exposes_only_external_boundary_seams() -> None:
    import qingpu_insight.cli as cli
    import qingpu_insight.web as web

    cli_parameters = inspect.signature(cli._create_listing_update_service).parameters
    web_parameters = inspect.signature(web._create_production_admin_services).parameters
    runner_parameters = inspect.signature(cli.M3ListingPreparationRunner).parameters
    assert "connection_factory" in cli_parameters
    assert "source_factory" in cli_parameters
    assert "preparation_runner_factory" not in cli_parameters
    assert "connection_factory" in web_parameters
    assert "source_factory" in web_parameters
    assert "preparation_runner_factory" not in web_parameters
    assert "executor_factory" in web_parameters
    assert runner_parameters["source_factory"].kind is inspect.Parameter.KEYWORD_ONLY


class ProductionFakeDatabase(FakeDatabase):
    """Transactional external MySQL boundary for production composition."""

    def __init__(self) -> None:
        super().__init__()
        self.job_runs: dict[str, dict[str, object]] = {}
        self.job_sequence = 0
        self.job_transitions: list[tuple[str, str]] = []
        self.terminal = Event()

    def snapshot(self) -> dict[str, object]:
        snapshot = super().snapshot()
        snapshot["job_runs"] = {
            key: dict(value) for key, value in self.job_runs.items()
        }
        snapshot["job_sequence"] = self.job_sequence
        snapshot["job_transitions"] = list(self.job_transitions)
        return snapshot

    def restore(self, snapshot: dict[str, object]) -> None:
        super().restore(snapshot)
        self.job_runs = {
            key: dict(value)
            for key, value in snapshot.get("job_runs", {}).items()
        }
        self.job_sequence = int(snapshot.get("job_sequence", 0))
        self.job_transitions = list(snapshot.get("job_transitions", []))


class ProductionCursor(FakeCursor):
    def execute(self, sql: str, params=None) -> int:
        normalized = " ".join(sql.lower().split())
        database = self.connection.database
        if normalized.startswith("show columns from `listing_"):
            self.connection.kinds.discard("publisher")
            self.connection.kinds.add("preparation")
            default_unknown = {
                "acquisition_representation",
                "acquisition_schema_version",
                "location_method",
                "location_confidence",
                "location_reason",
            }
            self.result = [
                {
                    "Field": column,
                    "Null": "NO",
                    "Default": "unknown" if column in default_unknown else None,
                }
                for column in (
                    "asking_unit_price_low_twd_per_ping",
                    "asking_unit_price_high_twd_per_ping",
                    "building_area_min_ping",
                    "building_area_max_ping",
                    "acquisition_representation",
                    "acquisition_schema_version",
                    "structured_address",
                    "address_source_url",
                    "address_observed_at",
                    "location_method",
                    "location_confidence",
                    "location_reason",
                    "geocoded_at",
                    "geocoder_version",
                )
            ]
            self.rowcount = len(self.result)
            return self.rowcount
        if normalized.startswith("select * from listing_current"):
            self.connection.kinds.discard("publisher")
            self.connection.kinds.add("preparation")
            listing_type = params["lt"] if params else None
            self.result = [
                {
                    **row,
                    "snapshot_at": pd.Timestamp(row["snapshot_at"]),
                }
                for row in database.runtime_current.values()
                if listing_type is None or row["listing_type"] == listing_type
            ]
            self.description = (
                [(column,) for column in self.result[0]] if self.result else []
            )
            self.rowcount = len(self.result)
            return self.rowcount
        if normalized.startswith((
            "update `listing_snapshots` set `",
            "update `listing_current` set `",
        )):
            self.connection.kinds.discard("publisher")
            self.connection.kinds.add("preparation")
            self.result = []
            self.rowcount = 0
            return 0
        if "job_runs" not in normalized:
            self.connection.kinds.add("publisher")
            if (
                normalized.startswith("insert into dataset_version_rows")
                and database.fail_on == "dataset_version_rows"
            ):
                raise RuntimeError("injected dataset_version_rows failure")
            return super().execute(sql, params)

        self.connection.kinds.add("job")
        self.result = []
        self.rowcount = 0
        if normalized.startswith(("create table", "alter table")):
            return 0
        if "information_schema" in normalized:
            self.result = [{"present": 1}]
            return 0
        if normalized.startswith("insert into job_runs"):
            (
                run_id, job_type, trigger, idempotency_key, status, attempt,
                input_version, output_version, summary, error_code, error_message,
                started_at, finished_at,
            ) = params
            duplicate = next(
                (
                    row for row in database.job_runs.values()
                    if row["idempotency_key"] == idempotency_key
                    and row["status"] in {"pending", "running", "retry_wait"}
                ),
                None,
            )
            if duplicate is not None:
                raise pymysql.err.IntegrityError(
                    1062, "Duplicate entry for key 'uq_job_runs_active_key'"
                )
            database.job_sequence += 1
            database.job_runs[run_id] = {
                "run_id": run_id,
                "job_type": job_type,
                "trigger": trigger,
                "idempotency_key": idempotency_key,
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "attempt": attempt,
                "input_version": input_version,
                "output_version": output_version,
                "summary": summary,
                "error_code": error_code,
                "error_message": error_message,
                "created_at": database.job_sequence,
            }
            self.rowcount = 1
            return 1
        if normalized.startswith("select * from job_runs where run_id"):
            row = database.job_runs.get(params[0])
            self.result = [dict(row)] if row is not None else []
            return len(self.result)
        if normalized.startswith("select * from job_runs where idempotency_key"):
            self.result = [
                dict(row)
                for row in database.job_runs.values()
                if row["idempotency_key"] == params[0]
                and row["status"] in {"pending", "running", "retry_wait"}
            ][:1]
            return len(self.result)
        if normalized.startswith("select * from job_runs order by"):
            limit = params[0]
            ordered = sorted(
                database.job_runs.values(),
                key=lambda row: (row["created_at"], row["run_id"]),
                reverse=True,
            )
            self.result = [dict(row) for row in ordered[:limit]]
            return len(self.result)
        if normalized.startswith("update job_runs set status"):
            target_status = params[0]
            current_status = params[13]
            run_id = params[12]
            row = database.job_runs.get(run_id)
            if row is None or row["status"] != current_status:
                return 0
            row["status"] = target_status
            if target_status == "running" and row["started_at"] is None:
                row["started_at"] = params[2]
            if target_status in {"succeeded", "failed", "skipped", "needs_attention"}:
                row["finished_at"] = params[4]
            if current_status == "retry_wait" and target_status == "running":
                row["attempt"] = int(row["attempt"]) + 1
            if params[7] is not None:
                row["output_version"] = params[7]
            if params[8] is not None:
                row["summary"] = params[8]
            if params[9] is not None:
                row["error_code"] = params[9]
            if params[10] is not None:
                row["error_message"] = params[10]
            database.job_transitions.append((current_status, target_status))
            if target_status in {"succeeded", "failed", "skipped", "needs_attention"}:
                database.terminal.set()
            self.rowcount = 1
            return 1
        raise AssertionError(f"unhandled job SQL: {normalized}")


class ProductionConnection(FakeConnection):
    def __init__(self, database: ProductionFakeDatabase) -> None:
        self.kinds: set[str] = set()
        super().__init__(database)

    def cursor(self, cursor_class=None) -> ProductionCursor:
        self.cursor_classes.append(cursor_class)
        return ProductionCursor(self)


class ProductionConnectionFactory:
    def __init__(self, database: ProductionFakeDatabase) -> None:
        self.database = database
        self.connections: list[ProductionConnection] = []

    def __call__(self) -> ProductionConnection:
        connection = ProductionConnection(self.database)
        self.connections.append(connection)
        return connection


def _listing_payload(listing_type: str, version: str) -> dict[str, object]:
    return {
        "source": "591",
        "listing_type": listing_type,
        "source_listing_id": f"{listing_type}-{version}",
        "snapshot_at": pd.Timestamp("2026-07-22 10:00:00", tz="UTC"),
        "source_url": f"https://{listing_type}.591.com.tw/{version}",
        "title": f"{listing_type} listing {version}",
        "raw_hash": (listing_type[0] or "a") * 64,
        "active": True,
        "consecutive_absences": 0,
    }


class ReleaseListingSource:
    def __init__(
        self,
        owner: ReleaseSourceFactory,
    ) -> None:
        self.owner = owner

    def capture(self, listing_type: str, max_pages: int) -> CaptureBatch:
        assert max_pages == 1
        self.owner.calls.append(listing_type)
        if self.owner.started is not None:
            self.owner.started.set()
        if self.owner.release is not None:
            assert self.owner.release.wait(5), "release gate did not open capture"
        if self.owner.fail:
            raise RuntimeError(
                "mysql://admin:password@localhost/db <html> 0912-345-678"
            )
        fixture_name = {
            "sale": "591_sale_page.html",
            "newhouse": "591_newhouse_page.html",
            "rental": "591_rental_page.html",
        }[listing_type]
        html = (
            Path(__file__).parent / "fixtures" / "listings" / fixture_name
        ).read_text(encoding="utf-8")
        batch = CaptureBatch(
            batch_id=f"batch-{listing_type}-{self.owner.version}",
            source="591",
            listing_type=listing_type,
            started_at=datetime(2026, 7, 22, 10, 0, tzinfo=UTC),
            pages=[
                CapturedPage(
                    page_number=1,
                    url=f"https://{listing_type}.591.com.tw/",
                    html=html,
                    accepted_count=2,
                    representation="legacy_dom",
                    schema_version="fixture-v1",
                )
            ],
            reached_terminal_page=True,
        )
        self.owner.batches.append(batch)
        return batch


class ReleaseSourceFactory:
    def __init__(self, version: str, **runner_kwargs) -> None:
        self.version = version
        self.started = runner_kwargs.get("started")
        self.release = runner_kwargs.get("release")
        self.fail = bool(runner_kwargs.get("fail", False))
        self.calls: list[str] = []
        self.batches: list[CaptureBatch] = []
        self.factory_roots: list[Path] = []
        self.configs: list[object] = []

    def __call__(self, root: Path, config) -> ReleaseListingSource:
        self.factory_roots.append(root)
        self.configs.append(config)
        return ReleaseListingSource(self)


def _prepare_release_root(root: Path) -> None:
    doorplates = root / "data" / "raw" / "doorplates.csv"
    doorplates.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).parent / "fixtures" / "doorplates.csv", doorplates)


class EmptyMarketSource:
    def load(self, filters) -> pd.DataFrame:
        del filters
        return pd.DataFrame()


def _seed_v1(
    tmp_path: Path, publisher: MySQLVersionPublisher
) -> DatasetVersion:
    batch = CaptureBatch(
        batch_id="batch-sale-v1",
        source="591",
        listing_type="sale",
        started_at=datetime(2026, 7, 22, 9, 0, tzinfo=UTC),
        reached_terminal_page=True,
    )
    v1_payload = _listing_payload("sale", "v1")
    v1_payload["source_listing_id"] = "S-1001"
    rows = pd.DataFrame([v1_payload])
    events = pd.DataFrame(
        [{
            "event_key": "event-sale-v1",
            "source": "591",
            "listing_type": "sale",
            "source_listing_id": "sale-v1",
            "event_type": "listed",
            "event_data": "{}",
            "occurred_at": batch.started_at,
        }]
    )
    metadata = AtomicParquetArtifactWriter().write(rows, tmp_path / "v1.parquet")
    version = DatasetVersion(
        version="v1",
        run_id="00000000-0000-4000-8000-000000000001",
        status="ready",
        summary={"rows": 1, "events": 1},
        artifact_path=str(metadata.path),
        artifact_hash=metadata.artifact_hash,
        artifact_row_count=metadata.row_count,
        rows_hash=metadata.rows_hash,
    )
    publisher.stage(version, [batch], rows, events)
    return publisher.publish(version.version, expected_current_version=None)


def _post(client, payload: dict[str, object]):
    return client.post(
        "/api/admin/listing-updates",
        json=payload,
        headers={"X-Qingpu-CSRF": "release-token"},
    )


def test_m3_runner_resolves_default_source_at_prepare_time(
    tmp_path: Path, monkeypatch,
) -> None:
    import qingpu_insight.cli as cli
    import qingpu_insight.web as web

    _prepare_release_root(tmp_path)
    database = ProductionFakeDatabase()
    factory = ProductionConnectionFactory(database)
    services = web._create_production_admin_services(
        tmp_path,
        connection_factory=factory,
        executor_factory=LocalJobExecutor,
    )
    source_factory = ReleaseSourceFactory("runtime-default")
    monkeypatch.setattr(cli, "create_listing_source", source_factory)

    runner = services.listing_update_service._preparation_runner
    assert isinstance(runner, cli.M3ListingPreparationRunner)
    prepared = runner.prepare("sale", 1)

    assert len(prepared.rows) == 2
    assert source_factory.calls == ["sale"]
    preparation_connections = [
        connection for connection in factory.connections
        if "preparation" in connection.kinds
    ]
    assert len(preparation_connections) == 1
    assert preparation_connections[0].closed is True


@pytest.mark.parametrize(
    ("boundary", "expected_code"),
    [
        ("preparation", "preparation_failed"),
        ("artifact", "artifact_failed"),
        ("stage", "stage_failed"),
        ("runtime_publish", "publish_failed"),
        ("pointer_update", "publish_failed"),
    ],
)
def test_m42_atomic_release_gate(
    tmp_path: Path, monkeypatch, boundary: str, expected_code: str,
) -> None:
    import qingpu_insight.cli as cli
    import qingpu_insight.web as web

    _prepare_release_root(tmp_path)
    database = ProductionFakeDatabase()
    factory = ProductionConnectionFactory(database)
    publisher = MySQLVersionPublisher(factory, dataset_key="listings")
    v1 = _seed_v1(tmp_path, publisher)
    assert v1.version == "v1"
    assert database.pointer["listings"] == "v1"

    started = Event()
    release = Event()
    source_v2 = ReleaseSourceFactory(
        "v2", started=started, release=release
    )
    services_v2 = web._create_production_admin_services(
        tmp_path,
        connection_factory=factory,
        source_factory=source_v2,
        executor_factory=LocalJobExecutor,
    )
    assert isinstance(
        services_v2.listing_update_service._preparation_runner,
        cli.M3ListingPreparationRunner,
    )
    assert source_v2.factory_roots == []
    app_v2 = create_app(
        data_source=EmptyMarketSource(),
        admin_services=services_v2,
    )
    try:
        with app_v2.test_client() as client:
            with client.session_transaction() as session:
                session["_csrf_token"] = "release-token"
            first = _post(
                client,
                {"types": ["sale", "newhouse", "rental"], "max_pages": 1},
            )
            assert first.status_code == 202
            assert started.wait(5), "v2 preparation did not start"
            duplicate = _post(
                client,
                {"types": ["sale", "newhouse", "rental"], "max_pages": 1},
            )
            assert duplicate.status_code == 202
            assert duplicate.json["run_id"] == first.json["run_id"]
            assert duplicate.json["created"] is False
            assert source_v2.calls == ["sale"]
            release.set()
            assert database.terminal.wait(5), "v2 did not finish"
            detail = client.get(f"/api/jobs/{first.json['run_id']}")
            assert detail.status_code == 200
            assert detail.json["status"] == "succeeded"
            assert detail.json["summary"]["rows"] == 6
            v2_version = detail.json["output_version"]
    finally:
        release.set()
        app_v2.extensions["qingpu_admin_shutdown"]()

    assert database.job_transitions.count(("pending", "running")) == 1
    assert source_v2.calls == ["sale", "newhouse", "rental"]
    assert source_v2.factory_roots == [tmp_path] * 3
    assert database.pointer["listings"] == v2_version
    assert publisher.current().version == v2_version
    assert len(database.runtime_current) == 6
    assert len(database.runtime_events) == 6
    assert database.versions[("listings", v2_version)]["artifact_row_count"] == 6

    v2 = publisher.current()
    assert v2 is not None
    batches = source_v2.batches
    rows = pd.read_parquet(v2.artifact_path)
    events = pd.DataFrame(
        [
            json.loads(payload)
            for (dataset_key, version, _), payload in database.events.items()
            if (dataset_key, version) == ("listings", v2.version)
        ]
    )
    event_keys_before = set(database.runtime_events)
    publisher.stage(v2, batches, rows, events)
    publisher.publish(v2.version, expected_current_version=v2.version)
    assert set(database.runtime_events) == event_keys_before

    runtime_before = database.snapshot()
    database.terminal.clear()
    source_v3 = ReleaseSourceFactory(
        f"v3-{boundary}", fail=boundary == "preparation"
    )
    if boundary == "artifact":
        monkeypatch.setattr(
            AtomicParquetArtifactWriter,
            "write",
            lambda self, rows, path: (_ for _ in ()).throw(
                OSError("mysql://admin:password@localhost/db artifact failed")
            ),
        )
    elif boundary == "stage":
        database.fail_on = "dataset_version_rows"
    elif boundary == "runtime_publish":
        database.fail_on = "listing_current"
    elif boundary == "pointer_update":
        database.fail_on = "published_datasets"
    services_v3 = web._create_production_admin_services(
        tmp_path,
        connection_factory=factory,
        source_factory=source_v3,
        executor_factory=LocalJobExecutor,
    )
    assert isinstance(
        services_v3.listing_update_service._preparation_runner,
        cli.M3ListingPreparationRunner,
    )
    app_v3 = create_app(
        data_source=EmptyMarketSource(),
        admin_services=services_v3,
    )
    try:
        with app_v3.test_client() as client:
            with client.session_transaction() as session:
                session["_csrf_token"] = "release-token"
            response = _post(
                client,
                {
                    "types": ["sale", "newhouse", "rental"],
                    "max_pages": 1,
                    "trigger": "scheduled",
                },
            )
            assert response.status_code == 202
            assert database.terminal.wait(5), f"{boundary} did not terminalize"
            failed = client.get(f"/api/jobs/{response.json['run_id']}")
            assert failed.status_code == 200
            assert failed.json["status"] == "failed"
            assert failed.json["error_code"] == expected_code
            serialized = failed.get_data(as_text=True)
            assert "password" not in serialized
            assert "<html>" not in serialized
            assert "0912-345-678" not in serialized
    finally:
        app_v3.extensions["qingpu_admin_shutdown"]()
        database.fail_on = None

    assert database.pointer["listings"] == v2.version
    assert database.runtime_batches == runtime_before["runtime_batches"]
    assert database.runtime_snapshots == runtime_before["runtime_snapshots"]
    assert database.runtime_current == runtime_before["runtime_current"]
    assert database.runtime_events == runtime_before["runtime_events"]
    assert all(connection.closed for connection in factory.connections)
    job_connection_ids = {
        id(connection) for connection in factory.connections
        if "job" in connection.kinds
    }
    publisher_connection_ids = {
        id(connection) for connection in factory.connections
        if "publisher" in connection.kinds
    }
    preparation_connection_ids = {
        id(connection) for connection in factory.connections
        if "preparation" in connection.kinds
    }
    assert len(job_connection_ids) > 5
    assert len(publisher_connection_ids) > 5
    assert len(preparation_connection_ids) >= 3
    assert job_connection_ids.isdisjoint(preparation_connection_ids)
    assert publisher_connection_ids.isdisjoint(preparation_connection_ids)
