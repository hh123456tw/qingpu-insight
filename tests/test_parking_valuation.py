import pandas as pd
import pytest

from qingpu_insight.parking_valuation import (
    ParkingPriceEstimate,
    ParkingPricePolicy,
    ParkingPriceStat,
    build_parking_price_policy,
    estimate_parking_price,
)


def test_build_policy_uses_positive_prices_and_type_threshold():
    frame = pd.DataFrame({
        "parking_type": ["坡道平面"] * 20 + ["坡道機械"] * 2 + [""],
        "parking_price_twd": [1_700_000] * 20 + [800_000, 900_000, 0],
    })
    policy = build_parking_price_policy(frame, minimum_type_samples=20)
    assert policy.by_type["坡道平面"] == ParkingPriceStat(1_700_000, 20)
    assert "坡道機械" not in policy.by_type
    assert policy.market_fallback == ParkingPriceStat(1_700_000, 22)


def test_estimate_uses_type_then_market_fallback():
    policy = ParkingPricePolicy(
        version=1,
        minimum_type_samples=20,
        by_type={"坡道平面": ParkingPriceStat(1_700_000, 40)},
        market_fallback=ParkingPriceStat(1_200_000, 60),
    )
    assert estimate_parking_price(policy, "坡道平面").source == "type_median"
    fallback = estimate_parking_price(policy, "坡道機械")
    assert fallback.price_twd == 1_200_000
    assert fallback.source == "market_median"
    assert estimate_parking_price(policy, "") == ParkingPriceEstimate(
        price_twd=0, sample_size=0, source="none", parking_type=""
    )
