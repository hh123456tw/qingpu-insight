from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pandas as pd
import pytest

from qingpu_insight.evidence import EvidenceBuilder
from qingpu_insight.report_contracts import (
    EvidencePack,
    ReportRequest,
)

# ---------------------------------------------------------------------------
# Fake repository for deterministic testing
# ---------------------------------------------------------------------------


class FakeEvidenceRepository:
    """Stateful fake that returns hardcoded DataFrames."""

    def __init__(
        self,
        version: str,
        candidates: pd.DataFrame,
        market_evidence: pd.DataFrame,
    ) -> None:
        self._version = version
        self._candidates = candidates
        self._market_evidence = market_evidence

    def current_dataset_version(self) -> str:
        return self._version

    def load_candidates(self, candidate_ids: Sequence[str]) -> pd.DataFrame:
        mask = self._candidates["listing_id"].isin(candidate_ids)
        return self._candidates[mask].copy()

    def load_market_evidence(self, candidate_ids: Sequence[str]) -> pd.DataFrame:
        mask = self._market_evidence["listing_id"].isin(candidate_ids)
        return self._market_evidence[mask].copy()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NOW_ISO = datetime.now(UTC).isoformat()


@pytest.fixture
def base_candidates() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "listing_id": "c1",
            "listing_type": "sale",
            "title": "青埔好宅",
            "price": 15_000_000,
            "price_per_ping": 500_000,
            "building_area_ping": 30.0,
            "building_age_years": 5.0,
            "station_code": "A18",
            "station_distance_m": 300.0,
            "location_eligible": True,
            "location_method": "structured_address",
            "snapshot_at": NOW_ISO,
            "dataset_version": "v1",
        },
        {
            "listing_id": "c2",
            "listing_type": "newhouse",
            "title": "預售新案",
            "price": 18_000_000,
            "price_per_ping": 600_000,
            "building_area_ping": 35.0,
            "building_age_years": None,
            "station_code": "A19",
            "station_distance_m": 500.0,
            "location_eligible": True,
            "location_method": "source_coordinates",
            "snapshot_at": NOW_ISO,
            "dataset_version": "v1",
        },
    ])


@pytest.fixture
def base_market() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "listing_id": "c1",
            "transaction_price": 14_500_000,
            "transaction_date": "2025-06-01",
        },
        {
            "listing_id": "c1",
            "transaction_price": 15_200_000,
            "transaction_date": "2025-05-15",
        },
        {
            "listing_id": "c2",
            "transaction_price": 17_800_000,
            "transaction_date": "2025-06-10",
        },
    ])


@pytest.fixture
def repo(base_candidates: pd.DataFrame, base_market: pd.DataFrame) -> FakeEvidenceRepository:
    return FakeEvidenceRepository(
        version="v1", candidates=base_candidates, market_evidence=base_market,
    )


@pytest.fixture
def request_c1() -> ReportRequest:
    return ReportRequest(
        candidate_ids=("c1",), intended_use="self_use", provider="rule"
    )


@pytest.fixture
def builder(repo: FakeEvidenceRepository) -> EvidenceBuilder:
    return EvidenceBuilder(repo)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEvidenceBuilderDeterminism:
    def test_same_input_yields_same_fact_ids(
        self, builder: EvidenceBuilder, request_c1: ReportRequest
    ) -> None:
        pack1 = builder.build(request_c1)
        pack2 = builder.build(request_c1)
        fact_ids_1 = [f.fact_id for f in pack1.facts]
        fact_ids_2 = [f.fact_id for f in pack2.facts]
        assert fact_ids_1 == fact_ids_2

    def test_facts_stably_sorted(
        self, builder: EvidenceBuilder, request_c1: ReportRequest
    ) -> None:
        pack = builder.build(request_c1)
        fact_ids = [f.fact_id for f in pack.facts]
        assert fact_ids == sorted(fact_ids)

    def test_candidates_stably_sorted(
        self, repo: FakeEvidenceRepository
    ) -> None:
        builder = EvidenceBuilder(repo)
        req = ReportRequest(
            candidate_ids=("c2", "c1"), intended_use="self_use", provider="rule"
        )
        pack = builder.build(req)
        cids = [c.candidate_id for c in pack.candidates]
        assert cids == sorted(cids)


