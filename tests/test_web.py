from __future__ import annotations

import pandas as pd
import pytest
from flask.testing import FlaskClient

from qingpu_insight.market_metrics import MarketFilters


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

    def test_summary_keeps_transaction_type_isolated(
        self, client: FlaskClient
    ) -> None:
        response = client.get(
            "/api/market/summary?transaction_type=resale&station=A18"
        )
        assert response.status_code == 200
        assert response.get_json()["transaction_type"] == "resale"

    def test_trends_and_transactions_share_filters(
        self, client: FlaskClient
    ) -> None:
        query = "transaction_type=presale&station=A17&date_from=2026-01-01"
        assert (
            client.get(f"/api/market/trends?{query}").status_code == 200
        )
        payload = client.get(f"/api/transactions?{query}&limit=10").get_json()
        assert all(row["station_code"] == "A17" for row in payload["items"])
        assert all(row["transaction_type"] == "presale" for row in payload["items"])

    def test_unhandled_exception_uses_safe_error_shape(
        self, failing_source: FlaskClient
    ) -> None:
        response = failing_source.get("/api/market/summary?transaction_type=resale")
        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "market_data_unavailable"
        assert "Traceback" not in response.get_data(as_text=True)

    def test_transactions_handles_nullable_numeric_fields(
        self, client: FlaskClient
    ) -> None:
        response = client.get("/api/transactions?transaction_type=presale&limit=1")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload is not None
        assert payload["limit"] == 1
        assert isinstance(payload["items"], list)
        assert "NaN" not in response.get_data(as_text=True)

    def test_empty_summary_serializes_missing_medians_as_null(
        self, client: FlaskClient
    ) -> None:
        response = client.get(
            "/api/market/summary?transaction_type=resale&date_from=2099-01-01"
        )
        assert response.status_code == 200
        assert response.get_json()["median_unit_price_per_ping_twd"] is None
        assert "NaN" not in response.get_data(as_text=True)

    def test_transactions_do_not_expose_internal_location_fields(
        self, client: FlaskClient
    ) -> None:
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


def test_unknown_route_preserves_http_404(client: FlaskClient) -> None:
    assert client.get("/does-not-exist").status_code == 404
