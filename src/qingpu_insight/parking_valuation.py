from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class ParkingPriceStat:
    price_twd: int
    sample_size: int


@dataclass(frozen=True)
class ParkingPriceEstimate:
    price_twd: int
    sample_size: int
    source: str
    parking_type: str


@dataclass(frozen=True)
class ParkingPricePolicy:
    version: int
    minimum_type_samples: int
    by_type: dict[str, ParkingPriceStat]
    market_fallback: Optional[ParkingPriceStat]


def _normalize(value: object) -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    return ""


def build_parking_price_policy(
    frame: pd.DataFrame, *, minimum_type_samples: int = 20
) -> ParkingPricePolicy:
    types = frame["parking_type"].apply(_normalize)
    prices = pd.to_numeric(frame["parking_price_twd"], errors="coerce")

    valid = (types != "") & (prices > 0)
    valid_types = types[valid]
    valid_prices = prices[valid]

    by_type = {}
    for parking_type in valid_types.unique():
        mask = valid_types == parking_type
        sample_size = mask.sum()
        if sample_size >= minimum_type_samples:
            median = int(round(valid_prices[mask].median()))
            by_type[parking_type] = ParkingPriceStat(median, sample_size)

    market_fallback = None
    if len(valid_prices) > 0:
        market_fallback = ParkingPriceStat(
            int(round(valid_prices.median())), len(valid_prices)
        )

    return ParkingPricePolicy(
        version=1,
        minimum_type_samples=minimum_type_samples,
        by_type=by_type,
        market_fallback=market_fallback,
    )


def estimate_parking_price(
    policy: Optional[ParkingPricePolicy], parking_type: str
) -> Optional[ParkingPriceEstimate]:
    if policy is None:
        return None

    norm_type = _normalize(parking_type)

    if not norm_type:
        return ParkingPriceEstimate(
            price_twd=0, sample_size=0, source="none", parking_type=norm_type
        )

    if norm_type in policy.by_type:
        stat = policy.by_type[norm_type]
        return ParkingPriceEstimate(
            price_twd=stat.price_twd,
            sample_size=stat.sample_size,
            source="type_median",
            parking_type=norm_type,
        )

    if policy.market_fallback is not None:
        return ParkingPriceEstimate(
            price_twd=policy.market_fallback.price_twd,
            sample_size=policy.market_fallback.sample_size,
            source="market_median",
            parking_type=norm_type,
        )

    return ParkingPriceEstimate(
        price_twd=0, sample_size=0, source="none", parking_type=norm_type
    )
