from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from qingpu_insight.llm_benchmark import (
    BenchmarkResult,
    _atomic_write,
    _check_fact_accuracy,
    _check_required_sections,
    _summarize,
    _write_results,
    run_benchmark,
    score_result,
)
from qingpu_insight.report_contracts import (
    BuyerReportDraft,
    EvidenceCandidate,
    EvidenceFact,
    EvidencePack,
    ReportClaim,
)
from qingpu_insight.report_providers import MockReportProvider, ProviderResult

_NOW = datetime.now(UTC).isoformat()
_VER = "v1"

_FACTS = (
    EvidenceFact(
        fact_id="f1", kind="asking_price", label="Asking Price",
        value="15000000", unit="twd", source_type="listing",
        source_version=_VER, observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id="f2", kind="area", label="Area",
        value="30.00", unit="ping", source_type="listing",
        source_version=_VER, observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id="f3", kind="location_evidence", label="Location",
        value="structured_address", unit="method", source_type="listing",
        source_version=_VER, observed_at=_NOW,
    ),
)

_CANDIDATE = EvidenceCandidate(candidate_id="c1", listing_type="sale")

PACK = EvidencePack(
    pack_id="test-pack", dataset_version=_VER, generated_at=_NOW,
    candidates=(_CANDIDATE,), facts=_FACTS, limitations=(),
)

_VALID_CLAIM = ReportClaim(
    text="總價15000000元",
    fact_ids=("f1",),
    numeric_fact_ids=("f1",),
)

_VALID_DRAFT = BuyerReportDraft(
    summary=_VALID_CLAIM,
    advantages=(_VALID_CLAIM,),
    risks=(_VALID_CLAIM,),
    negotiation=(_VALID_CLAIM,),
    limitations=(_VALID_CLAIM,),
)


class TestScoreResult:
    def test_all_valid(self) -> None:
        result = ProviderResult(
            provider="mock", model="mock", draft=_VALID_DRAFT, latency_ms=10,
        )
        br = score_result(result, PACK)
        assert br.schema_success is True
        assert br.fact_accuracy == 1.0
        assert br.required_section_coverage == 1.0
        assert br.fallback_used is False
        assert br.latency_ms == 10
        assert not br.failure_codes

    def test_unknown_fact_id(self) -> None:
        bad = ReportClaim(
            text="測試", fact_ids=("nonexistent",), numeric_fact_ids=(),
        )
        draft = BuyerReportDraft(
            summary=bad, advantages=(bad,), risks=(bad,),
            negotiation=(bad,), limitations=(bad,),
        )
        result = ProviderResult(
            provider="mock", model="mock", draft=draft, latency_ms=0,
        )
        br = score_result(result, PACK)
        assert br.fact_accuracy == 0.0
        assert "unknown_fact_id" in br.failure_codes

    def test_low_coverage(self) -> None:
        empty = ReportClaim(text="only", fact_ids=("f1",), numeric_fact_ids=())
        empty_summary = ReportClaim(text="", fact_ids=("f1",), numeric_fact_ids=())
        low_draft = BuyerReportDraft(
            summary=empty_summary, advantages=(empty,), risks=(empty,),
            negotiation=(empty,), limitations=(empty,),
        )
        result = ProviderResult(
            provider="mock", model="mock", draft=low_draft, latency_ms=0,
        )
        br = score_result(result, PACK)
        assert br.required_section_coverage == 4.0 / 5

    def test_fallback_used_flag(self) -> None:
        result = ProviderResult(
            provider="rule", model="rule", draft=_VALID_DRAFT, latency_ms=0,
        )
        br = score_result(result, PACK, fallback_used=True)
        assert br.fallback_used is True
        assert br.schema_success is True

    def test_sensitive_content_detected(self) -> None:
        bad = ReportClaim(
            text="請聯繫0912345678", fact_ids=("f3",), numeric_fact_ids=(),
        )
        draft = BuyerReportDraft(
            summary=bad, advantages=(bad,), risks=(bad,),
            negotiation=(bad,), limitations=(bad,),
        )
        result = ProviderResult(
            provider="mock", model="mock", draft=draft, latency_ms=0,
        )
        br = score_result(result, PACK)
        assert "sensitive_content" in br.failure_codes

    def test_latency_rounded_to_int(self) -> None:
        result = ProviderResult(
            provider="mock", model="mock", draft=_VALID_DRAFT, latency_ms=42.7,
        )
        br = score_result(result, PACK)
        assert br.latency_ms == 42


