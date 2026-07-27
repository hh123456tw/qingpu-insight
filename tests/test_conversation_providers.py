from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import requests
import responses

from qingpu_insight.conversation_evidence import EvidenceFact
from qingpu_insight.conversation_providers import (
    ConversationContext,
    ConversationProviderRegistry,
    GeminiConversationProvider,
    OllamaConversationProvider,
    RuleConversationProvider,
    _build_prompt,
)
from qingpu_insight.conversation_validation import ChatAnswerDraft
from qingpu_insight.ollama_report_provider import ProviderError

_NOW = "2025-01-15T10:00:00Z"

_FACT_1 = EvidenceFact(
    id="f001",
    label="開價總價",
    value="15000000",
    source="591",
    kind="asking_price",
    observed_at=_NOW,
)

_FACT_2 = EvidenceFact(
    id="f002",
    label="建物面積",
    value="30.00",
    source="591",
    kind="area",
    observed_at=_NOW,
)

_FACT_3 = EvidenceFact(
    id="f003",
    label="車站距離",
    value="A18 300m",
    source="591",
    kind="station_distance",
    observed_at=_NOW,
)

_RULE_FACT_PRICE = EvidenceFact(
    id="listing.price",
    label="開價總價",
    value="2,298 萬",
    source="591",
    kind="asking_price",
    observed_at=_NOW,
)

_RULE_FACT_POINT = EvidenceFact(
    id="valuation.point",
    label="估值點",
    value="1,989 萬",
    source="估值模型",
    observed_at=_NOW,
)

_RULE_FACT_POSITION = EvidenceFact(
    id="valuation.asking_position",
    label="開價在估值區間的位置",
    value="仍在合理區間內",
    source="估值模型",
    observed_at=_NOW,
)

_VALID_DRAFT_DICT = {
    "answer": "這是個不錯的物件。",
    "property_claims": [
        {"text": "開價 15000000 元", "fact_ids": ["f001"]},
    ],
    "general_guidance": ["一般建議：購屋前應確認產權。"],
    "suggested_questions": ["開價合理嗎？"],
}

_EMPTY_CONTEXT = ConversationContext(
    rolling_summary=None,
    recent_messages=(),
    evidence_revision=1,
    evidence_facts=(),
    limitations=(),
)


