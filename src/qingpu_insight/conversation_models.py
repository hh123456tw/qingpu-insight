from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModelDefinition:
    id: str
    label: str
    provider: str
    cloud: bool
    description: str


PUBLIC_CONVERSATION_MODELS = (
    ModelDefinition(
        id="gemini-3.5-flash-lite",
        label="Google Gemini 3.5 Flash-Lite",
        provider="gemini",
        cloud=True,
        description="快速、穩定；雲端失敗時自動改用本機",
    ),
    ModelDefinition(
        id="gemma-4-31b-it",
        label="Google Gemma 4 31B",
        provider="gemini",
        cloud=True,
        description="Google 託管的大型開放模型",
    ),
    ModelDefinition(
        id="gemma4:e2b",
        label="本機 Ollama Gemma 4",
        provider="ollama",
        cloud=False,
        description="不使用雲端 API",
    ),
    ModelDefinition(
        id="rule",
        label="Rule 摘要模式",
        provider="rule",
        cloud=False,
        description="完全離線備援",
    ),
)

DEFAULT_CONVERSATION_MODEL = "gemini-3.5-flash-lite"
_MODELS_BY_ID = {model.id: model for model in PUBLIC_CONVERSATION_MODELS}


def resolve_conversation_model(model_id: str) -> ModelDefinition:
    try:
        return _MODELS_BY_ID[model_id]
    except KeyError:
        raise ValueError(f"unknown conversation model: {model_id}") from None


def public_model_catalog(*, gemini_configured: bool) -> dict[str, object]:
    return {
        "default_model": DEFAULT_CONVERSATION_MODEL,
        "gemini_configured": gemini_configured,
        "items": [asdict(model) for model in PUBLIC_CONVERSATION_MODELS],
    }
