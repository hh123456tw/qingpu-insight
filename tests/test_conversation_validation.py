from __future__ import annotations

import pytest
from pydantic import ValidationError

from qingpu_insight.conversation_validation import (
    ChatAnswerDraft,
    GroundingValidationError,
    PropertyClaim,
    ValidatedChatAnswer,
    validate_chat_answer,
)


class TestPropertyClaim:
    def test_valid(self) -> None:
        claim = PropertyClaim(text="附近有公園", fact_ids=["fact-1", "fact-2"])
        assert claim.text == "附近有公園"
        assert claim.fact_ids == ["fact-1", "fact-2"]

    def test_rejects_empty_text(self) -> None:
        with pytest.raises(ValidationError):
            PropertyClaim(text="", fact_ids=["fact-1"])

    def test_rejects_text_too_long(self) -> None:
        with pytest.raises(ValidationError):
            PropertyClaim(text="x" * 1001, fact_ids=["fact-1"])

    def test_rejects_empty_fact_ids(self) -> None:
        with pytest.raises(ValidationError):
            PropertyClaim(text="附近有公園", fact_ids=[])

    def test_rejects_too_many_fact_ids(self) -> None:
        with pytest.raises(ValidationError):
            PropertyClaim(
                text="附近有公園",
                fact_ids=[f"fact-{i}" for i in range(13)],
            )

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            PropertyClaim(text="附近有公園", fact_ids=["fact-1"], source="web")  # type: ignore[call-arg]


class TestChatAnswerDraft:
    def test_valid(self) -> None:
        draft = ChatAnswerDraft(
            answer="這是一個好物件。",
            property_claims=[PropertyClaim(text="附近有公園", fact_ids=["f1"])],
            general_guidance=["注意交易安全"],
            suggested_questions=["貸款問題？"],
        )
        assert draft.answer == "這是一個好物件。"
        assert len(draft.property_claims) == 1
        assert len(draft.general_guidance) == 1
        assert len(draft.suggested_questions) == 1

    def test_valid_minimal(self) -> None:
        draft = ChatAnswerDraft(answer="簡單回答。")
        assert draft.answer == "簡單回答。"
        assert draft.property_claims == []
        assert draft.general_guidance == []
        assert draft.suggested_questions == []

    def test_rejects_empty_answer(self) -> None:
        with pytest.raises(ValidationError):
            ChatAnswerDraft(answer="")

    def test_rejects_answer_too_long(self) -> None:
        with pytest.raises(ValidationError):
            ChatAnswerDraft(answer="x" * 8001)

    def test_rejects_too_many_claims(self) -> None:
        with pytest.raises(ValidationError):
            ChatAnswerDraft(
                answer="test",
                property_claims=[
                    PropertyClaim(text=f"t{i}", fact_ids=["f1"]) for i in range(31)
                ],
            )

    def test_rejects_too_many_guidance_items(self) -> None:
        with pytest.raises(ValidationError):
            ChatAnswerDraft(
                answer="test",
                general_guidance=[f"item{i}" for i in range(13)],
            )

    def test_rejects_too_many_questions(self) -> None:
        with pytest.raises(ValidationError):
            ChatAnswerDraft(
                answer="test",
                suggested_questions=[f"q{i}" for i in range(7)],
            )

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            ChatAnswerDraft(answer="test", model="gpt4")  # type: ignore[call-arg]