class TestRuleConversationProvider:
    def test_rule_provider_returns_fixed_summary(self) -> None:
        ctx = ConversationContext(
            rolling_summary=None,
            recent_messages=(),
            evidence_revision=1,
            evidence_facts=(_RULE_FACT_PRICE, _FACT_2),
            limitations=(),
        )
        provider = RuleConversationProvider()
        draft = provider.reply(model="rule", question="這個物件怎麼樣？", context=ctx)

        assert "這是物件證據摘要" in draft.answer
        assert len(draft.property_claims) == 1
        assert any("開價總價" in c.text for c in draft.property_claims)

    def test_rule_provider_ignores_question(self) -> None:
        ctx = ConversationContext(
            rolling_summary=None,
            recent_messages=(),
            evidence_revision=1,
            evidence_facts=(_RULE_FACT_PRICE,),
            limitations=(),
        )
        provider = RuleConversationProvider()
        draft1 = provider.reply(model="rule", question="這個物件怎麼樣？", context=ctx)
        draft2 = provider.reply(model="rule", question="附近有捷運嗎？", context=ctx)

        assert draft1.answer == draft2.answer
        assert draft1.property_claims == draft2.property_claims

    def test_rule_suggested_questions(self) -> None:
        ctx = ConversationContext(
            rolling_summary=None,
            recent_messages=(),
            evidence_revision=1,
            evidence_facts=(_FACT_1, _FACT_2, _FACT_3),
            limitations=(),
        )
        provider = RuleConversationProvider()
        draft = provider.reply(model="rule", question="任何問題", context=ctx)

        assert 2 <= len(draft.suggested_questions) <= 4
        assert any("開價合理" in q for q in draft.suggested_questions)
        assert any("交通" in q or "生活機能" in q for q in draft.suggested_questions)

    def test_rule_provider_has_general_guidance(self) -> None:
        provider = RuleConversationProvider()
        draft = provider.reply(model="rule", question="test", context=_EMPTY_CONTEXT)

        assert len(draft.general_guidance) <= 1
        if draft.general_guidance:
            assert "一般建議" in draft.general_guidance[0]

    def test_rule_provider_claims_have_fact_ids(self) -> None:
        ctx = ConversationContext(
            rolling_summary=None,
            recent_messages=(),
            evidence_revision=1,
            evidence_facts=(_RULE_FACT_PRICE, _RULE_FACT_POINT),
            limitations=(),
        )
        provider = RuleConversationProvider()
        draft = provider.reply(model="rule", question="test", context=ctx)

        assert len(draft.property_claims) >= 1
        for claim in draft.property_claims:
            assert len(claim.fact_ids) >= 1

    def test_rule_provider_excludes_comparables_and_caps_at_six(self) -> None:
        comparable_facts = tuple(
            EvidenceFact(
                id=f"comparable.{index}.price",
                label=f"案例 {index}",
                value=str(index),
                source="實價登錄",
                observed_at=_NOW,
            )
            for index in range(40)
        )
        ctx = ConversationContext(
            rolling_summary=None,
            recent_messages=(),
            evidence_revision=1,
            evidence_facts=comparable_facts + (_RULE_FACT_PRICE, _RULE_FACT_POINT, _RULE_FACT_POSITION),
            limitations=(),
        )

        draft = RuleConversationProvider().reply(
            model="rule",
            question="test",
            context=ctx,
        )

        cited_ids = {
            fact_id
            for claim in draft.property_claims
            for fact_id in claim.fact_ids
        }
        assert len(draft.property_claims) <= 6
        assert "listing.price" in cited_ids
        assert "valuation.point" in cited_ids
        assert "valuation.asking_position" in cited_ids
        assert not any(fid.startswith("comparable.") for fid in cited_ids)

    def test_rule_provider_accepts_shared_repair_hint_contract(self) -> None:
        provider = RuleConversationProvider()

        draft = provider.reply(
            model="rule",
            question="test",
            context=_EMPTY_CONTEXT,
            repair_hint="Return valid JSON.",
        )

        assert "物件證據摘要" in draft.answer

    def test_rule_summary_prioritizes_six_decision_facts(self) -> None:
        facts = (
            _RULE_FACT_PRICE,
            EvidenceFact(id="listing.area", label="建物面積", value="32.5 坪", source="591", observed_at=_NOW),
            EvidenceFact(id="listing.floor", label="樓層", value="12", source="591", observed_at=_NOW),
            _RULE_FACT_POINT,
            _RULE_FACT_POSITION,
            EvidenceFact(id="valuation.low", label="估值下限", value="1,538 萬", source="估值模型", observed_at=_NOW),
            EvidenceFact(id="valuation.high", label="估值上限", value="2,441 萬", source="估值模型", observed_at=_NOW),
            EvidenceFact(id="valuation.confidence", label="信心度", value="低", source="估值模型", observed_at=_NOW),
            EvidenceFact(id="valuation.asking_gap_percent", label="開價與估值差距百分比", value="高於估值中心 15.5%", source="估值模型", observed_at=_NOW),
            EvidenceFact(id="market.sample_size", label="相似成交筆數", value="10", source="實價登錄", observed_at=_NOW),
        )
        ctx = ConversationContext(
            rolling_summary=None,
            recent_messages=(),
            evidence_revision=1,
            evidence_facts=facts,
            limitations=(),
        )
        draft = RuleConversationProvider().reply(
            model="rule", question="摘要", context=ctx,
        )
        assert len(draft.property_claims) <= 6
        cited = {fid for claim in draft.property_claims for fid in claim.fact_ids}
        assert "listing.price" in cited
        assert "valuation.point" in cited
        assert "valuation.asking_position" in cited
        assert not any(fid.startswith("comparable.") for fid in cited)


