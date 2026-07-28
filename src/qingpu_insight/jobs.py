from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

CONVERSATION_IMPORT = "conversation_import"
CONVERSATION_REFRESH = "conversation_refresh"
CONVERSATION_REPLY = "conversation_reply"

JobStatus = Literal[
    "pending", "running", "succeeded", "retry_wait", "skipped", "failed",
    "needs_attention",
]

ALLOWED_TRANSITIONS: dict[JobStatus, tuple[JobStatus, ...]] = {
    "pending": ("running", "failed"),
    "running": ("succeeded", "retry_wait", "skipped", "failed"),
    "failed": ("needs_attention",),
    "retry_wait": ("running", "needs_attention", "failed"),
    "succeeded": (),
    "skipped": (),
    "needs_attention": (),
}


class InvalidJobTransition(Exception):
    def __init__(self, run_id: str, current: JobStatus, target: JobStatus) -> None:
        self.run_id = run_id
        self.current = current
        self.target = target
        super().__init__(f"invalid transition {current} -> {target} for run {run_id}")


class ActiveIdempotencyKey(Exception):
    def __init__(self, idempotency_key: str, run_id: str) -> None:
        self.idempotency_key = idempotency_key
        self.run_id = run_id
        super().__init__(f"active idempotency key {idempotency_key} already has run {run_id}")


ACTIVE_STATUSES: tuple[JobStatus, ...] = ("pending", "running", "retry_wait")


@dataclass(frozen=True)
class JobRun:
    run_id: str
    job_type: str
    trigger: str
    idempotency_key: str
    status: JobStatus
    started_at: datetime | None
    finished_at: datetime | None
    attempt: int
    input_version: str | None
    output_version: str | None
    summary: dict[str, object]
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True)
class JobSubmission:
    run: JobRun
    created: bool


_CREDENTIAL_PATTERN = re.compile(r"(?<=\/\/)[^:]+:[^@]+@")
_API_KEY_PATTERN = re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*\S+")
_PHONE_PATTERN = re.compile(r"(?<!\d)09\d{2}(?:-?\d{3}){2}(?!\d)")
_LANDLINE_PATTERN = re.compile(r"(?<!\d)0[2-8](?:-?\d){7,8}(?!\d)")
_ERROR_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def redact_job_message(message: str) -> str:
    result = _CREDENTIAL_PATTERN.sub("***:***@", message)
    result = _API_KEY_PATTERN.sub(lambda m: m.group(1) + ": ***", result)
    result = _PHONE_PATTERN.sub("***-***-***", result)
    result = _LANDLINE_PATTERN.sub("***-****", result)
    return result


class JobRepository(Protocol):
    def create_or_get(self, run: JobRun) -> tuple[JobRun, bool]: ...
    def get(self, run_id: str) -> JobRun | None: ...
    def find_active_by_key(self, idempotency_key: str) -> JobRun | None: ...
    def list_recent(
        self, limit: int = 20, job_type: str | None = None
    ) -> list[JobRun]: ...
    def list_active(self, job_type: str) -> list[JobRun]: ...
    def update_summary(
        self,
        run_id: str,
        expected_status: JobStatus,
        summary: dict[str, object],
    ) -> bool: ...
    def transition(
        self, run_id: str, current_status: JobStatus, target_status: JobStatus,
        *,
        output_version: str | None = None,
        summary: dict[str, object] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool: ...


class JobService:
    def __init__(self, repository: JobRepository) -> None:
        self._repository = repository

    def create(
        self, job_type: str, idempotency_key: str, trigger: str,
        input_version: str | None = None,
    ) -> JobSubmission:
        run = JobRun(
            run_id=str(uuid.uuid4()),
            job_type=job_type,
            trigger=trigger,
            idempotency_key=idempotency_key,
            status="pending",
            started_at=None,
            finished_at=None,
            attempt=1,
            input_version=input_version,
            output_version=None,
            summary={},
            error_code=None,
            error_message=None,
        )
        persisted, created = self._repository.create_or_get(run)
        return JobSubmission(run=persisted, created=created)

    def get(self, run_id: str) -> JobRun | None:
        return self._repository.get(run_id)

    def list_active(self, job_type: str) -> list[JobRun]:
        return self._repository.list_active(job_type)

    def list_recent(
        self, limit: int = 20, job_type: str | None = None
    ) -> list[JobRun]:
        return self._repository.list_recent(limit, job_type)

    def progress(self, run_id: str, summary: dict[str, object]) -> JobRun:
        run = self._repository.get(run_id)
        if run is None:
            raise ValueError(f"run {run_id} not found")
        if run.status != "running":
            raise InvalidJobTransition(run_id, run.status, "running")
        if not self._repository.update_summary(run_id, "running", summary):
            raise InvalidJobTransition(run_id, "running", "running")
        updated = self._repository.get(run_id)
        assert updated is not None
        return updated

    def recover_interrupted(self, job_type: str) -> list[JobRun]:
        recovered = []
        for run in self._repository.list_active(job_type):
            recovered.append(
                self.fail(run.run_id, "worker_interrupted", "worker interrupted")
            )
        return recovered

    def _transition(
        self,
        run_id: str,
        target: JobStatus,
        *,
        output_version: str | None = None,
        summary: dict[str, object] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> JobRun:
        run = self._repository.get(run_id)
        if run is None:
            raise ValueError(f"run {run_id} not found")
        if target not in ALLOWED_TRANSITIONS.get(run.status, ()):
            raise InvalidJobTransition(run_id, run.status, target)
        success = self._repository.transition(
            run_id,
            run.status,
            target,
            output_version=output_version,
            summary=summary,
            error_code=error_code,
            error_message=error_message,
        )
        if not success:
            raise InvalidJobTransition(run_id, run.status, target)
        updated = self._repository.get(run_id)
        assert updated is not None
        return updated

    def start(self, run_id: str) -> JobRun:
        return self._transition(run_id, "running")

    def succeed(
        self,
        run_id: str,
        output_version: str,
        summary: dict[str, object],
    ) -> JobRun:
        return self._transition(
            run_id,
            "succeeded",
            output_version=output_version,
            summary=summary,
        )

    def fail(self, run_id: str, error_code: str, error_message: str) -> JobRun:
        if _ERROR_CODE_PATTERN.fullmatch(error_code) is None:
            raise ValueError("error_code must be a nonblank stable error code token")
        return self._transition(
            run_id,
            "failed",
            error_code=error_code,
            error_message=redact_job_message(error_message),
        )

    def skip(self, run_id: str, summary: dict[str, object]) -> JobRun:
        return self._transition(run_id, "skipped", summary=summary)

    def retry(self, run_id: str) -> JobRun:
        return self._transition(run_id, "retry_wait")

    def needs_attention(self, run_id: str) -> JobRun:
        return self._transition(run_id, "needs_attention")
