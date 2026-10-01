"""Duplicate listings, daily history, asking-price index, negotiation gap, radar gates."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from qingpu_insight.asking_index import AskingIndexStore, asking_drift, asking_ratio_rows
from qingpu_insight.listing_dedupe import group_properties, unique_property_count
from qingpu_insight.listing_history import (
    ListingHistoryStore,
    complete_api_batches,
    listing_timelines,
    market_heat,
    timeline_annotations,
)
from qingpu_insight.listing_negotiation import (
    MIN_MATCHES,
    gone_properties,
    match_deals,
    negotiation_summary,
    offer_range,
)
from qingpu_insight.listing_radar import apply_community_check, assess_record, rank_records
from qingpu_insight.listing_radar_runtime import RAW_LISTING_DIR
from tests.test_listing_radar_two_stage import _api_batch

DAY1 = datetime(2026, 9, 1, 2, tzinfo=UTC)
DAY2 = DAY1 + timedelta(days=7)


def _listing(listing_id: str, **overrides) -> dict:
    base = {
        "source_listing_id": listing_id,
        "station_code": "A18",
        "floor_text": "3F/10F",
        "total_floors": 10,
        "area_ping": 42.59,
        "asking_price_twd": 16_200_000,
        "building_age_years": 5.0,
        "community_name": "鼎藏文星",
    }
    return {**base, **overrides}


# --------------------------------------------------------------------------- dedupe


def test_one_flat_listed_by_several_agents_is_one_property() -> None:
    groups = group_properties([
        _listing("20846682"),
        _listing("20676570", community_name="鼎藏麗星", area_ping=42.5),
        _listing("20862858", community_name="鼎藏", asking_price_twd=15_980_000),
        _listing("30000001", floor_text="6F/14F", total_floors=14),
        _listing("30000002", asking_price_twd=18_000_000),
        _listing("30000003", area_ping=44.0),
    ])

    same = groups["20846682"]
    assert set(same.listing_ids) == {"20676570", "20846682", "20862858"}
    assert same.representative_id == "20862858"  # the cheapest asking price
    assert same.property_key == "p20676570"
    assert same.duplicate_count == 2
    assert same.min_price_twd == 15_980_000
    for other in ("30000001", "30000002", "30000003"):
        assert groups[other].listing_ids == (other,)
    assert unique_property_count(groups) == 4


def test_listing_without_floor_stays_alone() -> None:
    groups = group_properties([_listing("1", floor_text=None), _listing("2", floor_text=None)])
    assert unique_property_count(groups) == 2


# --------------------------------------------------------------------------- history


def _panel_row(batch: str, day: datetime, listing_id: str, price: float, **extra) -> dict:
    return {
        "batch_id": batch,
        "observed_at": day,
        "source_listing_id": listing_id,
        "property_key": extra.pop("property_key", f"p{listing_id}"),
        "asking_price_twd": price,
        "original_price_twd": extra.pop("original_price_twd", math.nan),
        "down_price_percent": extra.pop("down_price_percent", math.nan),
        "posted_at": extra.pop("posted_at", day - timedelta(days=20)),
        "community_name": "c",
        "station_code": extra.pop("station_code", "A18"),
        "area_ping": extra.pop("area_ping", 40.0),
        "floor_text": extra.pop("floor_text", "5F/12F"),
        "total_floors": 12.0,
        "building_age_years": extra.pop("building_age_years", 8.0),
    }


def _two_day_panel() -> pd.DataFrame:
    return pd.DataFrame([
        _panel_row("b1", DAY1, "1", 20_000_000),
        _panel_row("b1", DAY1, "2", 15_000_000, floor_text="7F/12F", area_ping=35.0),
        _panel_row("b2", DAY2, "1", 19_000_000, original_price_twd=20_000_000,
                   down_price_percent=5.0),
        _panel_row("b2", DAY2, "3", 12_000_000),
    ])


def test_timelines_track_cuts_and_days_on_market() -> None:
    timelines = listing_timelines(_two_day_panel()).set_index("source_listing_id")

    assert timelines.loc["1", "price_cuts"] == 1
    assert timelines.loc["1", "cut_from_max_pct"] == 0.05
    assert bool(timelines.loc["1", "active"]) and not bool(timelines.loc["2", "active"])
    # posted 20 days before the first observation, seen again 7 days later
    assert round(timelines.loc["1", "days_on_market"]) == 27
    notes = timeline_annotations(listing_timelines(_two_day_panel()), ["1"])
    assert notes == {"1": {"days_on_market": 27.0, "observed_price_cuts": 1}}


def test_market_heat_counts_new_and_gone() -> None:
    heat = market_heat(_two_day_panel())
    first, second = heat.iloc[0], heat.iloc[1]

    assert first["properties"] == 2 and pd.isna(first["new_properties"])
    assert second["new_properties"] == 1  # listing 3
    assert second["gone_listings"] == 1  # listing 2
    assert second["price_cut_share"] == 0.5


def test_history_store_reads_only_complete_api_batches(tmp_path: Path) -> None:
    _api_batch(tmp_path)  # complete: total 60 over two pages
    _api_batch(tmp_path, total=200,  # stops after two pages: incomplete
               clock=lambda: DAY2)
    raw = tmp_path / RAW_LISTING_DIR

    assert len(complete_api_batches(raw)) == 1
    store = ListingHistoryStore(tmp_path / "history")
    panel, added = store.update(raw)
    assert len(added) == 1 and len(panel) == 62
    assert panel["property_key"].nunique() == 57
    _, added_again = store.update(raw)
    assert added_again == []
    stored = panel.to_json(force_ascii=False)
    for value in ("王小明", "0912-345-678", "agent@example.com"):
        assert value not in stored


# --------------------------------------------------------------------------- asking index


def _priced(n: int, ratio: float, station: str = "A18", status: str = "prescreen_only"):
    return [
        {"status": status, "station_code": station, "asking_price_twd": 10_000_000 * ratio,
         "prescreen_estimate_twd": 10_000_000}
        for _ in range(n)
    ]


def test_asking_index_uses_unique_properties_only() -> None:
    records = _priced(40, 1.10) + _priced(500, 2.0, status="duplicate") + _priced(5, 1.0, "A17")
    rows = asking_ratio_rows(records, radar_batch_id="r1", observed_at=DAY1.isoformat(),
                             model_version="m1")

    assert [row["station"] for row in rows] == ["all", "A18"]  # A17 has too few
    assert rows[0]["properties"] == 45
    assert math.isclose(math.exp(rows[1]["median_log_ratio"]), 1.10)


def test_asking_drift_needs_time_within_one_model_version(tmp_path: Path) -> None:
    store = AskingIndexStore(tmp_path)
    store.append(asking_ratio_rows(_priced(40, 1.10), radar_batch_id="r1",
                                   observed_at=DAY1.isoformat(), model_version="m1"))
    index = store.append(asking_ratio_rows(_priced(40, 1.12), radar_batch_id="r2",
                                           observed_at=DAY2.isoformat(), model_version="m1"))
    assert asking_drift(index) is None  # 7 days apart

    later = (DAY1 + timedelta(days=40)).isoformat()
    index = store.append(asking_ratio_rows(_priced(40, 1.155), radar_batch_id="r3",
                                           observed_at=later, model_version="m1"))
    drift = asking_drift(index)
    assert drift is not None and drift["days"] == 40
    assert math.isclose(drift["pct_change"], 0.05, rel_tol=1e-6)

    index = store.append(asking_ratio_rows(_priced(40, 1.0), radar_batch_id="r4",
                                           observed_at=later, model_version="m2"))
    assert asking_drift(index) is None  # a new model resets the baseline


# --------------------------------------------------------------------------- negotiation


def _deal(key: str, price: float, date: str, **extra) -> dict:
    return {
        "transaction_type": "resale",
        "transaction_key": key,
        "station_code": extra.get("station_code", "A18"),
        "floor": extra.get("floor", "五層"),
        "total_floors": "十二層",
        "building_area_ping": extra.get("area", 40.2),
        "building_age_years": 8.3,
        "transaction_date": pd.Timestamp(date),
        "total_price_twd": price,
    }


def test_gone_property_matches_its_transaction() -> None:
    gone = gone_properties(_two_day_panel())
    assert list(gone["property_key"]) == ["p2"]

    panel = _two_day_panel()
    panel.loc[panel["source_listing_id"] == "2", ["floor_text", "area_ping"]] = ["5F/12F", 40.0]
    gone = gone_properties(panel)
    deals = pd.DataFrame([
        _deal("t1", 13_800_000, "2026-08-25"),
        _deal("t2", 13_000_000, "2026-08-25", floor="六層"),  # another floor
        _deal("t3", 14_000_000, "2025-01-10"),  # long before
    ])
    matches = match_deals(gone, deals)

    assert list(matches["transaction_key"]) == ["t1"]
    assert math.isclose(matches["deal_to_asking"].iloc[0], 0.92)


def test_ambiguous_or_implausible_matches_are_dropped() -> None:
    panel = _two_day_panel()
    panel.loc[panel["source_listing_id"] == "2", ["floor_text", "area_ping"]] = ["5F/12F", 40.0]
    gone = gone_properties(panel)
    twins = pd.DataFrame([_deal("t1", 13_800_000, "2026-08-25"),
                          _deal("t2", 13_900_000, "2026-08-28")])
    assert match_deals(gone, twins).empty
    cheap = pd.DataFrame([_deal("t1", 6_000_000, "2026-08-25")])
    assert match_deals(gone, cheap).empty


def test_offer_range_waits_for_enough_matches() -> None:
    few = pd.DataFrame({"deal_to_asking": [0.9] * (MIN_MATCHES - 1)})
    summary = negotiation_summary(few, gone_count=50)
    assert summary["usable"] is False and offer_range(10_000_000, summary) is None

    many = pd.DataFrame({"deal_to_asking": [0.88, 0.9, 0.92, 0.94] * 10})
    summary = negotiation_summary(many, gone_count=60)
    assert summary["usable"] is True
    low, high = offer_range(20_000_000, summary)  # type: ignore[misc]
    assert low < high < 20_000_000


# --------------------------------------------------------------------------- radar gates


def _valued(listing_id: str, asking: float, **extra) -> dict:
    return {
        "status": "valued",
        "listing_type": "sale",
        "source_listing_id": listing_id,
        "asking_price_twd": asking,
        "estimate_twd": 20_000_000,
        "interval_low_twd": 16_000_000,
        "interval_high_twd": 25_000_000,
        "has_coordinates": True,
        "layout": "3房2廳2衛",
        "age_years": 8.0,
        "confidence": "high",
        "net_area_ping": 35.0,
        "station_distance_m": 600.0,
        "common_area_source": "591_areas",
        **extra,
    }


def test_verdicts_put_suspicious_bargains_behind_clean_ones() -> None:
    ranked = rank_records([
        _valued("clean", 15_000_000),
        _valued("too_good", 11_000_000),  # 45% under the estimate
        _valued("no_split", 14_000_000, common_area_source=None),
        _valued("mild", 18_000_000),
    ])
    by_id = {r["source_listing_id"]: r for r in ranked}

    assert by_id["clean"]["verdict"] == "clear_below"
    assert by_id["too_good"]["verdict"] == "needs_check"
    assert "implausible_gap" in by_id["too_good"]["flags"]
    assert by_id["no_split"]["verdict"] == "needs_check"
    assert by_id["mild"]["verdict"] == "below_estimate"
    order = sorted(ranked, key=lambda r: r["rank"])
    assert [r["source_listing_id"] for r in order] == ["clean", "mild", "too_good", "no_split"]


def test_community_check_flags_a_listing_far_from_its_peers() -> None:
    def capture(lat: float, lon: float) -> dict:
        return {"status": "captured",
                "payload_json": json.dumps({"latitude": lat, "longitude": lon})}

    captures = {"a": capture(25.010, 121.215), "b": capture(25.011, 121.216),
                "far": capture(24.95, 121.25), "near": capture(25.0105, 121.2155)}
    annotations = {key: {"community_name": "鼎藏璞麗"} for key in captures}
    records = [
        _valued("far", 15_000_000, community_name="鼎藏璞麗", latitude=24.95, longitude=121.25),
        _valued("near", 15_000_000, community_name="鼎藏璞麗", latitude=25.0105,
                longitude=121.2155),
    ]
    apply_community_check(records, captures, annotations)
    far, near = (assess_record(r) for r in records)

    assert "community_mismatch" in far["flags"] and far["verdict"] == "needs_check"
    assert "community_mismatch" not in near["flags"] and near["verdict"] == "clear_below"


def test_valuation_page_offer_range_appears_only_when_usable() -> None:
    from qingpu_insight.conversation_presentation import with_offer_range

    summary = {"asking_twd": 20_000_000, "point_twd": 21_000_000}
    pending = {"usable": False, "matched": 3}
    assert with_offer_range(summary, pending) == summary
    assert with_offer_range(summary, None) == summary

    usable = {"usable": True, "matched": 42, "p25_deal_to_asking": 0.9,
              "p75_deal_to_asking": 0.95}
    shown = with_offer_range(summary, usable)
    assert shown is not None
    assert (shown["offer_low_twd"], shown["offer_high_twd"]) == (18_000_000, 19_000_000)
    assert shown["offer_basis_count"] == 42