class TestOllamaConversationProvider:
    _URL = "http://localhost:11434/api/chat"

    def _ollama_response(self, draft_dict: dict) -> dict:
        return {
            "message": {
                "role": "assistant",
                "content": json.dumps(draft_dict, ensure_ascii=False),
            },
        }

    def test_ollama_valid_json(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(self._URL, json=self._ollama_response(_VALID_DRAFT_DICT), status=200)
            provider = OllamaConversationProvider(
                base_url="http://localhost:11434",
            )
            draft = provider.reply(
                model="llama3", question="test", context=_EMPTY_CONTEXT,
            )

        assert isinstance(draft, ChatAnswerDraft)
        assert draft.answer == "這是個不錯的物件。"
        assert len(draft.property_claims) == 1

    def test_ollama_malformed_json(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(
                self._URL,
                json={"message": {"role": "assistant", "content": "not json"}},
                status=200,
            )
            provider = OllamaConversationProvider(base_url="http://localhost:11434")
            with pytest.raises(ProviderError) as exc:
                provider.reply(model="llama3", question="test", context=_EMPTY_CONTEXT)
            assert exc.value.code == "ollama_validation_error"
            assert len(rsps.calls) == 1

    def test_ollama_timeout(self) -> None:
        provider = OllamaConversationProvider(
            base_url="http://localhost:11434", timeout_seconds=1,
        )
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.exceptions.Timeout("timed out")
        provider._session = mock_session

        with pytest.raises(ProviderError) as exc:
            provider.reply(model="llama3", question="test", context=_EMPTY_CONTEXT)
        assert exc.value.code == "ollama_timeout"

    def test_ollama_connection_error(self) -> None:
        provider = OllamaConversationProvider(base_url="http://localhost:11434")
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.exceptions.ConnectionError("refused")
        provider._session = mock_session

        with pytest.raises(ProviderError) as exc:
            provider.reply(model="llama3", question="test", context=_EMPTY_CONTEXT)
        assert exc.value.code == "ollama_connection_error"

    def test_ollama_http_error(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(self._URL, body="Bad Request", status=400)
            provider = OllamaConversationProvider(base_url="http://localhost:11434")
            with pytest.raises(ProviderError) as exc:
                provider.reply(model="llama3", question="test", context=_EMPTY_CONTEXT)
            assert exc.value.code == "ollama_http_error"

    def test_ollama_non_json_response(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(self._URL, body="not json", status=200)
            provider = OllamaConversationProvider(base_url="http://localhost:11434")
            with pytest.raises(ProviderError) as exc:
                provider.reply(model="llama3", question="test", context=_EMPTY_CONTEXT)
            assert exc.value.code == "ollama_validation_error"

    def test_ollama_sends_format_json(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(self._URL, json=self._ollama_response(_VALID_DRAFT_DICT), status=200)
            provider = OllamaConversationProvider(base_url="http://localhost:11434")
            provider.reply(model="llama3", question="test", context=_EMPTY_CONTEXT)

            req_body = json.loads(rsps.calls[0].request.body)
            assert req_body["model"] == "llama3"
            assert req_body["format"] == "json"
            assert req_body["stream"] is False

    def test_ollama_passes_session(self) -> None:
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = self._ollama_response(_VALID_DRAFT_DICT)
        session.post.return_value = resp

        provider = OllamaConversationProvider(
            base_url="http://localhost:11434", session=session,
        )
        draft = provider.reply(model="llama3", question="test", context=_EMPTY_CONTEXT)
        assert isinstance(draft, ChatAnswerDraft)
        session.post.assert_called_once()


class TestGeminiConversationProvider:
    _URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent"

    def _gemini_response(self, draft_dict: dict) -> dict:
        return {
            "candidates": [
                {"content": {"parts": [{"text": json.dumps(draft_dict, ensure_ascii=False)}]}},
            ],
        }

    def test_gemini_valid_json(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(self._URL, json=self._gemini_response(_VALID_DRAFT_DICT), status=200)
            provider = GeminiConversationProvider(api_key_getter=lambda: "test-key")
            draft = provider.reply(
                model="gemini-pro", question="test", context=_EMPTY_CONTEXT,
            )

        assert isinstance(draft, ChatAnswerDraft)
        assert draft.answer == "這是個不錯的物件。"
        assert len(draft.property_claims) == 1

    def test_gemini_code_fence(self) -> None:
        raw = f"```json\n{json.dumps(_VALID_DRAFT_DICT, ensure_ascii=False)}\n```"
        with responses.RequestsMock() as rsps:
            rsps.post(
                self._URL,
                json={"candidates": [{"content": {"parts": [{"text": raw}]}}]},
                status=200,
            )
            provider = GeminiConversationProvider(api_key_getter=lambda: "test-key")
            draft = provider.reply(
                model="gemini-pro", question="test", context=_EMPTY_CONTEXT,
            )
        assert isinstance(draft, ChatAnswerDraft)
        assert draft.answer == "這是個不錯的物件。"

    def test_gemini_malformed_json(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(
                self._URL,
                json={"candidates": [{"content": {"parts": [{"text": "not json"}]}}]},
                status=200,
            )
            provider = GeminiConversationProvider(api_key_getter=lambda: "test-key")
            with pytest.raises(ProviderError) as exc:
                provider.reply(
                    model="gemini-pro", question="test", context=_EMPTY_CONTEXT,
                )
            assert exc.value.code == "gemini_validation_error"
            assert len(rsps.calls) == 1

    def test_gemini_timeout(self) -> None:
        provider = GeminiConversationProvider(
            api_key_getter=lambda: "test-key", timeout_seconds=1,
        )
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.exceptions.Timeout("timed out")
        provider._session = mock_session

        with pytest.raises(ProviderError) as exc:
            provider.reply(model="gemini-pro", question="test", context=_EMPTY_CONTEXT)
        assert exc.value.code == "gemini_timeout"

    @pytest.mark.parametrize(
        ("status_code", "expected_code"),
        [
            (401, "gemini_auth_failed"),
            (403, "gemini_auth_failed"),
            (429, "gemini_rate_limited"),
            (500, "gemini_unavailable"),
            (503, "gemini_unavailable"),
            (404, "gemini_http_error"),
        ],
    )
    def test_gemini_maps_http_status_without_exposing_body(
        self, status_code: int, expected_code: str,
    ) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(self._URL, body="secret upstream body", status=status_code)
            provider = GeminiConversationProvider(
                api_key_getter=lambda: "test-key",
            )
            with pytest.raises(ProviderError) as exc:
                provider.reply(
                    model="gemini-pro", question="test", context=_EMPTY_CONTEXT,
                )
            assert exc.value.code == expected_code
            assert "secret upstream body" not in str(exc.value)

    def test_gemini_non_json_response(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(self._URL, body="not json", status=200)
            provider = GeminiConversationProvider(api_key_getter=lambda: "test-key")
            with pytest.raises(ProviderError) as exc:
                provider.reply(
                    model="gemini-pro", question="test", context=_EMPTY_CONTEXT,
                )
            assert exc.value.code == "gemini_validation_error"

    def test_gemini_empty_candidates(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(self._URL, json={"candidates": []}, status=200)
            provider = GeminiConversationProvider(api_key_getter=lambda: "test-key")
            with pytest.raises(ProviderError) as exc:
                provider.reply(
                    model="gemini-pro", question="test", context=_EMPTY_CONTEXT,
                )
            assert exc.value.code == "gemini_validation_error"

    def test_gemini_api_key_not_in_error_message(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.post(self._URL, body="Unauthorized", status=401)
            provider = GeminiConversationProvider(
                api_key_getter=lambda: "secret-key-12345",
            )
            with pytest.raises(ProviderError) as exc:
                provider.reply(
                    model="gemini-pro", question="test", context=_EMPTY_CONTEXT,
                )
            assert "secret-key-12345" not in str(exc.value)
            assert "secret" not in str(exc.value).lower()

    def test_gemini_passes_session(self) -> None:
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = self._gemini_response(_VALID_DRAFT_DICT)
        session.post.return_value = resp

        provider = GeminiConversationProvider(
            api_key_getter=lambda: "test-key", session=session,
        )
        draft = provider.reply(
            model="gemini-pro", question="test", context=_EMPTY_CONTEXT,
        )
        assert isinstance(draft, ChatAnswerDraft)
        session.post.assert_called_once()

    def test_gemini_resolves_key_for_each_request(self) -> None:
        session = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = self._gemini_response(_VALID_DRAFT_DICT)
        session.post.return_value = response
        keys = iter(["first-key", "second-key"])
        provider = GeminiConversationProvider(
            api_key_getter=lambda: next(keys),
            session=session,
        )

        provider.reply(
            model="gemini-pro", question="first", context=_EMPTY_CONTEXT,
        )
        provider.reply(
            model="gemini-pro", question="second", context=_EMPTY_CONTEXT,
        )

        assert session.post.call_args_list[0].kwargs["headers"]["x-goog-api-key"] == (
            "first-key"
        )
        assert session.post.call_args_list[1].kwargs["headers"]["x-goog-api-key"] == (
            "second-key"
        )

    def test_gemini_missing_key_fails_without_http_call(self) -> None:
        session = MagicMock()
        provider = GeminiConversationProvider(
            api_key_getter=lambda: None,
            session=session,
        )

        with pytest.raises(ProviderError) as exc:
            provider.reply(
                model="gemini-pro", question="test", context=_EMPTY_CONTEXT,
            )

        assert exc.value.code == "gemini_auth_missing"
        session.post.assert_not_called()


class TestConversationProviderRegistry:
    def test_provider_registry_register_and_get(self) -> None:
        registry = ConversationProviderRegistry()
        provider = RuleConversationProvider()
        registry.register("rule", provider)
        assert registry.get("rule") is provider

    def test_provider_registry_unknown(self) -> None:
        registry = ConversationProviderRegistry()
        with pytest.raises(ValueError, match="unknown provider"):
            registry.get("nonexistent")


class TestConversationContext:
    def test_context_includes_rolling_summary(self) -> None:
        ctx = ConversationContext(
            rolling_summary="摘要內容",
            recent_messages=(),
            evidence_revision=1,
            evidence_facts=(),
            limitations=(),
        )
        prompt = _build_prompt("問題", ctx)
        assert "摘要內容" in prompt
        assert "<UNTRUSTED_USER_DATA>" in prompt
        assert '"rolling_summary"' in prompt

    def test_context_includes_recent_messages(self) -> None:
        msgs = tuple(
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"訊息{i}"}
            for i in range(15)
        )
        ctx = ConversationContext(
            rolling_summary=None,
            recent_messages=msgs,
            evidence_revision=1,
            evidence_facts=(),
            limitations=(),
        )
        prompt = _build_prompt("問題", ctx)
        assert '"recent_messages"' in prompt
        assert "訊息3" in prompt
        assert '"role": "user"' in prompt
        assert "訊息14" in prompt
        assert "[Rolling Summary]" not in prompt

    def test_context_includes_evidence_facts(self) -> None:
        ctx = ConversationContext(
            rolling_summary=None,
            recent_messages=(),
            evidence_revision=1,
            evidence_facts=(_FACT_1, _FACT_2),
            limitations=(),
        )
        prompt = _build_prompt("問題", ctx)
        assert '"evidence_facts"' in prompt
        assert "f001" in prompt
        assert "開價總價" in prompt
        assert "f002" in prompt

    def test_context_includes_limitations(self) -> None:
        ctx = ConversationContext(
            rolling_summary=None,
            recent_messages=(),
            evidence_revision=1,
            evidence_facts=(),
            limitations=("缺少車站距離", "缺少座標"),
        )
        prompt = _build_prompt("問題", ctx)
        assert '"limitations"' in prompt
        assert "缺少車站距離" in prompt
        assert "缺少座標" in prompt

    def test_context_includes_user_question(self) -> None:
        ctx = ConversationContext(
            rolling_summary=None,
            recent_messages=(),
            evidence_revision=1,
            evidence_facts=(),
            limitations=(),
        )
        prompt = _build_prompt("這個物件怎麼樣？", ctx)
        assert '"question"' in prompt
        assert "這個物件怎麼樣？" in prompt

    def test_context_only_latest_12_messages(self) -> None:
        msgs = tuple(
            {"role": "user", "content": f"msg{i}"}
            for i in range(20)
        )
        ctx = ConversationContext(
            rolling_summary=None,
            recent_messages=msgs,
            evidence_revision=1,
            evidence_facts=(),
            limitations=(),
        )
        prompt = _build_prompt("問題", ctx)
        assert "msg8" in prompt
        assert "msg19" in prompt
        assert "msg0" not in prompt

    def test_context_evidence_revision(self) -> None:
        ctx = ConversationContext(
            rolling_summary=None,
            recent_messages=(),
            evidence_revision=5,
            evidence_facts=(),
            limitations=(),
        )
        assert ctx.evidence_revision == 5
