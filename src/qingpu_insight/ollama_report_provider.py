from __future__ import annotations

import json
import time
from typing import Any

import requests

from qingpu_insight.report_contracts import BuyerReportDraft, EvidencePack
from qingpu_insight.report_providers import ProviderResult


class ProviderError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_REPORT_SKELETON = {
    "summary": {
        "text": "",
        "fact_ids": [],
        "numeric_fact_ids": [],
    },
    "advantages": [{
        "text": "",
        "fact_ids": [],
        "numeric_fact_ids": [],
    }],
    "risks": [{
        "text": "",
        "fact_ids": [],
        "numeric_fact_ids": [],
    }],
    "negotiation": [{
        "text": "",
        "fact_ids": [],
        "numeric_fact_ids": [],
    }],
    "limitations": [{
        "text": "",
        "fact_ids": [],
        "numeric_fact_ids": [],
    }],
}

_SYSTEM_PROMPT = """You are a structured buyer report generator.
Return exactly one JSON object matching this skeleton and replace the empty values:
{skeleton}

The root object MUST contain all five keys shown above.
summary MUST be one claim object, never an array.
advantages, risks, negotiation, and limitations MUST be arrays of claim objects.
Do not add title or any other root key.
Only reference fact IDs present in the evidence pack.
Do not invent values. Output valid JSON only."""


def _normalize_draft_payload(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    summary = normalized.get("summary")
    if (
        isinstance(summary, list)
        and len(summary) == 1
        and isinstance(summary[0], dict)
    ):
        summary = summary[0]
        normalized["summary"] = summary
    if isinstance(summary, dict) and "numeric_fact_ids" not in summary:
        normalized["summary"] = {**summary, "numeric_fact_ids": []}
    for section in ("advantages", "risks", "negotiation", "limitations"):
        claims = normalized.get(section)
        if not isinstance(claims, list):
            continue
        normalized[section] = [
            (
                {**claim, "numeric_fact_ids": []}
                if isinstance(claim, dict) and "numeric_fact_ids" not in claim
                else claim
            )
            for claim in claims
        ]
    return normalized


class OllamaReportProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._session = session or requests.Session()

    def generate(
        self, pack: EvidencePack, repair_codes: tuple[str, ...] = ()
    ) -> ProviderResult:
        start = time.perf_counter()
        prompt = self._build_prompt(pack, repair_codes)
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": _SYSTEM_PROMPT.format(
                        skeleton=json.dumps(
                            _REPORT_SKELETON,
                            ensure_ascii=False,
                        )
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "format": "json",
            "stream": False,
            "options": {
                "num_predict": 2000,
                "temperature": 0,
            },
        }
        try:
            resp = self._session.post(
                f"{self._base_url}/api/chat",
                json=body,
                timeout=self._timeout,
            )
        except requests.exceptions.Timeout:
            raise ProviderError("ollama_timeout") from None
        except requests.exceptions.ConnectionError:
            raise ProviderError("ollama_connection_error") from None

        if resp.status_code >= 400:
            raise ProviderError("ollama_http_error")

        try:
            data = resp.json()
        except (json.JSONDecodeError, requests.exceptions.JSONDecodeError):
            raise ProviderError("ollama_non_json_response") from None

        try:
            content_raw = data["message"]["content"]
        except (KeyError, TypeError):
            raise ProviderError("ollama_non_json_response") from None

        try:
            parsed = json.loads(content_raw)
        except (json.JSONDecodeError, TypeError):
            raise ProviderError("ollama_non_json_response") from None

        try:
            draft = BuyerReportDraft.model_validate(
                _normalize_draft_payload(parsed)
            )
        except Exception:
            raise ProviderError("ollama_validation_error") from None

        elapsed = (time.perf_counter() - start) * 1000
        return ProviderResult(
            provider="ollama",
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
