from __future__ import annotations

from pathlib import Path
from typing import Any

from qingpu_insight.provider_ops import BenchmarkRequest, ProviderOpsService


class _MockProvider:
    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    def generate(self, pack: Any, repair_codes: tuple[str, ...] = ()) -> Any:
        if self._fail:
            raise RuntimeError("provider error with AIza12345SecretKey")
        from qingpu_insight.report_contracts import BuyerReportDraft, ReportClaim
        return type("Result", (), {
            "provider": "mock", "model": "mock",
            "draft": BuyerReportDraft(
                summary=ReportClaim(text="ok", fact_ids=("f1",), numeric_fact_ids=()),
                advantages=(ReportClaim(text="a", fact_ids=("f1",), numeric_fact_ids=()),),
                risks=(ReportClaim(text="r", fact_ids=("f1",), numeric_fact_ids=()),),
                negotiation=(ReportClaim(text="n", fact_ids=("f1",), numeric_fact_ids=()),),
                limitations=(),
            ),
            "latency_ms": 1.0,
        })()


class _MockRuleProvider:
    def generate(self, pack: Any, repair_codes: tuple[str, ...] = ()) -> Any:
        from qingpu_insight.report_contracts import BuyerReportDraft, ReportClaim
        return type("Result", (), {
            "provider": "rule", "model": "rule",
            "draft": BuyerReportDraft(
                summary=ReportClaim(text="ok", fact_ids=("f1",), numeric_fact_ids=()),
                advantages=(ReportClaim(text="a", fact_ids=("f1",), numeric_fact_ids=()),),
                risks=(ReportClaim(text="r", fact_ids=("f1",), numeric_fact_ids=()),),
                negotiation=(ReportClaim(text="n", fact_ids=("f1",), numeric_fact_ids=()),),
                limitations=(),
            ),
            "latency_ms": 1.0,
        })()


def _factory(name: str) -> _MockProvider | None:
    if name == "ollama":
        return _MockProvider()
    if name == "gemini":
        return _MockProvider()
    return None


def test_provider_status() -> None:
    svc = ProviderOpsService(
        rule_provider=_MockRuleProvider(),
        provider_factory=_factory,
        env={"QINGPU_OLLAMA_MODEL": "llama3", "QINGPU_GEMINI_API_KEY": "key"},
    )
    status = svc.status()
    names = {s["name"] for s in status}
    assert names == {"rule", "ollama", "gemini"}
    for s in status:
        assert s["ready"] is True


def test_provider_status_gemini_unconfigured() -> None:
    svc = ProviderOpsService(
        rule_provider=_MockRuleProvider(),
        provider_factory=_factory,
        env={},
    )
    status = svc.status()
    gemini = next(s for s in status if s["name"] == "gemini")
    assert gemini["ready"] is False


def test_smoke_rule_succeeds() -> None:
    svc = ProviderOpsService(
        rule_provider=_MockRuleProvider(),
        provider_factory=_factory,
        env={},
    )
    sub = svc.submit_smoke("rule")
    result = svc.execute_smoke(sub["run_id"], "rule")
    assert result["status"] == "succeeded"
    assert result["latency_ms"] > 0


def test_smoke_ollama_succeeds() -> None:
    svc = ProviderOpsService(
        rule_provider=_MockRuleProvider(),
        provider_factory=_factory,
        env={"QINGPU_OLLAMA_MODEL": "llama3"},
    )
    sub = svc.submit_smoke("ollama")
    result = svc.execute_smoke(sub["run_id"], "ollama")
    assert result["status"] == "succeeded"


class _BenchmarkRunner:
    def __init__(self, result: dict | None = None) -> None:
        self._result = result or {
            "schema_success": 1.0,
            "fact_accuracy": 0.95,
            "required_section_success": 1.0,
            "p50_latency_ms": 150.0,
            "p95_latency_ms": 300.0,
            "reports": {"json": "benchmark_results.json", "markdown": "benchmark_results.md"},
        }

    def run(self, provider: str, model: str, cases: list, output_dir) -> dict:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "benchmark_results.json").write_text('{"result": "ok"}')
        (output_dir / "benchmark_results.md").write_text("# Benchmark")
        return dict(self._result)


def test_benchmark_request_cannot_choose_case_or_output_path() -> None:
    from qingpu_insight.provider_ops import BenchmarkRequest

    req = BenchmarkRequest(provider="ollama", model="llama3")
    assert req.provider == "ollama"
    assert req.model == "llama3"
    assert not hasattr(req, "cases")
    assert not hasattr(req, "output_path")


