from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyRegressor

from qingpu_insight.listing_normalization import NormalizedListing
from qingpu_insight.listing_valuation import compare_listing_to_model
from qingpu_insight.valuation import ModelUnavailableError, ValuationBundle


def _listing(**overrides) -> NormalizedListing:
    defaults = {
        "source": "591",
        "source_listing_id": "L001",
        "listing_type": "sale",
        "snapshot_at": datetime(2026, 7, 1, tzinfo=UTC),
        "source_url": "https://sale.591.com.tw/L001",
        "title": "Test Listing",
        "asking_price_twd": 15_000_000,
        "monthly_rent_twd": None,
        "building_area_ping": 30.0,
        "building_type": "住宅大樓",
        "bedrooms": 3,
        "living_rooms": 2,
        "bathrooms": 2,
        "building_age_years": 6.0,
        "floor": 12,
        "total_floors": 15,
        "parking_type": None,
        "latitude": 25.0,
        "longitude": 121.2,
        "raw_hash": "abc",
    }
    defaults.update(overrides)
    return NormalizedListing(**defaults)


class SpyRegistry:
    def __init__(self):
        self.calls = []

    def get(self, transaction_type: str) -> ValuationBundle:
        self.calls.append(transaction_type)
        raise ModelUnavailableError(f"{transaction_type} model artifact not found")


class MapRegistry:
    def __init__(self, bundles: dict[str, ValuationBundle]):
        self._bundles = bundles

    def get(self, transaction_type: str) -> ValuationBundle:
        if transaction_type not in self._bundles:
            raise ModelUnavailableError(
                f"{transaction_type} model artifact not found"
            )
        return self._bundles[transaction_type]


@pytest.fixture
def market() -> pd.DataFrame:
    np.random.seed(42)
    n = 50
    dates = pd.date_range("2024-01-01", "2026-06-01", periods=n)
    return pd.DataFrame(
        {
            "record_id": [f"M{i}" for i in range(n)],
            "transaction_type": ["resale"] * n,
            "transaction_date": dates,
            "station_code": np.random.choice(["A17", "A18", "A19"], n),
            "building_type": np.random.choice(["住宅大樓", "華廈"], n),
            "building_area_ping": np.random.uniform(15, 80, n),
            "unit_price_per_ping_twd": np.random.uniform(200_000, 800_000, n).astype(int),
            "total_price_twd": np.random.uniform(5_000_000, 40_000_000, n).astype(int),
            "station_distance_m": np.random.uniform(100, 1500, n),
            "bedrooms": np.random.randint(1, 5, n),
            "living_rooms": np.random.randint(1, 3, n),
            "bathrooms": np.random.randint(1, 3, n),
            "floor_ratio": np.random.uniform(0.1, 0.9, n),
            "longitude": np.random.uniform(121.20, 121.25, n),
            "latitude": np.random.uniform(25.00, 25.05, n),
            "building_age_years": np.where(
                np.random.random(n) > 0.2,
                np.random.uniform(0, 30, n),
                np.nan,
            ),
        }
    )


@pytest.fixture
def bundle() -> ValuationBundle:
    dummy = DummyRegressor(strategy="constant", constant=500_000)
    dummy.fit(np.zeros((5, 5)), np.ones(5))
    return ValuationBundle(
        transaction_type="resale",
        model_name="ridge",
        model_version="resale-2026-06-01-a1b2c3d4",
        pipeline=dummy,
        interval_abs_residual_twd_per_ping=50_000,
        feature_ranges={
            "building_area_ping": (20, 80),
            "station_distance_m": (100, 1500),
            "bedrooms": (1, 5),
            "living_rooms": (1, 4),
            "bathrooms": (1, 4),
            "building_age_years": (0, 30),
            "floor": (1, 20),
            "total_floors": (5, 25),
            "parking_area_ping": (0, 20),
        },
        feature_hard_ranges={
            "building_area_ping": (15, 90),
            "station_distance_m": (50, 1800),
            "bedrooms": (1, 5),
            "living_rooms": (1, 4),
            "bathrooms": (1, 4),
            "building_age_years": (0, 40),
            "floor": (1, 22),
            "total_floors": (3, 28),
            "parking_area_ping": (0, 25),
        },
        feature_medians={
            "building_area_ping": 35.0,
            "station_distance_m": 600.0,
            "bedrooms": 3.0,
            "living_rooms": 2.0,
            "bathrooms": 2.0,
            "building_age_years": 10.0,
            "floor": 8.0,
            "total_floors": 15.0,
            "parking_area_ping": 8.0,
        },
        global_importance=[],
        reference_rows=pd.DataFrame(),
        data_min_date="2024-01-01",
        data_max_date="2026-06-01",
        metrics={},
    )


@pytest.fixture
def registry(bundle):
    return MapRegistry({"resale": bundle})


