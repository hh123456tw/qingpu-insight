from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol


class TransientStepError(Exception):
    def __init__(self, message: str, delay_seconds: float = 60.0) -> None:
        self.delay_seconds = delay_seconds
        super().__init__(message)


@dataclass(frozen=True)
class PipelineContext:
    run_id: str
    working_dir: Path
    params: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class StepResult:
    name: str
    status: Literal["succeeded", "skipped", "failed"]
    output: dict[str, object] = field(default_factory=dict)
    error_code: str | None = None


class PipelineStep(Protocol):
    name: str
    required: bool
    max_attempts: int

    def run(self, context: PipelineContext) -> StepResult: ...


@dataclass(frozen=True)
class PipelineResult:
    status: Literal["succeeded", "failed", "stopped"]
    step_results: list[StepResult]
    output: dict[str, object] = field(default_factory=dict)


class Clock(Protocol):
    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class RealClock:
    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def exponential_delay(
    attempt: int, base_seconds: float = 60.0, max_seconds: float = 3600.0,
) -> float:
    return min(base_seconds * 2 ** (attempt - 1), max_seconds)


class PipelineRunner:
    def __init__(
        self, steps: list[PipelineStep], clock: Clock | None = None,
    ) -> None:
        self._steps = list(steps)
        self._clock = clock or RealClock()

    def run(self, context: PipelineContext) -> PipelineResult:
        results: list[StepResult] = []
        for step in self._steps:
            last_error: str | None = None
            step_result: StepResult | None = None
            for attempt in range(1, step.max_attempts + 1):
                try:
                    step_result = step.run(context)
                except TransientStepError as exc:
                    last_error = exc.error_code if hasattr(exc, "error_code") else "transient"
                    if attempt < step.max_attempts:
                        delay = compute_delay_for_step(step, attempt)
                        self._clock.sleep(delay)
                    continue
                except Exception as exc:
                    step_result = StepResult(
                        name=step.name,
                        status="failed",
                        output={},
                        error_code=getattr(exc, "error_code", None) or type(exc).__name__,
                    )
                    break
                break

            if step_result is None:
                step_result = StepResult(
                    name=step.name, status="failed",
                    output={}, error_code=last_error or "max_attempts",
                )

            results.append(step_result)

            if step_result.status == "failed" and step.required:
                return PipelineResult(status="failed", step_results=results)

        return PipelineResult(status="succeeded", step_results=results)


def compute_delay_for_step(step: PipelineStep, attempt: int) -> float:
    return exponential_delay(attempt)
