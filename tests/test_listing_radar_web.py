"""Public radar page/API and the guarded admin trigger."""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest
from bs4 import BeautifulSoup
from flask import Flask

from qingpu_insight.job_executor import LocalJobExecutor
from qingpu_insight.jobs import JobService
from qingpu_insight.listing_radar import ListingRadarRunner, ListingRadarStore
from qingpu_insight.listing_radar_runtime import ListingRadarJobService, run_listing_radar
from qingpu_insight.valuation import ModelRegistry
from qingpu_insight.valuation_store import FileValuationStore
from qingpu_insight.web import AdminServices, create_app
from tests.test_listing_radar import _candidates, _captured, _fake_valuate, _FakeCapture
from tests.test_listing_radar_runtime import _MemoryJobs, _Repo


class _EmptyMarket:
    def load(self, filters):
        del filters
        return pd.DataFrame()


def _station_valuate(stations: dict[str, str]):
    base = _fake_valuate()

    def valuate(payload):
        public, context = base(payload)
        context["station_code"] = stations[payload["source_listing_id"]]
        return public, context

    return valuate


def _seed_store(tmp_path: Path) -> ListingRadarStore:
    store = ListingRadarStore(tmp_path / "radar")
    capture = _FakeCapture(
        {
            "1": _captured("1", price_wan="1,280", title="急售 0912-345-678"),
            "2": _captured("2", price_wan="1,500"),
            "3": _captured("3", price_wan="1,700"),
            "4": _captured("4", price_wan="2,100"),
        }
    )
    ListingRadarRunner(
        store=store,
        capture=capture,
        valuate=_station_valuate({"1": "A18", "2": "A19", "3": "A18", "4": "A17"}),
        sleep=lambda _: None,
    ).run(_candidates("1", "2", "3", "4"))
    return store


def _app(tmp_path: Path, store: ListingRadarStore | None, **kwargs) -> Flask:
    return create_app(
        data_source=_EmptyMarket(),
        valuation_store=FileValuationStore(tmp_path / "valuations"),
        model_registry=ModelRegistry(tmp_path / "artifacts"),
        listing_radar_store=store,
        **kwargs,
    )


# --------------------------------------------------------------------------- public API


def test_api_without_a_radar_batch_is_empty_but_explains_itself(tmp_path: Path) -> None:
    client = _app(tmp_path, ListingRadarStore(tmp_path / "none")).test_client()
    response = client.get("/api/listing-radar")
    assert response.status_code == 200
    body = response.get_json()
    assert body["batch"] is None
    assert body["items"] == []
    assert any("凶宅" in caveat for caveat in body["caveats"])


def test_api_lists_ranked_candidates_with_estimates_and_reasons(tmp_path: Path) -> None:
    client = _app(tmp_path, _seed_store(tmp_path)).test_client()
    body = client.get("/api/listing-radar").get_json()

    assert body["batch"]["radar_batch_id"].startswith("radar-")
    assert body["batch"]["counts"]["valued"] == 4
    ids = [item["source_listing_id"] for item in body["items"]]
    assert ids == ["1", "2", "3"]  # 2,100 萬 asks above the 1,800 萬 estimate
    first = body["items"][0]
    assert first["rank"] == 1
    assert first["url"] == "https://sale.591.com.tw/home/house/detail/2/1.html"
    assert first["asking_price_twd"] == 12_800_000
    assert first["estimate_twd"] == 18_000_000
    assert first["interval_low_twd"] == 16_000_000
    assert first["below_interval"] is True
    assert first["price_anchor"] == "same_building"
    assert first["common_area_source"] == "591_areas"
    assert "明顯低於區間" in first["reason"]
    assert "0912" not in str(body)


def test_api_filters_by_station_sorts_and_limits(tmp_path: Path) -> None:
    client = _app(tmp_path, _seed_store(tmp_path)).test_client()
    a18 = client.get("/api/listing-radar?station=A18").get_json()
    assert [i["source_listing_id"] for i in a18["items"]] == ["1", "3"]
    both = client.get("/api/listing-radar?station=A18,A19&sort=asking&limit=2").get_json()
    assert [i["source_listing_id"] for i in both["items"]] == ["1", "2"]


@pytest.mark.parametrize(
    "query",
    ["station=A20", "limit=0", "limit=500", "limit=abc", "sort=random", "verdict=cheap"],
)
def test_api_rejects_invalid_queries(tmp_path: Path, query: str) -> None:
    client = _app(tmp_path, _seed_store(tmp_path)).test_client()
    response = client.get(f"/api/listing-radar?{query}")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_request"


def test_api_is_public_and_read_only(tmp_path: Path) -> None:
    client = _app(tmp_path, _seed_store(tmp_path)).test_client()
    remote = client.get("/api/listing-radar", environ_base={"REMOTE_ADDR": "10.0.0.2"})
    assert remote.status_code == 200
    assert client.post("/api/listing-radar", json={}).status_code == 405


