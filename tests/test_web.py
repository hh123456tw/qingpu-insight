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
        self, client: FlaskClient, failing_source: FlaskClient
    ) -> None:
        response = failing_source.get("/api/market/summary?transaction_type=resale")
        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "market_data_unavailable"
        assert "Traceback" not in response.get_data(as_text=True)
