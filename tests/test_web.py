from __future__ import annotations

import subprocess
from concurrent.futures import Future
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import numpy as np
import pandas as pd
import pytest
from flask.testing import FlaskClient
from sklearn.dummy import DummyRegressor

from qingpu_insight.jobs import JobRun
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
    def __init__(self, df: pd.DataFrame, events_df: pd.DataFrame | None = None) -> None:
        self._df = df
        self._events_df = events_df if events_df is not None else pd.DataFrame()

    def load_current(self, listing_type: str | None = None) -> pd.DataFrame:
        df = self._df
        if listing_type is not None:
            df = df[df["listing_type"] == listing_type]
        return df

    def load_events(self, listing_type: str | None = None) -> pd.DataFrame:
        df = self._events_df
        if listing_type is not None:
            df = df[df["listing_type"] == listing_type]
        return df

    def load_snapshots(self, batch_id: str | None = None) -> pd.DataFrame:
        return pd.DataFrame()

    def save_batch(self, batch, rows) -> None:
        pass

    def append_events(self, events) -> None:
        pass

    def merge_state(self, state) -> None:
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
            "location_eligible": True, "active": True,
        },
        {
            "source": "591", "source_listing_id": "L002", "listing_type": "sale",
            "snapshot_at": pd.Timestamp("2026-07-20 10:00", tz="UTC"),
            "source_url": "https://sale.591.com.tw/L002",
            "title": "A17大樓", "asking_price_twd": 22_000_000,
            "building_area_ping": 48.0, "station_code": "A17",
            "latitude": 25.0156, "longitude": 121.2078,
            "location_eligible": True, "active": True,
        },
        {
            "source": "591", "source_listing_id": "N001", "listing_type": "newhouse",
            "snapshot_at": pd.Timestamp("2026-07-20 10:00", tz="UTC"),
            "source_url": "https://newhouse.591.com.tw/N001",
            "title": "青埔預售案", "asking_price_twd": None,
            "asking_unit_price_low_twd_per_ping": 500_000,
            "asking_unit_price_high_twd_per_ping": 560_000,
            "building_area_min_ping": 19.0, "building_area_max_ping": 30.0,
            "station_code": "A18", "latitude": 25.0123, "longitude": 121.2018,
            "location_eligible": True, "active": True,
        },
        {
            "source": "591", "source_listing_id": "OUT001", "listing_type": "newhouse",
            "snapshot_at": pd.Timestamp("2026-07-20 10:00", tz="UTC"),
            "source_url": "https://newhouse.591.com.tw/OUT001",
            "title": "圈外預售案", "asking_price_twd": 20_000_000,
            "station_code": "A18", "latitude": 25.0123, "longitude": 121.2018,
            "location_eligible": False, "active": True,
        },
    ])
    events_df = pd.DataFrame([
        {
            "event_key": "a" * 64, "source": "591",
            "listing_type": "sale", "source_listing_id": "L001",
            "event_type": "price_decrease",
            "event_data": (
                '{"previous_price":20000000,"new_price":18000000,'
                '"absolute_change":-2000000,"percentage_change":-10.0}'
            ),
            "occurred_at": pd.Timestamp("2026-07-19 10:00", tz="UTC"),
        },
    ])
    ds = InMemoryMarketDataSource(market_frame)
    app = create_app(data_source=ds, listing_repo=InMemoryListingRepo(listing_df, events_df))
    with app.test_client() as client:
        yield client


