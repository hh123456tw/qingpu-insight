from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from qingpu_insight.conversation_fallback import ReplyExecution
from qingpu_insight.conversation_service import ConversationService
from qingpu_insight.conversation_validation import ValidatedChatAnswer
from qingpu_insight.job_executor import LocalJobExecutor
from qingpu_insight.jobs import ACTIVE_STATUSES, InvalidJobTransition, JobRun, JobService, JobStatus
from qingpu_insight.listing_update import ListingUpdateRequest, ListingUpdateService


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

    def list_recent(
        self, limit: int = 20, job_type: str | None = None
    ) -> list[JobRun]:
        runs = list(self._runs.values())
        if job_type is not None:
            runs = [run for run in runs if run.job_type == job_type]
        return runs[-limit:][::-1]

    def list_active(self, job_type: str) -> list[JobRun]:
        return [
            run
            for run in self._runs.values()
            if run.job_type == job_type and run.status in ACTIVE_STATUSES
        ]

    def update_summary(
        self,
        run_id: str,
        expected_status: JobStatus,
        summary: dict[str, object],
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


def test_conversation_commands_use_executor_owned_start_transition() -> None:
    repo = FakeJobRepository()
    job_service = JobService(repo)
    executor = LocalJobExecutor(job_service)
    conversation_repository = MagicMock()
    import_service = MagicMock()
    import_service.import_initial_listing.return_value = SimpleNamespace(
        outcome="needs_attention"
    )
    import_service.refresh_listing.return_value = SimpleNamespace(
        outcome="needs_attention"
    )
    conversation_repository.get_conversation.return_value = SimpleNamespace(
        active_evidence_revision=1,
        rolling_summary=None,
        default_provider="gemini",
        default_model="gemini-3.5-flash-lite",
    )
    conversation_repository.get_evidence_pack.return_value = SimpleNamespace(
        facts=[],
        limitations=[],
    )
    conversation_repository.get_messages.return_value = []
    provider_registry = MagicMock()
    provider_registry.get.return_value.reply.return_value = MagicMock()
    validator = MagicMock(
        return_value=ValidatedChatAnswer(
            answer="一般建議",
            citations=[],
            evidence_revision=1,
            general_guidance=["一般建議"],
            suggested_questions=[],
        )
    )
    reply_executor = MagicMock()
    reply_executor.execute.return_value = ReplyExecution(
        validated=validator.return_value,
        actual_provider="rule",
        actual_model="rule",
        fallback_reason="cloud_unavailable",
    )
    service = ConversationService(
        repository=conversation_repository,
        import_service=import_service,
        provider_registry=provider_registry,
        reply_executor=reply_executor,
        validator=validator,
        job_service=job_service,
        executor=executor,
    )

    imported = service.start_import(
        conversation_id="conv-1",
        raw_url="https://sale.591.com.tw/home/house/detail/1/2.html",
        idempotency_key="conversation-import",
    )
    executor.shutdown(wait=True)
    assert repo.get(imported.run_id).status == "needs_attention"  # type: ignore[union-attr]

    executor = LocalJobExecutor(job_service)
    service._executor = executor
    refreshed = service.start_refresh(
        conversation_id="conv-1",
        idempotency_key="conversation-refresh",
    )
    executor.shutdown(wait=True)
    assert repo.get(refreshed.run_id).status == "needs_attention"  # type: ignore[union-attr]

    executor = LocalJobExecutor(job_service)
    service._executor = executor
    replied = service.start_reply(
        conversation_id="conv-1",
        question="值得買嗎？",
        evidence_revision=1,
        idempotency_key="conversation-reply",
    )
    executor.shutdown(wait=True)
    assert repo.get(replied.run_id).status == "succeeded"  # type: ignore[union-attr]


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


def test_executor_preserves_listing_service_terminal_failure(tmp_path) -> None:
    repo = FakeJobRepository()
    job_service = JobService(repo)
    executor = LocalJobExecutor(job_service)

    class FailingPreparation:
        def prepare(self, listing_type, max_pages):
            raise RuntimeError("mysql://admin:password@db/private")

    class Lock:
        def __init__(self) -> None:
            self.released = 0

        def try_acquire(self):
            return True

        def set_owner(self, idempotency_key, run_id):
            pass

        def read_owner(self):
            return None

        def release(self):
            self.released += 1

    class Publisher:
        def current(self):
            return None

    lock = Lock()
    listing_service = ListingUpdateService(
        job_service,
        publisher=Publisher(),  # type: ignore[arg-type]
        preparation_runner=FailingPreparation(),
        root=tmp_path,
        lock_factory=lambda: lock,
    )
    request = ListingUpdateRequest(types=("sale",), max_pages=1)
    submission = listing_service.submit(request)

    future = listing_service.handoff(submission, request, executor)
    future.result(timeout=2)

    failed = job_service.get(submission.run.run_id)
    assert failed is not None and failed.status == "failed"
    assert failed.error_code == "preparation_failed"
    assert failed.error_message == "sale listing preparation failed"
    assert lock.released == 1
    executor.shutdown()