# ── Step 1: Rental never calls model ──────────────────────────────

def test_rental_never_calls_model():
    listing = _listing(listing_type="rental", asking_price_twd=None, monthly_rent_twd=25000)
    spy = SpyRegistry()

    result = compare_listing_to_model(listing, spy, pd.DataFrame())

    assert result["valuation_eligible"] is False
    assert result["reason"] == "rental_not_supported"
    assert spy.calls == []


# ── Step 2: Missing fields → reason, no imputation ─────────────────

def test_incomplete_sale_returns_reason_instead_of_imputing():
    listing = _listing(floor=None, total_floors=None)

    result = compare_listing_to_model(
        listing,
        MapRegistry({}),
        pd.DataFrame(),
        station_code="A17",
        station_distance_m=500.0,
        location_eligible=True,
    )

    assert result == {"valuation_eligible": False, "reason": "missing:floor,total_floors"}


# ── Sale → resale mapping ─────────────────────────────────────────

def test_sale_maps_to_resale(registry, market, bundle):
    listing = _listing(listing_type="sale", asking_price_twd=15_000_000)

    result = compare_listing_to_model(
        listing,
        registry,
        market,
        station_code="A17",
        station_distance_m=500.0,
        location_eligible=True,
    )

    assert result["valuation_eligible"] is True
    assert result["model_version"] == bundle.model_version
    assert result["model_name"] == bundle.model_name


# ── Newhouse → presale mapping ────────────────────────────────────

def test_newhouse_maps_to_presale(registry, market, bundle):
    presale_bundle = replace(bundle, transaction_type="presale")
    pre_registry = MapRegistry({"presale": presale_bundle})
    listing = _listing(
        listing_type="newhouse",
        building_age_years=None,
        asking_price_twd=18_000_000,
    )

    result = compare_listing_to_model(
        listing,
        pre_registry,
        market,
        station_code="A18",
        station_distance_m=700.0,
        location_eligible=True,
    )

    assert result["valuation_eligible"] is True


def test_newhouse_without_total_price_never_calls_model():
    listing = _listing(
        listing_type="newhouse",
        asking_price_twd=None,
        asking_unit_price_low_twd_per_ping=500_000,
        asking_unit_price_high_twd_per_ping=560_000,
    )
    spy = SpyRegistry()

    result = compare_listing_to_model(
        listing,
        spy,
        pd.DataFrame(),
        station_code="A18",
        station_distance_m=500,
        location_eligible=True,
    )

    assert result == {
        "valuation_eligible": False,
        "reason": "no_total_asking_price",
        "advertised_unit_price_range_twd_per_ping": (500_000, 560_000),
    }
    assert spy.calls == []


# ── Location ineligibility ────────────────────────────────────────

def test_outside_area_rejected():
    listing = _listing()

    result = compare_listing_to_model(
        listing,
        MapRegistry({}),
        pd.DataFrame(),
        station_code=None,
        station_distance_m=None,
        location_eligible=False,
    )

    assert result["valuation_eligible"] is False
    assert result["reason"] == "location_ineligible"


# ── Asking gap and range status ───────────────────────────────────

def test_below_range_status(registry, market):
    listing = _listing(listing_type="sale", asking_price_twd=12_000_000)

    result = compare_listing_to_model(
        listing,
        registry,
        market,
        station_code="A17",
        station_distance_m=500.0,
        location_eligible=True,
    )

    assert result["status"] == "below_range"
    assert result["asking_gap_pct"] == pytest.approx(-20.0, abs=0.01)


def test_within_range_status(registry, market):
    listing = _listing(listing_type="sale", asking_price_twd=15_000_000)

    result = compare_listing_to_model(
        listing,
        registry,
        market,
        station_code="A17",
        station_distance_m=500.0,
        location_eligible=True,
    )

    assert result["status"] == "within_range"
    assert result["asking_gap_pct"] == pytest.approx(0.0, abs=0.01)


def test_above_range_status(registry, market):
    listing = _listing(listing_type="sale", asking_price_twd=18_000_000)

    result = compare_listing_to_model(
        listing,
        registry,
        market,
        station_code="A17",
        station_distance_m=500.0,
        location_eligible=True,
    )

    assert result["status"] == "above_range"
    assert result["asking_gap_pct"] == pytest.approx(20.0, abs=0.01)


# ── Result metadata ───────────────────────────────────────────────

def test_result_contains_model_version_and_valued_at(registry, market, bundle):
    listing = _listing(listing_type="sale", asking_price_twd=15_000_000)

    result = compare_listing_to_model(
        listing,
        registry,
        market,
        station_code="A17",
        station_distance_m=500.0,
        location_eligible=True,
    )

    assert result["model_version"] == bundle.model_version
    assert "valued_at" in result
    assert result["valued_at"] == bundle.data_max_date
