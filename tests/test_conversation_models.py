from __future__ import annotations

import pytest

from qingpu_insight.conversation_models import (
    DEFAULT_CONVERSATION_MODEL,
    public_model_catalog,
    resolve_conversation_model,
)


def test_catalog_has_exact_public_models_in_display_order():
    catalog = public_model_catalog(
        gemini_configured=True,
        ollama_ready=False,
    )

    assert catalog["default_model"] == "gemini-3.5-flash-lite"
    assert [item["id"] for item in catalog["items"]] == [
        "gemini-3.5-flash-lite",
        "gemma-4-31b-it",
        "gemma4:e2b",
        "rule",
    ]
    assert catalog["gemini_configured"] is True
    assert catalog["ollama_ready"] is False


def test_catalog_resolves_provider_server_side():
    assert resolve_conversation_model("gemini-3.5-flash-lite").provider == "gemini"
    assert resolve_conversation_model("gemma-4-31b-it").provider == "gemini"
    assert resolve_conversation_model("gemma4:e2b").provider == "ollama"
    assert resolve_conversation_model("rule").provider == "rule"
    assert DEFAULT_CONVERSATION_MODEL == "gemini-3.5-flash-lite"


def test_catalog_rejects_unknown_model():
    with pytest.raises(ValueError, match="unknown conversation model"):
        resolve_conversation_model("gemini-pro")
