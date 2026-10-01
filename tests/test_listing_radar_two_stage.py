"""Two-stage radar: list-field prescreen over the API batch, detail pages for the top few."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qingpu_insight import cli
from qingpu_insight.conversation_valuation import ListingOutOfArea
from qingpu_insight.listing_api_591 import CONTACT_FIELDS, Logged591ApiSource
from qingpu_insight.listing_capture import ChromeConfig
from qingpu_insight.listing_radar import (
    PRESCREEN_CAVEAT,
    ListingRadarStore,
    load_latest_api_batch,
    prescreen_listings,
    public_candidate,
    select_for_detail,
)
from qingpu_insight.listing_radar_runtime import (
    RAW_LISTING_DIR,
    RadarRunOptions,
    radar_store,
    run_listing_radar,
)
from tests.test_listing_api_591 import (
    DEEP_PAGE,
    FIRST_PAGE,
    FakeApiBrowser,
    _raw,
    _with_contacts,
)
from tests.test_listing_radar import _captured, _fake_valuate, _FakeCapture

NOW = datetime(2026, 10, 1, 4, 0, tzinfo=UTC)
CUT_ID = "20661373"  # 站前新鋭, 2,198 萬 -> 1,598 萬 (−27.3%)


def _api_batch(root: Path, *, total: int = 60, clock=None) -> Path:
    first = _with_contacts(_raw(FIRST_PAGE))
    deep = _with_contacts(_raw(DEEP_PAGE))
    first["data"]["total"] = deep["data"]["total"] = str(total)
    pages = {0: first, 30: deep}
    source = Logged591ApiSource(
        browser=FakeApiBrowser(lambda row: pages[row]),
        config=ChromeConfig(profile_dir="p", delay_seconds=(3, 3)),
        base_dir=root,
        sleep=lambda _: None,
        **({"clock": clock} if clock else {}),
    )
    return source.capture("sale", 300).batch_dir


def _prescreen(estimate: int = 20_000_000, low: int = 16_000_000, high: int = 24_000_000,
               failures: dict[str, Exception] | None = None):
    calls: list[int] = []

    def prescreen(payloads):
        calls.append(len(payloads))
        out = []
        for payload in payloads:
            failure = (failures or {}).get(payload["source_listing_id"])
            if failure is not None:
                out.append(failure)
                continue
            assert payload["latitude"] is None and payload["station_code"] in {"A17", "A18", "A19"}
            out.append((
                {"point_estimate_twd": estimate, "low_estimate_twd": low,
                 "high_estimate_twd": high, "confidence": None},
                {"parking_unverified": True},
            ))
        return out

    prescreen.calls = calls  # type: ignore[attr-defined]
    return prescreen


# --------------------------------------------------------------------------- stage 1 pieces


def test_latest_api_batch_reads_contact_free_listings(tmp_path: Path) -> None:
    _api_batch(tmp_path)
    batch = load_latest_api_batch(tmp_path / RAW_LISTING_DIR)
    assert batch is not None and batch.is_complete
    assert len(batch.listings) == 62
    text = json.dumps(batch.listings, ensure_ascii=False)
    assert "0912" not in text and "王小明" not in text
    assert not any(field in listing for listing in batch.listings for field in CONTACT_FIELDS)


def test_latest_api_batch_prefers_complete_batches(tmp_path: Path) -> None:
    _api_batch(tmp_path, clock=lambda: NOW)
    later = _api_batch(tmp_path, total=5494, clock=lambda: NOW + timedelta(hours=1))
    assert later.exists()
    batch = load_latest_api_batch(tmp_path / RAW_LISTING_DIR)
    assert batch.is_complete


def test_no_api_batch_means_no_two_stage(tmp_path: Path) -> None:
    assert load_latest_api_batch(tmp_path / RAW_LISTING_DIR) is None


def test_prescreen_scores_every_listing_in_one_call(tmp_path: Path) -> None:
    _api_batch(tmp_path)
    listings = load_latest_api_batch(tmp_path / RAW_LISTING_DIR).listings
    ids = [listing["source_listing_id"] for listing in listings]
    prescreen = _prescreen(failures={ids[0]: ListingOutOfArea("far"), ids[1]: ValueError("x")})
    progress: list[dict] = []

    records = prescreen_listings(listings, prescreen, progress=progress.append)

    assert prescreen.calls == [62]
    by_id = {r["source_listing_id"]: r for r in records}
    assert by_id[ids[0]]["prescreen_status"] == "out_of_area"
    assert by_id[ids[1]]["prescreen_status"] == "valuation_failed"
    cut = by_id[CUT_ID]
    assert cut["prescreen_status"] == "valued"
    assert cut["community_name"] == "站前新鋭"
    assert cut["original_price_twd"] == 21_980_000
    assert cut["prescreen_score"] == pytest.approx(
        math.log(20_000_000 / 15_980_000) / math.log(20 / 16)
    )
    assert progress[-1] == {"stage": "prescreening", "processed": 62, "total": 62}


def test_prescreen_survives_a_failing_model(tmp_path: Path) -> None:
    _api_batch(tmp_path)
    listings = load_latest_api_batch(tmp_path / RAW_LISTING_DIR).listings

    def broken(payloads):
        raise RuntimeError("market data unavailable")

    records = prescreen_listings(listings, broken)
    assert {r["prescreen_status"] for r in records} == {"valuation_failed"}


def test_select_for_detail_takes_top_scores_plus_fresh_cache() -> None:
    def record(listing_id: str, score, status="valued"):
        return {
            "source_listing_id": listing_id,
            "url": f"https://sale.591.com.tw/home/house/detail/2/{listing_id}.html",
            "prescreen_status": status,
            "prescreen_score": score,
        }

    prescreened = [
        record("1", 0.2), record("2", 1.5), record("3", None, "valuation_failed"),
        record("4", 0.9), record("5", 3.0, "out_of_area"), record("6", -0.4),
    ]
    candidates, rest = select_for_detail(prescreened, 2, fresh_ids={"6"})
    assert [c.source_listing_id for c in candidates] == ["2", "4", "6"]
    assert sorted(r["source_listing_id"] for r in rest) == ["1", "3", "5"]


# --------------------------------------------------------------------------- end to end


def test_two_stage_radar_only_opens_the_top_listings(tmp_path: Path) -> None:
    _api_batch(tmp_path)
    listings = load_latest_api_batch(tmp_path / RAW_LISTING_DIR).listings
    cheapest = sorted(
        listings, key=lambda item: (item["asking_price_twd"], item["source_listing_id"])
    )[:3]
    top_ids = [item["source_listing_id"] for item in cheapest]
    capture = _FakeCapture({i: _captured(i, price_wan="1,280") for i in top_ids})
    progress: list[dict] = []

    result, json_path, md_path = run_listing_radar(
        tmp_path,
        RadarRunOptions(max_listings=3),
        progress=progress.append,
        valuate=_fake_valuate(),
        prescreen=_prescreen(),
        capture=capture,
        sleep=lambda _: None,
        clock=lambda: NOW,
    )

    assert sorted(capture.requested) == sorted(top_ids)
    # The real two-page capture lists 5 flats twice; only the cheapest listing is screened.
    assert result.counts["prescreened"] == 57
    assert result.counts["duplicate"] == 5
    assert result.counts["valued"] == 3
    assert result.counts["prescreen_only"] == 54
    assert result.meta["source"].startswith("591-api:")
    assert result.meta["prescreen"]["detail_candidates"] == 3
    assert {p["stage"] for p in progress} >= {"prescreening", "selected", "capturing"}

    prescreen_only = [r for r in result.records if r["status"] == "prescreen_only"]
    assert all(r.get("rank") is None for r in prescreen_only)
    assert all(r["prescreen_score"] is not None for r in prescreen_only)

    ranked = result.candidates
    assert ranked and all(r["source_listing_id"] in top_ids for r in ranked)
    shown = public_candidate(ranked[0])
    assert {"community_name", "original_price_twd", "down_price_percent",
            "prescreen_score", "prescreen_estimate_twd"} <= set(shown)

    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["prescreen"]["listings"] == 62
    assert report["prescreen"]["unique_properties"] == 57
    assert PRESCREEN_CAVEAT in report["caveats"]
    markdown = md_path.read_text(encoding="utf-8")
    assert "兩階段" in markdown and "社區" in markdown

    meta, frame = radar_store(tmp_path).load_latest()
    assert len(frame) == 62
    assert meta["prescreen"]["list_batch_complete"] is True
    stored = frame.to_json(force_ascii=False)
    for value in ("王小明", "0912-345-678", "agent@example.com", "998877"):
        assert value not in stored


def test_price_cut_is_carried_to_the_ranked_card(tmp_path: Path) -> None:
    _api_batch(tmp_path)
    capture = _FakeCapture({CUT_ID: _captured(CUT_ID, price_wan="1,280")})

    def prescreen(payloads):
        out = []
        for payload in payloads:
            estimate = 30_000_000 if payload["source_listing_id"] == CUT_ID else 1_000_000
            out.append(({"point_estimate_twd": estimate, "low_estimate_twd": estimate * 0.8,
                         "high_estimate_twd": estimate * 1.2}, {}))
        return out

    result, _, md_path = run_listing_radar(
        tmp_path, RadarRunOptions(max_listings=1), valuate=_fake_valuate(),
        prescreen=prescreen, capture=capture, sleep=lambda _: None, clock=lambda: NOW,
    )

    assert capture.requested == [CUT_ID]
    card = public_candidate(result.candidates[0])
    assert card["community_name"] == "站前新鋭"
    assert card["original_price_twd"] == 21_980_000
    assert card["down_price_percent"] == 27.3
    assert "price_cut" in result.candidates[0]["flags"]
    assert "近期降價" in result.candidates[0]["reason"]
    assert "原 2,198 萬（−27.3%）" in md_path.read_text(encoding="utf-8")


def test_public_api_shows_community_and_price_cut(tmp_path: Path) -> None:
    from tests.test_listing_radar_web import _app

    _api_batch(tmp_path)
    capture = _FakeCapture({CUT_ID: _captured(CUT_ID, price_wan="1,280")})

    def prescreen(payloads):
        return [
            ({"point_estimate_twd": 30_000_000 if p["source_listing_id"] == CUT_ID else 1,
              "low_estimate_twd": 24_000_000 if p["source_listing_id"] == CUT_ID else 1,
              "high_estimate_twd": 36_000_000}, {})
            for p in payloads
        ]

    run_listing_radar(
        tmp_path, RadarRunOptions(max_listings=1), valuate=_fake_valuate(),
        prescreen=prescreen, capture=capture, sleep=lambda _: None, clock=lambda: NOW,
    )
    client = _app(tmp_path, radar_store(tmp_path)).test_client()
    body = client.get("/api/listing-radar").get_json()

    assert [item["source_listing_id"] for item in body["items"]] == [CUT_ID]
    item = body["items"][0]
    assert item["community_name"] == "站前新鋭"
    assert item["original_price_twd"] == 21_980_000
    assert item["down_price_percent"] == 27.3
    assert body["batch"]["counts"]["prescreened"] == 57
    assert body["batch"]["counts"]["prescreen_only"] == 56
    assert body["batch"]["counts"]["duplicate"] == 5


def test_fresh_cached_listings_join_without_live_requests(tmp_path: Path) -> None:
    _api_batch(tmp_path)
    listings = load_latest_api_batch(tmp_path / RAW_LISTING_DIR).listings
    priciest = max(listings, key=lambda item: item["asking_price_twd"])["source_listing_id"]
    cheapest = min(
        listings, key=lambda item: (item["asking_price_twd"], item["source_listing_id"])
    )["source_listing_id"]
    store = ListingRadarStore(tmp_path / "data" / "processed" / "listing_radar")
    store.write_captures({
        priciest: {
            "source_listing_id": priciest,
            "captured_at": (NOW - timedelta(hours=2)).isoformat(),
            "status": "delisted",
            "final_url": None,
            "payload_json": None,
        }
    })
    capture = _FakeCapture({cheapest: _captured(cheapest)})

    result, _, _ = run_listing_radar(
        tmp_path, RadarRunOptions(max_listings=1), valuate=_fake_valuate(),
        prescreen=_prescreen(), capture=capture, sleep=lambda _: None, clock=lambda: NOW,
    )

    assert capture.requested == [cheapest]
    assert result.counts["from_cache"] == 1
    assert result.counts["delisted"] == 1


def test_no_prescreen_option_keeps_the_single_stage_path(tmp_path: Path) -> None:
    from tests.test_listing_radar_runtime import _Repo

    _api_batch(tmp_path)
    capture = _FakeCapture({"1": _captured("1")})
    result, _, _ = run_listing_radar(
        tmp_path, RadarRunOptions(prescreen=False), repository=_Repo(["1"]),
        valuate=_fake_valuate(), prescreen=_prescreen(), capture=capture,
        sleep=lambda _: None,
    )
    assert capture.requested == ["1"]
    assert "prescreened" not in result.counts


def test_cli_no_prescreen_flag(tmp_path: Path, monkeypatch) -> None:
    seen: list[RadarRunOptions] = []

    def fake_run(root, options, **kwargs):
        seen.append(options)
        raise SystemExit(0)

    monkeypatch.setattr(cli, "run_listing_radar", fake_run)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        cli.main(["listing-radar", "--no-prescreen"])
    with pytest.raises(SystemExit):
        cli.main(["listing-radar"])
    assert [o.prescreen for o in seen] == [False, True]


def test_cli_radar_uses_the_591_profile_env(tmp_path: Path, monkeypatch) -> None:
    seen: list[RadarRunOptions] = []

    def fake_run(root, options, **kwargs):
        seen.append(options)
        raise SystemExit(0)

    monkeypatch.setattr(cli, "run_listing_radar", fake_run)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QINGPU_591_PROFILE_DIR", "instance/chrome-591")
    with pytest.raises(SystemExit):
        cli.main(["listing-radar"])
    with pytest.raises(SystemExit):
        cli.main(["listing-radar", "--profile-dir", "D:/other"])
    assert [o.profile_dir for o in seen] == ["instance/chrome-591", "D:/other"]
