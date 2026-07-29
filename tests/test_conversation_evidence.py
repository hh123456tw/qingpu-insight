from __future__ import annotations

from datetime import datetime
from typing import Any

from qingpu_insight.conversation_evidence import (
    ConversationEvidence,
    ConversationEvidenceBuilder,
)
from qingpu_insight.conversation_repository import SnapshotRecord


def _make_snapshot(**overrides: Any) -> SnapshotRecord:
    payload: dict[str, Any] = {
        "listing_type": "sale",
        "source_listing_id": "2/123",
        "title": "測試住宅",
        "total_price_twd": 18800000,
        "unit_price_twd_per_ping": 578000,
        "area_ping": 32.5,
        "layout": "3房2廳2衛",
        "address": "測試路100號",
        "community_name": "測試社區",
        "builder_name": "測試建商",
        "building_type": "住宅大樓",
        "floor": "12",
        "total_floors": 15,
        "age_years": 3.0,
        "parking_type": "坡道平面",
        "latitude": 25.033611,
        "longitude": 121.565000,
        "source_updated_text": "2025-06-01 更新",
    }
    payload.update(overrides)
    return SnapshotRecord(
        id="snap-1",
        conversation_listing_id="listing-1",
        revision=1,
        captured_at=datetime(2025, 6, 1, 12, 0, 0),
        source_url="https://sale.591.com.tw/home/house/detail/2/123.html",
        structured_payload=payload,
        content_sha256="abc123",
    )


# ---------------------------------------------------------------------------
# listing evidence
# ---------------------------------------------------------------------------


def test_sale_evidence() -> None:
    snapshot = _make_snapshot()
    builder = ConversationEvidenceBuilder()
    ev = builder.build(snapshot=snapshot)

    assert isinstance(ev, ConversationEvidence)
    fact_ids = {f.id for f in ev.facts}
    expected_listing = {
        "listing.title",
        "listing.price",
        "listing.unit_price",
        "listing.area",
        "listing.layout",
        "listing.address",
        "listing.community",
        "listing.builder",
        "listing.building_type",
        "listing.floor",
        "listing.age",
        "listing.parking",
        "listing.location",
    }
    assert expected_listing.issubset(fact_ids), f"missing facts: {expected_listing - fact_ids}"

    title_fact = next(f for f in ev.facts if f.id == "listing.title")
    assert title_fact.value == "測試住宅"
    assert title_fact.source == "591 詳細頁"
    assert title_fact.observed_at == "2025-06-01 更新"

    price_fact = next(f for f in ev.facts if f.id == "listing.price")
    assert price_fact.value == "1,880 萬"

    unit_fact = next(f for f in ev.facts if f.id == "listing.unit_price")
    assert unit_fact.value == "57.8 萬／坪"

    location_fact = next(f for f in ev.facts if f.id == "listing.location")
    assert location_fact.value == "25.033611, 121.565"


def test_newhouse_evidence() -> None:
    snapshot = _make_snapshot(listing_type="newhouse")
    builder = ConversationEvidenceBuilder()
    ev = builder.build(snapshot=snapshot)

    fact_ids = {f.id for f in ev.facts}
    assert "listing.title" in fact_ids
    assert "listing.price" in fact_ids
    assert "listing.location" in fact_ids


# ---------------------------------------------------------------------------
# limitations
# ---------------------------------------------------------------------------


def test_missing_coordinates() -> None:
    snapshot = _make_snapshot(latitude=None, longitude=None)
    builder = ConversationEvidenceBuilder()
    ev = builder.build(snapshot=snapshot)

    assert "缺少座標資訊，距離相關分析可能不準確" in ev.limitations
    assert "listing.location" not in {f.id for f in ev.facts}


def test_unavailable_valuation() -> None:
    snapshot = _make_snapshot()
    builder = ConversationEvidenceBuilder(valuation_service=None)
    ev = builder.build(snapshot=snapshot)

    assert "估值模型尚未啟用" in ev.limitations
    assert ev.valuation is None


def test_impossible_floor() -> None:
    snapshot = _make_snapshot(floor="20", total_floors=10)
    builder = ConversationEvidenceBuilder(
        valuation_service=lambda p: {"point_estimate_twd": 1},
    )
    ev = builder.build(snapshot=snapshot)

    assert "樓層資料不一致（20/10），無法進行精確估價" in ev.limitations
    assert ev.valuation is None


