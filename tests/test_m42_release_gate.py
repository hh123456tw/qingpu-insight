from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pandas as pd
import pytest
from test_publishing import FakeConnectionFactory, FakeDatabase

from qingpu_insight.job_executor import LocalJobExecutor
from qingpu_insight.jobs import ACTIVE_STATUSES, JobRun, JobService, JobStatus
from qingpu_insight.listing_sources import CaptureBatch
from qingpu_insight.listing_update import (
    AtomicParquetArtifactWriter,
    ListingUpdateService,
    PreparedListingType,
)
from qingpu_insight.publishing import DatasetVersion, MySQLVersionPublisher
from qingpu_insight.web import AdminServices, create_app


class ReleaseJobRepository:
    """Stateful external-boundary fake under the real Task-1 service."""

    def __init__(self) -> None:
        self.runs: dict[str, JobRun] = {}
        self.transitions: list[tuple[str, str]] = []
        self.terminal = Event()

    def create_or_get(self, run: JobRun) -> tuple[JobRun, bool]:
        active = self.find_active_by_key(run.idempotency_key)
        if active is not None:
            return active, False
        self.runs[run.run_id] = run
        return run, True

    def get(self, run_id: str) -> JobRun | None:
        return self.runs.get(run_id)

    def find_active_by_key(self, idempotency_key: str) -> JobRun | None:
        return next(
            (
                run
                for run in self.runs.values()
                if run.idempotency_key == idempotency_key
                and run.status in ACTIVE_STATUSES
            ),
            None,
        )

    def list_recent(self, limit: int = 20) -> list[JobRun]:
        return list(reversed(list(self.runs.values())))[:limit]

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
        run = self.runs.get(run_id)
        if run is None or run.status != current_status:
            return False
        self.transitions.append((current_status, target_status))
        self.runs[run_id] = replace(
            run,
            status=target_status,
            started_at=(
                datetime.now(UTC)
                if target_status == "running" and run.started_at is None
                else run.started_at
            ),
            finished_at=(
                datetime.now(UTC)
                if target_status in {"succeeded", "failed", "skipped"}
                else run.finished_at
            ),
            output_version=output_version or run.output_version,
            summary=summary if summary is not None else run.summary,
            error_code=error_code or run.error_code,
            error_message=error_message or run.error_message,
        )
        if target_status in {"succeeded", "failed", "skipped", "needs_attention"}:
            self.terminal.set()
        return True


class ReleaseLock:
    def __init__(self) -> None:
        self.owner: tuple[str, str] | None = None

    def try_acquire(self) -> bool:
        return True

    def set_owner(self, idempotency_key: str, run_id: str) -> None:
        self.owner = (idempotency_key, run_id)

    def read_owner(self) -> tuple[str, str] | None:
        return self.owner

    def release(self) -> None:
        self.owner = None


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


class ReleasePreparationRunner:
    def __init__(
        self,
        version: str,
        *,
        started: Event | None = None,
        release: Event | None = None,
        fail: bool = False,
    ) -> None:
        self.version = version
        self.started = started
        self.release = release
        self.fail = fail
        self.calls: list[str] = []
        self.prepared: list[PreparedListingType] = []

    def prepare(self, listing_type: str, max_pages: int) -> PreparedListingType:
        assert max_pages == 1
        self.calls.append(listing_type)
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            assert self.release.wait(5), "release gate did not open preparation"
        if self.fail:
            raise RuntimeError(
                "mysql://admin:password@localhost/db <html> 0912-345-678"
            )
        batch = CaptureBatch(
            batch_id=f"batch-{listing_type}-{self.version}",
            source="591",
            listing_type=listing_type,
            started_at=datetime(2026, 7, 22, 10, 0, tzinfo=UTC),
            reached_terminal_page=True,
        )
        rows = pd.DataFrame([_listing_payload(listing_type, self.version)])
        events = pd.DataFrame(
            [{
                "event_key": f"event-{listing_type}-{self.version}",
                "source": "591",
                "listing_type": listing_type,
                "source_listing_id": f"{listing_type}-{self.version}",
                "event_type": "listed",
                "event_data": "{}",
                "occurred_at": batch.started_at,
            }]
        )
        result = PreparedListingType(batch, rows, events, {"accepted": 1})
        self.prepared.append(result)
        return result


class FailingArtifactWriter:
    def write(self, rows: pd.DataFrame, path: Path):
        del rows, path
        raise OSError("mysql://admin:password@localhost/db artifact write failed")


