from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import qingpu_insight.listing_update as listing_update
from qingpu_insight.jobs import ACTIVE_STATUSES, JobRun, JobService, JobStatus
from qingpu_insight.listing_sources import CaptureBatch, CaptureError


class MemoryJobRepository:
    def __init__(self) -> None:
        self.runs: dict[str, JobRun] = {}
        self.create_calls = 0
        self.transitions: list[tuple[str, str]] = []

    def create_or_get(self, run: JobRun) -> tuple[JobRun, bool]:
        self.create_calls += 1
        for current in self.runs.values():
            if (
                current.idempotency_key == run.idempotency_key
                and current.status in ACTIVE_STATUSES
            ):
                return current, False
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
        return True


class FakeLock:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.owner: tuple[str, str] | None = None
        self.released = False

    def try_acquire(self) -> bool:
        return self.available

    def set_owner(self, idempotency_key: str, run_id: str) -> None:
        self.owner = (idempotency_key, run_id)

    def read_owner(self) -> tuple[str, str] | None:
        return self.owner

    def release(self) -> None:
        self.released = True


class FakePreparationRunner:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.results: dict[str, object] = {}
        self.error: Exception | None = None

    def prepare(self, listing_type: str, max_pages: int):
        self.calls.append(listing_type)
        if self.error is not None:
            raise self.error
        result = self.results.get(listing_type)
        if result is not None:
            return result
        batch = CaptureBatch(
            batch_id=f"batch-{listing_type}",
            source="591",
            listing_type=listing_type,
            started_at=datetime(2026, 7, 22, tzinfo=UTC),
            reached_terminal_page=True,
        )
        rows = pd.DataFrame(
            [
                {
                    "source": "591",
                    "listing_type": listing_type,
                    "source_listing_id": f"{listing_type}-1",
                    "snapshot_at": batch.started_at,
                }
            ]
        )
        events = pd.DataFrame(
            [
                {
                    "event_key": f"event-{listing_type}",
                    "listing_type": listing_type,
                }
            ]
        )
        return listing_update.PreparedListingType(
            batch=batch,
            rows=rows,
            events=events,
            summary={"accepted": 1},
        )


class FakePublisher:
    def __init__(self, current_version=None) -> None:
        self.pointer = current_version
        self.staged: list[tuple[object, list, pd.DataFrame, pd.DataFrame]] = []
        self.published: list[tuple[str, str | None]] = []
        self.calls: list[str] = []
        self.stage_error: Exception | None = None
        self.publish_error: Exception | None = None
        self.current_error: Exception | None = None

    def current(self):
        if self.current_error:
            raise self.current_error
        return self.pointer

    def stage(self, version, batches, rows, events) -> None:
        self.calls.append("stage")
        if self.stage_error:
            raise self.stage_error
        self.staged.append((version, list(batches), rows.copy(), events.copy()))

    def publish(self, version: str, expected_current_version: str | None):
        self.calls.append("publish")
        self.published.append((version, expected_current_version))
        if self.publish_error:
            raise self.publish_error
        self.pointer = self.staged[-1][0]
        return self.pointer


