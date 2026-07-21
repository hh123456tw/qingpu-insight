"""Tests for listing lifecycle event detection."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from qingpu_insight.listing_events import (
    detect_listing_events,
    event_key,
)
from qingpu_insight.listing_sources import CaptureBatch

T0 = datetime(2026, 7, 20, 0, 0, 0)
T1 = datetime(2026, 7, 21, 0, 0, 0)
T2 = datetime(2026, 7, 22, 0, 0, 0)
T3 = datetime(2026, 7, 23, 0, 0, 0)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

BASE_COLS = [
    "source",
    "listing_type",
    "source_listing_id",
    "snapshot_at",
    "source_url",
    "title",
    "asking_price_twd",
    "monthly_rent_twd",
    "building_area_ping",
    "asking_unit_price_low_twd_per_ping",
    "asking_unit_price_high_twd_per_ping",
    "building_area_min_ping",
    "building_area_max_ping",
    "acquisition_representation",
    "acquisition_schema_version",
    "building_type",
    "bedrooms",
    "living_rooms",
    "bathrooms",
    "building_age_years",
    "floor",
    "total_floors",
    "parking_type",
    "latitude",
    "longitude",
    "raw_hash",
]


def complete_batch(bid: str, listing_type: str = "sale") -> CaptureBatch:
    return CaptureBatch(
        batch_id=bid,
        source="591",
        listing_type=listing_type,
        started_at=T1,
        reached_terminal_page=True,
        errors=[],
    )


def incomplete_batch(bid: str) -> CaptureBatch:
    return CaptureBatch(
        batch_id=bid,
        source="591",
        listing_type="sale",
        started_at=T1,
        reached_terminal_page=False,
        errors=[],
    )


def empty_rows() -> pd.DataFrame:
    return pd.DataFrame(columns=BASE_COLS)


def listing_row(
    listing_id: str = "sale-001",
    listing_type: str = "sale",
    asking_price: int | None = 18_800_000,
    monthly_rent: int | None = None,
    snapshot_at: datetime = T1,
    raw_hash: str = "aaa",
    **overrides,
) -> dict:
    row = {
        "source": "591",
        "listing_type": listing_type,
        "source_listing_id": listing_id,
        "snapshot_at": snapshot_at,
        "source_url": f"https://sale.591.com.tw/{listing_id}",
        "title": "領航站三房平車",
        "asking_price_twd": asking_price,
        "monthly_rent_twd": monthly_rent,
        "building_area_ping": 35.5,
        "asking_unit_price_low_twd_per_ping": None,
        "asking_unit_price_high_twd_per_ping": None,
        "building_area_min_ping": None,
        "building_area_max_ping": None,
        "acquisition_representation": "dom",
        "acquisition_schema_version": f"591-{listing_type}-dom-v1",
        "building_type": "住宅大樓",
        "bedrooms": 3,
        "living_rooms": 2,
        "bathrooms": 2,
        "building_age_years": 6.0,
        "floor": 8,
        "total_floors": 15,
        "parking_type": "坡道平面",
        "latitude": 25.002,
        "longitude": 121.215,
        "raw_hash": raw_hash,
    }
    row.update(overrides)
    return row


def as_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=BASE_COLS)


def with_state(
    rows: list[dict],
    active: bool = True,
    consecutive_absences: int = 0,
    last_seen_batch_id: str = "B0",
) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["listing_key"] = (
        df["source"] + "|" + df["listing_type"] + "|" + df["source_listing_id"]
    )
    df["active"] = active
    df["consecutive_absences"] = consecutive_absences
    df["last_seen_batch_id"] = last_seen_batch_id
    return df


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def active_state() -> pd.DataFrame:
    return with_state(
        [listing_row(snapshot_at=T0, raw_hash="old-hash")]
    )


@pytest.fixture
def sale_state() -> pd.DataFrame:
    return with_state(
        [listing_row("sale-001", "sale", asking_price=10_000_000, snapshot_at=T0, raw_hash="h1")]
    )


@pytest.fixture
def rental_state() -> pd.DataFrame:
    return with_state(
        [listing_row("rent-001", "rental", asking_price=None,
                      monthly_rent=15_000, snapshot_at=T0, raw_hash="h2")]
    )


# ------------------------------------------------------------------
# Listed
# ------------------------------------------------------------------


def test_listed_new_listing():
    result = detect_listing_events(
        pd.DataFrame(),
        as_df([listing_row("new-001")]),
        complete_batch("B1"),
    )
    assert len(result.events) == 1
    assert result.events.iloc[0]["event_type"] == "listed"
    assert result.events.iloc[0]["source_listing_id"] == "new-001"
    assert result.state.iloc[0]["active"]
    assert result.state.iloc[0]["consecutive_absences"] == 0


def test_listed_multiple_new():
    result = detect_listing_events(
        pd.DataFrame(),
        as_df([
            listing_row("a", raw_hash="h1"),
            listing_row("b", raw_hash="h2"),
        ]),
        complete_batch("B1"),
    )
    assert len(result.events) == 2
    assert result.events["event_type"].tolist() == ["listed", "listed"]


# ------------------------------------------------------------------
# Delisted
# ------------------------------------------------------------------


def test_two_complete_absences_are_required_for_delisting(active_state):
    first = detect_listing_events(
        active_state, empty_rows(), complete_batch("B2")
    )
    assert first.events.empty
    assert first.state.loc[0, "consecutive_absences"] == 1

    second = detect_listing_events(
        first.state, empty_rows(), complete_batch("B3")
    )
    assert second.events["event_type"].tolist() == ["delisted"]


def test_incomplete_batch_does_not_increment_absence(active_state):
    result = detect_listing_events(
        active_state, empty_rows(), incomplete_batch("B2")
    )
    assert result.state.loc[0, "consecutive_absences"] == 0
    assert result.events.empty


def test_absent_listing_preserves_ranges_metadata_and_hash():
    advertised = {
        "asking_unit_price_low_twd_per_ping": 500_000,
        "asking_unit_price_high_twd_per_ping": 560_000,
        "building_area_min_ping": 19.0,
        "building_area_max_ping": 30.0,
        "acquisition_representation": "jsonld",
        "acquisition_schema_version": "591-newhouse-jsonld-v1",
        "raw_hash": "range-hash",
    }
    previous = with_state(
        [
            listing_row(
                "newhouse-001",
                "newhouse",
                asking_price=None,
                snapshot_at=T0,
                **advertised,
            )
        ]
    )

    result = detect_listing_events(
        previous,
        empty_rows(),
        complete_batch("B2", listing_type="newhouse"),
    )

    state = result.state.iloc[0]
    for field, expected in advertised.items():
        assert state[field] == expected


@pytest.mark.parametrize("invalid_metadata", [None, float("nan"), "", "   "])
def test_absent_listing_normalizes_invalid_acquisition_metadata(invalid_metadata):
    previous = with_state(
        [
            listing_row(
                acquisition_representation=invalid_metadata,
                acquisition_schema_version=invalid_metadata,
            )
        ]
    )

    result = detect_listing_events(
        previous,
        empty_rows(),
        complete_batch("B2"),
    )

    state = result.state.iloc[0]
    assert state["acquisition_representation"] == "unknown"
    assert state["acquisition_schema_version"] == "unknown"


def test_single_absence_not_delisted(active_state):
    result = detect_listing_events(
        active_state, empty_rows(), complete_batch("B2")
    )
    assert result.events.empty
    assert result.state.loc[0, "active"]


# ------------------------------------------------------------------
# Relisted
# ------------------------------------------------------------------


def test_relisted_after_delisting():
    state = with_state(
        [listing_row("sale-001", snapshot_at=T0, raw_hash="h1")],
        active=False, consecutive_absences=2, last_seen_batch_id="B3",
    )
    result = detect_listing_events(
        state,
        as_df([listing_row("sale-001", snapshot_at=T2, raw_hash="h2")]),
        complete_batch("B4"),
    )
    assert len(result.events) == 1
    assert result.events.iloc[0]["event_type"] == "relisted"
    assert result.state.loc[0, "active"]
    assert result.state.loc[0, "consecutive_absences"] == 0


def test_relisted_resets_absence_count():
    state = with_state(
        [listing_row("sale-001", snapshot_at=T0, raw_hash="h1")],
        active=True, consecutive_absences=1, last_seen_batch_id="B2",
    )
    result = detect_listing_events(
        state,
        as_df([listing_row("sale-001", snapshot_at=T2, raw_hash="h1")]),
        complete_batch("B3"),
    )
    assert result.events.iloc[0]["event_type"] == "relisted"
    assert result.state.loc[0, "consecutive_absences"] == 0


# ------------------------------------------------------------------
# Price events
# ------------------------------------------------------------------


def test_price_increase(sale_state):
    result = detect_listing_events(
        sale_state,
        as_df([listing_row("sale-001", asking_price=12_000_000, snapshot_at=T2, raw_hash="h2")]),
        complete_batch("B1"),
    )
    assert len(result.events) == 1
    ev = result.events.iloc[0]
    assert ev["event_type"] == "price_increase"
    data = ev["event_data"]
    assert "12_000_000" in data or "12000000" in data
    assert "10_000_000" in data or "10000000" in data


def test_price_decrease(sale_state):
    result = detect_listing_events(
        sale_state,
        as_df([listing_row("sale-001", asking_price=8_000_000, snapshot_at=T2, raw_hash="h2")]),
        complete_batch("B1"),
    )
    assert len(result.events) == 1
    assert result.events.iloc[0]["event_type"] == "price_decrease"


def test_no_price_event_when_unchanged(sale_state):
    result = detect_listing_events(
        sale_state,
        as_df([listing_row("sale-001", asking_price=10_000_000, snapshot_at=T2, raw_hash="h1")]),
        complete_batch("B1"),
    )
    assert result.events.empty


def test_price_event_data_contains_change_fields(sale_state):
    result = detect_listing_events(
        sale_state,
        as_df([listing_row("sale-001", asking_price=12_000_000, snapshot_at=T2, raw_hash="h2")]),
        complete_batch("B1"),
    )
    import json
    data = json.loads(result.events.iloc[0]["event_data"])
    assert data["previous_price"] == 10_000_000
    assert data["new_price"] == 12_000_000
    assert data["absolute_change"] == 2_000_000
    assert data["percentage_change"] == 20.0


def test_price_event_requires_positive_old_price():
    state = with_state(
        [listing_row("sale-001", asking_price=None, snapshot_at=T0, raw_hash="h1")],
    )
    result = detect_listing_events(
        state,
        as_df([listing_row("sale-001", asking_price=12_000_000, snapshot_at=T2, raw_hash="h2")]),
        complete_batch("B1"),
    )
    assert result.events.empty


def test_price_event_requires_positive_new_price(sale_state):
    result = detect_listing_events(
        sale_state,
        as_df([listing_row("sale-001", asking_price=None, snapshot_at=T2, raw_hash="h2")]),
        complete_batch("B1"),
    )
    assert result.events.empty


def test_rental_price_increase(rental_state):
    result = detect_listing_events(
        rental_state,
        as_df([listing_row(
            "rent-001", "rental", asking_price=None, monthly_rent=18_000,
            snapshot_at=T2, raw_hash="h3",
        )]),
        complete_batch("B1", listing_type="rental"),
    )
    assert len(result.events) == 1
    assert result.events.iloc[0]["event_type"] == "price_increase"


# ------------------------------------------------------------------
# Idempotency
# ------------------------------------------------------------------


def test_same_batch_idempotent():
    rows = as_df([listing_row("sale-001", snapshot_at=T1, raw_hash="h1")])
    batch = complete_batch("B1")
    r1 = detect_listing_events(pd.DataFrame(), rows, batch)
    r2 = detect_listing_events(pd.DataFrame(), rows, batch)
    assert r1.events.iloc[0]["event_key"] == r2.events.iloc[0]["event_key"]


def test_event_key_deterministic():
    k1 = event_key("B1", "591|sale|sale-001", "listed")
    k2 = event_key("B1", "591|sale|sale-001", "listed")
    assert k1 == k2
    assert len(k1) == 64


def test_event_key_differs_for_different_types():
    listed = event_key("B1", "591|sale|sale-001", "listed")
    delisted = event_key("B1", "591|sale|sale-001", "delisted")
    assert listed != delisted


# ------------------------------------------------------------------
# Cross-type isolation
# ------------------------------------------------------------------


def test_cross_type_isolation():
    state = with_state([
        listing_row("sale-001", "sale", snapshot_at=T0, raw_hash="h1"),
        listing_row("rent-001", "rental", asking_price=None,
                      monthly_rent=15_000, snapshot_at=T0, raw_hash="h2"),
    ])
    # Only sale batch -- rental listing is absent
    result = detect_listing_events(
        state,
        as_df([listing_row("sale-001", "sale", snapshot_at=T2, raw_hash="h1")]),
        complete_batch("B1"),
    )
    assert result.events.empty
    # Rental should not be affected (its listing_type differs)
    rental_row = result.state[result.state["listing_type"] == "rental"]
    assert rental_row.iloc[0]["consecutive_absences"] == 0


# ------------------------------------------------------------------
# Full lifecycle
# ------------------------------------------------------------------


def test_listed_delisted_relisted_price_change():
    state = pd.DataFrame()

    # B1: listed
    r1 = detect_listing_events(
        state,
        as_df([listing_row("sale-001", asking_price=10_000_000, snapshot_at=T1, raw_hash="h1")]),
        complete_batch("B1"),
    )
    assert r1.events.iloc[0]["event_type"] == "listed"

    # B2: price increase
    r2 = detect_listing_events(
        r1.state,
        as_df([listing_row("sale-001", asking_price=12_000_000, snapshot_at=T2, raw_hash="h2")]),
        complete_batch("B2"),
    )
    assert r2.events.iloc[0]["event_type"] == "price_increase"

    # B3: absent (1st absence)
    r3 = detect_listing_events(
        r2.state, empty_rows(), complete_batch("B3"),
    )
    assert r3.events.empty

    # B4: absent (2nd absence → delisted)
    r4 = detect_listing_events(
        r3.state, empty_rows(), complete_batch("B4"),
    )
    assert r4.events.iloc[0]["event_type"] == "delisted"

    # B5: relisted
    r5 = detect_listing_events(
        r4.state,
        as_df([listing_row("sale-001", asking_price=11_000_000, snapshot_at=T3, raw_hash="h3")]),
        complete_batch("B5"),
    )
    assert r5.events.iloc[0]["event_type"] == "relisted"


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


def test_empty_previous_with_empty_current():
    result = detect_listing_events(
        pd.DataFrame(), empty_rows(), complete_batch("B1"),
    )
    assert result.events.empty
    assert result.state.empty


def test_empty_previous_is_same_as_empty_dataframe():
    r1 = detect_listing_events(
        pd.DataFrame(), empty_rows(), complete_batch("B1"),
    )
    r2 = detect_listing_events(
        pd.DataFrame(columns=["listing_key"]), empty_rows(), complete_batch("B1"),
    )
    assert r1.events.empty
    assert r2.events.empty


def test_multiple_listings_independent_absences():
    state = with_state([
        listing_row("sale-001", snapshot_at=T0, raw_hash="h1"),
        listing_row("sale-002", snapshot_at=T0, raw_hash="h2"),
    ])
    result = detect_listing_events(
        state,
        as_df([listing_row("sale-001", snapshot_at=T2, raw_hash="h1")]),
        complete_batch("B1"),
    )
    # sale-002 should have 1 absence
    sale002 = result.state[result.state["source_listing_id"] == "sale-002"]
    assert sale002.iloc[0]["consecutive_absences"] == 1
    sale001 = result.state[result.state["source_listing_id"] == "sale-001"]
    assert sale001.iloc[0]["consecutive_absences"] == 0


def test_zero_price_does_not_trigger_price_event():
    state = with_state([
        listing_row("sale-001", asking_price=0, snapshot_at=T0, raw_hash="h1"),
    ])
    result = detect_listing_events(
        state,
        as_df([listing_row("sale-001", asking_price=10_000_000, snapshot_at=T2, raw_hash="h2")]),
        complete_batch("B1"),
    )
    assert result.events.empty