class TestEvidenceBuilderDataQuality:
    def test_missing_data_adds_limitation(
        self, repo: FakeEvidenceRepository, request_c1: ReportRequest
    ) -> None:
        repo._candidates.loc[
            repo._candidates["listing_id"] == "c1", "price"
        ] = None
        builder = EvidenceBuilder(repo)
        pack = builder.build(request_c1)
        assert any("price" in lim or "asking" in lim for lim in pack.limitations) or any(
            "price" in lim.lower() or "asking" in lim.lower()
            for lim in pack.limitations
        )

    def test_dataset_version_mismatch_rows_rejected(
        self, base_candidates: pd.DataFrame, base_market: pd.DataFrame
    ) -> None:
        df = base_candidates.copy()
        extra = pd.DataFrame([{
            "listing_id": "c3",
            "listing_type": "sale",
            "title": "stale",
            "price": 10_000_000,
            "price_per_ping": 400_000,
            "building_area_ping": 25.0,
            "building_age_years": 10.0,
            "station_code": "A17",
            "station_distance_m": 800.0,
            "location_eligible": True,
            "location_method": "manual",
            "snapshot_at": NOW_ISO,
            "dataset_version": "v0",
        }])
        df = pd.concat([df, extra], ignore_index=True)
        repo = FakeEvidenceRepository(version="v1", candidates=df, market_evidence=base_market)
        builder = EvidenceBuilder(repo)
        req = ReportRequest(
            candidate_ids=("c1", "c3"), intended_use="self_use", provider="rule"
        )
        pack = builder.build(req)
        cids = {c.candidate_id for c in pack.candidates}
        assert "c3" not in cids
        assert "c1" in cids

    def test_no_pii_in_output(
        self, base_candidates: pd.DataFrame, base_market: pd.DataFrame
    ) -> None:
        df = base_candidates.copy()
        df["phone"] = "0912345678"
        df["email"] = "test@example.com"
        df["structured_address"] = "桃園市中壢區青埔路一段123號"
        repo = FakeEvidenceRepository(version="v1", candidates=df, market_evidence=base_market)
        builder = EvidenceBuilder(repo)
        req = ReportRequest(
            candidate_ids=("c1",), intended_use="self_use", provider="rule"
        )
        pack = builder.build(req)
        output = str(pack)
        assert "0912345678" not in output
        assert "test@example.com" not in output
        assert "青埔路一段123號" not in output

    def test_allowlisted_kinds_only(
        self, builder: EvidenceBuilder, request_c1: ReportRequest
    ) -> None:
        pack = builder.build(request_c1)
        allowed = {
            "asking_price",
            "unit_price",
            "area",
            "building_age",
            "station_distance",
            "model_interval",
            "nearby_transactions_summary",
            "data_freshness",
            "location_evidence",
        }
        for fact in pack.facts:
            assert fact.kind in allowed, f"unexpected kind: {fact.kind}"

    def test_fact_id_format(
        self, builder: EvidenceBuilder, request_c1: ReportRequest
    ) -> None:
        pack = builder.build(request_c1)
        for fact in pack.facts:
            assert len(fact.fact_id) == 20
            assert all(c in "0123456789abcdef" for c in fact.fact_id)


class TestEvidenceBuilderIntegration:
    def test_returns_evidence_pack(
        self, builder: EvidenceBuilder, request_c1: ReportRequest
    ) -> None:
        pack = builder.build(request_c1)
        assert isinstance(pack, EvidencePack)
        assert pack.dataset_version == "v1"
        assert len(pack.candidates) > 0
        assert len(pack.facts) > 0

    def test_multiple_candidates(
        self, repo: FakeEvidenceRepository
    ) -> None:
        builder = EvidenceBuilder(repo)
        req = ReportRequest(
            candidate_ids=("c1", "c2"), intended_use="self_use", provider="rule"
        )
        pack = builder.build(req)
        assert len(pack.candidates) == 2

    def test_no_market_evidence_still_builds(
        self, base_candidates: pd.DataFrame
    ) -> None:
        empty_market = pd.DataFrame(columns=["listing_id", "transaction_price", "transaction_date"])
        repo = FakeEvidenceRepository(
            version="v1", candidates=base_candidates, market_evidence=empty_market,
        )
        builder = EvidenceBuilder(repo)
        req = ReportRequest(
            candidate_ids=("c1",), intended_use="self_use", provider="rule"
        )
        pack = builder.build(req)
        assert isinstance(pack, EvidencePack)
        assert len(pack.candidates) == 1

    def test_empty_candidates_still_builds(
        self, base_candidates: pd.DataFrame, base_market: pd.DataFrame
    ) -> None:
        empty_candidates = pd.DataFrame(
            columns=[
                "listing_id", "listing_type", "title", "price", "price_per_ping",
                "building_area_ping", "building_age_years", "station_code",
                "station_distance_m", "location_eligible", "location_method",
                "snapshot_at", "dataset_version",
            ]
        )
        repo = FakeEvidenceRepository(
            version="v1", candidates=empty_candidates, market_evidence=base_market,
        )
        builder = EvidenceBuilder(repo)
        req = ReportRequest(
            candidate_ids=("c1",), intended_use="self_use", provider="rule"
        )
        pack = builder.build(req)
        assert len(pack.candidates) == 0
        assert len(pack.facts) == 0