class TestListingApi:
    def test_listing_api_never_exposes_private_or_raw_fields(  # noqa: E501
        self, listing_client: FlaskClient
    ) -> None:
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
        expected = {
            "listing_id", "type", "title", "source_url", "station",
            "area", "price", "event", "status", "latitude", "longitude",
            "model_evidence", "snapshot_time", "unit_price_range_twd_per_ping",
            "area_range_ping",
        }
        assert set(row.keys()) == expected

    def test_listings_omits_location_ineligible_and_preserves_missing_total_price(
        self, listing_client: FlaskClient
    ) -> None:
        response = listing_client.get("/api/listings?listing_type=newhouse&station=A18")

        assert response.status_code == 200
        items = response.get_json()["items"]
        assert [item["listing_id"] for item in items] == ["N001"]
        assert items[0]["price"] is None
        assert items[0]["unit_price_range_twd_per_ping"] == {
            "low": 500_000,
            "high": 560_000,
        }
        assert items[0]["area_range_ping"] == {"low": 19.0, "high": 30.0}
        assert "NaN" not in response.get_data(as_text=True)

    def test_listing_events_returns_filtered(self, listing_client: FlaskClient) -> None:
        response = listing_client.get("/api/listing-events?listing_type=sale")
        assert response.status_code == 200
        data = response.get_json()
        assert "items" in data
        assert len(data["items"]) == 1
        ev = data["items"][0]
        assert ev["event_type"] == "price_decrease"
        assert ev["type"] == "sale"
        assert ev["source_listing_id"] == "L001"
        assert ev["event_data"]["previous_price"] == 20_000_000
        assert ev["event_data"]["new_price"] == 18_000_000

    def test_no_listing_repo_returns_503(self, client: FlaskClient) -> None:
        response = client.get("/api/listings?listing_type=sale")
        assert response.status_code == 503

    def test_listings_with_missing_type_returns_400(self, listing_client: FlaskClient) -> None:
        response = listing_client.get("/api/listings")
        assert response.status_code == 400

    @pytest.mark.parametrize("missing_column", ["location_eligible", "active"])
    def test_summary_and_listings_fail_closed_without_visibility_contract(
        self, market_frame: pd.DataFrame, missing_column: str
    ) -> None:
        from qingpu_insight.web import create_app

        frame = pd.DataFrame(
            [
                {
                    "source": "591",
                    "source_listing_id": "L001",
                    "listing_type": "sale",
                    "snapshot_at": pd.Timestamp("2026-07-20 10:00", tz="UTC"),
                    "source_url": "https://sale.591.com.tw/L001",
                    "title": "青埔三房",
                    "asking_price_twd": 18_000_000,
                    "building_area_ping": 35.5,
                    "station_code": "A18",
                    "latitude": 25.0123,
                    "longitude": 121.2018,
                    "location_eligible": True,
                    "active": True,
                }
            ]
        ).drop(columns=[missing_column])
        app = create_app(
            data_source=InMemoryMarketDataSource(market_frame),
            listing_repo=InMemoryListingRepo(frame),
        )

        with app.test_client() as api:
            summary = api.get("/api/listings/summary?listing_type=sale").get_json()
            listings = api.get("/api/listings?listing_type=sale").get_json()

        assert summary["active_count"] == 0
        assert listings["items"] == []

    def test_lifecycle_visibility_through_incomplete_and_two_complete_absences(
        self, market_frame: pd.DataFrame
    ) -> None:
        from datetime import UTC, datetime

        from qingpu_insight.listing_events import detect_listing_events
        from qingpu_insight.listing_sources import CaptureBatch
        from qingpu_insight.web import create_app

        initial = pd.DataFrame(
            [
                {
                    "source": "591",
                    "source_listing_id": "L001",
                    "listing_type": "sale",
                    "snapshot_at": pd.Timestamp("2026-07-20 10:00", tz="UTC"),
                    "source_url": "https://sale.591.com.tw/L001",
                    "title": "青埔三房",
                    "asking_price_twd": 18_000_000,
                    "building_area_ping": 35.5,
                    "station_code": "A18",
                    "station_distance_m": 320.0,
                    "latitude": 25.0123,
                    "longitude": 121.2018,
                    "location_eligible": True,
                    "model_evidence": '{"model_version":"resale-v1"}',
                    "active": True,
                    "consecutive_absences": 0,
                    "last_seen_batch_id": "B1",
                }
            ]
        )
        empty = pd.DataFrame(columns=initial.columns)

        def batch(batch_id: str, complete: bool) -> CaptureBatch:
            return CaptureBatch(
                batch_id=batch_id,
                source="591",
                listing_type="sale",
                started_at=datetime(2026, 7, 22, tzinfo=UTC),
                reached_terminal_page=complete,
            )

        incomplete = detect_listing_events(initial, empty, batch("B2", False)).state
        first_absence = detect_listing_events(
            incomplete, empty, batch("B3", True)
        ).state
        second_absence = detect_listing_events(
            first_absence, empty, batch("B4", True)
        ).state

        for state, expected_count in (
            (incomplete, 1),
            (first_absence, 1),
            (second_absence, 0),
        ):
            app = create_app(
                data_source=InMemoryMarketDataSource(market_frame),
                listing_repo=InMemoryListingRepo(state),
            )
            with app.test_client() as api:
                summary = api.get(
                    "/api/listings/summary?listing_type=sale&station=A18"
                ).get_json()
                listings = api.get(
                    "/api/listings?listing_type=sale&station=A18"
                ).get_json()
            assert summary["active_count"] == expected_count
            assert len(listings["items"]) == expected_count


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


