"""The radar values listings through the same adapter as the 591 assistant."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from qingpu_insight import conversation_valuation


class InMemoryMarketDataSource:
    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def load(self, filters) -> pd.DataFrame:
        del filters
        return self._frame.copy()


PAYLOAD = {
    "listing_type": "sale",
    "area_ping": "40.32",
    "layout": "3房2廳2衛",
    "building_type": "住宅大樓",
    "floor": "12F/15F",
    "total_floors": 15,
    "age_years": "1.5",
    "parking_type": "10. 32坪，平面式，已含售金內",
    "total_price_twd": 15800000,
    "latitude": "25.01",
    "longitude": "121.21",
    "main_building_area_ping": "20",
    "auxiliary_building_area_ping": "2",
    "common_area_ping": "8",
}


def _patch(monkeypatch: pytest.MonkeyPatch, model: dict[str, Any], **extra: Any) -> None:
    monkeypatch.setattr(
        conversation_valuation, "build_model_frame", lambda frame, transaction_type: frame
    )

    def fake_valuate(input_, registry, frame, latest_data_date):
        return {
            "estimated_total_price_twd": 16000000,
            "interval_total_price_twd": (14000000, 18000000),
            "confidence": "medium",
            "confidence_reasons": [],
            "data_date": "2026-06-13",
            "model": model,
            **extra,
        }

    monkeypatch.setattr(conversation_valuation, "valuate", fake_valuate)


def _market() -> InMemoryMarketDataSource:
    return InMemoryMarketDataSource(
        pd.DataFrame(
            [{"transaction_type": "resale", "transaction_date": pd.Timestamp("2026-06-13")}]
        )
    )


def test_context_reports_anchor_station_and_parking(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, {"version": "official-v3", "price_anchor": "same_building"})

    result, context = conversation_valuation.valuate_listing_with_context(
        _market(), object(), PAYLOAD  # type: ignore[arg-type]
    )

    assert result["point_estimate_twd"] == 16000000
    assert "context" not in result
    assert context["price_anchor"] == "same_building"
    assert context["station_code"] in {"A17", "A18", "A19"}
    assert context["station_distance_m"] > 0
    assert context["net_area_ping"] == pytest.approx(30.0)
    assert context["parking_area_ping"] == pytest.approx(10.32)
    assert context["parking_unverified"] is False
    assert context["degraded"] is False
    assert context["age_years"] == pytest.approx(1.5)


def test_public_valuate_listing_output_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, {"version": "fallback"}, degraded=True, degraded_reason="stale_model")

    public = conversation_valuation.valuate_listing(
        _market(), object(), PAYLOAD  # type: ignore[arg-type]
    )
    _, context = conversation_valuation.valuate_listing_with_context(
        _market(), object(), PAYLOAD  # type: ignore[arg-type]
    )

    assert set(public) == {
        "common_area",
        "point_estimate_twd",
        "estimated_building_price_twd",
        "estimated_parking_price_twd",
        "low_estimate_twd",
        "high_estimate_twd",
        "confidence",
        "confidence_reasons",
        "asking_price_assessment",
        "model_version",
        "dataset_version",
        "comparables",
        "limitations",
    }
    assert context["degraded"] is True
    assert context["degraded_reason"] == "stale_model"
    assert context["price_anchor"] is None


def test_unverified_parking_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, {"version": "official-v3"})
    payload = {**PAYLOAD, "parking_type": "有車位"}

    _, context = conversation_valuation.valuate_listing_with_context(
        _market(), object(), payload  # type: ignore[arg-type]
    )

    assert context["parking_unverified"] is True
    assert context["net_area_ping"] == pytest.approx(40.32)
