"""Tests for listing normalization."""

from datetime import datetime

import pytest

from qingpu_insight.listing_591 import SourceListing
from qingpu_insight.listing_normalization import normalize_listing

SNAPSHOT_AT = datetime(2026, 7, 21, 12, 0, 0)
ALT_SNAPSHOT = datetime(2026, 7, 22, 8, 0, 0)


@pytest.fixture
def source_sale() -> SourceListing:
    return SourceListing(
        source_listing_id="sale-001",
        listing_type="sale",
        source_url="https://sale.591.com.tw/index.php?h=12345",
        payload={
            "id": "sale-001",
            "url": "https://sale.591.com.tw/index.php?h=12345",
            "title": "領航站三房平車",
            "asking_price_twd": 18_800_000,
            "area_ping": 35.5,
            "layout_rooms": 3,
            "layout_living_rooms": 2,
            "layout_bathrooms": 2,
            "floor": 8,
            "total_floors": 15,
            "lat": 25.002,
            "lng": 121.215,
        },
    )


@pytest.fixture
def source_newhouse() -> SourceListing:
    return SourceListing(
        source_listing_id="newhouse-001",
        listing_type="newhouse",
        source_url="https://newhouse.591.com.tw/home/ABC",
        payload={
            "id": "newhouse-001",
            "url": "https://newhouse.591.com.tw/home/ABC",
            "title": "高鐵站前兩房",
            "asking_price_twd": 12_500_000,
            "area_ping": 28.0,
            "layout_rooms": 2,
            "layout_living_rooms": 1,
            "layout_bathrooms": 1,
            "floor": 5,
            "total_floors": 12,
            "lat": 25.015,
            "lng": 121.225,
        },
    )


@pytest.fixture
def source_rental() -> SourceListing:
    return SourceListing(
        source_listing_id="rental-001",
        listing_type="rental",
        source_url="https://rent.591.com.tw/rent-detail-999.html",
        payload={
            "id": "rental-001",
            "url": "https://rent.591.com.tw/rent-detail-999.html",
            "title": "體育園區套房",
            "monthly_rent_twd": 15_000,
            "area_ping": 12.0,
            "layout_rooms": 1,
            "layout_living_rooms": 1,
            "layout_bathrooms": 1,
            "floor": 3,
            "total_floors": 7,
            "lat": 24.995,
            "lng": 121.205,
        },
    )


# --- Step 1 tests ---

def test_sale_normalization_never_places_price_in_rent_field(source_sale):
    row = normalize_listing(source_sale, SNAPSHOT_AT)
    assert row.asking_price_twd == 18_800_000
    assert row.monthly_rent_twd is None


def test_newhouse_normalization(source_newhouse):
    row = normalize_listing(source_newhouse, SNAPSHOT_AT)
    assert row.asking_price_twd == 12_500_000
    assert row.monthly_rent_twd is None


def test_rental_normalization(source_rental):
    row = normalize_listing(source_rental, SNAPSHOT_AT)
    assert row.monthly_rent_twd == 15_000
    assert row.asking_price_twd is None


def test_normalization_contract_fields_present(source_sale):
    row = normalize_listing(source_sale, SNAPSHOT_AT)
    assert row.source == "591"
    assert row.source_listing_id == "sale-001"
    assert row.listing_type == "sale"
    assert row.snapshot_at == SNAPSHOT_AT
    assert row.source_url == "https://sale.591.com.tw/index.php?h=12345"
    assert row.title == "領航站三房平車"
    assert row.building_area_ping == 35.5
    assert row.bedrooms == 3
    assert row.living_rooms == 2
    assert row.bathrooms == 2
    assert row.floor == 8
    assert row.total_floors == 15
    assert row.latitude == 25.002
    assert row.longitude == 121.215
    assert row.building_type is None
    assert row.building_age_years is None
    assert row.parking_type is None


def test_missing_coordinates_use_zero_default(source_sale):
    payload = dict(source_sale.payload, lat=0.0, lng=0.0)
    no_coords = SourceListing(
        source_listing_id=source_sale.source_listing_id,
        listing_type=source_sale.listing_type,
        source_url=source_sale.source_url,
        payload=payload,
    )
    row = normalize_listing(no_coords, SNAPSHOT_AT)
    assert row.latitude is None
    assert row.longitude is None


def test_url_host_rejects_non_https(source_sale):
    bad = SourceListing(
        source_listing_id="bad",
        listing_type="sale",
        source_url="http://sale.591.com.tw/",
        payload=source_sale.payload,
    )
    with pytest.raises(ValueError, match="Invalid listing URL"):
        normalize_listing(bad, SNAPSHOT_AT)


def test_url_host_rejects_non_591(source_sale):
    bad = SourceListing(
        source_listing_id="bad",
        listing_type="sale",
        source_url="https://example.com/",
        payload=source_sale.payload,
    )
    with pytest.raises(ValueError, match="Invalid listing URL"):
        normalize_listing(bad, SNAPSHOT_AT)


@pytest.mark.parametrize(
    "url",
    [
        "https://591.com.tw.evil.example/listing/1",
        "https://evil.example/listing/1?source=591.com.tw",
    ],
)
def test_url_host_rejects_591_text_outside_the_hostname(source_sale, url):
    bad = SourceListing(
        source_listing_id="bad",
        listing_type="sale",
        source_url=url,
        payload=source_sale.payload,
    )
    with pytest.raises(ValueError, match="Invalid listing URL"):
        normalize_listing(bad, SNAPSHOT_AT)


def test_raw_hash_is_stable_across_snapshots(source_sale):
    row1 = normalize_listing(source_sale, SNAPSHOT_AT)
    row2 = normalize_listing(source_sale, ALT_SNAPSHOT)
    assert row1.raw_hash == row2.raw_hash


def test_raw_hash_changes_when_content_differs(source_sale, source_newhouse):
    row1 = normalize_listing(source_sale, SNAPSHOT_AT)
    row2 = normalize_listing(source_newhouse, SNAPSHOT_AT)
    assert row1.raw_hash != row2.raw_hash


def test_raw_hash_format(source_sale):
    row = normalize_listing(source_sale, SNAPSHOT_AT)
    assert len(row.raw_hash) == 64
    assert all(c in "0123456789abcdef" for c in row.raw_hash)