# ------------------------------------------------------------------
# Admin API / Job Center (M4.2)
# ------------------------------------------------------------------


class MemoryAdminJobRepository:
    def __init__(self) -> None:
        self._runs: dict[str, JobRun] = {}
        self.terminal = Event()

    def create_or_get(self, run: JobRun) -> tuple[JobRun, bool]:
        active = self.find_active_by_key(run.idempotency_key)
        if active is not None:
            return active, False
        self._runs[run.run_id] = run
        return run, True

    def get(self, run_id: str) -> JobRun | None:
        return self._runs.get(run_id)

    def find_active_by_key(self, idempotency_key: str) -> JobRun | None:
        for run in self._runs.values():
            if run.idempotency_key == idempotency_key and run.status in (
                "pending", "running", "retry_wait",
            ):
                return run
        return None

    def list_recent(self, limit: int = 20) -> list[JobRun]:
        return list(reversed(list(self._runs.values())))[:limit]

    def transition(
        self,
        run_id,
        current_status,
        target_status,
        *,
        output_version=None,
        summary=None,
        error_code=None,
        error_message=None,
    ):
        run = self._runs.get(run_id)
        if run is None or run.status != current_status:
            return False
        started_at = run.started_at
        if target_status == "running" and started_at is None:
            started_at = datetime.now(UTC)
        finished_at = run.finished_at
        if target_status in {"succeeded", "failed", "skipped"}:
            finished_at = datetime.now(UTC)
        self._runs[run_id] = replace(
            run,
            status=target_status,
            started_at=started_at,
            finished_at=finished_at,
            output_version=output_version or run.output_version,
            summary=summary if summary is not None else run.summary,
            error_code=error_code or run.error_code,
            error_message=error_message or run.error_message,
        )
        if target_status in {"succeeded", "failed", "skipped", "needs_attention"}:
            self.terminal.set()
        return True


class FakeAdminExecutor:
    def __init__(self) -> None:
        self.submitted: list[str] = []

    def submit(self, run_id: str, callable) -> Future:
        self.submitted.append(run_id)
        return Future()

    def shutdown(self, wait: bool = True) -> None:
        del wait


class StubListingUpdateService:
    def __init__(self, job_service) -> None:
        self.job_service = job_service
        self.handoffs: list[str] = []
        self.handoff_error: Exception | None = None

    def submit(self, request):
        identity = f"{request.types!r}:{request.max_pages}:{request.trigger}"
        return self.job_service.create("listing_update", identity, request.trigger)

    def handoff(self, submission, request, executor):
        del request
        if self.handoff_error is not None:
            raise self.handoff_error
        self.handoffs.append(submission.run.run_id)
        return executor.submit(submission.run.run_id, lambda: None)


@pytest.fixture
def admin_app(market_frame: pd.DataFrame):
    from qingpu_insight.jobs import JobService
    from qingpu_insight.web import AdminServices, create_app

    repo = MemoryAdminJobRepository()
    job_service = JobService(repo)
    listing_service = StubListingUpdateService(job_service)
    executor = FakeAdminExecutor()
    app = create_app(
        data_source=InMemoryMarketDataSource(market_frame),
        admin_services=AdminServices(job_service, listing_service, executor),
    )
    return app, repo, listing_service, executor