class TestValidateChatAnswer:
    def test_valid_multi_fact_claims(self) -> None:
        draft = ChatAnswerDraft(
            answer="好物件。",
            property_claims=[
                PropertyClaim(text="公園近", fact_ids=["f1", "f2"]),
                PropertyClaim(text="學校近", fact_ids=["f3"]),
            ],
        )
        result = validate_chat_answer(
            draft,
            available_fact_ids={"f1", "f2", "f3"},
            evidence_revision=5,
        )
        assert isinstance(result, ValidatedChatAnswer)
        assert "公園近" in result.answer
        assert "好物件。" in result.answer
        assert result.citations == ["f1", "f2", "f3"]
        assert result.evidence_revision == 5

    def test_unknown_fact_id(self) -> None:
        draft = ChatAnswerDraft(
            answer="test",
            property_claims=[
                PropertyClaim(text="claim", fact_ids=["f1", "f_unknown"])
            ],
        )
        with pytest.raises(GroundingValidationError):
            validate_chat_answer(
                draft, available_fact_ids={"f1"}, evidence_revision=1
            )

    def test_unknown_fact_ids_multiple(self) -> None:
        draft = ChatAnswerDraft(
            answer="test",
            property_claims=[
                PropertyClaim(text="claim", fact_ids=["f1", "f2"]),
                PropertyClaim(text="claim2", fact_ids=["f3", "f_unknown"]),
            ],
        )
        with pytest.raises(GroundingValidationError):
            validate_chat_answer(
                draft, available_fact_ids={"f1", "f2"}, evidence_revision=1
            )

    def test_empty_fact_ids_raises_grounding_error(self) -> None:
        claim = PropertyClaim.model_construct(text="claim", fact_ids=[])
        draft = ChatAnswerDraft(answer="test", property_claims=[claim])
        with pytest.raises(GroundingValidationError):
            validate_chat_answer(
                draft, available_fact_ids={"f1"}, evidence_revision=1
            )

    def test_duplicate_fact_ids_in_claim(self) -> None:
        draft = ChatAnswerDraft(
            answer="test",
            property_claims=[PropertyClaim(text="claim", fact_ids=["f1", "f1"])],
        )
        with pytest.raises(GroundingValidationError):
            validate_chat_answer(
                draft, available_fact_ids={"f1"}, evidence_revision=1
            )

    def test_numbers_in_general_guidance(self) -> None:
        draft = ChatAnswerDraft(
            answer="test",
            general_guidance=["注意事項", "3年內交易"],
        )
        with pytest.raises(GroundingValidationError):
            validate_chat_answer(
                draft, available_fact_ids=set(), evidence_revision=1
            )

    def test_numbers_in_general_guidance_single_item(self) -> None:
        draft = ChatAnswerDraft(
            answer="test",
            general_guidance=["第1條建議"],
        )
        with pytest.raises(GroundingValidationError):
            validate_chat_answer(
                draft, available_fact_ids=set(), evidence_revision=1
            )

    def test_validated_answer_keeps_citations_out_of_visible_text(self) -> None:
        draft = ChatAnswerDraft(
            answer="test",
            property_claims=[
                PropertyClaim(text="開價總價 2,298 萬", fact_ids=["listing.price"])
            ],
        )
        result = validate_chat_answer(
            draft,
            available_fact_ids={"listing.price"},
            evidence_revision=1,
        )
        assert result.citations == ["listing.price"]
        assert "依據：" not in result.answer

    def test_citation_ordering_and_deduplication(self) -> None:
        draft = ChatAnswerDraft(
            answer="test",
            property_claims=[
                PropertyClaim(text="a", fact_ids=["f1", "f2"]),
                PropertyClaim(text="b", fact_ids=["f2", "f3", "f1"]),
                PropertyClaim(text="c", fact_ids=["f4"]),
            ],
        )
        result = validate_chat_answer(
            draft,
            available_fact_ids={"f1", "f2", "f3", "f4"},
            evidence_revision=1,
        )
        assert result.citations == ["f1", "f2", "f3", "f4"]

    def test_evidence_revision_preserved(self) -> None:
        draft = ChatAnswerDraft(
            answer="test",
            general_guidance=["注意產權"],
        )
        result = validate_chat_answer(
            draft, available_fact_ids=set(), evidence_revision=42
        )
        assert result.evidence_revision == 42

    def test_empty_general_guidance(self) -> None:
        draft = ChatAnswerDraft(
            answer="test",
            property_claims=[
                PropertyClaim(text="已驗證內容", fact_ids=["f1"])
            ],
        )
        result = validate_chat_answer(
            draft, available_fact_ids={"f1"}, evidence_revision=1
        )
        assert result.general_guidance == []

    def test_general_guidance_labeled(self) -> None:
        draft = ChatAnswerDraft(
            answer="test",
            general_guidance=["注意交易安全", "確認產權"],
        )
        result = validate_chat_answer(
            draft, available_fact_ids=set(), evidence_revision=1
        )
        assert len(result.general_guidance) == 2
        for item in result.general_guidance:
            assert item.startswith("【一般建議】")

    def test_general_guidance_no_double_label(self) -> None:
        draft = ChatAnswerDraft(
            answer="test",
            general_guidance=["【一般建議】注意交易安全"],
        )
        result = validate_chat_answer(
            draft, available_fact_ids=set(), evidence_revision=1
        )
        # Should not double-prefix
        assert result.general_guidance == ["【一般建議】注意交易安全"]

    def test_suggested_questions_preserved(self) -> None:
        draft = ChatAnswerDraft(
            answer="test",
            general_guidance=["注意產權"],
            suggested_questions=["Q1?", "Q2?"],
        )
        result = validate_chat_answer(
            draft, available_fact_ids=set(), evidence_revision=1
        )
        assert result.suggested_questions == ["Q1?", "Q2?"]

    def test_answer_is_included_in_display(self) -> None:
        draft = ChatAnswerDraft(
            answer="這是一個沒有證據的非常好的物件。",
            property_claims=[
                PropertyClaim(text="開價一千萬", fact_ids=["listing.price"])
            ],
        )
        result = validate_chat_answer(
            draft,
            available_fact_ids={"listing.price"},
            evidence_revision=1,
        )
        assert "這是一個沒有證據的非常好的物件。" in result.answer
        assert "listing.price" not in result.answer
        assert "listing.price" in result.citations

    def test_no_extra_fields_on_validated_answer(self) -> None:
        draft = ChatAnswerDraft(
            answer="test",
            general_guidance=["注意產權"],
        )
        result = validate_chat_answer(
            draft, available_fact_ids=set(), evidence_revision=1
        )
        with pytest.raises(AttributeError):
            _ = result.property_claims  # type: ignore[attr-defined]
