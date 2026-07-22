from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock

from qingpu_insight.jobs import InvalidJobTransition, JobService, redact_job_message

logger = logging.getLogger(__name__)


class LocalJobExecutor:
    def __init__(self, job_service: JobService, max_workers: int = 1) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._job_service = job_service
        self._futures: dict[str, Future[None]] = {}
        self._futures_lock = Lock()

    @property
    def submitted(self) -> list[str]:
        with self._futures_lock:
            return list(self._futures)

    def submit(self, run_id: str, callable: Callable[[], None]) -> Future[None]:
        ready = Event()
        with self._futures_lock:
            if run_id in self._futures:
                raise ValueError(f"run {run_id} is already submitted")
            future = self._executor.submit(self._wrap(run_id, callable, ready))
            self._futures[run_id] = future
            ready.set()
        return future

    def _wrap(
        self,
        run_id: str,
        callable: Callable[[], None],
        ready: Event,
    ) -> Callable[[], None]:
        def wrapper() -> None:
            ready.wait()
            try:
                self._job_service.start(run_id)
                try:
                    callable()
                except InvalidJobTransition:
                    raise
                except Exception as error:
                    message = redact_job_message(str(error))
                    logger.error("job %s failed: %s", run_id, message)
                    current = self._job_service.get(run_id)
                    if current is not None and current.status == "running":
                        self._job_service.fail(
                            run_id, "unhandled_exception", message
                        )
            finally:
                with self._futures_lock:
                    self._futures.pop(run_id, None)

        return wrapper

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)
