from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from qingpu_insight.report_contracts import (
    BuyerReportDraft,
    EvidenceCandidate,
    EvidenceFact,
    EvidencePack,
    ReportClaim,
    ReportRequest,
)


class TestReportRequest:
    def test_valid_minimal(self) -> None:
        req = ReportRequest(
            candidate_ids=("c1",),
            intended_use="self_use",
            provider="rule",
        )
        assert req.candidate_ids == ("c1",)
        assert req.budget_twd is None

    def test_valid_full(self) -> None:
        req = ReportRequest(
            candidate_ids=("c1", "c2"),
            budget_twd=15_000_000,
            intended_use="rental_reference",
            provider="ollama",
        )
        assert req.budget_twd == 15_000_000

    def test_rejects_empty_candidates(self) -> None:
        with pytest.raises(ValidationError):
            ReportRequest(candidate_ids=(), intended_use="self_use", provider="rule")

    def test_rejects_too_many_candidates(self) -> None:
        with pytest.raises(ValidationError):
            ReportRequest(
                candidate_ids=tuple(f"id-{i}" for i in range(6)),
                intended_use="self_use",
                provider="rule",
            )

    def test_rejects_invalid_intended_use(self) -> None:
        with pytest.raises(ValidationError):
            ReportRequest(
                candidate_ids=("c1",),
                intended_use="investment",
                provider="rule",
            )

    def test_rejects_invalid_provider(self) -> None:
        with pytest.raises(ValidationError):
            ReportRequest(
                candidate_ids=("c1",),
                intended_use="self_use",
                provider="claude",
            )

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            ReportRequest(
                candidate_ids=("c1",),
                intended_use="self_use",
                provider="rule",
                user_profile="abc",
            )

    def test_frozen(self) -> None:
        req = ReportRequest(
            candidate_ids=("c1",), intended_use="self_use", provider="rule"
        )
        with pytest.raises(ValidationError):
            req.candidate_ids = ("c2",)


class TestEvidenceCandidate:
    def test_valid(self) -> None:
        ec = EvidenceCandidate(candidate_id="abc-123", listing_type="sale")
        assert ec.candidate_id == "abc-123"
        assert ec.listing_type == "sale"

    def test_all_listing_types(self) -> None:
        for t in ("sale", "newhouse", "rental"):
            ec = EvidenceCandidate(candidate_id="x", listing_type=t)
            assert ec.listing_type == t

    def test_rejects_invalid_listing_type(self) -> None:
        with pytest.raises(ValidationError):
            EvidenceCandidate(candidate_id="x", listing_type="commercial")

    def test_frozen(self) -> None:
        ec = EvidenceCandidate(candidate_id="x", listing_type="sale")
        with pytest.raises(ValidationError):
            ec.candidate_id = "y"


class TestEvidenceFact:
    def test_valid(self) -> None:
        now = datetime.now(UTC).isoformat()
        fact = EvidenceFact(
            fact_id="a" * 20,
            kind="asking_price",
            label="Asking Price",
            value="15000000",
            unit="twd",
            source_type="listing",
            source_version="2025-01-01",
            observed_at=now,
        )
        assert fact.kind == "asking_price"

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            EvidenceFact(
                fact_id="a" * 20,
                kind="asking_price",
                label="Asking Price",
                value="15000000",
                unit="twd",
                source_type="listing",
                source_version="2025-01-01",
                observed_at="2025-01-01",
                confidential=True,
            )


class TestEvidencePack:
    def test_valid(self) -> None:
        now = datetime.now(UTC).isoformat()
        candidate = EvidenceCandidate(candidate_id="c1", listing_type="sale")
        fact = EvidenceFact(
            fact_id="a" * 20,
            kind="asking_price",
            label="Asking Price",
            value="15000000",
            unit="twd",
            source_type="listing",
            source_version="v1",
            observed_at=now,
        )
        pack = EvidencePack(
            pack_id="pack-1",
            dataset_version="v1",
            generated_at=now,
            candidates=(candidate,),
            facts=(fact,),
            limitations=(),
        )
        assert pack.pack_id == "pack-1"

    def test_minimal(self) -> None:
        now = datetime.now(UTC).isoformat()
        pack = EvidencePack(
            pack_id="p1",
            dataset_version="v1",
            generated_at=now,
            candidates=(),
            facts=(),
            limitations=("no data available",),
        )
        assert len(pack.limitations) == 1


