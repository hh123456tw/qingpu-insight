"""Tests for listing normalization."""

import math
from datetime import UTC, datetime, timedelta, timezone, tzinfo

import pytest

from qingpu_insight.listing_591 import SourceListing
from qingpu_insight.listing_normalization import normalize_listing

SNAPSHOT_AT = datetime(2026, 7, 21, 12, 0, 0)
ALT_SNAPSHOT = datetime(2026, 7, 22, 8, 0, 0)


class OffsetlessTimezone(tzinfo):
    """A tzinfo object that intentionally behaves like a naive datetime."""

    def utcoffset(self, dt):
        return None

    def dst(self, dt):
        return None

    def tzname(self, dt):
        return "offsetless"


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
            "asking_price_twd": None,
            "asking_unit_price_low_twd_per_ping": 500_000,
            "asking_unit_price_high_twd_per_ping": 560_000,
            "area_min_ping": 19.0,
            "area_max_ping": 30.0,
            "representation": "jsonld",
            "schema_version": "591-newhouse-jsonld-v1",
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
    assert row.asking_price_twd is None
    assert row.monthly_rent_twd is None
    assert row.asking_unit_price_low_twd_per_ping == 500_000
    assert row.asking_unit_price_high_twd_per_ping == 560_000
    assert row.building_area_min_ping == 19.0
    assert row.building_area_max_ping == 30.0
    assert row.acquisition_representation == "jsonld"
    assert row.acquisition_schema_version == "591-newhouse-jsonld-v1"


def test_rental_normalization(source_rental):
    row = normalize_listing(source_rental, SNAPSHOT_AT)
    assert row.monthly_rent_twd == 15_000
    assert row.asking_price_twd is None


def test_rental_normalization_discards_unit_price_ranges(source_rental):
    ranged_rental = SourceListing(
        source_listing_id=source_rental.source_listing_id,
        listing_type=source_rental.listing_type,
        source_url=source_rental.source_url,
        payload=dict(
            source_rental.payload,
            asking_unit_price_low_twd_per_ping=1_000,
            asking_unit_price_high_twd_per_ping=2_000,
        ),
    )

    row = normalize_listing(ranged_rental, SNAPSHOT_AT)

    assert row.asking_unit_price_low_twd_per_ping is None
    assert row.asking_unit_price_high_twd_per_ping is None


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
    assert row.location_method == "source_coordinates"
    assert row.location_confidence == "high"
    assert row.location_reason == "valid_source_coordinates"
    assert row.geocoded_at is None
    assert row.geocoder_version is None
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
    assert row.location_method == "unknown"
    assert row.location_confidence == "unknown"
    assert row.location_reason == "missing_or_invalid_source_coordinates"


@pytest.mark.parametrize(
    ("latitude", "longitude", "is_valid"),
    [
        (True, 121.215, False),
        (25.002, False, False),
        (math.nan, 121.215, False),
        (25.002, math.nan, False),
        (math.inf, 121.215, False),
        (25.002, -math.inf, False),
        (20.0, 121.215, False),
        (30.0, 121.215, False),
        (25.002, 115.0, False),
        (25.002, 125.0, False),
        (20.000_001, 121.215, True),
        (25.002, 115.000_001, True),
    ],
)
def test_source_coordinate_validation_handles_invalid_values_and_boundaries(
    source_sale, latitude, longitude, is_valid
):
    source = SourceListing(
        source_listing_id=source_sale.source_listing_id,
        listing_type=source_sale.listing_type,
        source_url=source_sale.source_url,
        payload=dict(source_sale.payload, lat=latitude, lng=longitude),
    )

    row = normalize_listing(source, SNAPSHOT_AT)

    if is_valid:
        assert (row.latitude, row.longitude) == (float(latitude), float(longitude))
        assert row.location_method == "source_coordinates"
        assert row.location_confidence == "high"
    else:
        assert (row.latitude, row.longitude) == (None, None)
        assert row.location_method == "unknown"
        assert row.location_confidence == "unknown"
        assert row.location_reason == "missing_or_invalid_source_coordinates"


