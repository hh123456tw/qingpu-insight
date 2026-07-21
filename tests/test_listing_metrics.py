from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from qingpu_insight.listing_metrics import ListingFilters, listing_summary, public_events, public_listings


@pytest.fixture
def listing_frame() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "source": "591",
            "source_listing_id": "L001",
            "listing_type": "sale",
            "snapshot_at": datetime(2026, 7, 20, 10, 0, 0, tzinfo=timezone.utc),
            "source_url": "https://sale.591.com.tw/L001",
            "title": "青埔精美三房",
            "asking_price_twd": 18_000_000,
            "monthly_rent_twd": None,
            "building_area_ping": 35.5,
            "building_type": "住宅大樓",
            "bedrooms": 3,
            "living_rooms": 2,
            "bathrooms": 2,
            "building_age_years": 5.0,
            "floor": 8,
            "total_floors": 15,
            "parking_type": "坡道平面",
            "latitude": 25.0123,
            "longitude": 121.2018,
            "station_code": "A18",
        },
        {
            "source": "591",
            "source_listing_id": "L002",
            "listing_type": "sale",
            "snapshot_at": datetime(2026, 7, 20, 10, 0, 0, tzinfo=timezone.utc),
            "source_url": "https://sale.591.com.tw/L002",
            "title": "A17站前大樓",
            "asking_price_twd": 22_000_000,
            "monthly_rent_twd": None,
            "building_area_ping": 48.0,
            "building_type": "住宅大樓",
            "bedrooms": 4,
            "living_rooms": 2,
            "bathrooms": 2,
            "building_age_years": 8.0,
            "floor": 5,
            "total_floors": 12,
            "parking_type": "坡道機械",
            "latitude": 25.0156,
            "longitude": 121.2078,
            "station_code": "A17",
        },
        {
            "source": "591",
            "source_listing_id": "L003",
            "listing_type": "sale",
            "snapshot_at": datetime(2026, 7, 20, 10, 0, 0, tzinfo=timezone.utc),
            "source_url": "https://sale.591.com.tw/L003",
            "title": "A18捷運宅",
            "asking_price_twd": 15_000_000,
            "monthly_rent_twd": None,
            "building_area_ping": 28.0,
            "building_type": "華廈",
            "bedrooms": 2,
            "living_rooms": 1,
            "bathrooms": 1,
            "building_age_years": 3.0,
            "floor": 3,
            "total_floors": 10,
            "parking_type": None,
            "latitude": 25.0098,
            "longitude": 121.2034,
            "station_code": "A18",
        },
        {
            "source": "591",
            "source_listing_id": "L004",
            "listing_type": "rental",
            "snapshot_at": datetime(2026, 7, 20, 10, 0, 0, tzinfo=timezone.utc),
            "source_url": "https://rent.591.com.tw/L004",
            "title": "A19套房出租",
            "asking_price_twd": None,
            "monthly_rent_twd": 18_000,
            "building_area_ping": 15.0,
            "building_type": "公寓",
            "bedrooms": 1,
            "living_rooms": 1,
            "bathrooms": 1,
            "building_age_years": 15.0,
            "floor": 2,
            "total_floors": 5,
            "parking_type": None,
            "latitude": 25.0055,
            "longitude": 121.1955,
            "station_code": "A19",
        },
    ])


@pytest.fixture
def events_frame() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "event_key": "evt001",
            "source": "591",
            "listing_type": "sale",
            "source_listing_id": "L001",
            "event_type": "price_decrease",
            "event_data": '{"previous_price":20000000,"new_price":18000000,"absolute_change":-2000000,"percentage_change":-10.0}',
            "occurred_at": datetime(2026, 7, 19, 8, 0, 0, tzinfo=timezone.utc),
        },
        {
            "event_key": "evt002",
            "source": "591",
            "listing_type": "sale",
            "source_listing_id": "L002",
            "event_type": "listed",
            "event_data": None,
            "occurred_at": datetime(2026, 7, 18, 8, 0, 0, tzinfo=timezone.utc),
        },
        {
            "event_key": "evt003",
            "source": "591",
            "listing_type": "rental",
            "source_listing_id": "L004",
            "event_type": "price_decrease",
            "event_data": '{"previous_price":20000,"new_price":18000,"absolute_change":-2000,"percentage_change":-10.0}',
            "occurred_at": datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc),
        },
    ])


