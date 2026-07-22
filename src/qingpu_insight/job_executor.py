from __future__ import annotations

import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from qingpu_insight.jobs import JobService


class LocalJobExecutor:
    def __init__(self, job_service: JobService, max_workers: int = 1) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._job_service = job_service
        self._submitted: list[str] = []

    @property
    def submitted(self) -> list[str]:
        return list(self._submitted)

    def submit(self, run_id: str, callable: Callable[[], None]) -> None:
        self._submitted.append(run_id)
        self._executor.submit(self._wrap(run_id, callable))

    def _wrap(self, run_id: str, callable: Callable[[], None]) -> Callable[[], None]:
        def wrapper() -> None:
            try:
                self._job_service.start(run_id)
                callable()
            except Exception:
                traceback.print_exc()
                try:
                    self._job_service.fail(run_id)
                except Exception:
                    pass

        return wrapper
