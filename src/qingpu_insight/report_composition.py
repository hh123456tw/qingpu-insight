from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from qingpu_insight.evidence import EvidenceBuilder
from qingpu_insight.evidence_repository import MySQLEvidenceRepository
from qingpu_insight.report_providers import ReportProvider, RuleReportProvider
from qingpu_insight.report_repository import MySQLReportRepository
from qingpu_insight.report_service import ReportService
from qingpu_insight.report_validation import validate_report


def create_provider_registry(env: Mapping[str, str]) -> dict[str, ReportProvider]:
    providers: dict[str, ReportProvider] = {"rule": RuleReportProvider()}
    if model := env.get("QINGPU_OLLAMA_MODEL"):
        from qingpu_insight.ollama_report_provider import OllamaReportProvider

        providers["ollama"] = OllamaReportProvider(
            base_url=env.get("QINGPU_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            model=model,
        )
    if key := env.get("QINGPU_GEMINI_API_KEY"):
        model = env.get("QINGPU_GEMINI_MODEL")
        if model:
            from qingpu_insight.gemini_report_provider import GeminiReportProvider

            providers["gemini"] = GeminiReportProvider(api_key=key, model=model)
    return providers


@dataclass(frozen=True)
class ReportRuntime:
    service: ReportService
    repository: MySQLReportRepository
    providers: Mapping[str, ReportProvider]


def create_report_runtime(
    connection_factory,
    root: Path,
    env: Mapping[str, str],
) -> ReportRuntime:
    repository = MySQLReportRepository(connection_factory)
    providers = create_provider_registry(env)
    service = ReportService(
        evidence_builder=EvidenceBuilder(MySQLEvidenceRepository(connection_factory)),
        providers=dict(providers),
        rule_provider=providers["rule"],
        validator=validate_report,
        repository=repository,
    )
    return ReportRuntime(service=service, repository=repository, providers=providers)
