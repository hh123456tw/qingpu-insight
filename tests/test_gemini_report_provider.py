from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
import requests
import responses

from qingpu_insight.gemini_report_provider import GeminiReportProvider, ProviderError
from qingpu_insight.report_contracts import (
    BuyerReportDraft,
    EvidenceCandidate,
    EvidenceFact,
    EvidencePack,
)


def _fact_id(version: str, cid: str, kind: str, unit: str) -> str:
    raw = f"{version}|{cid}|{kind}|{unit}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


_NOW = datetime.now(UTC).isoformat()
_VER = "v1"

_FID = {
    "asking_price": _fact_id(_VER, "c1", "asking_price", "twd"),
    "unit_price": _fact_id(_VER, "c1", "unit_price", "twd_per_ping"),
    "area": _fact_id(_VER, "c1", "area", "ping"),
    "building_age": _fact_id(_VER, "c1", "building_age", "years"),
    "station_distance": _fact_id(_VER, "c1", "station_distance", "m"),
    "model_interval": _fact_id(_VER, "c1", "model_interval", "twd"),
    "location_evidence": _fact_id(_VER, "c1", "location_evidence", "method"),
    "data_freshness": _fact_id(_VER, "c1", "data_freshness", "iso"),
}


def _mkfact(kind: str, value: str, unit: str, label: str) -> EvidenceFact:
    st = "valuation" if kind == "model_interval" else "listing"
    return EvidenceFact(
        fact_id=_FID[kind], kind=kind, label=label,
        value=value, unit=unit, source_type=st,
        source_version=_VER, observed_at=_NOW,
    )


_FACTS = (
    _mkfact("asking_price", "15000000", "twd", "Asking Price"),
    _mkfact("unit_price", "500000", "twd_per_ping", "Unit Price"),
    _mkfact("area", "30.00", "ping", "Building Area"),
    _mkfact("building_age", "5.0", "years", "Building Age"),
    _mkfact("station_distance", "A18 300m", "m", "Station Distance"),
    _mkfact("model_interval", "13000000-16000000", "twd", "Model Valuation Interval"),
    _mkfact("location_evidence", "structured_address", "method", "Location Method"),
    _mkfact("data_freshness", _NOW, "iso", "Data Freshness"),
)

_CANDIDATE = EvidenceCandidate(candidate_id="c1", listing_type="sale")

PACK = EvidencePack(
    pack_id="test-pack", dataset_version=_VER, generated_at=_NOW,
    candidates=(_CANDIDATE,), facts=_FACTS, limitations=(),
)

_VALID_DRAFT_DICT = {
    "summary": {
        "text": "總價15000000元，面積30.00坪",
        "fact_ids": [_FID["asking_price"], _FID["area"]],
        "numeric_fact_ids": [_FID["asking_price"], _FID["area"]],
    },
    "advantages": [
        {"text": "交通便利", "fact_ids": [_FID["station_distance"], _FID["location_evidence"]], "numeric_fact_ids": []},  # noqa: E501
    ],
    "risks": [
        {"text": "屋齡5.0年", "fact_ids": [_FID["building_age"]], "numeric_fact_ids": [_FID["building_age"]]},  # noqa: E501
    ],
    "negotiation": [
        {"text": "開價15000000元", "fact_ids": [_FID["asking_price"]], "numeric_fact_ids": [_FID["asking_price"]]},  # noqa: E501
    ],
    "limitations": [
        {"text": "僅供參考", "fact_ids": [_FID["location_evidence"]], "numeric_fact_ids": []},
    ],
}

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent"


def _gemini_response(draft_dict: dict) -> dict:
    return {
        "candidates": [
            {"content": {"parts": [{"text": json.dumps(draft_dict, ensure_ascii=False)}]}},
        ],
    }


