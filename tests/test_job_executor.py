from __future__ import annotations

import time
from datetime import UTC, datetime

from qingpu_insight.job_executor import LocalJobExecutor
from qingpu_insight.jobs import ACTIVE_STATUSES, JobRun, JobService, JobStatus


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


def test_executor_submits_and_tracks_run_id() -> None:
    repo = FakeJobRepository()
    service = JobService(repo)
    executor = LocalJobExecutor(service)
    run = service.create("test", "exec-key", "manual")
    executed: list[str] = []

    def task() -> None:
        executed.append("done")

    executor.submit(run.run_id, task)
    time.sleep(0.1)
    assert run.run_id in executor.submitted
    assert executed == ["done"]


def test_executor_starts_then_records_failure() -> None:
    repo = FakeJobRepository()
    service = JobService(repo)
    executor = LocalJobExecutor(service)
    run = service.create("test", "fail-key", "manual")

    def failing_task() -> None:
        raise RuntimeError("task failed")

    executor.submit(run.run_id, failing_task)
    time.sleep(0.2)
    updated = repo.get(run.run_id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.started_at is not None
