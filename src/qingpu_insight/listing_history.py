"""Daily 591 listing history: price changes, days on market and market heat.

Every complete list-API batch on disk (already contact-free) becomes one observation
day in ``panel.parquet``. Only complete batches count: a partial walk would make the
listings it never reached look delisted. From the panel come

* a per-listing timeline (first/last seen, price cuts, days on market), and
* a per-day market heat table over deduplicated properties (supply, new and gone
  properties, median days on market, share with a price cut).

591's ``posttime`` can be refreshed by agents re-posting, so days on market take the
earliest of the first observation and the earliest posting time across every agent
listing the same property; it is still a lower bound until the panel is long.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from qingpu_insight.listing_api_591 import API_REPRESENTATION, extract_api_page
from qingpu_insight.listing_dedupe import group_properties

HISTORY_DIR = Path("data/processed/listing_history")
PANEL_FILE = "panel.parquet"
HEAT_FILE = "heat.parquet"
LONG_ON_MARKET_DAYS = 180

PANEL_COLUMNS = (
    "batch_id",
    "observed_at",
    "source_listing_id",
    "property_key",
    "asking_price_twd",
    "original_price_twd",
    "down_price_percent",
    "posted_at",
    "community_name",
    "station_code",
    "area_ping",
    "floor_text",
    "total_floors",
    "building_age_years",
)


@dataclass(frozen=True)
class ApiBatch:
    batch_id: str
    observed_at: datetime
    directory: Path
    pages: tuple[int, ...]


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def complete_api_batches(raw_root: Path) -> list[ApiBatch]:
    """Complete sale API batches under ``raw_root``, oldest first."""
    batches: list[ApiBatch] = []
    if not raw_root.exists():
        return batches
    for manifest_path in raw_root.rglob("manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict) or manifest.get("listing_type") != "sale":
            continue
        if manifest.get("is_complete") is not True:
            continue
        pages = manifest.get("pages")
        if not isinstance(pages, list) or not pages or not all(
            isinstance(p, dict) and p.get("representation") == API_REPRESENTATION for p in pages
        ):
            continue
        observed = _parse_time(manifest.get("started_at"))
        if observed is None:
            continue
        batches.append(
            ApiBatch(
                batch_id=str(manifest.get("batch_id") or manifest_path.parent.name),
                observed_at=observed.astimezone(UTC),
                directory=manifest_path.parent,
                pages=tuple(sorted(int(p.get("page_number") or 0) for p in pages)),
            )
        )
    return sorted(batches, key=lambda b: (b.observed_at, b.batch_id))


def batch_listings(batch: ApiBatch) -> list[dict[str, Any]]:
    """Listings of one batch, deduplicated by listing id (first page wins)."""
    listings: dict[str, dict[str, Any]] = {}
    for page_number in batch.pages:
        path = batch.directory / f"page-{page_number:04d}.json"
        try:
            extraction = extract_api_page(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for listing in extraction.listings:
            listings.setdefault(
                listing.source_listing_id,
                {**listing.payload, "source_listing_id": listing.source_listing_id},
            )
    return list(listings.values())


def panel_rows(batch: ApiBatch, listings: list[dict[str, Any]]) -> pd.DataFrame:
    groups = group_properties(listings)
    rows = []
    for listing in listings:
        listing_id = str(listing["source_listing_id"])
        rows.append(
            {
                "batch_id": batch.batch_id,
                "observed_at": batch.observed_at,
                "source_listing_id": listing_id,
                "property_key": groups[listing_id].property_key,
                "asking_price_twd": listing.get("asking_price_twd"),
                "original_price_twd": listing.get("original_price_twd"),
                "down_price_percent": listing.get("down_price_percent"),
                "posted_at": _parse_time(listing.get("posted_at")),
                "community_name": listing.get("community_name"),
                "station_code": listing.get("station_code"),
                "area_ping": listing.get("area_ping"),
                "floor_text": listing.get("floor_text"),
                "total_floors": listing.get("total_floors"),
                "building_age_years": listing.get("building_age_years"),
            }
        )
    return _typed_panel(pd.DataFrame(rows, columns=list(PANEL_COLUMNS)))


def _typed_panel(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    for column in ("observed_at", "posted_at"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    for column in ("asking_price_twd", "original_price_twd", "down_price_percent", "area_ping",
                   "total_floors", "building_age_years"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
    for column in ("batch_id", "source_listing_id", "property_key", "community_name",
                   "station_code", "floor_text"):
        frame[column] = frame[column].astype("string")
    return frame


class ListingHistoryStore:
    """``panel.parquet`` (one row per listing per complete batch) and ``heat.parquet``."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    def load_panel(self) -> pd.DataFrame:
        try:
            return _typed_panel(pd.read_parquet(self.base_dir / PANEL_FILE))
        except (FileNotFoundError, OSError, ValueError):
            return _typed_panel(pd.DataFrame(columns=list(PANEL_COLUMNS)))

    def load_heat(self) -> pd.DataFrame:
        try:
            return pd.read_parquet(self.base_dir / HEAT_FILE)
        except (FileNotFoundError, OSError, ValueError):
            return pd.DataFrame()

    def _write(self, name: str, frame: pd.DataFrame) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        target = self.base_dir / name
        temporary = target.with_suffix(".tmp")
        frame.to_parquet(temporary, index=False)
        temporary.replace(target)

    def update(self, raw_root: Path) -> tuple[pd.DataFrame, list[str]]:
        """Append every complete API batch not yet in the panel; rebuild the heat table."""
        panel = self.load_panel()
        known = set(panel["batch_id"].dropna().astype(str))
        added: list[str] = []
        frames = [panel] if not panel.empty else []
        for batch in complete_api_batches(raw_root):
            if batch.batch_id in known:
                continue
            listings = batch_listings(batch)
            if not listings:
                continue
            frames.append(panel_rows(batch, listings))
            added.append(batch.batch_id)
        if added:
            panel = _typed_panel(pd.concat(frames, ignore_index=True))
            self._write(PANEL_FILE, panel)
        heat = market_heat(panel)
        self._write(HEAT_FILE, heat)
        return panel, added


