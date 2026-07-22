from __future__ import annotations

from datetime import UTC, datetime

import pytest

from qingpu_insight.jobs import ACTIVE_STATUSES, JobRun, JobService, JobStatus
from qingpu_insight.listing_sources import ListingType
from qingpu_insight.listing_update import ListingUpdateRequest, ListingUpdateService
from qingpu_insight.publishing import DatasetVersion


class FakeJobRepository:
    def __init__(self) -> None:
        self._runs: dict[str, JobRun] = {}

    def create(self, run: JobRun) -> None:
        self._runs[run.run_id] = run

    def get(self, run_id: str) -> JobRun | None:
        return self._runs.get(run_id)

    def find_active_by_key(self, idempotency_key: str) -> JobRun | None:
        for run in self._runs.values():
            if run.idempotency_key == idempotency_key and run.status in ACTIVE_STATUSES:
                return run
        return None

    def transition(
        self, run_id: str, current_status: JobStatus, target_status: JobStatus,
    ) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.status != current_status:
            return False
        now = datetime.now(UTC)
        started_at = run.started_at
        finished_at = run.finished_at
        if target_status == "running":
            started_at = started_at or now
        elif target_status in ("succeeded", "failed", "skipped"):
            finished_at = finished_at or now
        self._runs[run_id] = JobRun(
            run_id=run.run_id, job_type=run.job_type, trigger=run.trigger,
            idempotency_key=run.idempotency_key, status=target_status,
            started_at=started_at, finished_at=finished_at,
            attempt=run.attempt, input_version=run.input_version,
            output_version=run.output_version, summary=run.summary,
            error_code=run.error_code, error_message=run.error_message,
        )
        return True


class FakePublisher:
    def __init__(self) -> None:
        self.staged: list[DatasetVersion] = []
        self.published: list[str] = []
        self._current: DatasetVersion | None = None

    def stage(self, version: DatasetVersion) -> None:
        self.staged.append(version)

    def publish(self, version: str) -> None:
        self.published.append(version)

    def current(self) -> DatasetVersion | None:
        return self._current


class FakeCaptureRunner:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.incomplete_type: str | None = None

    def capture(self, listing_type: ListingType, max_pages: int) -> None:
        self.calls.append(listing_type)
        if listing_type == self.incomplete_type:
            raise RuntimeError(f"capture failed for {listing_type}")


class FakeDependencies:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.repo = FakeJobRepository()
        self.job_service = JobService(self.repo)
        self.publisher = FakePublisher()
        self.source = FakeCaptureRunner(self.calls)
        self.service = ListingUpdateService(
            job_service=self.job_service,
            publisher=self.publisher,
            capture_runner=self.source,
        )


@pytest.fixture
def fake_dependencies() -> FakeDependencies:
    return FakeDependencies()


class TestListingUpdateService:
    def test_listing_update_runs_all_types_then_publishes(
        self, fake_dependencies: FakeDependencies,
    ) -> None:
        request = ListingUpdateRequest(types=("sale", "newhouse", "rental"), max_pages=1)
        run = fake_dependencies.service.submit(request)
        result = fake_dependencies.service.execute(run.run_id, request)
        assert fake_dependencies.calls == ["sale", "newhouse", "rental"]
        assert fake_dependencies.publisher.published
        assert result.status == "succeeded"

    def test_incomplete_type_never_publishes(
        self, fake_dependencies: FakeDependencies,
    ) -> None:
        fake_dependencies.source.incomplete_type = "newhouse"
        request = ListingUpdateRequest()
        run = fake_dependencies.service.submit(request)
        with pytest.raises(RuntimeError, match="capture failed"):
            fake_dependencies.service.execute(run.run_id, request)
        updated = fake_dependencies.repo.get(run.run_id)
        assert updated is not None
        assert updated.status == "failed"
        assert fake_dependencies.publisher.published == []

    def test_submit_returns_job_run(self, fake_dependencies: FakeDependencies) -> None:
        request = ListingUpdateRequest(types=("sale",), max_pages=5)
        run = fake_dependencies.service.submit(request)
        assert run.status == "pending"
        assert run.job_type == "listing_update"