class BadArtifactWriter:
    def write(self, rows: pd.DataFrame, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        rows.to_parquet(path, index=False)
        return listing_update.ArtifactMetadata(
            path=path,
            artifact_hash="0" * 64,
            row_count=len(rows),
            rows_hash="0" * 64,
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"types": ()}, "types"),
        ({"types": ("sale", "sale")}, "duplicate"),
        ({"types": ("sale", "invalid")}, "unsupported"),
        ({"max_pages": True}, "max_pages"),
        ({"max_pages": 0}, "max_pages"),
        ({"max_pages": 101}, "max_pages"),
        ({"trigger": "  "}, "trigger"),
    ],
)
def test_request_rejects_invalid_values(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        listing_update.ListingUpdateRequest(**kwargs)


def test_missing_preparation_runner_fails_before_job_creation(tmp_path: Path) -> None:
    repository = MemoryJobRepository()
    with pytest.raises(ValueError, match="preparation_runner"):
        listing_update.ListingUpdateService(
            JobService(repository), FakePublisher(), preparation_runner=None, root=tmp_path
        )
    assert repository.create_calls == 0


def make_service(tmp_path: Path, *, lock: FakeLock | None = None):
    repository = MemoryJobRepository()
    job_service = JobService(repository)
    preparation = FakePreparationRunner()
    publisher = FakePublisher()
    owned_lock = lock or FakeLock()
    service = listing_update.ListingUpdateService(
        job_service,
        publisher,
        preparation_runner=preparation,
        root=tmp_path,
        lock_factory=lambda: owned_lock,
    )
    return service, job_service, repository, preparation, publisher, owned_lock


def start_submission(service, job_service, request):
    submission = service.submit(request)
    assert submission.created is True
    job_service.start(submission.run.run_id)
    return submission


def test_submit_preserves_created_semantics_and_duplicate_is_not_executed(
    tmp_path: Path,
) -> None:
    service, _, repository, preparation, _, lock = make_service(tmp_path)
    request = listing_update.ListingUpdateRequest(types=("sale",), max_pages=1)

    first = service.submit(request)
    second = service.submit(request)

    assert first.created is True
    assert second == listing_update.JobSubmission(run=first.run, created=False)
    assert repository.create_calls == 1
    assert preparation.calls == []
    assert lock.released is False


def test_execute_running_requires_executor_owned_running_state(tmp_path: Path) -> None:
    service, _, repository, _, _, _ = make_service(tmp_path)
    request = listing_update.ListingUpdateRequest(types=("sale",), max_pages=1)
    submission = service.submit(request)

    with pytest.raises(listing_update.ListingUpdateError, match="must already be running"):
        service.execute_running(submission.run.run_id, request)

    assert repository.transitions == []


def test_complete_three_type_pipeline_stages_and_publishes_once(tmp_path: Path) -> None:
    service, job_service, repository, preparation, publisher, lock = make_service(tmp_path)
    request = listing_update.ListingUpdateRequest(max_pages=3)
    submission = start_submission(service, job_service, request)

    result = service.execute_running(submission.run.run_id, request)

    assert preparation.calls == ["sale", "newhouse", "rental"]
    assert publisher.calls == ["stage", "publish"]
    assert len(publisher.staged) == 1
    version, batches, rows, events = publisher.staged[0]
    assert [batch.listing_type for batch in batches] == ["sale", "newhouse", "rental"]
    assert len(rows) == 3
    assert len(events) == 3
    assert Path(version.artifact_path).is_file()
    assert version.artifact_hash != "0" * 64
    assert version.artifact_row_count == 3
    assert version.rows_hash
    assert publisher.published == [(version.version, None)]
    assert result.status == "succeeded"
    assert result.output_version == version.version
    assert result.summary["rows"] == 3
    assert result.summary["events"] == 3
    assert repository.transitions == [("pending", "running"), ("running", "succeeded")]
    assert lock.released is True


@pytest.mark.parametrize("failure", ["empty", "incomplete", "capture_errors"])
def test_invalid_preparation_fails_closed(tmp_path: Path, failure: str) -> None:
    service, job_service, _, preparation, publisher, _ = make_service(tmp_path)
    request = listing_update.ListingUpdateRequest(types=("sale",), max_pages=1)
    result = preparation.prepare("sale", 1)
    if failure == "empty":
        result = replace(result, rows=pd.DataFrame())
    elif failure == "incomplete":
        result.batch.reached_terminal_page = False
    else:
        result.batch.errors.append(CaptureError(1, "blocked", "raw selenium page"))
    preparation.results["sale"] = result
    submission = start_submission(service, job_service, request)

    with pytest.raises(listing_update.ListingUpdateError):
        service.execute_running(submission.run.run_id, request)

    failed = job_service.get(submission.run.run_id)
    assert failed is not None and failed.status == "failed"
    assert failed.error_code in {"empty_prepared_rows", "capture_incomplete"}
    assert publisher.calls == []
    assert publisher.pointer is None


def test_preparation_exception_is_sanitized_in_job_and_application_error(
    tmp_path: Path,
) -> None:
    service, job_service, _, preparation, publisher, _ = make_service(tmp_path)
    preparation.error = RuntimeError(
        "mysql://admin:password@localhost/db 0912-345-678 <html>secret</html>"
    )
    request = listing_update.ListingUpdateRequest(types=("sale",), max_pages=1)
    submission = start_submission(service, job_service, request)

    with pytest.raises(listing_update.ListingUpdateError) as caught:
        service.execute_running(submission.run.run_id, request)

    failed = job_service.get(submission.run.run_id)
    assert failed is not None
    combined = f"{caught.value} {failed.error_message}"
    for secret in ("password", "0912", "<html>", "secret"):
        assert secret not in combined
    assert failed.error_code == "preparation_failed"
    assert publisher.calls == []


def test_schema_error_keeps_stable_code_and_never_stages(tmp_path: Path) -> None:
    service, job_service, _, preparation, publisher, _ = make_service(tmp_path)
    preparation.error = listing_update.ListingUpdateError(
        "schema_error", "listing schema validation failed"
    )
    request = listing_update.ListingUpdateRequest(types=("sale",), max_pages=1)
    submission = start_submission(service, job_service, request)

    with pytest.raises(listing_update.ListingUpdateError) as caught:
        service.execute_running(submission.run.run_id, request)

    failed = job_service.get(submission.run.run_id)
    assert caught.value.error_code == "schema_error"
    assert failed is not None and failed.error_code == "schema_error"
    assert publisher.calls == []


def test_current_pointer_read_failure_fails_job_and_releases_lock(tmp_path: Path) -> None:
    service, job_service, _, _, publisher, lock = make_service(tmp_path)
    publisher.current_error = RuntimeError("mysql://root:password@db/private")
    request = listing_update.ListingUpdateRequest(types=("sale",), max_pages=1)
    submission = start_submission(service, job_service, request)

    with pytest.raises(listing_update.ListingUpdateError) as caught:
        service.execute_running(submission.run.run_id, request)

    failed = job_service.get(submission.run.run_id)
    assert caught.value.error_code == "current_version_failed"
    assert failed is not None and failed.status == "failed"
    assert failed.error_code == "current_version_failed"
    assert lock.released is True


@pytest.mark.parametrize("failure", ["artifact", "stage", "publish"])
def test_required_output_failure_preserves_pointer_and_fails_job(
    tmp_path: Path, failure: str,
) -> None:
    service, job_service, _, _, publisher, _ = make_service(tmp_path)
    old = SimpleNamespace(version="old-version")
    publisher.pointer = old
    if failure == "artifact":
        service._artifact_writer = BadArtifactWriter()
    elif failure == "stage":
        publisher.stage_error = RuntimeError("mysql://root:pw@db/secret")
    else:
        publisher.publish_error = RuntimeError("phone 0912-345-678")
    request = listing_update.ListingUpdateRequest(types=("sale",), max_pages=1)
    submission = start_submission(service, job_service, request)

    with pytest.raises(listing_update.ListingUpdateError):
        service.execute_running(submission.run.run_id, request)

    failed = job_service.get(submission.run.run_id)
    assert failed is not None and failed.status == "failed"
    assert failed.error_code == f"{failure}_failed"
    assert publisher.pointer is old
    if failure == "stage":
        assert publisher.calls == ["stage"]
    elif failure == "publish":
        assert publisher.calls == ["stage", "publish"]


def test_versions_include_run_uuid_and_do_not_collide_in_same_second(tmp_path: Path) -> None:
    service, job_service, _, _, publisher, _ = make_service(tmp_path)
    request = listing_update.ListingUpdateRequest(types=("sale",), max_pages=1)
    first = start_submission(service, job_service, request)
    service.execute_running(first.run.run_id, request)
    second = start_submission(service, job_service, request)
    service.execute_running(second.run.run_id, request)

    versions = [stage[0].version for stage in publisher.staged]
    assert versions[0] != versions[1]
    assert first.run.run_id.replace("-", "") in versions[0]
    assert second.run.run_id.replace("-", "") in versions[1]


def test_lock_contention_happens_before_pending_job_creation(tmp_path: Path) -> None:
    lock = FakeLock(available=False)
    service, _, repository, _, _, _ = make_service(tmp_path, lock=lock)

    with pytest.raises(listing_update.ListingUpdateAlreadyRunning):
        service.submit(listing_update.ListingUpdateRequest(types=("sale",)))

    assert repository.create_calls == 0
    assert repository.runs == {}


def test_advisory_lock_releases_after_context_exception(tmp_path: Path) -> None:
    lock_path = tmp_path / "listing_update.lock"
    first = listing_update.AdvisoryFileLock(lock_path)
    second = listing_update.AdvisoryFileLock(lock_path)

    with pytest.raises(RuntimeError):
        with first:
            assert second.try_acquire() is False
            raise RuntimeError("boom")

    assert second.try_acquire() is True
    second.release()