class TestCheckFactAccuracy:
    def test_all_facts_valid(self) -> None:
        acc = _check_fact_accuracy(_VALID_DRAFT, PACK)
        assert acc == 1.0

    def test_nonexistent_fact(self) -> None:
        bad = ReportClaim(
            text="bad", fact_ids=("nope",), numeric_fact_ids=(),
        )
        draft = BuyerReportDraft(
            summary=bad, advantages=(bad,), risks=(bad,),
            negotiation=(bad,), limitations=(bad,),
        )
        acc = _check_fact_accuracy(draft, PACK)
        assert acc == 0.0

    def test_empty_pack_returns_1(self) -> None:
        claim = ReportClaim(text="x", fact_ids=("x",), numeric_fact_ids=())
        draft = BuyerReportDraft(
            summary=claim, advantages=(claim,), risks=(claim,),
            negotiation=(claim,), limitations=(claim,),
        )
        empty = EvidencePack(
            pack_id="e", dataset_version=_VER, generated_at=_NOW,
            candidates=(_CANDIDATE,), facts=(), limitations=(),
        )
        acc = _check_fact_accuracy(draft, empty)
        assert acc == 1.0

    def test_partial_accuracy(self) -> None:
        valid = ReportClaim(text="valid f1", fact_ids=("f1",), numeric_fact_ids=())
        invalid = ReportClaim(text="bad x", fact_ids=("x",), numeric_fact_ids=())
        draft = BuyerReportDraft(
            summary=valid, advantages=(invalid,), risks=(valid,),
            negotiation=(valid,), limitations=(valid,),
        )
        acc = _check_fact_accuracy(draft, PACK)
        assert acc == 0.5


class TestCheckRequiredSections:
    def test_all_present(self) -> None:
        cov = _check_required_sections(_VALID_DRAFT)
        assert cov == 1.0

    def test_all_present_returns_1(self) -> None:
        claim = ReportClaim(text="only", fact_ids=("f1",), numeric_fact_ids=())
        draft = BuyerReportDraft(
            summary=claim, advantages=(claim,), risks=(claim,),
            negotiation=(claim,), limitations=(claim,),
        )
        cov = _check_required_sections(draft)
        assert cov == 1.0

    def test_empty_summary_counts_as_missing(self) -> None:
        claim = ReportClaim(text="", fact_ids=("f1",), numeric_fact_ids=())
        draft = BuyerReportDraft(
            summary=claim, advantages=(claim,), risks=(claim,),
            negotiation=(claim,), limitations=(claim,),
        )
        cov = _check_required_sections(draft)
        assert cov == 4.0 / 5


class TestAtomicWrite:
    def test_writes_content(self, tmp_path: Path) -> None:
        path = tmp_path / "test.json"
        _atomic_write(path, '{"a": 1}')
        assert path.read_text(encoding="utf-8") == '{"a": 1}'

    def test_temp_file_removed_after_write(self, tmp_path: Path) -> None:
        path = tmp_path / "output.json"
        _atomic_write(path, "hello")
        tmp_files = list(tmp_path.glob(".*.tmp"))
        assert len(tmp_files) == 0

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "data.json"
        path.write_text("old", encoding="utf-8")
        _atomic_write(path, "new")
        assert path.read_text(encoding="utf-8") == "new"


