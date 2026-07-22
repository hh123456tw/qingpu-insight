from __future__ import annotations

import json
import msvcrt
import os
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, cast

import pandas as pd

from qingpu_insight.jobs import JobRun, JobService, JobSubmission, redact_job_message
from qingpu_insight.listing_sources import CaptureBatch, ListingType
from qingpu_insight.pipeline import Clock, PipelineContext, PipelineRunner, StepResult
from qingpu_insight.publishing import (
    DatasetVersion,
    MySQLVersionPublisher,
    compute_artifact_hash,
    compute_rows_hash,
)

_LISTING_TYPES = frozenset({"sale", "newhouse", "rental"})


@dataclass(frozen=True)
class ListingUpdateRequest:
    types: tuple[ListingType, ...] = ("sale", "newhouse", "rental")
    max_pages: int = 10
    trigger: str = "manual"

    def __post_init__(self) -> None:
        if not self.types:
            raise ValueError("types must be non-empty")
        unsupported = [value for value in self.types if value not in _LISTING_TYPES]
        if unsupported:
            raise ValueError(f"unsupported listing types: {unsupported!r}")
        if len(set(self.types)) != len(self.types):
            raise ValueError("duplicate listing types are not allowed")
        if type(self.max_pages) is not int or not 1 <= self.max_pages <= 100:
            raise ValueError("max_pages must be an integer from 1 through 100")
        if not isinstance(self.trigger, str) or not self.trigger.strip():
            raise ValueError("trigger must be nonblank")


@dataclass(frozen=True)
class PreparedListingType:
    batch: CaptureBatch
    rows: pd.DataFrame
    events: pd.DataFrame
    summary: dict[str, object]


class ListingPreparationRunner(Protocol):
    def prepare(
        self, listing_type: ListingType, max_pages: int
    ) -> PreparedListingType: ...


class ListingUpdateLock(Protocol):
    def try_acquire(self) -> bool: ...
    def set_owner(self, idempotency_key: str, run_id: str) -> None: ...
    def read_owner(self) -> tuple[str, str] | None: ...
    def release(self) -> None: ...


class ListingUpdateExecutor(Protocol):
    def submit(self, run_id: str, callable: Callable[[], object]) -> Future: ...


class ListingUpdateError(RuntimeError):
    def __init__(self, error_code: str, safe_message: str) -> None:
        self.error_code = error_code
        self.safe_message = redact_job_message(safe_message)
        super().__init__(self.safe_message)


class ListingUpdateAlreadyRunning(ListingUpdateError):
    def __init__(self) -> None:
        super().__init__("already_running", "listing update already running")


