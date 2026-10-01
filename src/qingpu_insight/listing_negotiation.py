"""Negotiation gap: what a delisted 591 property finally sold for versus its asking price.

When every agent listing of a property disappears from the daily panel, the property
probably sold. Its recorded resale transaction (實價登錄) is looked up by physical
attributes, because 591 never shows the exact address: same station, same floor and
building height, area within tolerance, similar age, and a contract date shortly before
or after the listing vanished. Only unambiguous one-to-one matches count.

The ratio ``deal / last asking`` summarises how much buyers negotiate in 青埔. It is
shown only once ``MIN_MATCHES`` properties have matched; with fewer, a median of a
handful of deals would be noise presented as a fact.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from qingpu_insight.model_features import parse_floor

MIN_MATCHES = 30
AREA_TOLERANCE_PING = 0.5
AREA_TOLERANCE_RATIO = 0.02
AGE_TOLERANCE_YEARS = 1.5
DEAL_BEFORE_GONE_DAYS = 120
DEAL_AFTER_GONE_DAYS = 45
PLAUSIBLE_RATIO = (0.6, 1.15)
SUMMARY_FILE = "negotiation.json"


def _floor_pair(floor_text: object) -> tuple[int | None, int | None]:
    if not isinstance(floor_text, str) or "/" not in floor_text:
        return None, None
    floor, _, total = floor_text.upper().replace("F", "").partition("/")
    try:
        return int(floor), int(total)
    except ValueError:
        return None, None


def gone_properties(panel: pd.DataFrame) -> pd.DataFrame:
    """Properties none of whose listings appear in the latest complete batch."""
    columns = ["property_key", "gone_after", "last_asking_twd", "first_asking_twd",
               "station_code", "floor", "total_floors", "area_ping", "building_age_years"]
    if panel.empty or panel["batch_id"].nunique() < 2:
        return pd.DataFrame(columns=columns)
    latest = panel["observed_at"].max()
    rows = []
    for key, frame in panel.groupby("property_key"):
        if (frame["observed_at"] == latest).any():
            continue
        last_day = frame["observed_at"].max()
        last = frame[frame["observed_at"] == last_day]
        floor, total = _floor_pair(last["floor_text"].dropna().iloc[0]
                                   if last["floor_text"].notna().any() else None)
        rows.append(
            {
                "property_key": key,
                "gone_after": last_day,
                "last_asking_twd": float(last["asking_price_twd"].min()),
                "first_asking_twd": float(
                    frame.sort_values("observed_at")["asking_price_twd"].iloc[0]
                ),
                "station_code": last["station_code"].iloc[0],
                "floor": floor,
                "total_floors": total,
                "area_ping": float(last["area_ping"].median()),
                "building_age_years": float(last["building_age_years"].median()),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _prepared_transactions(transactions: pd.DataFrame) -> pd.DataFrame:
    frame = transactions.copy()
    if "transaction_type" in frame:
        frame = frame[frame["transaction_type"] == "resale"]
    frame["transaction_date"] = pd.to_datetime(frame["transaction_date"], errors="coerce")
    frame["floor_number"] = frame["floor"].map(parse_floor)
    frame["total_floor_number"] = frame["total_floors"].map(parse_floor)
    return frame.dropna(subset=["transaction_date", "total_price_twd", "building_area_ping"])


def match_deals(gone: pd.DataFrame, transactions: pd.DataFrame) -> pd.DataFrame:
    """One row per gone property with exactly one plausible transaction."""
    columns = ["property_key", "gone_after", "transaction_key", "transaction_date",
               "last_asking_twd", "deal_price_twd", "deal_to_asking"]
    if gone.empty or transactions.empty:
        return pd.DataFrame(columns=columns)
    deals = _prepared_transactions(transactions)
    rows = []
    for item in gone.itertuples():
        if pd.isna(item.floor) or pd.isna(item.total_floors) or pd.isna(item.area_ping):
            continue
        gone_day = pd.Timestamp(item.gone_after)
        if gone_day.tzinfo is not None:
            gone_day = gone_day.tz_convert(None)  # contract dates are naive calendar days
        window = deals[
            (deals["station_code"] == item.station_code)
            & (deals["floor_number"] == int(item.floor))
            & (deals["total_floor_number"] == int(item.total_floors))
            & (deals["transaction_date"] >= gone_day - pd.Timedelta(days=DEAL_BEFORE_GONE_DAYS))
            & (deals["transaction_date"] <= gone_day + pd.Timedelta(days=DEAL_AFTER_GONE_DAYS))
        ]
        tolerance = max(AREA_TOLERANCE_PING, AREA_TOLERANCE_RATIO * float(item.area_ping))
        window = window[(window["building_area_ping"] - item.area_ping).abs() <= tolerance]
        if not pd.isna(item.building_age_years) and "building_age_years" in window:
            age = window["building_age_years"]
            window = window[age.isna() | ((age - item.building_age_years).abs()
                                          <= AGE_TOLERANCE_YEARS)]
        if len(window) != 1:
            continue
        deal = window.iloc[0]
        ratio = float(deal["total_price_twd"]) / float(item.last_asking_twd)
        if not PLAUSIBLE_RATIO[0] <= ratio <= PLAUSIBLE_RATIO[1]:
            continue  # a different flat, a family sale, or a listing price typo
        rows.append(
            {
                "property_key": item.property_key,
                "gone_after": item.gone_after,
                "transaction_key": deal.get("transaction_key"),
                "transaction_date": deal["transaction_date"],
                "last_asking_twd": float(item.last_asking_twd),
                "deal_price_twd": float(deal["total_price_twd"]),
                "deal_to_asking": ratio,
            }
        )
    matched = pd.DataFrame(rows, columns=columns)
    # One transaction may only explain one property.
    return matched[~matched["transaction_key"].duplicated(keep=False)
                   | matched["transaction_key"].isna()]


def negotiation_summary(matches: pd.DataFrame, gone_count: int) -> dict[str, Any]:
    """Median deal-to-asking ratio; ``usable`` only with at least MIN_MATCHES matches."""
    n = int(len(matches))
    summary: dict[str, Any] = {
        "gone_properties": int(gone_count),
        "matched": n,
        "min_matches": MIN_MATCHES,
        "usable": n >= MIN_MATCHES,
    }
    if n:
        ratios = matches["deal_to_asking"].astype("float64")
        summary.update(
            median_deal_to_asking=float(ratios.median()),
            p25_deal_to_asking=float(ratios.quantile(0.25)),
            p75_deal_to_asking=float(ratios.quantile(0.75)),
        )
    return summary


def save_summary(base_dir: Path, summary: Mapping[str, Any]) -> None:
    target = Path(base_dir) / SUMMARY_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(dict(summary), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def load_summary(base_dir: Path) -> dict[str, Any] | None:
    try:
        summary = json.loads((Path(base_dir) / SUMMARY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return summary if isinstance(summary, dict) else None


def offer_range(asking_price_twd: object, summary: Mapping[str, Any] | None
                ) -> tuple[int, int] | None:
    """Suggested offer band (p25–p75 of deal/asking) once the summary is usable."""
    if not summary or not summary.get("usable"):
        return None
    try:
        asking = float(asking_price_twd)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(asking) or asking <= 0:
        return None
    low, high = summary.get("p25_deal_to_asking"), summary.get("p75_deal_to_asking")
    if low is None or high is None:
        return None
    return int(round(asking * float(low), -4)), int(round(asking * float(high), -4))
