from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from qingpu_insight.ollama_report_provider import ProviderError
from qingpu_insight.report_contracts import BuyerReportDraft, EvidencePack
from qingpu_insight.report_providers import ProviderResult

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```$", re.MULTILINE)


class GeminiReportProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: int = 30,
        thinking_level: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key or not model:
            raise ValueError("api_key and model are required")
        if thinking_level not in {None, "minimal", "low", "medium", "high"}:
            raise ValueError("unsupported thinking_level")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._thinking_level = thinking_level
        self._session = session or requests.Session()

    def __repr__(self) -> str:
        return f"GeminiReportProvider(model={self._model!r})"

    def generate(
        self, pack: EvidencePack, repair_codes: tuple[str, ...] = ()
    ) -> ProviderResult:
        start = time.perf_counter()
        prompt = self._build_prompt(pack, repair_codes)
        schema = BuyerReportDraft.model_json_schema()
        url = f"{_GEMINI_BASE}/{self._model}:generateContent"
        body: dict[str, Any] = {
            "system_instruction": {
                "parts": [{"text": (
                    "You are a structured buyer report generator. "
                    "Generate a JSON report matching the provided schema. "
                    "Only reference fact IDs present in the evidence pack. "
                    "advantages, risks, and negotiation must each contain "
                    "1 to 3 claims; limitations may be empty. "
                    "Do not invent values. Output valid JSON only."
                )}],
            },
            "contents": [
                {
                    "parts": [
                        {"text": (
                            f"Schema: {json.dumps(schema, ensure_ascii=False)}"
                            f"\n\nEvidence pack:\n{prompt}"
                        )},
                    ],
                },
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": 1200,
            },
        }
        if self._thinking_level is not None:
            body["generationConfig"]["thinkingConfig"] = {
                "thinkingLevel": self._thinking_level,
            }
        try:
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": self._api_key,
            }
            resp = self._session.post(
                url,
                headers=headers,
                json=body,
                timeout=self._timeout,
            )
        except requests.exceptions.Timeout:
            raise ProviderError("gemini_timeout") from None
        except requests.exceptions.ConnectionError:
            raise ProviderError("gemini_connection_error") from None

        if resp.status_code >= 400:
            raise ProviderError("gemini_http_error")

        try:
            data = resp.json()
        except (json.JSONDecodeError, requests.exceptions.JSONDecodeError):
            raise ProviderError("gemini_non_json_response") from None

        try:
            parts = data["candidates"][0]["content"]["parts"]
            content_raw = next(
                part["text"]
                for part in reversed(parts)
                if (
                    isinstance(part, dict)
                    and not part.get("thought", False)
                    and isinstance(part.get("text"), str)
                    and part["text"].strip()
                )
            )
        except (KeyError, TypeError, IndexError, StopIteration):
            raise ProviderError("gemini_non_json_response") from None

        content_raw = self._strip_code_fence(content_raw)

        try:
            parsed = json.loads(content_raw)
        except (json.JSONDecodeError, TypeError):
            raise ProviderError("gemini_non_json_response") from None

        try:
            draft = BuyerReportDraft.model_validate(parsed)
        except Exception:
            raise ProviderError("gemini_validation_error") from None

        elapsed = (time.perf_counter() - start) * 1000
        return ProviderResult(
            provider="gemini",
            model=self._model,
            draft=draft,
            latency_ms=elapsed,
        )

    def _build_prompt(self, pack: EvidencePack, repair_codes: tuple[str, ...]) -> str:
        prompt = json.dumps(pack.model_dump(mode="json"), ensure_ascii=False, indent=2)
        if repair_codes:
            codes = ", ".join(repair_codes)
            prompt += f"\n\nPrevious validation issues to fix: {codes}"
        return prompt

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        m = _CODE_FENCE_RE.search(text)
        return m.group(1) if m else text
