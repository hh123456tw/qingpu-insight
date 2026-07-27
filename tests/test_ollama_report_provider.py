from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
import requests
import responses

from qingpu_insight.ollama_report_provider import OllamaReportProvider, ProviderError
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

_LLM_JSON = {
    "message": {
        "role": "assistant",
        "content": json.dumps(_VALID_DRAFT_DICT, ensure_ascii=False),
    },
}

_OLLAMA_URL = "http://localhost:11434/api/chat"


class TestOllamaReportProvider:
    def test_successful_response(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(_OLLAMA_URL, json=_LLM_JSON, status=200)
            provider = OllamaReportProvider(
                base_url="http://localhost:11434", model="llama3",
            )
            result = provider.generate(PACK)

        assert result.provider == "ollama"
        assert result.model == "llama3"
        assert isinstance(result.draft, BuyerReportDraft)
        assert result.draft.summary.text == "總價15000000元，面積30.00坪"

    def test_passes_repair_codes(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(_OLLAMA_URL, json=_LLM_JSON, status=200)
            provider = OllamaReportProvider(
                base_url="http://localhost:11434", model="llama3",
            )
            result = provider.generate(PACK, repair_codes=("unknown_fact",))

        assert result.draft.summary.text == "總價15000000元，面積30.00坪"

    def test_timeout(self) -> None:
        provider = OllamaReportProvider(
            base_url="http://localhost:11434", model="llama3", timeout_seconds=1,
        )
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.exceptions.Timeout("timed out")
        provider._session = mock_session

        with pytest.raises(ProviderError) as exc:
            provider.generate(PACK)
        assert exc.value.code == "ollama_timeout"

    def test_connection_error(self) -> None:
        provider = OllamaReportProvider(
            base_url="http://localhost:11434", model="llama3",
        )
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.exceptions.ConnectionError("refused")
        provider._session = mock_session

        with pytest.raises(ProviderError) as exc:
            provider.generate(PACK)
        assert exc.value.code == "ollama_connection_error"

    def test_http_429(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(_OLLAMA_URL, body="Too Many Requests", status=429)
            provider = OllamaReportProvider(
                base_url="http://localhost:11434", model="llama3",
            )
            with pytest.raises(ProviderError) as exc:
                provider.generate(PACK)
            assert exc.value.code == "ollama_http_error"

    def test_http_5xx(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(_OLLAMA_URL, body="Internal Error", status=500)
            provider = OllamaReportProvider(
                base_url="http://localhost:11434", model="llama3",
            )
            with pytest.raises(ProviderError) as exc:
                provider.generate(PACK)
            assert exc.value.code == "ollama_http_error"

    def test_non_json_response(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(_OLLAMA_URL, body="not json", status=200)
            provider = OllamaReportProvider(
                base_url="http://localhost:11434", model="llama3",
            )
            with pytest.raises(ProviderError) as exc:
                provider.generate(PACK)
            assert exc.value.code == "ollama_non_json_response"

    def test_missing_message_field(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(_OLLAMA_URL, json={"error": "bad request"}, status=200)
            provider = OllamaReportProvider(
                base_url="http://localhost:11434", model="llama3",
            )
            with pytest.raises(ProviderError) as exc:
                provider.generate(PACK)
            assert exc.value.code == "ollama_non_json_response"

    def test_non_json_content(self) -> None:
        content = json.dumps({
            "message": {"role": "assistant", "content": "not json"},
        })
        with responses.RequestsMock() as rsps:
            rsps.post(_OLLAMA_URL, json=json.loads(content), status=200)
            provider = OllamaReportProvider(
                base_url="http://localhost:11434", model="llama3",
            )
            with pytest.raises(ProviderError) as exc:
                provider.generate(PACK)
            assert exc.value.code == "ollama_non_json_response"

    def test_extra_fields_in_draft(self) -> None:
        bad = dict(_VALID_DRAFT_DICT)
        bad["extra_field"] = "should not be here"
        content = json.dumps({
            "message": {"role": "assistant", "content": json.dumps(bad, ensure_ascii=False)},
        })
        with responses.RequestsMock() as rsps:
            rsps.post(_OLLAMA_URL, json=json.loads(content), status=200)
            provider = OllamaReportProvider(
                base_url="http://localhost:11434", model="llama3",
            )
            with pytest.raises(ProviderError) as exc:
                provider.generate(PACK)
            assert exc.value.code == "ollama_validation_error"

    def test_invalid_draft_missing_fields(self) -> None:
        bad = {"summary": {"text": "bad", "fact_ids": [], "numeric_fact_ids": []}}
        content = json.dumps({
            "message": {"role": "assistant", "content": json.dumps(bad, ensure_ascii=False)},
        })
        with responses.RequestsMock() as rsps:
            rsps.post(_OLLAMA_URL, json=json.loads(content), status=200)
            provider = OllamaReportProvider(
                base_url="http://localhost:11434", model="llama3",
            )
            with pytest.raises(ProviderError) as exc:
                provider.generate(PACK)
            assert exc.value.code == "ollama_validation_error"

    def test_normalizes_common_local_model_claim_shape(self) -> None:
        raw = dict(_VALID_DRAFT_DICT)
        raw["summary"] = [dict(raw["summary"])]
        for section in ("advantages", "risks", "negotiation", "limitations"):
            raw[section] = [
                {
                    key: value
                    for key, value in claim.items()
                    if key != "numeric_fact_ids"
                }
                for claim in raw[section]
            ]
        response = {
            "message": {
                "role": "assistant",
                "content": json.dumps(raw, ensure_ascii=False),
            },
        }
        with responses.RequestsMock() as rsps:
            rsps.post(_OLLAMA_URL, json=response, status=200)
            provider = OllamaReportProvider(
                base_url="http://localhost:11434",
                model="llama3",
            )

            result = provider.generate(PACK)

        assert result.draft.summary.text == _VALID_DRAFT_DICT["summary"]["text"]
        assert result.draft.advantages[0].numeric_fact_ids == ()

    def test_sends_model_and_format_in_body(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(_OLLAMA_URL, json=_LLM_JSON, status=200)
            provider = OllamaReportProvider(
                base_url="http://localhost:11434", model="llama3",
            )
            provider.generate(PACK)

            req_body = json.loads(rsps.calls[0].request.body)
            assert req_body["model"] == "llama3"
            assert req_body["format"] == "json"
            assert req_body["stream"] is False
            assert req_body["options"] == {
                "num_predict": 2000,
                "temperature": 0,
            }
            system_prompt = req_body["messages"][0]["content"]
            assert "summary MUST be one claim object, never an array" in system_prompt
            assert '"advantages": [' in system_prompt

    def test_passes_session(self) -> None:
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = _LLM_JSON
        session.post.return_value = resp

        provider = OllamaReportProvider(
            base_url="http://localhost:11434", model="llama3", session=session,
        )
        result = provider.generate(PACK)
        assert isinstance(result.draft, BuyerReportDraft)
        session.post.assert_called_once()

    def test_latency_recorded(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(_OLLAMA_URL, json=_LLM_JSON, status=200)
            provider = OllamaReportProvider(
                base_url="http://localhost:11434", model="llama3",
            )
            result = provider.generate(PACK)
        assert result.latency_ms > 0

    def test_error_has_no_raw_body(self) -> None:
        provider = OllamaReportProvider(
            base_url="http://localhost:11434", model="llama3",
        )
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.exceptions.Timeout("timed out")
        provider._session = mock_session

        with pytest.raises(ProviderError) as exc:
            provider.generate(PACK)
        assert exc.value.code == "ollama_timeout"
