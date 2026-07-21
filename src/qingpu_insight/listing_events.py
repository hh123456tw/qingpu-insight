"""Detect listing lifecycle events from normalized batch snapshots.

Consumes CaptureBatch and NormalizedListing DataFrame rows.
Produces typed events (listed, relisted, delisted, price_increase,
price_decrease) with deterministic SHA-256 event keys.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import pandas as pd

from qingpu_insight.listing_sources import CaptureBatch

EVENT_COLS = [
    "event_key",
    "source",
    "listing_type",
    "source_listing_id",
    "event_type",
    "event_data",
    "occurred_at",
]

STATE_COLS = [
    "listing_key",
    "source",
    "listing_type",
    "source_listing_id",
    "snapshot_at",
    "source_url",
    "title",
    "asking_price_twd",
    "monthly_rent_twd",
    "building_area_ping",
    "asking_unit_price_low_twd_per_ping",
    "asking_unit_price_high_twd_per_ping",
    "building_area_min_ping",
    "building_area_max_ping",
    "acquisition_representation",
    "acquisition_schema_version",
    "building_type",
    "bedrooms",
    "living_rooms",
    "bathrooms",
    "building_age_years",
    "floor",
    "total_floors",
    "parking_type",
    "latitude",
    "longitude",
    "raw_hash",
    "active",
    "consecutive_absences",
    "last_seen_batch_id",
]


def event_key(batch_id: str, listing_key: str, event_type: str) -> str:
    raw = f"{batch_id}|{listing_key}|{event_type}".encode()
    return hashlib.sha256(raw).hexdigest()


def _listing_key(row: pd.Series | dict) -> str:
    return f"{row['source']}|{row['listing_type']}|{row['source_listing_id']}"


def _price(row: pd.Series | dict) -> int | None:
    lt = row.get("listing_type", "")
    if lt in ("sale", "newhouse"):
        v = row.get("asking_price_twd")
    elif lt == "rental":
        v = row.get("monthly_rent_twd")
    else:
        return None
    return None if pd.isna(v) else int(v)


@dataclass
class ListingEventResult:
    events: pd.DataFrame
    state: pd.DataFrame


def detect_listing_events(
    previous: pd.DataFrame,
    current_rows: pd.DataFrame,
    batch: CaptureBatch,
) -> ListingEventResult:
    if not previous.empty:
        if "listing_key" not in previous.columns:
            previous = previous.copy()
            previous["listing_key"] = previous.apply(_listing_key, axis=1)
        prev_lookup = previous.set_index("listing_key")
    else:
        prev_lookup = pd.DataFrame(index=pd.Index([], name="listing_key"))

    events: list[dict] = []
    state_rows: list[dict] = []
    seen_keys: set[str] = set()

    for _, row in current_rows.iterrows():
        key = _listing_key(row)
        seen_keys.add(key)

        _fill_missing_cols(row)

        if key not in prev_lookup.index:
            events.append(
                _build_event(key, row, batch, "listed")
            )
        else:
            prev = prev_lookup.loc[key]
            if not prev.get("active", True) or prev.get("consecutive_absences", 0) > 0:
                events.append(
                    _build_event(key, row, batch, "relisted")
                )
            else:
                old_price = _price(prev)
                new_price = _price(row)
                if (
                    old_price is not None
                    and old_price > 0
                    and new_price is not None
                    and new_price > 0
                ):
                    if new_price > old_price:
                        events.append(
                            _build_price_event(
                                key, row, batch, "price_increase",
                                old_price, new_price,
                            )
                        )
                    elif new_price < old_price:
                        events.append(
                            _build_price_event(
                                key, row, batch, "price_decrease",
                                old_price, new_price,
                            )
                        )

        state_rows.append(
            _state_from_row(row, batch.batch_id, True, 0)
        )

    for key, prev in prev_lookup.iterrows():
        if key in seen_keys:
            continue

        _fill_missing_cols(prev)

        # Only compare rows of the same listing_type as the batch.
        if prev.get("listing_type", "") != batch.listing_type:
            state_rows.append(
                _state_from_row(
                    prev, prev.get("last_seen_batch_id", ""),
                    bool(prev.get("active", True)),
                    int(prev.get("consecutive_absences", 0)),
                )
            )
            continue

        if not prev.get("active", False):
            state_rows.append(
                _state_from_row(
                    prev, prev.get("last_seen_batch_id", ""),
                    False, int(prev.get("consecutive_absences", 0)),
                )
            )
        elif batch.is_complete:
            absences = int(prev.get("consecutive_absences", 0)) + 1
            if absences >= 2:
                events.append(
                    _build_event_from_prev(key, prev, batch, "delisted")
                )
                state_rows.append(
                    _state_from_row(prev, batch.batch_id, False, absences)
                )
            else:
                state_rows.append(
                    _state_from_row(prev, batch.batch_id, True, absences)
                )
        else:
            state_rows.append(
                _state_from_row(
                    prev, prev.get("last_seen_batch_id", batch.batch_id),
                    True, int(prev.get("consecutive_absences", 0)),
                )
            )

    events_df = (
        pd.DataFrame(events, columns=EVENT_COLS)
        if events
        else pd.DataFrame(columns=EVENT_COLS)
    )
    state_df = pd.DataFrame(state_rows)
    return ListingEventResult(events=events_df, state=state_df)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _fill_missing_cols(row: pd.Series) -> None:
    """Fill contract columns that may be absent in legacy state lookups."""
    defaults = {
        "asking_price_twd": None,
        "monthly_rent_twd": None,
        "asking_unit_price_low_twd_per_ping": None,
        "asking_unit_price_high_twd_per_ping": None,
        "building_area_min_ping": None,
        "building_area_max_ping": None,
        "acquisition_representation": "unknown",
        "acquisition_schema_version": "unknown",
    }
    for col, default in defaults.items():
        if col not in row.index:
            row[col] = default


def _state_from_row(
    row: pd.Series | dict,
    batch_id: str,
    active: bool,
    consecutive_absences: int,
) -> dict:
    d = {c: row.get(c) for c in STATE_COLS}
    d["listing_key"] = _listing_key(row)
    d["active"] = active
    d["consecutive_absences"] = consecutive_absences
    d["last_seen_batch_id"] = batch_id
    return d


def _build_event(
    listing_key: str,
    row: pd.Series | dict,
    batch: CaptureBatch,
    event_type: str,
) -> dict:
    return {
        "event_key": event_key(batch.batch_id, listing_key, event_type),
        "source": row.get("source", ""),
        "listing_type": row.get("listing_type", ""),
        "source_listing_id": row.get("source_listing_id", ""),
        "event_type": event_type,
        "event_data": None,
        "occurred_at": _occurred_at(row, batch),
    }


def _build_event_from_prev(
    listing_key: str,
    prev: pd.Series,
    batch: CaptureBatch,
    event_type: str,
) -> dict:
    return {
        "event_key": event_key(batch.batch_id, listing_key, event_type),
        "source": prev.get("source", ""),
        "listing_type": prev.get("listing_type", ""),
        "source_listing_id": prev.get("source_listing_id", ""),
        "event_type": event_type,
        "event_data": None,
        "occurred_at": batch.started_at,
    }


def _build_price_event(
    listing_key: str,
    row: pd.Series | dict,
    batch: CaptureBatch,
    event_type: str,
    old_price: int,
    new_price: int,
) -> dict:
    event_data = {
        "previous_price": old_price,
        "new_price": new_price,
        "absolute_change": new_price - old_price,
        "percentage_change": round(
            (new_price - old_price) / old_price * 100, 2
        ),
    }
    return {
        "event_key": event_key(batch.batch_id, listing_key, event_type),
        "source": row.get("source", ""),
        "listing_type": row.get("listing_type", ""),
        "source_listing_id": row.get("source_listing_id", ""),
        "event_type": event_type,
        "event_data": json.dumps(event_data, ensure_ascii=False),
        "occurred_at": _occurred_at(row, batch),
    }


def _occurred_at(
    row: pd.Series | dict,
    batch: CaptureBatch,
):
    ts = row.get("snapshot_at")
    return ts if ts is not None else batch.started_at