class StageFailPublisher:
    def __init__(self, publisher: MySQLVersionPublisher) -> None:
        self.publisher = publisher

    def current(self):
        return self.publisher.current()

    def stage(self, version, batches, rows, events) -> None:
        del version, batches, rows, events
        raise RuntimeError("mysql://admin:password@localhost/db staging failed")

    def publish(self, version: str, expected_current_version: str | None):
        return self.publisher.publish(version, expected_current_version)


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
    rows = pd.DataFrame([_listing_payload("sale", "v1")])
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
    tmp_path: Path, boundary: str, expected_code: str,
) -> None:
    database = FakeDatabase()
    factory = FakeConnectionFactory(database)
    publisher = MySQLVersionPublisher(factory, dataset_key="listings")
    v1 = _seed_v1(tmp_path, publisher)
    assert v1.version == "v1"
    assert database.pointer["listings"] == "v1"

    jobs_v2_repo = ReleaseJobRepository()
    jobs_v2 = JobService(jobs_v2_repo)
    started = Event()
    release = Event()
    preparation_v2 = ReleasePreparationRunner(
        "v2", started=started, release=release
    )
    service_v2 = ListingUpdateService(
        jobs_v2,
        publisher,
        preparation_runner=preparation_v2,
        root=tmp_path,
        lock_factory=ReleaseLock,
    )
    executor_v2 = LocalJobExecutor(jobs_v2)
    app_v2 = create_app(
        data_source=EmptyMarketSource(),
        admin_services=AdminServices(jobs_v2, service_v2, executor_v2),
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
            assert preparation_v2.calls == ["sale"]
            release.set()
            assert jobs_v2_repo.terminal.wait(5), "v2 did not finish"
            detail = client.get(f"/api/jobs/{first.json['run_id']}")
            assert detail.status_code == 200
            assert detail.json["status"] == "succeeded"
            assert detail.json["summary"]["rows"] == 3
            v2_version = detail.json["output_version"]
    finally:
        release.set()
        app_v2.extensions["qingpu_admin_shutdown"]()

    assert jobs_v2_repo.transitions.count(("pending", "running")) == 1
    assert preparation_v2.calls == ["sale", "newhouse", "rental"]
    assert database.pointer["listings"] == v2_version
    assert publisher.current().version == v2_version
    assert len(database.runtime_current) == 4
    assert len(database.runtime_events) == 4
    assert database.versions[("listings", v2_version)]["artifact_row_count"] == 3

    v2 = publisher.current()
    assert v2 is not None
    batches = [item.batch for item in preparation_v2.prepared]
    rows = pd.concat([item.rows for item in preparation_v2.prepared], ignore_index=True)
    events = pd.concat(
        [item.events for item in preparation_v2.prepared], ignore_index=True
    )
    event_keys_before = set(database.runtime_events)
    publisher.stage(v2, batches, rows, events)
    publisher.publish(v2.version, expected_current_version=v2.version)
    assert set(database.runtime_events) == event_keys_before

    runtime_before = database.snapshot()
    jobs_v3_repo = ReleaseJobRepository()
    jobs_v3 = JobService(jobs_v3_repo)
    preparation_v3 = ReleasePreparationRunner(
        f"v3-{boundary}", fail=boundary == "preparation"
    )
    candidate_publisher = (
        StageFailPublisher(publisher) if boundary == "stage" else publisher
    )
    if boundary == "runtime_publish":
        database.fail_on = "listing_current"
    elif boundary == "pointer_update":
        database.fail_on = "published_datasets"
    service_v3 = ListingUpdateService(
        jobs_v3,
        candidate_publisher,
        preparation_runner=preparation_v3,
        root=tmp_path,
        lock_factory=ReleaseLock,
        artifact_writer=(
            FailingArtifactWriter()
            if boundary == "artifact"
            else AtomicParquetArtifactWriter()
        ),
    )
    executor_v3 = LocalJobExecutor(jobs_v3)
    app_v3 = create_app(
        data_source=EmptyMarketSource(),
        admin_services=AdminServices(jobs_v3, service_v3, executor_v3),
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
                    "trigger": f"release-{boundary}",
                },
            )
            assert response.status_code == 202
            assert jobs_v3_repo.terminal.wait(5), f"{boundary} did not terminalize"
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
    assert len({id(connection) for connection in factory.connections}) > 5