def test_few_comparables() -> None:
    snapshot = _make_snapshot()

    def market(payload: dict) -> list[dict]:
        return [
            {"rank": 1, "price_twd": 1000, "distance_m": 100, "transaction_date": "2025-01-01"},
            {"rank": 2, "price_twd": 2000, "distance_m": 200, "transaction_date": "2025-02-01"},
        ]

    builder = ConversationEvidenceBuilder(market_service=market)
    ev = builder.build(snapshot=snapshot)

    assert "相似成交案例不足 3 筆，價格參考性有限" in ev.limitations


# ---------------------------------------------------------------------------
# determinism & stability
# ---------------------------------------------------------------------------


def test_fact_ordering_deterministic() -> None:
    snapshot = _make_snapshot()
    builder = ConversationEvidenceBuilder()
    ev1 = builder.build(snapshot=snapshot)
    ev2 = builder.build(snapshot=snapshot)

    ids1 = tuple(f.id for f in ev1.facts)
    ids2 = tuple(f.id for f in ev2.facts)
    assert ids1 == ids2


def test_stable_fact_ids_across_refreshes() -> None:
    snap1 = _make_snapshot()
    snap2 = _make_snapshot(
        id="snap-2",
        revision=2,
        captured_at=datetime(2025, 6, 2, 12, 0, 0),
        content_sha256="def456",
    )
    builder = ConversationEvidenceBuilder()
    ev1 = builder.build(snapshot=snap1)
    ev2 = builder.build(snapshot=snap2)

    ids1 = tuple(f.id for f in ev1.facts)
    ids2 = tuple(f.id for f in ev2.facts)
    assert ids1 == ids2


# ---------------------------------------------------------------------------
# version tracking
# ---------------------------------------------------------------------------


def test_model_version_recorded() -> None:
    snapshot = _make_snapshot()

    def valuate(payload: dict) -> dict:
        return {
            "point_estimate_twd": 15000000,
            "low_estimate_twd": 13500000,
            "high_estimate_twd": 16500000,
            "confidence": "medium",
            "estimated_building_price_twd": 15000000,
            "estimated_parking_price_twd": 1700000,
            "estimated_total_price_twd": 16700000,
        }

    builder = ConversationEvidenceBuilder(
        valuation_service=valuate,
        dataset_version="2025Q1",
        model_version="v2.1",
    )
    ev = builder.build(snapshot=snapshot)

    assert ev.valuation is not None
    assert ev.valuation["model_version"] == "v2.1"
    assert ev.valuation["dataset_version"] == "2025Q1"


def test_runtime_valuation_versions_and_limitations_are_preserved() -> None:
    snapshot = _make_snapshot()
    builder = ConversationEvidenceBuilder(
        valuation_service=lambda _payload: {
            "point_estimate_twd": 15000000,
            "low_estimate_twd": 13500000,
            "high_estimate_twd": 16500000,
            "confidence": "medium",
            "estimated_building_price_twd": 15000000,
            "estimated_parking_price_twd": 1700000,
            "estimated_total_price_twd": 16700000,
            "model_version": "official-v3",
            "dataset_version": "2026-06-13",
            "limitations": ["捷運距離為推定值"],
        },
    )

    ev = builder.build(snapshot=snapshot)

    assert ev.valuation is not None
    assert ev.valuation["model_version"] == "official-v3"
    assert ev.valuation["dataset_version"] == "2026-06-13"
    assert "捷運距離為推定值" in ev.limitations


# ---------------------------------------------------------------------------
# comparables limit
# ---------------------------------------------------------------------------


def test_comparables_limited_to_10() -> None:
    snapshot = _make_snapshot()

    def market(payload: dict) -> list[dict]:
        return [
            {
                "rank": i,
                "price_twd": i * 1000000,
                "unit_price_per_ping_twd": i * 30000,
                "area_ping": 30.0,
                "distance_m": i * 100,
                "transaction_date": f"2025-{i:02d}-01",
                "selection_reason": f"test comp {i}",
            }
            for i in range(1, 13)
        ]

    builder = ConversationEvidenceBuilder(market_service=market)
    ev = builder.build(snapshot=snapshot)

    assert len(ev.comparables) == 10


