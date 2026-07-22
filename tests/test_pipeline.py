from __future__ import annotations

from pathlib import Path

from qingpu_insight.pipeline import (
    PipelineContext,
    PipelineRunner,
    StepResult,
    TransientStepError,
)


class PassingStep:
    def __init__(self, name: str, calls: list[str], required: bool = True) -> None:
        self.name = name
        self.calls = calls
        self.required = required
        self.max_attempts = 1

    def run(self, context: PipelineContext) -> StepResult:
        self.calls.append(self.name)
        return StepResult(name=self.name, status="succeeded")


class FailingStep:
    def __init__(self, name: str, calls: list[str], required: bool = True) -> None:
        self.name = name
        self.calls = calls
        self.required = required
        self.max_attempts = 1

    def run(self, context: PipelineContext) -> StepResult:
        self.calls.append(self.name)
        return StepResult(name=self.name, status="failed", error_code="test_error")


class TransientFailingStep:
    def __init__(self, name: str, calls: list[str], fail_count: int = 2,
                 max_attempts: int | None = None) -> None:
        self.name = name
        self.calls = calls
        self.fail_count = fail_count
        self.required = True
        self.max_attempts = max_attempts or (fail_count + 1)

    def run(self, context: PipelineContext) -> StepResult:
        self.calls.append(self.name)
        if len([c for c in self.calls if c == self.name]) <= self.fail_count:
            raise TransientStepError("transient", delay_seconds=0)
        return StepResult(name=self.name, status="succeeded")


class FakeClock:
    def __init__(self) -> None:
        self._now: float = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def test_runner_does_not_execute_after_required_failure(tmp_path: Path) -> None:
    calls: list[str] = []
    runner = PipelineRunner(
        [PassingStep("capture", calls), FailingStep("validate", calls),
         PassingStep("publish", calls)],
    )
    result = runner.run(PipelineContext("run-1", tmp_path, {}))
    assert calls == ["capture", "validate"]
    assert result.status == "failed"


def test_runner_skips_non_required_failure(tmp_path: Path) -> None:
    calls: list[str] = []
    runner = PipelineRunner([
        PassingStep("init", calls),
        FailingStep("optional", calls, required=False),
        PassingStep("continue", calls),
    ])
    result = runner.run(PipelineContext("run-1", tmp_path, {}))
    assert calls == ["init", "optional", "continue"]
    assert result.status == "succeeded"


def test_transient_retry_succeeds(tmp_path: Path) -> None:
    calls: list[str] = []
    clock = FakeClock()
    runner = PipelineRunner([TransientFailingStep("flaky", calls, fail_count=2)], clock=clock)
    result = runner.run(PipelineContext("run-1", tmp_path, {}))
    assert result.status == "succeeded"
    assert len(calls) == 3


def test_transient_retry_exhausted(tmp_path: Path) -> None:
    calls: list[str] = []
    clock = FakeClock()
    step = TransientFailingStep("flaky", calls, fail_count=5, max_attempts=3)
    runner = PipelineRunner([step], clock=clock)
    result = runner.run(PipelineContext("run-1", tmp_path, {}))
    assert result.status == "failed"


def test_runner_with_fake_clock_tracks_delays(tmp_path: Path) -> None:
    calls: list[str] = []
    clock = FakeClock()
    runner = PipelineRunner(
        [TransientFailingStep("flaky", calls, fail_count=2)],
        clock=clock,
    )
    result = runner.run(PipelineContext("run-1", tmp_path, {}))
    assert result.status == "succeeded"
    assert clock.sleeps
