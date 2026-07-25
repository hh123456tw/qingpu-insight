from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_ids: tuple[str, ...] = Field(min_length=1, max_length=5)
    budget_twd: int | None = None
    intended_use: Literal["self_use", "rental_reference"]
    provider: Literal["rule", "ollama", "gemini"]


class EvidenceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(max_length=64)
    listing_type: Literal["sale", "newhouse", "rental"]


class EvidenceFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_id: str = Field(max_length=20)
    kind: str = Field(max_length=64)
    label: str = Field(max_length=256)
    value: str = Field(max_length=256)
    unit: str = Field(max_length=64)
    source_type: str = Field(max_length=64)
    source_version: str = Field(max_length=64)
    observed_at: str = Field(max_length=32)


class EvidencePack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pack_id: str = Field(max_length=64)
    dataset_version: str = Field(max_length=64)
    generated_at: str = Field(max_length=32)
    candidates: tuple[EvidenceCandidate, ...]
    facts: tuple[EvidenceFact, ...]
    limitations: tuple[str, ...]


class ReportClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(max_length=2000)
    fact_ids: tuple[str, ...]
    numeric_fact_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _require_evidence_reference(self) -> ReportClaim:
        if not self.fact_ids and not self.numeric_fact_ids:
            raise ValueError("at least one evidence reference required")
        if not set(self.numeric_fact_ids).issubset(self.fact_ids):
            raise ValueError("numeric_fact_ids must be a subset of fact_ids")
        return self


class BuyerReportDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: ReportClaim
    advantages: tuple[ReportClaim, ...]
    risks: tuple[ReportClaim, ...]
    negotiation: tuple[ReportClaim, ...]
    limitations: tuple[ReportClaim, ...]

    @model_validator(mode="after")
    def _validate_ranges(self) -> BuyerReportDraft:
        if not 1 <= len(self.advantages) <= 3:
            raise ValueError("advantages must have 1 to 3 items")
        if not 1 <= len(self.risks) <= 3:
            raise ValueError("risks must have 1 to 3 items")
        if not 1 <= len(self.negotiation) <= 3:
            raise ValueError("negotiation must have 1 to 3 items")
        return self


class SavedBuyerReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str = Field(max_length=64)
    request_hash: str = Field(max_length=64)
    dataset_version: str = Field(max_length=64)
    evidence_pack_id: str = Field(max_length=64)
    provider: str = Field(max_length=32)
    model: str = Field(max_length=64)
    content: dict
    fallback_reason: str | None = None
    validation_codes: tuple[str, ...] = ()
    latency_ms: float = 0
    created_at: str = Field(max_length=32)