def test_model_comparables_take_priority_over_market_fallback() -> None:
    snapshot = _make_snapshot()
    model_comparables = [
        {
            "record_id": f"model-{index}",
            "similarity_score": 0.7,
            "dwelling_unit_price_per_ping_twd": 400000 + index,
        }
        for index in range(3)
    ]

    builder = ConversationEvidenceBuilder(
        valuation_service=lambda _payload: {
            "point_estimate_twd": 15_000_000,
            "low_estimate_twd": 13_500_000,
            "high_estimate_twd": 16_500_000,
            "confidence": "medium",
            "comparables": model_comparables,
        },
        market_service=lambda _payload: [{"record_id": "fallback"}],
    )

    evidence = builder.build(snapshot=snapshot)

    assert list(evidence.comparables) == model_comparables


# ---------------------------------------------------------------------------
# valuation & comparable facts
# ---------------------------------------------------------------------------


def test_valuation_and_comparable_facts_present() -> None:
    snapshot = _make_snapshot()

    def valuate(payload: dict) -> dict:
        return {
            "point_estimate_twd": 15000000,
            "low_estimate_twd": 13500000,
            "high_estimate_twd": 16500000,
            "confidence": "medium",
            "estimated_building_price_twd": 15000000,
            "estimated_parking_price_twd": 1700000,
            "estimated_total_price_twd": 16700000,
        }

    def market(payload: dict) -> list[dict]:
        return [
            {
                "rank": 1,
                "price_twd": 14500000,
                "unit_price_per_ping_twd": 483333,
                "area_ping": 30.0,
                "distance_m": 200,
                "transaction_date": "2025-06-01",
                "selection_reason": "同社區 3 房成交",
            },
            {
                "rank": 2,
                "price_twd": 16000000,
                "unit_price_per_ping_twd": 500000,
                "area_ping": 32.0,
                "distance_m": 350,
                "transaction_date": "2025-05-15",
                "selection_reason": "同區域 3 房成交",
            },
            {
                "rank": 3,
                "price_twd": 13800000,
                "unit_price_per_ping_twd": 460000,
                "area_ping": 30.0,
                "distance_m": 500,
                "transaction_date": "2025-04-20",
                "selection_reason": "同區域 2 房成交",
            },
        ]

    builder = ConversationEvidenceBuilder(
        valuation_service=valuate,
        market_service=market,
    )
    ev = builder.build(snapshot=snapshot)

    fact_ids = {f.id for f in ev.facts}
    assert "valuation.point" in fact_ids
    assert "valuation.low" in fact_ids
    assert "valuation.high" in fact_ids
    assert "valuation.confidence" in fact_ids
    assert "comparable.1.price" in fact_ids
    assert "comparable.2.price" in fact_ids
    assert "comparable.3.price" in fact_ids
    assert "comparable.1.distance" in fact_ids
    assert "comparable.1.date" in fact_ids

    assert ev.valuation is not None
    assert ev.valuation["point_estimate_twd"] == 15000000
    assert ev.valuation["confidence"] == "medium"

    assert not any("相似成交案例不足 3 筆" in msg for msg in ev.limitations)


# ---------------------------------------------------------------------------
# asking-price comparison
# ---------------------------------------------------------------------------


def test_valuation_asking_price_comparison() -> None:
    snapshot = _make_snapshot(
        total_price_twd=22980000,
        unit_price_twd_per_ping=587000,
    )

    def valuate(payload: dict) -> dict:
        return {
            "point_estimate_twd": 19890000,
            "low_estimate_twd": 15380000,
            "high_estimate_twd": 24410000,
            "confidence": "low",
            "estimated_building_price_twd": 19890000,
            "estimated_parking_price_twd": 1700000,
            "estimated_total_price_twd": 21590000,
        }

    builder = ConversationEvidenceBuilder(valuation_service=valuate)
    ev = builder.build(snapshot=snapshot)

    facts = {fact.id: fact.value for fact in ev.facts}
    assert facts["listing.price"] == "2,298 萬"
    assert facts["listing.unit_price"] == "58.7 萬／坪"
    assert facts["valuation.point"] == "1,989 萬"
    assert facts["valuation.asking_gap_amount"] == "高於估值中心 309 萬"
    assert facts["valuation.asking_gap_percent"] == "高於估值中心 15.5%"
    assert facts["valuation.asking_position"] == "仍在合理區間內"
    assert facts["valuation.confidence"] == "低"


