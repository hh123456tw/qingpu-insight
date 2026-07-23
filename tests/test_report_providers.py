from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from qingpu_insight.report_contracts import (
    BuyerReportDraft,
    EvidenceCandidate,
    EvidenceFact,
    EvidencePack,
    ReportClaim,
)
from qingpu_insight.report_providers import (
    MockReportProvider,
    RuleReportProvider,
)


def _fact_id(version: str, cid: str, kind: str, unit: str) -> str:
    raw = f"{version}|{cid}|{kind}|{unit}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


_NOW = datetime.now(UTC).isoformat()
_VER = "v1"

_FACT_IDS = {
    "asking_price": _fact_id(_VER, "c1", "asking_price", "twd"),
    "unit_price": _fact_id(_VER, "c1", "unit_price", "twd_per_ping"),
    "area": _fact_id(_VER, "c1", "area", "ping"),
    "building_age": _fact_id(_VER, "c1", "building_age", "years"),
    "station_distance": _fact_id(_VER, "c1", "station_distance", "m"),
    "model_interval": _fact_id(_VER, "c1", "model_interval", "twd"),
    "data_freshness": _fact_id(_VER, "c1", "data_freshness", "iso"),
    "location_evidence": _fact_id(_VER, "c1", "location_evidence", "method"),
    "nearby_txns": _fact_id(_VER, "c1", "nearby_transactions_summary", "count"),
}

_FACTS = (
    EvidenceFact(
        fact_id=_FACT_IDS["asking_price"],
        kind="asking_price",
        label="Asking Price",
        value="15000000",
        unit="twd",
        source_type="listing",
        source_version=_VER,
        observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id=_FACT_IDS["unit_price"],
        kind="unit_price",
        label="Unit Price",
        value="500000",
        unit="twd_per_ping",
        source_type="listing",
        source_version=_VER,
        observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id=_FACT_IDS["area"],
        kind="area",
        label="Building Area",
        value="30.00",
        unit="ping",
        source_type="listing",
        source_version=_VER,
        observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id=_FACT_IDS["building_age"],
        kind="building_age",
        label="Building Age",
        value="5.0",
        unit="years",
        source_type="listing",
        source_version=_VER,
        observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id=_FACT_IDS["station_distance"],
        kind="station_distance",
        label="Station Distance",
        value="A18 300m",
        unit="m",
        source_type="listing",
        source_version=_VER,
        observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id=_FACT_IDS["model_interval"],
        kind="model_interval",
        label="Model Valuation Interval",
        value="13000000-16000000",
        unit="twd",
        source_type="valuation",
        source_version=_VER,
        observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id=_FACT_IDS["data_freshness"],
        kind="data_freshness",
        label="Data Freshness",
        value=_NOW,
        unit="iso",
        source_type="listing",
        source_version=_VER,
        observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id=_FACT_IDS["location_evidence"],
        kind="location_evidence",
        label="Location Method",
        value="structured_address",
        unit="method",
        source_type="listing",
        source_version=_VER,
        observed_at=_NOW,
    ),
    EvidenceFact(
        fact_id=_FACT_IDS["nearby_txns"],
        kind="nearby_transactions_summary",
        label="Nearby Official Transactions",
        value="2 transactions",
        unit="count",
        source_type="market_transactions",
        source_version=_VER,
        observed_at=_NOW,
    ),
)

_CANDIDATE = EvidenceCandidate(candidate_id="c1", listing_type="sale")

PACK = EvidencePack(
    pack_id="test-pack",
    dataset_version=_VER,
    generated_at=_NOW,
    candidates=(_CANDIDATE,),
    facts=_FACTS,
    limitations=(),
)


def all_claims(draft: BuyerReportDraft) -> list[ReportClaim]:
    return [
        draft.summary,
        *draft.advantages,
        *draft.risks,
        *draft.negotiation,
        *draft.limitations,
    ]


class TestRuleReportProvider:
    def test_generates_complete_report_without_network(self) -> None:
        result = RuleReportProvider().generate(PACK)
        assert result.provider == "rule"
        assert result.model == "rule"
        assert result.draft.summary.text
        assert len(result.draft.advantages) >= 1
        assert len(result.draft.risks) >= 1
        assert len(result.draft.negotiation) >= 1
        assert all(claim.fact_ids for claim in all_claims(result.draft))

    def test_all_claims_reference_existing_fact_ids(self) -> None:
        valid_ids = {f.fact_id for f in PACK.facts}
        result = RuleReportProvider().generate(PACK)
        for claim in all_claims(result.draft):
            for fid in claim.fact_ids:
                assert fid in valid_ids, f"fact_id {fid} not in pack"

    def test_no_future_price_or_guaranteed_sale(self) -> None:
        result = RuleReportProvider().generate(PACK)
        text = str(result.draft).lower()
        assert "will rise" not in text
        assert "guaranteed" not in text
        assert "一定會漲" not in text
        assert "保證成交" not in text

    def test_summary_describes_property(self) -> None:
        result = RuleReportProvider().generate(PACK)
        text = result.draft.summary.text
        assert "c1" in text or "C1" in text
        assert any(
            v in text for v in ("15000000", "1500", "坪", "30", "A18", "5", "年")
        )

    def test_limitations_reflect_pack_limitations(self) -> None:
        pack_with_limits = EvidencePack(
            pack_id="test-pack",
            dataset_version=_VER,
            generated_at=_NOW,
            candidates=(_CANDIDATE,),
            facts=_FACTS,
            limitations=("c1: missing nearby transactions",),
        )
        result = RuleReportProvider().generate(pack_with_limits)
        lim_texts = " ".join(c.text for c in result.draft.limitations)
        assert any("missing" in lim_texts.lower() for _ in [1])

    def test_latency_ms_recorded(self) -> None:
        result = RuleReportProvider().generate(PACK)
        assert result.latency_ms >= 0


class TestMockReportProvider:
    def test_returns_configured_draft(self) -> None:
        claim = ReportClaim(text="mock", fact_ids=("f1",), numeric_fact_ids=())
        draft = BuyerReportDraft(
            summary=claim,
            advantages=(claim,),
            risks=(claim,),
            negotiation=(claim,),
            limitations=(claim,),
        )
        provider = MockReportProvider(draft=draft, latency_ms=42)
        result = provider.generate(PACK)
        assert result.provider == "mock"
        assert result.model == "mock"
        assert result.draft is draft
        assert result.latency_ms == 42

    def test_accepts_repair_codes(self) -> None:
        claim = ReportClaim(text="mock", fact_ids=("f1",), numeric_fact_ids=())
        draft = BuyerReportDraft(
            summary=claim,
            advantages=(claim,),
            risks=(claim,),
            negotiation=(claim,),
            limitations=(claim,),
        )
        provider = MockReportProvider(draft=draft)
        result = provider.generate(PACK, repair_codes=("unknown_fact",))
        assert result.provider == "mock"
        assert result.latency_ms >= 0
