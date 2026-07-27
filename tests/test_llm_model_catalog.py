from __future__ import annotations

import pytest

from qingpu_insight.llm_model_catalog import LlmModelCatalog


class FakeResponse:
    def __init__(self, payload: object, *, status_error: Exception | None = None):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    def json(self) -> object:
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse | Exception):
        self.response = response
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, *, timeout: float):
        self.calls.append((url, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def make_catalog(session: FakeSession, *, gemini_ready: bool = True) -> LlmModelCatalog:
    return LlmModelCatalog(
        ollama_base_url_getter=lambda: "http://127.0.0.1:11434/",
        gemini_configured_getter=lambda: gemini_ready,
        session=session,
        timeout_seconds=2.0,
    )


def test_public_catalog_discovers_deduplicates_and_sorts_ollama_models():
    session = FakeSession(FakeResponse({
        "models": [
            {"name": "qwen2.5:0.5b", "digest": "secret-digest"},
            {"model": "gemma4:e2b", "path": "C:/private/model"},
            {"name": "gemma4:e2b"},
            {"name": ""},
            {"name": 123},
        ],
    }))

    result = make_catalog(session).public_catalog()

    assert session.calls == [("http://127.0.0.1:11434/api/tags", 2.0)]
    assert [item["id"] for item in result["items"]] == [
        "ollama:gemma4:e2b",
        "ollama:qwen2.5:0.5b",
        "gemini:gemini-3.5-flash-lite",
        "gemini:gemma-4-31b-it",
    ]
    assert result["warnings"] == []
    assert "digest" not in repr(result)
    assert "C:/private" not in repr(result)


def test_public_catalog_keeps_fixed_gemini_models_when_ollama_is_offline():
    result = make_catalog(
        FakeSession(ConnectionError("private host and token")),
        gemini_ready=False,
    ).public_catalog()

    assert [item["model"] for item in result["items"]] == [
        "gemini-3.5-flash-lite",
        "gemma-4-31b-it",
    ]
    assert all(item["ready"] is False for item in result["items"])
    assert result["warnings"] == ["ollama_unavailable"]
    assert "private host" not in repr(result)


def test_resolve_requires_exact_membership_and_preserves_ollama_tag():
    catalog = make_catalog(FakeSession(FakeResponse({
        "models": [{"name": "gemma4:e2b"}],
    })))

    option = catalog.resolve("ollama:gemma4:e2b")

    assert option.provider == "ollama"
    assert option.model == "gemma4:e2b"
    with pytest.raises(ValueError, match="unknown_model_id"):
        catalog.resolve("ollama:not-installed:latest")
    with pytest.raises(ValueError, match="model_not_ready"):
        make_catalog(
            FakeSession(FakeResponse({"models": []})),
            gemini_ready=False,
        ).resolve("gemini:gemma-4-31b-it")


def test_ollama_model_ready_uses_same_live_discovery():
    catalog = make_catalog(FakeSession(FakeResponse({
        "models": [{"name": "gemma4:e2b"}],
    })))

    assert catalog.ollama_model_ready("gemma4:e2b") is True
    assert catalog.ollama_model_ready("missing") is False
