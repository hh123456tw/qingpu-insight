from __future__ import annotations

import json
import secrets
import subprocess
import uuid
from concurrent.futures import Future
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any

import numpy as np
import pandas as pd
import pytest
from bs4 import BeautifulSoup
from flask.testing import FlaskClient
from sklearn.dummy import DummyRegressor

from qingpu_insight.jobs import JobRun, JobSubmission
from qingpu_insight.market_metrics import MarketFilters
from qingpu_insight.valuation import ModelRegistry, ValuationBundle


class InMemoryMarketDataSource:
    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame

    def load(self, filters: MarketFilters) -> pd.DataFrame:
        return self._frame


class _SecretsStore:
    def __init__(self) -> None:
        self.last_set: str | None = None
        self.deleted: bool = False

    def status(self) -> dict[str, bool]:
        return {"gemini_configured": self.last_set is not None and not self.deleted}

    def set_gemini_key(self, key: str) -> None:
        self.last_set = key
        self.deleted = False

    def delete_gemini_key(self) -> None:
        self.deleted = True

    def merged_env(self, base: dict) -> dict:
        if self.last_set and not self.deleted:
            return {**base, "QINGPU_GEMINI_API_KEY": self.last_set}
        return dict(base)


class FailingMarketDataSource:
    def load(self, filters: MarketFilters) -> pd.DataFrame:
        raise RuntimeError("database connection failed")


def test_conversation_model_catalog_tracks_secret_changes(
    tmp_path: Path,
) -> None:
    from qingpu_insight.local_secrets import LocalSecretsStore
    from qingpu_insight.web import create_app

    app = create_app(root=tmp_path)
    client = app.test_client()
    secret_store = LocalSecretsStore(tmp_path / "instance" / "secrets.env")

    assert client.get("/api/conversation-models").get_json()["gemini_configured"] is False

    secret_store.set_gemini_key("test-dynamic-key")

    assert client.get("/api/conversation-models").get_json()["gemini_configured"] is True


def test_conversation_schema_applies_fallback_metadata_migration(
    tmp_path: Path,
) -> None:
    from qingpu_insight.web import _ensure_conversation_schema

    database = tmp_path / "database"
    database.mkdir()
    (database / "008_conversation_assistant_schema.sql").write_text(
        "CREATE TABLE conversation_messages (id INT);",
        encoding="utf-8",
    )
    (database / "009_conversation_fallback_metadata.sql").write_text(
        "ALTER TABLE conversation_messages ADD COLUMN fallback_reason VARCHAR(64);",
        encoding="utf-8",
    )
    executed: list[str] = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, statement: str) -> None:
            executed.append(statement)

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    _ensure_conversation_schema(tmp_path, Connection)

    assert any("CREATE TABLE conversation_messages" in sql for sql in executed)
    assert any("fallback_reason" in sql for sql in executed)


def test_conversation_valuation_uses_official_model_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from qingpu_insight import web

    market = pd.DataFrame(
        [
            {
                "transaction_type": "resale",
                "transaction_date": pd.Timestamp("2026-06-13"),
                "latitude": 25.01,
                "longitude": 121.21,
                "building_area_ping": 30.0,
                "total_price_twd": 15000000,
                "unit_price_per_ping_twd": 500000,
                "station_code": "A18",
                "station_distance_m": 420.0,
            }
        ]
    )
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        web,
        "build_model_frame",
        lambda frame, transaction_type: frame,
    )

    def fake_valuate(input_, registry, frame, latest_data_date):
        captured["input"] = input_
        return {
            "estimated_total_price_twd": 16000000,
            "estimated_building_price_twd": 14500000,
            "estimated_parking_price_twd": 1500000,
            "interval_total_price_twd": (14500000, 17500000),
            "confidence": "medium",
            "confidence_reasons": ["comparable_count"],
            "asking_price_assessment": "合理區間",
            "comparables": [
                {
                    "record_id": "c1",
                    "similarity_score": 0.8,
                    "dwelling_unit_price_per_ping_twd": 480000,
                }
            ],
            "data_date": "2026-06-13",
            "model": {"version": "official-v3"},
        }

    monkeypatch.setattr(web, "valuate", fake_valuate)
    result = web._conversation_valuation(
        InMemoryMarketDataSource(market),
        object(),  # type: ignore[arg-type]
        {
            "listing_type": "sale",
            "area_ping": 40.32,
            "layout": "3房2廳2衛",
            "building_type": "住宅大樓",
            "floor": "12F/15F",
            "total_floors": 15,
            "age_years": 5,
            "parking_type": "10. 32坪，平面式，已含售金內",
            "total_price_twd": 15800000,
            "latitude": 25.01,
            "longitude": 121.21,
        },
    )

    assert captured["input"].station_code == "A18"
    assert captured["input"].bedrooms == 3
    assert captured["input"].building_area_ping == 30
    assert captured["input"].parking_type == "坡道平面"
    assert captured["input"].parking_area_ping == 10.32
    assert result["point_estimate_twd"] == 16000000
    assert result["model_version"] == "official-v3"
    assert result["dataset_version"] == "2026-06-13"
    assert result["estimated_parking_price_twd"] == 1500000
    assert result["comparables"][0]["similarity_score"] == 0.8


def test_conversation_valuation_maps_591_elevator_building_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from qingpu_insight import web

    captured: dict[str, Any] = {}
    market = pd.DataFrame(
        [
            {
                "transaction_type": "resale",
                "transaction_date": pd.Timestamp("2026-06-13"),
                "latitude": 25.01,
                "longitude": 121.21,
                "building_area_ping": 34.0,
                "total_price_twd": 13580000,
                "unit_price_per_ping_twd": 399000,
                "station_code": "A18",
                "station_distance_m": 500.0,
            }
        ]
    )
    monkeypatch.setattr(
        web,
        "build_model_frame",
        lambda frame, transaction_type: frame,
    )

    def fake_valuate(input_, registry, frame, latest_data_date):
        captured["building_type"] = input_.building_type
        return {
            "estimated_total_price_twd": 12000000,
            "interval_total_price_twd": (10000000, 14000000),
            "confidence": "medium",
            "confidence_reasons": [],
            "data_date": "2026-06-13",
            "model": {"version": "official-v3"},
        }

    monkeypatch.setattr(web, "valuate", fake_valuate)
    web._conversation_valuation(
        InMemoryMarketDataSource(market),
        object(),  # type: ignore[arg-type]
        {
            "listing_type": "sale",
            "area_ping": 34.02,
            "layout": "2房2廳1衛1陽台",
            "building_type": "電梯大樓",
            "floor": "5F/10F",
            "total_floors": 10,
            "age_years": 7,
            "total_price_twd": 13580000,
            "latitude": 25.01,
            "longitude": 121.21,
        },
    )

    assert captured["building_type"] == "華廈(10層含以下有電梯)"


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


# --- M4.4 Report API tests ---


class InMemoryReportRepository:
    def __init__(self) -> None:
        self._reports: dict[str, object] = {}

    def create(self, report) -> object:
        self._reports[report.report_id] = report
        return report

    def get(self, report_id: str) -> object | None:
        return self._reports.get(report_id)


class FakeReportService:
    def __init__(self, repository: InMemoryReportRepository) -> None:
        self._repository = repository
        self._counter = 0

    def generate(self, request) -> object:
        from datetime import UTC, datetime

        from qingpu_insight.report_contracts import SavedBuyerReport

        self._counter += 1
        report_id = f"report-{self._counter:04d}"
        report = SavedBuyerReport(
            report_id=report_id,
            request_hash="test-hash",
            dataset_version="v1",
            evidence_pack_id="pack-1",
            provider=request.provider,
            model="rule",
            content={
                "summary": {
                    "text": f"摘要 {request.candidate_ids[0]}",
                    "fact_ids": ["f1"],
                    "numeric_fact_ids": [],
                },
                "advantages": [{"text": "優點 1", "fact_ids": ["f1"], "numeric_fact_ids": []}],
                "risks": [{"text": "風險 1", "fact_ids": ["f1"], "numeric_fact_ids": []}],
                "negotiation": [{"text": "議價 1", "fact_ids": ["f1"], "numeric_fact_ids": []}],
                "limitations": [{"text": "限制 1", "fact_ids": ["f1"], "numeric_fact_ids": []}],
            },
            fallback_reason=None,
            validation_codes=(),
            latency_ms=42.0,
            created_at=datetime.now(UTC).isoformat(),
        )
        return self._repository.create(report)


def _report_post(client: FlaskClient, json_data=None, **kwargs) -> Any:
    """POST with CSRF token and loopback."""
    client.get("/")  # establish session with _csrf_token
    with client.session_transaction() as sess:
        token = sess.get("_csrf_token", "")
    kw = dict(kwargs)
    if json_data is not None:
        kw["json"] = json_data
    kw["headers"] = {**kw.get("headers", {}), "X-Qingpu-CSRF": token}
    kw["environ_base"] = {**kw.get("environ_base", {}), "REMOTE_ADDR": "127.0.0.1"}
    return client.post("/api/reports", **kw)


@pytest.fixture
def report_app(market_frame: pd.DataFrame) -> FlaskClient:
    from qingpu_insight.web import create_app

    repo = InMemoryReportRepository()
    service = FakeReportService(repo)
    ds = InMemoryMarketDataSource(market_frame)
    app = create_app(data_source=ds, report_service=service, report_repository=repo)
    with app.test_client() as client:
        yield client


