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
        session: requests.Session | None = None,
    ) -> None:
        if not api_key or not model:
            raise ValueError("api_key and model are required")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
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
            content_raw = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, TypeError, IndexError):
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
