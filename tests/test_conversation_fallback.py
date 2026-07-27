from __future__ import annotations

from dataclasses import dataclass

import pytest

from qingpu_insight.conversation_evidence import EvidenceFact
from qingpu_insight.conversation_fallback import ConversationFallbackExecutor
from qingpu_insight.conversation_providers import (
    ConversationContext,
    ConversationProviderRegistry,
)
from qingpu_insight.conversation_validation import (
    ChatAnswerDraft,
    PropertyClaim,
    validate_chat_answer,
)
from qingpu_insight.ollama_report_provider import ProviderError


_FACT = EvidenceFact(
    id="f1",
    label="開價",
    value="1500 萬元",
    source="591",
    kind="asking_price",
    observed_at="2026-07-27T00:00:00Z",
)
_CONTEXT = ConversationContext(
    rolling_summary=None,
    recent_messages=(),
    evidence_revision=1,
    evidence_facts=(_FACT,),
    limitations=(),
)
_VALID_DRAFT = ChatAnswerDraft(
    answer="依證據整理。",
    property_claims=[PropertyClaim(text="開價為 1500 萬元", fact_ids=["f1"])],
    general_guidance=[],
    suggested_questions=[],
)


@dataclass
class CountingProvider:
    outcomes: list[object]

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def reply(
        self,
        *,
        model: str,
        question: str,
        context: ConversationContext,
        repair_hint: str | None = None,
    ) -> ChatAnswerDraft:
        self.calls.append((model, repair_hint))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, ChatAnswerDraft)
        return outcome


def _executor(
    google: CountingProvider,
    ollama: CountingProvider,
    rule: CountingProvider,
) -> ConversationFallbackExecutor:
    registry = ConversationProviderRegistry()
    registry.register("gemini", google)
    registry.register("ollama", ollama)
    registry.register("rule", rule)
    return ConversationFallbackExecutor(
        provider_registry=registry,
        validator=validate_chat_answer,
    )


def _execute(
    executor: ConversationFallbackExecutor,
    model: str = "gemini-3.5-flash-lite",
):
    return executor.execute(
        requested_model=model,
        question="值得買嗎？",
        context=_CONTEXT,
        available_fact_ids={"f1"},
        evidence_revision=1,
    )


def test_google_success_does_not_call_fallbacks():
    google = CountingProvider([_VALID_DRAFT])
    ollama = CountingProvider([AssertionError("ollama must not run")])
    rule = CountingProvider([AssertionError("rule must not run")])

    result = _execute(_executor(google, ollama, rule))

    assert result.actual_provider == "gemini"
    assert result.actual_model == "gemini-3.5-flash-lite"
    assert result.fallback_reason is None
    assert len(google.calls) == 1
    assert ollama.calls == []
    assert rule.calls == []


def test_google_retries_once_then_uses_local():
    google = CountingProvider(
        [ProviderError("gemini_timeout"), ProviderError("gemini_timeout")]
    )
    ollama = CountingProvider([_VALID_DRAFT])
    rule = CountingProvider([AssertionError("rule must not run")])

    result = _execute(_executor(google, ollama, rule), "gemma-4-31b-it")

    assert google.calls == [
        ("gemma-4-31b-it", None),
        ("gemma-4-31b-it", None),
    ]
    assert result.actual_provider == "ollama"
    assert result.actual_model == "gemma4:e2b"
    assert result.fallback_reason == "cloud_timeout"


def test_google_retry_success_does_not_report_provider_fallback():
    google = CountingProvider([ProviderError("gemini_unavailable"), _VALID_DRAFT])
    ollama = CountingProvider([AssertionError("ollama must not run")])
    rule = CountingProvider([AssertionError("rule must not run")])

    result = _execute(_executor(google, ollama, rule))

    assert len(google.calls) == 2
    assert result.actual_provider == "gemini"
    assert result.fallback_reason is None


def test_grounding_failure_uses_repair_hint_on_second_google_attempt():
    invalid = ChatAnswerDraft(
        answer="unsupported",
        property_claims=[PropertyClaim(text="未知價格", fact_ids=["unknown"])],
        general_guidance=[],
        suggested_questions=[],
    )
    google = CountingProvider([invalid, _VALID_DRAFT])
    ollama = CountingProvider([AssertionError("ollama must not run")])
    rule = CountingProvider([AssertionError("rule must not run")])

    result = _execute(_executor(google, ollama, rule))

    assert result.actual_provider == "gemini"
    assert google.calls[0][1] is None
    assert google.calls[1][1] == (
        "Return valid JSON with only known evidence fact IDs."
    )


@pytest.mark.parametrize(
    ("provider_code", "safe_reason"),
    [
        ("gemini_rate_limited", "cloud_rate_limited"),
        ("gemini_auth_failed", "cloud_auth_failed"),
        ("gemini_connection_error", "cloud_unavailable"),
        ("gemini_validation_error", "cloud_invalid_response"),
    ],
)
def test_google_errors_map_to_safe_fallback_reasons(provider_code, safe_reason):
    google = CountingProvider([ProviderError(provider_code), ProviderError(provider_code)])
    ollama = CountingProvider([_VALID_DRAFT])
    rule = CountingProvider([AssertionError("rule must not run")])

    result = _execute(_executor(google, ollama, rule))

    assert result.fallback_reason == safe_reason
    assert provider_code not in (result.fallback_reason or "")


def test_local_failure_uses_rule_and_reports_local_unavailable():
    google = CountingProvider(
        [
            ProviderError("gemini_unavailable"),
            ProviderError("gemini_unavailable"),
        ]
    )
    ollama = CountingProvider([ProviderError("ollama_connection_error")])
    rule = CountingProvider([_VALID_DRAFT])

    result = _execute(_executor(google, ollama, rule))

    assert result.actual_provider == "rule"
    assert result.actual_model == "rule"
    assert result.fallback_reason == "local_unavailable"


def test_direct_ollama_uses_one_attempt_then_rule():
    google = CountingProvider([AssertionError("google must not run")])
    ollama = CountingProvider([ProviderError("ollama_timeout")])
    rule = CountingProvider([_VALID_DRAFT])

    result = _execute(_executor(google, ollama, rule), "gemma4:e2b")

    assert google.calls == []
    assert len(ollama.calls) == 1
    assert result.actual_provider == "rule"
    assert result.fallback_reason == "local_unavailable"


def test_direct_rule_calls_only_rule():
    google = CountingProvider([AssertionError("google must not run")])
    ollama = CountingProvider([AssertionError("ollama must not run")])
    rule = CountingProvider([_VALID_DRAFT])

    result = _execute(_executor(google, ollama, rule), "rule")

    assert google.calls == []
    assert ollama.calls == []
    assert len(rule.calls) == 1
    assert result.actual_provider == "rule"
    assert result.fallback_reason is None


def test_all_providers_fail_with_safe_terminal_code():
    google = CountingProvider(
        [ProviderError("gemini_timeout"), ProviderError("gemini_timeout")]
    )
    ollama = CountingProvider([ProviderError("ollama_timeout")])
    rule = CountingProvider([RuntimeError("secret raw failure")])

    with pytest.raises(ProviderError) as error:
        _execute(_executor(google, ollama, rule))

    assert error.value.code == "all_conversation_providers_unavailable"
    assert "secret raw failure" not in str(error.value)
