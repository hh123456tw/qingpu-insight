from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from flask.testing import FlaskClient
from sklearn.dummy import DummyRegressor

from qingpu_insight.market_metrics import MarketFilters
from qingpu_insight.valuation import ModelRegistry, ValuationBundle


class InMemoryMarketDataSource:
    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def load(self, filters: MarketFilters) -> pd.DataFrame:
        return self._frame


class FailingMarketDataSource:
    def load(self, filters: MarketFilters) -> pd.DataFrame:
        raise RuntimeError("database connection failed")


@pytest.fixture
def client(market_frame: pd.DataFrame) -> FlaskClient:
    from qingpu_insight.web import create_app

    ds = InMemoryMarketDataSource(market_frame)
    app = create_app(data_source=ds)
    with app.test_client() as client:
        yield client


@pytest.fixture
def failing_source() -> FlaskClient:
    from qingpu_insight.web import create_app

    ds = FailingMarketDataSource()
    app = create_app(data_source=ds)
    with app.test_client() as client:
        yield client


def test_homepage_contains_market_dashboard_contract(client) -> None:
    response = client.get("/")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'id="transaction-type"' in html
    assert 'id="station-filter"' in html
    assert 'id="date-from"' in html
    assert 'id="date-to"' in html
    assert 'id="area-ping-min"' in html
    assert 'id="area-ping-max"' in html
    assert 'id="building-type"' in html
    assert 'id="bedrooms"' in html
    assert 'value="住宅大樓(11層含以上有電梯)"' in html
    assert 'value="華廈(10層含以下有電梯)"' in html
    assert 'value="公寓(5樓含以下無電梯)"' in html
    assert 'id="market-map"' in html
    assert 'id="price-trend"' in html
    assert 'id="recent-transactions"' in html
    assert "資料更新至" in html
    assert "僅供市場研究" in html


def test_frontend_assets_keep_units_and_map_size_consistent(client) -> None:
    script = client.get("/static/app.js").get_data(as_text=True)
    styles = client.get("/static/app.css").get_data(as_text=True)
    assert "t.median_unit_price_per_ping_twd / 10000" in script
    assert "height: 440px" in styles
    assert "min-width: 0" in styles


class TestMarketApi:
    def test_summary_requires_transaction_type(self, client: FlaskClient) -> None:
        response = client.get("/api/market/summary")
        assert response.status_code == 400
        assert response.get_json() == {
            "error": {
                "code": "invalid_request",
                "message": "請選擇中古屋或預售屋。",
                "fields": {"transaction_type": "required"},
            }
        }

    def test_summary_keeps_transaction_type_isolated(self, client: FlaskClient) -> None:
        response = client.get("/api/market/summary?transaction_type=resale&station=A18")
        assert response.status_code == 200
        assert response.get_json()["transaction_type"] == "resale"

    def test_trends_and_transactions_share_filters(self, client: FlaskClient) -> None:
        query = "transaction_type=presale&station=A17&date_from=2026-01-01"
        assert client.get(f"/api/market/trends?{query}").status_code == 200
        payload = client.get(f"/api/transactions?{query}&limit=10").get_json()
        assert all(row["station_code"] == "A17" for row in payload["items"])
        assert all(row["transaction_type"] == "presale" for row in payload["items"])

    def test_unhandled_exception_uses_safe_error_shape(self, failing_source: FlaskClient) -> None:
        response = failing_source.get("/api/market/summary?transaction_type=resale")
        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "market_data_unavailable"
        assert "Traceback" not in response.get_data(as_text=True)

    def test_transactions_handles_nullable_numeric_fields(self, client: FlaskClient) -> None:
        response = client.get("/api/transactions?transaction_type=presale&limit=1")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload is not None
        assert payload["limit"] == 1
        assert isinstance(payload["items"], list)
        assert "NaN" not in response.get_data(as_text=True)

    def test_empty_summary_serializes_missing_medians_as_null(self, client: FlaskClient) -> None:
        response = client.get("/api/market/summary?transaction_type=resale&date_from=2099-01-01")
        assert response.status_code == 200
        assert response.get_json()["median_unit_price_per_ping_twd"] is None
        assert "NaN" not in response.get_data(as_text=True)

    def test_transactions_do_not_expose_internal_location_fields(self, client: FlaskClient) -> None:
        response = client.get("/api/transactions?transaction_type=resale&limit=1")
        raw = response.get_data(as_text=True)
        assert response.status_code == 200
        private_fields = (
            "normalized_address",
            "road_key",
            "house_number",
            "twd97_x",
            "twd97_y",
            "remarks",
        )
        for field in private_fields:
            assert field not in raw