# --------------------------------------------------------------------------- timelines


def _days(later: pd.Timestamp, earlier: pd.Timestamp) -> float | None:
    if pd.isna(later) or pd.isna(earlier):
        return None
    return max(0.0, (later - earlier).total_seconds() / 86_400)


def listing_timelines(panel: pd.DataFrame) -> pd.DataFrame:
    """One row per listing: first/last seen, prices, cuts and days on market."""
    columns = [
        "source_listing_id", "first_seen", "last_seen", "active", "first_posted",
        "first_price_twd", "last_price_twd", "max_price_twd", "price_cuts",
        "cut_from_max_pct", "days_on_market", "property_days_on_market",
    ]
    if panel.empty:
        return pd.DataFrame(columns=columns)
    latest = panel["observed_at"].max()
    ordered = panel.sort_values(["source_listing_id", "observed_at"])
    rows = []
    for listing_id, frame in ordered.groupby("source_listing_id", sort=False):
        prices = frame["asking_price_twd"].dropna()
        steps = prices.diff().dropna()
        original = frame["original_price_twd"].max()
        max_price = max(
            [v for v in (prices.max(), original) if pd.notna(v)], default=math.nan
        )
        last_price = prices.iloc[-1] if not prices.empty else math.nan
        first_seen = frame["observed_at"].min()
        first_posted = frame["posted_at"].min()
        start = min(t for t in (first_seen, first_posted) if pd.notna(t))
        last_seen = frame["observed_at"].max()
        rows.append(
            {
                "source_listing_id": listing_id,
                "first_seen": first_seen,
                "last_seen": last_seen,
                "active": bool(last_seen == latest),
                "first_posted": first_posted,
                "first_price_twd": prices.iloc[0] if not prices.empty else math.nan,
                "last_price_twd": last_price,
                "max_price_twd": max_price,
                "price_cuts": int((steps < 0).sum()),
                "cut_from_max_pct": (
                    (max_price - last_price) / max_price
                    if pd.notna(max_price) and pd.notna(last_price) and max_price > 0
                    else math.nan
                ),
                "days_on_market": _days(last_seen, start),
            }
        )
    timelines = pd.DataFrame(rows)
    # A property is on the market since the earliest agent listing of it appeared.
    latest_keys = (
        panel.sort_values("observed_at").groupby("source_listing_id")["property_key"].last()
    )
    timelines["property_key"] = timelines["source_listing_id"].map(latest_keys)
    starts = timelines.assign(
        start=timelines[["first_seen", "first_posted"]].min(axis=1)
    ).groupby("property_key")["start"].min()
    timelines["property_days_on_market"] = [
        _days(row.last_seen, starts.get(row.property_key)) for row in timelines.itertuples()
    ]
    return timelines[[*columns, "property_key"]]


