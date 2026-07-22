from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from qingpu_insight.jobs import JobRun, JobService
from qingpu_insight.listing_sources import ListingType
from qingpu_insight.publishing import DatasetVersion, MySQLVersionPublisher


@dataclass(frozen=True)
class ListingUpdateRequest:
    types: tuple[ListingType, ...] = ("sale", "newhouse", "rental")
    max_pages: int = 10
    trigger: str = "manual"


class CaptureRunner(Protocol):
    def capture(self, listing_type: ListingType, max_pages: int) -> None: ...


class ListingUpdateService:
    def __init__(
        self,
        job_service: JobService,
        publisher: MySQLVersionPublisher,
        capture_runner: CaptureRunner | None = None,
        root: Path | None = None,
    ) -> None:
        self._job_service = job_service
        self._publisher = publisher
        self._capture_runner = capture_runner
        self._root = root or Path.cwd()

    def _build_idempotency_key(self, request: ListingUpdateRequest) -> str:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        types_str = "-".join(sorted(request.types))
        return f"listing-update-{today}-{types_str}-p{request.max_pages}"

    def _acquire_lock(self) -> str | None:
        lock_dir = self._root / "data" / "locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / "listing_update.lock"
        try:
            fd = os.open(
                str(lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            os.close(fd)
            return str(lock_path)
        except FileExistsError:
            return None

    def _release_lock(self, lock_path: str | None) -> None:
        if lock_path:
            try:
                os.unlink(lock_path)
            except FileNotFoundError:
                pass

    def submit(self, request: ListingUpdateRequest) -> JobRun:
        idempotency_key = self._build_idempotency_key(request)
        return self._job_service.create(
            "listing_update", idempotency_key, request.trigger,
        )

    def execute(self, run_id: str, request: ListingUpdateRequest) -> JobRun:
        lock_path = self._acquire_lock()
        if lock_path is None:
            raise RuntimeError("listing update already running")
        try:
            self._job_service.start(run_id)
            for lt in request.types:
                if self._capture_runner:
                    self._capture_runner.capture(lt, request.max_pages)
            version = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
            dv = DatasetVersion(
                version=version, run_id=run_id, status="ready",
                summary={"types": list(request.types), "rows": 0},
            )
            self._publisher.stage(dv)
            self._publisher.publish(version)
            result = self._job_service.succeed(run_id)
        except Exception:
            self._job_service.fail(run_id)
            raise
        finally:
            self._release_lock(lock_path)
        return result