# --- Listing API tests (M3) ---


class InMemoryListingRepo:
    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def load_current(self, listing_type: str | None = None) -> pd.DataFrame:
        df = self._df
        if listing_type is not None:
            df = df[df["listing_type"] == listing_type]
        return df

    def load_snapshots(self, batch_id: str | None = None) -> pd.DataFrame:
        return pd.DataFrame()

    def save_batch(self, batch, rows) -> None:
        pass

    def append_events(self, events) -> None:
        pass


@pytest.fixture
def listing_client(market_frame: pd.DataFrame) -> FlaskClient:
    from qingpu_insight.web import create_app

    listing_df = pd.DataFrame([
        {
            "source": "591", "source_listing_id": "L001", "listing_type": "sale",
            "snapshot_at": pd.Timestamp("2026-07-20 10:00", tz="UTC"),
            "source_url": "https://sale.591.com.tw/L001",
            "title": "青埔三房", "asking_price_twd": 18_000_000,
            "building_area_ping": 35.5, "station_code": "A18",
            "latitude": 25.0123, "longitude": 121.2018,
        },
        {
            "source": "591", "source_listing_id": "L002", "listing_type": "sale",
            "snapshot_at": pd.Timestamp("2026-07-20 10:00", tz="UTC"),
            "source_url": "https://sale.591.com.tw/L002",
            "title": "A17大樓", "asking_price_twd": 22_000_000,
            "building_area_ping": 48.0, "station_code": "A17",
            "latitude": 25.0156, "longitude": 121.2078,
        },
    ])
    ds = InMemoryMarketDataSource(market_frame)
    app = create_app(data_source=ds, listing_repo=InMemoryListingRepo(listing_df))
    with app.test_client() as client:
        yield client


class TestListingApi:
    def test_listing_api_never_exposes_private_or_raw_fields(self, listing_client: FlaskClient) -> None:
        raw = listing_client.get("/api/listings?listing_type=sale").get_data(as_text=True)
        for field in ("raw_html", "payload", "phone", "contact_name", "full_address"):
            assert field not in raw

    def test_summary_requires_listing_type(self, listing_client: FlaskClient) -> None:
        response = listing_client.get("/api/listings/summary")
        assert response.status_code == 400

    def test_summary_keeps_type_isolated(self, listing_client: FlaskClient) -> None:
        response = listing_client.get("/api/listings/summary?listing_type=sale&station=A18")
        assert response.status_code == 200
        data = response.get_json()
        assert data["listing_type"] == "sale"
        assert data["active_count"] == 1
        assert data["station_codes"] == ["A18"]

    def test_listings_pagination_default_cap(self, listing_client: FlaskClient) -> None:
        response = listing_client.get("/api/listings?listing_type=sale")
        data = response.get_json()
        assert data["limit"] == 100

    def test_listings_returns_public_columns(self, listing_client: FlaskClient) -> None:
        response = listing_client.get("/api/listings?listing_type=sale&station=A18")
        data = response.get_json()
        assert len(data["items"]) == 1
        row = data["items"][0]
        expected = {"listing_id", "type", "title", "source_url", "station", "area", "price", "event", "status", "latitude", "longitude", "model_evidence", "snapshot_time"}
        assert set(row.keys()) == expected

    def test_listing_events_returns_filtered(self, listing_client: FlaskClient) -> None:
        response = listing_client.get("/api/listing-events?listing_type=sale")
        assert response.status_code == 200
        data = response.get_json()
        assert "items" in data

    def test_no_listing_repo_returns_503(self, client: FlaskClient) -> None:
        response = client.get("/api/listings?listing_type=sale")
        assert response.status_code == 503

    def test_listings_with_missing_type_returns_400(self, listing_client: FlaskClient) -> None:
        response = listing_client.get("/api/listings")
        assert response.status_code == 400


def test_unknown_route_preserves_http_404(client: FlaskClient) -> None:
    assert client.get("/does-not-exist").status_code == 404


# --- Valuation API tests ---


@pytest.fixture
def trained_registry(tmp_path) -> ModelRegistry:
    import joblib

    dummy = DummyRegressor(strategy="constant", constant=500_000)
    dummy.fit(np.zeros((5, 5)), np.ones(5))
    bundle = ValuationBundle(
        transaction_type="resale",
        model_name="ridge",
        model_version="v1",
        pipeline=dummy,
        interval_abs_residual_twd_per_ping=50000,
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
            "station_distance_m": 500.0,
            "bedrooms": 3.0,
            "living_rooms": 2.0,
            "bathrooms": 2.0,
            "building_age_years": 8.0,
            "floor": 8.0,
            "total_floors": 15.0,
            "parking_area_ping": 5.0,
        },
        global_importance=[],
        reference_rows=pd.DataFrame({"dummy": [1]}),
        data_min_date="2024-01-01",
        data_max_date="2026-06-01",
        metrics={},
    )
    joblib.dump(bundle, tmp_path / "resale.joblib")
    return ModelRegistry(tmp_path)


