from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock

from qingpu_insight.conversation_evidence import EvidenceFact
from qingpu_insight.conversation_fallback import ConversationFallbackExecutor
from qingpu_insight.conversation_import import ConversationImportService
from qingpu_insight.conversation_models import resolve_conversation_model
from qingpu_insight.conversation_providers import (
    ConversationContext,
    ConversationProviderRegistry,
)
from qingpu_insight.conversation_repository import (
    ConversationRecord,
    MySQLConversationRepository,
)
from qingpu_insight.job_executor import LocalJobExecutor
from qingpu_insight.jobs import (
    CONVERSATION_IMPORT,
    CONVERSATION_REFRESH,
    CONVERSATION_REPLY,
    JobService,
)
from qingpu_insight.conversation_validation import GroundingValidationError
from qingpu_insight.ollama_report_provider import ProviderError

logger = logging.getLogger(__name__)


def _facts_from_pack(raw_facts) -> tuple[EvidenceFact, ...]:
    if isinstance(raw_facts, dict):
        return tuple(
            EvidenceFact(
                id=str(key),
                label=str(key),
                value=str(value),
                source="",
            )
            for key, value in raw_facts.items()
            if value is not None
        )
    if isinstance(raw_facts, (list, tuple)):
        return tuple(
            EvidenceFact(
                id=fact.get("id") or fact.get("fact_id", ""),
                label=fact.get("label", ""),
                value=fact.get("value", ""),
                source=fact.get("source", ""),
                observed_at=fact.get("observed_at"),
            )
            for fact in raw_facts
            if (fact.get("id") or fact.get("fact_id"))
        )
    return ()


@dataclass(frozen=True)
class ConversationCommand:
    run_id: str
    conversation_id: str


