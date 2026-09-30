"""Locate a single valuation from a doorplate address or explicit coordinates.

The resale model finds a building's price history by TWD97 coordinates, so a
homepage valuation needs a point, not just a station and a distance. Addresses are
resolved offline against the official doorplate file (``data/raw/doorplates.csv``)
with the same rules transaction coordinates use: an exact doorplate, else the
nearest house number (at most 10 apart) on the same road, lane and alley. Road-only
guesses are never accepted.

Addresses are only used for lookup. Nothing here stores or logs them.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Protocol

import numpy as np
import pandas as pd

from qingpu_insight.addresses import build_doorplate_frame, normalize_address
from qingpu_insight.geo import station_from_coords, twd97_to_wgs84, wgs84_to_twd97

logger = logging.getLogger(__name__)

MAX_NUMBER_GAP = 10
SERVICE_RADIUS_M = 2_000.0
DISTRICTS = ("中壢區", "大園區")
_DISTRICT_ALIASES = {"中壢區": "中壢區", "中壢市": "中壢區", "大園區": "大園區", "大園鄉": "大園區"}
_PREFIX_RE = re.compile(
    r"^(?:台灣省?)?(?:桃園市|桃園縣)?(?P<district>中壢區|中壢市|大園區|大園鄉)?"
)
_VILLAGE_RE = re.compile(r"^[^\d路街道段巷弄號]{1,4}[村里](?:\d+鄰)?")
_BUILDING_RE = re.compile(r"^(?P<prefix>.*?)(?P<number>\d+)(?P<sub>之\d+)?號")


class AddressLocatorUnavailable(Exception):
    """The doorplate data needed to resolve addresses is not available."""


class AddressLocator(Protocol):
    def resolve(self, address: str) -> AddressMatch | None: ...


@dataclass(frozen=True)
class AddressMatch:
    twd97_x: float
    twd97_y: float
    match_quality: str  # "exact" | "nearest_number"


@dataclass(frozen=True)
class ValuationLocation:
    twd97_x: float
    twd97_y: float
    station_code: str
    station_distance_m: float


def _parse(address: str) -> tuple[str | None, str, int, str] | None:
    """Split an address into (district, lane prefix, house number, building key)."""
    text = normalize_address(address).replace("－", "-")
    text = re.sub(r"(\d+)-(\d+)號", r"\1之\2號", text)
    prefix_match = _PREFIX_RE.match(text)
    district = _DISTRICT_ALIASES.get(prefix_match.group("district") or "")
    text = text[prefix_match.end():]
    # Village and neighbourhood (村里、鄰) are administrative metadata, not doorplate text.
    text = _VILLAGE_RE.sub("", text)
    building = _BUILDING_RE.match(text)
    if building is None or not building.group("prefix"):
        return None
    key = building.group(0)
    return district, building.group("prefix"), int(building.group("number")), key


class DoorplateAddressIndex:
    """In-memory exact / nearest-number index over ``build_doorplate_frame`` output."""

    def __init__(self, doorplates: pd.DataFrame) -> None:
        frame = doorplates.loc[doorplates["district"].isin(DISTRICTS)]
        # Doorplate addresses are already normalized and carry no city or district.
        parts = frame["normalized_address"].astype(str).str.extract(_BUILDING_RE)
        buildings = pd.DataFrame(
            {
                "district": frame["district"].to_numpy(),
                "prefix": parts["prefix"].to_numpy(),
                "number": pd.to_numeric(parts["number"], errors="coerce").to_numpy(),
                "key": (
                    parts["prefix"] + parts["number"] + parts["sub"].fillna("") + "號"
                ).to_numpy(),
                "x": pd.to_numeric(frame["twd97_x"], errors="coerce").to_numpy(),
                "y": pd.to_numeric(frame["twd97_y"], errors="coerce").to_numpy(),
            }
        ).dropna()
        buildings = buildings.loc[buildings["prefix"].ne("")]
        # Floors of one doorplate share a point; the median is robust to stray rows.
        buildings = (
            buildings.groupby(["district", "key"], sort=False)
            .agg(prefix=("prefix", "first"), number=("number", "first"),
                 x=("x", "median"), y=("y", "median"))
            .reset_index()
        )
        self._exact = {
            (row.district, row.key): (float(row.x), float(row.y))
            for row in buildings.itertuples(index=False)
        }
        self._lanes: dict[tuple[str, str], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        for (district, prefix), group in buildings.groupby(["district", "prefix"], sort=False):
            ordered = group.sort_values("number", kind="stable")
            self._lanes[(district, prefix)] = (
                ordered["number"].to_numpy(int),
                ordered["x"].to_numpy(float),
                ordered["y"].to_numpy(float),
            )

    @classmethod
    def from_csv(cls, path: Path) -> DoorplateAddressIndex:
        return cls(build_doorplate_frame(path))

    def _nearest(self, district: str, prefix: str, number: int) -> tuple[float, float] | None:
        lane = self._lanes.get((district, prefix))
        if lane is None:
            return None
        numbers, xs, ys = lane
        gaps = np.abs(numbers - number)
        best = int(np.argmin(gaps))
        if gaps[best] > MAX_NUMBER_GAP:
            return None
        return float(xs[best]), float(ys[best])

    def resolve(self, address: str) -> AddressMatch | None:
        if not isinstance(address, str) or not address.strip():
            return None
        parsed = _parse(address)
        if parsed is None:
            return None
        district, prefix, number, key = parsed
        districts = (district,) if district else DISTRICTS
        for quality, lookup in (
            ("exact", lambda d: self._exact.get((d, key))),
            ("nearest_number", lambda d: self._nearest(d, prefix, number)),
        ):
            found = [point for point in map(lookup, districts) if point is not None]
            if len(found) > 1:
                return None  # same address in both districts: ask for the district
            if found:
                return AddressMatch(found[0][0], found[0][1], quality)
        return None


class LazyDoorplateIndex:
    """Load the doorplate index on first use; later calls reuse it."""

    def __init__(
        self,
        path: Path,
        loader: Callable[[Path], AddressLocator] = DoorplateAddressIndex.from_csv,
    ) -> None:
        self._path = path
        self._loader = loader
        self._lock = Lock()
        self._index: AddressLocator | None = None
        self._failed = False

    def _load(self) -> AddressLocator:
        with self._lock:
            if self._index is not None:
                return self._index
            if self._failed or not self._path.is_file():
                raise AddressLocatorUnavailable("doorplate data unavailable")
            try:
                self._index = self._loader(self._path)
            except Exception:
                self._failed = True
                logger.error("doorplate address index could not be built")
                raise AddressLocatorUnavailable("doorplate data unavailable") from None
            return self._index

    def warm(self) -> None:
        try:
            self._load()
        except AddressLocatorUnavailable:
            pass

    def resolve(self, address: str) -> AddressMatch | None:
        return self._load().resolve(address)


def location_from_wgs84(longitude: float, latitude: float) -> ValuationLocation:
    if not (118.0 <= longitude <= 123.0 and 20.0 <= latitude <= 27.0):
        raise ValueError("coordinates must be inside Taiwan")
    station_code, distance = station_from_coords(longitude, latitude)
    if distance > SERVICE_RADIUS_M:
        raise ValueError("coordinates are outside the service area")
    x, y = wgs84_to_twd97(longitude, latitude)
    return ValuationLocation(x, y, station_code, distance)


def location_from_twd97(x: float, y: float) -> ValuationLocation:
    longitude, latitude = twd97_to_wgs84(x, y)
    location = location_from_wgs84(longitude, latitude)
    return ValuationLocation(float(x), float(y), location.station_code,
                             location.station_distance_m)
