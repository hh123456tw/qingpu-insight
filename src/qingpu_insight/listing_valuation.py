from datetime import datetime, timezone
from typing import Any

import pandas as pd

from qingpu_insight.listing_normalization import NormalizedListing
from qingpu_insight.model_features import ValuationInput
from qingpu_insight.valuation import ModelRegistry, valuate

LISTING_TYPE_TO_TRANSACTION_TYPE = {
    "sale": "resale",
    "newhouse": "presale",
}

_REQUIRED_FOR_TRANSACTION_TYPE = {
    "resale": (
        "building_area_ping",
        "building_type",
        "bedrooms",
        "living_rooms",
        "bathrooms",
        "floor",
        "total_floors",
        "building_age_years",
    ),
    "presale": (
        "building_area_ping",
        "building_type",
        "bedrooms",
        "living_rooms",
        "bathrooms",
        "floor",
        "total_floors",
    ),
}


def asking_status(asking: int, interval: tuple[int, int]) -> str:
    if asking < interval[0]:
        return "below_range"
    if asking > interval[1]:
        return "above_range"
    return "within_range"


def compare_listing_to_model(
    listing: NormalizedListing,
    registry: ModelRegistry,
    market: pd.DataFrame,
    station_code: str | None = None,
    station_distance_m: float | None = None,
    location_eligible: bool = False,
) -> dict[str, Any]:
    if listing.listing_type == "rental":
        return {"valuation_eligible": False, "reason": "rental_not_supported"}

    transaction_type = LISTING_TYPE_TO_TRANSACTION_TYPE.get(listing.listing_type)
    if transaction_type is None:
        return {
            "valuation_eligible": False,
            "reason": f"unsupported_listing_type:{listing.listing_type}",
        }

    if not location_eligible:
        return {"valuation_eligible": False, "reason": "location_ineligible"}

    required = _REQUIRED_FOR_TRANSACTION_TYPE[transaction_type]
    missing = [f for f in required if getattr(listing, f, None) is None]
    if missing:
        return {
            "valuation_eligible": False,
            "reason": "missing:" + ",".join(sorted(missing)),
        }

    try:
        vin = ValuationInput(
            transaction_type=transaction_type,
            station_code=station_code,
            station_distance_m=station_distance_m,
            building_area_ping=listing.building_area_ping,
            building_type=listing.building_type,
            bedrooms=listing.bedrooms,
            living_rooms=listing.living_rooms,
            bathrooms=listing.bathrooms,
            building_age_years=listing.building_age_years,
            floor=listing.floor,
            total_floors=listing.total_floors,
            parking_type=listing.parking_type or "",
            parking_area_ping=0,
            asking_total_price_twd=listing.asking_price_twd,
        )
    except (ValueError, TypeError) as exc:
        return {"valuation_eligible": False, "reason": f"validation_error:{exc}"}

    valuation = valuate(vin, registry, market)

    estimate = valuation["estimated_total_price_twd"]
    interval_low, interval_high = valuation["interval_total_price_twd"]
    asking = listing.asking_price_twd

    if estimate > 0 and asking is not None:
        asking_gap_pct = round((asking - estimate) / estimate * 100, 2)
    else:
        asking_gap_pct = None

    status = asking_status(asking, (interval_low, interval_high))

    return {
        "valuation_eligible": True,
        "model_version": valuation["model"]["version"],
        "model_name": valuation["model"]["name"],
        "valued_at": valuation["data_date"],
        "estimated_total_price_twd": estimate,
        "interval_total_price_twd": (interval_low, interval_high),
        "asking_gap_pct": asking_gap_pct,
        "status": status,
        "confidence": valuation["confidence"],
    }
