"""低估物件雷達: candidate selection, polite capture loop, honest ranking and storage."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from qingpu_insight.conversation_listing_capture import CapturedListing
from qingpu_insight.conversation_listing_parser import (
    ListingDelisted,
    ListingPageVerificationRequired,
    parse_listing_detail,
)
from qingpu_insight.listing_radar import (
    ListingRadarRunner,
    ListingRadarStore,
    RadarCandidate,
    assess_record,
    load_candidate_frame,
    rank_records,
    select_candidates,
    write_radar_report,
)

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 1, 4, 0, tzinfo=UTC)


def _url(listing_id: str) -> str:
    return f"https://sale.591.com.tw/home/house/detail/2/{listing_id}.html"


def _detail_html(listing_id: str, *, price_wan: str = "1,580", title: str = "青埔景觀三房") -> str:
    """A synthetic live-style 591 sale page; numbers carry empty decoy tags."""
    template = (FIXTURES / "591_sale_detail_radar.html").read_text(encoding="utf-8")
    return (
        template.replace("{{LISTING_ID}}", listing_id)
        .replace("{{PRICE}}", price_wan)
        .replace("{{TITLE}}", title)
    )


def _captured(listing_id: str, **kwargs) -> CapturedListing:
    detail = parse_listing_detail(
        _detail_html(listing_id, **kwargs), canonical_url=_url(listing_id), listing_type="sale"
    )
    return CapturedListing(final_url=_url(listing_id), detail=detail)


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class _Sleeper:
    def __init__(self, clock: _Clock) -> None:
        self.calls: list[float] = []
        self._clock = clock

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self._clock.now += timedelta(seconds=seconds)


class _FakeCapture:
    def __init__(self, outcomes: dict[str, object]) -> None:
        self.outcomes = outcomes
        self.requested: list[str] = []

    def __call__(self, initial):
        listing_id = initial.request_url.rsplit("/", 1)[-1].removesuffix(".html")
        self.requested.append(listing_id)
        outcome = self.outcomes[listing_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _fake_valuate(estimate: int = 18_000_000, low: int = 16_000_000, high: int = 20_000_000,
                  **context_overrides):
    calls: list[dict] = []

    def valuate(payload: dict):
        calls.append(payload)
        public = {
            "common_area": {"provided": True, "source": "591_areas", "ratio": 0.31},
            "point_estimate_twd": estimate,
            "low_estimate_twd": low,
            "high_estimate_twd": high,
            "confidence": "medium",
            "confidence_reasons": ["高相似度成交案例不足（少於 3 筆 ≥ 0.60）"],
            "model_version": "resale-anchor-v9",
            "dataset_version": "2026-09-30",
            "limitations": ["房屋坪數已從建物總坪數扣除車位 10.32 坪"],
        }
        context = {
            "station_code": "A18",
            "station_distance_m": 420.0,
            "total_area_ping": float(payload["area_ping"]),
            "net_area_ping": float(payload["area_ping"]) - 10.32,
            "parking_area_ping": 10.32,
            "parking_unverified": False,
            "age_years": float(payload["age_years"]),
            "price_anchor": "same_building",
            "degraded": False,
            "degraded_reason": None,
            **context_overrides,
        }
        return public, context

    valuate.calls = calls  # type: ignore[attr-defined]
    return valuate


def _runner(tmp_path: Path, capture, valuate=None, clock=None, **kwargs) -> ListingRadarRunner:
    clock = clock or _Clock()
    return ListingRadarRunner(
        store=ListingRadarStore(tmp_path / "radar"),
        capture=capture,
        valuate=valuate or _fake_valuate(),
        clock=clock,
        sleep=kwargs.pop("sleep", _Sleeper(clock)),
        **kwargs,
    )


def _candidates(*ids: str) -> list[RadarCandidate]:
    return [RadarCandidate(source_listing_id=i, source_url=_url(i)) for i in ids]


# --------------------------------------------------------------------------- fixtures


def test_synthetic_detail_fixture_parses_through_decoy_tags() -> None:
    detail = _captured("30000001").detail
    assert detail.total_price_twd == 15_800_000
    assert detail.area_ping == Decimal("40.32")
    assert detail.layout == "3房2廳2衛"
    assert detail.age_years == Decimal("6")
    assert detail.listed_common_area_percent == Decimal("31.5")
    assert detail.main_building_area_ping == Decimal("24.12")
    assert detail.parking_type == "10.32坪，平面式，已含售金內"
    assert detail.latitude is not None and detail.longitude is not None
    assert "0912" not in detail.title


# --------------------------------------------------------------------------- candidates


def test_select_candidates_keeps_active_sale_rows_in_area_newest_first() -> None:
    frame = pd.DataFrame(
        [
            {"listing_type": "sale", "source_listing_id": "1", "source_url": _url("1"),
             "snapshot_at": "2026-09-01T00:00:00Z", "active": True, "station_code": "A18"},
            {"listing_type": "sale", "source_listing_id": "2", "source_url": _url("2"),
             "snapshot_at": "2026-09-03T00:00:00Z", "active": True, "station_code": None},
            {"listing_type": "sale", "source_listing_id": "3", "source_url": _url("3"),
             "snapshot_at": "2026-09-04T00:00:00Z", "active": False, "station_code": "A18"},
            {"listing_type": "sale", "source_listing_id": "4", "source_url": _url("4"),
             "snapshot_at": "2026-09-05T00:00:00Z", "active": True, "station_code": "A20"},
            {"listing_type": "rental", "source_listing_id": "5", "source_url": _url("5"),
             "snapshot_at": "2026-09-05T00:00:00Z", "active": True, "station_code": "A18"},
            {"listing_type": "sale", "source_listing_id": "6",
             "source_url": "https://evil.example/detail/6.html",
             "snapshot_at": "2026-09-05T00:00:00Z", "active": True, "station_code": "A18"},
        ]
    )
    candidates = select_candidates(frame, max_listings=10)
    assert [c.source_listing_id for c in candidates] == ["2", "1"]
    assert select_candidates(frame, max_listings=1)[0].source_listing_id == "2"


def test_select_candidates_without_active_column_uses_latest_batch() -> None:
    frame = pd.DataFrame(
        [
            {"listing_type": "sale", "source_listing_id": "1", "source_url": _url("1"),
             "snapshot_at": "2026-09-01T00:00:00Z", "batch_id": "591-sale-old"},
            {"listing_type": "sale", "source_listing_id": "2", "source_url": _url("2"),
             "snapshot_at": "2026-09-03T00:00:00Z", "batch_id": "591-sale-new"},
        ]
    )
    assert [c.source_listing_id for c in select_candidates(frame, 10)] == ["2"]


def test_load_candidate_frame_falls_back_to_snapshot_export(tmp_path: Path) -> None:
    class _EmptyRepo:
        def load_current(self, listing_type=None):
            return pd.DataFrame()

    export = tmp_path / "listing_snapshots.parquet"
    pd.DataFrame(
        [{"listing_type": "sale", "source_listing_id": "9", "source_url": _url("9"),
          "snapshot_at": "2026-09-01T00:00:00Z", "batch_id": "591-sale-x"}]
    ).to_parquet(export)

    frame, source = load_candidate_frame(_EmptyRepo(), export)
    assert list(frame["source_listing_id"]) == ["9"]
    assert source == "listing_snapshots.parquet"


# --------------------------------------------------------------------------- runner


def test_runner_reuses_one_capture_path_with_polite_delays(tmp_path: Path) -> None:
    capture = _FakeCapture({"1": _captured("1"), "2": _captured("2"), "3": _captured("3")})
    clock = _Clock()
    sleeper = _Sleeper(clock)
    valuate = _fake_valuate()
    runner = _runner(tmp_path, capture, valuate, clock=clock, sleep=sleeper)

    result = runner.run(_candidates("1", "2", "3"), max_listings=60)

    assert capture.requested == ["1", "2", "3"]
    assert len(sleeper.calls) == 2  # between pages only
    assert all(3.0 <= delay <= 6.0 for delay in sleeper.calls)
    assert result.status == "completed"
    assert result.counts["valued"] == 3
    assert len(valuate.calls) == 3
    # The valuation sees the parsed 591 detail exactly as the assistant would.
    assert valuate.calls[0]["layout"] == "3房2廳2衛"
    assert valuate.calls[0]["listing_type"] == "sale"


def test_runner_caps_listings(tmp_path: Path) -> None:
    capture = _FakeCapture({"1": _captured("1"), "2": _captured("2")})
    result = _runner(tmp_path, capture).run(_candidates("1", "2"), max_listings=1)
    assert capture.requested == ["1"]
    assert result.counts["candidates"] == 1


def test_verification_page_stops_the_whole_run_and_keeps_finished_work(tmp_path: Path) -> None:
    capture = _FakeCapture(
        {
            "1": _captured("1"),
            "2": ListingPageVerificationRequired("captcha"),
            "3": _captured("3"),
        }
    )
    store = ListingRadarStore(tmp_path / "radar")
    runner = ListingRadarRunner(
        store=store, capture=capture, valuate=_fake_valuate(), clock=_Clock(),
        sleep=lambda _: None,
    )

    result = runner.run(_candidates("1", "2", "3"))

    assert capture.requested == ["1", "2"]
    assert result.status == "stopped_verification"
    assert result.counts["valued"] == 1
    meta, frame = store.load_latest()
    assert meta is not None and meta["status"] == "stopped_verification"
    assert list(frame["source_listing_id"]) == ["1"]


def test_delisted_and_broken_pages_are_skipped(tmp_path: Path) -> None:
    capture = _FakeCapture(
        {
            "1": ListingDelisted("gone"),
            "2": TimeoutError("slow"),
            "3": _captured("3"),
        }
    )
    result = _runner(tmp_path, capture).run(_candidates("1", "2", "3"))
    statuses = {r["source_listing_id"]: r["status"] for r in result.records}
    assert statuses == {"1": "delisted", "2": "capture_failed", "3": "valued"}
    assert result.status == "completed"


def test_consecutive_capture_failures_stop_the_run(tmp_path: Path) -> None:
    capture = _FakeCapture({str(i): TimeoutError("blocked?") for i in range(1, 6)})
    result = _runner(tmp_path, capture, max_consecutive_failures=3).run(
        _candidates("1", "2", "3", "4", "5")
    )
    assert capture.requested == ["1", "2", "3"]
    assert result.status == "stopped_failures"


def test_recent_captures_are_not_refetched(tmp_path: Path) -> None:
    clock = _Clock()
    first = _FakeCapture({"1": _captured("1"), "2": ListingDelisted("gone")})
    _runner(tmp_path, first, clock=clock).run(_candidates("1", "2"))

    clock.now += timedelta(hours=5)
    second = _FakeCapture({"1": _captured("1"), "2": _captured("2"), "3": _captured("3")})
    sleeper = _Sleeper(clock)
    result = _runner(tmp_path, second, clock=clock, sleep=sleeper, refresh_hours=24).run(
        _candidates("1", "2", "3")
    )

    assert second.requested == ["3"]
    assert sleeper.calls == []
    statuses = {r["source_listing_id"]: r["status"] for r in result.records}
    assert statuses == {"1": "valued", "2": "delisted", "3": "valued"}
    assert result.counts["from_cache"] == 2

    clock.now += timedelta(hours=30)
    third = _FakeCapture({"1": _captured("1")})
    _runner(tmp_path, third, clock=clock, refresh_hours=24).run(_candidates("1"))
    assert third.requested == ["1"]


def test_offline_mode_only_uses_cached_captures(tmp_path: Path) -> None:
    clock = _Clock()
    _runner(tmp_path, _FakeCapture({"1": _captured("1")}), clock=clock).run(_candidates("1"))
    result = _runner(tmp_path, None, clock=clock).run(_candidates("1", "2"))
    statuses = {r["source_listing_id"]: r["status"] for r in result.records}
    assert statuses == {"1": "valued", "2": "not_cached"}


def test_valuation_failures_and_out_of_area_are_recorded_not_raised(tmp_path: Path) -> None:
    def failing(payload):
        raise ValueError("listing lacks coordinates")

    capture = _FakeCapture({"1": _captured("1")})
    result = _runner(tmp_path, capture, valuate=failing).run(_candidates("1"))
    assert result.records[0]["status"] == "valuation_failed"
    assert result.records[0]["status_reason"] == "listing lacks coordinates"

    far = _fake_valuate(station_distance_m=3500.0)
    capture = _FakeCapture({"2": _captured("2")})
    result = _runner(tmp_path, capture, valuate=far).run(_candidates("2"))
    assert result.records[0]["status"] == "out_of_area"


def test_stored_records_never_contain_contact_details(tmp_path: Path) -> None:
    capture = _FakeCapture({"1": _captured("1", title="屋主急售 0912-345-678 a@b.com")})
    store = ListingRadarStore(tmp_path / "radar")
    ListingRadarRunner(
        store=store, capture=capture, valuate=_fake_valuate(), clock=_Clock(),
        sleep=lambda _: None,
    ).run(_candidates("1"))

    captures = store.load_captures()
    payload = json.loads(captures.iloc[0]["payload_json"])
    assert "address" not in payload
    _, frame = store.load_latest()
    stored_text = json.dumps(payload, ensure_ascii=False) + frame.to_json(force_ascii=False)
    assert "0912" not in stored_text
    assert "a@b.com" not in stored_text
    assert "屋主急售" in stored_text
    assert not frame.iloc[0]["title"].endswith("591售屋網")


# --------------------------------------------------------------------------- ranking


def _valued(**overrides) -> dict:
    record = {
        "source_listing_id": "1",
        "status": "valued",
        "listing_type": "sale",
        "asking_price_twd": 14_000_000,
        "estimate_twd": 18_000_000,
        "interval_low_twd": 16_000_000,
        "interval_high_twd": 20_000_000,
        "confidence": "medium",
        "confidence_reasons": [],
        "has_coordinates": True,
        "layout": "3房2廳2衛",
        "age_years": 8.0,
        "net_area_ping": 30.0,
        "station_distance_m": 400.0,
        "price_anchor": "same_building",
        "common_area_source": "591_areas",
        "parking_unverified": False,
        "degraded": False,
    }
    record.update(overrides)
    return record


def test_assess_record_computes_gap_and_interval_position() -> None:
    record = assess_record(_valued(), radius_m=2000.0)
    assert record["gap_pct"] == pytest.approx((14 - 18) / 18)
    assert record["below_interval"] is True
    assert record["score"] > 1  # beyond the interval low in log-radius units
    assert record["eligible"] is True
    assert "明顯低於區間" in record["reason"]


def test_assess_record_inside_interval_is_scored_below_one() -> None:
    record = assess_record(_valued(asking_price_twd=17_000_000), radius_m=2000.0)
    assert record["below_interval"] is False
    assert 0 < record["score"] < 1


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"confidence": "low"}, "low_confidence"),
        ({"has_coordinates": False}, "missing_coordinates"),
        ({"layout": None}, "missing_layout"),
        ({"age_years": None}, "missing_age"),
        ({"net_area_ping": 3.0}, "area_out_of_range"),
        ({"net_area_ping": 400.0}, "area_out_of_range"),
        ({"listing_type": "newhouse"}, "not_sale"),
        ({"degraded": True}, "fallback_valuation"),
        ({"asking_price_twd": None}, "missing_asking_price"),
    ],
)
def test_minimum_data_quality_gates_ranking(overrides, reason) -> None:
    record = assess_record(_valued(**overrides), radius_m=2000.0)
    assert record["eligible"] is False
    assert reason in record["ineligible_reasons"]


def test_new_projects_and_fallbacks_are_flagged() -> None:
    record = assess_record(
        _valued(
            age_years=1.0,
            confidence_reasons=["新建案：主要依同棟完工前成交價推估，完工後轉手價可能與預售價有明顯落差"],
            common_area_source=None,
            parking_unverified=True,
        ),
        radius_m=2000.0,
    )
    assert {"new_project", "presale_anchor", "common_area_unused", "parking_unverified"} <= set(
        record["flags"]
    )
    assert record["eligible"] is True


def test_rank_orders_by_distance_below_interval_not_raw_gap() -> None:
    # Same 20% raw gap, but B has a much narrower interval, so it is more clearly low.
    wide = _valued(source_listing_id="A", asking_price_twd=14_400_000,
                   interval_low_twd=12_000_000, interval_high_twd=27_000_000)
    narrow = _valued(source_listing_id="B", asking_price_twd=14_400_000)
    above = _valued(source_listing_id="C", asking_price_twd=19_000_000)
    low_conf = _valued(source_listing_id="D", asking_price_twd=9_000_000, confidence="low")

    ranked = rank_records([wide, narrow, above, low_conf], radius_m=2000.0)
    by_id = {r["source_listing_id"]: r for r in ranked}

    assert by_id["B"]["rank"] == 1
    assert by_id["A"]["rank"] == 2
    assert by_id["C"]["rank"] is None  # asking above estimate is not a candidate
    assert by_id["D"]["rank"] is None


# --------------------------------------------------------------------------- report


def test_report_is_written_as_json_and_markdown(tmp_path: Path) -> None:
    capture = _FakeCapture({"1": _captured("1", price_wan="1,280"), "2": ListingDelisted("x")})
    result = _runner(tmp_path, capture).run(_candidates("1", "2"))

    json_path, md_path = write_radar_report(tmp_path / "outputs", result)

    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["radar_batch_id"] == result.radar_batch_id
    assert report["counts"]["delisted"] == 1
    assert report["candidates"][0]["source_listing_id"] == "1"
    text = md_path.read_text(encoding="utf-8")
    assert "低估物件雷達" in text
    assert "凶宅" in text and "裝潢" in text