class TestWriteResults:
    def test_json_output_format(self, tmp_path: Path) -> None:
        results = [
            BenchmarkResult(
                case_id="c1", model="m1", provider="mock",
                schema_success=True, fact_accuracy=1.0,
                required_section_coverage=1.0, fallback_used=False,
                latency_ms=10, failure_codes=(),
            ),
        ]
        json_path, _ = _write_results(results, tmp_path)
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert "results" in data
        assert "summaries" in data
        assert len(data["results"]) == 1
        assert data["results"][0]["model"] == "m1"

    def test_md_output_format(self, tmp_path: Path) -> None:
        results = [
            BenchmarkResult(
                case_id="c1", model="m1", provider="mock",
                schema_success=True, fact_accuracy=1.0,
                required_section_coverage=1.0, fallback_used=False,
                latency_ms=10, failure_codes=(),
            ),
        ]
        _, md_path = _write_results(results, tmp_path)
        md = md_path.read_text(encoding="utf-8")
        assert "# LLM Benchmark Results" in md
        assert "| Model | Cases" in md
        assert "m1" in md

    def test_no_secrets_in_output(self, tmp_path: Path) -> None:
        results = [
            BenchmarkResult(
                case_id="c1", model="m1", provider="mock",
                schema_success=True, fact_accuracy=1.0,
                required_section_coverage=1.0, fallback_used=False,
                latency_ms=10, failure_codes=(),
            ),
        ]
        json_path, _ = _write_results(results, tmp_path)
        text = json_path.read_text(encoding="utf-8")
        assert "api_key" not in text
        assert "prompt" not in text
        assert "secret" not in text
        assert "password" not in text

    def test_failure_codes_in_md(self, tmp_path: Path) -> None:
        results = [
            BenchmarkResult(
                case_id="c1", model="m1", provider="mock",
                schema_success=False, fact_accuracy=0.5,
                required_section_coverage=0.8, fallback_used=True,
                latency_ms=5, failure_codes=("unknown_fact_id",),
            ),
        ]
        _, md_path = _write_results(results, tmp_path)
        md = md_path.read_text(encoding="utf-8")
        assert "unknown_fact_id" in md


class TestSummarize:
    def test_single_model_single_result(self) -> None:
        results = [
            BenchmarkResult(
                case_id="c1", model="m1", provider="mock",
                schema_success=True, fact_accuracy=1.0,
                required_section_coverage=1.0, fallback_used=False,
                latency_ms=10, failure_codes=(),
            ),
        ]
        summaries = _summarize(results)
        assert len(summaries) == 1
        s = summaries[0]
        assert s.model == "m1"
        assert s.case_count == 1
        assert s.success_rate == 1.0
        assert s.avg_fact_accuracy == 1.0
        assert s.p50_latency == 10

    def test_multiple_models(self) -> None:
        results = [
            BenchmarkResult(
                case_id="c1", model="a", provider="mock",
                schema_success=True, fact_accuracy=1.0,
                required_section_coverage=1.0, fallback_used=False,
                latency_ms=10, failure_codes=(),
            ),
            BenchmarkResult(
                case_id="c1", model="b", provider="mock",
                schema_success=False, fact_accuracy=0.5,
                required_section_coverage=0.5, fallback_used=True,
                latency_ms=20, failure_codes=("x",),
            ),
        ]
        summaries = _summarize(results)
        assert len(summaries) == 2

    def test_failure_codes_aggregated(self) -> None:
        results = [
            BenchmarkResult(
                case_id="c1", model="m", provider="mock",
                schema_success=False, fact_accuracy=0.0,
                required_section_coverage=0.0, fallback_used=False,
                latency_ms=0, failure_codes=("a", "b"),
            ),
            BenchmarkResult(
                case_id="c2", model="m", provider="mock",
                schema_success=False, fact_accuracy=0.0,
                required_section_coverage=0.0, fallback_used=False,
                latency_ms=0, failure_codes=("b", "c"),
            ),
        ]
        summaries = _summarize(results)
        assert set(summaries[0].failure_codes) == {"a", "b", "c"}


class TestRunBenchmark:
    def test_with_fake_providers(self, tmp_path: Path) -> None:
        class ModelProvider:
            def __init__(self, name: str):
                self._name = name
            def generate(self, pack, repair_codes=()):
                return ProviderResult(
                    provider=self._name, model=self._name,
                    draft=_VALID_DRAFT, latency_ms=5,
                )

        results, summaries = run_benchmark(
            [PACK], {"m1": ModelProvider("m1"), "m2": ModelProvider("m2")}, tmp_path,
        )
        assert len(results) == 2
        assert len(summaries) == 2

    def test_output_files_created(self, tmp_path: Path) -> None:
        provider = MockReportProvider(draft=_VALID_DRAFT)
        run_benchmark([PACK], {"mock": provider}, tmp_path)
        assert (tmp_path / "benchmark_results.json").exists()
        assert (tmp_path / "benchmark_results.md").exists()

    def test_provider_error_recorded(self, tmp_path: Path) -> None:
        class FailingProvider:
            def generate(self, pack, repair_codes=()):
                raise RuntimeError("fail")

        results, _ = run_benchmark(
            [PACK], {"fail": FailingProvider()}, tmp_path,
        )
        assert len(results) == 1
        assert not results[0].success
        assert results[0].error_code == "provider_failed"
