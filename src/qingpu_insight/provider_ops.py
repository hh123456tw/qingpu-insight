from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from qingpu_insight.report_contracts import EvidencePack

_KEY_REDACTED = "***redacted***"


@dataclass(frozen=True)
class BenchmarkRequest:
    provider: Literal["ollama", "gemini"]
    model: str


class BenchmarkRunner(Protocol):
    def run(
        self, model: str, cases: list[EvidencePack], output_dir: Path
    ) -> dict[str, Any]:
        ...


class ProviderOpsService:
    def __init__(
        self,
        rule_provider: object,
        provider_factory: Callable[[str], object | None],
        env: dict[str, str],
    ) -> None:
        self._rule_provider = rule_provider
        self._provider_factory = provider_factory
        self._env = env
        self._benchmark_runner: BenchmarkRunner | None = None

    def set_benchmark_runner(self, runner: BenchmarkRunner | None) -> None:
        self._benchmark_runner = runner

    def status(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        available = bool(self._env.get("QINGPU_OLLAMA_MODEL"))
        result.append({
            "name": "rule",
            "ready": True,
            "note": "always ready",
        })
        result.append({
            "name": "ollama",
            "ready": available,
            "note": "" if available else "model not configured",
        })
        gemini_available = bool(self._env.get("QINGPU_GEMINI_API_KEY"))
        result.append({
            "name": "gemini",
            "ready": gemini_available,
            "note": "" if gemini_available else "api key not configured",
        })
        return result

    def submit_smoke(self, provider: str) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        return {
            "run_id": run_id,
            "provider": provider,
            "status": "pending",
        }

    def execute_smoke(self, run_id: str, provider: str) -> dict[str, Any]:
        if provider == "rule":
            return self._smoke_rule(run_id)
        if provider == "ollama":
            return self._smoke_ollama(run_id)
        if provider == "gemini":
            return self._smoke_gemini(run_id)
        return {"run_id": run_id, "provider": provider, "status": "failed",
                "error": "unknown_provider"}

    def _smoke_rule(self, run_id: str) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            pack = _smoke_pack()
            self._rule_provider.generate(pack)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return {
                "run_id": run_id, "provider": "rule", "status": "succeeded",
                "latency_ms": round(elapsed_ms, 1),
            }
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return {
                "run_id": run_id, "provider": "rule", "status": "failed",
                "latency_ms": round(elapsed_ms, 1),
                "error": str(e),
            }

    def _smoke_ollama(self, run_id: str) -> dict[str, Any]:
        return self._smoke_ai(run_id, "ollama")

    def _smoke_gemini(self, run_id: str) -> dict[str, Any]:
        return self._smoke_ai(run_id, "gemini")

    def _smoke_ai(self, run_id: str, provider_name: str) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            provider = self._provider_factory(provider_name)
            if provider is None:
                return {
                    "run_id": run_id, "provider": provider_name,
                    "status": "failed", "latency_ms": 0,
                    "error": f"{provider_name}_unavailable",
                }
            pack = _smoke_pack()
            provider.generate(pack)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return {
                "run_id": run_id, "provider": provider_name,
                "status": "succeeded", "latency_ms": round(elapsed_ms, 1),
            }
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return {
                "run_id": run_id, "provider": provider_name,
                "status": "failed", "latency_ms": round(elapsed_ms, 1),
                "error": _redacted_error(e),
            }

    def submit_benchmark(self, request: BenchmarkRequest) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        return {
            "run_id": run_id,
            "provider": request.provider,
            "model": request.model,
            "status": "pending",
        }

    def execute_benchmark(
        self, run_id: str, request: BenchmarkRequest,
    ) -> dict[str, Any]:
        runner = self._benchmark_runner
        if runner is None:
            return {
                "run_id": run_id, "provider": request.provider,
                "model": request.model, "status": "failed",
                "error": "benchmark_runner_not_configured",
            }
        provider = self._provider_factory(request.provider)
        if provider is None:
            return {
                "run_id": run_id, "provider": request.provider,
                "model": request.model, "status": "failed",
                "error": f"{request.provider}_unavailable",
            }
        try:
            cases_path = Path("benchmarks/m44_cases.json")
            cases_data = json.loads(cases_path.read_text(encoding="utf-8"))
            cases = [EvidencePack(**c) for c in cases_data]
            output_dir = Path("outputs") / "m44-benchmark" / run_id
            output_dir.mkdir(parents=True, exist_ok=True)
            result = runner.run(request.model, cases, output_dir)
            result["run_id"] = run_id
            result["provider"] = request.provider
            result["model"] = request.model
            result["status"] = "succeeded"
            return result
        except Exception as e:
            return {
                "run_id": run_id, "provider": request.provider,
                "model": request.model, "status": "failed",
                "error": _redacted_error(e),
            }


def _smoke_pack() -> EvidencePack:
    from qingpu_insight.report_contracts import (
        EvidenceCandidate,
        EvidenceFact,
    )

    return EvidencePack(
        pack_id="smoke-pack",
        dataset_version="smoke-v0",
        generated_at="2025-01-01T00:00:00Z",
        candidates=(
            EvidenceCandidate(
                candidate_id="smoke-c1",
                listing_type="sale",
            ),
        ),
        facts=(
            EvidenceFact(
                fact_id="smoke-f1",
                kind="asking_price", label="Asking Price",
                value="10000000", unit="twd",
                source_type="listing", source_version="smoke-v0",
                observed_at="2025-01-01T00:00:00Z",
            ),
            EvidenceFact(
                fact_id="smoke-f2",
                kind="area", label="Area",
                value="25.0", unit="ping",
                source_type="listing", source_version="smoke-v0",
                observed_at="2025-01-01T00:00:00Z",
            ),
        ),
        limitations=("This is a smoke test.",),
    )


def _redacted_error(exc: Exception) -> str:
    msg = str(exc)
    # Redact common API key patterns from error messages
    for pattern in ("AIza", "sk-"):
        if pattern in msg:
            return _KEY_REDACTED
    return msg
