from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field


class PropertyClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1000)
    fact_ids: list[str] = Field(min_length=1, max_length=12)


class ChatAnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=8000)
    property_claims: list[PropertyClaim] = Field(default_factory=list, max_length=30)
    general_guidance: list[str] = Field(default_factory=list, max_length=12)
    suggested_questions: list[str] = Field(default_factory=list, max_length=6)


class ValidatedChatAnswer(BaseModel):
    answer: str
    citations: list[str]
    evidence_revision: int
    general_guidance: list[str]
    suggested_questions: list[str]


class GroundingValidationError(ValueError):
    pass


def _has_digit(text: str) -> bool:
    return bool(re.search(r"\d", text))


def _collect_citations(claims: list[PropertyClaim]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for claim in claims:
        for fid in claim.fact_ids:
            if fid not in seen:
                seen.add(fid)
                result.append(fid)
    return result


GENERAL_LABEL = "【一般建議】"


def _label_guidance(items: list[str]) -> list[str]:
    return [
        item if item.startswith(GENERAL_LABEL) else f"{GENERAL_LABEL}{item}"
        for item in items
    ]


def _render_validated_answer(
    claims: list[PropertyClaim],
    guidance: list[str],
) -> str:
    sections: list[str] = []
    if claims:
        claim_lines = ["【物件證據】"]
        claim_lines.extend(
            f"- {claim.text}"
            for claim in claims
        )
        sections.append("\n".join(claim_lines))
    if guidance:
        sections.append("\n".join(guidance))
    if not sections:
        raise GroundingValidationError(
            "Answer contains no validated claims or general guidance"
        )
    return "\n\n".join(sections)


def _reject_unknown_fact_ids(
    draft: ChatAnswerDraft, available_fact_ids: set[str]
) -> None:
    for claim in draft.property_claims:
        for fid in claim.fact_ids:
            if fid not in available_fact_ids:
                raise GroundingValidationError(
                    f"Unknown fact ID: {fid}"
                )


def _reject_duplicate_fact_ids_in_claim(draft: ChatAnswerDraft) -> None:
    for claim in draft.property_claims:
        if len(claim.fact_ids) != len(set(claim.fact_ids)):
            raise GroundingValidationError(
                f"Duplicate fact IDs in claim: {claim.fact_ids}"
            )


def _reject_empty_fact_ids(draft: ChatAnswerDraft) -> None:
    for claim in draft.property_claims:
        if not claim.fact_ids:
            raise GroundingValidationError("PropertyClaim has empty fact_ids")


def _reject_numbers_in_guidance(draft: ChatAnswerDraft) -> None:
    for item in draft.general_guidance:
        if _has_digit(item):
            raise GroundingValidationError(
                f"General guidance contains numeric content: {item}"
            )


def validate_chat_answer(
    draft: ChatAnswerDraft,
    *,
    available_fact_ids: set[str],
    evidence_revision: int,
) -> ValidatedChatAnswer:
    _reject_unknown_fact_ids(draft, available_fact_ids)
    _reject_empty_fact_ids(draft)
    _reject_duplicate_fact_ids_in_claim(draft)
    _reject_numbers_in_guidance(draft)
    citations = _collect_citations(draft.property_claims)
    guidance = _label_guidance(draft.general_guidance)
    return ValidatedChatAnswer(
        # Never display the provider's detached free-form answer. The visible
        # response is composed only from claims whose fact IDs were validated.
        answer=_render_validated_answer(draft.property_claims, guidance),
        citations=citations,
        evidence_revision=evidence_revision,
        general_guidance=guidance,
        suggested_questions=draft.suggested_questions,
    )
