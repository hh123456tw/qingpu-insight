from __future__ import annotations

from dataclasses import replace
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

    def create_or_get(self, run: JobRun) -> tuple[JobRun, bool]:
        existing = self.find_active_by_key(run.idempotency_key)
        if existing is not None:
            return existing, False
        self.create(run)
        return run, True

    def get(self, run_id: str) -> JobRun | None:
        return self._runs.get(run_id)

    def find_active_by_key(self, idempotency_key: str) -> JobRun | None:
        for run in self._runs.values():
            if run.idempotency_key == idempotency_key and run.status in ACTIVE_STATUSES:
                return run
        return None

    def list_recent(self, limit: int = 20) -> list[JobRun]:
        return list(self._runs.values())[-limit:][::-1]

    def transition(
        self, run_id: str, current_status: JobStatus, target_status: JobStatus,
        *, output_version: str | None = None, summary: dict[str, object] | None = None,
        error_code: str | None = None, error_message: str | None = None,
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
        self._runs[run_id] = replace(
            run, status=target_status, started_at=started_at, finished_at=finished_at,
            attempt=run.attempt + (
                1 if run.status == "retry_wait" and target_status == "running" else 0
            ),
            output_version=output_version if output_version is not None else run.output_version,
            summary=summary if summary is not None else run.summary,
            error_code=error_code if error_code is not None else run.error_code,
            error_message=error_message if error_message is not None else run.error_message,
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
        run = JobService(repo).create("listing_update", "same-key", "manual").run
        with pytest.raises(InvalidJobTransition):
            JobService(repo).succeed(run.run_id, "v1", {})

    def test_active_idempotency_key_returns_existing_run(self, repo: FakeJobRepository) -> None:
        service = JobService(repo)
        first = service.create("listing_update", "same-key", "manual")
        second = service.create("listing_update", "same-key", "manual")
        assert first.__class__.__name__ == "JobSubmission"
        assert first.created is True
        assert second.created is False
        assert second.run.run_id == first.run.run_id

    def test_start_transitions_to_running(self, service: JobService) -> None:
        run = service.create("test", "key1", "manual").run
        started = service.start(run.run_id)
        assert started.status == "running"
        assert started.started_at is not None

    def test_succeed_after_start(self, service: JobService) -> None:
        run = service.create("test", "key2", "manual").run
        service.start(run.run_id)
        finished = service.succeed(run.run_id, "v1", {"rows": 2})
        assert finished.status == "succeeded"
        assert finished.finished_at is not None
        assert finished.output_version == "v1"
        assert finished.summary == {"rows": 2}

    def test_fail_after_running(self, service: JobService) -> None:
        run = service.create("test", "key3", "manual").run
        service.start(run.run_id)
        failed = service.fail(run.run_id, "capture_failed", "api_key=super-secret")
        assert failed.status == "failed"
        assert failed.error_code == "capture_failed"
        assert failed.error_message == "api_key: ***"

    @pytest.mark.parametrize("error_code", ["", " ", "UPPER_CASE", "has-dash", "has.dot"])
    def test_fail_rejects_nonstable_error_code(
        self,
        service: JobService,
        error_code: str,
    ) -> None:
        run = service.create("test", f"bad-code-{error_code}", "manual").run
        service.start(run.run_id)
        with pytest.raises(ValueError, match="stable error code"):
            service.fail(run.run_id, error_code, "api_key=super-secret")
        assert service.get(run.run_id).status == "running"  # type: ignore[union-attr]

    def test_succeed_and_fail_require_terminal_metadata(self, service: JobService) -> None:
        run = service.create("test", "required-metadata", "manual").run
        service.start(run.run_id)
        with pytest.raises(TypeError):
            service.succeed(run.run_id)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            service.fail(run.run_id)  # type: ignore[call-arg]

    def test_failed_to_needs_attention(self, service: JobService) -> None:
        run = service.create("test", "key4", "manual").run
        service.start(run.run_id)
        service.fail(run.run_id, "test_failure", "test failed")
        attention = service.needs_attention(run.run_id)
        assert attention.status == "needs_attention"

    def test_retry_wait_to_running(self, service: JobService) -> None:
        run = service.create("test", "key5", "manual").run
        service.start(run.run_id)
        service.retry(run.run_id)
        restarted = service.start(run.run_id)
        assert restarted.status == "running"
        assert restarted.attempt == 2

    def test_idempotency_returns_existing_different_key(self, service: JobService) -> None:
        first = service.create("test", "key-a", "manual")
        second = service.create("test", "key-b", "manual")
        assert second.run.run_id != first.run.run_id

    def test_completed_idempotency_key_allows_new_run(self, repo: FakeJobRepository) -> None:
        service = JobService(repo)
        first = service.create("test", "dup-key", "manual").run
        service.start(first.run_id)
        service.succeed(first.run_id, "v1", {})
        second = service.create("test", "dup-key", "manual").run
        assert second.run_id != first.run_id

    def test_get_and_list_recent_are_public_service_operations(self, service: JobService) -> None:
        first = service.create("test", "recent-1", "manual").run
        second = service.create("test", "recent-2", "manual").run
        assert service.get(first.run_id) == first
        assert service.list_recent() == [second, first]


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