def test_complete_structured_address_metadata_is_preserved_in_utc(source_sale):
    observed_at = datetime(2026, 7, 21, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    source = SourceListing(
        source_listing_id=source_sale.source_listing_id,
        listing_type=source_sale.listing_type,
        source_url=source_sale.source_url,
        payload=dict(
            source_sale.payload,
            structured_address="桃園市中壢區青埔路 1 號",
            address_source_url="https://newhouse.591.com.tw/home/ABC",
            address_observed_at=observed_at,
        ),
    )

    row = normalize_listing(source, SNAPSHOT_AT)

    assert row.structured_address == "桃園市中壢區青埔路 1 號"
    assert row.address_source_url == "https://newhouse.591.com.tw/home/ABC"
    assert row.address_observed_at == datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


def test_offsetless_timezone_is_treated_as_utc_for_address_provenance(source_sale):
    offsetless = SourceListing(
        source_listing_id=source_sale.source_listing_id,
        listing_type=source_sale.listing_type,
        source_url=source_sale.source_url,
        payload=dict(
            source_sale.payload,
            structured_address="桃園市中壢區青埔路 1 號",
            address_source_url="https://newhouse.591.com.tw/home/ABC",
            address_observed_at=datetime(2026, 7, 21, 12, 0, tzinfo=OffsetlessTimezone()),
        ),
    )
    naive = SourceListing(
        source_listing_id=source_sale.source_listing_id,
        listing_type=source_sale.listing_type,
        source_url=source_sale.source_url,
        payload=dict(
            offsetless.payload,
            address_observed_at=datetime(2026, 7, 21, 12, 0),
        ),
    )

    offsetless_row = normalize_listing(offsetless, SNAPSHOT_AT)
    naive_row = normalize_listing(naive, SNAPSHOT_AT)

    assert offsetless_row.address_observed_at == datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    assert offsetless_row.raw_hash == naive_row.raw_hash


def test_same_address_observed_instant_has_the_same_utc_value_and_hash(source_sale):
    common = dict(
        source_sale.payload,
        structured_address="桃園市中壢區青埔路 1 號",
        address_source_url="https://newhouse.591.com.tw/home/ABC",
    )
    utc_source = SourceListing(
        source_listing_id=source_sale.source_listing_id,
        listing_type=source_sale.listing_type,
        source_url=source_sale.source_url,
        payload=dict(common, address_observed_at=datetime(2026, 7, 21, 12, 0, tzinfo=UTC)),
    )
    taipei_source = SourceListing(
        source_listing_id=source_sale.source_listing_id,
        listing_type=source_sale.listing_type,
        source_url=source_sale.source_url,
        payload=dict(
            common,
            address_observed_at=datetime(
                2026, 7, 21, 20, 0, tzinfo=timezone(timedelta(hours=8))
            ),
        ),
    )

    utc_row = normalize_listing(utc_source, SNAPSHOT_AT)
    taipei_row = normalize_listing(taipei_source, SNAPSHOT_AT)

    assert utc_row.address_observed_at == taipei_row.address_observed_at
    assert utc_row.raw_hash == taipei_row.raw_hash


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("structured_address", 123),
        ("address_source_url", 123),
        ("address_observed_at", "2026-07-21T12:00:00Z"),
    ],
)
def test_incomplete_or_invalid_address_metadata_is_discarded(source_sale, field, value):
    payload = dict(
        source_sale.payload,
        structured_address="桃園市中壢區青埔路 1 號",
        address_source_url="https://newhouse.591.com.tw/home/ABC",
        address_observed_at=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
    )
    payload[field] = value
    source = SourceListing(
        source_listing_id=source_sale.source_listing_id,
        listing_type=source_sale.listing_type,
        source_url=source_sale.source_url,
        payload=payload,
    )

    row = normalize_listing(source, SNAPSHOT_AT)

    assert row.structured_address is None
    assert row.address_source_url is None
    assert row.address_observed_at is None


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


def test_raw_hash_changes_when_complete_address_provenance_differs(source_sale):
    observed_at = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    first = SourceListing(
        source_listing_id=source_sale.source_listing_id,
        listing_type=source_sale.listing_type,
        source_url=source_sale.source_url,
        payload=dict(
            source_sale.payload,
            structured_address="桃園市中壢區青埔路 1 號",
            address_source_url="https://newhouse.591.com.tw/home/ABC",
            address_observed_at=observed_at,
        ),
    )
    second = SourceListing(
        source_listing_id=source_sale.source_listing_id,
        listing_type=source_sale.listing_type,
        source_url=source_sale.source_url,
        payload=dict(first.payload, structured_address="桃園市中壢區青埔路 2 號"),
    )

    assert normalize_listing(first, SNAPSHOT_AT).raw_hash != normalize_listing(
        second, SNAPSHOT_AT
    ).raw_hash


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("asking_unit_price_low_twd_per_ping", 510_000),
        ("asking_unit_price_high_twd_per_ping", 570_000),
        ("area_min_ping", 20.0),
        ("area_max_ping", 31.0),
        ("representation", "dom"),
        ("schema_version", "591-newhouse-jsonld-v2"),
    ],
)
def test_raw_hash_changes_when_range_or_acquisition_field_changes(
    source_newhouse, field, replacement
):
    changed = SourceListing(
        source_listing_id=source_newhouse.source_listing_id,
        listing_type=source_newhouse.listing_type,
        source_url=source_newhouse.source_url,
        payload=dict(source_newhouse.payload, **{field: replacement}),
    )

    assert normalize_listing(source_newhouse, SNAPSHOT_AT).raw_hash != normalize_listing(
        changed, SNAPSHOT_AT
    ).raw_hash


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("asking_price_twd", 0),
        ("monthly_rent_twd", -1),
        ("area_ping", 0),
        ("asking_unit_price_low_twd_per_ping", -1),
        ("asking_unit_price_high_twd_per_ping", 0),
        ("area_min_ping", -1.0),
        ("area_max_ping", 0.0),
    ],
)
def test_non_positive_monetary_and_area_values_are_rejected(
    source_newhouse, field, invalid
):
    invalid_source = SourceListing(
        source_listing_id=source_newhouse.source_listing_id,
        listing_type=source_newhouse.listing_type,
        source_url=source_newhouse.source_url,
        payload=dict(source_newhouse.payload, **{field: invalid}),
    )

    with pytest.raises(ValueError, match=field):
        normalize_listing(invalid_source, SNAPSHOT_AT)


@pytest.mark.parametrize(
    ("low_field", "high_field"),
    [
        (
            "asking_unit_price_low_twd_per_ping",
            "asking_unit_price_high_twd_per_ping",
        ),
        ("area_min_ping", "area_max_ping"),
    ],
)
def test_inverted_ranges_are_rejected(source_newhouse, low_field, high_field):
    invalid_source = SourceListing(
        source_listing_id=source_newhouse.source_listing_id,
        listing_type=source_newhouse.listing_type,
        source_url=source_newhouse.source_url,
        payload=dict(
            source_newhouse.payload,
            **{low_field: source_newhouse.payload[high_field] + 1},
        ),
    )

    with pytest.raises(ValueError, match="range"):
        normalize_listing(invalid_source, SNAPSHOT_AT)


def test_raw_hash_format(source_sale):
    row = normalize_listing(source_sale, SNAPSHOT_AT)
    assert len(row.raw_hash) == 64
    assert all(c in "0123456789abcdef" for c in row.raw_hash)
