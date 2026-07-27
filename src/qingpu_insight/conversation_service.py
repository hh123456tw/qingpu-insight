from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from dataclasses import dataclass

from qingpu_insight.conversation_import import ConversationImportService
from qingpu_insight.conversation_providers import (
    ConversationContext,
    ConversationProviderRegistry,
)
from qingpu_insight.conversation_repository import (
    ConversationRecord,
    MySQLConversationRepository,
)
from qingpu_insight.conversation_validation import (
    GroundingValidationError,
)
from qingpu_insight.job_executor import LocalJobExecutor
from qingpu_insight.jobs import (
    CONVERSATION_IMPORT,
    CONVERSATION_REFRESH,
    CONVERSATION_REPLY,
    JobService,
)

logger = logging.getLogger(__name__)


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
        validator: Callable[..., object],
        job_service: JobService,
        executor: LocalJobExecutor,
    ) -> None:
        self._repository = repository
        self._import_service = import_service
        self._provider_registry = provider_registry
        self._validator = validator
        self._job_service = job_service
        self._executor = executor

    def create_conversation(
        self, *, provider: str, model: str
    ) -> ConversationRecord:
        return self._repository.create_conversation(
            provider=provider, model=model
        )

    def start_import(
        self, *, conversation_id: str, raw_url: str, idempotency_key: str
    ) -> ConversationCommand:
        submission = self._job_service.create(
            job_type=CONVERSATION_IMPORT,
            idempotency_key=idempotency_key,
            trigger="manual",
        )
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
        submission = self._job_service.create(
            job_type=CONVERSATION_REFRESH,
            idempotency_key=idempotency_key,
            trigger="manual",
        )
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
        provider: str,
        model: str,
        evidence_revision: int,
        idempotency_key: str,
    ) -> ConversationCommand:
        conv = self._repository.get_conversation(conversation_id)
        if not conv:
            raise ValueError(f"conversation {conversation_id} not found")
        if conv.active_evidence_revision != evidence_revision:
            raise ValueError("stale evidence revision")
        active = self._job_service.list_active(CONVERSATION_REPLY)
        for run in active:
            if run.input_version == conversation_id:
                raise ValueError(
                    f"active reply already in progress for conversation"
                    f" {conversation_id}"
                )
        submission = self._job_service.create(
            job_type=CONVERSATION_REPLY,
            idempotency_key=idempotency_key,
            trigger="manual",
            input_version=conversation_id,
        )
        self._executor.submit(
            submission.run.run_id,
            functools.partial(
                self._run_reply,
                submission.run.run_id,
                conversation_id,
                question,
                provider,
                model,
                evidence_revision,
            ),
        )
        return ConversationCommand(
            run_id=submission.run.run_id,
            conversation_id=conversation_id,
        )

    def delete_conversation(
        self, *, conversation_id: str
    ) -> bool:
        return self._repository.delete_conversation(conversation_id)

    def _run_import(
        self, run_id: str, conversation_id: str, raw_url: str
    ) -> None:
        self._job_service.start(run_id)
        try:
            result = self._import_service.import_initial_listing(
                conversation_id=conversation_id, raw_url=raw_url
            )
            if result.outcome == "needs_attention":
                self._job_service.fail(
                    run_id, "verification_required", "verification required"
                )
                self._job_service.needs_attention(run_id)
            else:
                self._job_service.succeed(
                    run_id, f"rev{result.evidence_revision}", {}
                )
        except Exception as e:
            self._job_service.fail(run_id, "import_failed", str(e))
            raise

    def _run_refresh(
        self, run_id: str, conversation_id: str
    ) -> None:
        self._job_service.start(run_id)
        try:
            result = self._import_service.refresh_listing(
                conversation_id=conversation_id
            )
            if result.outcome == "needs_attention":
                self._job_service.fail(
                    run_id, "verification_required", "verification required"
                )
                self._job_service.needs_attention(run_id)
            else:
                self._job_service.succeed(
                    run_id, f"rev{result.evidence_revision}", {}
                )
        except Exception as e:
            self._job_service.fail(run_id, "refresh_failed", str(e))
            raise

    def _run_reply(
        self,
        run_id: str,
        conversation_id: str,
        question: str,
        provider_name: str,
        model: str,
        evidence_revision: int,
    ) -> None:
        self._job_service.start(run_id)
        try:
            self._repository.append_message(
                conversation_id=conversation_id,
                role="user",
                content=question,
                evidence_revision=None,
                provider=None,
                model=None,
                citations=[],
            )
            conv = self._repository.get_conversation(conversation_id)
            messages = self._repository.get_messages(
                conversation_id=conversation_id, limit=12
            )
            provider = self._provider_registry.get(provider_name)
            context = ConversationContext(
                rolling_summary=conv.rolling_summary,
                recent_messages=tuple(
                    {"role": m.role, "content": m.content}
                    for m in messages
                ),
                evidence_revision=evidence_revision,
                evidence_facts=(),
                limitations=(),
            )
            draft = provider.reply(
                model=model, question=question, context=context
            )
            try:
                validated = self._validator(
                    draft,
                    available_fact_ids=set(),
                    evidence_revision=evidence_revision,
                )
            except GroundingValidationError:
                draft = provider.reply(
                    model=model, question=question, context=context
                )
                validated = self._validator(
                    draft,
                    available_fact_ids=set(),
                    evidence_revision=evidence_revision,
                )
            self._repository.append_message(
                conversation_id=conversation_id,
                role="assistant",
                content=validated.answer,
                evidence_revision=evidence_revision,
                provider=provider_name,
                model=model,
                citations=list(validated.citations),
            )
            self._update_rolling_summary(conversation_id)
            self._job_service.succeed(
                run_id, f"rev{evidence_revision}", {}
            )
        except GroundingValidationError:
            self._job_service.fail(
                run_id, "validation_failed", "answer validation failed"
            )
        except Exception as e:
            self._job_service.fail(run_id, "reply_failed", str(e))
            raise

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