@pytest.fixture
def admin_client(admin_app) -> FlaskClient:
    app, _, _, _ = admin_app
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "test-token"
        yield client


def test_listing_update_returns_202_without_waiting(
    admin_app, admin_client: FlaskClient,
) -> None:
    _, _, service, executor = admin_app
    response = admin_client.post(
        "/api/admin/listing-updates",
        json={"types": ["sale", "newhouse", "rental"], "max_pages": 1},
        headers={"X-Qingpu-CSRF": "test-token"},
    )
    assert response.status_code == 202
    assert response.json["status"] == "pending"
    assert response.json["created"] is True
    assert executor.submitted == [response.json["run_id"]]
    assert service.handoffs == [response.json["run_id"]]


def test_exact_active_duplicate_returns_existing_run_without_second_handoff(
    admin_app, admin_client: FlaskClient,
) -> None:
    _, _, service, executor = admin_app
    request = {
        "json": {"types": ["sale", "newhouse", "rental"], "max_pages": 1},
        "headers": {"X-Qingpu-CSRF": "test-token"},
    }
    first = admin_client.post("/api/admin/listing-updates", **request)
    duplicate = admin_client.post("/api/admin/listing-updates", **request)

    assert duplicate.status_code == 202
    assert duplicate.json["run_id"] == first.json["run_id"]
    assert duplicate.json["created"] is False
    assert service.handoffs == [first.json["run_id"]]
    assert executor.submitted == [first.json["run_id"]]


