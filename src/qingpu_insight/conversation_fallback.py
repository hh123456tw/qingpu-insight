from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from qingpu_insight.conversation_models import resolve_conversation_model
from qingpu_insight.conversation_providers import (
    ConversationContext,
    ConversationProviderRegistry,
)
from qingpu_insight.conversation_validation import (
    GroundingValidationError,
    ValidatedChatAnswer,
)
from qingpu_insight.ollama_report_provider import ProviderError

_REPAIR_HINT = "Return valid JSON with only known evidence fact IDs."

_SAFE_REASON_BY_PROVIDER_ERROR = {
    "gemini_timeout": "cloud_timeout",
    "gemini_rate_limited": "cloud_rate_limited",
    "gemini_auth_missing": "cloud_auth_failed",
    "gemini_auth_failed": "cloud_auth_failed",
    "gemini_connection_error": "cloud_unavailable",
    "gemini_unavailable": "cloud_unavailable",
    "gemini_http_error": "cloud_unavailable",
    "gemini_validation_error": "cloud_invalid_response",
    "ollama_timeout": "local_unavailable",
    "ollama_connection_error": "local_unavailable",
    "ollama_http_error": "local_unavailable",
    "ollama_validation_error": "local_unavailable",
}


@dataclass(frozen=True)
class ReplyExecution:
    validated: ValidatedChatAnswer
    actual_provider: str
    actual_model: str
    fallback_reason: str | None


@dataclass(frozen=True)
class _RouteStep:
    provider: str
    model: str
    attempts: int


class ConversationFallbackExecutor:
    def __init__(
        self,
        *,
        provider_registry: ConversationProviderRegistry,
        validator: Callable[..., ValidatedChatAnswer],
    ) -> None:
        self._provider_registry = provider_registry
        self._validator = validator

    def execute(
        self,
        *,
        requested_model: str,
        question: str,
        context: ConversationContext,
        available_fact_ids: set[str],
        evidence_revision: int,
    ) -> ReplyExecution:
        definition = resolve_conversation_model(requested_model)
        route = self._route(definition.provider, requested_model)
        fallback_reason: str | None = None

        for step in route:
            provider = self._provider_registry.get(step.provider)
            step_reason: str | None = None
            for attempt in range(step.attempts):
                repair_hint = (
                    _REPAIR_HINT
                    if attempt > 0 and step_reason == "cloud_invalid_response"
                    else None
                )
                try:
                    draft = provider.reply(
                        model=step.model,
                        question=question,
                        context=context,
                        repair_hint=repair_hint,
                    )
                    validated = self._validator(
                        draft,
                        available_fact_ids=available_fact_ids,
                        evidence_revision=evidence_revision,
                    )
                except GroundingValidationError:
                    step_reason = self._validation_reason(step.provider)
                    continue
                except ProviderError as error:
                    step_reason = self._safe_reason(step.provider, error.code)
                    continue
                except Exception:
                    step_reason = self._unexpected_reason(step.provider)
                    continue

                return ReplyExecution(
                    validated=validated,
                    actual_provider=step.provider,
                    actual_model=step.model,
                    fallback_reason=(
                        fallback_reason
                        if step.provider != definition.provider
                        else None
                    ),
                )

            fallback_reason = step_reason or self._unexpected_reason(step.provider)

        raise ProviderError("all_conversation_providers_unavailable")

    @staticmethod
    def _route(requested_provider: str, requested_model: str) -> tuple[_RouteStep, ...]:
        if requested_provider == "gemini":
            return (
                _RouteStep("gemini", requested_model, 2),
                _RouteStep("ollama", "gemma4:e2b", 1),
                _RouteStep("rule", "rule", 1),
            )
        if requested_provider == "ollama":
            return (
                _RouteStep("ollama", "gemma4:e2b", 1),
                _RouteStep("rule", "rule", 1),
            )
        return (_RouteStep("rule", "rule", 1),)

    @staticmethod
    def _validation_reason(provider: str) -> str:
        if provider == "gemini":
            return "cloud_invalid_response"
        return "local_unavailable"

    @staticmethod
    def _unexpected_reason(provider: str) -> str:
        if provider == "gemini":
            return "cloud_unavailable"
        return "local_unavailable"

    @classmethod
    def _safe_reason(cls, provider: str, code: str) -> str:
        return _SAFE_REASON_BY_PROVIDER_ERROR.get(
            code,
            cls._unexpected_reason(provider),
        )
