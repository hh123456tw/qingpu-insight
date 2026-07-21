"""Normalize raw SourceListing records into a fixed contract."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

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


def _validate_url(url: str) -> None:
    if not url.startswith("https://") or "591.com.tw" not in url:
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

    asking_price = payload.get("asking_price_twd")
    monthly_rent = payload.get("monthly_rent_twd")

    raw_area = payload.get("area_ping")
    building_area_ping = float(raw_area) if raw_area else None

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
        asking_price_twd=asking_price if isinstance(asking_price, int) else None,
        monthly_rent_twd=monthly_rent if isinstance(monthly_rent, int) else None,
        building_area_ping=building_area_ping,
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
    raw_hash = _compute_raw_hash(stable)

    return NormalizedListing(
        source="591",
        source_listing_id=source.source_listing_id,
        listing_type=source.listing_type,
        snapshot_at=snapshot_at,
        source_url=source.source_url,
        title=str(payload.get("title", "")),
        asking_price_twd=asking_price if isinstance(asking_price, int) else None,
        monthly_rent_twd=monthly_rent if isinstance(monthly_rent, int) else None,
        building_area_ping=building_area_ping,
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
        raw_hash=raw_hash,
    )