class AdvisoryFileLock:
    """Process-owned, non-blocking advisory lock held by an open file handle."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle = None

    def try_acquire(self) -> bool:
        if self._handle is not None:
            return False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def set_owner(self, idempotency_key: str, run_id: str) -> None:
        if self._handle is None:
            raise RuntimeError("cannot set owner on an unlocked file")
        payload = json.dumps(
            {"idempotency_key": idempotency_key, "run_id": run_id},
            sort_keys=True,
        ).encode("utf-8")
        self._handle.seek(1)
        self._handle.truncate()
        self._handle.write(payload)
        self._handle.flush()

    def read_owner(self) -> tuple[str, str] | None:
        try:
            with self._path.open("rb") as handle:
                handle.seek(1)
                payload = json.loads(handle.read().decode("utf-8"))
            key = payload.get("idempotency_key")
            run_id = payload.get("run_id")
            if isinstance(key, str) and isinstance(run_id, str):
                return key, run_id
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            pass
        return None

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> AdvisoryFileLock:
        if not self.try_acquire():
            raise ListingUpdateAlreadyRunning()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


@dataclass(frozen=True)
class ArtifactMetadata:
    path: Path
    artifact_hash: str
    row_count: int
    rows_hash: str


class ArtifactWriter(Protocol):
    def write(self, rows: pd.DataFrame, path: Path) -> ArtifactMetadata: ...


class AtomicParquetArtifactWriter:
    def write(self, rows: pd.DataFrame, path: Path) -> ArtifactMetadata:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            rows.to_parquet(temporary, index=False)
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        artifact_rows = pd.read_parquet(path)
        rows_hash = compute_rows_hash(rows)
        if len(artifact_rows) != len(rows) or compute_rows_hash(artifact_rows) != rows_hash:
            raise ValueError("Parquet round-trip changed canonical listing rows")
        return ArtifactMetadata(
            path=path,
            artifact_hash=compute_artifact_hash(path),
            row_count=len(rows),
            rows_hash=rows_hash,
        )


@dataclass
class _RunState:
    prepared: list[PreparedListingType] = field(default_factory=list)
    rows: pd.DataFrame = field(default_factory=pd.DataFrame)
    events: pd.DataFrame = field(default_factory=pd.DataFrame)
    version: DatasetVersion | None = None


@dataclass
class _Reservation:
    idempotency_key: str
    lock: ListingUpdateLock
    state: Literal[
        "reserved", "handed_off", "claimed", "reconciling", "released"
    ] = "reserved"


class _ActionStep:
    required = True
    max_attempts = 1

    def __init__(self, name: str, action: Callable[[], dict[str, object]]) -> None:
        self.name = name
        self._action = action

    def run(self, context: PipelineContext) -> StepResult:
        del context
        try:
            return StepResult(name=self.name, status="succeeded", output=self._action())
        except ListingUpdateError as error:
            return StepResult(
                name=self.name,
                status="failed",
                output={"safe_message": error.safe_message},
                error_code=error.error_code,
            )


class ListingUpdateService:
    def __init__(
        self,
        job_service: JobService,
        publisher: MySQLVersionPublisher,
        preparation_runner: ListingPreparationRunner | None = None,
        root: Path | None = None,
        *,
        lock_factory: Callable[[], ListingUpdateLock] | None = None,
        artifact_writer: ArtifactWriter | None = None,
        clock: Clock | None = None,
    ) -> None:
        if preparation_runner is None:
            raise ValueError("preparation_runner is required")
        self._job_service = job_service
        self._publisher = publisher
        self._preparation_runner = preparation_runner
        self._root = root or Path.cwd()
        lock_path = self._root / "data" / "locks" / "listing_update.lock"
        self._lock_factory = lock_factory or (lambda: AdvisoryFileLock(lock_path))
        self._artifact_writer = artifact_writer or AtomicParquetArtifactWriter()
        self._clock = clock
        self._reservation_guard = threading.Lock()
        self._reservations: dict[str, _Reservation] = {}
        self._reserved_by_key: dict[str, str] = {}

    @property
    def job_service(self) -> JobService:
        """Lifecycle access for the foreground CLI and background executor."""
        return self._job_service

    def _build_idempotency_key(self, request: ListingUpdateRequest) -> str:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        types_str = "-".join(sorted(request.types))
        trigger_identity = sha256(request.trigger.encode("utf-8")).hexdigest()
        return (
            f"listing-update-{today}-{types_str}-p{request.max_pages}"
            f"-t{trigger_identity}"
        )

    @staticmethod
    def _version_for_run(run_id: str) -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return f"{timestamp}-{run_id.replace('-', '')}"

    def submit(self, request: ListingUpdateRequest) -> JobSubmission:
        if not isinstance(request, ListingUpdateRequest):
            raise ValueError("request must be a validated ListingUpdateRequest")
        idempotency_key = self._build_idempotency_key(request)
        with self._reservation_guard:
            reserved_run_id = self._reserved_by_key.get(idempotency_key)
            if reserved_run_id is not None:
                existing = self._job_service.get(reserved_run_id)
                if existing is not None and existing.status in (
                    "pending", "running", "retry_wait"
                ):
                    return JobSubmission(run=existing, created=False)

            lock = self._lock_factory()
            if not lock.try_acquire():
                owner = lock.read_owner()
                if owner is not None and owner[0] == idempotency_key:
                    existing = self._job_service.get(owner[1])
                    if existing is not None and existing.status in (
                        "pending", "running", "retry_wait"
                    ):
                        return JobSubmission(run=existing, created=False)
                raise ListingUpdateAlreadyRunning()

            try:
                submission = self._job_service.create(
                    "listing_update", idempotency_key, request.trigger
                )
                if not submission.created:
                    lock.release()
                    return submission
                try:
                    lock.set_owner(idempotency_key, submission.run.run_id)
                except Exception:
                    self._recover_startup_failure(submission.run.run_id)
                    raise ListingUpdateError(
                        "startup_failed", "listing update startup failed"
                    ) from None
                self._reservations[submission.run.run_id] = _Reservation(
                    idempotency_key=idempotency_key,
                    lock=lock,
                )
                self._reserved_by_key[idempotency_key] = submission.run.run_id
                return submission
            except ListingUpdateError:
                lock.release()
                raise
            except Exception:
                lock.release()
                raise

    def handoff(
        self,
        submission: JobSubmission,
        request: ListingUpdateRequest,
        executor: ListingUpdateExecutor,
    ) -> Future:
        """Hand a reserved new run to the lifecycle-owning executor safely."""
        if not submission.created:
            raise ListingUpdateError(
                "invalid_submission", "only a newly created run can be handed off"
            )
        run_id = submission.run.run_id
        with self._reservation_guard:
            reservation = self._reservations.get(run_id)
            if reservation is None or reservation.state != "reserved":
                raise ListingUpdateError(
                    "execution_not_owned", "listing update execution is not owned"
                )
            reservation.state = "handed_off"
        try:
            future = executor.submit(
                run_id, lambda: self.execute_running(run_id, request)
            )
        except Exception:
            self._reconcile_handoff_failure(run_id)
            raise ListingUpdateError(
                "startup_failed", "listing update executor startup failed"
            ) from None
        future.add_done_callback(
            lambda completed, reserved_run_id=run_id: self._reconcile_handoff_failure(
                reserved_run_id
            )
        )
        return future

    def _claim_execution(self, run_id: str) -> None:
        with self._reservation_guard:
            reservation = self._reservations.get(run_id)
            if reservation is None or reservation.state != "handed_off":
                raise ListingUpdateError(
                    "execution_not_owned", "listing update execution is not owned"
                )
            reservation.state = "claimed"

    def _reconcile_handoff_failure(self, run_id: str) -> None:
        with self._reservation_guard:
            reservation = self._reservations.get(run_id)
            if reservation is None or reservation.state != "handed_off":
                return
            reservation.state = "reconciling"
        self._recover_startup_failure(run_id)
        self._release_reservation(run_id)

    def _recover_startup_failure(self, run_id: str) -> None:
        """Bounded legal recovery: pending/retry_wait -> running -> failed."""
        for _attempt in range(3):
            try:
                run = self._job_service.get(run_id)
            except Exception:
                continue
            if run is None or run.status in (
                "succeeded", "failed", "skipped", "needs_attention"
            ):
                return
            if run.status in ("pending", "retry_wait"):
                try:
                    run = self._job_service.start(run_id)
                except Exception:
                    continue
            if run.status == "running":
                try:
                    self._job_service.fail(
                        run_id,
                        "startup_failed",
                        "listing update startup failed",
                    )
                    return
                except Exception:
                    continue

    def _fail_running_if_possible(
        self, run_id: str, error: ListingUpdateError
    ) -> None:
        try:
            current = self._job_service.get(run_id)
            if current is not None and current.status == "running":
                self._job_service.fail(run_id, error.error_code, error.safe_message)
        except Exception:
            pass

    def execute_running(
        self, run_id: str, request: ListingUpdateRequest
    ) -> JobRun:
        self._claim_execution(run_id)
        run: JobRun | None = None
        try:
            try:
                run = self._job_service.get(run_id)
            except Exception:
                raise ListingUpdateError(
                    "job_state_failed", "listing update job state lookup failed"
                ) from None
            if run is None or run.status != "running":
                raise ListingUpdateError(
                    "invalid_job_state", "job must already be running before execution"
                )
            try:
                expected = self._publisher.current()
            except Exception:
                raise ListingUpdateError(
                    "current_version_failed", "current listing version lookup failed"
                ) from None
            expected_version = expected.version if expected is not None else None
            version_name = self._version_for_run(run_id)
            state = _RunState()
            steps = [
                _ActionStep(
                    f"prepare_{listing_type}",
                    lambda listing_type=listing_type: self._prepare(
                        state, cast(ListingType, listing_type), request.max_pages
                    ),
                )
                for listing_type in request.types
            ]
            steps.extend(
                [
                    _ActionStep(
                        "artifact",
                        lambda: self._write_artifact(state, version_name, run_id),
                    ),
                    _ActionStep("stage", lambda: self._stage(state)),
                    _ActionStep(
                        "publish", lambda: self._publish(state, expected_version)
                    ),
                ]
            )
            runner = PipelineRunner(steps, clock=self._clock)
            pipeline_result = runner.run(
                PipelineContext(
                    run_id=run_id,
                    working_dir=self._root / "data" / "processed" / "listing_versions",
                    params={"types": list(request.types), "max_pages": request.max_pages},
                )
            )
            if pipeline_result.status != "succeeded":
                failed = pipeline_result.step_results[-1]
                error = ListingUpdateError(
                    failed.error_code or "listing_update_failed",
                    str(failed.output.get("safe_message") or "listing update failed"),
                )
                raise error

            assert state.version is not None
            summary = dict(state.version.summary)
            return self._job_service.succeed(
                run_id, output_version=state.version.version, summary=summary
            )
        except ListingUpdateError as error:
            if run is not None and run.status == "running":
                self._job_service.fail(run_id, error.error_code, error.safe_message)
            elif run is not None and run.status in ("pending", "retry_wait"):
                self._recover_startup_failure(run_id)
            elif run is None:
                self._fail_running_if_possible(run_id, error)
            raise
        except Exception:
            error = ListingUpdateError(
                "listing_update_failed", "listing update failed safely"
            )
            if run is not None and run.status == "running":
                self._job_service.fail(run_id, error.error_code, error.safe_message)
            elif run is None:
                self._fail_running_if_possible(run_id, error)
            raise error from None
        finally:
            self._release_reservation(run_id)

    def _prepare(
        self, state: _RunState, listing_type: ListingType, max_pages: int
    ) -> dict[str, object]:
        try:
            prepared = self._preparation_runner.prepare(listing_type, max_pages)
        except ListingUpdateError:
            raise
        except Exception:
            raise ListingUpdateError(
                "preparation_failed", f"{listing_type} listing preparation failed"
            ) from None
        batch = prepared.batch
        if (
            batch.listing_type != listing_type
            or batch.errors
            or not batch.reached_terminal_page
            or not batch.is_complete
        ):
            raise ListingUpdateError(
                "capture_incomplete", f"{listing_type} capture is incomplete"
            )
        if prepared.rows.empty:
            raise ListingUpdateError(
                "empty_prepared_rows", f"{listing_type} produced no prepared rows"
            )
        state.prepared.append(prepared)
        return {"rows": len(prepared.rows), "events": len(prepared.events)}

    def _write_artifact(
        self, state: _RunState, version_name: str, run_id: str
    ) -> dict[str, object]:
        try:
            state.rows = pd.concat(
                [prepared.rows for prepared in state.prepared], ignore_index=True
            )
            state.events = pd.concat(
                [prepared.events for prepared in state.prepared], ignore_index=True
            )
            path = (
                self._root
                / "data"
                / "processed"
                / "listing_versions"
                / f"{version_name}.parquet"
            )
            metadata = self._artifact_writer.write(state.rows, path)
            if (
                not metadata.path.is_file()
                or metadata.row_count != len(state.rows)
                or metadata.artifact_hash != compute_artifact_hash(metadata.path)
                or metadata.rows_hash != compute_rows_hash(state.rows)
            ):
                raise ValueError("artifact metadata mismatch")
            per_type = {
                prepared.batch.listing_type: prepared.summary
                for prepared in state.prepared
            }
            summary: dict[str, object] = {
                "types": [prepared.batch.listing_type for prepared in state.prepared],
                "rows": len(state.rows),
                "events": len(state.events),
                "batches": len(state.prepared),
                "per_type": per_type,
                "artifact_hash": metadata.artifact_hash,
                "rows_hash": metadata.rows_hash,
            }
            state.version = DatasetVersion(
                version=version_name,
                run_id=run_id,
                status="ready",
                summary=summary,
                artifact_path=str(metadata.path),
                artifact_hash=metadata.artifact_hash,
                artifact_row_count=metadata.row_count,
                rows_hash=metadata.rows_hash,
            )
            return {"path": str(metadata.path), "rows": metadata.row_count}
        except ListingUpdateError:
            raise
        except Exception:
            raise ListingUpdateError(
                "artifact_failed", "listing artifact validation failed"
            ) from None

    def _stage(self, state: _RunState) -> dict[str, object]:
        assert state.version is not None
        try:
            self._publisher.stage(
                state.version,
                [prepared.batch for prepared in state.prepared],
                state.rows,
                state.events,
            )
        except Exception:
            raise ListingUpdateError("stage_failed", "listing dataset staging failed") from None
        return {"version": state.version.version}

    def _publish(
        self, state: _RunState, expected_current_version: str | None
    ) -> dict[str, object]:
        assert state.version is not None
        try:
            self._publisher.publish(
                state.version.version,
                expected_current_version=expected_current_version,
            )
        except Exception:
            raise ListingUpdateError(
                "publish_failed", "listing dataset publication failed"
            ) from None
        return {"version": state.version.version}

    def _release_reservation(self, run_id: str) -> None:
        with self._reservation_guard:
            reservation = self._reservations.pop(run_id, None)
            if reservation is None:
                return
            reservation.state = "released"
            if self._reserved_by_key.get(reservation.idempotency_key) == run_id:
                self._reserved_by_key.pop(reservation.idempotency_key, None)
        reservation.lock.release()