class TestReportClaim:
    def test_valid_with_fact_ids(self) -> None:
        claim = ReportClaim(
            text="價格合理", fact_ids=("abc123",), numeric_fact_ids=()
        )
        assert claim.text == "價格合理"

    def test_valid_with_numeric_fact_ids(self) -> None:
        claim = ReportClaim(
            text="總價偏低", fact_ids=("def456",), numeric_fact_ids=("def456",)
        )
        assert claim.numeric_fact_ids == ("def456",)

    def test_requires_at_least_one_reference(self) -> None:
        with pytest.raises(ValidationError):
            ReportClaim(text="價格便宜 10%", fact_ids=(), numeric_fact_ids=())

    def test_numeric_fact_ids_must_be_subset_of_fact_ids(self) -> None:
        with pytest.raises(ValidationError):
            ReportClaim(
                text="總價100元",
                fact_ids=(),
                numeric_fact_ids=("price-fact",),
            )

    def test_rejects_extra_field(self) -> None:
        with pytest.raises(ValidationError):
            ReportClaim(
                text="ok", fact_ids=("x",), numeric_fact_ids=(), source="web"
            )


class TestBuyerReportDraft:
    @pytest.fixture
    def claim(self) -> ReportClaim:
        return ReportClaim(text="summary", fact_ids=("f1",), numeric_fact_ids=())

    def test_valid(self, claim: ReportClaim) -> None:
        draft = BuyerReportDraft(
            summary=claim,
            advantages=(claim,),
            risks=(claim, claim),
            negotiation=(claim, claim, claim),
            limitations=(claim,),
        )
        assert len(draft.advantages) == 1
        assert len(draft.risks) == 2
        assert len(draft.negotiation) == 3

    def test_rejects_empty_advantages(self, claim: ReportClaim) -> None:
        with pytest.raises(ValidationError):
            BuyerReportDraft(
                summary=claim,
                advantages=(),
                risks=(claim,),
                negotiation=(claim,),
                limitations=(claim,),
            )

    def test_rejects_too_many_advantages(self, claim: ReportClaim) -> None:
        with pytest.raises(ValidationError):
            BuyerReportDraft(
                summary=claim,
                advantages=(claim, claim, claim, claim),
                risks=(claim,),
                negotiation=(claim,),
                limitations=(claim,),
            )

    def test_rejects_empty_risks(self, claim: ReportClaim) -> None:
        with pytest.raises(ValidationError):
            BuyerReportDraft(
                summary=claim,
                advantages=(claim,),
                risks=(),
                negotiation=(claim,),
                limitations=(claim,),
            )

    def test_rejects_too_many_risks(self, claim: ReportClaim) -> None:
        with pytest.raises(ValidationError):
            BuyerReportDraft(
                summary=claim,
                advantages=(claim,),
                risks=(claim, claim, claim, claim),
                negotiation=(claim,),
                limitations=(claim,),
            )

    def test_rejects_empty_negotiation(self, claim: ReportClaim) -> None:
        with pytest.raises(ValidationError):
            BuyerReportDraft(
                summary=claim,
                advantages=(claim,),
                risks=(claim,),
                negotiation=(),
                limitations=(claim,),
            )

    def test_rejects_too_many_negotiation(self, claim: ReportClaim) -> None:
        with pytest.raises(ValidationError):
            BuyerReportDraft(
                summary=claim,
                advantages=(claim,),
                risks=(claim,),
                negotiation=(claim, claim, claim, claim),
                limitations=(claim,),
            )

    def test_rejects_extra_field(self, claim: ReportClaim) -> None:
        with pytest.raises(ValidationError):
            BuyerReportDraft(
                summary=claim,
                advantages=(claim,),
                risks=(claim,),
                negotiation=(claim,),
                limitations=(claim,),
                agent_notes="nope",
            )


class TestEvidenceFactKinds:
    """Verify the allowlisted kinds are accepted."""

    ALLOWED_KINDS = (
        "asking_price",
        "unit_price",
        "area",
        "building_age",
        "station_distance",
        "model_interval",
        "nearby_transactions_summary",
        "data_freshness",
        "location_evidence",
    )

    def test_all_allowed_kinds_are_valid(self) -> None:
        now = datetime.now(UTC).isoformat()
        for kind in self.ALLOWED_KINDS:
            fact = EvidenceFact(
                fact_id="a" * 20,
                kind=kind,
                label=kind,
                value="test",
                unit="unit",
                source_type="listing",
                source_version="v1",
                observed_at=now,
            )
            assert fact.kind == kind