# --------------------------------------------------------------------------- heat


def market_heat(panel: pd.DataFrame) -> pd.DataFrame:
    """Per complete batch: supply, flow and price-cut share over unique properties."""
    if panel.empty:
        return pd.DataFrame()
    rows = []
    seen_listings: set[str] = set()
    previous: set[str] = set()
    batches = (
        panel.groupby("batch_id")["observed_at"].min().sort_values().index.tolist()
    )
    for batch_id in batches:
        day = panel[panel["batch_id"] == batch_id]
        listing_ids = set(day["source_listing_id"].astype(str))
        properties = day.groupby("property_key")
        new_properties = sum(
            1
            for _, members in properties
            if seen_listings and not (set(members["source_listing_id"].astype(str)) & seen_listings)
        )
        observed = day["observed_at"].min()
        starts = properties["posted_at"].min()
        dom = [
            _days(observed, start) for start in starts if pd.notna(start)
        ]
        cut = properties.apply(
            lambda m: bool(
                (m["down_price_percent"].fillna(0) > 0).any()
                or (m["original_price_twd"] > m["asking_price_twd"]).any()
            ),
            include_groups=False,
        )
        unit = (day["asking_price_twd"] / day["area_ping"]).replace([math.inf, -math.inf], math.nan)
        rows.append(
            {
                "batch_id": batch_id,
                "observed_at": observed,
                "listings": len(listing_ids),
                "properties": int(properties.ngroups),
                "new_properties": new_properties if seen_listings else None,
                "gone_listings": len(previous - listing_ids) if previous else None,
                "median_days_on_market": float(pd.Series(dom).median()) if dom else None,
                "price_cut_share": float(cut.mean()) if len(cut) else None,
                "median_asking_unit_price_twd": float(unit.median()) if unit.notna().any()
                else None,
            }
        )
        seen_listings |= listing_ids
        previous = listing_ids
    return pd.DataFrame(rows)


def heat_summary(heat: pd.DataFrame) -> dict[str, Any] | None:
    """Latest heat row as plain JSON-able values (None when there is no history)."""
    if heat.empty:
        return None
    latest = heat.sort_values("observed_at").iloc[-1].to_dict()
    out: dict[str, Any] = {}
    for key, value in latest.items():
        if isinstance(value, pd.Timestamp):
            out[key] = value.isoformat()
        elif value is None or (isinstance(value, float) and math.isnan(value)):
            out[key] = None
        elif hasattr(value, "item"):
            out[key] = value.item()
        else:
            out[key] = value
    out["days_observed"] = int(len(heat))
    return out


def timeline_annotations(
    timelines: pd.DataFrame, listing_ids: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Radar fields per listing id: days on market and observed price cuts."""
    if timelines.empty:
        return {}
    wanted = set(map(str, listing_ids))
    out: dict[str, dict[str, Any]] = {}
    for row in timelines[timelines["source_listing_id"].isin(wanted)].itertuples():
        dom = row.property_days_on_market
        out[str(row.source_listing_id)] = {
            "days_on_market": None if dom is None or pd.isna(dom) else round(float(dom), 1),
            "observed_price_cuts": int(row.price_cuts),
        }
    return out
