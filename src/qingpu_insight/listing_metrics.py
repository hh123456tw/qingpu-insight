from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class ListingFilters:
    listing_type: str
    station_codes: tuple[str, ...] = field(default_factory=lambda: ("A17", "A18", "A19"))
    limit: int = 100


def _numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series([], dtype=float)
    return pd.to_numeric(df[column], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    ).dropna()


def _summary_value(prices: pd.Series, operation: str) -> float | None:
    if prices.empty:
        return None
    return float(getattr(prices, operation)())


def listing_summary(df: pd.DataFrame, filters: ListingFilters) -> dict[str, Any]:
    if df.empty:
        return {
            "listing_type": filters.listing_type,
            "station_codes": list(filters.station_codes),
            "active_count": 0,
            "median_price": None,
            "min_price": None,
            "max_price": None,
            "median_unit_price_low": None,
            "median_unit_price_high": None,
            "snapshot_time": None,
        }

    df = df.copy()
    if "listing_type" in df.columns:
        df = df[df["listing_type"] == filters.listing_type]

    if "station_code" in df.columns and filters.station_codes:
        df = df[df["station_code"].isin(filters.station_codes)]

    is_sale = filters.listing_type in ("sale", "newhouse")
    price_col = "asking_price_twd" if is_sale else "monthly_rent_twd"
    prices = _numeric_series(df, price_col)
    unit_price_lows = _numeric_series(df, "asking_unit_price_low_twd_per_ping")
    unit_price_highs = _numeric_series(df, "asking_unit_price_high_twd_per_ping")

    snapshot_time = None
    if "snapshot_at" in df.columns and not df.empty:
        valid = df["snapshot_at"].dropna()
        if not valid.empty:
            snapshot_time = valid.max()
            if hasattr(snapshot_time, "isoformat"):
                snapshot_time = snapshot_time.isoformat()

    return {
        "listing_type": filters.listing_type,
        "station_codes": list(filters.station_codes),
        "active_count": int(len(df)),
        "median_price": _summary_value(prices, "median"),
        "min_price": _summary_value(prices, "min"),
        "max_price": _summary_value(prices, "max"),
        "median_unit_price_low": (
            _summary_value(unit_price_lows, "median")
            if filters.listing_type == "newhouse"
            else None
        ),
        "median_unit_price_high": (
            _summary_value(unit_price_highs, "median")
            if filters.listing_type == "newhouse"
            else None
        ),
        "snapshot_time": str(snapshot_time) if snapshot_time is not None else None,
    }


def _round_coord(val: Any) -> float | None:
    if val is None:
        return None
    try:
        v = float(val)
        if np.isnan(v) or np.isinf(v):
            return None
        return round(v, 4)
    except (ValueError, TypeError):
        return None


def _safe_number(value: Any, converter: type[int] | type[float]) -> int | float | None:
    if value is None or pd.isna(value):
        return None
    try:
        parsed = float(value)
        if not np.isfinite(parsed):
            return None
        return converter(parsed)
    except (TypeError, ValueError):
        return None


def _public_range(
    row: pd.Series,
    low_column: str,
    high_column: str,
    converter: type[int] | type[float],
) -> dict[str, int | float | None]:
    low = _safe_number(row.get(low_column), converter)
    high = _safe_number(row.get(high_column), converter)
    return {"low": low, "high": high}


def public_listings(df: pd.DataFrame, filters: ListingFilters) -> list[dict[str, Any]]:
    if df.empty:
        return []

    df = df.copy()
    if "listing_type" in df.columns:
        df = df[df["listing_type"] == filters.listing_type]

    if "station_code" in df.columns and filters.station_codes:
        df = df[df["station_code"].isin(filters.station_codes)]

    df = df.head(filters.limit)

    results: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        is_sale = filters.listing_type in ("sale", "newhouse")
        price_col = "asking_price_twd" if is_sale else "monthly_rent_twd"
        price = _safe_number(row.get(price_col), int)

        station = row.get("station_code")
        if station is not None and not pd.isna(station):
            station = str(station)
        else:
            station = None

        snapshot = row.get("snapshot_at")
        if snapshot is not None and not pd.isna(snapshot):
            snapshot = str(snapshot)
        else:
            snapshot = None

        area = _safe_number(row.get("building_area_ping"), float)

        results.append({
            "listing_id": str(row.get("source_listing_id", "")),
            "type": row.get("listing_type", ""),
            "title": str(row.get("title", "")),
            "source_url": str(row.get("source_url", "")),
            "station": station,
            "area": area,
            "price": price,
            "event": None,
            "status": None,
            "latitude": _round_coord(row.get("latitude")),
            "longitude": _round_coord(row.get("longitude")),
            "model_evidence": None,
            "snapshot_time": snapshot,
            "unit_price_range_twd_per_ping": _public_range(
                row,
                "asking_unit_price_low_twd_per_ping",
                "asking_unit_price_high_twd_per_ping",
                int,
            ),
            "area_range_ping": _public_range(
                row,
                "building_area_min_ping",
                "building_area_max_ping",
                float,
            ),
        })
    return results


def public_events(events_df: pd.DataFrame, filters: ListingFilters) -> list[dict[str, Any]]:
    if events_df.empty:
        return []

    df = events_df.copy()
    if "listing_type" in df.columns:
        df = df[df["listing_type"] == filters.listing_type]

    df = df.head(filters.limit)

    results: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        raw_data = row.get("event_data")
        if raw_data is not None and not pd.isna(raw_data):
            import json as _json
            if isinstance(raw_data, str):
                try:
                    raw_data = _json.loads(raw_data)
                except (ValueError, TypeError):
                    raw_data = None
        else:
            raw_data = None

        occurred = row.get("occurred_at")
        if occurred is not None and not pd.isna(occurred):
            occurred = str(occurred)
        else:
            occurred = None

        results.append({
            "event_key": str(row.get("event_key", "")),
            "type": row.get("listing_type", ""),
            "source_listing_id": str(row.get("source_listing_id", "")),
            "event_type": row.get("event_type", ""),
            "event_data": raw_data,
            "occurred_at": occurred,
        })
    return results
