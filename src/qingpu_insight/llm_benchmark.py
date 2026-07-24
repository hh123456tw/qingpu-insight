from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from qingpu_insight.report_contracts import BuyerReportDraft, EvidencePack, ReportClaim
from qingpu_insight.report_providers import ProviderResult, ReportProvider
from qingpu_insight.report_validation import validate_report

REQUIRED_SECTIONS = frozenset({
    "summary", "advantages", "risks", "negotiation", "limitations",
})


@dataclass
class BenchmarkResult:
    case_id: str
    model: str
    provider: str
    schema_success: bool
    fact_accuracy: float
    required_section_coverage: float
    fallback_used: bool
    latency_ms: int
    failure_codes: tuple[str, ...] = ()
    success: bool = True
    error_code: str | None = None


@dataclass
class ModelSummary:
    model: str
    case_count: int
    success_rate: float
    avg_fact_accuracy: float
    avg_coverage: float
    p50_latency: float
    p95_latency: float
    failure_codes: tuple[str, ...] = ()


def _all_claims(draft: BuyerReportDraft) -> list[ReportClaim]:
    claims = [draft.summary]
    claims.extend(draft.advantages)
    claims.extend(draft.risks)
    claims.extend(draft.negotiation)
    claims.extend(draft.limitations)
    return claims


def _check_fact_accuracy(draft: BuyerReportDraft, pack: EvidencePack) -> float:
    valid_ids = {f.fact_id for f in pack.facts}
    if not valid_ids:
        return 1.0
    referenced: set[str] = set()
    for claim in _all_claims(draft):
        referenced.update(claim.fact_ids)
        referenced.update(claim.numeric_fact_ids)
    if not referenced:
        return 0.0
    correct = sum(1 for fid in referenced if fid in valid_ids)
    return correct / len(referenced)


def _check_required_sections(draft: BuyerReportDraft) -> float:
    present = 0
    if draft.summary.text.strip():
        present += 1
    if draft.advantages:
        present += 1
    if draft.risks:
        present += 1
    if draft.negotiation:
        present += 1
    if draft.limitations:
        present += 1
    return present / len(REQUIRED_SECTIONS)


def score_result(
    result: ProviderResult, pack: EvidencePack, fallback_used: bool = False,
) -> BenchmarkResult:
    draft = result.draft
    try:
        BuyerReportDraft.model_validate(draft.model_dump())
        schema_success = True
    except Exception:
        schema_success = False
    validation = validate_report(draft, pack)
    fact_accuracy = _check_fact_accuracy(draft, pack)
    coverage = _check_required_sections(draft)
    failure_codes = tuple(i.code for i in validation.issues)
    return BenchmarkResult(
        case_id=pack.pack_id,
        model=result.model,
        provider=result.provider,
        schema_success=schema_success,
        fact_accuracy=fact_accuracy,
        required_section_coverage=coverage,
        fallback_used=fallback_used,
        latency_ms=int(result.latency_ms),
        failure_codes=failure_codes,
    )


def _percentile(sorted_values: list[float], p: int) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    n = len(sorted_values)
    k = (p / 100.0) * (n - 1)
    f = int(k)
    c = k - f
    if f + 1 < n:
        return sorted_values[f] * (1 - c) + sorted_values[f + 1] * c
    return sorted_values[-1]


def _summarize(results: list[BenchmarkResult]) -> list[ModelSummary]:
    by_model: dict[str, list[BenchmarkResult]] = {}
    for r in results:
        by_model.setdefault(r.model, []).append(r)
    summaries: list[ModelSummary] = []
    for model, model_results in sorted(by_model.items()):
        n = len(model_results)
        successes = sum(
            1 for r in model_results
            if r.schema_success and not r.failure_codes
        )
        latencies = sorted(r.latency_ms for r in model_results)
        all_codes: list[str] = []
        for r in model_results:
            all_codes.extend(r.failure_codes)
        failure_codes = tuple(sorted(set(all_codes)))[:10]
        summaries.append(ModelSummary(
            model=model,
            case_count=n,
            success_rate=successes / n if n else 0.0,
            avg_fact_accuracy=(
                sum(r.fact_accuracy for r in model_results) / n if n else 0.0
            ),
            avg_coverage=(
                sum(r.required_section_coverage for r in model_results) / n if n else 0.0
            ),
            p50_latency=_percentile(latencies, 50),
            p95_latency=_percentile(latencies, 95),
            failure_codes=failure_codes,
        ))
    return summaries


def _atomic_write(path: Path, content: str) -> None:
    encoding = "utf-8"
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding=encoding, dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
            tmp = Path(f.name)
        tmp.replace(path)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink(missing_ok=True)


def _write_results(
    results: list[BenchmarkResult], output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "benchmark_results.json"
    md_path = output_dir / "benchmark_results.md"
    summaries = _summarize(results)
    json_data = {
        "results": [
            {
                "case_id": r.case_id,
                "model": r.model,
                "provider": r.provider,
                "schema_success": r.schema_success,
                "fact_accuracy": r.fact_accuracy,
                "required_section_coverage": r.required_section_coverage,
                "fallback_used": r.fallback_used,
                "latency_ms": r.latency_ms,
                "failure_codes": list(r.failure_codes),
            }
            for r in results
        ],
        "summaries": [
            {
                "model": s.model,
                "case_count": s.case_count,
                "success_rate": s.success_rate,
                "avg_fact_accuracy": s.avg_fact_accuracy,
                "avg_coverage": s.avg_coverage,
                "p50_latency": s.p50_latency,
                "p95_latency": s.p95_latency,
                "failure_codes": list(s.failure_codes),
            }
            for s in summaries
        ],
    }
    _atomic_write(json_path, json.dumps(json_data, ensure_ascii=False, indent=2))
    md_lines = [
        "# LLM Benchmark Results",
        "",
        "| Model | Cases | Success% | FactAcc | Coverage | P50 Lat | P95 Lat | Fail Codes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        fc = ", ".join(s.failure_codes) if s.failure_codes else "-"
        md_lines.append(
            f"| {s.model} | {s.case_count} | {s.success_rate:.1%} | "
            f"{s.avg_fact_accuracy:.2f} | {s.avg_coverage:.2f} | "
            f"{s.p50_latency:.0f}ms | {s.p95_latency:.0f}ms | {fc} |"
        )
    md_lines.append("")
    _atomic_write(md_path, "\n".join(md_lines))
    return json_path, md_path


def run_benchmark(
    cases: list[EvidencePack],
    providers: dict[str, ReportProvider],
    output_dir: Path,
) -> tuple[list[BenchmarkResult], list[ModelSummary]]:
    import time

    results: list[BenchmarkResult] = []
    for pack in cases:
        for provider_name, provider in providers.items():
            fallback_used = False
            start = time.perf_counter()
            try:
                result = provider.generate(pack)
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                br = score_result(result, pack, fallback_used)
            except Exception:
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                br = BenchmarkResult(
                    case_id=pack.pack_id,
                    provider=provider_name,
                    model=getattr(provider, 'model', provider_name),
                    schema_success=False,
                    fact_accuracy=0.0,
                    required_section_coverage=0.0,
                    fallback_used=fallback_used,
                    latency_ms=elapsed_ms,
                    failure_codes=(),
                    success=False,
                    error_code="provider_failed",
                )
            results.append(br)
    summaries = _summarize(results)
    _write_results(results, output_dir)
    return results, summaries