def test_asking_price_below_range() -> None:
    snapshot = _make_snapshot(total_price_twd=12000000)

    def valuate(payload: dict) -> dict:
        return {
            "point_estimate_twd": 19890000,
            "low_estimate_twd": 15380000,
            "high_estimate_twd": 24410000,
            "confidence": "medium",
            "estimated_building_price_twd": 19890000,
            "estimated_parking_price_twd": 1700000,
            "estimated_total_price_twd": 21590000,
        }

    builder = ConversationEvidenceBuilder(valuation_service=valuate)
    ev = builder.build(snapshot=snapshot)

    facts = {fact.id: fact.value for fact in ev.facts}
    assert "低於估值中心" in facts["valuation.asking_gap_amount"]
    assert "低於估值中心" in facts["valuation.asking_gap_percent"]
    assert facts["valuation.asking_position"] == "低於合理區間下限"


def test_asking_price_above_range() -> None:
    snapshot = _make_snapshot(total_price_twd=30000000)

    def valuate(payload: dict) -> dict:
        return {
            "point_estimate_twd": 19890000,
            "low_estimate_twd": 15380000,
            "high_estimate_twd": 24410000,
            "confidence": "high",
            "estimated_building_price_twd": 19890000,
            "estimated_parking_price_twd": 1700000,
            "estimated_total_price_twd": 21590000,
        }

    builder = ConversationEvidenceBuilder(valuation_service=valuate)
    ev = builder.build(snapshot=snapshot)

    facts = {fact.id: fact.value for fact in ev.facts}
    assert "高於估值中心" in facts["valuation.asking_gap_amount"]
    assert "高於估值中心" in facts["valuation.asking_gap_percent"]
    assert facts["valuation.asking_position"] == "高於合理區間上限"


def test_asking_price_equal_to_point() -> None:
    snapshot = _make_snapshot(total_price_twd=19890000)

    def valuate(payload: dict) -> dict:
        return {
            "point_estimate_twd": 19890000,
            "low_estimate_twd": 15380000,
            "high_estimate_twd": 24410000,
            "confidence": "low",
            "estimated_building_price_twd": 19890000,
            "estimated_parking_price_twd": 1700000,
            "estimated_total_price_twd": 21590000,
        }

    builder = ConversationEvidenceBuilder(valuation_service=valuate)
    ev = builder.build(snapshot=snapshot)

    facts = {fact.id: fact.value for fact in ev.facts}
    assert facts["valuation.asking_gap_amount"] == "與估值中心一致"
    assert facts["valuation.asking_gap_percent"] == "與估值中心一致"
    assert facts["valuation.asking_position"] == "仍在合理區間內"


def test_asking_gap_missing_when_no_valuation() -> None:
    snapshot = _make_snapshot(total_price_twd=22980000)
    builder = ConversationEvidenceBuilder()
    ev = builder.build(snapshot=snapshot)

    fact_ids = {f.id for f in ev.facts}
    assert "valuation.asking_gap_amount" not in fact_ids
    assert "valuation.asking_gap_percent" not in fact_ids
    assert "valuation.asking_position" not in fact_ids


# ---------------------------------------------------------------------------
# edge: valuation service raises
# ---------------------------------------------------------------------------


def test_valuation_service_raises_adds_limitation() -> None:
    snapshot = _make_snapshot()

    def broken(payload: dict) -> dict:
        raise RuntimeError("service timeout")

    builder = ConversationEvidenceBuilder(valuation_service=broken)
    ev = builder.build(snapshot=snapshot)

    assert any("估值模型暫時無法使用" in msg for msg in ev.limitations)
    assert ev.valuation is None


# ---------------------------------------------------------------------------
# edge: valuation with impossible floor and missing coordinates
# ---------------------------------------------------------------------------


def test_impossible_floor_and_no_coordinates() -> None:
    snapshot = _make_snapshot(
        floor="20",
        total_floors=10,
        latitude=None,
        longitude=None,
    )

    def valuate(payload: dict) -> dict:
        return {"point_estimate_twd": 1}

    builder = ConversationEvidenceBuilder(valuation_service=valuate)
    ev = builder.build(snapshot=snapshot)

    assert "樓層資料不一致（20/10），無法進行精確估價" in ev.limitations
    assert "缺少座標資訊，距離相關分析可能不準確" in ev.limitations
    assert ev.valuation is None
