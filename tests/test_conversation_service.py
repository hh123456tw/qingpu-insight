from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import ANY, MagicMock, call, sentinel

import pytest

from qingpu_insight.conversation_repository import (
    ConversationRecord,
    MessageRecord,
)
from qingpu_insight.conversation_service import (
    ConversationCommand,
    ConversationService,
)
from qingpu_insight.conversation_validation import (
    ChatAnswerDraft,
    GroundingValidationError,
    ValidatedChatAnswer,
)
from qingpu_insight.jobs import (
    CONVERSATION_IMPORT,
    CONVERSATION_REFRESH,
    CONVERSATION_REPLY,
    JobRun,
    JobSubmission,
)


def _run(run_id: str = "test-run", **overrides: object) -> JobRun:
    return JobRun(
        run_id=run_id,
        job_type="test",
        trigger="manual",
        idempotency_key="ik",
        status="pending",
        started_at=None,
        finished_at=None,
        attempt=1,
        input_version=overrides.get("input_version"),
        output_version=overrides.get("output_version"),
        summary=overrides.get("summary", {}),
        error_code=overrides.get("error_code"),
        error_message=overrides.get("error_message"),
    )


def _submission(
    run_id: str = "test-run", **overrides: object
) -> JobSubmission:
    return JobSubmission(run=_run(run_id, **overrides), created=True)


