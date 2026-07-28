from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

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

    def list_recent(self, limit: int = 20, job_type: str | None = None) -> list[JobRun]:
        runs = list(self._runs.values())
        if job_type is not None:
            runs = [r for r in runs if r.job_type == job_type]
        return runs[-limit:][::-1]

    def list_active(self, job_type: str) -> list[JobRun]:
        return sorted(
            [
                r
                for r in self._runs.values()
                if r.job_type == job_type and r.status in ACTIVE_STATUSES
            ],
            key=lambda r: r.run_id,
        )

    def update_summary(
        self, run_id: str, expected_status: JobStatus, summary: dict[str, Any]
    ) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.status != expected_status:
            return False
        self._runs[run_id] = replace(run, summary=summary)
        return True

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


class TestProgressAndRecovery:
    def test_progress_replaces_summary_only_while_running(
        self, service: JobService
    ) -> None:
        run = service.create("model_training", "model-training", "web").run
        service.start(run.run_id)
        updated = service.progress(
            run.run_id,
            {"stage": "training_resale", "completed_markets": []},
        )
        assert updated.status == "running"
        assert updated.summary == {
            "stage": "training_resale",
            "completed_markets": [],
        }
        service.succeed(run.run_id, run.run_id, {"stage": "complete"})
        with pytest.raises(InvalidJobTransition):
            service.progress(run.run_id, {"stage": "late_write"})

    def test_list_recent_can_filter_job_type(self, service: JobService) -> None:
        model = service.create("model_training", "model-1", "web").run
        service.create("listing_update", "listing-1", "web")
        assert service.list_recent(job_type="model_training") == [model]

    def test_recover_interrupted_marks_only_requested_job_type_failed(
        self,
        service: JobService,
    ) -> None:
        pending = service.create("model_training", "model-pending", "web").run
        running = service.create("model_training", "model-running", "web").run
        retry_wait = service.create("model_training", "model-retry-wait", "web").run
        listing = service.create("listing_update", "listing-running", "web").run
        service.start(running.run_id)
        service.start(retry_wait.run_id)
        service.retry(retry_wait.run_id)
        service.start(listing.run_id)

        recovered = service.recover_interrupted("model_training")

        assert {run.run_id for run in recovered} == {
            pending.run_id,
            running.run_id,
            retry_wait.run_id,
        }
        assert all(run.status == "failed" for run in recovered)
        assert all(run.error_code == "worker_interrupted" for run in recovered)
        assert service.get(listing.run_id).status == "running"  # type: ignore[union-attr]


class TestJobServiceSkip:
    def test_skip_transitions_running_to_skipped(
        self, service: JobService
    ) -> None:
        run = service.create("test", "skip-key", "manual").run
        service.start(run.run_id)
        skipped = service.skip(run.run_id, {"reason": "user cancelled"})
        assert skipped.status == "skipped"
        assert skipped.finished_at is not None
        assert skipped.summary == {"reason": "user cancelled"}

    def test_skip_preserves_summary(self, service: JobService) -> None:
        run = service.create("test", "skip-key2", "manual").run
        service.start(run.run_id)
        skipped = service.skip(run.run_id, {"reason": "no more time", "trials": 5})
        assert skipped.summary == {"reason": "no more time", "trials": 5}

    def test_skip_from_pending_is_illegal(self, service: JobService) -> None:
        run = service.create("test", "skip-key3", "manual").run
        with pytest.raises(InvalidJobTransition):
            service.skip(run.run_id, {})

    def test_skip_from_succeeded_is_illegal(self, service: JobService) -> None:
        run = service.create("test", "skip-key4", "manual").run
        service.start(run.run_id)
        service.succeed(run.run_id, "v1", {})
        with pytest.raises(InvalidJobTransition):
            service.skip(run.run_id, {})


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