def test_admin_update_rejects_non_loopback(admin_client: FlaskClient) -> None:
    response = admin_client.post(
        "/api/admin/listing-updates",
        environ_base={"REMOTE_ADDR": "10.0.0.2"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "path",
    ["/api/admin/listing-updates", "/api/jobs", "/api/jobs/123"],
)
def test_admin_and_job_routes_reject_untrusted_host(
    admin_client: FlaskClient, path: str,
) -> None:
    method = admin_client.post if path.startswith("/api/admin") else admin_client.get
    response = method(path, base_url="http://attacker.example")
    assert response.status_code == 403


def test_admin_update_rejects_wrong_csrf(admin_client: FlaskClient) -> None:
    response = admin_client.post(
        "/api/admin/listing-updates",
        json={"types": ["sale"], "max_pages": 1},
        headers={"X-Qingpu-CSRF": "wrong-token"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ([], "body"),
        ({"types": []}, "types"),
        ({"types": "sale"}, "types"),
        ({"types": ["sale", "sale"]}, "types"),
        ({"types": ["other"]}, "types"),
        ({"types": ["sale"], "max_pages": True}, "max_pages"),
        ({"types": ["sale"], "max_pages": 0}, "max_pages"),
        ({"types": ["sale"], "max_pages": 101}, "max_pages"),
        ({"types": ["sale"], "trigger": ""}, "trigger"),
        ({"types": ["sale"], "trigger": "x" * 33}, "trigger"),
        ({"types": ["sale"], "trigger": "mysql://admin:password@db/x"}, "trigger"),
        ({"types": ["sale"], "trigger": "<html>verify</html>"}, "trigger"),
        ({"types": ["sale"], "trigger": "0912-345-678"}, "trigger"),
        ({"types": ["sale"], "trigger": "unknown"}, "trigger"),
    ],
)
def test_admin_update_rejects_invalid_json_contract(
    admin_client: FlaskClient, payload, field: str,
) -> None:
    response = admin_client.post(
        "/api/admin/listing-updates",
        json=payload,
        headers={"X-Qingpu-CSRF": "test-token"},
    )
    assert response.status_code == 400
    assert response.json["error"]["code"] == "invalid_request"
    assert field in response.json["error"]["fields"]
    if field == "trigger" and isinstance(payload, dict) and payload.get("trigger"):
        assert str(payload["trigger"]) not in response.get_data(as_text=True)


def test_admin_update_rejects_malformed_or_wrong_content_type(
    admin_client: FlaskClient,
) -> None:
    malformed = admin_client.post(
        "/api/admin/listing-updates",
        data="{",
        content_type="application/json",
        headers={"X-Qingpu-CSRF": "test-token"},
    )
    wrong_type = admin_client.post(
        "/api/admin/listing-updates",
        data='{"types":["sale"]}',
        content_type="text/plain",
        headers={"X-Qingpu-CSRF": "test-token"},
    )
    assert malformed.status_code == 400
    assert wrong_type.status_code == 400
    assert malformed.json["error"]["code"] == "invalid_request"
    assert wrong_type.json["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("trigger", ["manual", "scheduled", "web"])
def test_admin_update_accepts_only_explicit_supported_triggers(
    admin_client: FlaskClient, trigger: str,
) -> None:
    response = admin_client.post(
        "/api/admin/listing-updates",
        json={"types": ["sale"], "max_pages": 1, "trigger": trigger},
        headers={"X-Qingpu-CSRF": "test-token"},
    )
    assert response.status_code == 202
    assert response.json["trigger"] == trigger


def test_synchronous_handoff_failure_returns_safe_503(
    admin_app, admin_client: FlaskClient,
) -> None:
    _, repo, service, _ = admin_app
    service.handoff_error = RuntimeError(
        "mysql://admin:password@localhost/db <html> 0912-345-678"
    )
    response = admin_client.post(
        "/api/admin/listing-updates",
        json={"types": ["sale"], "max_pages": 1},
        headers={"X-Qingpu-CSRF": "test-token"},
    )
    body = response.get_data(as_text=True)
    assert response.status_code == 503
    assert response.json["error"]["code"] == "enqueue_failed"
    assert "password" not in body
    assert "<html>" not in body
    assert repo._runs


def test_job_detail_and_history_use_public_safe_contract(
    admin_app, admin_client: FlaskClient,
) -> None:
    _, repo, _, _ = admin_app
    from qingpu_insight.jobs import JobService

    service = JobService(repo)
    first = service.create("listing_update", "first", "manual").run
    second = service.create("listing_update", "second", "scheduled").run
    service.start(second.run_id)
    service.fail(
        second.run_id,
        "capture_failed",
        "mysql://admin:password@localhost/db token=abc 0912-345-678 SELECT * FROM users",
    )

    detail = admin_client.get(f"/api/jobs/{second.run_id}")
    history = admin_client.get("/api/jobs?limit=1")
    serialized = detail.get_data(as_text=True)

    assert detail.status_code == 200
    assert detail.json == history.json["items"][0]
    assert detail.json["run_id"] == second.run_id
    assert detail.json["input_version"] is None
    assert detail.json["output_version"] is None
    assert detail.json["started_at"] is not None
    assert detail.json["finished_at"] is not None
    assert history.json["limit"] == 1
    assert first.run_id not in serialized
    assert "password" not in serialized
    assert "token=abc" not in serialized
    assert "0912-345-678" not in serialized
    assert "SELECT" not in serialized


def test_job_detail_redacts_unsafe_nested_summary(
    admin_app, admin_client: FlaskClient,
) -> None:
    _, repo, _, _ = admin_app
    from qingpu_insight.jobs import JobService

    service = JobService(repo)
    run = service.create("listing_update", "unsafe-summary", "manual").run
    service.start(run.run_id)
    service.succeed(
        run.run_id,
        "v-safe",
        {
            "rows": 3,
            "diagnostic": "<html>verification page</html>",
            "database_url": "mysql://admin:password@localhost/db",
            "nested": {"query": "SELECT * FROM contacts"},
        },
    )

    response = admin_client.get(f"/api/jobs/{run.run_id}")
    serialized = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.json["summary"]["rows"] == 3
    assert "<html>" not in serialized
    assert "password" not in serialized
    assert "SELECT" not in serialized


@pytest.mark.parametrize(
    "unsafe_trigger",
    [
        "x" * 64,
        "mysql://admin:password@localhost/db",
        "<html>verification</html>",
        "0912-345-678",
    ],
)
def test_job_detail_and_history_do_not_echo_unsafe_persisted_trigger(
    admin_app, admin_client: FlaskClient, unsafe_trigger: str,
) -> None:
    _, repo, _, _ = admin_app
    from qingpu_insight.jobs import JobService

    service = JobService(repo)
    run = service.create("listing_update", f"unsafe-{len(repo._runs)}", "manual").run
    repo._runs[run.run_id] = replace(run, trigger=unsafe_trigger)

    detail = admin_client.get(f"/api/jobs/{run.run_id}")
    history = admin_client.get("/api/jobs?limit=1")
    assert detail.status_code == 200
    assert detail.json["trigger"] == "redacted"
    assert history.json["items"][0]["trigger"] == "redacted"
    assert unsafe_trigger not in detail.get_data(as_text=True)


def test_secret_bearing_job_repository_value_error_returns_fixed_503(
    admin_app, admin_client: FlaskClient,
) -> None:
    _, repo, _, _ = admin_app
    repo.get = lambda run_id: (_ for _ in ()).throw(
        ValueError("mysql://admin:password@localhost/db SELECT * FROM job_runs")
    )

    response = admin_client.get(
        "/api/jobs/00000000-0000-4000-8000-000000000000"
    )
    body = response.get_data(as_text=True)
    assert response.status_code == 503
    assert response.json["error"]["code"] == "job_unavailable"
    assert "password" not in body
    assert "SELECT" not in body


def test_invalid_market_filter_is_curated_api_input_error(client: FlaskClient) -> None:
    response = client.get(
        "/api/market/summary",
        query_string={"transaction_type": "resale", "date_from": "secret-date"},
    )
    body = response.get_data(as_text=True)
    assert response.status_code == 400
    assert response.json["error"]["code"] == "invalid_request"
    assert response.json["error"]["fields"] == {"date_from": "invalid"}
    assert "secret-date" not in body


def test_secret_bearing_listing_repository_value_error_returns_fixed_503(
    market_frame: pd.DataFrame,
) -> None:
    from qingpu_insight.web import create_app

    class FailingListingRepository:
        def load_current(self, listing_type):
            del listing_type
            raise ValueError(
                "mysql://admin:password@localhost/db SELECT * FROM listing_current"
            )

    app = create_app(
        data_source=InMemoryMarketDataSource(market_frame),
        listing_repo=FailingListingRepository(),
    )
    with app.test_client() as client:
        response = client.get(
            "/api/listings/summary", query_string={"listing_type": "sale"}
        )
    body = response.get_data(as_text=True)
    assert response.status_code == 503
    assert response.json["error"]["code"] == "market_data_unavailable"
    assert "password" not in body
    assert "SELECT" not in body


@pytest.mark.parametrize("limit", ["", "zero", "0", "101", "1.5"])
def test_job_history_rejects_invalid_limit(
    admin_client: FlaskClient, limit: str,
) -> None:
    response = admin_client.get("/api/jobs", query_string={"limit": limit})
    assert response.status_code == 400
    assert response.json["error"]["fields"] == {"limit": "integer_1_to_100"}


def test_job_detail_validates_uuid_before_lookup(admin_client: FlaskClient) -> None:
    invalid = admin_client.get("/api/jobs/not-a-uuid")
    absent = admin_client.get("/api/jobs/00000000-0000-4000-8000-000000000000")
    assert invalid.status_code == 400
    assert invalid.json["error"]["fields"] == {"run_id": "invalid_uuid"}
    assert absent.status_code == 404


def test_production_admin_composition_requires_database_and_strong_secret(
    monkeypatch, tmp_path: Path, market_frame: pd.DataFrame,
) -> None:
    import qingpu_insight.cli as cli
    import qingpu_insight.web as web
    from qingpu_insight.jobs import JobService

    repo = MemoryAdminJobRepository()
    service = StubListingUpdateService(JobService(repo))
    executor = FakeAdminExecutor()
    monkeypatch.setattr(cli, "_create_listing_update_service", lambda root: service)
    monkeypatch.setattr(web, "LocalJobExecutor", lambda job_service: executor)
    monkeypatch.setenv(
        "QINGPU_DATABASE_URL",
        "mysql+pymysql://<user>:<password>@127.0.0.1:3306/<database>",
    )
    strong_secret = "Ab3!xY7@qR9#tU2$vW5&zC8*mN4+eH6@K7"
    monkeypatch.setenv("QINGPU_SECRET_KEY", strong_secret)

    app = web.create_app(
        root=tmp_path, data_source=InMemoryMarketDataSource(market_frame)
    )
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "test-token"
        response = client.post(
            "/api/admin/listing-updates",
            json={"types": ["sale"], "max_pages": 1},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
    assert response.status_code == 202
    assert app.secret_key == strong_secret
    app.extensions["qingpu_admin_shutdown"]()


@pytest.mark.parametrize(
    "secret",
    [
        None,
        "short-secret",
        "dev-secret-key",
        "A" * 64,
        "abcd" * 16,
        "change-me-change-me-change-me-change-me",
        "<at-least-32-cryptographically-random-characters>",
    ],
)
def test_production_admin_fails_closed_without_strong_secret(
    monkeypatch, tmp_path: Path, market_frame: pd.DataFrame, secret: str | None,
) -> None:
    import qingpu_insight.web as web

    composition_calls = []
    monkeypatch.setattr(
        web,
        "_create_production_admin_services",
        lambda root: composition_calls.append(root),
    )
    monkeypatch.setenv("QINGPU_DATABASE_URL", "mysql://placeholder/db")
    if secret is None:
        monkeypatch.delenv("QINGPU_SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("QINGPU_SECRET_KEY", secret)
    app = web.create_app(
        root=tmp_path, data_source=InMemoryMarketDataSource(market_frame)
    )
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "test-token"
        response = client.post(
            "/api/admin/listing-updates",
            json={"types": ["sale"], "max_pages": 1},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
    assert response.status_code == 503
    assert response.json["error"]["code"] == "admin_unavailable"
    assert composition_calls == []


def test_market_composition_error_starts_with_fixed_safe_response(
    monkeypatch, tmp_path: Path,
) -> None:
    import qingpu_insight.web as web

    monkeypatch.setattr(
        web,
        "repository_from_env",
        lambda root: (_ for _ in ()).throw(
            RuntimeError("mysql://admin:password@localhost/db SELECT secret")
        ),
    )
    app = web.create_app(root=tmp_path)
    with app.test_client() as client:
        response = client.get(
            "/api/market/summary", query_string={"transaction_type": "resale"}
        )
    body = response.get_data(as_text=True)
    assert response.status_code == 503
    assert response.json["error"]["code"] == "market_data_unavailable"
    assert "password" not in body
    assert "SELECT" not in body


def test_admin_composition_error_returns_fixed_safe_message(
    monkeypatch, tmp_path: Path, market_frame: pd.DataFrame,
) -> None:
    import qingpu_insight.cli as cli
    import qingpu_insight.web as web

    monkeypatch.setenv("QINGPU_DATABASE_URL", "mysql://<user>:<password>@local/<db>")
    monkeypatch.setenv(
        "QINGPU_SECRET_KEY", "Bc4!yZ8@rS1#uV3%wX6&dE9*fG2+hJ5@L6"
    )
    monkeypatch.setattr(
        cli,
        "_create_listing_update_service",
        lambda root: (_ for _ in ()).throw(
            RuntimeError("mysql://admin:password@localhost/db SELECT secret")
        ),
    )
    app = web.create_app(
        root=tmp_path, data_source=InMemoryMarketDataSource(market_frame)
    )
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "test-token"
        response = client.post(
            "/api/admin/listing-updates",
            json={"types": ["sale"], "max_pages": 1},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
    body = response.get_data(as_text=True)
    assert response.status_code == 503
    assert response.json["error"]["code"] == "admin_unavailable"
    assert "password" not in body
    assert "SELECT" not in body


def test_job_center_polling_state_machine_in_node() -> None:
    result = subprocess.run(
        ["node", "tests/js/job_polling_contract.cjs"],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


class GatePreparationRunner:
    def __init__(self, started: Event, release: Event) -> None:
        self.started = started
        self.release = release
        self.calls: list[str] = []

    def prepare(self, listing_type: str, max_pages: int):
        from qingpu_insight.listing_sources import CaptureBatch
        from qingpu_insight.listing_update import PreparedListingType

        self.calls.append(listing_type)
        self.started.set()
        assert self.release.wait(5), "test did not release preparation gate"
        batch = CaptureBatch(
            batch_id=f"batch-{listing_type}",
            source="591",
            listing_type=listing_type,
            started_at=datetime(2026, 7, 22, tzinfo=UTC),
            reached_terminal_page=True,
        )
        rows = pd.DataFrame(
            [{
                "source": "591",
                "listing_type": listing_type,
                "source_listing_id": f"{listing_type}-1",
                "snapshot_at": batch.started_at,
            }]
        )
        events = pd.DataFrame(
            [{"event_key": f"event-{listing_type}", "listing_type": listing_type}]
        )
        return PreparedListingType(batch, rows, events, {"accepted": 1})


class GatePublisher:
    def __init__(self) -> None:
        self.pointer = None
        self.staged = []

    def current(self):
        return self.pointer

    def stage(self, version, batches, rows, events) -> None:
        self.staged.append((version, list(batches), rows.copy(), events.copy()))

    def publish(self, version: str, expected_current_version: str | None):
        assert expected_current_version is None
        self.pointer = self.staged[-1][0]
        return self.pointer


class GateLock:
    def __init__(self) -> None:
        self.owner = None

    def try_acquire(self) -> bool:
        return True

    def set_owner(self, idempotency_key: str, run_id: str) -> None:
        self.owner = (idempotency_key, run_id)

    def read_owner(self):
        return self.owner

    def release(self) -> None:
        self.owner = None


def test_real_executor_web_flow_starts_once_and_shuts_down(
    tmp_path: Path, market_frame: pd.DataFrame,
) -> None:
    from qingpu_insight.job_executor import LocalJobExecutor
    from qingpu_insight.jobs import JobService
    from qingpu_insight.listing_update import ListingUpdateService
    from qingpu_insight.web import AdminServices, create_app

    repo = MemoryAdminJobRepository()
    jobs = JobService(repo)
    started = Event()
    release = Event()
    preparation = GatePreparationRunner(started, release)
    service = ListingUpdateService(
        jobs,
        GatePublisher(),
        preparation_runner=preparation,
        root=tmp_path,
        lock_factory=GateLock,
    )
    executor = LocalJobExecutor(jobs)
    app = create_app(
        data_source=InMemoryMarketDataSource(market_frame),
        admin_services=AdminServices(jobs, service, executor),
    )
    try:
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["_csrf_token"] = "test-token"
            first = client.post(
                "/api/admin/listing-updates",
                json={"types": ["sale", "newhouse", "rental"], "max_pages": 1},
                headers={"X-Qingpu-CSRF": "test-token"},
            )
            assert first.status_code == 202
            assert started.wait(5), "executor did not enter preparation"
            duplicate = client.post(
                "/api/admin/listing-updates",
                json={"types": ["sale", "newhouse", "rental"], "max_pages": 1},
                headers={"X-Qingpu-CSRF": "test-token"},
            )
            assert duplicate.json["run_id"] == first.json["run_id"]
            assert duplicate.json["created"] is False
            assert preparation.calls == ["sale"]
            release.set()
            assert repo.terminal.wait(5), "job did not reach a terminal state"
            detail = client.get(f"/api/jobs/{first.json['run_id']}")
            assert detail.status_code == 200
            assert detail.json["status"] == "succeeded"
            assert detail.json["output_version"]
            assert detail.json["summary"]["rows"] == 3
            assert preparation.calls == ["sale", "newhouse", "rental"]
    finally:
        release.set()
        app.extensions["qingpu_admin_shutdown"]()