class TestGeminiReportProvider:
    def test_rejects_empty_api_key(self) -> None:
        with pytest.raises(ValueError, match="api_key and model are required"):
            GeminiReportProvider(api_key="", model="gemini-pro")

    def test_rejects_empty_model(self) -> None:
        with pytest.raises(ValueError, match="api_key and model are required"):
            GeminiReportProvider(api_key="test-key", model="")

    def test_rejects_none_api_key(self) -> None:
        with pytest.raises(ValueError, match="api_key and model are required"):
            GeminiReportProvider(api_key=None, model="gemini-pro")  # type: ignore[arg-type]

    def test_successful_response(self) -> None:
        url = f"{_GEMINI_URL}?key=test-key"
        with responses.RequestsMock() as rsps:
            rsps.post(url, json=_gemini_response(_VALID_DRAFT_DICT), status=200)
            provider = GeminiReportProvider(api_key="test-key", model="gemini-pro")
            result = provider.generate(PACK)

        assert result.provider == "gemini"
        assert result.model == "gemini-pro"
        assert isinstance(result.draft, BuyerReportDraft)
        assert result.draft.summary.text == "總價15000000元，面積30.00坪"

    def test_handles_markdown_code_fence(self) -> None:
        url = f"{_GEMINI_URL}?key=test-key"
        raw = f"```json\n{json.dumps(_VALID_DRAFT_DICT, ensure_ascii=False)}\n```"
        with responses.RequestsMock() as rsps:
            rsps.post(
                url,
                json={"candidates": [{"content": {"parts": [{"text": raw}]}}]},
                status=200,
            )
            provider = GeminiReportProvider(api_key="test-key", model="gemini-pro")
            result = provider.generate(PACK)
        assert isinstance(result.draft, BuyerReportDraft)

    def test_timeout(self) -> None:
        provider = GeminiReportProvider(
            api_key="test-key", model="gemini-pro", timeout_seconds=1,
        )
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.exceptions.Timeout("timed out")
        provider._session = mock_session

        with pytest.raises(ProviderError) as exc:
            provider.generate(PACK)
        assert exc.value.code == "gemini_timeout"

    def test_connection_error(self) -> None:
        provider = GeminiReportProvider(api_key="test-key", model="gemini-pro")
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.exceptions.ConnectionError("refused")
        provider._session = mock_session

        with pytest.raises(ProviderError) as exc:
            provider.generate(PACK)
        assert exc.value.code == "gemini_connection_error"

    def test_http_429(self) -> None:
        url = f"{_GEMINI_URL}?key=test-key"
        with responses.RequestsMock() as rsps:
            rsps.post(url, body="Rate Limited", status=429)
            provider = GeminiReportProvider(api_key="test-key", model="gemini-pro")
            with pytest.raises(ProviderError) as exc:
                provider.generate(PACK)
            assert exc.value.code == "gemini_http_error"

    def test_http_5xx(self) -> None:
        url = f"{_GEMINI_URL}?key=test-key"
        with responses.RequestsMock() as rsps:
            rsps.post(url, body="Internal Error", status=500)
            provider = GeminiReportProvider(api_key="test-key", model="gemini-pro")
            with pytest.raises(ProviderError) as exc:
                provider.generate(PACK)
            assert exc.value.code == "gemini_http_error"

    def test_non_json_response(self) -> None:
        url = f"{_GEMINI_URL}?key=test-key"
        with responses.RequestsMock() as rsps:
            rsps.post(url, body="not json", status=200)
            provider = GeminiReportProvider(api_key="test-key", model="gemini-pro")
            with pytest.raises(ProviderError) as exc:
                provider.generate(PACK)
            assert exc.value.code == "gemini_non_json_response"

    def test_missing_candidates_field(self) -> None:
        url = f"{_GEMINI_URL}?key=test-key"
        with responses.RequestsMock() as rsps:
            rsps.post(url, json={"error": "bad request"}, status=200)
            provider = GeminiReportProvider(api_key="test-key", model="gemini-pro")
            with pytest.raises(ProviderError) as exc:
                provider.generate(PACK)
            assert exc.value.code == "gemini_non_json_response"

    def test_empty_candidates(self) -> None:
        url = f"{_GEMINI_URL}?key=test-key"
        with responses.RequestsMock() as rsps:
            rsps.post(url, json={"candidates": []}, status=200)
            provider = GeminiReportProvider(api_key="test-key", model="gemini-pro")
            with pytest.raises(ProviderError) as exc:
                provider.generate(PACK)
            assert exc.value.code == "gemini_non_json_response"

    def test_non_json_content(self) -> None:
        url = f"{_GEMINI_URL}?key=test-key"
        with responses.RequestsMock() as rsps:
            rsps.post(
                url,
                json={"candidates": [{"content": {"parts": [{"text": "not json"}]}}]},
                status=200,
            )
            provider = GeminiReportProvider(api_key="test-key", model="gemini-pro")
            with pytest.raises(ProviderError) as exc:
                provider.generate(PACK)
            assert exc.value.code == "gemini_non_json_response"

    def test_validation_error(self) -> None:
        url = f"{_GEMINI_URL}?key=test-key"
        bad = {"summary": {"text": "bad", "fact_ids": [], "numeric_fact_ids": []}}
        with responses.RequestsMock() as rsps:
            rsps.post(url, json=_gemini_response(bad), status=200)
            provider = GeminiReportProvider(api_key="test-key", model="gemini-pro")
            with pytest.raises(ProviderError) as exc:
                provider.generate(PACK)
            assert exc.value.code == "gemini_validation_error"

    def test_passes_session(self) -> None:
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = _gemini_response(_VALID_DRAFT_DICT)
        session.post.return_value = resp

        provider = GeminiReportProvider(
            api_key="test-key", model="gemini-pro", session=session,
        )
        result = provider.generate(PACK)
        assert isinstance(result.draft, BuyerReportDraft)

    def test_api_key_in_url_not_repr(self) -> None:
        provider = GeminiReportProvider(api_key="super-secret-key", model="gemini-pro")
        assert "super-secret-key" not in repr(provider)

    def test_latency_recorded(self) -> None:
        url = f"{_GEMINI_URL}?key=test-key"
        with responses.RequestsMock() as rsps:
            rsps.post(url, json=_gemini_response(_VALID_DRAFT_DICT), status=200)
            provider = GeminiReportProvider(api_key="test-key", model="gemini-pro")
            result = provider.generate(PACK)
        assert result.latency_ms > 0
