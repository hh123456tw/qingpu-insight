from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import ANY, MagicMock, call, sentinel

import pytest

from qingpu_insight.conversation_fallback import ReplyExecution
from qingpu_insight.conversation_repository import (
    ConversationRecord,
    MessageRecord,
)
from qingpu_insight.conversation_service import (
    ConversationCommand,
    ConversationService,
)
from qingpu_insight.conversation_validation import ValidatedChatAnswer
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
        "reply_executor": MagicMock(),
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

        result = service.create_conversation(model="gemma-4-31b-it")

        deps["repository"].create_conversation.assert_called_once_with(
            provider="gemini", model="gemma-4-31b-it"
        )
        assert result is sentinel.conv

    def test_create_conversation_rejects_unknown_model(
        self, service: ConversationService, deps: dict
    ) -> None:
        with pytest.raises(ValueError, match="unknown conversation model"):
            service.create_conversation(model="custom-model")

        deps["repository"].create_conversation.assert_not_called()


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
                evidence_revision=1,
                idempotency_key="ik-3",
            )

        deps["job_service"].create.assert_not_called()

    def test_start_reply_uses_saved_rule_provider_to_reject_free_form(
        self, service: ConversationService, deps: dict
    ) -> None:
        deps["repository"].get_conversation.return_value = _conv(
            default_provider="rule",
            default_model="rule",
            active_evidence_revision=1,
        )

        with pytest.raises(ValueError, match="rule provider"):
            service.start_reply(
                conversation_id="conv-1",
                question="hello",
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
        default_provider="gemini",
        default_model="gemini-3.5-flash-lite",
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

    validated = ValidatedChatAnswer(
        answer="Great price",
        citations=["fact-1"],
        evidence_revision=1,
        general_guidance=["advice"],
        suggested_questions=["What else?"],
    )
    execution = ReplyExecution(
        validated=validated,
        actual_provider="ollama",
        actual_model="gemma4:e2b",
        fallback_reason="cloud_timeout",
    )
    deps["reply_executor"].execute.return_value = execution

    return {
        "conv": conv,
        "msg": msg,
        "validated": validated,
        "execution": execution,
    }


class TestReplyWorker:
    def test_reply_worker_success(
        self,
        service: ConversationService,
        deps: dict,
        reply_deps: dict,
    ) -> None:
        service._run_reply("run-3", "conv-1", "hello", 1)

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
                fallback_reason=None,
            ),
            call(
                conversation_id="conv-1",
                role="assistant",
                content="Great price",
                evidence_revision=1,
                provider="ollama",
                model="gemma4:e2b",
                citations=["fact-1"],
                fallback_reason="cloud_timeout",
            ),
        ])
        deps["reply_executor"].execute.assert_called_once_with(
            requested_model="gemini-3.5-flash-lite",
            question="hello",
            context=ANY,
            available_fact_ids=set(),
            evidence_revision=1,
        )
        deps["job_service"].succeed.assert_called_once_with(
            "run-3", "rev1", {}
        )
        deps["repository"].set_rolling_summary.assert_called_once()

    def test_reply_worker_terminal_provider_failure_is_safe(
        self,
        service: ConversationService,
        deps: dict,
        reply_deps: dict,
    ) -> None:
        from qingpu_insight.ollama_report_provider import ProviderError

        deps["reply_executor"].execute.side_effect = ProviderError(
            "all_conversation_providers_unavailable"
        )

        service._run_reply("run-3", "conv-1", "hello", 1)

        deps["job_service"].fail.assert_called_once_with(
            "run-3",
            "reply_providers_unavailable",
            "all conversation providers unavailable",
        )
        assert deps["repository"].append_message.call_count == 1
        assert deps["repository"].append_message.call_args[1]["role"] == "user"
        deps["repository"].set_rolling_summary.assert_not_called()

    def test_reply_worker_context_contains_only_latest_12(
        self,
        service: ConversationService,
        deps: dict,
        reply_deps: dict,
    ) -> None:
        service._run_reply("run-3", "conv-1", "hello", 1)

        deps["job_service"].start.assert_not_called()
        deps["repository"].get_messages.assert_any_call(
            conversation_id="conv-1", limit=12
        )
        context = deps["reply_executor"].execute.call_args.kwargs["context"]
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
        deps["reply_executor"].execute.return_value = ReplyExecution(
            validated=validated,
            actual_provider="gemini",
            actual_model="gemini-3.5-flash-lite",
            fallback_reason=None,
        )

        service._run_reply("run-3", "conv-1", "hello", 1)

        deps["job_service"].start.assert_not_called()
        assistant_call = deps["repository"].append_message.call_args_list[1]
        assert assistant_call.kwargs["citations"] == ["fact-1", "fact-2"]
        assert assistant_call.kwargs["provider"] == "gemini"
        assert assistant_call.kwargs["fallback_reason"] is None


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