class TestReportApi:
    def test_post_report_requires_json(self, report_app: FlaskClient) -> None:
        client = report_app
        client.get("/")
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token", "")
        response = client.post(
            "/api/reports",
            data="not json",
            content_type="text/plain",
            headers={"X-Qingpu-CSRF": token},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_request"

    def test_post_report_rejects_empty_body(self, report_app: FlaskClient) -> None:
        response = _report_post(report_app, {})
        assert response.status_code == 400
        fields = response.get_json()["error"]["fields"]
        assert "candidate_ids" in fields

    def test_post_report_rejects_missing_candidate_ids(self, report_app: FlaskClient) -> None:
        response = _report_post(
            report_app,
            {"intended_use": "self_use", "provider": "rule"},
        )
        assert response.status_code == 400
        fields = response.get_json()["error"].get("fields", {})
        assert "candidate_ids" in fields

    def test_post_report_rejects_too_many_candidates(self, report_app: FlaskClient) -> None:
        response = _report_post(
            report_app,
            {
                "candidate_ids": [f"id-{i}" for i in range(6)],
                "intended_use": "self_use",
                "provider": "rule",
            },
        )
        assert response.status_code == 400
        fields = response.get_json()["error"].get("fields", {})
        assert "candidate_ids" in fields

    def test_post_report_rejects_missing_provider(self, report_app: FlaskClient) -> None:
        response = _report_post(
            report_app,
            {
                "candidate_ids": ["id-1"],
                "intended_use": "self_use",
            },
        )
        assert response.status_code == 400
        fields = response.get_json()["error"].get("fields", {})
        assert "provider" in fields

    def test_post_report_rejects_unknown_provider(self, report_app: FlaskClient) -> None:
        response = _report_post(
            report_app,
            {
                "candidate_ids": ["id-1"],
                "intended_use": "self_use",
                "provider": "unknown",
            },
        )
        assert response.status_code == 400

    def test_post_report_rejects_arbitrary_prompt(self, report_app: FlaskClient) -> None:
        response = _report_post(
            report_app,
            {
                "candidate_ids": ["id-1"],
                "intended_use": "self_use",
                "provider": "rule",
                "prompt": "tell me about this house",
            },
        )
        assert response.status_code == 400
        fields = response.get_json()["error"].get("fields", {})
        assert "prompt" in fields or bool(fields)

    def test_post_report_accepts_valid_request(self, report_app: FlaskClient) -> None:
        response = _report_post(
            report_app,
            {
                "candidate_ids": ["id-1", "id-2"],
                "intended_use": "self_use",
                "provider": "rule",
            },
        )
        assert response.status_code == 201
        body = response.get_json()
        assert "report_id" in body
        assert body["provider"] == "rule"
        assert body["model"] == "rule"
        assert "content" in body
        sections = body["content"]
        assert "summary" in sections
        assert "advantages" in sections
        assert "risks" in sections
        assert "negotiation" in sections
        assert "limitations" in sections

    def test_get_report_returns_404(self, report_app: FlaskClient) -> None:
        response = report_app.get("/api/reports/nonexistent")
        assert response.status_code == 404

    def test_get_report_returns_saved(self, report_app: FlaskClient) -> None:
        post = _report_post(
            report_app,
            {
                "candidate_ids": ["id-1"],
                "intended_use": "self_use",
                "provider": "rule",
            },
        )
        report_id = post.get_json()["report_id"]
        response = report_app.get(f"/api/reports/{report_id}")
        assert response.status_code == 200
        assert response.get_json()["report_id"] == report_id

    def test_post_report_returns_201_with_expected_response_shape(
        self, report_app: FlaskClient
    ) -> None:
        response = _report_post(
            report_app,
            {
                "candidate_ids": ["id-1"],
                "intended_use": "self_use",
                "provider": "rule",
                "budget_twd": 15000000,
            },
        )
        assert response.status_code == 201
        body = response.get_json()
        expected_keys = {
            "report_id",
            "provider",
            "model",
            "dataset_version",
            "evidence_pack_id",
            "fallback_reason",
            "content",
            "created_at",
        }
        assert expected_keys.issubset(body.keys())
        assert body["fallback_reason"] is None

    def test_post_report_rejects_untrusted_host(self, report_app: FlaskClient) -> None:
        with report_app.session_transaction() as sess:
            token = sess.get("_csrf_token", "")
        response = report_app.post(
            "/api/reports",
            json={
                "candidate_ids": ["id-1"],
                "intended_use": "self_use",
                "provider": "rule",
            },
            headers={"X-Qingpu-CSRF": token},
            base_url="http://attacker.example",
        )
        assert response.status_code == 403

    def test_post_report_rejects_missing_csrf(self, report_app: FlaskClient) -> None:
        response = report_app.post(
            "/api/reports",
            json={
                "candidate_ids": ["id-1"],
                "intended_use": "self_use",
                "provider": "rule",
            },
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "csrf_mismatch"

    def test_post_report_rejects_wrong_csrf(self, report_app: FlaskClient) -> None:
        response = report_app.post(
            "/api/reports",
            json={
                "candidate_ids": ["id-1"],
                "intended_use": "self_use",
                "provider": "rule",
            },
            headers={"X-Qingpu-CSRF": "wrong-token"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "csrf_mismatch"

    def test_post_report_accepts_matching_csrf(self, report_app: FlaskClient) -> None:
        client = report_app
        client.get("/")
        with client.session_transaction() as sess:
            token = sess.get("_csrf_token", "")
        response = client.post(
            "/api/reports",
            json={
                "candidate_ids": ["id-1"],
                "intended_use": "self_use",
                "provider": "rule",
            },
            headers={"X-Qingpu-CSRF": token},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        assert response.status_code == 201


def test_homepage_has_h1_before_assistant_starter_and_valuation_fieldsets(client) -> None:
    home = BeautifulSoup(client.get("/").get_data(as_text=True), "html.parser")
    assert home.find(["h1", "h2"]).name == "h1"
    assert home.select_one("#assistant-starter") is not None
    assert home.select_one("#report-form") is None
    assert home.select_one("#report-result") is None
    assert home.select_one("#valuation-form fieldset.basic-valuation-fields") is not None
    assert home.select_one("#valuation-form fieldset.detailed-valuation-fields") is not None


def test_homepage_keeps_user_features_and_moves_ops_to_admin(client) -> None:
    home = BeautifulSoup(client.get("/").get_data(as_text=True), "html.parser")
    admin_page = BeautifulSoup(client.get("/admin/").get_data(as_text=True), "html.parser")
    assert home.select_one("#valuation-form") is not None
    assert home.select_one("#report-form") is None
    assert home.select_one("#job-submit") is None
    assert home.select_one(".ops-panel") is None
    assert home.select_one('a[href="/admin"]') is not None
    assert admin_page.select_one("#admin-data") is not None
    assert admin_page.select_one("#admin-backups") is None


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

    @pytest.mark.parametrize(
        ("query", "fields"),
        [
            (
                "transaction_type=secret-unsupported-type",
                {"transaction_type": "resale_or_presale"},
            ),
            (
                "transaction_type=resale&station=secret-station",
                {"station": "A17_A18_or_A19"},
            ),
            (
                "transaction_type=resale&area_ping_min=-1",
                {"area_ping_min": "non_negative"},
            ),
            (
                "transaction_type=resale&area_ping_max=-1",
                {"area_ping_max": "non_negative"},
            ),
            (
                "transaction_type=resale&area_ping_min=-1&area_ping_max=-2",
                {
                    "area_ping_min": "non_negative",
                    "area_ping_max": "non_negative",
                },
            ),
            (
                "transaction_type=resale&area_ping_min=20&area_ping_max=10",
                {
                    "area_ping_min": "must_not_exceed_area_ping_max",
                    "area_ping_max": "must_not_be_less_than_area_ping_min",
                },
            ),
        ],
    )
    def test_domain_filter_errors_are_curated_as_field_specific_400s(
        self, client: FlaskClient, query: str, fields: dict[str, str]
    ) -> None:
        response = client.get(f"/api/market/summary?{query}")

        assert response.status_code == 400
        assert response.get_json() == {
            "error": {
                "code": "invalid_request",
                "message": "篩選條件無效。",
                "fields": fields,
            }
        }
        serialized = response.get_data(as_text=True)
        assert "secret-unsupported-type" not in serialized
        assert "secret-station" not in serialized

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

    def test_map_points_reports_complete_counts_and_public_groups(
        self,
        client: FlaskClient,
    ) -> None:
        response = client.get("/api/market/map-points?transaction_type=resale&station=A18&zoom=14")

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["total_records"] == 2
        assert payload["located_records"] <= payload["total_records"]
        assert payload["unlocated_records"] == (
            payload["total_records"] - payload["located_records"]
        )
        assert payload["group_count"] == len(payload["items"])
        assert set(payload["items"][0]) == {
            "latitude",
            "longitude",
            "record_count",
            "median_unit_price_per_ping_twd",
            "latest_transaction_date",
        }

    def test_map_points_bounds_do_not_change_complete_filtered_count(
        self,
        client: FlaskClient,
    ) -> None:
        base = client.get(
            "/api/market/map-points?transaction_type=resale&station=A18&zoom=14"
        ).get_json()
        bounded = client.get(
            "/api/market/map-points"
            "?transaction_type=resale&station=A18&zoom=14"
            "&south=24&west=120&north=24.1&east=120.1"
        ).get_json()

        assert bounded["total_records"] == base["total_records"]
        assert bounded["items"] == []

    @pytest.mark.parametrize(
        ("query", "fields"),
        [
            ("zoom=9", {"zoom": "integer_10_to_19"}),
            ("zoom=14.5", {"zoom": "integer_10_to_19"}),
            ("zoom=14&south=24", {"bounds": "all_or_none"}),
            (
                "zoom=14&south=25&west=121&north=24&east=122",
                {"bounds": "ordered_finite_numbers"},
            ),
            (
                "zoom=14&south=nan&west=121&north=25&east=122",
                {"bounds": "ordered_finite_numbers"},
            ),
        ],
    )
    def test_map_points_rejects_invalid_view_parameters(
        self, client: FlaskClient, query: str, fields: dict[str, str]
    ) -> None:
        response = client.get(f"/api/market/map-points?transaction_type=resale&{query}")

        assert response.status_code == 400
        assert response.get_json()["error"]["fields"] == fields


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

    listing_df = pd.DataFrame(
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
            },
            {
                "source": "591",
                "source_listing_id": "L002",
                "listing_type": "sale",
                "snapshot_at": pd.Timestamp("2026-07-20 10:00", tz="UTC"),
                "source_url": "https://sale.591.com.tw/L002",
                "title": "A17大樓",
                "asking_price_twd": 22_000_000,
                "building_area_ping": 48.0,
                "station_code": "A17",
                "latitude": 25.0156,
                "longitude": 121.2078,
                "location_eligible": True,
                "active": True,
            },
            {
                "source": "591",
                "source_listing_id": "N001",
                "listing_type": "newhouse",
                "snapshot_at": pd.Timestamp("2026-07-20 10:00", tz="UTC"),
                "source_url": "https://newhouse.591.com.tw/N001",
                "title": "青埔預售案",
                "asking_price_twd": None,
                "asking_unit_price_low_twd_per_ping": 500_000,
                "asking_unit_price_high_twd_per_ping": 560_000,
                "building_area_min_ping": 19.0,
                "building_area_max_ping": 30.0,
                "station_code": "A18",
                "latitude": 25.0123,
                "longitude": 121.2018,
                "location_eligible": True,
                "active": True,
            },
            {
                "source": "591",
                "source_listing_id": "OUT001",
                "listing_type": "newhouse",
                "snapshot_at": pd.Timestamp("2026-07-20 10:00", tz="UTC"),
                "source_url": "https://newhouse.591.com.tw/OUT001",
                "title": "圈外預售案",
                "asking_price_twd": 20_000_000,
                "station_code": "A18",
                "latitude": 25.0123,
                "longitude": 121.2018,
                "location_eligible": False,
                "active": True,
            },
        ]
    )
    events_df = pd.DataFrame(
        [
            {
                "event_key": "a" * 64,
                "source": "591",
                "listing_type": "sale",
                "source_listing_id": "L001",
                "event_type": "price_decrease",
                "event_data": (
                    '{"previous_price":20000000,"new_price":18000000,'
                    '"absolute_change":-2000000,"percentage_change":-10.0}'
                ),
                "occurred_at": pd.Timestamp("2026-07-19 10:00", tz="UTC"),
            },
        ]
    )
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
            "listing_id",
            "type",
            "title",
            "source_url",
            "station",
            "area",
            "price",
            "event",
            "status",
            "latitude",
            "longitude",
            "model_evidence",
            "snapshot_time",
            "unit_price_range_twd_per_ping",
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
        first_absence = detect_listing_events(incomplete, empty, batch("B3", True)).state
        second_absence = detect_listing_events(first_absence, empty, batch("B4", True)).state

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
                summary = api.get("/api/listings/summary?listing_type=sale&station=A18").get_json()
                listings = api.get("/api/listings?listing_type=sale&station=A18").get_json()
            assert summary["active_count"] == expected_count
            assert len(listings["items"]) == expected_count


def test_unknown_route_preserves_http_404(client: FlaskClient) -> None:
    assert client.get("/does-not-exist").status_code == 404


# --- Valuation API tests ---


@pytest.fixture
def trained_registry(tmp_path) -> ModelRegistry:
    import joblib

    from qingpu_insight.parking_valuation import ParkingPricePolicy, ParkingPriceStat

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
        parking_price_policy=ParkingPricePolicy(
            version=1,
            minimum_type_samples=1,
            by_type={"坡道平面": ParkingPriceStat(price_twd=1700000, sample_size=50)},
            market_fallback=ParkingPriceStat(price_twd=1500000, sample_size=200),
        ),
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


@pytest.fixture
def valid_payload():
    return dict(VALID_RESALE_PAYLOAD)


def test_valuation_rejects_selected_parking_with_zero_area(client, valid_payload):
    valid_payload.update(parking_type="坡道平面", parking_area_ping=0)
    response = client.post("/api/valuations", json=valid_payload)
    assert response.status_code == 400
    assert response.json["error"]["fields"]["parking_area_ping"] == "positive_when_parking_selected"


def test_valuation_normalizes_no_parking_area(valuation_client, valid_payload):
    valid_payload.update(parking_type="", parking_area_ping=8)
    response = valuation_client.post("/api/valuations", json=valid_payload)
    assert response.status_code == 201
    assert response.json["estimated_parking_price_twd"] == 0


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
                "pending",
                "running",
                "retry_wait",
            ):
                return run
        return None

    def list_recent(self, limit: int = 20, job_type: str | None = None) -> list[JobRun]:
        all_runs = reversed(list(self._runs.values()))
        if job_type is not None:
            all_runs = (r for r in all_runs if r.job_type == job_type)
        return list(all_runs)[:limit]

    def list_active(self, job_type: str) -> list[JobRun]:
        return [
            r
            for r in self._runs.values()
            if r.job_type == job_type and r.status in ("pending", "running", "retry_wait")
        ]

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
        self.shutdown_calls = 0

    def submit(self, run_id: str, callable) -> Future:
        self.submitted.append(run_id)
        return Future()

    def shutdown(self, wait: bool = True) -> None:
        del wait
        self.shutdown_calls += 1


class StubListingUpdateService:
    def __init__(self, job_service) -> None:
        self.job_service = job_service
        self.handoffs: list[str] = []
        self.requests = []
        self.handoff_error: Exception | None = None

    def submit(self, request):
        self.requests.append(request)
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
    official_ds = StubOfficialDataService(job_service)
    app = create_app(
        data_source=InMemoryMarketDataSource(market_frame),
        admin_services=AdminServices(
            job_service,
            listing_service,
            executor,
            official_data_service=official_ds,
        ),
    )
    from dataclasses import replace

    app.extensions["qingpu_admin_runtime"] = replace(
        app.extensions["qingpu_admin_runtime"],
        dashboard_service=StubDashboardService(),
        llm_model_catalog=FakeLlmModelCatalog(),
    )
    return app, repo, listing_service, executor


def test_conversation_runtime_owns_executor_separate_from_admin(
    tmp_path: Path,
    market_frame: pd.DataFrame,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qingpu_insight.cli as cli
    import qingpu_insight.web as web
    from qingpu_insight.jobs import JobService

    jobs = JobService(MemoryAdminJobRepository())
    admin_executor = FakeAdminExecutor()
    conversation_executor = FakeAdminExecutor()
    admin_services = web.AdminServices(
        jobs,
        StubListingUpdateService(jobs),
        admin_executor,
    )
    monkeypatch.setenv(
        "QINGPU_DATABASE_URL",
        "mysql+pymysql://user:password@127.0.0.1:3306/qingpu_insight",
    )
    monkeypatch.setattr(
        cli,
        "create_mysql_connection_factory",
        lambda: object(),
    )
    monkeypatch.setattr(web, "_ensure_conversation_schema", lambda root, factory: None)
    monkeypatch.setattr(
        web,
        "LocalJobExecutor",
        lambda job_service: conversation_executor,
    )

    app = web.create_app(
        root=tmp_path,
        data_source=InMemoryMarketDataSource(market_frame),
        admin_services=admin_services,
        conversation_repository=object(),
    )

    assert app.extensions["qingpu_conversation_executor"] is conversation_executor
    assert app.extensions["qingpu_conversation_executor"] is not admin_executor

    app.extensions["qingpu_admin_shutdown"]()
    assert admin_executor.shutdown_calls == 1
    assert conversation_executor.shutdown_calls == 1


@pytest.fixture
def admin_client(admin_app) -> FlaskClient:
    app, _, _, _ = admin_app
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "test-token"
        yield client


def test_web_app_wires_catalog_and_benchmark_runner(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from qingpu_insight.web import create_app

    app = create_app(root=tmp_path)
    runtime = app.extensions["qingpu_admin_runtime"]

    assert runtime.llm_model_catalog is not None
    assert runtime.provider_ops_service._benchmark_runner is not None


def test_listing_update_returns_202_without_waiting(
    admin_app,
    admin_client: FlaskClient,
) -> None:
    _, _, service, executor = admin_app
    response = admin_client.post(
        "/api/admin/listing-updates",
        json={"types": ["sale", "newhouse"], "max_pages": 1},
        headers={"X-Qingpu-CSRF": "test-token"},
    )
    assert response.status_code == 202
    assert response.json["status"] == "pending"
    assert response.json["created"] is True
    assert executor.submitted == [response.json["run_id"]]
    assert service.handoffs == [response.json["run_id"]]


def test_exact_active_duplicate_returns_existing_run_without_second_handoff(
    admin_app,
    admin_client: FlaskClient,
) -> None:
    _, _, service, executor = admin_app
    request = {
        "json": {"types": ["sale", "newhouse"], "max_pages": 1},
        "headers": {"X-Qingpu-CSRF": "test-token"},
    }
    first = admin_client.post("/api/admin/listing-updates", **request)
    duplicate = admin_client.post("/api/admin/listing-updates", **request)

    assert duplicate.status_code == 202
    assert duplicate.json["run_id"] == first.json["run_id"]
    assert duplicate.json["created"] is False
    assert service.handoffs == [first.json["run_id"]]
    assert executor.submitted == [first.json["run_id"]]


def test_listing_update_defaults_to_sale_and_newhouse(
    admin_app,
    admin_client: FlaskClient,
) -> None:
    _, _, service, _ = admin_app
    response = admin_client.post(
        "/api/admin/listing-updates",
        json={},
        headers={"X-Qingpu-CSRF": "test-token"},
    )
    assert response.status_code == 202
    assert service.requests[-1].types == ("sale", "newhouse")


def test_listing_update_rejects_rental_before_job_creation(
    admin_app,
    admin_client: FlaskClient,
) -> None:
    _, repo, service, executor = admin_app
    response = admin_client.post(
        "/api/admin/listing-updates",
        json={"types": ["rental"], "max_pages": 1},
        headers={"X-Qingpu-CSRF": "test-token"},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["fields"]["types"] == "supported_values"
    assert service.requests == []
    assert executor.submitted == []
    assert repo.list_recent(limit=10) == []


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
    admin_client: FlaskClient,
    path: str,
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
    admin_client: FlaskClient,
    payload,
    field: str,
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
    admin_client: FlaskClient,
    trigger: str,
) -> None:
    response = admin_client.post(
        "/api/admin/listing-updates",
        json={"types": ["sale"], "max_pages": 1, "trigger": trigger},
        headers={"X-Qingpu-CSRF": "test-token"},
    )
    assert response.status_code == 202
    assert response.json["trigger"] == trigger


def test_synchronous_handoff_failure_returns_safe_503(
    admin_app,
    admin_client: FlaskClient,
) -> None:
    _, repo, service, _ = admin_app
    service.handoff_error = RuntimeError("mysql://admin:password@localhost/db <html> 0912-345-678")
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
    admin_app,
    admin_client: FlaskClient,
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
    admin_app,
    admin_client: FlaskClient,
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
    "database_url",
    [
        "mysql://admin:password@db.internal:3306/private",
        "mysql+pymysql://db.internal/private",
        "mariadb://db.internal/private",
        "postgresql://db.internal/private",
        "postgres://db.internal/private",
        "QINGPU_DATABASE_URL=customdb://db.internal/private",
    ],
)
def test_database_urls_are_fully_redacted_from_post_detail_and_history(
    admin_app,
    admin_client: FlaskClient,
    database_url: str,
) -> None:
    _, repo, listing_service, _ = admin_app
    from qingpu_insight.jobs import JobService

    run = JobService(repo).create("listing_update", database_url, "manual").run
    unsafe = replace(
        run,
        trigger=database_url,
        summary={
            "connection": database_url,
            "public_report": "https://public.example/results/v2",
        },
        error_message=database_url,
    )
    repo._runs[run.run_id] = unsafe
    listing_service.submit = lambda request: JobSubmission(unsafe, False)

    post = admin_client.post(
        "/api/admin/listing-updates",
        json={"types": ["sale"], "max_pages": 1},
        headers={"X-Qingpu-CSRF": "test-token"},
    )
    detail = admin_client.get(f"/api/jobs/{run.run_id}")
    history = admin_client.get("/api/jobs?limit=1")

    for payload in (post.json, detail.json, history.json["items"][0]):
        assert payload["trigger"] == "redacted"
        assert payload["summary"]["connection"] == "redacted"
        assert payload["error_message"] == "redacted"
        assert payload["summary"]["public_report"] == ("https://public.example/results/v2")
    for response in (post, detail, history):
        serialized = response.get_data(as_text=True)
        assert database_url not in serialized
        assert "db.internal" not in serialized
        assert "/private" not in serialized


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
    admin_app,
    admin_client: FlaskClient,
    unsafe_trigger: str,
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
    admin_app,
    admin_client: FlaskClient,
) -> None:
    _, repo, _, _ = admin_app
    repo.get = lambda run_id: (_ for _ in ()).throw(
        ValueError("mysql://admin:password@localhost/db SELECT * FROM job_runs")
    )

    response = admin_client.get("/api/jobs/00000000-0000-4000-8000-000000000000")
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
            raise ValueError("mysql://admin:password@localhost/db SELECT * FROM listing_current")

    app = create_app(
        data_source=InMemoryMarketDataSource(market_frame),
        listing_repo=FailingListingRepository(),
    )
    with app.test_client() as client:
        response = client.get("/api/listings/summary", query_string={"listing_type": "sale"})
    body = response.get_data(as_text=True)
    assert response.status_code == 503
    assert response.json["error"]["code"] == "market_data_unavailable"
    assert "password" not in body
    assert "SELECT" not in body


@pytest.mark.parametrize("limit", ["", "zero", "0", "101", "1.5"])
def test_job_history_rejects_invalid_limit(
    admin_client: FlaskClient,
    limit: str,
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


def test_runtime_app_loads_dotenv_and_wires_listing_repository(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import qingpu_insight.cli as cli
    import qingpu_insight.web as web

    listing_repo = object()
    dotenv_calls = []
    repository_calls = []

    monkeypatch.setattr(
        web,
        "load_dotenv",
        lambda path, override: dotenv_calls.append((path, override)),
    )
    monkeypatch.setattr(
        cli,
        "create_listing_repository",
        lambda root: repository_calls.append(root) or listing_repo,
    )
    monkeypatch.setattr(web, "create_app", lambda **kwargs: kwargs)

    runtime = web._create_runtime_app(tmp_path)

    assert dotenv_calls == [(tmp_path / ".env", False)]
    assert repository_calls == [tmp_path]
    assert runtime == {"root": tmp_path, "listing_repo": listing_repo}


def test_production_admin_composition_requires_database_and_strong_secret(
    monkeypatch,
    tmp_path: Path,
    market_frame: pd.DataFrame,
) -> None:
    import qingpu_insight.cli as cli
    import qingpu_insight.web as web
    from qingpu_insight.jobs import JobService

    repo = MemoryAdminJobRepository()
    service = StubListingUpdateService(JobService(repo))
    executor = FakeAdminExecutor()
    monkeypatch.setattr(
        cli,
        "_create_listing_update_service",
        lambda root, **kwargs: service,
    )
    monkeypatch.setattr(web, "LocalJobExecutor", lambda job_service: executor)
    monkeypatch.setenv(
        "QINGPU_DATABASE_URL",
        "mysql+pymysql://<user>:<password>@127.0.0.1:3306/<database>",
    )
    strong_secret = "Ab3!xY7@qR9#tU2$vW5&zC8*mN4+eH6@K7"
    monkeypatch.setenv("QINGPU_SECRET_KEY", strong_secret)

    app = web.create_app(root=tmp_path, data_source=InMemoryMarketDataSource(market_frame))
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
        "01234567" * 8,
        "0123456789abcdef" * 4,
        "Abcdefghijklmnop12345678!@#$%^&*" * 2,
        "abcdefghijklmnopqrstuvwxyzABCDEF1234!",
        "change-me-change-me-change-me-change-me",
        "<at-least-32-cryptographically-random-characters>",
    ],
)
def test_production_admin_fails_closed_without_strong_secret(
    monkeypatch,
    tmp_path: Path,
    market_frame: pd.DataFrame,
    secret: str | None,
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
    app = web.create_app(root=tmp_path, data_source=InMemoryMarketDataSource(market_frame))
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


def test_production_admin_accepts_generated_secret_formats() -> None:
    import qingpu_insight.web as web

    generated = (secrets.token_hex(32), secrets.token_urlsafe(32) + "Aa1!")

    assert all(web._strong_admin_secret(value) for value in generated)


# --- M4.3 Ops API tests ---


class FakeOpsProbes:
    def __init__(self) -> None:
        self._now = datetime.now(UTC)

    def mysql(self):
        from qingpu_insight.health import HealthItem

        return HealthItem("mysql", "healthy", self._now, "ok", 1, "boolean")

    def market_dataset(self):
        from qingpu_insight.health import HealthItem

        return HealthItem("market_dataset", "healthy", self._now, "ok", 1, "boolean")

    def listing_dataset(self, listing_type: str):
        from qingpu_insight.health import HealthItem

        return HealthItem(f"listing_{listing_type}", "healthy", self._now, "ok", 1, "count")

    def latest_listing_job(self):
        from qingpu_insight.health import HealthItem

        return HealthItem("latest_listing_job", "healthy", self._now, "ok", None, None)

    def latest_backup(self):
        from qingpu_insight.health import HealthItem

        return HealthItem("latest_backup", "healthy", self._now, "backup exists", None, None)

    def disk_free(self):
        from qingpu_insight.health import HealthItem

        return HealthItem("disk_free", "healthy", self._now, "ok", 100 * 1024**3, "bytes")


@pytest.fixture
def ops_app(market_frame: pd.DataFrame):
    from qingpu_insight.health import HealthService
    from qingpu_insight.web import OpsServices, create_app

    now = datetime.now(UTC)
    probes = FakeOpsProbes()
    health_service = HealthService(probes)

    class FakeHealthRepo:
        def save(self, summary):
            self.saved = summary

        def latest(self):
            from qingpu_insight.health import HealthItem, HealthSummary

            return HealthSummary(
                status="healthy",
                checked_at=now,
                items=(HealthItem("mysql", "healthy", now, "ok", 1, "boolean"),),
            )

    from qingpu_insight.backups import BackupRecord

    class FakeBackupRepo:
        def list_recent(self, limit):
            return [
                BackupRecord(
                    backup_id="backup-1",
                    status="completed",
                    path="backup-1.sql",
                    sha256="abc",
                    size_bytes=100,
                    created_at=now,
                    restore_status=None,
                    restore_checked_at=None,
                )
            ]

    ops = OpsServices(
        health_service=health_service,
        health_repository=FakeHealthRepo(),
        backup_repository=FakeBackupRepo(),
    )
    app = create_app(
        data_source=InMemoryMarketDataSource(market_frame),
        ops_services=ops,
    )
    with app.test_client() as client:
        yield client


def test_ops_health_returns_200_and_contract(ops_app) -> None:
    response = ops_app.get("/api/ops/health")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] in ("healthy", "warning", "critical")
    assert "checked_at" in body
    assert isinstance(body["items"], list)


def test_ops_backups_returns_items(ops_app) -> None:
    response = ops_app.get("/api/ops/backups?limit=10")
    assert response.status_code == 200
    body = response.get_json()
    assert "items" in body
    assert body["limit"] == 10
    assert len(body["items"]) == 1
    assert body["items"][0]["backup_id"] == "backup-1"
    assert body["items"][0]["status"] == "completed"


def test_ops_backups_rejects_invalid_limit(ops_app) -> None:
    response = ops_app.get("/api/ops/backups?limit=101")
    assert response.status_code == 400
    assert response.get_json()["error"]["fields"] == {"limit": "integer_1_to_100"}


def test_ops_backups_post_returns_503_without_admin(ops_app) -> None:
    response = ops_app.post("/api/ops/backups")
    assert response.status_code == 503


def test_ops_restore_returns_404(ops_app) -> None:
    response = ops_app.post("/api/ops/restore")
    assert response.status_code == 404


def test_ops_health_rejects_non_loopback(ops_app) -> None:
    response = ops_app.get("/api/ops/health", environ_base={"REMOTE_ADDR": "10.0.0.2"})
    assert response.status_code == 403


def test_ops_backups_rejects_non_loopback(ops_app) -> None:
    response = ops_app.get("/api/ops/backups", environ_base={"REMOTE_ADDR": "10.0.0.2"})
    assert response.status_code == 403


def test_ops_health_rejects_untrusted_host(ops_app) -> None:
    response = ops_app.get("/api/ops/health", base_url="http://attacker.example")
    assert response.status_code == 403


def test_ops_backups_rejects_untrusted_host(ops_app) -> None:
    response = ops_app.get("/api/ops/backups", base_url="http://attacker.example")
    assert response.status_code == 403


def test_ops_unavailable_returns_503(client) -> None:
    assert client.get("/api/ops/health").status_code == 503
    assert client.get("/api/ops/backups").status_code == 503


def test_ops_health_repository_failure_returns_safe_503(market_frame) -> None:
    from qingpu_insight.health import HealthService
    from qingpu_insight.web import OpsServices, create_app

    probes = FakeOpsProbes()
    health_service = HealthService(probes)

    class FailingHealthRepo:
        def save(self, summary):
            raise RuntimeError("mysql://admin:password@localhost/db")

    ops = OpsServices(
        health_service=health_service,
        health_repository=FailingHealthRepo(),
        backup_repository=None,
    )
    app = create_app(
        data_source=InMemoryMarketDataSource(market_frame),
        ops_services=ops,
    )
    with app.test_client() as c:
        response = c.get("/api/ops/health")
        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "ops_unavailable"


def test_ops_backups_repository_failure_returns_safe_503(market_frame) -> None:
    from qingpu_insight.web import OpsServices, create_app

    class FailingBackupRepo:
        def list_recent(self, limit):
            raise RuntimeError("mysql://admin:password@localhost/db")

    ops = OpsServices(
        health_service=None,
        health_repository=None,
        backup_repository=FailingBackupRepo(),
    )
    app = create_app(
        data_source=InMemoryMarketDataSource(market_frame),
        ops_services=ops,
    )
    with app.test_client() as c:
        response = c.get("/api/ops/backups")
        body = response.get_data(as_text=True)
        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "ops_unavailable"
        assert "password" not in body
        assert "/db" not in body


def test_ops_health_error_does_not_leak_secrets(market_frame) -> None:
    from qingpu_insight.web import OpsServices, create_app

    class FailingHealthService:
        def run(self):
            raise RuntimeError("mysql://admin:password@localhost/db SELECT * FROM health")

    ops = OpsServices(
        health_service=FailingHealthService(),
        health_repository=None,
        backup_repository=None,
    )
    app = create_app(
        data_source=InMemoryMarketDataSource(market_frame),
        ops_services=ops,
    )
    with app.test_client() as c:
        response = c.get("/api/ops/health")
        body = response.get_data(as_text=True)
        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "ops_unavailable"
        assert "password" not in body
        assert "SELECT" not in body


def test_market_composition_error_starts_with_fixed_safe_response(
    monkeypatch,
    tmp_path: Path,
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
        response = client.get("/api/market/summary", query_string={"transaction_type": "resale"})
    body = response.get_data(as_text=True)
    assert response.status_code == 503
    assert response.json["error"]["code"] == "market_data_unavailable"
    assert "password" not in body
    assert "SELECT" not in body


def test_admin_composition_error_returns_fixed_safe_message(
    monkeypatch,
    tmp_path: Path,
    market_frame: pd.DataFrame,
) -> None:
    import qingpu_insight.cli as cli
    import qingpu_insight.web as web

    monkeypatch.setenv("QINGPU_DATABASE_URL", "mysql://<user>:<password>@local/<db>")
    monkeypatch.setenv("QINGPU_SECRET_KEY", "Bc4!yZ8@rS1#uV3%wX6&dE9*fG2+hJ5@L6")
    monkeypatch.setattr(
        cli,
        "_create_listing_update_service",
        lambda root: (_ for _ in ()).throw(
            RuntimeError("mysql://admin:password@localhost/db SELECT secret")
        ),
    )
    app = web.create_app(root=tmp_path, data_source=InMemoryMarketDataSource(market_frame))
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
            [
                {
                    "source": "591",
                    "listing_type": listing_type,
                    "source_listing_id": f"{listing_type}-1",
                    "snapshot_at": batch.started_at,
                }
            ]
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
    tmp_path: Path,
    market_frame: pd.DataFrame,
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
                json={"types": ["sale", "newhouse"], "max_pages": 1},
                headers={"X-Qingpu-CSRF": "test-token"},
            )
            assert first.status_code == 202
            assert started.wait(5), "executor did not enter preparation"
            duplicate = client.post(
                "/api/admin/listing-updates",
                json={"types": ["sale", "newhouse"], "max_pages": 1},
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
            assert detail.json["summary"]["rows"] == 2
            assert preparation.calls == ["sale", "newhouse"]
    finally:
        release.set()
        app.extensions["qingpu_admin_shutdown"]()


# --- Model Admin API tests (M5 / Task 6) ---


class StubModelTrainingService:
    def __init__(self, job_service) -> None:
        self.job_service = job_service
        self.handoffs: list[str] = []
        self.requests: list = []
        self.should_stop = True

    def submit(self, request):
        self.requests.append(request)
        return self.job_service.create(
            "model_training",
            "model_training:active",
            "web",
        )

    def handoff(self, submission, request, executor):
        self.handoffs.append(submission.run.run_id)
        return executor.submit(submission.run.run_id, lambda: None)

    def request_stop(self, run_id: str) -> bool:
        return self.should_stop


class StubModelObservatory:
    def __init__(self, job_service) -> None:
        self.job_service = job_service
        self._runs: dict[str, dict] = {}
        self._report_paths: dict[str, Path] = {}

    def status(self) -> dict:
        return {"official_models": {}, "candidate_count": 0}

    def list_runs(self, limit: int = 20) -> list[dict]:
        jobs = self.job_service.list_recent(limit, job_type="model_training")
        results = []
        for run in jobs:
            entry = {
                "run_id": run.run_id,
                "status": run.status,
                "trigger": run.trigger,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            }
            if run.run_id in self._runs:
                entry["markets"] = self._runs[run.run_id].get("markets", [])
            results.append(entry)
        return results

    def get_run(self, run_id: str) -> dict | None:
        run = self.job_service.get(run_id)
        if run is None:
            return None
        result: dict = {
            "run_id": run.run_id,
            "status": run.status,
            "trigger": run.trigger,
        }
        if run.run_id in self._runs:
            result["manifest"] = dict(self._runs[run.run_id])
        return result

    def report_path(self, run_id: str, report_type: str) -> Path:
        from qingpu_insight.model_artifacts import REPORT_TYPES

        if report_type not in REPORT_TYPES:
            raise ValueError(f"Unknown report_type: {report_type}")
        path = Path.cwd() / "reports" / report_type
        if path.suffix == ".joblib":
            raise ValueError("joblib downloads are not permitted")
        return path


class StubOfficialDataService:
    def __init__(self, job_service) -> None:
        self.job_service = job_service
        self.handoffs: list[str] = []
        self.handoff_error: Exception | None = None

    def submit(self, request):
        return self.job_service.create(
            "official_data_update",
            "official_data_update:active",
            request.trigger,
        )

    def handoff(self, submission, request, executor):
        if self.handoff_error is not None:
            raise self.handoff_error
        self.handoffs.append(submission.run.run_id)
        return executor.submit(submission.run.run_id, lambda: None)


class StubDashboardService:
    def read(self) -> dict[str, object]:
        return {
            "mutation_ready": True,
            "readiness": [],
            "active_jobs": [],
            "recent_jobs": [],
            "health": None,
            "backup": None,
            "models": None,
            "action_items": [],
        }


@pytest.fixture
def model_admin_client(market_frame: pd.DataFrame) -> FlaskClient:
    from qingpu_insight.jobs import JobService
    from qingpu_insight.web import AdminServices, create_app

    repo = MemoryAdminJobRepository()
    job_service = JobService(repo)
    mts = StubModelTrainingService(job_service)
    obs = StubModelObservatory(job_service)
    app = create_app(
        data_source=InMemoryMarketDataSource(market_frame),
        admin_services=AdminServices(
            job_service=job_service,
            listing_update_service=StubListingUpdateService(job_service),
            executor=FakeAdminExecutor(),
            model_training_service=mts,
            model_observatory=obs,
        ),
    )
    from dataclasses import replace

    app.extensions["qingpu_admin_runtime"] = replace(
        app.extensions["qingpu_admin_runtime"],
        dashboard_service=StubDashboardService(),
    )
    app.extensions["test_model_training_service"] = mts
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "test-token"
        yield client


class TestModelAdminApi:
    @pytest.mark.parametrize(
        ("payload", "field"),
        [
            ({}, "markets"),
            ({"markets": []}, "markets"),
            ({"markets": ["resale", "resale"]}, "markets"),
            ({"markets": ["sale"]}, "markets"),
            ({"markets": ["resale"], "path": "C:/secret"}, "path"),
            ({"markets": ["resale"], "model": "xgboost"}, "model"),
        ],
    )
    def test_model_training_post_rejects_nonfixed_payload(
        self,
        model_admin_client: FlaskClient,
        payload: dict[str, object],
        field: str,
    ) -> None:
        response = model_admin_client.post(
            "/api/admin/model-training-runs",
            json=payload,
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["fields"][field]

    def test_model_admin_get_rejects_untrusted_host(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.get(
            "/api/admin/models/status",
            base_url="http://attacker.example",
        )
        assert response.status_code == 403

    def test_model_admin_training_runs_get_rejects_untrusted_host(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.get(
            "/api/admin/model-training-runs",
            base_url="http://attacker.example",
        )
        assert response.status_code == 403

    def test_model_admin_training_run_get_rejects_untrusted_host(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.get(
            "/api/admin/model-training-runs/00000000-0000-4000-8000-000000000000",
            base_url="http://attacker.example",
        )
        assert response.status_code == 403

    def test_model_admin_report_get_rejects_untrusted_host(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.get(
            "/api/admin/model-training-runs/00000000-0000-4000-8000-000000000000/reports/resale-evaluation",
            base_url="http://attacker.example",
        )
        assert response.status_code == 403

    def test_model_admin_post_rejects_missing_csrf(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.post(
            "/api/admin/model-training-runs",
            json={"markets": ["resale"]},
        )
        assert response.status_code == 403

    def test_model_admin_post_rejects_wrong_csrf(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.post(
            "/api/admin/model-training-runs",
            json={"markets": ["resale"]},
            headers={"X-Qingpu-CSRF": "wrong-token"},
        )
        assert response.status_code == 403

    def test_model_admin_post_submit_new_run_returns_202(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.post(
            "/api/admin/model-training-runs",
            json={"markets": ["resale"]},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 202
        body = response.get_json()
        assert body["created"] is True
        assert body["job_type"] == "model_training"
        assert body["status"] == "pending"

    def test_model_admin_post_repeat_while_active_returns_200(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        first = model_admin_client.post(
            "/api/admin/model-training-runs",
            json={"markets": ["resale"]},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        second = model_admin_client.post(
            "/api/admin/model-training-runs",
            json={"markets": ["resale"]},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert second.status_code == 200
        assert second.get_json()["created"] is False
        assert second.get_json()["run_id"] == first.get_json()["run_id"]

    def test_model_training_history_contains_only_model_training(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        model_admin_client.post(
            "/api/admin/model-training-runs",
            json={"markets": ["resale"]},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        model_admin_client.post(
            "/api/admin/model-training-runs",
            json={"markets": ["presale"]},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        response = model_admin_client.get("/api/admin/model-training-runs?limit=10")
        assert response.status_code == 200
        body = response.get_json()
        assert all(item["status"] == "pending" for item in body["items"])

    def test_model_training_run_detail_validates_uuid(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        bad = model_admin_client.get("/api/admin/model-training-runs/not-a-uuid")
        missing = model_admin_client.get(
            "/api/admin/model-training-runs/00000000-0000-4000-8000-000000000000"
        )
        assert bad.status_code == 400
        assert bad.get_json()["error"]["fields"] == {"run_id": "invalid_uuid"}
        assert missing.status_code == 404

    @pytest.mark.parametrize(
        "bad_type",
        [
            "resale.joblib",
            "unknown",
        ],
    )
    def test_model_admin_report_unknown_type_returns_400(
        self,
        model_admin_client: FlaskClient,
        bad_type: str,
    ) -> None:
        response = model_admin_client.get(
            "/api/admin/model-training-runs/00000000-0000-4000-8000-000000000000"
            f"/reports/{bad_type}"
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["fields"]["report_type"]

    def test_model_admin_status_returns_shape(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.get("/api/admin/models/status")
        assert response.status_code == 200
        body = response.get_json()
        assert "official_models" in body
        assert "candidate_count" in body

    def test_model_training_post_canonicalizes_markets(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.post(
            "/api/admin/model-training-runs",
            json={"markets": ["presale", "resale"]},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        data = response.get_json()
        assert data.get("created") is True

    def test_model_training_post_accepts_four_profile_plan(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.post(
            "/api/admin/model-training-runs",
            json={
                "markets": ["resale", "presale"],
                "tuning": {
                    "mode": "preset_comparison",
                    "include_custom": True,
                    "custom": {
                        "hgb_learning_rate": 0.05,
                        "hgb_max_iter": 420,
                        "rf_n_estimators": 520,
                        "recency_half_life_months": 36,
                    },
                },
            },
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 202
        service = model_admin_client.application.extensions["test_model_training_service"]
        assert [p.name for p in service.requests[-1].tuning_plan.profiles] == [
            "quick",
            "balanced",
            "thorough",
            "custom",
        ]

    def test_model_training_post_defaults_to_three_profiles(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.post(
            "/api/admin/model-training-runs",
            json={"markets": ["resale"]},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 202
        service = model_admin_client.application.extensions["test_model_training_service"]
        assert [p.name for p in service.requests[-1].tuning_plan.profiles] == [
            "quick",
            "balanced",
            "thorough",
        ]

    def test_model_training_post_rejects_invalid_tuning_field(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.post(
            "/api/admin/model-training-runs",
            json={
                "markets": ["resale"],
                "tuning": {"mode": "unknown_mode"},
            },
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["fields"]["tuning.mode"]

    def test_model_training_post_rejects_invalid_custom(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.post(
            "/api/admin/model-training-runs",
            json={
                "markets": ["resale"],
                "tuning": {
                    "mode": "preset_comparison",
                    "include_custom": True,
                    "custom": {
                        "hgb_learning_rate": 999,
                        "hgb_max_iter": 420,
                        "rf_n_estimators": 520,
                        "recency_half_life_months": 36,
                    },
                },
            },
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["fields"]["tuning.custom.hgb_learning_rate"]

    def test_model_admin_report_download_success(
        self,
        monkeypatch,
        model_admin_client: FlaskClient,
        tmp_path: Path,
    ) -> None:
        obs = model_admin_client.application.extensions["qingpu_admin_services"].model_observatory
        report_type = "presale-evaluation"
        report_dir = tmp_path / "reports"
        report_dir.mkdir(parents=True)
        report_file = report_dir / report_type
        report_file.write_text("dummy report content")

        monkeypatch.setattr(obs, "report_path", lambda run_id, rt: report_file)

        response = model_admin_client.get(
            "/api/admin/model-training-runs/00000000-0000-4000-8000-000000000000"
            f"/reports/{report_type}"
        )
        assert response.status_code == 200
        assert response.data.decode() == "dummy report content"

    # ------------------------------------------------------------------
    # Stop endpoint tests
    # ------------------------------------------------------------------

    def test_model_training_stop_rejects_untrusted_host(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.post(
            "/api/admin/model-training-runs/00000000-0000-4000-8000-000000000000/stop",
            base_url="http://attacker.example",
        )
        assert response.status_code == 403

    def test_model_training_stop_rejects_missing_csrf(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.post(
            "/api/admin/model-training-runs/00000000-0000-4000-8000-000000000000/stop",
        )
        assert response.status_code == 403

    def test_model_training_stop_rejects_invalid_uuid(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.post(
            "/api/admin/model-training-runs/not-a-uuid/stop",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["fields"]["run_id"] == "invalid_uuid"

    def test_model_training_stop_returns_404_for_unknown_run(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        response = model_admin_client.post(
            "/api/admin/model-training-runs/00000000-0000-4000-8000-000000000000/stop",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 404

    def test_model_training_stop_returns_404_for_wrong_job_type(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        js = model_admin_client.application.extensions["qingpu_admin_services"].job_service
        bad_run = js.create("listing_update", "lu:active", "web")
        bad_id = bad_run.run.run_id
        response = model_admin_client.post(
            f"/api/admin/model-training-runs/{bad_id}/stop",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 404

    def test_model_training_stop_returns_409_for_terminal_status(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        js = model_admin_client.application.extensions["qingpu_admin_services"].job_service
        submit_resp = model_admin_client.post(
            "/api/admin/model-training-runs",
            json={"markets": ["resale"]},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        run_id = submit_resp.get_json()["run_id"]
        js.start(run_id)
        js.succeed(run_id, "v1", {"done": True})

        response = model_admin_client.post(
            f"/api/admin/model-training-runs/{run_id}/stop",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "not_stoppable"

    def test_model_training_stop_returns_409_for_guided_run(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        mts = model_admin_client.application.extensions["test_model_training_service"]
        mts.should_stop = False
        submit_resp = model_admin_client.post(
            "/api/admin/model-training-runs",
            json={"markets": ["resale"]},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        run_id = submit_resp.get_json()["run_id"]
        response = model_admin_client.post(
            f"/api/admin/model-training-runs/{run_id}/stop",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "not_stoppable"
        mts.should_stop = True

    def test_model_training_stop_returns_202(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        mts = model_admin_client.application.extensions["test_model_training_service"]
        mts.should_stop = True
        submit_resp = model_admin_client.post(
            "/api/admin/model-training-runs",
            json={"markets": ["resale"]},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        run_id = submit_resp.get_json()["run_id"]
        response = model_admin_client.post(
            f"/api/admin/model-training-runs/{run_id}/stop",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 202
        body = response.get_json()
        assert body["run_id"] == run_id
        assert body["stop_requested"] is True

    def test_model_training_stop_repeated_request_returns_202(
        self,
        model_admin_client: FlaskClient,
    ) -> None:
        mts = model_admin_client.application.extensions["test_model_training_service"]
        mts.should_stop = True
        submit_resp = model_admin_client.post(
            "/api/admin/model-training-runs",
            json={"markets": ["resale"]},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        run_id = submit_resp.get_json()["run_id"]
        first = model_admin_client.post(
            f"/api/admin/model-training-runs/{run_id}/stop",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        second = model_admin_client.post(
            f"/api/admin/model-training-runs/{run_id}/stop",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert first.status_code == 202
        assert second.status_code == 202
        assert second.get_json()["stop_requested"] is True


class TestModelAdminPage:
    def test_model_admin_page_untrusted_host(self, model_admin_client) -> None:
        response = model_admin_client.get(
            "/admin/models",
            base_url="http://attacker.example",
        )
        assert response.status_code == 403

    def test_model_admin_page_untrusted_remote(self, model_admin_client) -> None:
        response = model_admin_client.get(
            "/admin/models",
            environ_base={"REMOTE_ADDR": "10.0.0.2"},
        )
        assert response.status_code == 403

    def test_model_admin_page_contract(self, model_admin_client) -> None:
        response = model_admin_client.get("/admin/models")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/admin#models")

        response = model_admin_client.get("/admin")
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert 'name="csrf-token"' in html
        assert 'id="mr-official-cards"' in html
        assert 'id="ma-data-grid"' in html
        assert 'id="ma-market-select"' in html
        assert 'value="resale"' in html
        assert 'value="presale"' in html
        assert 'value="all"' in html
        assert "不會自動發布" in html
        assert 'id="ma-history-table"' in html
        assert 'id="ma-detail-content"' in html
        assert "job_polling.js" in html
        assert "models_admin.js" in html
        assert "查看詳細數據" in html
        assert "平均每坪估錯多少元" in html
        assert "重要特徵（前五項）" in html
        assert "renderTrainingDetail(detailRun, true)" in html
        assert 'var detailSection = document.getElementById("ma-detail-content")' in html
        js_pos = html.index("job_polling.js")
        ma_pos = html.index("models_admin.js")
        assert js_pos < ma_pos, "job_polling.js must load before models_admin.js"
        assert 'id="ma-active-status"' in html
        assert 'id="ma-submit-btn"' in html

        page = BeautifulSoup(html, "html.parser")
        assert page.select_one("#ma-profile-quick").get("data-locked") == "true"
        assert page.select_one("#ma-profile-balanced").get("data-locked") == "true"
        assert page.select_one("#ma-profile-thorough").get("data-locked") == "true"
        assert page.select_one("#ma-custom-enabled") is not None
        assert page.select_one("#ma-custom-hgb-learning-rate") is not None
        assert page.select_one("#ma-custom-hgb-max-iter") is not None
        assert page.select_one("#ma-custom-rf-n-estimators") is not None
        assert page.select_one("#ma-custom-half-life") is not None
        assert page.select_one('link[href*="model_training.css"]') is not None

    def test_model_admin_page_no_freeform_inputs(self, model_admin_client) -> None:
        response = model_admin_client.get("/admin")
        html = response.get_data(as_text=True)
        forbidden = (
            'name="path"',
            'name="command"',
            'name="estimator"',
            'name="hyperparameter"',
            'name="publish"',
            'name="rollback"',
        )
        for token in forbidden:
            assert token not in html, f"unexpected freeform input: {token}"

    def test_model_admin_page_permanent_notice(self, model_admin_client) -> None:
        response = model_admin_client.get("/admin")
        html = response.get_data(as_text=True)
        assert "不會自動發布" in html


def test_admin_page_is_local_only(model_admin_client):
    assert (
        model_admin_client.get("/admin", environ_base={"REMOTE_ADDR": "10.0.0.2"}).status_code
        == 403
    )


def test_admin_listing_controls_exclude_rental(
    model_admin_client: FlaskClient,
) -> None:
    html = model_admin_client.get("/admin").get_data(as_text=True)
    page = BeautifulSoup(html, "html.parser")
    types = [item.get("data-type") for item in page.select(".listing-status-item")]
    assert types == ["sale", "newhouse"]
    assert page.select_one("#ls-status-rental") is None
    assert page.select_one("#ls-run-all-btn").get_text(strip=True) == "更新出售與新建案"


def test_admin_page_has_seven_classified_sections(model_admin_client):

    response = model_admin_client.get("/admin")
    soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")
    assert response.status_code == 200
    assert [node["id"] for node in soup.select("main > section[id]")] == [
        "admin-overview",
        "admin-data",
        "admin-listings",
        "admin-models",
        "admin-llm",
        "admin-jobs",
        "admin-diagnostics",
    ]
    assert soup.select_one('meta[name="csrf-token"]')["content"]


def test_admin_overview_returns_readiness(model_admin_client):
    response = model_admin_client.get("/api/admin/overview")
    assert response.status_code == 200
    assert response.get_json()["mutation_ready"] is True


def test_admin_overview_never_leaks_probe_exception(model_admin_client, monkeypatch):
    monkeypatch.setattr(
        model_admin_client.application.extensions["qingpu_admin_runtime"].dashboard_service,
        "read",
        lambda: (_ for _ in ()).throw(RuntimeError("mysql://user:secret@localhost/db")),
    )
    response = model_admin_client.get("/api/admin/overview")
    assert response.status_code == 503
    assert "secret" not in response.get_data(as_text=True)


def test_admin_jobs_returns_job_history(admin_app, admin_client):
    app, repo, _, _ = admin_app
    from datetime import UTC, datetime

    from qingpu_insight.jobs import JobRun

    now = datetime.now(UTC)
    run1 = JobRun(
        run_id="11111111-1111-4111-8111-111111111111",
        job_type="listing_update",
        trigger="manual",
        idempotency_key="k1",
        status="pending",
        started_at=None,
        finished_at=None,
        attempt=1,
        input_version=None,
        output_version=None,
        summary={},
        error_code=None,
        error_message=None,
    )
    run2 = JobRun(
        run_id="22222222-2222-4222-8222-222222222222",
        job_type="listing_update",
        trigger="manual",
        idempotency_key="k2",
        status="succeeded",
        started_at=now,
        finished_at=now,
        attempt=1,
        input_version=None,
        output_version="v1",
        summary={},
        error_code=None,
        error_message=None,
    )
    run3 = JobRun(
        run_id="33333333-3333-4333-8333-333333333333",
        job_type="model_training",
        trigger="manual",
        idempotency_key="k3",
        status="failed",
        started_at=now,
        finished_at=now,
        attempt=1,
        input_version=None,
        output_version=None,
        summary={},
        error_code="worker_interrupted",
        error_message="worker interrupted",
    )
    repo._runs[run1.run_id] = run1
    repo._runs[run2.run_id] = run2
    repo._runs[run3.run_id] = run3

    response = admin_client.get("/api/admin/jobs?limit=10")
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["items"]) == 3
    assert data["limit"] == 10

    assert data["items"][0]["run_id"] == run3.run_id
    assert data["items"][0]["display_status"] == "interrupted"
    assert data["items"][0]["info_url"] == f"/api/jobs/{run3.run_id}"

    assert data["items"][1]["run_id"] == run2.run_id
    assert data["items"][1]["display_status"] == "succeeded"
    assert data["items"][1]["info_url"] == f"/api/jobs/{run2.run_id}"

    assert data["items"][2]["run_id"] == run1.run_id
    assert data["items"][2]["display_status"] == "queued"
    assert data["items"][2]["info_url"] == f"/api/jobs/{run1.run_id}"


def test_admin_jobs_filter_by_job_type(admin_app, admin_client):
    app, repo, _, _ = admin_app
    from datetime import UTC, datetime

    from qingpu_insight.jobs import JobRun

    now = datetime.now(UTC)
    run = JobRun(
        run_id="44444444-4444-4444-8444-444444444444",
        job_type="listing_update",
        trigger="manual",
        idempotency_key="k4",
        status="succeeded",
        started_at=now,
        finished_at=now,
        attempt=1,
        input_version=None,
        output_version="v1",
        summary={},
        error_code=None,
        error_message=None,
    )
    repo._runs[run.run_id] = run

    response = admin_client.get("/api/admin/jobs?limit=10&job_type=listing_update")
    assert response.status_code == 200
    data = response.get_json()
    assert len(data["items"]) == 1

    response = admin_client.get("/api/admin/jobs?limit=10&job_type=unknown_type")
    assert response.status_code == 400
    assert response.get_json()["error"]["fields"]["job_type"] == "unsupported"


def test_admin_jobs_rejects_invalid_limit(admin_app, admin_client):
    response = admin_client.get("/api/admin/jobs?limit=0")
    assert response.status_code == 400
    assert response.get_json()["error"]["fields"]["limit"] == "integer_1_to_100"

    response = admin_client.get("/api/admin/jobs?limit=abc")
    assert response.status_code == 400
    assert response.get_json()["error"]["fields"]["limit"] == "integer_1_to_100"


# ------------------------------------------------------------------
# Official Data Update tests
# ------------------------------------------------------------------


def csrf_headers(client):
    return {"X-Qingpu-CSRF": "test-token"}


def post_official_update(client):
    return client.post(
        "/api/admin/official-data-updates",
        json={"start_season": "110S3", "end_season": "115S2"},
        headers=csrf_headers(client),
    )


def test_official_update_rejects_paths_and_unknown_fields(admin_client):
    response = admin_client.post(
        "/api/admin/official-data-updates",
        json={"start_season": "110S3", "end_season": "115S2", "input": "C:/secret"},
        headers=csrf_headers(admin_client),
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["fields"]["input"] == "not_allowed"


def test_official_update_rejects_unrecognized_checkpoint(admin_client):
    response = admin_client.post(
        "/api/admin/official-data-updates",
        json={
            "start_season": "110S3",
            "end_season": "115S2",
            "start_at": "C:/processed/transactions.parquet",
        },
        headers=csrf_headers(admin_client),
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["fields"]["start_at"] == "unsupported"


def test_official_update_returns_existing_active_job(admin_client):
    first = post_official_update(admin_client)
    second = post_official_update(admin_client)
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.get_json()["run_id"] == first.get_json()["run_id"]


def test_official_update_rejects_when_mutation_not_ready(admin_client, monkeypatch):
    monkeypatch.setattr(
        admin_client.application.extensions["qingpu_admin_runtime"].dashboard_service,
        "read",
        lambda: {"mutation_ready": False},
    )
    response = admin_client.post(
        "/api/admin/official-data-updates",
        json={"start_season": "110S3", "end_season": "115S2"},
        headers=csrf_headers(admin_client),
    )
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "mutation_not_ready"


def test_official_data_quality_validates_uuid_format(admin_client):
    response = admin_client.get("/api/admin/official-data-updates/not-a-uuid/reports/quality")
    assert response.status_code == 400
    assert response.get_json()["error"]["fields"]["run_id"] == "invalid_uuid"


def test_official_data_quality_returns_404_for_nonexistent_job(admin_client):
    response = admin_client.get(
        "/api/admin/official-data-updates/00000000-0000-4000-8000-000000000000/reports/quality"
    )
    assert response.status_code == 404


def test_official_data_quality_returns_404_for_non_succeeded_job(
    admin_app,
    admin_client,
) -> None:
    _, repo, _, _ = admin_app
    from qingpu_insight.jobs import JobService

    service = JobService(repo)
    submission = service.create("official_data_update", "pending-job", "manual")
    response = admin_client.get(
        f"/api/admin/official-data-updates/{submission.run.run_id}/reports/quality"
    )
    assert response.status_code == 404


def test_official_data_quality_returns_404_for_wrong_job_type(
    admin_app,
    admin_client,
) -> None:
    _, repo, _, _ = admin_app
    from qingpu_insight.jobs import JobService

    service = JobService(repo)
    submission = service.create("listing_update", "wrong-type", "manual")
    service.start(submission.run.run_id)
    service.succeed(submission.run.run_id, "v1", {"rows": 5})
    response = admin_client.get(
        f"/api/admin/official-data-updates/{submission.run.run_id}/reports/quality"
    )
    assert response.status_code == 404


def test_official_data_quality_happy_path(market_frame, tmp_path) -> None:
    from qingpu_insight.jobs import JobService
    from qingpu_insight.web import AdminServices, create_app

    repo = MemoryAdminJobRepository()
    job_service = JobService(repo)
    executor = FakeAdminExecutor()
    listing_service = StubListingUpdateService(job_service)
    official_ds = StubOfficialDataService(job_service)

    app = create_app(
        root=tmp_path,
        data_source=InMemoryMarketDataSource(market_frame),
        admin_services=AdminServices(
            job_service,
            listing_service,
            executor,
            official_data_service=official_ds,
        ),
    )

    submission = job_service.create("official_data_update", "quality-happy", "manual")
    run = submission.run
    job_service.start(run.run_id)
    job_service.succeed(run.run_id, "v1", {"rows": 100})

    quality_dir = tmp_path / "outputs" / "admin" / "official-data" / run.run_id
    quality_dir.mkdir(parents=True)
    quality_path = quality_dir / "quality.json"
    expected_data = {"quality_score": 0.95, "row_count": 100}
    quality_path.write_text(json.dumps(expected_data), encoding="utf-8")

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "test-token"
        response = client.get(
            f"/api/admin/official-data-updates/{run.run_id}/reports/quality",
            headers={"X-Qingpu-CSRF": "test-token"},
        )

    assert response.status_code == 200
    assert response.get_json() == expected_data


# ------------------------------------------------------------------
# Model Release API tests (Task 11)
# ------------------------------------------------------------------


class StubModelReleaseService:
    def __init__(self, job_service) -> None:
        self.job_service = job_service
        self._previews: dict[str, Any] = {}
        self._submissions: list[Any] = []

    def preview_publish(self, run_id: str, market: str) -> Any:
        from datetime import UTC, datetime, timedelta

        from qingpu_insight.operation_previews import OperationPreview

        preview_id = f"preview-{uuid.uuid4().hex[:8]}"
        preview = OperationPreview(
            preview_id=preview_id,
            operation="model_publish",
            payload={"operation": "publish", "market": market, "run_id": run_id},
            confirmation_text=f"發布 {market} {run_id[:8]}",
            expires_at=datetime.now(UTC) + timedelta(seconds=300),
            consumed_at=None,
        )
        self._previews[preview_id] = preview
        return preview

    def preview_rollback(self, market: str, version_id: str) -> Any:
        from datetime import UTC, datetime, timedelta

        from qingpu_insight.operation_previews import OperationPreview

        preview_id = f"preview-{uuid.uuid4().hex[:8]}"
        preview = OperationPreview(
            preview_id=preview_id,
            operation="model_rollback",
            payload={"operation": "rollback", "market": market, "version_id": version_id},
            confirmation_text=f"回滾 {market} {version_id}",
            expires_at=datetime.now(UTC) + timedelta(seconds=300),
            consumed_at=None,
        )
        self._previews[preview_id] = preview
        return preview

    def submit(self, preview_id: str, confirmation_text: str) -> Any:

        preview = self._previews.get(preview_id)
        if preview is None:
            raise ValueError(f"preview {preview_id!r} not found")
        if preview.confirmation_text != confirmation_text:
            raise ValueError("confirmation text mismatch")
        if preview.consumed_at is not None:
            raise ValueError("preview already consumed")

        market = preview.payload["market"]
        submission = self.job_service.create(
            "model_release",
            f"model_release:{market}:active",
            "manual",
        )
        self._submissions.append(submission)
        return submission


@pytest.fixture
def model_release_client(market_frame: pd.DataFrame) -> FlaskClient:
    from qingpu_insight.jobs import JobService
    from qingpu_insight.web import AdminServices, create_app

    repo = MemoryAdminJobRepository()
    job_service = JobService(repo)
    mts = StubModelTrainingService(job_service)
    obs = StubModelObservatory(job_service)
    mrs = StubModelReleaseService(job_service)
    app = create_app(
        data_source=InMemoryMarketDataSource(market_frame),
        admin_services=AdminServices(
            job_service=job_service,
            listing_update_service=StubListingUpdateService(job_service),
            executor=FakeAdminExecutor(),
            model_training_service=mts,
            model_observatory=obs,
            model_release_service=mrs,
        ),
    )
    from dataclasses import replace

    app.extensions["qingpu_admin_runtime"] = replace(
        app.extensions["qingpu_admin_runtime"],
        dashboard_service=StubDashboardService(),
        model_release_service=mrs,
    )
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "test-token"
        yield client


class TestModelReleaseApi:
    def test_preview_publish_success(self, model_release_client) -> None:
        response = model_release_client.post(
            "/api/admin/model-release-previews",
            json={
                "action": "publish",
                "market": "resale",
                "run_id": "00000000-0000-4000-8000-000000000000",
            },
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert "preview_id" in body
        assert body["operation"] == "model_publish"
        assert "confirmation_text" in body
        assert "expires_at" in body

    def test_preview_rollback_success(self, model_release_client) -> None:
        response = model_release_client.post(
            "/api/admin/model-release-previews",
            json={"action": "rollback", "market": "presale", "version_id": "abc12345"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["operation"] == "model_rollback"
        assert "preview_id" in body

    def test_preview_rejects_non_json(self, model_release_client) -> None:
        response = model_release_client.post(
            "/api/admin/model-release-previews",
            data="not json",
            content_type="text/plain",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400

    def test_preview_rejects_missing_action(self, model_release_client) -> None:
        response = model_release_client.post(
            "/api/admin/model-release-previews",
            json={"market": "resale", "run_id": "run-123"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        fields = response.get_json()["error"]["fields"]
        assert "action" in fields

    def test_preview_rejects_bad_action(self, model_release_client) -> None:
        response = model_release_client.post(
            "/api/admin/model-release-previews",
            json={"action": "sync", "market": "resale", "run_id": "run-123"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400

    def test_preview_rejects_extra_fields(self, model_release_client) -> None:
        response = model_release_client.post(
            "/api/admin/model-release-previews",
            json={
                "action": "publish",
                "market": "resale",
                "run_id": "run-123",
                "estimator": "xgboost",
            },
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        fields = response.get_json()["error"]["fields"]
        assert "estimator" in fields

    def test_preview_rejects_missing_version_id_for_rollback(self, model_release_client) -> None:
        response = model_release_client.post(
            "/api/admin/model-release-previews",
            json={"action": "rollback", "market": "resale"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        fields = response.get_json()["error"]["fields"]
        assert "version_id" in fields

    def test_preview_rejects_missing_csrf(self, model_release_client) -> None:
        response = model_release_client.post(
            "/api/admin/model-release-previews",
            json={"action": "publish", "market": "resale", "run_id": "run-123"},
        )
        assert response.status_code == 403

    def test_preview_rejects_wrong_csrf(self, model_release_client) -> None:
        response = model_release_client.post(
            "/api/admin/model-release-previews",
            json={"action": "publish", "market": "resale", "run_id": "run-123"},
            headers={"X-Qingpu-CSRF": "wrong-token"},
        )
        assert response.status_code == 403

    def test_release_submit_success(self, model_release_client) -> None:
        preview = model_release_client.post(
            "/api/admin/model-release-previews",
            json={
                "action": "publish",
                "market": "resale",
                "run_id": "00000000-0000-4000-8000-000000000000",
            },
            headers={"X-Qingpu-CSRF": "test-token"},
        ).get_json()

        response = model_release_client.post(
            "/api/admin/model-releases",
            json={
                "preview_id": preview["preview_id"],
                "confirmation_text": preview["confirmation_text"],
            },
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 202
        body = response.get_json()
        assert body["job_type"] == "model_release"
        assert body["status"] == "pending"

    def test_release_submit_rejects_missing_preview_id(self, model_release_client) -> None:
        response = model_release_client.post(
            "/api/admin/model-releases",
            json={"confirmation_text": "發布 resale run-123"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        fields = response.get_json()["error"]["fields"]
        assert "preview_id" in fields

    def test_release_submit_rejects_missing_confirmation(self, model_release_client) -> None:
        response = model_release_client.post(
            "/api/admin/model-releases",
            json={"preview_id": "pv-123"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400

    def test_release_submit_rejects_csrf(self, model_release_client) -> None:
        response = model_release_client.post(
            "/api/admin/model-releases",
            json={"preview_id": "pv-123", "confirmation_text": "確認"},
        )
        assert response.status_code == 403

    def test_release_list_returns_items(self, model_release_client) -> None:
        response = model_release_client.get(
            "/api/admin/model-releases",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert "items" in body
        assert "limit" in body

    def test_release_list_with_limit(self, model_release_client) -> None:
        response = model_release_client.get(
            "/api/admin/model-releases?limit=5",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 200
        assert response.get_json()["limit"] == 5

    def test_release_list_rejects_invalid_limit(self, model_release_client) -> None:
        response = model_release_client.get(
            "/api/admin/model-releases?limit=0",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400

    def test_release_submit_rejects_extra_fields(self, model_release_client) -> None:
        response = model_release_client.post(
            "/api/admin/model-releases",
            json={"preview_id": "pv-123", "confirmation_text": "確認", "force": True},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        fields = response.get_json()["error"]["fields"]
        assert "force" in fields

    def test_model_admin_contract_in_node(self) -> None:
        result = subprocess.run(
            ["node", "tests/js/model_admin_contract.cjs"],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_admin_contract_in_node(self) -> None:
        result = subprocess.run(
            ["node", "tests/js/admin_contract.cjs"],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr


# ------------------------------------------------------------------
# Backup Job API tests (Task 12)
# ------------------------------------------------------------------


class StubBackupJobService:
    def __init__(self, job_service) -> None:
        self.job_service = job_service
        self.handoffs: list[tuple[str, str | None]] = []
        self.create_result: object | None = None
        self.restore_drill_result: object | None = None
        self.restore_drill_error: Exception | None = None

    def submit_create(self):
        return self.job_service.create("backup_create", "backup_create:active", "manual")

    def submit_restore_drill(self, backup_id: str):
        return self.job_service.create("restore_drill", f"restore_drill:{backup_id}", "manual")

    def execute_create(self, run_id: str):
        self.handoffs.append(("create", run_id))
        return self.create_result

    def execute_restore_drill(self, run_id: str, backup_id: str):
        self.handoffs.append(("restore_drill", run_id))
        if self.restore_drill_error:
            raise self.restore_drill_error
        return self.restore_drill_result


@pytest.fixture
def backup_admin_app(market_frame: pd.DataFrame):
    from qingpu_insight.jobs import JobService
    from qingpu_insight.web import AdminServices, create_app

    repo = MemoryAdminJobRepository()
    job_service = JobService(repo)
    listing_service = StubListingUpdateService(job_service)
    executor = FakeAdminExecutor()
    bjs = StubBackupJobService(job_service)
    app = create_app(
        data_source=InMemoryMarketDataSource(market_frame),
        admin_services=AdminServices(
            job_service=job_service,
            listing_update_service=listing_service,
            executor=executor,
            backup_job_service=bjs,
        ),
    )
    from dataclasses import replace

    app.extensions["qingpu_admin_runtime"] = replace(
        app.extensions["qingpu_admin_runtime"],
        dashboard_service=StubDashboardService(),
        backup_service=bjs,
    )
    return app, repo, bjs, executor


@pytest.fixture
def backup_admin_client(backup_admin_app) -> FlaskClient:
    app, _, _, _ = backup_admin_app
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "test-token"
        yield client


class TestBackupAdminApi:
    def test_backup_create_submit_returns_202(self, backup_admin_client) -> None:
        response = backup_admin_client.post(
            "/api/admin/backups",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 202
        body = response.get_json()
        assert body["job_type"] == "backup_create"
        assert body["status"] == "pending"
        assert body["created"] is True

    def test_backup_create_duplicate_returns_existing(
        self,
        backup_admin_client,
    ) -> None:
        first = backup_admin_client.post(
            "/api/admin/backups",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        second = backup_admin_client.post(
            "/api/admin/backups",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert second.status_code == 200
        assert second.get_json()["run_id"] == first.get_json()["run_id"]
        assert second.get_json()["created"] is False

    def test_backup_create_rejects_missing_csrf(self, backup_admin_client) -> None:
        response = backup_admin_client.post("/api/admin/backups")
        assert response.status_code == 403

    def test_backup_create_rejects_non_loopback(
        self,
        backup_admin_client,
    ) -> None:
        response = backup_admin_client.post(
            "/api/admin/backups",
            environ_base={"REMOTE_ADDR": "10.0.0.2"},
        )
        assert response.status_code == 403

    def test_restore_drill_submit_returns_202(self, backup_admin_client) -> None:
        response = backup_admin_client.post(
            "/api/admin/backups/00000000-0000-4000-8000-000000000000/restore-drills",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 202
        body = response.get_json()
        assert body["job_type"] == "restore_drill"
        assert body["status"] == "pending"

    def test_restore_drill_duplicate_returns_existing(
        self,
        backup_admin_client,
    ) -> None:
        backup_id = "00000000-0000-4000-8000-000000000000"
        first = backup_admin_client.post(
            f"/api/admin/backups/{backup_id}/restore-drills",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        second = backup_admin_client.post(
            f"/api/admin/backups/{backup_id}/restore-drills",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert second.status_code == 200
        assert second.get_json()["run_id"] == first.get_json()["run_id"]

    def test_restore_drill_rejects_missing_csrf(
        self,
        backup_admin_client,
    ) -> None:
        response = backup_admin_client.post(
            "/api/admin/backups/00000000-0000-4000-8000-000000000000/restore-drills",
        )
        assert response.status_code == 403

    def test_backup_admin_unavailable_without_service(self, client) -> None:
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "test-token"
        response = client.post(
            "/api/admin/backups",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 503

    def test_ops_backups_still_lists(self, ops_app) -> None:
        response = ops_app.get("/api/ops/backups?limit=10")
        assert response.status_code == 200
        assert "items" in response.get_json()


# ------------------------------------------------------------------
# Production Restore API tests (Task 13)
# ------------------------------------------------------------------


class StubProductionRestoreService:
    def __init__(self, job_service) -> None:
        self.job_service = job_service
        self._previews: dict[str, object] = {}

    def preview(self, backup_id: str) -> object:
        from datetime import UTC, datetime, timedelta

        from qingpu_insight.operation_previews import OperationPreview

        preview_id = f"restore-preview-{uuid.uuid4().hex[:8]}"
        preview = OperationPreview(
            preview_id=preview_id,
            operation="database_restore",
            payload={"backup_id": backup_id},
            confirmation_text=f"還原資料庫 {backup_id[:8]}",
            expires_at=datetime.now(UTC) + timedelta(seconds=300),
            consumed_at=None,
        )
        self._previews[preview_id] = preview
        return preview

    def submit(self, preview_id: str, confirmation_text: str) -> object:
        preview = self._previews.get(preview_id)
        if preview is None:
            raise ValueError(f"preview {preview_id!r} not found")
        if preview.confirmation_text != confirmation_text:
            raise ValueError("confirmation text mismatch")
        return self.job_service.create(
            "database_restore",
            f"database_restore:{preview.payload['backup_id']}",
            "manual",
        )


@pytest.fixture
def restore_app(market_frame: pd.DataFrame):
    from qingpu_insight.jobs import JobService
    from qingpu_insight.web import AdminServices, create_app

    repo = MemoryAdminJobRepository()
    job_service = JobService(repo)
    listing_service = StubListingUpdateService(job_service)
    executor = FakeAdminExecutor()
    rrs = StubProductionRestoreService(job_service)
    app = create_app(
        data_source=InMemoryMarketDataSource(market_frame),
        admin_services=AdminServices(
            job_service=job_service,
            listing_update_service=listing_service,
            executor=executor,
        ),
    )
    from dataclasses import replace

    app.extensions["qingpu_admin_runtime"] = replace(
        app.extensions["qingpu_admin_runtime"],
        dashboard_service=StubDashboardService(),
        restore_service=rrs,
    )
    return app, repo, rrs, executor


@pytest.fixture
def restore_client(restore_app) -> FlaskClient:
    app, _, _, _ = restore_app
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "test-token"
        yield client


class TestProductionRestoreApi:
    def test_restore_preview_success(self, restore_client) -> None:
        response = restore_client.post(
            "/api/ops/restore-previews",
            json={"backup_id": "test-backup-id"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert "preview_id" in body
        assert "confirmation_text" in body
        assert body["backup_id"] == "test-backup-id"
        assert "還原資料庫" in body["confirmation_text"]

    def test_restore_preview_rejects_missing_backup_id(self, restore_client) -> None:
        response = restore_client.post(
            "/api/ops/restore-previews",
            json={},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        fields = response.get_json()["error"]["fields"]
        assert "backup_id" in fields

    def test_restore_preview_rejects_extra_fields(self, restore_client) -> None:
        response = restore_client.post(
            "/api/ops/restore-previews",
            json={"backup_id": "b1", "force": True},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        fields = response.get_json()["error"]["fields"]
        assert "force" in fields

    def test_restore_preview_rejects_missing_csrf(self, restore_client) -> None:
        response = restore_client.post(
            "/api/ops/restore-previews",
            json={"backup_id": "b1"},
        )
        assert response.status_code == 403

    def test_restore_submit_success(self, restore_client) -> None:
        preview = restore_client.post(
            "/api/ops/restore-previews",
            json={"backup_id": "test-backup-id"},
            headers={"X-Qingpu-CSRF": "test-token"},
        ).get_json()

        response = restore_client.post(
            "/api/ops/restores",
            json={
                "preview_id": preview["preview_id"],
                "confirmation_text": preview["confirmation_text"],
            },
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 202
        body = response.get_json()
        assert body["job_type"] == "database_restore"
        assert body["status"] == "pending"
        assert body["created"] is True

    def test_restore_submit_rejects_missing_preview_id(self, restore_client) -> None:
        response = restore_client.post(
            "/api/ops/restores",
            json={"confirmation_text": "還原資料庫 abc"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        fields = response.get_json()["error"]["fields"]
        assert "preview_id" in fields

    def test_restore_submit_rejects_missing_confirmation(self, restore_client) -> None:
        response = restore_client.post(
            "/api/ops/restores",
            json={"preview_id": "pv-123"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        fields = response.get_json()["error"]["fields"]
        assert "confirmation_text" in fields

    def test_restore_submit_rejects_wrong_confirmation(self, restore_client) -> None:
        preview = restore_client.post(
            "/api/ops/restore-previews",
            json={"backup_id": "test-backup-id"},
            headers={"X-Qingpu-CSRF": "test-token"},
        ).get_json()

        response = restore_client.post(
            "/api/ops/restores",
            json={
                "preview_id": preview["preview_id"],
                "confirmation_text": "wrong text",
            },
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400

    def test_restore_submit_rejects_extra_fields(self, restore_client) -> None:
        response = restore_client.post(
            "/api/ops/restores",
            json={"preview_id": "pv-123", "confirmation_text": "x", "force": True},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        fields = response.get_json()["error"]["fields"]
        assert "force" in fields

    def test_restore_submit_rejects_csrf(self, restore_client) -> None:
        response = restore_client.post(
            "/api/ops/restores",
            json={"preview_id": "pv-123", "confirmation_text": "x"},
        )
        assert response.status_code == 403

    def test_restore_preview_rejects_non_json(self, restore_client) -> None:
        response = restore_client.post(
            "/api/ops/restore-previews",
            data="not json",
            content_type="text/plain",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400

    def test_restore_submit_rejects_non_json(self, restore_client) -> None:
        response = restore_client.post(
            "/api/ops/restores",
            data="not json",
            content_type="text/plain",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400

    def test_restore_preview_unavailable_without_service(self, admin_app, admin_client) -> None:
        app, _, _, _ = admin_app
        from dataclasses import replace

        app.extensions["qingpu_admin_runtime"] = replace(
            app.extensions["qingpu_admin_runtime"],
            restore_service=None,
        )
        response = admin_client.post(
            "/api/ops/restore-previews",
            json={"backup_id": "b1"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 503

    def test_restore_submit_unavailable_without_service(self, admin_app, admin_client) -> None:
        app, _, _, _ = admin_app
        from dataclasses import replace

        app.extensions["qingpu_admin_runtime"] = replace(
            app.extensions["qingpu_admin_runtime"],
            restore_service=None,
        )
        response = admin_client.post(
            "/api/ops/restores",
            json={"preview_id": "pv-123", "confirmation_text": "x"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 503

    # ------------------------------------------------------------------
    # Provider API tests (Task 14)
    # ------------------------------------------------------------------

    def test_providers_get_status(self, admin_app, admin_client) -> None:
        app, _, _, _ = admin_app
        from dataclasses import replace

        from qingpu_insight.provider_ops import ProviderOpsService

        class _Rule:
            def generate(self, pack, repair_codes=()):
                return type("R", (), {"provider": "rule", "model": "rule"})()

        svc = ProviderOpsService(
            rule_provider=_Rule(),
            provider_factory=lambda n: None,
            env={"QINGPU_OLLAMA_MODEL": "test"},
        )
        store = _SecretsStore()
        app.extensions["qingpu_admin_runtime"] = replace(
            app.extensions["qingpu_admin_runtime"],
            provider_ops_service=svc,
            secrets_store=store,
        )
        response = admin_client.get("/api/admin/providers")
        assert response.status_code == 200
        data = response.json
        assert "providers" in data
        names = [p["name"] for p in data["providers"]]
        assert "rule" in names
        assert "ollama" in names
        assert "gemini" in names

    def test_providers_unavailable_without_service(self, admin_app, admin_client) -> None:
        app, _, _, _ = admin_app
        from dataclasses import replace

        app.extensions["qingpu_admin_runtime"] = replace(
            app.extensions["qingpu_admin_runtime"],
            provider_ops_service=None,
        )
        response = admin_client.get("/api/admin/providers")
        assert response.status_code == 503

    def test_set_gemini_key(self, admin_app, admin_client) -> None:
        app, _, _, _ = admin_app
        from dataclasses import replace

        from qingpu_insight.provider_ops import ProviderOpsService

        class _Rule:
            def generate(self, pack, repair_codes=()):
                return type("R", (), {"provider": "rule", "model": "rule"})()

        store = _SecretsStore()
        svc = ProviderOpsService(
            rule_provider=_Rule(),
            provider_factory=lambda n: None,
            env={},
        )
        app.extensions["qingpu_admin_runtime"] = replace(
            app.extensions["qingpu_admin_runtime"],
            provider_ops_service=svc,
            secrets_store=store,
        )
        response = admin_client.put(
            "/api/admin/providers/gemini-key",
            json={"key": "test-key-123"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 200
        assert response.json["gemini_configured"] is True
        assert store.last_set == "test-key-123"

    def test_set_gemini_key_rejects_extra_fields(self, admin_app, admin_client) -> None:
        response = admin_client.put(
            "/api/admin/providers/gemini-key",
            json={"key": "test", "extra": "bad"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400

    def test_set_gemini_key_rejects_empty(self, admin_app, admin_client) -> None:
        response = admin_client.put(
            "/api/admin/providers/gemini-key",
            json={"key": ""},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400

    def test_set_gemini_key_requires_csrf(self, admin_app, admin_client) -> None:
        response = admin_client.put(
            "/api/admin/providers/gemini-key",
            json={"key": "test-key"},
        )
        assert response.status_code == 403

    def test_delete_gemini_key(self, admin_app, admin_client) -> None:
        app, _, _, _ = admin_app
        from dataclasses import replace

        store = _SecretsStore()
        store.last_set = "existing-key"
        app.extensions["qingpu_admin_runtime"] = replace(
            app.extensions["qingpu_admin_runtime"],
            secrets_store=store,
        )
        response = admin_client.delete(
            "/api/admin/providers/gemini-key",
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 200
        assert store.deleted is True
        assert response.json["gemini_configured"] is False

    def test_provider_smoke_submit(self, admin_app, admin_client) -> None:
        app, _, _, _ = admin_app
        from dataclasses import replace

        from qingpu_insight.provider_ops import ProviderOpsService

        class _Rule:
            def generate(self, pack, repair_codes=()):
                return type("R", (), {"provider": "rule", "model": "rule"})()

        svc = ProviderOpsService(
            rule_provider=_Rule(),
            provider_factory=lambda n: None,
            env={},
        )
        app.extensions["qingpu_admin_runtime"] = replace(
            app.extensions["qingpu_admin_runtime"],
            provider_ops_service=svc,
        )
        response = admin_client.post(
            "/api/admin/provider-smoke-runs",
            json={"provider": "rule"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 202
        assert response.json["provider"] == "rule"
        assert response.json["status"] == "pending"
        assert response.json["job_type"] == "provider_smoke"
        assert response.json["created"] is True
        assert response.json["run_id"] in admin_app[3].submitted

    def test_provider_smoke_rejects_invalid_provider(self, admin_app, admin_client) -> None:
        response = admin_client.post(
            "/api/admin/provider-smoke-runs",
            json={"provider": "invalid"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400

    def test_provider_smoke_requires_csrf(self, admin_app, admin_client) -> None:
        response = admin_client.post(
            "/api/admin/provider-smoke-runs",
            json={"provider": "rule"},
        )
        assert response.status_code == 403

    # ------------------------------------------------------------------
    # Admin LLM Model Catalog tests (Task 3)
    # ------------------------------------------------------------------

    def test_admin_llm_models_returns_only_safe_catalog(
        self,
        admin_client,
    ) -> None:
        response = admin_client.get("/api/admin/llm-models")

        assert response.status_code == 200
        assert response.get_json()["items"][0]["id"] == "ollama:gemma4:e2b"
        assert "api_key" not in response.get_data(as_text=True).lower()
        assert "digest" not in response.get_data(as_text=True).lower()

    def test_admin_benchmark_accepts_only_catalog_model_id(
        self,
        admin_app,
        admin_client,
    ) -> None:
        from dataclasses import replace

        from qingpu_insight.provider_ops import ProviderOpsService

        class _Rule:
            def generate(self, pack, repair_codes=()):
                return type("R", (), {"provider": "rule", "model": "rule"})()

        svc = ProviderOpsService(
            rule_provider=_Rule(),
            provider_factory=lambda n: None,
            env={"QINGPU_OLLAMA_MODEL": "test"},
        )
        runner = _StubBenchmarkRunner()
        svc.set_benchmark_runner(runner)
        app, _, _, _ = admin_app
        app.extensions["qingpu_admin_runtime"] = replace(
            app.extensions["qingpu_admin_runtime"],
            provider_ops_service=svc,
            llm_model_catalog=FakeLlmModelCatalog(),
        )
        response = admin_client.post(
            "/api/admin/llm-benchmark-runs",
            json={"model_id": "ollama:gemma4:e2b"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )

        assert response.status_code == 202
        assert response.get_json()["provider"] == "ollama"
        assert response.get_json()["model"] == "gemma4:e2b"

    def test_admin_benchmark_rejects_unknown_or_spoofed_model(
        self,
        admin_app,
        admin_client,
    ) -> None:
        from dataclasses import replace

        from qingpu_insight.provider_ops import ProviderOpsService

        class _Rule:
            def generate(self, pack, repair_codes=()):
                return type("R", (), {"provider": "rule", "model": "rule"})()

        svc = ProviderOpsService(
            rule_provider=_Rule(),
            provider_factory=lambda n: None,
            env={"QINGPU_OLLAMA_MODEL": "test"},
        )
        runner = _StubBenchmarkRunner()
        svc.set_benchmark_runner(runner)
        app, _, _, _ = admin_app
        app.extensions["qingpu_admin_runtime"] = replace(
            app.extensions["qingpu_admin_runtime"],
            provider_ops_service=svc,
            llm_model_catalog=FakeLlmModelCatalog(),
        )

        unknown = admin_client.post(
            "/api/admin/llm-benchmark-runs",
            json={"model_id": "ollama:not-installed"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        spoofed = admin_client.post(
            "/api/admin/llm-benchmark-runs",
            json={
                "model_id": "ollama:gemma4:e2b",
                "provider": "gemini",
                "model": "gemma-4-31b-it",
            },
            headers={"X-Qingpu-CSRF": "test-token"},
        )

        assert unknown.status_code == 400
        assert unknown.get_json()["error"]["fields"] == {"model_id": "unsupported"}
        assert spoofed.status_code == 400
        assert set(spoofed.get_json()["error"]["fields"]) == {"provider", "model"}

    # ------------------------------------------------------------------
    # LLM Benchmark tests (Task 15)
    # ------------------------------------------------------------------

    def _setup_benchmark(self, admin_app, monkeypatch, tmp_path):
        from dataclasses import replace

        from qingpu_insight.provider_ops import ProviderOpsService

        class _Rule:
            def generate(self, pack, repair_codes=()):
                return type("R", (), {"provider": "rule", "model": "rule"})()

        svc = ProviderOpsService(
            rule_provider=_Rule(),
            provider_factory=lambda n: None,
            env={"QINGPU_OLLAMA_MODEL": "test"},
        )
        runner = _StubBenchmarkRunner()
        svc.set_benchmark_runner(runner)
        app, repo, _, _ = admin_app
        app.extensions["qingpu_admin_runtime"] = replace(
            app.extensions["qingpu_admin_runtime"],
            provider_ops_service=svc,
            llm_model_catalog=FakeLlmModelCatalog(),
        )
        return app, repo

    def test_benchmark_submit_success(self, admin_app, admin_client, monkeypatch, tmp_path) -> None:
        self._setup_benchmark(admin_app, monkeypatch, tmp_path)
        response = admin_client.post(
            "/api/admin/llm-benchmark-runs",
            json={"model_id": "ollama:gemma4:e2b"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 202
        body = response.get_json()
        assert body["provider"] == "ollama"
        assert body["model"] == "gemma4:e2b"
        assert body["status"] == "pending"

    def test_benchmark_submit_rejects_extra_fields(self, admin_app, admin_client) -> None:
        response = admin_client.post(
            "/api/admin/llm-benchmark-runs",
            json={"model_id": "ollama:gemma4:e2b", "cases": ["custom"]},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        assert "cases" in response.get_json()["error"]["fields"]

    def test_benchmark_submit_rejects_unknown_model_id(self, admin_app, admin_client) -> None:
        app, _, _, _ = admin_app
        from dataclasses import replace

        from qingpu_insight.provider_ops import ProviderOpsService

        class _Rule:
            def generate(self, pack, repair_codes=()):
                return type("R", (), {"provider": "rule", "model": "rule"})()

        svc = ProviderOpsService(
            rule_provider=_Rule(),
            provider_factory=lambda n: None,
            env={"QINGPU_OLLAMA_MODEL": "test"},
        )
        runner = _StubBenchmarkRunner()
        svc.set_benchmark_runner(runner)
        app.extensions["qingpu_admin_runtime"] = replace(
            app.extensions["qingpu_admin_runtime"],
            provider_ops_service=svc,
        )
        response = admin_client.post(
            "/api/admin/llm-benchmark-runs",
            json={"model_id": "unknown:model"},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["fields"]["model_id"] == "unsupported"

    def test_benchmark_submit_rejects_missing_model_id(self, admin_app, admin_client) -> None:
        app, _, _, _ = admin_app
        from dataclasses import replace

        from qingpu_insight.provider_ops import ProviderOpsService

        class _Rule:
            def generate(self, pack, repair_codes=()):
                return type("R", (), {"provider": "rule", "model": "rule"})()

        svc = ProviderOpsService(
            rule_provider=_Rule(),
            provider_factory=lambda n: None,
            env={"QINGPU_OLLAMA_MODEL": "test"},
        )
        runner = _StubBenchmarkRunner()
        svc.set_benchmark_runner(runner)
        app.extensions["qingpu_admin_runtime"] = replace(
            app.extensions["qingpu_admin_runtime"],
            provider_ops_service=svc,
        )
        response = admin_client.post(
            "/api/admin/llm-benchmark-runs",
            json={},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 400
        assert "model_id" in response.get_json()["error"]["fields"]

    def test_benchmark_submit_requires_csrf(self, admin_app, admin_client) -> None:
        response = admin_client.post(
            "/api/admin/llm-benchmark-runs",
            json={"model_id": "ollama:gemma4:e2b"},
        )
        assert response.status_code == 403

    def test_benchmark_report_validates_uuid(self, admin_app, admin_client) -> None:
        response = admin_client.get("/api/admin/llm-benchmark-runs/not-a-uuid/reports/json")
        assert response.status_code == 400
        assert response.get_json()["error"]["fields"]["run_id"] == "invalid_uuid"

    def test_benchmark_report_validates_type(self, admin_app, admin_client) -> None:
        response = admin_client.get(
            "/api/admin/llm-benchmark-runs/00000000-0000-4000-8000-000000000000/reports/pdf"
        )
        assert response.status_code == 400
        assert response.get_json()["error"]["fields"]["report_type"] == "json_or_markdown"

    def test_benchmark_report_returns_404_for_nonexistent_job(
        self,
        admin_app,
        admin_client,
    ) -> None:
        response = admin_client.get(
            "/api/admin/llm-benchmark-runs/00000000-0000-4000-8000-000000000000/reports/json"
        )
        assert response.status_code == 404

    def test_benchmark_report_happy_path(self, market_frame, tmp_path, monkeypatch) -> None:
        from qingpu_insight.jobs import JobService
        from qingpu_insight.provider_ops import ProviderOpsService
        from qingpu_insight.web import AdminServices, create_app

        monkeypatch.chdir(tmp_path)
        cases_dir = tmp_path / "benchmarks"
        cases_dir.mkdir(parents=True, exist_ok=True)
        cases_path = cases_dir / "m44_cases.json"
        import json as _json

        cases_path.write_text(_json.dumps([]))

        repo = MemoryAdminJobRepository()
        job_service = JobService(repo)
        executor = FakeAdminExecutor()
        listing_service = StubListingUpdateService(job_service)

        app = create_app(
            root=tmp_path,
            data_source=InMemoryMarketDataSource(market_frame),
            admin_services=AdminServices(
                job_service,
                listing_service,
                executor,
            ),
        )
        from dataclasses import replace

        svc = ProviderOpsService(
            rule_provider=type("R", (), {"generate": lambda s, p, rc=(): None})(),
            provider_factory=lambda n: type("P", (), {"generate": lambda s, p, rc=(): None})(),
            env={"QINGPU_OLLAMA_MODEL": "test"},
        )
        runner = _StubBenchmarkRunner()
        svc.set_benchmark_runner(runner)
        app.extensions["qingpu_admin_runtime"] = replace(
            app.extensions["qingpu_admin_runtime"],
            provider_ops_service=svc,
            root=tmp_path,
            dashboard_service=StubDashboardService(),
        )

        submission = job_service.create("llm_benchmark", "benchmark:active", "manual")
        run = submission.run
        job_service.start(run.run_id)
        job_service.succeed(run.run_id, "v1", {"schema_success": 1.0})

        report_dir = tmp_path / "outputs" / "m44-benchmark" / run.run_id
        report_dir.mkdir(parents=True)
        report_path = report_dir / "benchmark_results.json"
        expected_data = {"result": "ok"}
        report_path.write_text(_json.dumps(expected_data), encoding="utf-8")

        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["_csrf_token"] = "test-token"
            response = client.get(
                f"/api/admin/llm-benchmark-runs/{run.run_id}/reports/json",
                headers={"X-Qingpu-CSRF": "test-token"},
            )

        assert response.status_code == 200
        assert response.get_json() == expected_data


class FakeLlmModelCatalog:
    def public_catalog(self):
        return {
            "items": [
                {
                    "id": "ollama:gemma4:e2b",
                    "provider": "ollama",
                    "model": "gemma4:e2b",
                    "label": "Ollama｜gemma4:e2b",
                    "ready": True,
                    "note": "本機已安裝",
                }
            ],
            "warnings": [],
        }

    def resolve(self, model_id):
        if model_id != "ollama:gemma4:e2b":
            raise ValueError("unknown_model_id")
        from qingpu_insight.llm_model_catalog import BenchmarkModelOption

        return BenchmarkModelOption(
            id=model_id,
            provider="ollama",
            model="gemma4:e2b",
            label="Ollama｜gemma4:e2b",
            ready=True,
            note="本機已安裝",
        )


class _StubBenchmarkRunner:
    def run(self, provider: str, model: str, cases: list, output_dir) -> dict:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "benchmark_results.json").write_text('{"result": "ok"}')
        (output_dir / "benchmark_results.md").write_text("# Benchmark")
        return {
            "schema_success": 1.0,
            "fact_accuracy": 0.95,
            "required_section_success": 1.0,
            "p50_latency_ms": 150.0,
            "p95_latency_ms": 300.0,
            "reports": {"json": "benchmark_results.json", "markdown": "benchmark_results.md"},
        }