def _conv(
    conversation_id: str = "conv-1",
    **overrides: object,
) -> ConversationRecord:
    now = datetime.now(UTC)
    return ConversationRecord(
        id=conversation_id,
        title=overrides.get("title", "test"),
        status=overrides.get("status", "ready"),
        default_provider=overrides.get("default_provider", "ollama"),
        default_model=overrides.get("default_model", "gpt-4"),
        active_listing_id=overrides.get("active_listing_id"),
        active_evidence_revision=overrides.get("active_evidence_revision", 1),
        rolling_summary=overrides.get("rolling_summary"),
        created_at=now,
        updated_at=now,
        deleted_at=overrides.get("deleted_at"),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def deps() -> dict:
    return {
        "repository": MagicMock(),
        "import_service": MagicMock(),
        "provider_registry": MagicMock(),
        "validator": MagicMock(),
        "job_service": MagicMock(),
        "executor": MagicMock(),
    }


@pytest.fixture
def service(deps: dict) -> ConversationService:
    return ConversationService(**deps)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# create_conversation
# ---------------------------------------------------------------------------


class TestCreateConversation:
    def test_create_conversation(self, service: ConversationService, deps: dict) -> None:
        deps["repository"].create_conversation.return_value = sentinel.conv

        result = service.create_conversation(
            provider="ollama", model="gpt-4"
        )

        deps["repository"].create_conversation.assert_called_once_with(
            provider="ollama", model="gpt-4"
        )
        assert result is sentinel.conv


# ---------------------------------------------------------------------------
# start_import
# ---------------------------------------------------------------------------


class TestStartImport:
    def test_start_import_creates_job(
        self, service: ConversationService, deps: dict
    ) -> None:
        deps["job_service"].create.return_value = _submission("run-1")

        cmd = service.start_import(
            conversation_id="conv-1",
            raw_url="https://example.com",
            idempotency_key="ik-1",
        )

        deps["job_service"].create.assert_called_once_with(
            job_type=CONVERSATION_IMPORT,
            idempotency_key="ik-1",
            trigger="manual",
            input_version="conv-1",
        )
        deps["executor"].submit.assert_called_once()
        assert deps["executor"].submit.call_args[0][0] == "run-1"
        assert cmd == ConversationCommand(
            run_id="run-1", conversation_id="conv-1"
        )

    def test_import_worker_success(
        self, service: ConversationService, deps: dict
    ) -> None:
        result = MagicMock()
        result.outcome = "ready"
        result.evidence_revision = 3
        deps["import_service"].import_initial_listing.return_value = result

        service._run_import("run-1", "conv-1", "https://example.com")

        deps["job_service"].start.assert_not_called()
        deps["import_service"].import_initial_listing.assert_called_once_with(
            conversation_id="conv-1",
            raw_url="https://example.com",
            stage_callback=ANY,
        )
        deps["job_service"].succeed.assert_called_once_with(
            "run-1", "rev3", {}
        )
        deps["provider_registry"].get.assert_called_once_with("rule")

    def test_import_worker_needs_attention(
        self, service: ConversationService, deps: dict
    ) -> None:
        result = MagicMock()
        result.outcome = "needs_attention"
        deps["import_service"].import_initial_listing.return_value = result

        service._run_import("run-1", "conv-1", "https://example.com")

        deps["job_service"].start.assert_not_called()
        deps["job_service"].fail.assert_called_once_with(
            "run-1", "verification_required", "verification required"
        )
        deps["job_service"].needs_attention.assert_called_once_with("run-1")
        deps["job_service"].succeed.assert_not_called()

    def test_import_worker_exception(
        self, service: ConversationService, deps: dict
    ) -> None:
        deps["import_service"].import_initial_listing.side_effect = RuntimeError(
            "boom"
        )

        with pytest.raises(RuntimeError, match="boom"):
            service._run_import("run-1", "conv-1", "https://example.com")

        deps["job_service"].start.assert_not_called()
        deps["job_service"].fail.assert_called_once_with(
            "run-1", "import_failed", "boom"
        )


# ---------------------------------------------------------------------------
# start_refresh
# ---------------------------------------------------------------------------


class TestStartRefresh:
    def test_start_refresh_creates_job(
        self, service: ConversationService, deps: dict
    ) -> None:
        deps["job_service"].create.return_value = _submission("run-2")

        cmd = service.start_refresh(
            conversation_id="conv-1",
            idempotency_key="ik-2",
        )

        deps["job_service"].create.assert_called_once_with(
            job_type=CONVERSATION_REFRESH,
            idempotency_key="ik-2",
            trigger="manual",
            input_version="conv-1",
        )
        deps["executor"].submit.assert_called_once()
        assert deps["executor"].submit.call_args[0][0] == "run-2"
        assert cmd == ConversationCommand(
            run_id="run-2", conversation_id="conv-1"
        )

    def test_refresh_worker_success(
        self, service: ConversationService, deps: dict
    ) -> None:
        result = MagicMock()
        result.outcome = "ready"
        result.evidence_revision = 2
        deps["import_service"].refresh_listing.return_value = result

        service._run_refresh("run-2", "conv-1")

        deps["job_service"].start.assert_not_called()
        deps["import_service"].refresh_listing.assert_called_once_with(
            conversation_id="conv-1",
            stage_callback=ANY,
        )
        deps["job_service"].succeed.assert_called_once_with(
            "run-2", "rev2", {}
        )

    def test_refresh_worker_needs_attention(
        self, service: ConversationService, deps: dict
    ) -> None:
        result = MagicMock()
        result.outcome = "needs_attention"
        deps["import_service"].refresh_listing.return_value = result

        service._run_refresh("run-2", "conv-1")

        deps["job_service"].start.assert_not_called()
        deps["job_service"].fail.assert_called_once_with(
            "run-2", "verification_required", "verification required"
        )
        deps["job_service"].needs_attention.assert_called_once_with("run-2")

    def test_refresh_worker_exception(
        self, service: ConversationService, deps: dict
    ) -> None:
        deps["import_service"].refresh_listing.side_effect = RuntimeError(
            "refresh fail"
        )

        with pytest.raises(RuntimeError, match="refresh fail"):
            service._run_refresh("run-2", "conv-1")

        deps["job_service"].start.assert_not_called()
        deps["job_service"].fail.assert_called_once_with(
            "run-2", "refresh_failed", "refresh fail"
        )


# ---------------------------------------------------------------------------
# start_reply — entry point
# ---------------------------------------------------------------------------


class TestStartReply:
    def test_start_reply_creates_job(
        self, service: ConversationService, deps: dict
    ) -> None:
        deps["repository"].get_conversation.return_value = _conv(
            active_evidence_revision=1
        )
        deps["job_service"].list_active.return_value = []
        deps["job_service"].create.return_value = _submission("run-3")

        cmd = service.start_reply(
            conversation_id="conv-1",
            question="hello",
            provider="ollama",
            model="gpt-4",
            evidence_revision=1,
            idempotency_key="ik-3",
        )

        deps["repository"].get_conversation.assert_called_once_with("conv-1")
        assert deps["job_service"].list_active.call_args_list == [
            call(CONVERSATION_REPLY),
            call(CONVERSATION_IMPORT),
            call(CONVERSATION_REFRESH),
        ]
        deps["job_service"].create.assert_called_once_with(
            job_type=CONVERSATION_REPLY,
            idempotency_key="ik-3",
            trigger="manual",
            input_version="conv-1",
        )
        deps["executor"].submit.assert_called_once()
        assert deps["executor"].submit.call_args[0][0] == "run-3"
        assert cmd == ConversationCommand(
            run_id="run-3", conversation_id="conv-1"
        )

    def test_start_reply_missing_conversation(
        self, service: ConversationService, deps: dict
    ) -> None:
        deps["repository"].get_conversation.return_value = None

        with pytest.raises(ValueError, match="conv-999 not found"):
            service.start_reply(
                conversation_id="conv-999",
                question="hello",
                provider="ollama",
                model="gpt-4",
                evidence_revision=1,
                idempotency_key="ik-3",
            )

        deps["job_service"].create.assert_not_called()

    def test_start_reply_stale_revision(
        self, service: ConversationService, deps: dict
    ) -> None:
        deps["repository"].get_conversation.return_value = _conv(
            active_evidence_revision=2
        )

        with pytest.raises(ValueError, match="stale evidence revision"):
            service.start_reply(
                conversation_id="conv-1",
                question="hello",
                provider="ollama",
                model="gpt-4",
                evidence_revision=1,
                idempotency_key="ik-3",
            )

        deps["job_service"].create.assert_not_called()

    def test_start_reply_active_reply_rejected(
        self, service: ConversationService, deps: dict
    ) -> None:
        deps["repository"].get_conversation.return_value = _conv(
            active_evidence_revision=1
        )
        active_run = _run("existing-run", input_version="conv-1")
        deps["job_service"].list_active.return_value = [active_run]

        with pytest.raises(ValueError, match="active reply already in progress"):
            service.start_reply(
                conversation_id="conv-1",
                question="hello",
                provider="ollama",
                model="gpt-4",
                evidence_revision=1,
                idempotency_key="ik-3",
            )

        deps["job_service"].create.assert_not_called()


# ---------------------------------------------------------------------------
# reply worker
# ---------------------------------------------------------------------------


@pytest.fixture
def reply_deps(service: ConversationService, deps: dict) -> dict:
    """Set up mocks for a successful reply flow."""
    conv = _conv(
        conversation_id="conv-1",
        active_evidence_revision=1,
        rolling_summary="Previous summary",
    )
    deps["repository"].get_conversation.return_value = conv

    msg = MessageRecord(
        id="m1",
        conversation_id="conv-1",
        sequence_no=1,
        role="assistant",
        content="old answer",
        evidence_revision=1,
        provider="ollama",
        model="gpt-4",
        citations=["fact-1"],
        created_at=datetime.now(UTC),
    )
    deps["repository"].get_messages.return_value = [msg]

    provider = MagicMock()
    draft = ChatAnswerDraft(answer="Great price", property_claims=[])
    provider.reply.return_value = draft
    deps["provider_registry"].get.return_value = provider

    validated = ValidatedChatAnswer(
        answer="Great price",
        citations=["fact-1"],
        evidence_revision=1,
        general_guidance=["advice"],
        suggested_questions=["What else?"],
    )
    deps["validator"].return_value = validated

    return {
        "conv": conv,
        "msg": msg,
        "provider": provider,
        "draft": draft,
        "validated": validated,
    }


class TestReplyWorker:
    def test_reply_worker_success(
        self,
        service: ConversationService,
        deps: dict,
        reply_deps: dict,
    ) -> None:
        service._run_reply(
            "run-3", "conv-1", "hello", "ollama", "gpt-4", 1
        )

        deps["job_service"].start.assert_not_called()
        deps["repository"].append_message.assert_has_calls([
            call(
                conversation_id="conv-1",
                role="user",
                content="hello",
                evidence_revision=None,
                provider=None,
                model=None,
                citations=[],
            ),
            call(
                conversation_id="conv-1",
                role="assistant",
                content="Great price",
                evidence_revision=1,
                provider="ollama",
                model="gpt-4",
                citations=["fact-1"],
            ),
        ])
        deps["provider_registry"].get.assert_called_once_with("ollama")
        deps["validator"].assert_called_once_with(
            reply_deps["draft"],
            available_fact_ids=set(),
            evidence_revision=1,
        )
        deps["job_service"].succeed.assert_called_once_with(
            "run-3", "rev1", {}
        )
        deps["repository"].set_rolling_summary.assert_called_once()

    def test_reply_worker_validation_failure(
        self,
        service: ConversationService,
        deps: dict,
        reply_deps: dict,
    ) -> None:
        deps["validator"].side_effect = GroundingValidationError(
            "bad fact IDs"
        )

        service._run_reply(
            "run-3", "conv-1", "hello", "ollama", "gpt-4", 1
        )

        deps["job_service"].fail.assert_called_once_with(
            "run-3", "validation_failed", "answer validation failed"
        )
        deps["job_service"].succeed.assert_not_called()
        assert deps["repository"].append_message.call_count == 1
        assert deps["repository"].append_message.call_args[1]["role"] == "user"
        deps["repository"].set_rolling_summary.assert_not_called()

    def test_reply_worker_one_repair_passes(
        self,
        service: ConversationService,
        deps: dict,
        reply_deps: dict,
    ) -> None:
        draft2 = ChatAnswerDraft(
            answer="Fixed answer", property_claims=[]
        )
        validated2 = ValidatedChatAnswer(
            answer="Fixed answer",
            citations=["fact-2"],
            evidence_revision=1,
            general_guidance=[],
            suggested_questions=[],
        )
        deps["validator"].side_effect = [
            GroundingValidationError("bad fact IDs"),
            validated2,
        ]
        deps["provider_registry"].get.return_value.reply.side_effect = [
            reply_deps["draft"],
            draft2,
        ]

        service._run_reply(
            "run-3", "conv-1", "hello", "ollama", "gpt-4", 1
        )

        deps["job_service"].start.assert_not_called()
        deps["job_service"].succeed.assert_called_once_with(
            "run-3", "rev1", {}
        )
        assert deps["repository"].append_message.call_count == 2
        assistant_call = deps["repository"].append_message.call_args_list[1]
        assert assistant_call.kwargs["content"] == "Fixed answer"
        assert assistant_call.kwargs["citations"] == ["fact-2"]

    def test_reply_worker_both_attempts_fail(
        self,
        service: ConversationService,
        deps: dict,
        reply_deps: dict,
    ) -> None:
        deps["validator"].side_effect = GroundingValidationError(
            "bad fact IDs"
        )
        deps["provider_registry"].get.return_value.reply.side_effect = [
            reply_deps["draft"],
            reply_deps["draft"],
        ]

        service._run_reply(
            "run-3", "conv-1", "hello", "ollama", "gpt-4", 1
        )

        deps["job_service"].start.assert_not_called()
        deps["job_service"].fail.assert_called_once_with(
            "run-3", "validation_failed", "answer validation failed"
        )
        deps["job_service"].succeed.assert_not_called()
        assert deps["repository"].append_message.call_count == 1
        deps["repository"].set_rolling_summary.assert_not_called()

    def test_reply_worker_exception(
        self,
        service: ConversationService,
        deps: dict,
        reply_deps: dict,
    ) -> None:
        deps["provider_registry"].get.side_effect = ValueError(
            "unknown provider: bad"
        )

        with pytest.raises(ValueError, match="unknown provider"):
            service._run_reply(
                "run-3", "conv-1", "hello", "bad", "gpt-4", 1
            )

        deps["job_service"].start.assert_not_called()
        deps["job_service"].fail.assert_called_once_with(
            "run-3", "reply_failed", "unknown provider: bad"
        )
        deps["job_service"].succeed.assert_not_called()

    def test_reply_worker_no_partial_assistant_on_provider_failure(
        self,
        service: ConversationService,
        deps: dict,
        reply_deps: dict,
    ) -> None:
        deps["provider_registry"].get.return_value.reply.side_effect = RuntimeError(
            "provider down"
        )

        with pytest.raises(RuntimeError, match="provider down"):
            service._run_reply(
                "run-3", "conv-1", "hello", "ollama", "gpt-4", 1
            )

        deps["job_service"].start.assert_not_called()
        assert deps["repository"].append_message.call_count == 1
        assert deps["repository"].append_message.call_args[1]["role"] == "user"
        deps["repository"].set_rolling_summary.assert_not_called()

    def test_reply_worker_context_contains_only_latest_12(
        self,
        service: ConversationService,
        deps: dict,
        reply_deps: dict,
    ) -> None:
        service._run_reply(
            "run-3", "conv-1", "hello", "ollama", "gpt-4", 1
        )

        deps["job_service"].start.assert_not_called()
        deps["repository"].get_messages.assert_any_call(
            conversation_id="conv-1", limit=12
        )
        provider = deps["provider_registry"].get.return_value
        context = provider.reply.call_args[1]["context"]
        assert context.rolling_summary == "Previous summary"
        assert len(context.recent_messages) == 1
        assert context.recent_messages[0] == {
            "role": "assistant",
            "content": "old answer",
        }

    def test_reply_worker_citations_from_validator(
        self,
        service: ConversationService,
        deps: dict,
        reply_deps: dict,
    ) -> None:
        validated = ValidatedChatAnswer(
            answer="Great price",
            citations=["fact-1", "fact-2"],
            evidence_revision=1,
            general_guidance=[],
            suggested_questions=[],
        )
        deps["validator"].return_value = validated

        service._run_reply(
            "run-3", "conv-1", "hello", "ollama", "gpt-4", 1
        )

        deps["job_service"].start.assert_not_called()
        assistant_call = deps["repository"].append_message.call_args_list[1]
        assert assistant_call.kwargs["citations"] == ["fact-1", "fact-2"]


# ---------------------------------------------------------------------------
# delete_conversation
# ---------------------------------------------------------------------------


class TestDeleteConversation:
    def test_delete_conversation(
        self, service: ConversationService, deps: dict
    ) -> None:
        deps["repository"].delete_conversation.return_value = True

        result = service.delete_conversation(conversation_id="conv-1")

        deps["repository"].delete_conversation.assert_called_once_with(
            "conv-1"
        )
        assert result is True

    def test_delete_conversation_not_found(
        self, service: ConversationService, deps: dict
    ) -> None:
        deps["repository"].delete_conversation.return_value = False

        result = service.delete_conversation(conversation_id="missing")

        assert result is False


# ---------------------------------------------------------------------------
# rolling summary
# ---------------------------------------------------------------------------


class TestRollingSummary:
    def test_update_rolling_summary(
        self, service: ConversationService, deps: dict
    ) -> None:
        now = datetime.now(UTC)
        msgs = [
            MessageRecord(
                id="m1", conversation_id="conv-1", sequence_no=1,
                role="user", content="What is the price?",
                evidence_revision=None, provider=None, model=None,
                citations=[], created_at=now,
            ),
            MessageRecord(
                id="m2", conversation_id="conv-1", sequence_no=2,
                role="assistant", content="The price is 12M.",
                evidence_revision=1, provider="ollama", model="gpt-4",
                citations=["fact-1"], created_at=now,
            ),
        ]
        deps["repository"].get_messages.return_value = msgs

        service._update_rolling_summary("conv-1")

        deps["repository"].set_rolling_summary.assert_called_once()
        summary = deps["repository"].set_rolling_summary.call_args[1][
            "summary"
        ]
        assert "User: What is the price?" in summary
        assert "Assistant: The price is 12M." in summary
        assert len(summary) <= 4000

    def test_rolling_summary_truncated(
        self, service: ConversationService, deps: dict
    ) -> None:
        now = datetime.now(UTC)
        long_content = "x" * 500
        msgs = [
            MessageRecord(
                id="m1", conversation_id="conv-1", sequence_no=1,
                role="user", content=long_content,
                evidence_revision=None, provider=None, model=None,
                citations=[], created_at=now,
            ),
        ]
        deps["repository"].get_messages.return_value = msgs

        service._update_rolling_summary("conv-1")

        summary = deps["repository"].set_rolling_summary.call_args[1][
            "summary"
        ]
        assert len(summary) <= 4000
        assert "User:" in summary

    def test_no_rolling_summary_if_no_assistant(
        self, service: ConversationService, deps: dict
    ) -> None:
        deps["repository"].get_messages.return_value = []

        service._update_rolling_summary("conv-1")

        deps["repository"].set_rolling_summary.assert_called_once()
        summary = deps["repository"].set_rolling_summary.call_args[1][
            "summary"
        ]
        assert summary == ""
