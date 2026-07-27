from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from qingpu_insight.gemini_report_provider import GeminiReportProvider
from qingpu_insight.llm_benchmark import run_benchmark
from qingpu_insight.ollama_report_provider import OllamaReportProvider
from qingpu_insight.report_contracts import EvidencePack


class ConfiguredWebBenchmarkRunner:
    def __init__(
        self,
        *,
        ollama_base_url_getter: Callable[[], str],
        gemini_api_key_getter: Callable[[], str],
        ollama_factory: Callable[[str, str], object] | None = None,
        gemini_factory: Callable[[str, str], object] | None = None,
        benchmark: Callable[..., tuple[list[Any], list[Any]]] = run_benchmark,
    ) -> None:
        self._ollama_base_url_getter = ollama_base_url_getter
        self._gemini_api_key_getter = gemini_api_key_getter
        self._ollama_factory = ollama_factory or (
            lambda base_url, model: OllamaReportProvider(
                base_url=base_url,
                model=model,
            )
        )
        self._gemini_factory = gemini_factory or (
            lambda api_key, model: GeminiReportProvider(
                api_key=api_key,
                model=model,
            )
        )
        self._benchmark = benchmark

    def run(
        self,
        provider: str,
        model: str,
        cases: list[EvidencePack],
        output_dir: Path,
    ) -> dict[str, Any]:
        if provider == "ollama":
            selected = self._ollama_factory(
                self._ollama_base_url_getter(),
                model,
            )
        elif provider == "gemini":
            api_key = self._gemini_api_key_getter()
            if not api_key:
                raise ValueError("gemini_api_key_not_configured")
            selected = self._gemini_factory(api_key, model)
        else:
            raise ValueError("unsupported_benchmark_provider")
        provider_id = f"{provider}:{model}"
        results, summaries = self._benchmark(
            cases,
            {provider_id: selected},
            output_dir,
            requested_provider=provider,
            requested_model=model,
        )
        summary_dicts = [
            summary
            if isinstance(summary, dict)
            else asdict(summary)
            if is_dataclass(summary)
            else raise_type_error(summary)
            for summary in summaries
        ]
        count = len(results)
        first_summary = summary_dicts[0] if summary_dicts else {}
        return {
            "case_count": count,
            "schema_success": (
                sum(bool(result.schema_success) for result in results) / count
                if count else 0.0
            ),
            "fact_accuracy": (
                sum(result.fact_accuracy for result in results) / count
                if count else 0.0
            ),
            "required_section_success": (
                sum(result.required_section_coverage for result in results) / count
                if count else 0.0
            ),
            "p50_latency_ms": first_summary.get("p50_latency"),
            "p95_latency_ms": first_summary.get("p95_latency"),
            "models": summary_dicts,
        }


def raise_type_error(value: object) -> dict[str, Any]:
    raise TypeError(f"unsupported_benchmark_summary:{type(value).__name__}")
