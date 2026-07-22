from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from threading import Event

import pytest

from qingpu_insight.job_executor import LocalJobExecutor
from qingpu_insight.jobs import ACTIVE_STATUSES, InvalidJobTransition, JobRun, JobService, JobStatus


class FakeJobRepository:
    def __init__(self) -> None:
        self._runs: dict[str, JobRun] = {}

    def create_or_get(self, run: JobRun) -> tuple[JobRun, bool]:
        existing = self.find_active_by_key(run.idempotency_key)
        if existing:
            return existing, False
        self._runs[run.run_id] = run
        return run, True

    def get(self, run_id: str) -> JobRun | None:
        return self._runs.get(run_id)

    def find_active_by_key(self, idempotency_key: str) -> JobRun | None:
        return next((run for run in self._runs.values() if run.idempotency_key == idempotency_key
                     and run.status in ACTIVE_STATUSES), None)

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
        now = datetime.now(UTC)
        self._runs[run_id] = replace(
            run, status=target_status,
            started_at=run.started_at or now if target_status == "running" else run.started_at,
            finished_at=(
                now
                if target_status in ("succeeded", "failed", "skipped")
                else run.finished_at
            ),
            attempt=run.attempt + (run.status == "retry_wait" and target_status == "running"),
            output_version=output_version if output_version is not None else run.output_version,
            summary=summary if summary is not None else run.summary,
            error_code=error_code if error_code is not None else run.error_code,
            error_message=error_message if error_message is not None else run.error_message,
        )
        return True


def test_executor_starts_once_and_invokes_callable() -> None:
    repo = FakeJobRepository()
    service = JobService(repo)
    executor = LocalJobExecutor(service)
    run = service.create("test", "exec-key", "manual").run
    started = Event()
    future = executor.submit(run.run_id, started.set)
    assert started.wait(timeout=1)
    future.result(timeout=1)
    assert repo.get(run.run_id).status == "running"  # type: ignore[union-attr]
    executor.shutdown()


def test_executor_sanitizes_uncaught_failure_and_removes_completed_future(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = FakeJobRepository()
    service = JobService(repo)
    executor = LocalJobExecutor(service)
    run = service.create("test", "fail-key", "manual").run
    completed = Event()

    def failing_task() -> None:
        completed.set()
        raise RuntimeError("api_key=super-secret")

    future = executor.submit(run.run_id, failing_task)
    assert completed.wait(timeout=1)
    future.result(timeout=1)
    updated = repo.get(run.run_id)
    assert updated is not None and updated.status == "failed"
    assert updated.error_code == "unhandled_exception"
    assert updated.error_message == "api_key: ***"
    assert "super-secret" not in caplog.text
    assert executor.submitted == []
    executor.shutdown()


def test_executor_propagates_lifecycle_corruption() -> None:
    repo = FakeJobRepository()
    service = JobService(repo)
    executor = LocalJobExecutor(service)
    run = service.create("test", "corrupt-key", "manual").run
    service.start(run.run_id)
    future = executor.submit(run.run_id, lambda: None)
    with pytest.raises(InvalidJobTransition):
        future.result(timeout=1)
    executor.shutdown()
