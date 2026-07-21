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


def listing_summary(df: pd.DataFrame, filters: ListingFilters) -> dict[str, Any]:
    if df.empty:
        return {
            "listing_type": filters.listing_type,
            "station_codes": list(filters.station_codes),
            "active_count": 0,
            "median_price": None,
            "min_price": None,
            "max_price": None,
            "snapshot_time": None,
        }

    df = df.copy()
    if "listing_type" in df.columns:
        df = df[df["listing_type"] == filters.listing_type]

    if "station_code" in df.columns and filters.station_codes:
        df = df[df["station_code"].isin(filters.station_codes)]

    is_sale = filters.listing_type in ("sale", "newhouse")
    price_col = "asking_price_twd" if is_sale else "monthly_rent_twd"
    prices = df[price_col].dropna() if price_col in df.columns else pd.Series([], dtype=float)

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
        "median_price": float(prices.median()) if not prices.empty else None,
        "min_price": float(prices.min()) if not prices.empty else None,
        "max_price": float(prices.max()) if not prices.empty else None,
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
        raw_price = row.get(price_col)
        price = int(raw_price) if raw_price is not None and not pd.isna(raw_price) else None

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

        area = row.get("building_area_ping")
        if area is not None and not pd.isna(area):
            area = float(area)
        else:
            area = None

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