class ConversationService:
    def __init__(
        self,
        *,
        repository: MySQLConversationRepository,
        import_service: ConversationImportService,
        provider_registry: ConversationProviderRegistry,
        reply_executor: ConversationFallbackExecutor,
        validator: Callable[..., object],
        job_service: JobService,
        executor: LocalJobExecutor,
    ) -> None:
        self._repository = repository
        self._import_service = import_service
        self._provider_registry = provider_registry
        self._reply_executor = reply_executor
        self._validator = validator
        self._job_service = job_service
        self._executor = executor
        self._reply_lock = Lock()
        self._active_replies: set[str] = set()

    def create_conversation(
        self, *, model: str
    ) -> ConversationRecord:
        definition = resolve_conversation_model(model)
        return self._repository.create_conversation(
            provider=definition.provider, model=model
        )

    def start_import(
        self, *, conversation_id: str, raw_url: str, idempotency_key: str
    ) -> ConversationCommand:
        with self._reply_lock:
            submission = self._job_service.create(
                job_type=CONVERSATION_IMPORT,
                idempotency_key=idempotency_key,
                trigger="manual",
                input_version=conversation_id,
            )
            if submission.created:
                conflicting = any(
                    run.run_id != submission.run.run_id
                    and run.input_version == conversation_id
                    for job_type in (
                        CONVERSATION_IMPORT,
                        CONVERSATION_REFRESH,
                        CONVERSATION_REPLY,
                    )
                    for run in self._job_service.list_active(job_type)
                )
                if conflicting:
                    self._job_service.fail(
                        submission.run.run_id,
                        "conversation_busy",
                        "conversation busy",
                    )
                    raise ValueError("conversation busy")
                self._executor.submit(
                    submission.run.run_id,
                    functools.partial(
                        self._run_import,
                        submission.run.run_id,
                        conversation_id,
                        raw_url,
                    ),
                )
        return ConversationCommand(
            run_id=submission.run.run_id,
            conversation_id=conversation_id,
        )

    def start_refresh(
        self, *, conversation_id: str, idempotency_key: str
    ) -> ConversationCommand:
        with self._reply_lock:
            if (
                conversation_id in self._active_replies
                or any(
                    run.input_version == conversation_id
                    for run in self._job_service.list_active(
                        CONVERSATION_REPLY
                    )
                )
            ):
                raise ValueError("conversation busy")
            submission = self._job_service.create(
                job_type=CONVERSATION_REFRESH,
                idempotency_key=idempotency_key,
                trigger="manual",
                input_version=conversation_id,
            )
            if submission.created:
                self._executor.submit(
                    submission.run.run_id,
                    functools.partial(
                        self._run_refresh,
                        submission.run.run_id,
                        conversation_id,
                    ),
                )
        return ConversationCommand(
            run_id=submission.run.run_id,
            conversation_id=conversation_id,
        )

    def start_reply(
        self,
        *,
        conversation_id: str,
        question: str,
        evidence_revision: int,
        idempotency_key: str,
    ) -> ConversationCommand:
        conv = self._repository.get_conversation(conversation_id)
        if not conv:
            raise ValueError(f"conversation {conversation_id} not found")
        if conv.active_evidence_revision != evidence_revision:
            raise ValueError("stale evidence revision")
        if conv.default_provider == "rule":
            raise ValueError("rule provider does not support free-form replies")
        with self._reply_lock:
            if conversation_id in self._active_replies:
                raise ValueError(
                    f"active reply already in progress for conversation"
                    f" {conversation_id}"
                )
            active = self._job_service.list_active(CONVERSATION_REPLY)
            if any(run.input_version == conversation_id for run in active):
                raise ValueError(
                    f"active reply already in progress for conversation"
                    f" {conversation_id}"
                )
            for job_type in (CONVERSATION_IMPORT, CONVERSATION_REFRESH):
                if any(
                    run.input_version == conversation_id
                    for run in self._job_service.list_active(job_type)
                ):
                    raise ValueError("conversation busy")
            submission = self._job_service.create(
                job_type=CONVERSATION_REPLY,
                idempotency_key=idempotency_key,
                trigger="manual",
                input_version=conversation_id,
            )
            if submission.created:
                self._active_replies.add(conversation_id)
                try:
                    self._executor.submit(
                        submission.run.run_id,
                        functools.partial(
                            self._run_reply,
                            submission.run.run_id,
                            conversation_id,
                            question,
                            evidence_revision,
                        ),
                    )
                except Exception:
                    self._active_replies.discard(conversation_id)
                    raise
        return ConversationCommand(
            run_id=submission.run.run_id,
            conversation_id=conversation_id,
        )

    def delete_conversation(
        self, *, conversation_id: str
    ) -> bool:
        for job_type in (
            CONVERSATION_IMPORT,
            CONVERSATION_REFRESH,
            CONVERSATION_REPLY,
        ):
            if any(
                run.input_version == conversation_id
                for run in self._job_service.list_active(job_type)
            ):
                raise ValueError("conversation busy")
        return self._repository.delete_conversation(conversation_id)

    def _run_import(
        self, run_id: str, conversation_id: str, raw_url: str
    ) -> None:
        self._repository.set_status(
            conversation_id=conversation_id,
            status="importing",
        )
        try:
            result = self._import_service.import_initial_listing(
                conversation_id=conversation_id,
                raw_url=raw_url,
                stage_callback=lambda _conversation_id, stage: (
                    self._job_service.progress(
                        run_id,
                        {"stage": stage},
                    )
                ),
            )
            if result.outcome == "needs_attention":
                self._job_service.fail(
                    run_id, "verification_required", "verification required"
                )
                self._job_service.needs_attention(run_id)
            else:
                self._append_initial_summary(
                    conversation_id=conversation_id,
                    evidence_revision=result.evidence_revision,
                )
                self._job_service.succeed(
                    run_id, f"rev{result.evidence_revision}", {}
                )
        except Exception as e:
            self._repository.set_status(
                conversation_id=conversation_id,
                status="failed",
            )
            self._job_service.fail(run_id, "import_failed", str(e))
            raise

    def _run_refresh(
        self, run_id: str, conversation_id: str
    ) -> None:
        self._repository.set_status(
            conversation_id=conversation_id,
            status="importing",
        )
        try:
            result = self._import_service.refresh_listing(
                conversation_id=conversation_id,
                stage_callback=lambda _conversation_id, stage: (
                    self._job_service.progress(
                        run_id,
                        {"stage": stage},
                    )
                ),
            )
            if result.outcome == "needs_attention":
                self._job_service.fail(
                    run_id, "verification_required", "verification required"
                )
                self._job_service.needs_attention(run_id)
            else:
                self._append_initial_summary(
                    conversation_id=conversation_id,
                    evidence_revision=result.evidence_revision,
                )
                self._job_service.succeed(
                    run_id, f"rev{result.evidence_revision}", {}
                )
        except Exception as e:
            self._repository.set_status(
                conversation_id=conversation_id,
                status="failed",
            )
            self._job_service.fail(run_id, "refresh_failed", str(e))
            raise

    def _run_reply(
        self,
        run_id: str,
        conversation_id: str,
        question: str,
        evidence_revision: int,
    ) -> None:
        try:
            self._job_service.progress(
                run_id,
                {"stage": "preparing_evidence"},
            )
            self._repository.append_message(
                conversation_id=conversation_id,
                role="user",
                content=question,
                evidence_revision=None,
                provider=None,
                model=None,
                citations=[],
                fallback_reason=None,
            )
            messages = self._repository.get_messages(
                conversation_id=conversation_id, limit=12
            )

            evidence_facts: tuple[EvidenceFact, ...] = ()
            available_fact_ids: set[str] = set()
            evidence_pack = self._repository.get_evidence_pack(
                conversation_id=conversation_id,
                revision=evidence_revision,
            )
            if evidence_pack is None:
                raise ValueError("evidence revision not found")
            if evidence_pack.facts:
                evidence_facts = _facts_from_pack(evidence_pack.facts)
                available_fact_ids = {f.id for f in evidence_facts}

            conversation = self._repository.get_conversation(conversation_id)
            if conversation is None:
                raise ValueError(f"conversation {conversation_id} not found")
            context = ConversationContext(
                rolling_summary=conversation.rolling_summary,
                recent_messages=tuple(
                    {"role": m.role, "content": m.content}
                    for m in reversed(messages)
                ),
                evidence_revision=evidence_revision,
                evidence_facts=evidence_facts,
                limitations=tuple(evidence_pack.limitations),
            )
            self._job_service.progress(
                run_id,
                {"stage": "asking_provider"},
            )
            execution = self._reply_executor.execute(
                requested_model=conversation.default_model,
                question=question,
                context=context,
                available_fact_ids=available_fact_ids,
                evidence_revision=evidence_revision,
            )
            validated = execution.validated
            self._repository.append_message(
                conversation_id=conversation_id,
                role="assistant",
                content=validated.answer,
                evidence_revision=evidence_revision,
                provider=execution.actual_provider,
                model=execution.actual_model,
                citations=list(validated.citations),
                fallback_reason=execution.fallback_reason,
            )
            self._update_rolling_summary(conversation_id)
            self._job_service.progress(
                run_id,
                {"stage": "ready"},
            )
            self._job_service.succeed(
                run_id, f"rev{evidence_revision}", {}
            )
        except ProviderError as error:
            if error.code != "all_conversation_providers_unavailable":
                raise
            self._job_service.fail(
                run_id,
                "reply_providers_unavailable",
                "all conversation providers unavailable",
            )
        except Exception as e:
            self._job_service.fail(run_id, "reply_failed", str(e))
            raise
        finally:
            with self._reply_lock:
                self._active_replies.discard(conversation_id)

    def _append_initial_summary(
        self,
        *,
        conversation_id: str,
        evidence_revision: int,
    ) -> None:
        conversation = self._repository.get_conversation(conversation_id)
        if conversation is None:
            return
        evidence_pack = self._repository.get_evidence_pack(
            conversation_id=conversation_id,
            revision=evidence_revision,
        )
        if evidence_pack is None:
            return
        facts = _facts_from_pack(evidence_pack.facts)
        context = ConversationContext(
            rolling_summary=None,
            recent_messages=(),
            evidence_revision=evidence_revision,
            evidence_facts=facts,
            limitations=tuple(evidence_pack.limitations),
        )
        rule_provider = self._provider_registry.get("rule")
        try:
            draft = rule_provider.reply(
                model="rule",
                question="請根據現有證據，先提供精簡的物件分析。",
                context=context,
            )
            validated = self._validator(
                draft,
                available_fact_ids={fact.id for fact in facts},
                evidence_revision=evidence_revision,
            )
        except GroundingValidationError:
            return
        self._repository.append_message(
            conversation_id=conversation_id,
            role="assistant",
            content=validated.answer,
            evidence_revision=evidence_revision,
            provider="rule",
            model="rule",
            citations=list(validated.citations),
            fallback_reason=None,
        )

    def _update_rolling_summary(
        self, conversation_id: str
    ) -> None:
        messages = self._repository.get_messages(
            conversation_id=conversation_id, limit=12
        )
        lines: list[str] = []
        for m in reversed(messages):
            prefix = "User:" if m.role == "user" else "Assistant:"
            content = m.content[:200]
            lines.append(f"{prefix} {content}")
        summary = "\n".join(reversed(lines))
        if len(summary) > 4000:
            summary = summary[:3997] + "..."
        self._repository.set_rolling_summary(
            conversation_id=conversation_id, summary=summary
        )