def test_benchmark_uses_fixed_cases_and_run_directory(tmp_path, monkeypatch) -> None:
    from qingpu_insight.provider_ops import BenchmarkRequest, ProviderOpsService

    monkeypatch.chdir(tmp_path)
    cases_dir = tmp_path / "benchmarks"
    cases_dir.mkdir(parents=True, exist_ok=True)
    cases_path = cases_dir / "m44_cases.json"
    import json as _json
    cases_path.write_text(_json.dumps([{
        "pack_id": "test-pack", "dataset_version": "v1",
        "generated_at": "2026-07-24T00:00:00+00:00",
        "candidates": [{"candidate_id": "c1", "listing_type": "sale"}],
        "facts": [{
            "fact_id": "f1", "kind": "asking_price", "label": "Price",
            "value": "10000000", "unit": "twd", "source_type": "listing",
            "source_version": "v1", "observed_at": "2026-07-01T00:00:00+00:00",
        }],
        "limitations": [],
    }]))

    class _Rule:
        def generate(self, pack, repair_codes=()):
            return type("R", (), {"provider": "rule", "model": "rule"})()

    _mock_provider = type("P", (), {"generate": lambda s, p, repair_codes=(): None})
    svc = ProviderOpsService(
        rule_provider=_Rule(),
        provider_factory=lambda n: _mock_provider(),
        env={"QINGPU_OLLAMA_MODEL": "llama3"},
    )
    runner = _BenchmarkRunner()
    svc.set_benchmark_runner(runner)
    req = BenchmarkRequest(provider="ollama", model="llama3")
    sub = svc.submit_benchmark(req)
    result = svc.execute_benchmark(sub["run_id"], req)
    assert result["status"] == "succeeded"
    assert result["schema_success"] == 1.0
    assert result["fact_accuracy"] == 0.95
    assert result["provider"] == "ollama"
    assert result["model"] == "llama3"
    assert "reports" in result


def test_execute_benchmark_passes_provider_and_exact_model_to_runner(
    tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    cases = Path("benchmarks")
    cases.mkdir()
    (cases / "m44_cases.json").write_text("[]", encoding="utf-8")
    calls = []

    class Runner:
        def run(self, provider, model, evidence_cases, output_dir):
            calls.append((provider, model, evidence_cases, output_dir))
            return {"case_count": 0, "models": []}

    service = ProviderOpsService(
        rule_provider=object(),
        provider_factory=lambda _name: None,
        env={},
    )
    service.set_benchmark_runner(Runner())
    request = BenchmarkRequest(provider="ollama", model="gemma4:e2b")

    result = service.execute_benchmark("run-1", request)

    assert result["status"] == "succeeded"
    assert calls[0][0:3] == ("ollama", "gemma4:e2b", [])
    assert calls[0][3] == Path("outputs/m44-benchmark/run-1")


def test_execute_benchmark_excludes_rental_case(tmp_path, monkeypatch):
    import json

    monkeypatch.chdir(tmp_path)
    Path("benchmarks").mkdir()

    def case(pack_id, listing_type):
        return {
            "pack_id": pack_id,
            "dataset_version": "v1",
            "generated_at": "2026-07-24T00:00:00+00:00",
            "candidates": [{
                "candidate_id": f"c-{pack_id}",
                "listing_type": listing_type,
            }],
            "facts": [],
            "limitations": [],
        }

    Path("benchmarks/m44_cases.json").write_text(
        json.dumps([
            case("sale-case", "sale"),
            case("rental-case", "rental"),
        ]),
        encoding="utf-8",
    )
    observed = []

    class Runner:
        def run(self, provider, model, evidence_cases, output_dir):
            observed.extend(pack.pack_id for pack in evidence_cases)
            return {"case_count": len(evidence_cases), "models": []}

    service = ProviderOpsService(object(), lambda _name: None, {})
    service.set_benchmark_runner(Runner())

    result = service.execute_benchmark(
        "run-filter",
        BenchmarkRequest(provider="gemini", model="gemini-3.5-flash-lite"),
    )

    assert result["status"] == "succeeded"
    assert observed == ["sale-case"]


def test_execute_benchmark_does_not_expose_runner_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("benchmarks").mkdir()
    Path("benchmarks/m44_cases.json").write_text("[]", encoding="utf-8")

    class Runner:
        def run(self, provider, model, evidence_cases, output_dir):
            raise ConnectionError("http://private-host/?key=secret-value")

    service = ProviderOpsService(object(), lambda _name: None, {})
    service.set_benchmark_runner(Runner())

    result = service.execute_benchmark(
        "run-2",
        BenchmarkRequest(provider="gemini", model="gemma-4-31b-it"),
    )

    assert result["status"] == "failed"
    assert result["error"] == "benchmark_execution_failed"
    assert "private-host" not in repr(result)


def test_provider_smoke_failure_redacts_key() -> None:
    def failing_factory(name: str) -> _MockProvider | None:
        return _MockProvider(fail=True)
    svc = ProviderOpsService(
        rule_provider=_MockRuleProvider(),
        provider_factory=failing_factory,
        env={"QINGPU_GEMINI_API_KEY": "key"},
    )
    sub = svc.submit_smoke("gemini")
    result = svc.execute_smoke(sub["run_id"], "gemini")
    assert result["status"] == "failed"
    assert result["error"] == "***redacted***"
    assert "AIza12345SecretKey" not in result["error"]