class TestListingSummary:
    def test_keeps_types_and_stations_isolated(self, listing_frame: pd.DataFrame) -> None:
        result = listing_summary(listing_frame, ListingFilters("sale", ("A18",)))
        assert result["listing_type"] == "sale"
        assert result["station_codes"] == ["A18"]
        assert result["active_count"] == 2

    def test_all_stations_default(self, listing_frame: pd.DataFrame) -> None:
        result = listing_summary(listing_frame, ListingFilters("sale"))
        assert result["active_count"] == 3

    def test_rental_uses_monthly_price(self, listing_frame: pd.DataFrame) -> None:
        result = listing_summary(listing_frame, ListingFilters("rental", ("A19",)))
        assert result["active_count"] == 1
        assert result["median_price"] == 18_000

    def test_empty_frame_returns_defaults(self) -> None:
        empty = pd.DataFrame()
        result = listing_summary(empty, ListingFilters("sale"))
        assert result["active_count"] == 0
        assert result["median_price"] is None
        assert result["snapshot_time"] is None

    def test_nullable_prices_are_handled(self) -> None:
        df = pd.DataFrame([
            {"listing_type": "sale", "asking_price_twd": None, "snapshot_at": pd.NaT, "station_code": "A18"},
            {"listing_type": "sale", "asking_price_twd": 10_000_000, "snapshot_at": pd.NaT, "station_code": "A18"},
        ])
        result = listing_summary(df, ListingFilters("sale", ("A18",)))
        assert result["active_count"] == 2
        assert result["median_price"] == 10_000_000


class TestPublicListings:
    def test_never_exposes_private_fields(self, listing_frame: pd.DataFrame) -> None:
        result = public_listings(listing_frame, ListingFilters("sale"))
        raw = str(result)
        for field in ("raw_html", "payload", "phone", "contact_name", "full_address"):
            assert field not in raw

    def test_coordinates_rounded_to_four_decimals(self, listing_frame: pd.DataFrame) -> None:
        result = public_listings(listing_frame, ListingFilters("sale"))
        row = next(r for r in result if r["listing_id"] == "L001")
        lat_str = str(row["latitude"])
        _, frac = lat_str.split(".")
        assert len(frac) <= 4

    def test_pagination_caps_at_limit(self, listing_frame: pd.DataFrame) -> None:
        result = public_listings(listing_frame, ListingFilters("sale", limit=2))
        assert len(result) == 2

    def test_returns_expected_columns(self, listing_frame: pd.DataFrame) -> None:
        result = public_listings(listing_frame, ListingFilters("sale", ("A18",)))
        expected = {"listing_id", "type", "title", "source_url", "station", "area", "price", "event", "status", "latitude", "longitude", "model_evidence", "snapshot_time"}
        assert len(result) == 2
        for row in result:
            assert set(row.keys()) == expected

    def test_empty_frame(self) -> None:
        assert public_listings(pd.DataFrame(), ListingFilters("sale")) == []


class TestPublicEvents:
    def test_filters_by_type(self, events_frame: pd.DataFrame) -> None:
        result = public_events(events_frame, ListingFilters("sale"))
        assert len(result) == 2
        assert all(r["type"] == "sale" for r in result)

    def test_parses_json_event_data(self, events_frame: pd.DataFrame) -> None:
        result = public_events(events_frame, ListingFilters("sale"))
        evt = next(r for r in result if r["event_key"] == "evt001")
        assert isinstance(evt["event_data"], dict)
        assert evt["event_data"]["percentage_change"] == -10.0

    def test_empty_frame(self) -> None:
        assert public_events(pd.DataFrame(), ListingFilters("sale")) == []
