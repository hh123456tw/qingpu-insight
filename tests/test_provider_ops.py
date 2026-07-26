from __future__ import annotations

from typing import Any

from qingpu_insight.provider_ops import ProviderOpsService


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
