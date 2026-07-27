from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Literal

import requests

ProviderName = Literal["ollama", "gemini"]
_GEMINI_MODELS = (
    ("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"),
    ("gemma-4-31b-it", "Gemma 4 31B"),
)


@dataclass(frozen=True)
class BenchmarkModelOption:
    id: str
    provider: ProviderName
    model: str
    label: str
    ready: bool
    note: str


class LlmModelCatalog:
    def __init__(
        self,
        *,
        ollama_base_url_getter: Callable[[], str],
        gemini_configured_getter: Callable[[], bool],
        session: requests.Session | None = None,
        timeout_seconds: float = 2.0,
    ) -> None:
        self._ollama_base_url_getter = ollama_base_url_getter
        self._gemini_configured_getter = gemini_configured_getter
        self._session = session or requests.Session()
        self._timeout_seconds = timeout_seconds

    def _ollama_names(self) -> tuple[tuple[str, ...], bool]:
        try:
            base_url = self._ollama_base_url_getter().rstrip("/")
            response = self._session.get(
                f"{base_url}/api/tags",
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
                raise ValueError("invalid_ollama_catalog")
            names = {
                value
                for entry in payload["models"]
                if isinstance(entry, dict)
                for value in (entry.get("name") or entry.get("model"),)
                if isinstance(value, str) and value.strip()
            }
            return tuple(sorted(names)), True
        except Exception:
            return (), False

    def public_catalog(self) -> dict[str, object]:
        names, discovery_ok = self._ollama_names()
        items = [
            BenchmarkModelOption(
                id=f"ollama:{name}",
                provider="ollama",
                model=name,
                label=f"Ollama｜{name}",
                ready=True,
                note="本機已安裝",
            )
            for name in names
        ]
        gemini_ready = bool(self._gemini_configured_getter())
        items.extend(
            BenchmarkModelOption(
                id=f"gemini:{model}",
                provider="gemini",
                model=model,
                label=f"Gemini｜{label}",
                ready=gemini_ready,
                note="可使用" if gemini_ready else "尚未設定 Gemini API Key",
            )
            for model, label in _GEMINI_MODELS
        )
        return {
            "items": [asdict(item) for item in items],
            "warnings": [] if discovery_ok else ["ollama_unavailable"],
        }

    def resolve(self, model_id: str) -> BenchmarkModelOption:
        if not isinstance(model_id, str) or ":" not in model_id:
            raise ValueError("unknown_model_id")
        provider, model = model_id.split(":", 1)
        items = {
            item["id"]: BenchmarkModelOption(**item)
            for item in self.public_catalog()["items"]
        }
        option = items.get(model_id)
        if (
            option is None
            or option.provider != provider
            or option.model != model
        ):
            raise ValueError("unknown_model_id")
        if not option.ready:
            raise ValueError("model_not_ready")
        return option

    def ollama_model_ready(self, model: str) -> bool:
        names, discovery_ok = self._ollama_names()
        return discovery_ok and model in names
