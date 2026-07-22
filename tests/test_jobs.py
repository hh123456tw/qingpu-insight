from __future__ import annotations

from datetime import UTC, datetime

import pytest

from qingpu_insight.jobs import (
    ACTIVE_STATUSES,
    InvalidJobTransition,
    JobRun,
    JobService,
    JobStatus,
    redact_job_message,
)


class FakeJobRepository:
    def __init__(self) -> None:
        self._runs: dict[str, JobRun] = {}
        self._now: datetime | None = None

    def set_now(self, now: datetime) -> None:
        self._now = now

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
        now = self._now or datetime.now(UTC)
        started_at = run.started_at
        finished_at = run.finished_at
        if target_status in ("running",):
            started_at = started_at or now
        elif target_status in ("succeeded", "failed", "skipped"):
            finished_at = finished_at or now
        self._runs[run_id] = JobRun(
            run_id=run.run_id,
            job_type=run.job_type,
            trigger=run.trigger,
            idempotency_key=run.idempotency_key,
            status=target_status,
            started_at=started_at,
            finished_at=finished_at,
            attempt=run.attempt,
            input_version=run.input_version,
            output_version=run.output_version,
            summary=run.summary,
            error_code=run.error_code,
            error_message=run.error_message,
        )
        return True


@pytest.fixture
def repo() -> FakeJobRepository:
    return FakeJobRepository()


@pytest.fixture
def service(repo: FakeJobRepository) -> JobService:
    return JobService(repo)


class TestJobService:
    def test_job_service_rejects_illegal_transition(self, repo: FakeJobRepository) -> None:
        run = JobService(repo).create("listing_update", "same-key", "manual")
        with pytest.raises(InvalidJobTransition):
            JobService(repo).succeed(run.run_id)

    def test_active_idempotency_key_returns_existing_run(self, repo: FakeJobRepository) -> None:
        service = JobService(repo)
        first = service.create("listing_update", "same-key", "manual")
        second = service.create("listing_update", "same-key", "manual")
        assert second.run_id == first.run_id

    def test_start_transitions_to_running(self, service: JobService) -> None:
        run = service.create("test", "key1", "manual")
        started = service.start(run.run_id)
        assert started.status == "running"
        assert started.started_at is not None

    def test_succeed_after_start(self, service: JobService) -> None:
        run = service.create("test", "key2", "manual")
        service.start(run.run_id)
        finished = service.succeed(run.run_id)
        assert finished.status == "succeeded"
        assert finished.finished_at is not None

    def test_fail_after_running(self, service: JobService) -> None:
        run = service.create("test", "key3", "manual")
        service.start(run.run_id)
        failed = service.fail(run.run_id)
        assert failed.status == "failed"

    def test_failed_to_needs_attention(self, service: JobService) -> None:
        run = service.create("test", "key4", "manual")
        service.start(run.run_id)
        service.fail(run.run_id)
        attention = service.needs_attention(run.run_id)
        assert attention.status == "needs_attention"

    def test_retry_wait_to_running(self, service: JobService) -> None:
        run = service.create("test", "key5", "manual")
        service.start(run.run_id)
        service.retry(run.run_id)
        restarted = service.start(run.run_id)
        assert restarted.status == "running"

    def test_idempotency_returns_existing_different_key(self, service: JobService) -> None:
        first = service.create("test", "key-a", "manual")
        second = service.create("test", "key-b", "manual")
        assert second.run_id != first.run_id

    def test_completed_idempotency_key_allows_new_run(self, repo: FakeJobRepository) -> None:
        service = JobService(repo)
        first = service.create("test", "dup-key", "manual")
        service.start(first.run_id)
        service.succeed(first.run_id)
        second = service.create("test", "dup-key", "manual")
        assert second.run_id != first.run_id


class TestRedactJobMessage:
    def test_redacts_url_credentials(self) -> None:
        msg = "connecting to mysql://user:secret@host:3306/db"
        result = redact_job_message(msg)
        assert "user:secret" not in result
        assert "***:***@" in result

    def test_redacts_api_key_pattern(self) -> None:
        msg = "api_key=sk-1234567890abcdef"
        result = redact_job_message(msg)
        assert "sk-1234567890abcdef" not in result
        assert "api_key: ***" in result

    def test_redacts_phone_number(self) -> None:
        msg = "contact 0912345678 for details"
        result = redact_job_message(msg)
        assert "0912345678" not in result

    def test_redacts_landline(self) -> None:
        msg = "call 033456789 for office"
        result = redact_job_message(msg)
        assert "033456789" not in result

    def test_plain_message_passes_through(self) -> None:
        msg = "all systems normal"
        assert redact_job_message(msg) == msg