# --------------------------------------------------------------------------- pages


def test_radar_page_explains_why_low_is_not_a_bargain(tmp_path: Path) -> None:
    client = _app(tmp_path, None).test_client()
    response = client.get("/radar")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    soup = BeautifulSoup(html, "html.parser")
    for term in ("頂樓加蓋", "凶宅", "海砂屋", "持分", "地上權", "裝潢", "採光", "景觀"):
        assert term in html
    scripts = soup.find_all("script")
    assert [s.get("src") for s in scripts] == ["/static/listing_radar.js"]
    assert all(not s.string for s in scripts)  # JS lives in static files
    assert soup.select_one("#radar-station") is not None
    assert soup.select_one("#radar-list") is not None


def test_homepage_links_to_the_radar(tmp_path: Path) -> None:
    home = BeautifulSoup(
        _app(tmp_path, None).test_client().get("/").get_data(as_text=True), "html.parser"
    )
    assert home.select_one('a[href="/radar"]') is not None


def test_admin_page_has_radar_trigger(tmp_path: Path) -> None:
    html = _app(tmp_path, None).test_client().get("/admin/").get_data(as_text=True)
    soup = BeautifulSoup(html, "html.parser")
    button = soup.select_one("#lr-run-btn")
    assert button is not None and "mutation-btn" in button.get("class", [])
    assert soup.select_one('script[src="/static/listing_radar_admin.js"]') is not None


# --------------------------------------------------------------------------- admin job


def _admin_app(tmp_path: Path, outcomes: dict) -> tuple[Flask, JobService, LocalJobExecutor]:
    jobs = JobService(_MemoryJobs())
    executor = LocalJobExecutor(jobs)

    def run(options, progress=None):
        return run_listing_radar(
            tmp_path,
            options,
            progress=progress,
            repository=_Repo(list(outcomes)),
            valuate=_fake_valuate(),
            capture=_FakeCapture(outcomes),
            sleep=lambda _: None,
        )

    app = _app(
        tmp_path,
        ListingRadarStore(tmp_path / "data" / "processed" / "listing_radar"),
        admin_services=AdminServices(
            job_service=jobs,
            listing_update_service=object(),
            executor=executor,
            listing_radar_service=ListingRadarJobService(jobs, run),
        ),
    )
    return app, jobs, executor


def test_admin_trigger_runs_the_radar_job_and_publishes_results(tmp_path: Path) -> None:
    app, jobs, executor = _admin_app(tmp_path, {"1": _captured("1", price_wan="1,280")})
    try:
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "test-token"
        response = client.post(
            "/api/admin/listing-radar-runs",
            json={"max_listings": 5, "refresh_hours": 12},
            headers={"X-Qingpu-CSRF": "test-token"},
        )
        assert response.status_code == 202
        body = response.get_json()
        assert body["job_type"] == "listing_radar"
        assert body["info_url"] == f"/api/jobs/{body['run_id']}"

        deadline = time.time() + 10
        while time.time() < deadline:
            run = jobs.get(body["run_id"])
            if run.status in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        assert run.status == "succeeded", run
        assert run.summary["valued"] == 1

        status = client.get(body["info_url"]).get_json()
        assert status["status"] == "succeeded"
        radar = client.get("/api/listing-radar").get_json()
        assert [item["source_listing_id"] for item in radar["items"]] == ["1"]
    finally:
        executor.shutdown(wait=True)


@pytest.mark.parametrize(
    "payload",
    [{"max_listings": 0}, {"max_listings": "60"}, {"refresh_hours": -1}, {"extra": 1}],
)
def test_admin_trigger_validates_payload(tmp_path: Path, payload) -> None:
    app, _, executor = _admin_app(tmp_path, {})
    try:
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["_csrf_token"] = "test-token"
        response = client.post(
            "/api/admin/listing-radar-runs", json=payload, headers={"X-Qingpu-CSRF": "test-token"}
        )
        assert response.status_code == 400
    finally:
        executor.shutdown(wait=True)


def test_admin_trigger_is_unavailable_without_admin_services(tmp_path: Path) -> None:
    client = _app(tmp_path, None).test_client()
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "test-token"
    response = client.post(
        "/api/admin/listing-radar-runs", json={}, headers={"X-Qingpu-CSRF": "test-token"}
    )
    assert response.status_code == 503


def test_api_filters_by_verdict_and_returns_market(tmp_path: Path) -> None:
    client = _app(tmp_path, _seed_store(tmp_path)).test_client()
    body = client.get("/api/listing-radar").get_json()
    verdicts = {item["verdict"] for item in body["items"]}
    assert verdicts and None not in verdicts
    some = next(iter(verdicts))
    only = client.get(f"/api/listing-radar?verdict={some}").get_json()
    assert only["items"] and all(item["verdict"] == some for item in only["items"])
    assert set(body["market"]) == {"market_heat", "asking_index", "asking_drift", "negotiation"}
