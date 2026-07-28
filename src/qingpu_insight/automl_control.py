from __future__ import annotations

import threading


class AutoMLControlRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}

    def register(self, run_id: str) -> None:
        with self._lock:
            self._events[run_id] = threading.Event()

    def request_stop(self, run_id: str) -> bool:
        with self._lock:
            event = self._events.get(run_id)
            if event is None:
                return False
            event.set()
            return True

    def should_stop(self, run_id: str) -> bool:
        with self._lock:
            event = self._events.get(run_id)
            if event is None:
                return False
            return event.is_set()

    def unregister(self, run_id: str) -> None:
        with self._lock:
            self._events.pop(run_id, None)