@pytest.fixture
def valuation_client(market_frame: pd.DataFrame, trained_registry, tmp_path) -> FlaskClient:
    from qingpu_insight.valuation_store import FileValuationStore
    from qingpu_insight.web import create_app

    mf = market_frame.copy()
    mf["floor"] = "五層"
    mf["total_floors"] = 15
    mf["parking_type"] = "坡道平面"
    mf["parking_area_sqm"] = 0
    ds = InMemoryMarketDataSource(mf)
    store = FileValuationStore(tmp_path / "vals")
    app = create_app(data_source=ds, valuation_store=store, model_registry=trained_registry)
    with app.test_client() as client:
        yield client


VALID_RESALE_PAYLOAD = {
    "transaction_type": "resale",
    "station_code": "A17",
    "building_area_ping": 30,
    "station_distance_m": 500,
    "building_type": "住宅大樓(11層含以上有電梯)",
    "bedrooms": 3,
    "living_rooms": 2,
    "bathrooms": 2,
    "building_age_years": 6.0,
    "floor": 12,
    "total_floors": 15,
    "parking_type": "坡道平面",
    "parking_area_ping": 10,
    "asking_total_price_twd": 18000000,
}


@pytest.fixture
def client_without_models(market_frame: pd.DataFrame, tmp_path) -> FlaskClient:
    from qingpu_insight.valuation import ModelRegistry
    from qingpu_insight.valuation_store import FileValuationStore
    from qingpu_insight.web import create_app

    mf = market_frame.copy()
    mf["floor"] = "五層"
    mf["total_floors"] = 15
    mf["parking_type"] = "坡道平面"
    mf["parking_area_sqm"] = 0
    ds = InMemoryMarketDataSource(mf)
    store = FileValuationStore(tmp_path / "vals")
    registry = ModelRegistry(tmp_path / "empty")
    app = create_app(data_source=ds, valuation_store=store, model_registry=registry)
    with app.test_client() as client:
        yield client


def test_post_valuation_returns_evidence(valuation_client):
    response = valuation_client.post("/api/valuations", json=VALID_RESALE_PAYLOAD)
    assert response.status_code == 201
    body = response.get_json()
    assert body["transaction_type"] == "resale"
    assert body["interval_total_price_twd"][0] <= body["estimated_total_price_twd"]
    assert {"confidence", "factors", "comparables", "model", "data_date"} <= body.keys()


def test_post_valuation_reports_field_errors(valuation_client):
    response = valuation_client.post("/api/valuations", json={"transaction_type": "resale"})
    assert response.status_code == 400
    assert "building_area_ping" in response.get_json()["error"]["fields"]


def test_missing_artifact_uses_explicit_baseline(client_without_models):
    response = client_without_models.post("/api/valuations", json=VALID_RESALE_PAYLOAD)
    assert response.status_code == 201
    body = response.get_json()
    assert body["degraded"] is True
    assert body["model"]["name"] == "recent_median_baseline"


def test_get_valuation_returns_saved_record(valuation_client):
    post = valuation_client.post("/api/valuations", json=VALID_RESALE_PAYLOAD)
    assert post.status_code == 201
    vid = post.get_json()["valuation_id"]
    response = valuation_client.get(f"/api/valuations/{vid}")
    assert response.status_code == 200
    assert response.get_json()["valuation_id"] == vid


def test_get_nonexistent_valuation_returns_404(valuation_client):
    response = valuation_client.get("/api/valuations/nonexistent")
    assert response.status_code == 404


def test_homepage_contains_complete_valuation_contract(client):
    html = client.get("/").get_data(as_text=True)
    for element_id in (
        "valuation-form",
        "valuation-type",
        "valuation-station",
        "valuation-area",
        "valuation-distance",
        "valuation-age",
        "valuation-floor",
        "valuation-total-floors",
        "valuation-bedrooms",
        "valuation-parking-area",
        "asking-price",
        "valuation-result",
    ):
        assert f'id="{element_id}"' in html


def test_frontend_renders_evidence_before_summary(client):
    script = client.get("/static/app.js").get_data(as_text=True)
    assert "interval_total_price_twd" in script
    assert "confidence_reasons" in script
    assert "comparables" in script
    assert "innerHTML =" not in script
