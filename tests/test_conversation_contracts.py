from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from qingpu_insight.conversation_contracts import (
    ConversationCreateRequest,
    ConversationStatus,
    ConversationView,
    ListingImportRequest,
    ProviderName,
    ReplyCreateRequest,
)


class TestConversationCreateRequest:
    def test_defaults(self) -> None:
        req = ConversationCreateRequest(model="qwen2.5")
        assert req.provider == "ollama"
        assert req.model == "qwen2.5"

    def test_valid_custom(self) -> None:
        req = ConversationCreateRequest(provider="gemini", model="gemini-2.0-flash")
        assert req.provider == "gemini"
        assert req.model == "gemini-2.0-flash"

    def test_valid_rule_provider(self) -> None:
        req = ConversationCreateRequest(provider="rule", model="rule-v1")
        assert req.provider == "rule"

    def test_rejects_invalid_provider(self) -> None:
        with pytest.raises(ValidationError):
            ConversationCreateRequest(provider="openai")

    def test_rejects_model_too_long(self) -> None:
        with pytest.raises(ValidationError):
            ConversationCreateRequest(model="x" * 121)

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            ConversationCreateRequest(provider="ollama", title="nope")


class TestListingImportRequest:
    def test_valid(self) -> None:
        req = ListingImportRequest(url="https://sale.591.com.tw/home/house/detail/123/456.html")
        assert req.url.startswith("https://")

    def test_rejects_empty_url(self) -> None:
        with pytest.raises(ValidationError):
            ListingImportRequest(url="")

    def test_rejects_url_too_long(self) -> None:
        with pytest.raises(ValidationError):
            ListingImportRequest(url="x" * 2049)

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            ListingImportRequest(url="https://example.com", source="web")


class TestReplyCreateRequest:
    def test_valid(self) -> None:
        req = ReplyCreateRequest(
            content="This is a reply",
            provider="ollama",
            model="qwen2.5",
            evidence_revision=1,
        )
        assert req.content == "This is a reply"
        assert req.evidence_revision == 1

    def test_valid_rule_provider(self) -> None:
        req = ReplyCreateRequest(
            content="Rule-based reply",
            provider="rule",
            model="rule-v1",
            evidence_revision=2,
        )
        assert req.provider == "rule"

    def test_rejects_empty_content(self) -> None:
        with pytest.raises(ValidationError):
            ReplyCreateRequest(content="", provider="ollama", model="m", evidence_revision=1)

    def test_rejects_content_too_long(self) -> None:
        with pytest.raises(ValidationError):
            ReplyCreateRequest(
                content="x" * 4001, provider="ollama", model="m", evidence_revision=1
            )

    def test_rejects_invalid_provider(self) -> None:
        with pytest.raises(ValidationError):
            ReplyCreateRequest(
                content="text", provider="anthropic", model="m", evidence_revision=1
            )

    def test_rejects_zero_evidence_revision(self) -> None:
        with pytest.raises(ValidationError):
            ReplyCreateRequest(content="text", provider="ollama", model="m", evidence_revision=0)

    def test_rejects_negative_evidence_revision(self) -> None:
        with pytest.raises(ValidationError):
            ReplyCreateRequest(content="text", provider="ollama", model="m", evidence_revision=-1)

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            ReplyCreateRequest(
                content="text",
                provider="ollama",
                model="m",
                evidence_revision=1,
                author="user",
            )


class TestConversationView:
    def test_valid(self) -> None:
        now = datetime.now()
        view = ConversationView(
            id="conv-1",
            title="My Conversation",
            status="ready",
            default_provider="ollama",
            default_model="qwen2.5",
            active_evidence_revision=1,
            created_at=now,
            updated_at=now,
        )
        assert view.id == "conv-1"
        assert view.title == "My Conversation"

    def test_empty_status(self) -> None:
        now = datetime.now()
        view = ConversationView(
            id="c1",
            title="",
            status="empty",
            default_provider="rule",
            default_model="",
            active_evidence_revision=None,
            created_at=now,
            updated_at=now,
        )
        assert view.status == "empty"
        assert view.active_evidence_revision is None

    def test_all_statuses(self) -> None:
        now = datetime.now()
        for status in ("empty", "importing", "ready", "needs_attention"):
            view = ConversationView(
                id="c1",
                title="t",
                status=status,
                default_provider="ollama",
                default_model="m",
                active_evidence_revision=None,
                created_at=now,
                updated_at=now,
            )
            assert view.status == status

    def test_rejects_invalid_status(self) -> None:
        now = datetime.now()
        with pytest.raises(ValidationError):
            ConversationView(
                id="c1",
                title="t",
                status="deleted",
                default_provider="ollama",
                default_model="m",
                active_evidence_revision=None,
                created_at=now,
                updated_at=now,
            )

    def test_rejects_extra_field(self) -> None:
        now = datetime.now()
        with pytest.raises(ValidationError):
            ConversationView(
                id="c1",
                title="t",
                status="ready",
                default_provider="ollama",
                default_model="m",
                active_evidence_revision=None,
                created_at=now,
                updated_at=now,
                hidden=True,
            )


class TestTypeAliases:
    """Verify that type aliases accept the expected literal values."""

    def test_provider_names(self) -> None:
        for p in ("ollama", "gemini", "rule"):
            v: ProviderName = p
            assert v == p

    def test_conversation_statuses(self) -> None:
        for s in ("empty", "importing", "ready", "needs_attention"):
            v: ConversationStatus = s
            assert v == s
