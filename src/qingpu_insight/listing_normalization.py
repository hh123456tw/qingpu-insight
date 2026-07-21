"""Normalize raw SourceListing records into a fixed contract."""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from qingpu_insight.listing_591 import SourceListing
from qingpu_insight.listing_sources import ListingType


@dataclass(frozen=True)
class NormalizedListing:
    source: str
    source_listing_id: str
    listing_type: ListingType
    snapshot_at: datetime
    source_url: str
    title: str
    asking_price_twd: int | None
    monthly_rent_twd: int | None
    building_area_ping: float | None
    building_type: str | None
    bedrooms: int | None
    living_rooms: int | None
    bathrooms: int | None
    building_age_years: float | None
    floor: int | None
    total_floors: int | None
    parking_type: str | None
    latitude: float | None
    longitude: float | None
    raw_hash: str
    asking_unit_price_low_twd_per_ping: int | None = None
    asking_unit_price_high_twd_per_ping: int | None = None
    building_area_min_ping: float | None = None
    building_area_max_ping: float | None = None
    acquisition_representation: str = "unknown"
    acquisition_schema_version: str = "unknown"


def _validate_url(url: str) -> None:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        hostname == "591.com.tw" or hostname.endswith(".591.com.tw")
    ):
        raise ValueError(f"Invalid listing URL: {url!r}")


def _valid_taiwan_coordinate(lat: float, lng: float) -> bool:
    return 20 < lat < 30 and 115 < lng < 125


def _stable_dict(
    source: str,
    source_listing_id: str,
    listing_type: ListingType,
    source_url: str,
    title: str,
    asking_price_twd: int | None,
    monthly_rent_twd: int | None,
    building_area_ping: float | None,
    asking_unit_price_low_twd_per_ping: int | None,
    asking_unit_price_high_twd_per_ping: int | None,
    building_area_min_ping: float | None,
    building_area_max_ping: float | None,
    acquisition_representation: str,
    acquisition_schema_version: str,
    building_type: str | None,
    bedrooms: int | None,
    living_rooms: int | None,
    bathrooms: int | None,
    building_age_years: float | None,
    floor: int | None,
    total_floors: int | None,
    parking_type: str | None,
    latitude: float | None,
    longitude: float | None,
) -> dict:
    return {
        "source": source,
        "source_listing_id": source_listing_id,
        "listing_type": listing_type,
        "source_url": source_url,
        "title": title,
        "asking_price_twd": asking_price_twd,
        "monthly_rent_twd": monthly_rent_twd,
        "building_area_ping": building_area_ping,
        "asking_unit_price_low_twd_per_ping": asking_unit_price_low_twd_per_ping,
        "asking_unit_price_high_twd_per_ping": asking_unit_price_high_twd_per_ping,
        "building_area_min_ping": building_area_min_ping,
        "building_area_max_ping": building_area_max_ping,
        "acquisition_representation": acquisition_representation,
        "acquisition_schema_version": acquisition_schema_version,
        "building_type": building_type,
        "bedrooms": bedrooms,
        "living_rooms": living_rooms,
        "bathrooms": bathrooms,
        "building_age_years": building_age_years,
        "floor": floor,
        "total_floors": total_floors,
        "parking_type": parking_type,
        "latitude": latitude,
        "longitude": longitude,
    }


def _compute_raw_hash(stable: dict) -> str:
    canonical = json.dumps(stable, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _positive_int(payload: dict[str, object], field: str) -> int | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if value <= 0:
        raise ValueError(f"{field} must be positive when present")
    return value


def _positive_float(payload: dict[str, object], field: str) -> float | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{field} must be positive when present")
    return parsed


def _metadata(payload: dict[str, object], field: str, legacy_field: str) -> str:
    value = payload.get(field, payload.get(legacy_field, "unknown"))
    return value if isinstance(value, str) and value else "unknown"


def _require_ordered_range(
    low: int | float | None,
    high: int | float | None,
    label: str,
) -> None:
    if low is not None and high is not None and low > high:
        raise ValueError(f"{label} range must have low <= high")


def normalize_listing(source: SourceListing, snapshot_at: datetime) -> NormalizedListing:
    _validate_url(source.source_url)

    payload = source.payload

    lat_raw = payload.get("lat", 0)
    lng_raw = payload.get("lng", 0)
    if isinstance(lat_raw, (int, float)) and isinstance(lng_raw, (int, float)):
        if _valid_taiwan_coordinate(float(lat_raw), float(lng_raw)):
            latitude = float(lat_raw)
            longitude = float(lng_raw)
        else:
            latitude = None
            longitude = None
    else:
        latitude = None
        longitude = None

    asking_price = _positive_int(payload, "asking_price_twd")
    monthly_rent = _positive_int(payload, "monthly_rent_twd")
    asking_unit_price_low = _positive_int(
        payload, "asking_unit_price_low_twd_per_ping"
    )
    asking_unit_price_high = _positive_int(
        payload, "asking_unit_price_high_twd_per_ping"
    )
    building_area_ping = _positive_float(payload, "area_ping")
    building_area_min = _positive_float(payload, "area_min_ping")
    building_area_max = _positive_float(payload, "area_max_ping")
    _require_ordered_range(
        asking_unit_price_low, asking_unit_price_high, "asking unit price"
    )
    _require_ordered_range(building_area_min, building_area_max, "building area")

    bedrooms = payload.get("layout_rooms")
    living_rooms = payload.get("layout_living_rooms")
    bathrooms = payload.get("layout_bathrooms")
    floor = payload.get("floor")
    total_floors = payload.get("total_floors")

    stable = _stable_dict(
        source="591",
        source_listing_id=source.source_listing_id,
        listing_type=source.listing_type,
        source_url=source.source_url,
        title=str(payload.get("title", "")),
        asking_price_twd=asking_price,
        monthly_rent_twd=monthly_rent,
        building_area_ping=building_area_ping,
        asking_unit_price_low_twd_per_ping=asking_unit_price_low,
        asking_unit_price_high_twd_per_ping=asking_unit_price_high,
        building_area_min_ping=building_area_min,
        building_area_max_ping=building_area_max,
        acquisition_representation=_metadata(
            payload, "representation", "acquisition_representation"
        ),
        acquisition_schema_version=_metadata(
            payload, "schema_version", "acquisition_schema_version"
        ),
        building_type=None,
        bedrooms=bedrooms if isinstance(bedrooms, int) else None,
        living_rooms=living_rooms if isinstance(living_rooms, int) else None,
        bathrooms=bathrooms if isinstance(bathrooms, int) else None,
        building_age_years=None,
        floor=floor if isinstance(floor, int) else None,
        total_floors=total_floors if isinstance(total_floors, int) else None,
        parking_type=None,
        latitude=latitude,
        longitude=longitude,
    )

    if stable["listing_type"] in ("sale", "newhouse"):
        stable["monthly_rent_twd"] = None
    elif stable["listing_type"] == "rental":
        stable["asking_price_twd"] = None
        stable["asking_unit_price_low_twd_per_ping"] = None
        stable["asking_unit_price_high_twd_per_ping"] = None

    raw_hash = _compute_raw_hash(stable)

    return NormalizedListing(
        **stable,
        snapshot_at=snapshot_at,
        raw_hash=raw_hash,
    )
